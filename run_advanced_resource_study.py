"""Multi-year PV/BESS sizing with least-cost selection at the primary site.

For every workload profile and import limit the full PV x BESS grid is
dispatched over all weather years. Designs that never exceed the limit are
ranked by total annual cost (annualised resource cost + grid energy cost at
historical hourly prices + Powerlink transmission network charges, where a
published tariff exists). A PPA-hedged energy cost is reported alongside. Emissions and on-site renewable share are
reported alongside, so cost-carbon trade-offs are visible.
"""

from __future__ import annotations

import itertools

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from studylib.config import RESULTS, load_config, site_by_id
from studylib.dispatch import dispatch
from studylib.economics import (
    annualised_resource_cost,
    energy_and_emissions,
    hourly_price,
    network_charges,
    ppa_adjusted_energy_cost,
    time_matched_metrics,
)
from studylib.facility import PROFILES, facility_load
from studylib.io import save_figure
from studylib.weather import load_site_weather


CONFIG = load_config()
RESOURCE = CONFIG["renewable_bess"]


def build_cases() -> list[tuple[float, float, float, bool]]:
    """(PV MW, BESS MW, BESS MWh, grid charging). No-BESS cases appear once."""
    cases = []
    for pv_mw in RESOURCE["pv_sizes_mw"]:
        cases.append((float(pv_mw), 0.0, 0.0, False))
        for power, duration, grid_charging in itertools.product(
            [value for value in RESOURCE["bess_power_mw"] if value > 0], RESOURCE["bess_durations_h"], [False, True]
        ):
            cases.append((float(pv_mw), float(power), float(power * duration), grid_charging))
    return cases


def run_site(site_id: str, profiles=PROFILES, limits=None, irradiance_scale: float | None = None) -> pd.DataFrame:
    site = site_by_id(site_id)
    years = RESOURCE["weather_years"]
    weather = load_site_weather(site_id, years, irradiance_scale)
    price = hourly_price(weather["timestamp"], site["grid"], site["price_region"])
    emission_key = site["price_region"] if site["grid"] == "NEM" else site["grid"]
    limits = limits or RESOURCE["grid_limits_mw"]
    rows = []
    for profile in profiles:
        demand = facility_load(weather, profile)["facility_mw"]
        for limit in limits:
            for pv_mw, power, energy, grid_charging in build_cases():
                pv = pv_mw * weather["solar_cf"].to_numpy()
                result = dispatch(demand, pv, power, energy, limit, grid_charging)
                costs = annualised_resource_cost(pv_mw, power, energy)
                energy_cost = energy_and_emissions(result.grid, price, emission_key, len(years))
                network = network_charges(result.grid, weather["timestamp"], site_id, len(years))
                matched = time_matched_metrics(result, demand, weather["timestamp"], emission_key, len(years))
                ppa = {
                    f"EnergyCostWithPPA{int(100 * fraction)}pctAUDm": ppa_adjusted_energy_cost(result.grid, price, fraction, len(years))
                    for fraction in CONFIG["economics"]["ppa"]["fractions"] if fraction > 0
                }
                rows.append(
                    {
                        "SiteId": site_id,
                        "LoadProfile": profile,
                        "WeatherYears": f"{min(years)}-{max(years)}",
                        "ImportLimitMW": limit,
                        "PvMW": pv_mw,
                        "BessPowerMW": power,
                        "BessEnergyMWh": energy,
                        "GridChargingAllowed": grid_charging,
                        **result.summary(demand, pv, energy),
                        **costs,
                        **energy_cost,
                        **network,
                        **matched,
                        **ppa,
                        "TotalAnnualCostAUDm": costs["AnnualisedResourceCostAUDm"] + energy_cost["AnnualEnergyCostAUDm"]
                        + network["NetworkChargesAUDm"],
                    }
                )
    return pd.DataFrame(rows)


def least_cost(frame: pd.DataFrame) -> pd.DataFrame:
    compliant = frame[frame["CompliesWithImportLimit"]]
    keys = ["SiteId", "LoadProfile", "ImportLimitMW"]
    best_any = compliant.sort_values("TotalAnnualCostAUDm").groupby(keys, as_index=False).first()
    best_any["Selection"] = "Least cost (any charging)"
    # Solar-only charging: designs without grid charging, including no-BESS designs.
    solar_only = compliant[~compliant["GridChargingAllowed"]]
    best_solar = solar_only.sort_values("TotalAnnualCostAUDm").groupby(keys, as_index=False).first()
    best_solar["Selection"] = "Least cost (no grid charging)"
    return pd.concat([best_any, best_solar], ignore_index=True).sort_values(keys + ["Selection"])


def main() -> None:
    site_id = RESOURCE["primary_site"]
    frame = run_site(site_id)
    frame.to_csv(RESULTS / "advanced_resource_sizing.csv", index=False)
    best = least_cost(frame)
    best.to_csv(RESULTS / "advanced_resource_best_cases.csv", index=False)

    limit = RESOURCE["selected_trace_limit_mw"]
    subset = frame[(frame["LoadProfile"] == "training") & (frame["ImportLimitMW"] == limit)]
    fig, axes = plt.subplots(1, 3, figsize=(18, 5.6))
    for grid_charging, marker in ((False, "o"), (True, "^")):
        part = subset[subset["GridChargingAllowed"] == grid_charging]
        axes[0].scatter(
            part["PvMW"] + (8 if grid_charging else -8), part["BessEnergyMWh"],
            c=np.minimum(part["HoursAboveImportLimit"], 2000), cmap="viridis_r", vmin=0, vmax=2000,
            marker=marker, s=40 + part["BessPowerMW"] / 2, edgecolor="black", linewidth=0.3,
            label="grid charging" if grid_charging else "solar charging only",
        )
    passing = subset[subset["CompliesWithImportLimit"]]
    axes[0].scatter(passing["PvMW"], passing["BessEnergyMWh"], marker="*", s=200, facecolor="none", edgecolor="#E69F00", linewidth=1.5, label="complies all years")
    axes[0].set_xlabel("PV capacity (MW AC)")
    axes[0].set_ylabel("BESS energy (MWh)")
    axes[0].set_title(f"Training load, {limit:.0f} MW limit: hours above limit")
    axes[0].legend(fontsize=7, loc="upper left")
    axes[0].grid(alpha=0.25)

    compliant = subset[subset["CompliesWithImportLimit"]]
    scatter = axes[1].scatter(compliant["TotalAnnualCostAUDm"], compliant["AnnualScope2EmissionsKt"], c=compliant["PvMW"], cmap="plasma", edgecolor="black", linewidth=0.3)
    choice = best[(best["LoadProfile"] == "training") & (best["ImportLimitMW"] == limit)]
    for row in choice.itertuples():
        axes[1].annotate(f"{row.Selection}\n{row.PvMW:.0f} MW PV, {row.BessPowerMW:.0f} MW/{row.BessEnergyMWh:.0f} MWh",
                         (row.TotalAnnualCostAUDm, row.AnnualScope2EmissionsKt), fontsize=7, xytext=(5, 5), textcoords="offset points")
    fig.colorbar(scatter, ax=axes[1], label="PV MW")
    axes[1].set_xlabel("Total annual cost (AUD m, resources + grid energy + network charges)")
    axes[1].set_ylabel("Scope 2 emissions (kt CO2-e / year)")
    axes[1].set_title("Compliant designs: cost vs emissions")
    axes[1].grid(alpha=0.25)

    peaks = frame.groupby("LoadProfile")["PeakFacilityLoadMW"].first().reindex(list(PROFILES))
    bars = axes[2].bar([p.title() for p in PROFILES], peaks, color=["#D55E00", "#0072B2", "#009E73"][: len(PROFILES)])
    for value in RESOURCE["grid_limits_mw"]:
        axes[2].axhline(value, color="#B22222", linestyle="--", linewidth=1)
        axes[2].text(0.99, value, f"{value:.0f} MW limit", va="bottom", ha="right", fontsize=8, color="#B22222",
                     transform=axes[2].get_yaxis_transform())
    for bar, value in zip(bars, peaks):
        axes[2].text(bar.get_x() + bar.get_width() / 2, value + 3, f"{value:.1f} MW", ha="center")
    axes[2].set_ylabel("Peak facility demand (MW)")
    axes[2].set_title("Workload profile changes the connection requirement")
    axes[2].grid(axis="y", alpha=0.25)
    fig.suptitle(f"{site_by_id(site_id)['name']} multi-year sizing ({frame['WeatherYears'].iloc[0]} weather, historical prices)")
    fig.tight_layout()
    save_figure(fig, RESULTS, "advanced_resource_sizing")
    print(best[["LoadProfile", "ImportLimitMW", "Selection", "PvMW", "BessPowerMW", "BessEnergyMWh", "TotalAnnualCostAUDm", "OnsiteRenewableFraction"]].to_string(index=False))


if __name__ == "__main__":
    main()
