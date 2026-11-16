"""Reduced-order dynamic event screening (voltage and frequency).

Scope follows supervisor guidance: start from minimum and maximum operating
conditions; test sudden load pickup and loss, a PV trip, a BESS trip and a
network outage; check how voltage and frequency recover.

Operating conditions
* Minimum: published minimum N-1 fault level (weakest network) and low
  system demand/inertia.
* Maximum: published maximum fault level and high system demand/inertia.

Voltage criteria: the step limit is an initial-screening assumption
(NER S5.1.5 voltage fluctuations, S5.3.7 fluctuating loads) until Powerlink
confirms the limit for the connection. Frequency criteria: NEM mainland
Frequency Operating Standard, generation/load event.

System inertia, response and demand values are assumed placeholders. The
robust outputs are the event size relative to the largest credible
contingency and the minimum inertia/response each event requires. This
screening identifies cases for RMS/EMT study; it does not replace it.
"""

from __future__ import annotations

import math
import textwrap

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from run_pilbara_ring_study import NOMINAL_KV as RING_KV, background_load, pck_impedance_ohm
from studylib import dynamics as dyn
from studylib import network as net
from studylib.config import RESULTS, SITE_CASES_PATH, load_config, ner_minimum_pf, read_csv_rows
from studylib.io import save_figure, write_csv


CONFIG = load_config()
DYN = CONFIG["dynamic_screening"]
TARGET = CONFIG["connection"]["target_mw"]
STEP_LIMIT = CONFIG["connection"]["load_trip_step_limit_percent"]
MIN_V = CONFIG["network_criteria"]["minimum_voltage_pu"]
FREQ = DYN["frequency_standard"]
EVENT_LABELS = {
    "facility_load_loss": "Sudden facility load loss",
    "full_load_pickup": "Sudden full load pickup",
    "training_swing_pickup": "Training-load swing pickup",
    "pv_trip": "On-site PV trip (import rises)",
    "bess_trip": "BESS trip while discharging",
    "network_outage": "Network outage (intact to N-1)",
}


def load_events() -> list[tuple[str, float, float]]:
    """(event, pre-event grid import MW, post-event grid import MW)."""
    events = DYN["events_mw"]
    return [
        ("facility_load_loss", TARGET, TARGET - events["facility_load_loss"]),
        ("full_load_pickup", TARGET - events["full_load_pickup"], TARGET),
        ("training_swing_pickup", TARGET - events["training_swing_pickup"], TARGET),
        ("pv_trip", TARGET - events["pv_trip"], TARGET),
        ("bess_trip", TARGET - events["bess_trip"], TARGET),
    ]


def connection_points() -> list[dict[str, object]]:
    """Thevenin impedances for each connection point and operating condition."""
    cases = read_csv_rows(SITE_CASES_PATH)
    ring = CONFIG["pilbara_ring"]
    points = []
    for point_id in DYN["connection_points"]:
        if point_id == "pilbara_ring":
            bg_mw, bg_mvar = background_load()
            points.append(
                {
                    "id": point_id, "name": "Pegs Creek 132 kV (Karratha ring)", "kv": RING_KV, "grid": "NWIS",
                    "background": complex(bg_mw, bg_mvar),
                    "z": {
                        "min": pck_impedance_ohm(min(ring["source_fault_mva_range"]), "krt_pck"),
                        "max": pck_impedance_ohm(max(ring["source_fault_mva_range"]), None),
                    },
                    "outage": (pck_impedance_ohm(min(ring["source_fault_mva_range"]), None),
                               pck_impedance_ohm(min(ring["source_fault_mva_range"]), "krt_pck")),
                    "basis": "Assumed source strength (750-5,000 MVA) and line impedance",
                }
            )
            continue
        rows = [row for row in cases if row["site_id"] == point_id]
        if not rows:
            raise KeyError(f"{point_id} not found in site_cases.csv")
        kv, xr = float(rows[0]["voltage_kv"]), float(rows[0]["source_xr"])

        def z(current_ka: float) -> complex:
            return net.thevenin_impedance(kv, net.fault_level_mva(kv, current_ka), xr)

        by_case = {row["case"]: row for row in rows}
        weakest = by_case.get("n_minus_1", rows[0])
        outage = None
        if "intact" in by_case and "n_minus_1" in by_case:
            outage = (z(float(by_case["intact"]["fault_current_3ph_ka"])), z(float(by_case["n_minus_1"]["fault_current_3ph_ka"])))
        points.append(
            {
                "id": point_id, "name": rows[0]["connection_point"], "kv": kv,
                "grid": "NEM" if kv >= 100 else "NWIS", "background": 0j,
                "z": {"min": z(float(weakest["fault_current_3ph_ka"])), "max": z(float(rows[0]["fault_current_max_3ph_ka"]))},
                "outage": outage,
                "basis": ("Published minimum N-1 and maximum fault levels" if weakest["fault_current_basis"] == "minimum"
                          else "Only maximum fault level published - minimum condition is optimistic"),
            }
        )
    return points


def facility_s(mw: float, pf: float) -> complex:
    return complex(mw, mw * math.tan(math.acos(pf)))


def voltage_screen(points) -> tuple[list[dict[str, object]], dict]:
    band = DYN["voltage"]
    rows, traces = [], {}
    time = np.arange(0.0, DYN["duration_s"] + DYN["time_step_s"] / 2, DYN["time_step_s"])
    for point in points:
        pf = ner_minimum_pf(point["kv"])
        scenarios = []
        for condition in ("min", "max"):
            for event, pre_mw, post_mw in load_events():
                scenarios.append((condition, event, point["z"][condition], point["z"][condition], pre_mw, post_mw))
        if point["outage"] is not None:
            scenarios.append(("min", "network_outage", point["outage"][0], point["outage"][1], TARGET, TARGET))
        for condition, event, z_pre, z_post, pre_mw, post_mw in scenarios:
            pre_v = net.voltage_behind_impedance_pu(point["kv"], z_pre, facility_s(pre_mw, pf) + point["background"])
            post_v = net.voltage_behind_impedance_pu(point["kv"], z_post, facility_s(post_mw, pf) + point["background"])
            solvable = not (math.isnan(pre_v) or math.isnan(post_v))
            step = 100.0 * (post_v - pre_v) / pre_v if solvable else float("nan")
            low, high = band["immediate_band_pu"]
            if not solvable:
                outcome = "No power-flow solution (load beyond PV-curve nose)"
            elif pre_v < MIN_V:
                outcome = f"Pre-event voltage already below {MIN_V:g} pu"
            elif abs(step) > STEP_LIMIT:
                outcome = f"Voltage step exceeds {STEP_LIMIT:g}% screening limit"
            elif not low <= post_v <= high:
                outcome = "Immediate voltage outside band"
            else:
                outcome = "Passes"
            rows.append(
                {
                    "ConnectionPointId": point["id"],
                    "ConnectionPoint": point["name"],
                    "Grid": point["grid"],
                    "Condition": "Minimum" if condition == "min" else "Maximum",
                    "FaultLevelMVA": point["kv"] ** 2 / abs(z_post),
                    "Event": EVENT_LABELS[event],
                    "PreEventImportMW": pre_mw,
                    "PostEventImportMW": post_mw,
                    "LoadPowerFactor": pf,
                    "PreEventVoltagePu": pre_v,
                    "PostEventVoltagePu": post_v,
                    "VoltageStepPercent": step,
                    "StepLimitPercent": STEP_LIMIT,
                    "RecoveryTimeToBandS": dyn.recovery_time_s(step, band["recovery_band_percent"], band["recovery_time_constant_s"]),
                    "Outcome": outcome,
                    "Passes": outcome == "Passes",
                    "DataBasis": point["basis"],
                }
            )
            if solvable:
                traces[(point["name"], condition, event)] = dyn.voltage_recovery_trace(
                    time, DYN["event_time_s"], pre_v, post_v, band["recovery_time_constant_s"])
    return rows, {"time": time, "traces": traces}


def frequency_screen() -> tuple[list[dict[str, object]], dict]:
    rows, traces = [], {}
    grids = sorted({system for point in connection_points() for system in DYN["grid_systems"][point["grid"]]})
    events = [(name, post - pre) for name, pre, post in load_events()]
    for grid in grids:
        system_config = DYN["systems"][grid]
        standard = DYN["island_frequency_standard"] if grid.endswith("islanded") else FREQ
        low, high = standard["containment_band_hz"]
        stab_low, stab_high = standard["stabilisation_band_hz"]
        for condition in ("min", "max"):
            system = dyn.FrequencySystem.from_config(system_config[condition])
            for event, delta_mw in events:
                time, frequency = dyn.simulate_frequency(
                    system, delta_mw, FREQ["nominal_hz"], FREQ["pfr_deadband_hz"],
                    DYN["duration_s"], DYN["time_step_s"], DYN["event_time_s"],
                )
                traces[(grid, condition, event)] = (time, frequency)
                metrics = dyn.frequency_metrics(time, frequency, DYN["event_time_s"], FREQ["nominal_hz"])
                settled = FREQ["nominal_hz"] + dyn.quasi_steady_deviation(system, delta_mw, FREQ["pfr_deadband_hz"])
                contained = low <= metrics["NadirHz"] and metrics["ZenithHz"] <= high
                rocof_ok = abs(metrics["RoCoF500msHzPerS"]) <= FREQ["rocof_limit_hz_per_s"]
                valid = metrics["MaxDeviationHz"] <= FREQ["model_validity_deviation_hz"]
                if not valid:
                    outcome = ("Outside linear-model validity: under/over-frequency protection "
                               "(load shedding, generator trips) would operate - system cannot absorb this event")
                    for key in ("NadirHz", "ZenithHz", "FinalHz"):
                        metrics[key] = float("nan")
                    settled = float("nan")
                elif not contained:
                    outcome = "Leaves containment band"
                elif not rocof_ok:
                    outcome = "RoCoF exceeds assumed limit"
                else:
                    outcome = "Passes"
                rows.append(
                    {
                        "Grid": grid,
                        "Condition": "Minimum" if condition == "min" else "Maximum",
                        "Event": EVENT_LABELS[event],
                        "FrequencyStandard": f"containment {low}-{high} Hz",
                        "GridImportChangeMW": delta_mw,
                        "EventShareOfLargestContingency": abs(delta_mw) / system_config["largest_credible_contingency_mw"],
                        "AssumedInertiaMWs": system.inertia_mws,
                        "AssumedPfrMWperHz": system.pfr_mw_per_hz,
                        **metrics,
                        "AnalyticInitialRoCoFHzPerS": dyn.initial_rocof(system, delta_mw, FREQ["nominal_hz"]),
                        "PrimaryOnlySettledHz": settled,
                        "WithinContainmentBand": contained,
                        "RoCoFWithinLimit": rocof_ok,
                        "PrimaryResponseReachesStabilisationBand": stab_low <= settled <= stab_high,
                        "MinimumInertiaForRoCoFMWs": dyn.minimum_inertia_for_rocof(delta_mw, FREQ["nominal_hz"], FREQ["rocof_limit_hz_per_s"]),
                        "MinimumPfrForContainmentMWperHz": dyn.minimum_pfr_for_containment(
                            system, delta_mw, high - FREQ["nominal_hz"], FREQ["pfr_deadband_hz"]),
                        "ModelValid": valid,
                        "Outcome": outcome,
                        "Passes": outcome == "Passes",
                        "ParameterStatus": "ASSUMED system parameters - VERIFY",
                    }
                )
    return rows, traces


def plot(voltage_rows, voltage_traces, frequency_traces) -> None:
    frame = pd.DataFrame(voltage_rows)
    points = list(dict.fromkeys(frame["ConnectionPoint"]))
    events = list(dict.fromkeys(frame["Event"]))
    fig, axes = plt.subplots(1, 2, figsize=(16, 6), gridspec_kw={"width_ratios": [1.6, 1]})
    width = 0.8 / len(events)
    colours = plt.get_cmap("tab10")
    x = np.arange(len(points))
    for index, event in enumerate(events):
        values = []
        for point in points:
            match = frame[(frame["ConnectionPoint"] == point) & (frame["Event"] == event) & (frame["Condition"] == "Minimum")]
            values.append(match["VoltageStepPercent"].iloc[0] if not match.empty else np.nan)
        values = np.array(values, dtype=float)
        positions = x + (index - (len(events) - 1) / 2) * width
        axes[0].bar(positions, np.nan_to_num(np.abs(values), nan=0.0), width, color=colours(index), label=event)
        for position, value in zip(positions, values):
            if np.isnan(value):
                axes[0].text(position, 0.3, "no\nsolution", ha="center", fontsize=6, rotation=90)
    axes[0].axhline(STEP_LIMIT, color="#B22222", linestyle="--", label=f"{STEP_LIMIT:g}% screening limit (assumption)")
    axes[0].set_xticks(x, [p.replace(" (Karratha ring)", "\n(Karratha ring)") for p in points], fontsize=8)
    axes[0].set_ylabel("|Voltage step| at event (%)")
    axes[0].set_title("Voltage step, minimum operating condition")
    axes[0].legend(fontsize=7, ncol=2)
    axes[0].grid(axis="y", alpha=0.25)

    reference = points[0]
    time = voltage_traces["time"]
    for index, event in enumerate(events):
        key = next((k for k in voltage_traces["traces"] if k[0] == reference and k[1] == "min" and EVENT_LABELS[k[2]] == event), None)
        if key:
            axes[1].plot(time, voltage_traces["traces"][key], color=colours(index), label=event)
    axes[1].set_xlabel("Time (s)")
    axes[1].set_ylabel("Connection-point voltage (pu)")
    axes[1].set_title(f"Voltage recovery, {reference} (minimum condition,\nassumed {DYN['voltage']['recovery_time_constant_s']:g} s control time constant)")
    axes[1].grid(alpha=0.25)
    axes[1].legend(fontsize=7)
    fig.suptitle("Dynamic screening: voltage (reduced-order; detailed RMS/EMT study required)")
    fig.tight_layout()
    save_figure(fig, RESULTS, "dynamic_voltage_screening")

    grids = sorted({key[0] for key in frequency_traces})
    fig2, axes2 = plt.subplots(len(grids), 2, figsize=(14, 4.2 * len(grids)), squeeze=False)
    for row, grid in enumerate(grids):
        standard = DYN["island_frequency_standard"] if grid.endswith("islanded") else FREQ
        low, high = standard["containment_band_hz"]
        stab_low, stab_high = standard["stabilisation_band_hz"]
        for column, condition in enumerate(("min", "max")):
            ax = axes2[row][column]
            for index, (event, label) in enumerate((k, v) for k, v in EVENT_LABELS.items() if k != "network_outage"):
                time, frequency = frequency_traces[(grid, condition, event)]
                ax.plot(time, frequency, color=colours(index), label=label)
            ax.axhspan(low, high, color="#2E8B57", alpha=0.06)
            for value in (low, high):
                ax.axhline(value, color="#B22222", linestyle="--", linewidth=1)
            for value in (stab_low, stab_high):
                ax.axhline(value, color="#888888", linestyle=":", linewidth=1)
            span = max(abs(frequency_traces[(grid, condition, e)][1] - FREQ["nominal_hz"]).max()
                       for e in EVENT_LABELS if e != "network_outage")
            limit = max(0.7, min(span * 1.1, 6.0))
            ax.set_ylim(FREQ["nominal_hz"] - limit, FREQ["nominal_hz"] + limit)
            validity = FREQ["model_validity_deviation_hz"]
            if span > validity:
                ax.text(0.5, 0.92, textwrap.fill(f"Traces leaving +/-{validity:g} Hz are beyond linear-model validity - "
                        "load shedding / generator protection would operate", 60), transform=ax.transAxes,
                        ha="center", va="top", fontsize=8, color="#B22222",
                        bbox={"facecolor": "white", "alpha": 0.8, "edgecolor": "#B22222"})
            ax.set_title(f"{grid}, {'minimum' if condition == 'min' else 'maximum'} condition (assumed parameters)")
            ax.set_xlabel("Time (s)")
            ax.set_ylabel("Frequency (Hz)")
            ax.grid(alpha=0.25)
    axes2[0][0].legend(fontsize=7)
    fig2.suptitle("Dynamic screening: frequency (red dashed = containment band, grey dotted = stabilisation band; island bands for QLD islanded)")
    fig2.tight_layout()
    save_figure(fig2, RESULTS, "dynamic_frequency_screening")


def main() -> None:
    points = connection_points()
    voltage_rows, voltage_traces = voltage_screen(points)
    frequency_rows, frequency_traces = frequency_screen()
    write_csv(RESULTS / "dynamic_voltage_events.csv", voltage_rows)
    write_csv(RESULTS / "dynamic_frequency_events.csv", frequency_rows)
    plot(voltage_rows, voltage_traces, frequency_traces)

    voltage = pd.DataFrame(voltage_rows)
    frequency = pd.DataFrame(frequency_rows)
    print(voltage[voltage["Condition"] == "Minimum"][["ConnectionPoint", "Event", "VoltageStepPercent", "Outcome"]].round(2).to_string(index=False))
    print(frequency[["Grid", "Condition", "Event", "EventShareOfLargestContingency", "NadirHz", "ZenithHz", "RoCoF500msHzPerS", "Outcome"]].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
