"""Reduced 132 kV Karratha ring hosting-capacity study (OpenDSS).

Published line lengths, ratings and N-1 utilisation are used directly. Line
impedances, line capacitance and source fault level are explicit
assumptions because the public system description does not provide them.

Important context: the Pilbara (NWIS) is an isolated grid. A 230-250 MW
load would be a large share of total system demand, so generation adequacy
and frequency stability - which this static model cannot represent - are
likely to be at least as limiting as the line and voltage checks here.
"""

from __future__ import annotations

import argparse
import math
from functools import lru_cache

import opendssdirect as dss

from studylib import network as net
from studylib.config import INPUTS, RESULTS, load_config, ner_minimum_pf, read_csv_rows
from studylib.io import write_csv


CONFIG = load_config()
RING = CONFIG["pilbara_ring"]
CRITERIA = CONFIG["network_criteria"]
CONNECTION = CONFIG["connection"]
NOMINAL_KV = float(RING["nominal_kv"])
LINES_PATH = INPUTS / "pilbara_ring_lines.csv"
SCENARIOS = (
    ("intact", None),
    ("n_minus_1_krt_pck", "krt_pck"),
    ("n_minus_1_krt_bul", "krt_bul"),
    ("n_minus_1_bul_pck", "bul_pck"),
)


@lru_cache(maxsize=1)
def load_lines() -> list[dict[str, str]]:
    return read_csv_rows(LINES_PATH)


def background_load() -> tuple[float, float]:
    """Background demand at PCK inferred from the published 45% N-1 utilisation.

    It has its own fixed power factor so that data-centre PF sensitivities do
    not also change the background reactive demand.
    """
    lines = load_lines()
    single_circuit_mva = min(float(line["rating_mva"]) for line in lines)
    mva = single_circuit_mva * RING["published_n_minus_1_utilisation"]
    pf = RING["background_power_factor"]
    return mva * pf, mva * pf * math.tan(math.acos(pf))


def build_network(source_fault_mva: float, outaged_line: str | None, impedance_multiplier: float = 1.0, source_pu: float = 1.0):
    lines = load_lines()
    dss.Basic.ClearAll()
    dss.Text.Command(f"new circuit.karratha_ring basekv={NOMINAL_KV:g} pu={source_pu:g} phases=3")
    dss.Text.Command(
        "edit vsource.source bus1=krt phases=3 "
        f"basekv={NOMINAL_KV:g} pu={source_pu:g} mvasc3={source_fault_mva:g} "
        f"mvasc1={source_fault_mva:g} x1r1=10 x0r0=10"
    )
    for line in lines:
        rating_a = float(line["rating_mva"]) * 1e6 / (math.sqrt(3.0) * NOMINAL_KV * 1000.0)
        r1 = float(line["r1_ohm_per_km"]) * impedance_multiplier
        x1 = float(line["x1_ohm_per_km"]) * impedance_multiplier
        c1 = float(line["c1_nf_per_km"])
        dss.Text.Command(
            f"new line.{line['line_id']} bus1={line['from_bus']} bus2={line['to_bus']} phases=3 "
            f"length={line['length_km']} units=km r1={r1:g} x1={x1:g} r0={3 * r1:g} x0={3 * x1:g} "
            f"c1={c1:g} c0={0.6 * c1:g} normamps={rating_a:.8g} emergamps={rating_a:.8g}"
        )
        if outaged_line == line["line_id"]:
            dss.Text.Command(f"edit line.{line['line_id']} enabled=no")
    background_mw, background_mvar = background_load()
    dss.Text.Command(
        f"new load.background phases=3 bus1=pck.1.2.3 conn=wye kv={NOMINAL_KV:g} "
        f"kw={background_mw * 1000:.12g} kvar={background_mvar * 1000:.12g} model=1 vminpu=0.5 vmaxpu=1.5"
    )
    dss.Text.Command(
        f"new load.ai_data_center phases=3 bus1=pck.1.2.3 conn=wye kv={NOMINAL_KV:g} "
        "kw=0.001 kvar=0 model=1 vminpu=0.5 vmaxpu=1.5"
    )
    dss.Text.Command(f"set voltagebases=[{NOMINAL_KV:g}]")
    dss.Text.Command("calcvoltagebases")
    return lines, background_mw


def line_loading_percent(line_id: str) -> float:
    dss.Circuit.SetActiveElement(f"Line.{line_id}")
    if not dss.CktElement.Enabled():
        return 0.0
    currents = dss.CktElement.CurrentsMagAng()
    phase_currents = [float(currents[index]) for index in range(0, len(currents), 2)]
    return 100.0 * max(phase_currents, default=0.0) / float(dss.Lines.NormAmps())


def pck_impedance_ohm(source_fault_mva: float, outaged_line: str | None, impedance_multiplier: float = 1.0) -> complex:
    """Analytic positive-sequence Thevenin impedance seen at PCK (source + ring)."""
    lines = {line["line_id"]: line for line in load_lines()}

    def z(line_id: str) -> complex | None:
        if line_id == outaged_line:
            return None
        line = lines[line_id]
        length = float(line["length_km"])
        return complex(float(line["r1_ohm_per_km"]), float(line["x1_ohm_per_km"])) * length * impedance_multiplier

    direct = z("krt_pck")
    via_bul = None if z("krt_bul") is None or z("bul_pck") is None else z("krt_bul") + z("bul_pck")
    paths = [path for path in (direct, via_bul) if path is not None]
    ring = 1.0 / sum(1.0 / path for path in paths)
    return net.thevenin_impedance(NOMINAL_KV, source_fault_mva, 10.0) + ring


def pck_fault_level_mva(source_fault_mva: float, outaged_line: str | None, impedance_multiplier: float = 1.0) -> float:
    """Analytic three-phase fault level at PCK (source + ring impedance)."""
    return NOMINAL_KV**2 / abs(pck_impedance_ohm(source_fault_mva, outaged_line, impedance_multiplier))


def evaluate(lines, outaged_line, load_mw, power_factor, minimum_voltage_pu) -> dict[str, object]:
    net.set_load("ai_data_center", load_mw, power_factor)
    converged = net.solve()
    voltage = net.bus_minimum_pu("pck") if converged else float("nan")
    loadings = {line["line_id"]: line_loading_percent(line["line_id"]) for line in lines if line["line_id"] != outaged_line}
    limiting = max(loadings, key=loadings.get)
    voltage_pass = converged and voltage >= minimum_voltage_pu
    thermal_pass = converged and loadings[limiting] <= 100.0 + 1e-6
    return {
        "converged": converged,
        "voltage": voltage,
        "loading": loadings[limiting],
        "limiting_line": limiting,
        "voltage_pass": voltage_pass,
        "thermal_pass": thermal_pass,
        "passes": converged and voltage_pass and thermal_pass,
    }


def reason(result: dict[str, object], minimum_voltage_pu: float) -> str:
    if not result["converged"]:
        return "Power flow convergence"
    if not result["thermal_pass"]:
        return f"Thermal rating of {result['limiting_line']}"
    return f"PCK voltage below {minimum_voltage_pu:g} pu"


def run_scenario(
    scenario: str,
    outaged_line: str | None,
    source_fault_mva: float,
    step_mw: float,
    maximum_mw: float,
    power_factor: float,
    minimum_voltage_pu: float,
    line_impedance_multiplier: float = 1.0,
    source_pu: float = 1.0,
    keep_detail: bool = True,
):
    lines, background_mw = build_network(source_fault_mva, outaged_line, line_impedance_multiplier, source_pu)
    base = evaluate(lines, outaged_line, 0.0, power_factor, minimum_voltage_pu)
    detail: list[dict[str, object]] = []
    last_pass, first_fail, binding, end = 0.0, "", "Sweep maximum reached", base
    if not base["passes"]:
        binding = f"Base case already fails ({reason(base, minimum_voltage_pu)})"
    elif not keep_detail:
        # Only the limit is needed: binary search over whole steps. Voltage and
        # loading are monotonic in added load below the PV-curve nose.
        steps = int(math.floor(maximum_mw / step_mw + 1e-9))
        end = evaluate(lines, outaged_line, steps * step_mw, power_factor, minimum_voltage_pu)
        if end["passes"]:
            last_pass = steps * step_mw
        else:
            low, high, failing = 0, steps, end
            while high - low > 1:
                middle = (low + high) // 2
                result = evaluate(lines, outaged_line, middle * step_mw, power_factor, minimum_voltage_pu)
                if result["passes"]:
                    low = middle
                else:
                    high, failing = middle, result
            last_pass, first_fail, end = low * step_mw, high * step_mw, failing
            binding = reason(failing, minimum_voltage_pu)
    else:
        for index in range(1, int(math.floor(maximum_mw / step_mw + 1e-9)) + 1):
            load_mw = index * step_mw
            end = evaluate(lines, outaged_line, load_mw, power_factor, minimum_voltage_pu)
            if keep_detail:
                detail.append(
                    {
                        "Scenario": scenario,
                        "OutagedLine": outaged_line or "none",
                        "AssumedSourceFaultMVA": source_fault_mva,
                        "SourceVoltagePu": source_pu,
                        "LineImpedanceMultiplier": line_impedance_multiplier,
                        "PowerFactor": power_factor,
                        "BackgroundMW": background_mw,
                        "AddedDataCentreMW": load_mw,
                        "PckVoltagePu": end["voltage"],
                        "MaximumLineLoadingPercent": end["loading"],
                        "LimitingLine": end["limiting_line"],
                        "VoltagePass": end["voltage_pass"],
                        "ThermalPass": end["thermal_pass"],
                        "OverallPass": end["passes"],
                    }
                )
            if not end["passes"]:
                first_fail = load_mw
                binding = reason(end, minimum_voltage_pu)
                break
            last_pass = load_mw

    # Voltage step if the hosted load trips (OpenDSS, at the hosting limit).
    trip_step = float("nan")
    if last_pass > 0:
        with_load = evaluate(lines, outaged_line, last_pass, power_factor, minimum_voltage_pu)["voltage"]
        without = evaluate(lines, outaged_line, 0.0, power_factor, minimum_voltage_pu)["voltage"]
        trip_step = 100.0 * (without - with_load) / with_load
    fault_mva = pck_fault_level_mva(source_fault_mva, outaged_line, line_impedance_multiplier)
    summary = {
        "Scenario": scenario,
        "OutagedLine": outaged_line or "none",
        "AssumedSourceFaultMVA": source_fault_mva,
        "SourceVoltagePu": source_pu,
        "LineImpedanceMultiplier": line_impedance_multiplier,
        "PowerFactor": power_factor,
        "PublishedNMinus1UtilisationPercent": 100 * RING["published_n_minus_1_utilisation"],
        "InferredBackgroundMW": background_mw,
        "BaseCasePasses": base["passes"],
        "LastPassingAddedMW": last_pass,
        "FirstFailingAddedMW": first_fail,
        "EndPckVoltagePu": end["voltage"],
        "EndMaximumLineLoadingPercent": end["loading"],
        "BindingConstraint": binding,
        "PckFaultLevelMVA": fault_mva,
        "SCRAtTarget": net.short_circuit_ratio(fault_mva, CONNECTION["target_mw"]),
        "TripVoltageStepAtLimitPercent": trip_step,
        "ResultClassification": "Reduced-network public-rating estimate with assumed impedances and source strength",
    }
    return detail, summary


def firm_result(summaries: list[dict[str, object]]) -> dict[str, object]:
    contingencies = [row for row in summaries if row["Scenario"].startswith("n_minus_1")]
    return min(contingencies, key=lambda row: float(row["LastPassingAddedMW"]))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-fault-mva", type=float, default=RING["base_source_fault_mva"])
    parser.add_argument("--step-mw", type=float, default=RING["step_mw"])
    parser.add_argument("--maximum-mw", type=float, default=RING["maximum_mw"])
    parser.add_argument("--power-factor", type=float, default=ner_minimum_pf(NOMINAL_KV))
    parser.add_argument("--minimum-voltage-pu", type=float, default=CRITERIA["minimum_voltage_pu"])
    parser.add_argument("--source-voltage-pu", type=float, default=CRITERIA["base_source_voltage_pu"])
    parser.add_argument("--line-impedance-multiplier", type=float, default=1.0)
    args = parser.parse_args()

    detail, summary = [], []
    for name, outage in SCENARIOS:
        case_detail, case_summary = run_scenario(
            name, outage, args.source_fault_mva, args.step_mw, args.maximum_mw, args.power_factor,
            args.minimum_voltage_pu, args.line_impedance_multiplier, args.source_voltage_pu,
        )
        detail.extend(case_detail)
        summary.append(case_summary)

    write_csv(RESULTS / "pilbara_ring_sweep_detail.csv", detail)
    write_csv(RESULTS / "pilbara_ring_sweep_summary.csv", summary)
    firm = firm_result(summary)
    print(f"{'Scenario':22s} {'Last pass':>9s} {'SCR':>5s}  Binding")
    for row in summary:
        print(f"{row['Scenario']:22s} {row['LastPassingAddedMW']:9.0f} {row['SCRAtTarget']:5.1f}  {row['BindingConstraint']}")
    print(f"Firm N-1 = {firm['LastPassingAddedMW']:.0f} MW ({firm['Scenario']}) at PF {args.power_factor}")


if __name__ == "__main__":
    main()
