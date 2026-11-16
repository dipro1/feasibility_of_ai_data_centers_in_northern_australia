"""Single-year hourly PV + BESS demand-cap assessment at the primary site.

Uses the same NASA POWER files, pvlib solar model, facility load model and
dispatch rule as the multi-year and multi-site studies, so results are
directly comparable. The weather year and site come from study_config.json.
"""

from __future__ import annotations

import textwrap

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from studylib.config import INPUTS, RESULTS, load_config, read_csv_rows, site_by_id
from studylib.dispatch import dispatch
from studylib.economics import annualised_resource_cost, energy_and_emissions, hourly_price, network_charges
from studylib.facility import facility_load
from studylib.io import save_figure
from studylib.weather import load_site_weather


CONFIG = load_config()
RESOURCE = CONFIG["renewable_bess"]
CASES_PATH = INPUTS / "renewable_bess_cases.csv"


def main() -> None:
    site = site_by_id(RESOURCE["primary_site"])
    year = int(RESOURCE["single_year"])
    weather = load_site_weather(site["site_id"], [year])
    load = facility_load(weather, "training")
    demand = load["facility_mw"]
    price = hourly_price(weather["timestamp"], site["grid"], site["price_region"])
    cases = read_csv_rows(CASES_PATH)

    summaries, traces = [], {}
    for limit in RESOURCE["grid_limits_mw"]:
        for case in cases:
            pv = float(case["solar_mw"]) * weather["solar_cf"].to_numpy()
            power, energy = float(case["bess_power_mw"]), float(case["bess_energy_mwh"])
            grid_charging = case["allow_grid_charging"].strip().lower() == "true"
            result = dispatch(demand, pv, power, energy, limit, grid_charging)
            summaries.append(
                {
                    "SiteId": site["site_id"],
                    "WeatherYear": year,
                    "CaseId": case["case_id"],
                    "CaseLabel": case["label"],
                    "GridLimitMW": limit,
                    "SolarMW": float(case["solar_mw"]),
                    "BessPowerMW": power,
                    "BessEnergyMWh": energy,
                    "GridChargingAllowed": grid_charging,
                    **result.summary(demand, pv, energy),
                    **annualised_resource_cost(float(case["solar_mw"]), power, energy),
                    **energy_and_emissions(result.grid, price, site["price_region"] if site["grid"] == "NEM" else site["grid"], 1),
                    **network_charges(result.grid, weather["timestamp"], site["site_id"], 1),
                    "Classification": f"Chronological planning sensitivity, NASA POWER {year} weather, explicit load assumptions",
                }
            )
            traces[(case["case_id"], limit)] = pd.DataFrame(
                {
                    "timestamp": weather["timestamp"],
                    "temperature_c": weather["temp_c"],
                    "pue": load["pue"],
                    "facility_load_mw": demand,
                    "solar_generation_mw": pv,
                    "grid_import_mw": result.grid,
                    "bess_pv_charge_mw": result.pv_charge,
                    "bess_grid_charge_mw": result.grid_charge,
                    "bess_discharge_mw": result.discharge,
                    "bess_soc_mwh": result.soc,
                    "import_limit_exceedance_mw": result.exceedance,
                    "curtailed_solar_mw": result.curtailed,
                    "price_aud_per_mwh": price,
                }
            )

    frame = pd.DataFrame(summaries)
    frame.to_csv(RESULTS / "renewable_bess_summary.csv", index=False)
    selected_key = (RESOURCE["selected_trace_case"], RESOURCE["selected_trace_limit_mw"])
    if selected_key not in traces:
        raise KeyError(f"selected_trace_case {selected_key[0]!r} is not in {CASES_PATH.name}")
    selected = traces[selected_key]
    selected.to_csv(RESULTS / "renewable_bess_trace_selected_case.csv", index=False)

    limits = RESOURCE["grid_limits_mw"]
    labels = ["\n".join(textwrap.wrap(case["label"], 28)) for case in cases]
    y = np.arange(len(cases))
    width = 0.8 / len(limits)
    fig, axes = plt.subplots(1, 2, figsize=(14, 7.5))
    for offset, limit in enumerate(limits):
        subset = frame[frame["GridLimitMW"] == limit]
        axes[0].barh(y + (offset - (len(limits) - 1) / 2) * width, subset["HoursAboveImportLimit"], height=width, label=f"{limit:.0f} MW limit")
    axes[0].set_yticks(y, labels, fontsize=8)
    axes[0].invert_yaxis()
    axes[0].set_xlabel("Hours above import limit")
    axes[0].set_title("Import-limit compliance")
    axes[0].legend()
    axes[0].grid(axis="x", alpha=0.25)

    limit = RESOURCE["selected_trace_limit_mw"]
    subset = frame[frame["GridLimitMW"] == limit]
    colours = ["#2E8B57" if ok else "#C0392B" for ok in subset["CompliesWithImportLimit"]]
    axes[1].barh(y, subset["PeakGridImportMW"], color=colours)
    axes[1].axvline(limit, color="#B22222", linestyle="--", label=f"{limit:.0f} MW limit")
    axes[1].set_yticks(y, labels, fontsize=8)
    axes[1].invert_yaxis()
    axes[1].set_xlabel("Peak grid import (MW)")
    axes[1].set_title(f"Peak import at the {limit:.0f} MW limit (green = complies)")
    axes[1].legend()
    axes[1].grid(axis="x", alpha=0.25)
    fig.suptitle(f"{site['name']} {year}: PV and BESS demand-cap sensitivity (single-axis tracking PV)")
    fig.tight_layout()
    save_figure(fig, RESULTS, "renewable_bess_summary")

    start = int(selected["facility_load_mw"].rolling(168).mean().idxmax())
    week = selected.iloc[max(start - 167, 0): max(start - 167, 0) + 168]
    fig2, ax = plt.subplots(figsize=(12, 5.2))
    ax.plot(week["timestamp"], week["facility_load_mw"], label="Facility load", color="#222222")
    ax.plot(week["timestamp"], week["solar_generation_mw"], label="Solar", color="#E69F00")
    ax.plot(week["timestamp"], week["grid_import_mw"], label="Grid import", color="#0072B2")
    ax.plot(week["timestamp"], week["bess_discharge_mw"] - week["bess_pv_charge_mw"] - week["bess_grid_charge_mw"],
            label="BESS power (+ discharge, - charge)", color="#009E73")
    ax.axhline(limit, color="#B22222", linestyle="--", label=f"{limit:.0f} MW import limit")
    ax.set_ylabel("Power (MW)")
    label = next(case["label"] for case in cases if case["case_id"] == RESOURCE["selected_trace_case"])
    ax.set_title(f"Highest-demand week: {label}")
    ax.grid(alpha=0.25)
    ax.legend(ncol=3, fontsize=8)
    fig2.autofmt_xdate()
    fig2.tight_layout()
    save_figure(fig2, RESULTS, "renewable_bess_stress_week")
    print(frame[["CaseId", "GridLimitMW", "PeakGridImportMW", "HoursAboveImportLimit", "BatteryPvChargingMWh", "CompliesWithImportLimit"]].to_string(index=False))


if __name__ == "__main__":
    main()
