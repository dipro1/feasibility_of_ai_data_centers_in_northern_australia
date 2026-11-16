"""Backup supply and reliability screen.

Sizes N+R standby gensets for the facility peak, estimates their cost, fuel
use and emissions (testing plus expected grid outages), and the expected
unserved energy given unit fail-to-start / fail-to-run probabilities. It
also checks how long the site BESS could carry the facility on its own.
Outage frequency and duration are swept because utility reliability data
for the connection point are not public. Every parameter is a screening
assumption (see backup in study_config.json).
"""

from __future__ import annotations

import itertools
import math

import matplotlib.pyplot as plt
import pandas as pd
from scipy.stats import binom

from studylib.config import RESULTS, load_config, load_sites
from studylib.economics import capital_recovery_factor
from studylib.facility import facility_load
from studylib.io import save_figure, write_csv
from studylib.weather import load_site_weather


CONFIG = load_config()
BACKUP = CONFIG["backup"]
ECO = CONFIG["economics"]


def probability_short(units: int, needed: int, fail_probability: float) -> float:
    """Probability that fewer than `needed` of `units` independent units are available."""
    return float(binom.cdf(needed - 1, units, 1.0 - fail_probability))


def expected_shortfall_mw(units: int, needed: int, fail_probability: float, unit_mw: float) -> float:
    """Expected generation shortfall (MW) = E[max(needed - available, 0)] x unit size."""
    available = range(0, needed)
    return unit_mw * sum(binom.pmf(k, units, 1.0 - fail_probability) * (needed - k) for k in available)


def redundancy_for_target(needed: int, fail_probability: float, target: float) -> int:
    redundancy = 0
    while probability_short(needed + redundancy, needed, fail_probability) > target:
        redundancy += 1
    return redundancy


def main() -> None:
    years = CONFIG["renewable_bess"]["weather_years"]
    bess = CONFIG["renewable_bess"]
    rows, scenarios = [], []
    for site in load_sites():
        weather = load_site_weather(site["site_id"], years)
        load = facility_load(weather, "training")["facility_mw"]
        peak = float(load.max())
        mean = float(load.mean())
        needed = math.ceil(peak / BACKUP["genset_unit_mw"])
        recommended = redundancy_for_target(needed, BACKUP["unit_fail_to_start_probability"], BACKUP["target_start_failure_probability"])
        units = needed + max(int(BACKUP["redundancy_units"]), recommended)
        installed = units * BACKUP["genset_unit_mw"]
        capex = installed * 1000 * BACKUP["genset_capex_per_kw"]
        annualised = (capex * capital_recovery_factor(ECO["discount_rate"], BACKUP["genset_life_years"])
                      + installed * 1000 * BACKUP["genset_fixed_om_per_kw_year"]) / 1e6
        test_mwh = units * BACKUP["test_hours_per_unit_year"] * BACKUP["genset_unit_mw"] * BACKUP["test_load_fraction"]
        p_start_short = probability_short(units, needed, BACKUP["unit_fail_to_start_probability"])
        largest_battery = max(bess["bess_power_mw"]) * max(bess["bess_durations_h"])
        rows.append({
            "SiteId": site["site_id"], "Site": site["name"], "PeakDemandMW": peak,
            "GensetUnits": units, "UnitsNeededForPeak": needed, "InstalledBackupMW": installed,
            "BackupCapexAUDm": capex / 1e6, "AnnualisedBackupCostAUDm": annualised,
            "AnnualTestFuelML": test_mwh * 1000 * BACKUP["fuel_litres_per_kwh"] / 1e6,
            "AnnualTestEmissionsKt": test_mwh * 1000 * BACKUP["fuel_litres_per_kwh"] * BACKUP["diesel_kg_co2e_per_litre"] / 1e6,
            "FuelStorageML": peak * 1000 * BACKUP["fuel_storage_hours"] * BACKUP["fuel_litres_per_kwh"] / 1e6,
            "ProbabilityInsufficientUnitsAtStart": p_start_short,
            "ProbabilityInsufficientWithNPlus1": probability_short(needed + 1, needed, BACKUP["unit_fail_to_start_probability"]),
            "SpareUnitsForTargetRisk": recommended,
            **{f"StartFailureRiskNPlus{r}": probability_short(needed + r, needed, BACKUP["unit_fail_to_start_probability"])
               for r in BACKUP["redundancy_options"]},
            "UpsEnergyMWh": peak * BACKUP["ups_ride_through_minutes"] / 60.0,
            "LargestModelledBessBridgeHoursAtMeanLoad": largest_battery * (1 - bess["minimum_soc_fraction"]) / mean,
        })
        for frequency, duration in itertools.product(BACKUP["grid_outages_per_year"], BACKUP["mean_outage_duration_h"]):
            outage_energy = frequency * duration * mean  # MWh/yr that must be carried by backup
            fuel = outage_energy * 1000 * BACKUP["fuel_litres_per_kwh"]
            # A unit is unavailable if it fails to start or fails during the outage.
            p_unit_out = 1.0 - (1.0 - BACKUP["unit_fail_to_start_probability"]) * (1.0 - BACKUP["unit_fail_to_run_per_hour"]) ** duration
            shortfall = expected_shortfall_mw(units, math.ceil(mean / BACKUP["genset_unit_mw"]), p_unit_out, BACKUP["genset_unit_mw"])
            expected_unserved = frequency * duration * shortfall
            scenarios.append({
                "Site": site["name"], "OutagesPerYear": frequency, "MeanDurationH": duration,
                "BackupEnergyMWhPerYear": outage_energy,
                "OutageFuelCostAUDm": fuel * BACKUP["diesel_price_per_litre"] / 1e6,
                "OutageEmissionsKt": fuel * BACKUP["diesel_kg_co2e_per_litre"] / 1e6,
                "ExpectedUnservedMWhPerYear": expected_unserved,
                "ExpectedAvailabilityPercent": 100 * (1 - expected_unserved / (mean * 8760)),
            })
    write_csv(RESULTS / "backup_sizing.csv", rows)
    write_csv(RESULTS / "backup_outage_scenarios.csv", scenarios)

    sizing = pd.DataFrame(rows)
    outcomes = pd.DataFrame(scenarios)
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    axes[0].bar(sizing["Site"], sizing["AnnualisedBackupCostAUDm"], color="#0072B2")
    for i, row in enumerate(sizing.itertuples()):
        axes[0].text(i, row.AnnualisedBackupCostAUDm, f"{row.GensetUnits} x {BACKUP['genset_unit_mw']:g} MW", ha="center", va="bottom", fontsize=8)
    axes[0].set_ylabel("Annualised standby generation cost (AUD m/yr)")
    axes[0].set_title(f"Diesel standby sized for <= {BACKUP['target_start_failure_probability']:.1%} start-failure risk")
    axes[0].tick_params(axis="x", rotation=20)
    options = BACKUP["redundancy_options"]
    for row in sizing.itertuples():
        risks = [getattr(row, f"StartFailureRiskNPlus{r}") for r in options]
        axes[1].semilogy(options, risks, marker="o", label=row.Site)
    axes[1].axhline(BACKUP["target_start_failure_probability"], color="#B22222", linestyle="--",
                    label=f"Target {BACKUP['target_start_failure_probability']:.1%}")
    axes[1].set_xlabel("Spare genset units (N+R)")
    axes[1].set_ylabel("Probability too few units start")
    axes[1].set_title(f"Start-failure risk vs redundancy ({BACKUP['unit_fail_to_start_probability']:.0%} per-unit fail-to-start)")
    axes[1].legend(fontsize=7)
    axes[1].grid(alpha=0.25, which="both")
    fig.suptitle("Backup supply and reliability screen (all parameters assumed - VERIFY)")
    fig.tight_layout()
    save_figure(fig, RESULTS, "backup_reliability")
    print(sizing[["Site", "UnitsNeededForPeak", "SpareUnitsForTargetRisk", "GensetUnits", "InstalledBackupMW", "BackupCapexAUDm",
                  "AnnualisedBackupCostAUDm", "ProbabilityInsufficientWithNPlus1", "LargestModelledBessBridgeHoursAtMeanLoad"]].round(4).to_string(index=False))


if __name__ == "__main__":
    main()
