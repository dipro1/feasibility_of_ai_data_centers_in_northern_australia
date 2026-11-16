"""Independent implementation checks for the network models.

1. OpenDSS Thevenin voltages vs a fixed-point phasor solution, for every
   connection point in site_cases.csv.
2. Analytic PV-curve nose vs an independent bisection on the discriminant of
   the bus-voltage quadratic  V^4 + (2(RP+XQ) - E^2) V^2 + |Z|^2 |S|^2 = 0.
3. Karratha ring current split (intact) vs the impedance current divider.
4. Multi-bus CQ equivalent: Zbus fault levels vs the OpenDSS fault study.

These verify that the code implements the stated model; they cannot verify
the model against the real network.
"""

from __future__ import annotations

import math

import opendssdirect as dss
import pandas as pd

from run_pilbara_ring_study import NOMINAL_KV, build_network, load_lines
from studylib import network as net
from studylib.config import RESULTS, SITE_CASES_PATH, load_config, ner_minimum_pf, read_csv_rows


TOLERANCE_PU = 1e-4
NOSE_TOLERANCE = 1e-6
SPLIT_TOLERANCE = 0.02


def discriminant_nose_mw(voltage_kv: float, fault_mva: float, xr: float, pf: float, source_pu: float) -> float:
    z = net.thevenin_impedance(voltage_kv, fault_mva, xr)
    e2 = (source_pu * voltage_kv) ** 2
    tan_phi = math.tan(math.acos(pf))

    def solvable(p: float) -> bool:
        q = p * tan_phi
        b = 2.0 * (z.real * p + z.imag * q) - e2
        return b * b - 4.0 * abs(z) ** 2 * (p * p + q * q) >= 0.0 and b < 0

    low, high = 0.0, 1.0
    while solvable(high):
        high *= 2.0
    for _ in range(200):
        middle = (low + high) / 2.0
        low, high = (middle, high) if solvable(middle) else (low, middle)
    return low


def thevenin_checks(source_pu: float) -> list[dict[str, object]]:
    rows = []
    for case in read_csv_rows(SITE_CASES_PATH):
        kv, xr = float(case["voltage_kv"]), float(case["source_xr"])
        fault = net.fault_level_mva(kv, float(case["fault_current_3ph_ka"]))
        pf = ner_minimum_pf(kv)
        nose = net.maximum_transfer_mw(kv, fault, xr, pf, source_pu)
        independent_nose = discriminant_nose_mw(kv, fault, xr, pf, source_pu)
        net.build_thevenin_case(kv, fault, xr, source_pu)
        for fraction in (0.1, 0.25, 0.5, 0.75):
            load_mw = fraction * nose
            net.set_load("ai_data_center", load_mw, pf)
            if not net.solve():
                raise RuntimeError(f"OpenDSS failed to converge for {case['site_id']} at {load_mw:.1f} MW")
            open_dss = net.bus_minimum_pu("sourcebus")
            analytic = net.analytic_voltage_pu(kv, fault, xr, load_mw, pf, source_pu)
            rows.append(
                {
                    "Check": "Thevenin voltage",
                    "Case": f"{case['connection_point']} {case['case']}",
                    "LoadMW": load_mw,
                    "OpenDSSValue": open_dss,
                    "IndependentValue": analytic,
                    "AbsoluteDifference": abs(open_dss - analytic),
                    "Tolerance": TOLERANCE_PU,
                    "Unit": "pu",
                }
            )
        rows.append(
            {
                "Check": "PV-curve nose",
                "Case": f"{case['connection_point']} {case['case']}",
                "LoadMW": nose,
                "OpenDSSValue": nose,
                "IndependentValue": independent_nose,
                "AbsoluteDifference": abs(nose - independent_nose) / nose,
                "Tolerance": NOSE_TOLERANCE,
                "Unit": "relative (closed form vs discriminant bisection)",
            }
        )
    return rows


def ring_split_check() -> dict[str, object]:
    config = load_config()["pilbara_ring"]
    lines = {line["line_id"]: line for line in load_lines()}

    def z(line_id: str) -> complex:
        line = lines[line_id]
        return complex(float(line["r1_ohm_per_km"]), float(line["x1_ohm_per_km"])) * float(line["length_km"])

    expected = abs((z("krt_bul") + z("bul_pck")) / z("krt_pck"))
    build_network(config["base_source_fault_mva"], None)
    net.set_load("ai_data_center", 50.0, ner_minimum_pf(NOMINAL_KV))
    net.solve()

    def current(line_id: str) -> float:
        dss.Circuit.SetActiveElement(f"Line.{line_id}")
        return float(dss.CktElement.CurrentsMagAng()[0])

    simulated = current("krt_pck") / current("krt_bul")
    return {
        "Check": "Ring current split (intact)",
        "Case": "I(krt_pck) / I(krt_bul) vs current divider",
        "LoadMW": 50.0,
        "OpenDSSValue": simulated,
        "IndependentValue": expected,
        "AbsoluteDifference": abs(simulated - expected) / expected,
        "Tolerance": SPLIT_TOLERANCE,
        "Unit": "relative (line charging causes small deviation)",
    }


def multibus_fault_check() -> list[dict[str, object]]:
    """Zbus (numpy) fault levels of the fitted CQ equivalent vs OpenDSS fault study."""
    from studylib import multibus as mb

    config = load_config()["multibus"]
    network = mb.load_network(config["model_dir"], config["source_xr"])
    mb.fit_sources(network)
    numpy_levels = mb.fault_levels(network)
    dss_levels = mb.opendss_fault_levels(network)
    return [
        {
            "Check": "Multi-bus fault level",
            "Case": f"CQ equivalent {bus}",
            "LoadMW": 0.0,
            "OpenDSSValue": dss_levels[bus],
            "IndependentValue": level,
            "AbsoluteDifference": abs(dss_levels[bus] - level) / level,
            "Tolerance": 1e-3,
            "Unit": "relative (OpenDSS fault study vs Zbus)",
        }
        for bus, level in numpy_levels.items()
    ]


def main() -> None:
    source_pu = load_config()["network_criteria"]["base_source_voltage_pu"]
    rows = thevenin_checks(source_pu) + [ring_split_check()] + multibus_fault_check()
    frame = pd.DataFrame(rows)
    frame["Passes"] = frame["AbsoluteDifference"] <= frame["Tolerance"]
    frame.to_csv(RESULTS / "model_validation.csv", index=False)
    print(frame.groupby("Check")[["AbsoluteDifference"]].max().to_string())
    if not frame["Passes"].all():
        failed = frame[~frame["Passes"]]
        raise RuntimeError(f"Validation failed:\n{failed.to_string(index=False)}")
    print(f"All {len(frame)} validation checks passed.")


if __name__ == "__main__":
    main()
