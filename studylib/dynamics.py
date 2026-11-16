"""Reduced-order dynamic models for event screening.

Frequency: aggregated single-machine swing equation for the whole system,

    d(df)/dt = f0 / (2 E) * (P_pfr - dP_event - D * demand * df)

where E is system kinetic energy (MWs), P_pfr is primary frequency response
(droop K MW/Hz beyond a deadband, limited by headroom, first-order lag Tg)
and D is load damping (fraction of demand per Hz). dP_event > 0 is a sudden
load increase (or generation loss); dP_event < 0 is a load loss.

Voltage: algebraic Thevenin step at the event, then first-order recovery of
the connection-point voltage towards its pre-event value by voltage control
(OLTCs, SVC/STATCOM, generator AVRs), with time constant Tv.

Both are screening abstractions with assumed parameters. They show which
events and conditions need detailed RMS/EMT study, not whether a connection
complies.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class FrequencySystem:
    demand_mw: float
    inertia_mws: float
    pfr_mw_per_hz: float
    pfr_headroom_mw: float
    governor_time_constant_s: float
    load_damping_percent_per_hz: float

    @classmethod
    def from_config(cls, values: dict) -> "FrequencySystem":
        return cls(**{key: float(values[key]) for key in cls.__dataclass_fields__})


def simulate_frequency(
    system: FrequencySystem,
    event_mw: float,
    nominal_hz: float,
    deadband_hz: float,
    duration_s: float,
    time_step_s: float,
    event_time_s: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (time s, frequency Hz) for a step disturbance at event_time_s."""
    steps = int(round(duration_s / time_step_s)) + 1
    time = np.arange(steps) * time_step_s
    frequency = np.full(steps, nominal_hz)
    damping_mw_per_hz = system.load_damping_percent_per_hz / 100.0 * system.demand_mw
    deviation, response = 0.0, 0.0
    for i in range(1, steps):
        disturbance = event_mw if time[i - 1] >= event_time_s else 0.0
        beyond = math.copysign(max(abs(deviation) - deadband_hz, 0.0), deviation)
        target = min(max(-system.pfr_mw_per_hz * beyond, -system.pfr_headroom_mw), system.pfr_headroom_mw)
        response += (target - response) * time_step_s / system.governor_time_constant_s
        imbalance = response - disturbance - damping_mw_per_hz * deviation
        deviation += nominal_hz * imbalance / (2.0 * system.inertia_mws) * time_step_s
        frequency[i] = nominal_hz + deviation
    return time, frequency


def frequency_metrics(time: np.ndarray, frequency: np.ndarray, event_time_s: float, nominal_hz: float) -> dict[str, float]:
    after = time >= event_time_s
    window = after & (time <= event_time_s + 0.5)
    rocof = float((frequency[window][-1] - frequency[window][0]) / (time[window][-1] - time[window][0]))
    return {
        "RoCoF500msHzPerS": rocof,
        "NadirHz": float(frequency[after].min()),
        "ZenithHz": float(frequency[after].max()),
        "FinalHz": float(frequency[-1]),
        "MaxDeviationHz": float(np.abs(frequency[after] - nominal_hz).max()),
    }


def initial_rocof(system: FrequencySystem, event_mw: float, nominal_hz: float) -> float:
    """Analytic RoCoF at t=0+ (before any response): -f0 dP / (2E)."""
    return -nominal_hz * event_mw / (2.0 * system.inertia_mws)


def quasi_steady_deviation(system: FrequencySystem, event_mw: float, deadband_hz: float) -> float:
    """Settled deviation with primary response only (no secondary control)."""
    damping = system.load_damping_percent_per_hz / 100.0 * system.demand_mw
    # Solve -event = K*(|df|-db)*sign + D*df with headroom limit, by bisection on df.
    def residual(df: float) -> float:
        beyond = math.copysign(max(abs(df) - deadband_hz, 0.0), df)
        response = min(max(-system.pfr_mw_per_hz * beyond, -system.pfr_headroom_mw), system.pfr_headroom_mw)
        return response - event_mw - damping * df

    low, high = -50.0, 50.0
    for _ in range(200):
        middle = (low + high) / 2.0
        if residual(middle) > 0:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def minimum_inertia_for_rocof(event_mw: float, nominal_hz: float, rocof_limit: float) -> float:
    return nominal_hz * abs(event_mw) / (2.0 * rocof_limit)


def minimum_pfr_for_containment(system: FrequencySystem, event_mw: float, band_hz: float, deadband_hz: float) -> float:
    """Droop stiffness (MW/Hz) needed so the settled deviation stays inside band_hz."""
    damping = system.load_damping_percent_per_hz / 100.0 * system.demand_mw
    margin = band_hz - deadband_hz
    if margin <= 0:
        return float("inf")
    return max((abs(event_mw) - damping * band_hz) / margin, 0.0)


def voltage_recovery_trace(
    time: np.ndarray, event_time_s: float, pre_pu: float, post_pu: float, time_constant_s: float
) -> np.ndarray:
    trace = np.full(len(time), pre_pu)
    after = time >= event_time_s
    elapsed = time[after] - event_time_s
    trace[after] = pre_pu + (post_pu - pre_pu) * np.exp(-elapsed / time_constant_s)
    return trace


def recovery_time_s(step_percent: float, band_percent: float, time_constant_s: float) -> float:
    if math.isnan(step_percent):
        return float("nan")
    if abs(step_percent) <= band_percent:
        return 0.0
    return time_constant_s * math.log(abs(step_percent) / band_percent)
