"""Recent-price sensitivity: does the design still hold at 2025-26 prices?

The base studies use 2020-2024 weather and prices. Price year is the largest
driver of total cost, so this re-runs the design grid at the primary site with:

* 2025: AEMO Queensland prices with matching 2025 NASA POWER weather;
* 2026 to date: 2026 prices (to the latest published month) against 2025
  weather on the same calendar hours (weather and prices not from the same year).

Each window is run with the rule-based and the day-ahead-forecast price-aware
controllers, and the least-cost compliant design is compared with the
2020-2024 base (results/price_aware_least_cost_selection.csv).
Outputs: results/recent_prices_comparison.csv,
results/recent_prices_selection.csv and results/recent_prices.png.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from download_nasa_power_weather import download_year
from studylib.config import PRICE_DIR, RESULTS, load_config, site_by_id
from studylib.dispatch import dispatch
from studylib.economics import annualised_resource_cost, energy_and_emissions, network_charges, time_matched_metrics
from studylib.facility import facility_load
from studylib.io import save_figure, write_csv
from studylib.optimal_dispatch import forecast_dispatch
from studylib.weather import load_site_weather


CONFIG = load_config()
RESOURCE = CONFIG["renewable_bess"]
RECENT = CONFIG["recent_prices"]
DESIGNS = [(pv, power, energy) for pv in CONFIG["price_aware_dispatch"]["design_pv_mw"]
           for power, energy in CONFIG["price_aware_dispatch"]["design_bess"]]
CONTROLLERS = ("Rule-based", "Price-aware, day-ahead forecast")


def recent_prices(region: str) -> pd.Series:
    path = PRICE_DIR / f"{region}_recent_hourly.csv"
    return pd.read_csv(path, parse_dates=["timestamp"]).set_index("timestamp")["rrp_per_mwh"]


def windows(weather: pd.DataFrame, prices: pd.Series):
    year = int(RECENT["weather_year"])
    stamps = weather["timestamp"]
    out = {}
    for price_year in RECENT["years"]:
        shifted = stamps + pd.DateOffset(years=int(price_year) - year)
        aligned = prices.reindex(pd.DatetimeIndex(shifted))
        keep = aligned.notna().to_numpy()
        if keep.mean() < 0.25:
            continue
        label = f"{price_year} prices" + ("" if price_year == year else f" (to {aligned.dropna().index.max():%b %Y}, {year} weather)")
        out[label] = (keep, aligned.to_numpy())
    return out


def main() -> None:
    site = site_by_id(RESOURCE["primary_site"])
    year = int(RECENT["weather_year"])
    download_year(site["site_id"], year, float(site["latitude"]), float(site["longitude"]))
    weather_all = load_site_weather(site["site_id"], [year])
    prices = recent_prices(site["price_region"])
    limit = RESOURCE["selected_trace_limit_mw"]
    rows = []
    for window, (keep, price_all) in windows(weather_all, prices).items():
        weather = weather_all[keep].reset_index(drop=True)
        price = pd.Series(price_all[keep]).interpolate(limit_direction="both").to_numpy()
        years = len(weather) / 8760.0
        demand = facility_load(weather, "training")["facility_mw"]
        for pv_mw, power, energy_mwh in DESIGNS:
            pv = pv_mw * weather["solar_cf"].to_numpy()
            resource = annualised_resource_cost(pv_mw, power, energy_mwh)["AnnualisedResourceCostAUDm"]
            results = {
                "Rule-based": dispatch(demand, pv, power, energy_mwh, limit, True),
                "Price-aware, day-ahead forecast": forecast_dispatch(demand, pv, price, max(power, 1e-3),
                                                                      max(energy_mwh, 1e-3), limit, True),
            }
            for controller, result in results.items():
                energy = energy_and_emissions(result.grid, price, site["price_region"], years)
                network = network_charges(result.grid, weather["timestamp"], site["site_id"], years)
                row = {"PriceWindow": window, "Controller": controller,
                       "Design": f"{pv_mw:.0f} MW PV + {power:.0f} MW/{energy_mwh:.0f} MWh",
                       "PvMW": pv_mw, "BessPowerMW": power, "BessEnergyMWh": energy_mwh,
                       "MeanPriceAUDperMWh": float(price.mean()), **result.summary(demand, pv, energy_mwh), **energy,
                       **network, **time_matched_metrics(result, demand, weather["timestamp"], site["price_region"], years),
                       "AnnualisedResourceCostAUDm": resource}
                row["TotalAnnualCostAUDm"] = resource + row["AnnualEnergyCostAUDm"] + row["NetworkChargesAUDm"]
                rows.append(row)
        print(f"{window}: done", flush=True)
    frame = pd.DataFrame(rows)
    frame.to_csv(RESULTS / "recent_prices_comparison.csv", index=False)

    base = pd.read_csv(RESULTS / "price_aware_least_cost_selection.csv")
    base = base[base["Controller"].isin(CONTROLLERS)].assign(PriceWindow="2020-2024 prices (base)")
    compliant = frame[frame["CompliesWithImportLimit"]]
    best = (compliant.sort_values("TotalAnnualCostAUDm").groupby(["PriceWindow", "Controller"], as_index=False).first()
            [["PriceWindow", "Controller", "Design", "TotalAnnualCostAUDm", "AnnualScope2EmissionsKt", "CfeScore",
              "MeanPriceAUDperMWh"]])
    # Cost, in each recent window, of the design chosen on 2020-2024 prices (regret of keeping it).
    base_choice = dict(zip(base["Controller"], base["Design"]))
    costs = frame.set_index(["PriceWindow", "Controller", "Design"])["TotalAnnualCostAUDm"]
    best["CostOfBaseDesignAUDm"] = [float(costs.loc[(r.PriceWindow, r.Controller, base_choice[r.Controller])])
                                    for r in best.itertuples()]
    best["RegretOfBaseDesignAUDm"] = best["CostOfBaseDesignAUDm"] - best["TotalAnnualCostAUDm"]
    selection = pd.concat([base, best], ignore_index=True)
    write_csv(RESULTS / "recent_prices_selection.csv", selection.to_dict("records"))
    print(selection.round(2).to_string(index=False))

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    for controller, marker in zip(CONTROLLERS, ("o", "s")):
        part = selection[selection["Controller"] == controller]
        axes[0].plot(part["PriceWindow"], part["TotalAnnualCostAUDm"], marker=marker, label=controller)
    axes[0].set_ylabel("Least-cost total (AUD m/yr)")
    axes[0].set_title("Least-cost compliant design cost by price window")
    axes[0].tick_params(axis="x", rotation=15)
    axes[0].legend(fontsize=8)
    axes[0].grid(alpha=0.25)
    for i, (window, part) in enumerate(frame[frame["Controller"] == "Rule-based"].groupby("PriceWindow")):
        part = part.sort_values("TotalAnnualCostAUDm")
        axes[1].bar(np.arange(len(part)) + 0.4 * i, part["TotalAnnualCostAUDm"], 0.4, label=window)
    axes[1].set_xlabel("Designs, cheapest first (rule-based)")
    axes[1].set_ylabel("Total annual cost (AUD m/yr)")
    axes[1].set_title("Spread of design costs at recent prices")
    axes[1].legend(fontsize=8)
    axes[1].grid(alpha=0.25, axis="y")
    fig.suptitle(f"Recent-price sensitivity, {site['name']}, training load, {limit:.0f} MW import limit")
    fig.tight_layout()
    save_figure(fig, RESULTS, "recent_prices")


if __name__ == "__main__":
    main()
