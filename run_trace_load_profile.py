"""Facility load from a real GPU-cluster trace instead of the synthetic profile.

The base studies use a synthetic IT profile (studylib/facility.py). This
replaces it with the hourly GPU-allocation profile of the public Alibaba PAI
GPU trace (build_gpu_trace_profile.py), tiled over the 2020-2024 weather years
by day and hour (the trace's clock phase is not documented, so its daily
pattern is used as recorded). Two versions are tested:

* trace as recorded: IT load = capacity x allocated share (mean about 50%);
* trace at training utilisation: the same shape rescaled to the synthetic
  training mean (90%), capped at installed capacity.

For each profile the study reports peak and mean facility demand, hourly
ramps, hours above the 200 MW import limit and the least-cost rule-based
PV/battery design, and compares them with the synthetic profiles.
Outputs: results/trace_load_profile_comparison.csv and
results/trace_load_profile.png.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from studylib.config import INPUTS, RESULTS, load_config, site_by_id
from studylib.dispatch import dispatch
from studylib.economics import annualised_resource_cost, energy_and_emissions, hourly_price, network_charges
from studylib.facility import facility_load
from studylib.io import save_figure, write_csv
from studylib.weather import load_site_weather


CONFIG = load_config()
RESOURCE = CONFIG["renewable_bess"]
FACILITY = CONFIG["facility"]
# The base design grid plus larger batteries, which a load running at full IT capacity needs.
DESIGNS = [(pv, power, energy) for pv in CONFIG["price_aware_dispatch"]["design_pv_mw"]
           for power, energy in CONFIG["price_aware_dispatch"]["design_bess"] + [[100, 800], [100, 1200], [150, 1200]]]


def tiled_trace(timestamps: pd.Series) -> np.ndarray:
    trace = pd.read_csv(INPUTS / "traces" / "alibaba_gpu_hourly.csv")
    days = int(trace["trace_day"].max()) + 1
    table = trace.pivot(index="trace_day", columns="hour_of_day", values="allocation_fraction").to_numpy()
    day_index = (timestamps.dt.normalize() - timestamps.dt.normalize().iloc[0]).dt.days.to_numpy() % days
    return table[day_index, timestamps.dt.hour.to_numpy()]


def profiles(weather: pd.DataFrame) -> dict[str, np.ndarray]:
    pue = facility_load(weather, "training")["pue"]
    allocation = tiled_trace(weather["timestamp"])
    capacity = FACILITY["it_capacity_mw"]
    scaled = np.minimum(allocation / allocation.mean() * FACILITY["training_utilisation"], 1.0)
    out = {f"Synthetic {name}": facility_load(weather, name)["facility_mw"] for name in ("training", "mixed", "inference")}
    out["Alibaba trace, as recorded"] = capacity * allocation * pue
    out["Alibaba trace, at training utilisation"] = capacity * scaled * pue
    return out


def largest_excess_mwh(demand: np.ndarray, limit: float) -> float:
    """Largest energy above the limit in one continuous spell (storage needed without PV or recharge)."""
    excess = np.maximum(demand - limit, 0.0)
    best = run = 0.0
    for value in excess:
        run = run + value if value > 0 else 0.0
        best = max(best, run)
    return float(best)


def least_cost(demand, weather, price, site, limit, years) -> dict[str, object]:
    best = None
    for pv_mw, power, energy_mwh in DESIGNS:
        pv = pv_mw * weather["solar_cf"].to_numpy()
        result = dispatch(demand, pv, power, energy_mwh, limit, True)
        summary = result.summary(demand, pv, energy_mwh)
        if not summary["CompliesWithImportLimit"]:
            continue
        cost = (annualised_resource_cost(pv_mw, power, energy_mwh)["AnnualisedResourceCostAUDm"]
                + energy_and_emissions(result.grid, price, site["price_region"], years)["AnnualEnergyCostAUDm"]
                + network_charges(result.grid, weather["timestamp"], site["site_id"], years)["NetworkChargesAUDm"])
        if best is None or cost < best["LeastCostTotalAUDm"]:
            best = {"LeastCostDesign": f"{pv_mw:.0f} MW PV + {power:.0f} MW/{energy_mwh:.0f} MWh",
                    "LeastCostTotalAUDm": cost}
    return best or {"LeastCostDesign": "none in design grid", "LeastCostTotalAUDm": float("nan")}


def main() -> None:
    site = site_by_id(RESOURCE["primary_site"])
    years = RESOURCE["weather_years"]
    limit = RESOURCE["selected_trace_limit_mw"]
    weather = load_site_weather(site["site_id"], years)
    price = hourly_price(weather["timestamp"], site["grid"], site["price_region"])
    loads = profiles(weather)
    rows = []
    for name, demand in loads.items():
        ramps = np.abs(np.diff(demand))
        rows.append({
            "Profile": name, "PeakFacilityMW": float(demand.max()), "MeanFacilityMW": float(demand.mean()),
            "LoadFactor": float(demand.mean() / demand.max()), "P99HourlyRampMW": float(np.percentile(ramps, 99)),
            "MaxHourlyRampMW": float(ramps.max()),
            "HoursPerYearAboveLimit": float((demand > limit).sum() / len(years)),
            "LargestContinuousExcessMWh": largest_excess_mwh(demand, limit),
            "AnnualEnergyGWh": float(demand.sum() / len(years) / 1000.0),
            **least_cost(demand, weather, price, site, limit, len(years)),
        })
        print(f"{name}: done", flush=True)
    frame = pd.DataFrame(rows)
    write_csv(RESULTS / "trace_load_profile_comparison.csv", frame.to_dict("records"))
    print(frame.round(2).to_string(index=False))

    fig, axes = plt.subplots(1, 2, figsize=(15, 5))
    week = slice(24 * 180, 24 * 194)
    for name, demand in loads.items():
        axes[0].plot(weather["timestamp"].iloc[week], demand[week], label=name, linewidth=1.2)
        axes[1].plot(np.linspace(0, 100, len(demand)), np.sort(demand)[::-1], label=name, linewidth=1.4)
    for ax in axes:
        ax.axhline(limit, color="#B22222", linestyle="--", linewidth=1, label=f"{limit:.0f} MW import limit")
        ax.set_ylabel("Facility demand (MW)")
        ax.grid(alpha=0.25)
    axes[0].set_title("Two example weeks")
    axes[0].tick_params(axis="x", rotation=30)
    axes[1].set_title("Load duration curves, 2020-2024")
    axes[1].set_xlabel("Percentage of time (%)")
    axes[1].legend(fontsize=7)
    fig.suptitle("Synthetic facility profiles vs a real GPU-cluster allocation trace (Alibaba PAI, 2020)")
    fig.tight_layout()
    save_figure(fig, RESULTS, "trace_load_profile")


if __name__ == "__main__":
    main()
