"""Reduced multi-bus transmission equivalent (template-schema inputs).

Workflow:
1. read buses / branches / contingencies (utility_model_template schema);
2. fit Thevenin source impedances (and one global line multiplier) so that
   each bus reproduces its published minimum system-normal fault level;
3. validate against the published minimum N-1 fault levels (not fitted);
4. build an OpenDSS circuit for incremental load-flow studies.

Fault levels are computed from the positive-sequence bus impedance matrix
(Zbus) with numpy, independently of OpenDSS.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import opendssdirect as dss
from scipy.optimize import least_squares

from .config import ROOT, read_csv_rows


@dataclass
class Network:
    buses: list[dict[str, str]]
    branches: list[dict[str, str]]
    contingencies: list[dict[str, str]]
    source_xr: float
    source_mva: dict[str, float] = field(default_factory=dict)
    line_multiplier: float = 1.0

    @property
    def bus_ids(self) -> list[str]:
        return [bus["bus_id"] for bus in self.buses]

    @property
    def source_ids(self) -> list[str]:
        return [bus["bus_id"] for bus in self.buses if bus["bus_type"] == "source"]

    @property
    def kv(self) -> float:
        return float(self.buses[0]["nominal_kv"])

    def bus(self, bus_id: str) -> dict[str, str]:
        return next(bus for bus in self.buses if bus["bus_id"] == bus_id)

    def branch_impedance(self, branch: dict[str, str]) -> complex:
        length = float(branch["length_km"])
        return complex(float(branch["r1_ohm_per_km"]), float(branch["x1_ohm_per_km"])) * length * self.line_multiplier

    def published_mva(self, column: str) -> dict[str, float]:
        return {bus["bus_id"]: math.sqrt(3.0) * self.kv * float(bus[column]) for bus in self.buses if bus[column]}


def load_network(model_dir: str, source_xr: float) -> Network:
    path = ROOT / model_dir
    network = Network(
        buses=read_csv_rows(path / "buses.csv"),
        branches=[b for b in read_csv_rows(path / "branches.csv") if b["in_service"].strip().lower() == "true"],
        contingencies=read_csv_rows(path / "contingencies.csv"),
        source_xr=source_xr,
    )
    ids = set(network.bus_ids)
    for branch in network.branches:
        if branch["from_bus"] not in ids or branch["to_bus"] not in ids:
            raise ValueError(f"Branch {branch['branch_id']} refers to an unknown bus")
    return network


def source_impedance(kv: float, fault_mva: float, xr: float) -> complex:
    magnitude = kv**2 / fault_mva
    resistance = magnitude / math.sqrt(1.0 + xr**2)
    return complex(resistance, resistance * xr)


def fault_levels(network: Network, outaged: set[str] | None = None) -> dict[str, float]:
    """Three-phase fault level (MVA) at every bus from Zbus."""
    outaged = outaged or set()
    index = {bus_id: i for i, bus_id in enumerate(network.bus_ids)}
    y = np.zeros((len(index), len(index)), dtype=complex)
    for branch in network.branches:
        if branch["branch_id"] in outaged:
            continue
        admittance = 1.0 / network.branch_impedance(branch)
        i, j = index[branch["from_bus"]], index[branch["to_bus"]]
        y[i, i] += admittance
        y[j, j] += admittance
        y[i, j] -= admittance
        y[j, i] -= admittance
    for bus_id, mva in network.source_mva.items():
        y[index[bus_id], index[bus_id]] += 1.0 / source_impedance(network.kv, mva, network.source_xr)
    # A bus cut off from every source (radial bus whose only line is out) has no
    # fault level; a tiny shunt keeps the matrix invertible and its level ~0.
    y += np.eye(len(index)) * 1e-9
    z = np.linalg.inv(y)
    return {bus_id: network.kv**2 / abs(z[i, i]) for bus_id, i in index.items()}


ISLANDED_MVA = 1.0  # below this a bus is treated as disconnected


def fit_sources(network: Network) -> dict[str, float]:
    """Least-squares fit of source MVA and a global line multiplier (log space)."""
    targets = network.published_mva("min_normal_fault_ka")
    sources = network.source_ids
    initial = np.log([targets[s] * 0.6 for s in sources] + [1.0])

    def residuals(params: np.ndarray) -> np.ndarray:
        network.source_mva = dict(zip(sources, np.exp(params[:-1])))
        network.line_multiplier = float(np.exp(params[-1]))
        levels = fault_levels(network)
        return np.array([math.log(levels[b] / targets[b]) for b in targets])

    lower = np.log([50.0] * len(sources) + [0.5])
    upper = np.log([50000.0] * len(sources) + [2.0])
    result = least_squares(residuals, initial, bounds=(lower, upper))
    residuals(result.x)
    return {"rms_log_error": float(np.sqrt(np.mean(result.fun**2))), "line_multiplier": network.line_multiplier}


def n_minus_1_fault_levels(network: Network) -> dict[str, tuple[float, str]]:
    """Minimum fault level at each bus over all listed single-element outages."""
    worst = {bus_id: (float("inf"), "") for bus_id in network.bus_ids}
    for contingency in network.contingencies:
        if contingency["element_type"] != "line":
            continue
        levels = fault_levels(network, {contingency["element_id"]})
        for bus_id, level in levels.items():
            if level < ISLANDED_MVA:
                continue  # radial bus disconnected by this outage: no N-1 fault level
            if level < worst[bus_id][0]:
                worst[bus_id] = (level, contingency["element_id"])
    return worst


def build_opendss(network: Network, outaged: set[str] | None = None, source_pu: float = 1.0) -> None:
    outaged = outaged or set()
    kv = network.kv
    sources = network.source_ids
    dss.Basic.ClearAll()
    first = sources[0]
    dss.Text.Command(f"new circuit.cq_equivalent basekv={kv:g} pu={source_pu:g} phases=3 bus1={first}")
    dss.Text.Command(
        f"edit vsource.source bus1={first} basekv={kv:g} pu={source_pu:g} "
        f"mvasc3={network.source_mva[first]:.10g} mvasc1={network.source_mva[first]:.10g} "
        f"x1r1={network.source_xr:g} x0r0={network.source_xr:g}"
    )
    for source in sources[1:]:
        dss.Text.Command(
            f"new vsource.{source} bus1={source} phases=3 basekv={kv:g} pu={source_pu:g} "
            f"mvasc3={network.source_mva[source]:.10g} mvasc1={network.source_mva[source]:.10g} "
            f"x1r1={network.source_xr:g} x0r0={network.source_xr:g}"
        )
    for branch in network.branches:
        z = network.branch_impedance(branch) / float(branch["length_km"])
        dss.Text.Command(
            f"new line.{branch['branch_id']} bus1={branch['from_bus']} bus2={branch['to_bus']} phases=3 "
            f"length={branch['length_km']} units=km r1={z.real:.8g} x1={z.imag:.8g} "
            f"r0={3 * z.real:.8g} x0={3 * z.imag:.8g} c1=0 c0=0"
        )
        if branch["branch_id"] in outaged:
            dss.Text.Command(f"edit line.{branch['branch_id']} enabled=no")
    dss.Text.Command(f"set voltagebases=[{kv:g}]")
    dss.Text.Command("calcvoltagebases")


def add_load(name: str, bus: str, kv: float, mw: float, pf: float) -> None:
    mvar = mw * math.tan(math.acos(pf))
    dss.Text.Command(
        f"new load.{name} phases=3 bus1={bus}.1.2.3 conn=wye kv={kv:g} kw={max(mw, 1e-6) * 1000:.10g} "
        f"kvar={mvar * 1000:.10g} model=1 vminpu=0.5 vmaxpu=1.5"
    )


def set_load(name: str, mw: float, pf: float) -> None:
    mvar = mw * math.tan(math.acos(pf))
    dss.Text.Command(f"edit load.{name} kw={max(mw, 1e-6) * 1000:.10g} kvar={mvar * 1000:.10g}")


def bus_voltages() -> dict[str, float]:
    voltages = {}
    for name in dss.Circuit.AllBusNames():
        dss.Circuit.SetActiveBus(name)
        values = dss.Bus.puVmagAngle()
        voltages[name.upper()] = min(float(values[i]) for i in range(0, len(values), 2))
    return voltages


def line_flows_mw() -> dict[str, float]:
    flows = {}
    for name in dss.Lines.AllNames():
        dss.Circuit.SetActiveElement(f"Line.{name}")
        if not dss.CktElement.Enabled():
            continue
        powers = dss.CktElement.Powers()
        flows[name.upper()] = sum(float(powers[i]) for i in range(0, 6, 2)) / 1000.0
    return flows


def opendss_fault_levels(network: Network) -> dict[str, float]:
    """Independent check: OpenDSS fault-study three-phase fault level at each bus."""
    build_opendss(network)
    dss.Text.Command("solve mode=faultstudy")
    levels = {}
    for bus_id in network.bus_ids:
        dss.Circuit.SetActiveBus(bus_id)
        isc = dss.Bus.Isc()
        currents = [math.hypot(isc[i], isc[i + 1]) for i in range(0, len(isc), 2)]
        levels[bus_id] = math.sqrt(3.0) * network.kv * max(currents) / 1000.0
    return levels
