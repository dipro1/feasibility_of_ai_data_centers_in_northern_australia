"""Compare the rule-based battery dispatch with a price-aware LP benchmark.

For a set of PV/BESS designs at the primary site and import limit, both
controllers are run over all weather/price years. The LP uses perfect
foresight, so its savings are an upper bound on what a real price-aware
controller could achieve. Note that in the LP, PV can be diverted to the
battery while the grid serves the load, so "solar-only charging" is not a
meaningful restriction there; all LP cases allow grid charging. A third,
realistic controller plans each day on persistence forecasts (yesterday's
prices and PV) with an energy reserve for import-limit duty, and shows how
much of the perfect-foresight saving is achievable in practice.

The comparison runs over a grid of designs so that the least-cost design can
be selected under each controller: if price-aware operation is assumed, the
economic battery size changes.
"""

from __future__ import annotations

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
    time_matched_metrics,
)
from studylib.facility import facility_load
from studylib.io import save_figure
from studylib.optimal_dispatch import forecast_dispatch, optimal_dispatch
from studylib.weather import load_site_weather


CONFIG = load_config()
RESOURCE = CONFIG["renewable_bess"]
DESIGNS = [(pv, power, energy) for pv in CONFIG["price_aware_dispatch"]["design_pv_mw"]
           for power, energy in CONFIG["price_aware_dispatch"]["design_bess"]]


def evaluate(name, result, demand, pv, price, weather, site, energy_mwh, years):
    emission_key = site["price_region"] if site["grid"] == "NEM" else site["grid"]
    energy = energy_and_emissions(result.grid, price, emission_key, years)
    network = network_charges(result.grid, weather["timestamp"], site["site_id"], years)
    return {
        "Controller": name,
        **result.summary(demand, pv, energy_mwh),
        **energy,
        **network,
        **time_matched_metrics(result, demand, weather["timestamp"], emission_key, years),
        "PeakImportMW": float(result.grid.max()),
    }


def main() -> None:
    site = site_by_id(RESOURCE["primary_site"])
    years = RESOURCE["weather_years"]
    limit = RESOURCE["selected_trace_limit_mw"]
    weather = load_site_weather(site["site_id"], years)
    demand = facility_load(weather, "training")["facility_mw"]
    price = hourly_price(weather["timestamp"], site["grid"], site["price_region"])

    rows, yearly, traces = [], [], {}
    for pv_mw, power, energy_mwh in DESIGNS:
        pv = pv_mw * weather["solar_cf"].to_numpy()
        label = f"{pv_mw:.0f} MW PV + {power:.0f} MW/{energy_mwh:.0f} MWh"
        resource_cost = annualised_resource_cost(pv_mw, power, energy_mwh)["AnnualisedResourceCostAUDm"]
        results = {
            "Rule-based": dispatch(demand, pv, power, energy_mwh, limit, True),
            "Price-aware LP (perfect foresight)": optimal_dispatch(demand, pv, price, max(power, 1e-3), max(energy_mwh, 1e-3), limit, True),
            "Price-aware, day-ahead forecast": forecast_dispatch(demand, pv, price, max(power, 1e-3), max(energy_mwh, 1e-3), limit, True),
        }
        for name, result in results.items():
            row = evaluate(name, result, demand, pv, price, weather, site, energy_mwh, len(years))
            row.update({"Design": label, "PvMW": pv_mw, "BessPowerMW": power, "BessEnergyMWh": energy_mwh,
                        "ImportLimitMW": limit, "AnnualisedResourceCostAUDm": resource_cost})
            row["TotalAnnualCostAUDm"] = resource_cost + row["AnnualEnergyCostAUDm"] + row["NetworkChargesAUDm"]
            rows.append(row)
            cost_by_year = pd.Series(result.grid * price).groupby(weather["timestamp"].dt.year.to_numpy()).sum() / 1e6
            for year, value in cost_by_year.items():
                yearly.append({"Design": label, "Controller": name, "Year": int(year), "EnergyCostAUDm": float(value)})
            traces[(label, name)] = result
        print(f"{label}: done", flush=True)

    frame = pd.DataFrame(rows)
    rule = frame[frame["Controller"] == "Rule-based"].set_index("Design")
    lp = frame[frame["Controller"] == "Price-aware LP (perfect foresight)"].set_index("Design")
    forecast = frame[frame["Controller"] == "Price-aware, day-ahead forecast"].set_index("Design")
    frame["SavingVsRuleAUDm"] = frame.apply(
        lambda r: rule.loc[r["Design"], "TotalAnnualCostAUDm"] - r["TotalAnnualCostAUDm"], axis=1)
    perfect_saving = rule["TotalAnnualCostAUDm"] - lp["TotalAnnualCostAUDm"]
    forecast_saving = rule["TotalAnnualCostAUDm"] - forecast["TotalAnnualCostAUDm"]
    capture = (forecast_saving / perfect_saving.where(perfect_saving > 0.5)).rename("ForecastCaptureOfPerfectSaving")
    frame = frame.merge(capture, left_on="Design", right_index=True, how="left")
    frame.to_csv(RESULTS / "price_aware_dispatch_comparison.csv", index=False)
    compliant = frame[frame["CompliesWithImportLimit"]]
    selection = (compliant.sort_values("TotalAnnualCostAUDm").groupby("Controller", as_index=False).first()
                 [["Controller", "Design", "TotalAnnualCostAUDm", "AnnualScope2EmissionsKt", "HourlyMatchedEmissionsKt", "CfeScore"]])
    selection.to_csv(RESULTS / "price_aware_least_cost_selection.csv", index=False)
    yearly_frame = pd.DataFrame(yearly)
    yearly_frame.to_csv(RESULTS / "price_aware_dispatch_by_year.csv", index=False)

    fig, axes = plt.subplots(1, 3, figsize=(18, 5.4))
    designs = list(dict.fromkeys(frame["Design"]))
    x = np.arange(len(designs))
    axes[0].bar(x - 0.27, rule.loc[designs, "TotalAnnualCostAUDm"], 0.27, label="Rule-based", color="#888888")
    axes[0].bar(x, forecast.loc[designs, "TotalAnnualCostAUDm"], 0.27, label="Price-aware, day-ahead forecast", color="#56B4E9")
    axes[0].bar(x + 0.27, lp.loc[designs, "TotalAnnualCostAUDm"], 0.27, label="Price-aware, perfect foresight (upper bound)", color="#0072B2")
    axes[0].set_xticks(x, [d.replace(" MW PV + ", "/") for d in designs], fontsize=6, rotation=60, ha="right")
    axes[0].set_ylabel("Total annual cost (AUD m)")
    axes[0].set_title(f"Total cost, {limit:.0f} MW import limit")
    axes[0].legend(fontsize=8)
    axes[0].grid(axis="y", alpha=0.25)

    for design in dict.fromkeys(selection["Design"]):
        rule_year = yearly_frame[(yearly_frame["Design"] == design) & (yearly_frame["Controller"] == "Rule-based")].set_index("Year")
        lp_year = yearly_frame[(yearly_frame["Design"] == design) & (yearly_frame["Controller"] == "Price-aware LP (perfect foresight)")].set_index("Year")
        axes[1].plot(rule_year.index, rule_year["EnergyCostAUDm"] - lp_year["EnergyCostAUDm"], marker="o", label=design)
    axes[1].set_xlabel("Year")
    axes[1].set_xticks(sorted(yearly_frame["Year"].unique()))
    axes[1].set_ylabel("Energy-cost saving from price-aware dispatch (AUD m)")
    axes[1].set_title("Savings depend on price volatility (2022 energy crisis)")
    axes[1].legend(fontsize=7)
    axes[1].grid(alpha=0.25)

    focus = selection.loc[selection["Controller"] == "Price-aware LP (perfect foresight)", "Design"].iloc[0]
    week = slice(24 * 200, 24 * 207)
    timestamps = weather["timestamp"].iloc[week]
    axes[2].plot(timestamps, traces[(focus, "Rule-based")].soc[week], color="#888888", label="SOC, rule-based")
    axes[2].plot(timestamps, traces[(focus, "Price-aware LP (perfect foresight)")].soc[week], color="#0072B2", label="SOC, perfect foresight")
    axes[2].plot(timestamps, traces[(focus, "Price-aware, day-ahead forecast")].soc[week], color="#56B4E9", linestyle="--", label="SOC, day-ahead forecast")
    twin = axes[2].twinx()
    twin.plot(timestamps, price[week], color="#D55E00", alpha=0.6, linewidth=1, label="Price")
    twin.set_ylabel("Price (AUD/MWh)", color="#D55E00")
    axes[2].set_ylabel("Battery state of charge (MWh)")
    axes[2].set_title(f"Example week: {focus}")
    axes[2].legend(fontsize=7, loc="upper left")
    axes[2].tick_params(axis="x", rotation=30)
    fig.suptitle(f"{site['name']}: rule-based vs price-aware battery dispatch ({min(years)}-{max(years)} prices)")
    fig.tight_layout()
    save_figure(fig, RESULTS, "price_aware_dispatch")
    print(selection.round(2).to_string(index=False))
    print(frame[["Design", "Controller", "TotalAnnualCostAUDm", "SavingVsRuleAUDm", "HoursAboveImportLimit", "CurtailedSolarMWh", "EquivalentFullCycles", "AnnualScope2EmissionsKt", "HourlyMatchedEmissionsKt", "CfeScore"]].round(2).to_string(index=False))


if __name__ == "__main__":
    main()
