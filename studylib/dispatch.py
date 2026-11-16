"""Rule-based PV + BESS demand-cap dispatch (single implementation).

Each hour: PV serves load first; surplus PV charges the battery (then is
curtailed); if net load exceeds the import limit the battery discharges;
otherwise, when allowed, the battery charges from the grid up to the limit.
This is a transparent peak-shaving rule, not a price-optimised schedule.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .config import load_config


TOLERANCE_MW = 1e-6


@dataclass
class DispatchResult:
    grid: np.ndarray
    pv_to_load: np.ndarray
    pv_charge: np.ndarray
    grid_charge: np.ndarray
    discharge: np.ndarray
    soc: np.ndarray
    exceedance: np.ndarray
    curtailed: np.ndarray
    initial_soc: float

    def summary(self, demand: np.ndarray, pv: np.ndarray, energy_mwh: float) -> dict[str, float | int | bool]:
        discharge_total = float(self.discharge.sum())
        pv_charge_total = float(self.pv_charge.sum())
        grid_charge_total = float(self.grid_charge.sum())
        # Share of discharged energy attributable to PV charging (pro-rata).
        charged = pv_charge_total + grid_charge_total
        pv_share_of_discharge = pv_charge_total / charged if charged > 0 else 0.0
        onsite_renewable = float(self.pv_to_load.sum()) + discharge_total * pv_share_of_discharge
        return {
            "PeakFacilityLoadMW": float(demand.max()),
            "PeakGridImportMW": float(self.grid.max()),
            "HoursAboveImportLimit": int(np.count_nonzero(self.exceedance > TOLERANCE_MW)),
            "EnergyAboveImportLimitMWh": float(self.exceedance.sum()),
            "MaximumImportExceedanceMW": float(self.exceedance.max()),
            "SolarEnergyMWh": float(pv.sum()),
            "CurtailedSolarMWh": float(self.curtailed.sum()),
            "GridImportMWh": float(self.grid.sum()),
            "BatteryDischargeMWh": discharge_total,
            "BatteryPvChargingMWh": pv_charge_total,
            "BatteryGridChargingMWh": grid_charge_total,
            "MinimumSocMWh": float(self.soc.min()) if len(self.soc) else 0.0,
            "FinalSocMWh": float(self.soc[-1]) if len(self.soc) else 0.0,
            "EquivalentFullCycles": discharge_total / energy_mwh if energy_mwh > 0 else 0.0,
            "OnsiteRenewableFraction": onsite_renewable / float(demand.sum()),
            "CompliesWithImportLimit": bool(np.all(self.exceedance <= TOLERANCE_MW)),
        }


def dispatch(
    demand: np.ndarray,
    pv: np.ndarray,
    bess_power_mw: float,
    bess_energy_mwh: float,
    import_limit_mw: float,
    allow_grid_charging: bool,
) -> DispatchResult:
    resource = load_config()["renewable_bess"]
    eta = math.sqrt(resource["round_trip_efficiency"])
    minimum_soc = resource["minimum_soc_fraction"] * bess_energy_mwh
    maximum_soc = bess_energy_mwh
    soc = initial_soc = resource["initial_soc_fraction"] * bess_energy_mwh
    has_battery = bess_energy_mwh > 0 and bess_power_mw > 0

    n = len(demand)
    grid = [0.0] * n
    pv_to_load = [0.0] * n
    pv_charge = [0.0] * n
    grid_charge = [0.0] * n
    discharge = [0.0] * n
    soc_trace = [0.0] * n
    exceedance = [0.0] * n
    curtailed = [0.0] * n

    for i, (load, generation) in enumerate(zip(demand.tolist(), pv.tolist())):
        direct = load if generation > load else generation
        remaining = load - direct
        surplus = generation - direct
        if has_battery:
            charge_pv = min(surplus, bess_power_mw, max(maximum_soc - soc, 0.0) / eta)
            soc += charge_pv * eta
            surplus -= charge_pv
            pv_charge[i] = charge_pv
            if remaining > import_limit_mw:
                available = max(soc - minimum_soc, 0.0) * eta
                out = min(remaining - import_limit_mw, bess_power_mw, available)
                soc -= out / eta
                remaining -= out
                discharge[i] = out
            elif allow_grid_charging:
                charge_grid = min(
                    import_limit_mw - remaining,
                    max(bess_power_mw - charge_pv, 0.0),
                    max(maximum_soc - soc, 0.0) / eta,
                )
                if charge_grid > 0:
                    soc += charge_grid * eta
                    remaining += charge_grid
                    grid_charge[i] = charge_grid
        grid[i] = remaining
        pv_to_load[i] = direct
        exceedance[i] = remaining - import_limit_mw if remaining > import_limit_mw else 0.0
        curtailed[i] = surplus
        soc_trace[i] = soc

    return DispatchResult(
        grid=np.asarray(grid),
        pv_to_load=np.asarray(pv_to_load),
        pv_charge=np.asarray(pv_charge),
        grid_charge=np.asarray(grid_charge),
        discharge=np.asarray(discharge),
        soc=np.asarray(soc_trace),
        exceedance=np.asarray(exceedance),
        curtailed=np.asarray(curtailed),
        initial_soc=initial_soc,
    )
