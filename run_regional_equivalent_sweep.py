"""Thevenin-equivalent screening of candidate connection points (OpenDSS).

Each row of inputs/site_cases.csv becomes a Thevenin source built from a
published fault current. For every case the script reports:

* the steady-state voltage-limited load (OpenDSS sweep, 1 MW resolution);
* the published thermal headroom limit where one exists;
* the short-circuit ratio (SCR) at the connection target;
* the PV-curve nose (maximum transferable power) and margin to it;
* the voltage step when the whole facility trips, and when a training
  cluster swings by the configured fraction of IT load;
* a power-factor x source-voltage sensitivity grid.

These are screening equivalents, not utility models.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from studylib import network as net
from studylib.config import RESULTS, SITE_CASES_PATH, load_config, ner_minimum_pf, read_csv_rows
from studylib.io import write_csv


CONFIG = load_config()
CRITERIA = CONFIG["network_criteria"]
CONNECTION = CONFIG["connection"]
SWEEP = CONFIG["regional_equivalent"]


def optional_float(value: str) -> float | None:
    value = value.strip()
    return None if not value else float(value)


def check_load(row: dict[str, str], load_mw: float, power_factor: float, minimum_voltage_pu: float) -> dict[str, object]:
    """Solve one load level in the already-built OpenDSS case."""
    headroom = optional_float(row["thermal_headroom_mva"])
    net.set_load("ai_data_center", load_mw, power_factor)
    converged = net.solve()
    voltage = net.bus_minimum_pu("sourcebus") if converged else float("nan")
    load_mva = load_mw / power_factor
    voltage_pass = converged and voltage >= minimum_voltage_pu
    thermal_pass = headroom is None or load_mva <= headroom + 1e-9
    return {
        "converged": converged,
        "voltage": voltage,
        "voltage_pass": voltage_pass,
        "thermal_pass": thermal_pass,
        "loading": "" if headroom is None else 100.0 * load_mva / headroom,
        "passes": converged and voltage_pass and thermal_pass,
    }


def binding_reason(result: dict[str, object], minimum_voltage_pu: float) -> str:
    if not result["converged"]:
        return "Power flow convergence (beyond PV-curve nose)"
    if not result["thermal_pass"]:
        return "Published thermal headroom"
    return f"Voltage below {minimum_voltage_pu:g} pu"


def bisect_limit(row, fault_mva, power_factor, source_pu, minimum_voltage_pu, maximum_mw) -> tuple[float, str]:
    """Largest whole-MW load that passes (monotonic below the PV nose)."""
    net.build_thevenin_case(float(row["voltage_kv"]), fault_mva, float(row["source_xr"]), source_pu)
    if not check_load(row, 0.0, power_factor, minimum_voltage_pu)["passes"]:
        return 0.0, "Base case already fails"
    if check_load(row, maximum_mw, power_factor, minimum_voltage_pu)["passes"]:
        return maximum_mw, "Sweep maximum reached"
    low, high = 0, int(maximum_mw)
    failing = None
    while high - low > 1:
        middle = (low + high) // 2
        result = check_load(row, float(middle), power_factor, minimum_voltage_pu)
        if result["passes"]:
            low = middle
        else:
            high, failing = middle, result
    if failing is None:
        failing = check_load(row, float(high), power_factor, minimum_voltage_pu)
    return float(low), binding_reason(failing, minimum_voltage_pu)


def run_case(row: dict[str, str], step_mw: float, maximum_mw: float, minimum_voltage_pu: float, source_pu: float):
    voltage_kv = float(row["voltage_kv"])
    xr = float(row["source_xr"])
    fault_mva = net.fault_level_mva(voltage_kv, float(row["fault_current_3ph_ka"]))
    power_factor = ner_minimum_pf(voltage_kv)
    target = CONNECTION["target_mw"]

    net.build_thevenin_case(voltage_kv, fault_mva, xr, source_pu)
    base = check_load(row, 0.0, power_factor, minimum_voltage_pu)
    detail: list[dict[str, object]] = []
    last_pass, first_fail, binding = 0.0, "", "Sweep maximum reached"
    end = base
    if not base["passes"]:
        binding = "Base case already fails"
    else:
        for step in range(1, int(math.floor(maximum_mw / step_mw + 1e-9)) + 1):
            load_mw = step * step_mw
            end = check_load(row, load_mw, power_factor, minimum_voltage_pu)
            detail.append(
                {
                    "SiteId": row["site_id"],
                    "ConnectionPoint": row["connection_point"],
                    "Case": row["case"],
                    "LoadMW": load_mw,
                    "PowerFactor": power_factor,
                    "PointVoltagePu": end["voltage"],
                    "ThermalLoadingPercent": end["loading"],
                    "VoltagePass": end["voltage_pass"],
                    "ThermalPass": end["thermal_pass"],
                    "Converged": end["converged"],
                    "OverallPass": end["passes"],
                }
            )
            if not end["passes"]:
                first_fail = load_mw
                binding = binding_reason(end, minimum_voltage_pu)
                break
            last_pass = load_mw

    nose = net.maximum_transfer_mw(voltage_kv, fault_mva, xr, power_factor, source_pu)
    swing_mw = CONNECTION["training_swing_fraction_of_it"] * CONFIG["facility"]["it_capacity_mw"] * CONFIG["facility"]["base_pue"]
    trip_step = net.voltage_step_percent(voltage_kv, fault_mva, xr, target, power_factor, target, source_pu) if target < nose else float("nan")
    swing_step = net.voltage_step_percent(voltage_kv, fault_mva, xr, swing_mw, power_factor, target, source_pu) if target < nose else float("nan")
    step_limit = CONNECTION["load_trip_step_limit_percent"]
    thermal = optional_float(row["thermal_headroom_mva"])
    summary = {
        "SiteId": row["site_id"],
        "Region": row["region"],
        "ConnectionPoint": row["connection_point"],
        "Case": row["case"],
        "VoltageKV": voltage_kv,
        "FaultCurrent3phKA": float(row["fault_current_3ph_ka"]),
        "FaultCurrentBasis": row["fault_current_basis"],
        "FaultLevelMVA": fault_mva,
        "SourceXR": xr,
        "SourceVoltagePu": source_pu,
        "ThermalHeadroomMVA": "" if thermal is None else thermal,
        "BasePowerFactor": power_factor,
        "MinimumVoltagePu": minimum_voltage_pu,
        "BaseCasePasses": base["passes"],
        "LastPassingMW": last_pass,
        "FirstFailingMW": first_fail,
        "BindingConstraint": binding,
        "TargetMW": target,
        "TargetPassesScreen": last_pass >= target,
        "SCRAtTarget": net.short_circuit_ratio(fault_mva, target),
        "PVNoseMW": nose,
        "NoseMarginAtTarget": nose / target,
        "FullTripVoltageStepPercent": trip_step,
        "TrainingSwingMW": swing_mw,
        "TrainingSwingVoltageStepPercent": swing_step,
        "StepLimitPercent": step_limit,
        "StepLimitBasis": "Screening assumption (NER S5.1.5, S5.3.7); to be confirmed by Powerlink for the connection",
        "FullTripStepPasses": (not math.isnan(trip_step)) and trip_step <= step_limit,
        "ThermalAssessment": "Published headroom applied" if thermal is not None else "Not assessed - cannot be inferred from fault level; requires line/transformer ratings, existing flows and N-1 load-flow results from the network owner",
        "Classification": (
            "Voltage-and-thermal public-data upper-bound screen" if thermal is not None
            else "Voltage-only Thevenin screening equivalent"
        ),
        "DataStatus": row["data_status"],
        "SourceReference": row["source_reference"],
    }

    sensitivity = []
    for pf in CRITERIA["power_factor_sensitivity"]:
        for vs in CRITERIA["source_voltage_sensitivity_pu"]:
            limit, reason = bisect_limit(row, fault_mva, pf, vs, minimum_voltage_pu, maximum_mw)
            sensitivity.append(
                {
                    "SiteId": row["site_id"],
                    "ConnectionPoint": row["connection_point"],
                    "Case": row["case"],
                    "PowerFactor": pf,
                    "SourceVoltagePu": vs,
                    "LastPassingMW": limit,
                    "BindingConstraint": reason,
                    "TargetPasses": limit >= target,
                }
            )
    return detail, summary, sensitivity


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=SITE_CASES_PATH)
    parser.add_argument("--results", type=Path, default=RESULTS)
    parser.add_argument("--step-mw", type=float, default=SWEEP["step_mw"])
    parser.add_argument("--maximum-mw", type=float, default=SWEEP["maximum_mw"])
    parser.add_argument("--minimum-voltage-pu", type=float, default=CRITERIA["minimum_voltage_pu"])
    parser.add_argument("--source-voltage-pu", type=float, default=CRITERIA["base_source_voltage_pu"])
    args = parser.parse_args()
    if args.step_mw <= 0 or args.maximum_mw <= 0:
        parser.error("--step-mw and --maximum-mw must be positive")

    all_detail, all_summary, all_sensitivity = [], [], []
    for row in read_csv_rows(args.input):
        detail, summary, sensitivity = run_case(row, args.step_mw, args.maximum_mw, args.minimum_voltage_pu, args.source_voltage_pu)
        all_detail.extend(detail)
        all_summary.append(summary)
        all_sensitivity.extend(sensitivity)

    write_csv(args.results / "regional_equivalent_sweep_detail.csv", all_detail)
    write_csv(args.results / "regional_equivalent_sweep_summary.csv", all_summary)
    write_csv(args.results / "regional_equivalent_pf_voltage_sensitivity.csv", all_sensitivity)

    print(f"{'Connection point / case':42s} {'PF':>5s} {'Pass MW':>8s} {'SCR':>6s} {'Trip dV%':>9s}  Binding")
    for row in all_summary:
        label = f"{row['ConnectionPoint']} / {row['Case']}"
        print(
            f"{label:42s} {row['BasePowerFactor']:5.2f} {row['LastPassingMW']:8.0f} "
            f"{row['SCRAtTarget']:6.1f} {row['FullTripVoltageStepPercent']:9.2f}  {row['BindingConstraint']}"
        )


if __name__ == "__main__":
    main()
