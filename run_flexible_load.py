"""The data centre as a flexible load (Rockhampton, training, 2020-2024).

A training cluster can pause checkpointed jobs at short notice. Pausing saves
energy and network cost but forgoes compute, valued at an assumed lost-compute
value per IT MWh (swept, see flexible_load in the config). Three uses of that
flexibility are compared with the base designs:

1. Price response: pause the flexible share of IT whenever the spot price is
   above the lost-compute value (a real-time rule, no forecast needed).
2. Import limit by curtailment: no battery; the facility trims its own load
   whenever grid import would exceed the 200 MW limit (with 0-400 MW PV).
3. Both: the least-cost rule-based PV/battery design plus price response.

Totals include annualised PV/battery cost, energy, Powerlink network charges
and the value of lost compute. Outputs: results/flexible_load.csv and
results/flexible_load.png.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from studylib.config import RESULTS, load_config, site_by_id
from studylib.dispatch import dispatch
from studylib.economics import annualised_resource_cost, energy_and_emissions, hourly_price, network_charges
from studylib.facility import facility_load
from studylib.io import save_figure, write_csv
from studylib.weather import load_site_weather


CONFIG = load_config()
RESOURCE = CONFIG["renewable_bess"]
FLEX = CONFIG["flexible_load"]
NO_LIMIT = 1e9


def costs(grid, price, weather, site, years, pv_mw, power, energy_mwh, lost_it_mwh, value):
    energy = energy_and_emissions(grid, price, site["price_region"], years)
    network = network_charges(grid, weather["timestamp"], site["site_id"], years)
    resource = annualised_resource_cost(pv_mw, power, energy_mwh)["AnnualisedResourceCostAUDm"]
    lost = lost_it_mwh * value / years / 1e6
    return {
        "AnnualisedResourceCostAUDm": resource, "AnnualEnergyCostAUDm": energy["AnnualEnergyCostAUDm"],
        "NetworkChargesAUDm": network["NetworkChargesAUDm"], "LostComputeValueAUDm": lost,
        "TotalAnnualCostAUDm": resource + energy["AnnualEnergyCostAUDm"] + network["NetworkChargesAUDm"] + lost,
        "AnnualScope2EmissionsKt": energy["AnnualScope2EmissionsKt"],
    }


def price_response(load: dict, price: np.ndarray, value: float) -> tuple[np.ndarray, np.ndarray]:
    """Facility demand after pausing the flexible IT share in hours priced above the lost-compute value."""
    paused = price > value
    it_paused = np.where(paused, FLEX["flexible_it_share"] * load["it_mw"], 0.0)
    return load["facility_mw"] - it_paused * load["pue"], it_paused


def main() -> None:
    site = site_by_id(RESOURCE["primary_site"])
    years_list = RESOURCE["weather_years"]
    years = len(years_list)
    limit = RESOURCE["selected_trace_limit_mw"]
    weather = load_site_weather(site["site_id"], years_list)
    price = hourly_price(weather["timestamp"], site["grid"], site["price_region"])
    load = facility_load(weather, "training")
    base_pv, base_power, base_energy = 200.0, 50.0, 100.0  # least-cost rule-based design (base studies)
    rows = []

    def record(case, value, grid, pv_mw, power, energy_mwh, lost_it, hours_paused, complies):
        rows.append({"Case": case, "LostComputeValuePerMWh": value, "PvMW": pv_mw, "BessPowerMW": power,
                     "BessEnergyMWh": energy_mwh, "PeakImportMW": float(grid.max()), "MeetsImportLimit": bool(complies),
                     "HoursPausedPerYear": hours_paused / years, "LostItGWhPerYear": lost_it / years / 1000.0,
                     **costs(grid, price, weather, site, years, pv_mw, power, energy_mwh, lost_it, value)})

    solar = weather["solar_cf"].to_numpy()
    # Reference designs without flexibility.
    for pv_mw, power, energy_mwh, case in [(0, 0, 0, "Grid only (no flexibility)"),
                                            (base_pv, base_power, base_energy, "Base design, no flexibility")]:
        result = dispatch(load["facility_mw"], pv_mw * solar, power, energy_mwh, limit, True)
        record(case, 0.0, result.grid, pv_mw, power, energy_mwh, 0.0, 0, result.grid.max() <= limit + 1e-6)

    for value in FLEX["lost_compute_value_per_mwh"]:
        # 1. Price response only (grid supply, no import limit).
        demand, it_paused = price_response(load, price, value)
        record("Price response only", value, demand, 0, 0, 0, it_paused.sum(), (it_paused > 0).sum(),
               demand.max() <= limit + 1e-6)
        # 2. Import limit by curtailment (no battery), for each PV size.
        best = None
        for pv_mw in CONFIG["price_aware_dispatch"]["design_pv_mw"] + [0]:
            result = dispatch(load["facility_mw"], pv_mw * solar, 0.0, 0.0, NO_LIMIT, False)
            curtail = np.maximum(result.grid - limit, 0.0)
            flexible = FLEX["flexible_it_share"] * load["it_mw"] * load["pue"]
            feasible = bool((curtail <= flexible + 1e-6).all())
            grid = result.grid - curtail
            lost_it = float((curtail / load["pue"]).sum())
            row_cost = costs(grid, price, weather, site, years, pv_mw, 0, 0, lost_it, value)["TotalAnnualCostAUDm"]
            if feasible and (best is None or row_cost < best[0]):
                best = (row_cost, pv_mw, grid, lost_it, int((curtail > 0).sum()))
        if best:
            _, pv_mw, grid, lost_it, hours = best
            record("Import limit by curtailment (no battery)", value, grid, pv_mw, 0, 0, lost_it, hours, True)
        # 3. Base design plus price response.
        result = dispatch(demand, base_pv * solar, base_power, base_energy, limit, True)
        record("Base design + price response", value, result.grid, base_pv, base_power, base_energy,
               it_paused.sum(), (it_paused > 0).sum(), result.grid.max() <= limit + 1e-6)
        print(f"lost-compute value A${value:,.0f}/MWh: done", flush=True)

    frame = pd.DataFrame(rows)
    reference = frame.loc[frame["Case"] == "Base design, no flexibility", "TotalAnnualCostAUDm"].iloc[0]
    frame["SavingVsBaseDesignAUDm"] = reference - frame["TotalAnnualCostAUDm"]
    write_csv(RESULTS / "flexible_load.csv", frame.to_dict("records"))
    print(frame[["Case", "LostComputeValuePerMWh", "PvMW", "BessPowerMW", "HoursPausedPerYear", "LostItGWhPerYear",
                 "LostComputeValueAUDm", "TotalAnnualCostAUDm", "SavingVsBaseDesignAUDm", "MeetsImportLimit"]]
          .round(2).to_string(index=False))

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    flexible_cases = ["Price response only", "Import limit by curtailment (no battery)", "Base design + price response"]
    values = FLEX["lost_compute_value_per_mwh"]
    x = np.arange(len(values))
    for i, case in enumerate(flexible_cases):
        part = frame[frame["Case"] == case].set_index("LostComputeValuePerMWh").reindex(values)
        axes[0].bar(x + (i - 1) * 0.27, part["TotalAnnualCostAUDm"], 0.27, label=case)
        axes[1].bar(x + (i - 1) * 0.27, part["HoursPausedPerYear"], 0.27, label=case)
    axes[0].axhline(reference, color="black", linestyle="--", linewidth=1, label="Base design, no flexibility")
    axes[0].set_ylabel("Total annual cost incl. lost compute (AUD m/yr)")
    axes[1].set_ylabel("Hours per year with training paused or trimmed")
    for ax in axes:
        ax.set_xticks(x, [f"A${v:,.0f}/MWh" for v in values])
        ax.set_xlabel("Assumed value of lost compute")
        ax.grid(alpha=0.25, axis="y")
        ax.legend(fontsize=7)
    fig.suptitle("Training-load flexibility at Rockhampton, 2020-2024 prices, 200 MW import limit")
    fig.tight_layout()
    save_figure(fig, RESULTS, "flexible_load")


if __name__ == "__main__":
    main()
