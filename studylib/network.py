"""Network helpers: Thevenin maths, OpenDSS builders and large-load metrics.

Analytic functions here are deliberately independent of OpenDSS so they can
be used to validate it.
"""

from __future__ import annotations

import math

import opendssdirect as dss


def fault_level_mva(voltage_kv: float, fault_current_ka: float) -> float:
    return math.sqrt(3.0) * voltage_kv * fault_current_ka


def thevenin_impedance(voltage_kv: float, fault_mva: float, xr: float) -> complex:
    """Positive-sequence source impedance in ohms."""
    magnitude = voltage_kv**2 / fault_mva
    resistance = magnitude / math.sqrt(1.0 + xr**2)
    return complex(resistance, resistance * xr)


def analytic_voltage_pu(
    voltage_kv: float, fault_mva: float, xr: float, load_mw: float, power_factor: float, source_pu: float = 1.0
) -> float:
    """Load-bus voltage behind a Thevenin source with a constant-PQ load.

    Returns NaN when the load exceeds the maximum transferable power (no
    power-flow solution exists).
    """
    load = complex(load_mw, load_mw * math.tan(math.acos(power_factor)))
    return voltage_behind_impedance_pu(voltage_kv, thevenin_impedance(voltage_kv, fault_mva, xr), load, source_pu)


def voltage_behind_impedance_pu(voltage_kv: float, impedance: complex, load_mva: complex, source_pu: float = 1.0) -> float:
    """Exact bus voltage for a complex constant-power load (MW + jMVAr) behind an impedance (ohm).

    Solves V^4 + (2(RP+XQ) - E^2) V^2 + |Z|^2 |S|^2 = 0 for the high-voltage
    root; NaN if no solution exists (load beyond the PV-curve nose).
    """
    e2 = (source_pu * voltage_kv) ** 2
    b = 2.0 * (impedance.real * load_mva.real + impedance.imag * load_mva.imag) - e2
    disc = b * b - 4.0 * abs(impedance) ** 2 * abs(load_mva) ** 2
    if disc < 0 or b >= 0:
        return float("nan")
    v2 = (-b + math.sqrt(disc)) / 2.0
    return math.sqrt(v2) / voltage_kv


def maximum_transfer_mw(voltage_kv: float, fault_mva: float, xr: float, power_factor: float, source_pu: float = 1.0) -> float:
    """Nose of the PV curve: P_max = E^2 cos(phi) / (2|Z| (1 + cos(theta - phi)))."""
    impedance = thevenin_impedance(voltage_kv, fault_mva, xr)
    theta = math.atan2(impedance.imag, impedance.real)
    phi = math.acos(power_factor)
    source_kv = source_pu * voltage_kv
    return source_kv**2 * math.cos(phi) / (2.0 * abs(impedance) * (1.0 + math.cos(theta - phi)))


def voltage_step_percent(
    voltage_kv: float, fault_mva: float, xr: float, step_mw: float, power_factor: float,
    pre_load_mw: float = 0.0, source_pu: float = 1.0,
) -> float:
    """Voltage change when step_mw is suddenly lost from pre_load_mw (load trip)."""
    with_load = analytic_voltage_pu(voltage_kv, fault_mva, xr, pre_load_mw, power_factor, source_pu)
    without = analytic_voltage_pu(voltage_kv, fault_mva, xr, max(pre_load_mw - step_mw, 0.0), power_factor, source_pu)
    return 100.0 * (without - with_load) / with_load


def short_circuit_ratio(fault_mva: float, load_mw: float) -> float:
    return fault_mva / load_mw if load_mw > 0 else float("inf")


def build_thevenin_case(voltage_kv: float, fault_mva: float, xr: float, source_pu: float = 1.0) -> None:
    dss.Basic.ClearAll()
    dss.Text.Command(f"new circuit.regional_equivalent basekv={voltage_kv:g} pu={source_pu:g} phases=3")
    dss.Text.Command(
        "edit vsource.source bus1=sourcebus phases=3 "
        f"basekv={voltage_kv:g} pu={source_pu:g} mvasc3={fault_mva:.12g} "
        f"mvasc1={fault_mva:.12g} x1r1={xr:g} x0r0={xr:g}"
    )
    dss.Text.Command(f"set voltagebases=[{voltage_kv:g}]")
    dss.Text.Command("calcvoltagebases")
    dss.Text.Command(
        f"new load.ai_data_center phases=3 bus1=sourcebus.1.2.3 conn=wye "
        f"kv={voltage_kv:g} kw=0.001 kvar=0 model=1 vminpu=0.5 vmaxpu=1.5"
    )


def set_load(name: str, mw: float, power_factor: float) -> None:
    mvar = mw * math.tan(math.acos(power_factor))
    dss.Text.Command(f"edit load.{name} kw={max(mw, 1e-6) * 1000:.12g} kvar={mvar * 1000:.12g}")


def bus_minimum_pu(bus_name: str) -> float:
    available = {name.lower() for name in dss.Circuit.AllBusNames()}
    if bus_name.lower() not in available:
        raise RuntimeError(f"OpenDSS bus was not created: {bus_name}")
    dss.Circuit.SetActiveBus(bus_name)
    values = dss.Bus.puVmagAngle()
    magnitudes = [float(values[index]) for index in range(0, len(values), 2)]
    valid = [value for value in magnitudes if value > 0.05]
    if not valid:
        raise RuntimeError(f"No valid per-unit voltage returned for {bus_name}")
    return min(valid)


def solve() -> bool:
    dss.Text.Command("solve")
    return bool(dss.Solution.Converged())
