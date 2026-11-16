"""Staged facility growth under declining system strength.

For each transmission connection point the published minimum N-1 fault level
is scaled down (a proxy for future coal retirement and lower synchronous
generation) and each facility stage is screened against:

* steady-state voltage (>= minimum_voltage_pu at the base PF and source voltage);
* short-circuit ratio (>= minimum_scr); and
* full-facility trip voltage step (<= the screening step limit).

It also solves for the minimum fault level each stage needs, which shows
how much system-strength headroom each site has. Fault-level scale factors
are scenarios, not forecasts; Powerlink's committed projects (for example
the Gladstone Project, ~2030) would change these values and require
utility data.
"""

from __future__ import annotations

import math

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from studylib import network as net
from studylib.config import RESULTS, SITE_CASES_PATH, load_config, ner_minimum_pf, read_csv_rows
from studylib.io import save_figure, write_csv


CONFIG = load_config()
GROWTH = CONFIG["growth_scenarios"]
CRITERIA = CONFIG["network_criteria"]
STEP_LIMIT = CONFIG["connection"]["load_trip_step_limit_percent"]


def screen(kv: float, fault_mva: float, xr: float, load_mw: float, pf: float, source_pu: float) -> tuple[bool, str, dict]:
    voltage = net.analytic_voltage_pu(kv, fault_mva, xr, load_mw, pf, source_pu)
    scr = net.short_circuit_ratio(fault_mva, load_mw)
    step = net.voltage_step_percent(kv, fault_mva, xr, load_mw, pf, load_mw, source_pu) if not math.isnan(voltage) else float("nan")
    metrics = {"VoltagePu": voltage, "SCR": scr, "TripStepPercent": step}
    if math.isnan(voltage):
        return False, "No power-flow solution", metrics
    failures = []
    if voltage < CRITERIA["minimum_voltage_pu"]:
        failures.append("voltage")
    if scr < GROWTH["minimum_scr"]:
        failures.append("SCR")
    if step > STEP_LIMIT:
        failures.append("trip step")
    return not failures, ("Passes" if not failures else "Fails: " + ", ".join(failures)), metrics


def required_fault_mva(kv, xr, load_mw, pf, source_pu) -> float:
    low, high = 1.0, 1e6
    for _ in range(100):
        middle = math.sqrt(low * high)
        if screen(kv, middle, xr, load_mw, pf, source_pu)[0]:
            high = middle
        else:
            low = middle
    return high


def main() -> None:
    source_pu = CRITERIA["base_source_voltage_pu"]
    rows, requirements = [], []
    for case in read_csv_rows(SITE_CASES_PATH):
        kv = float(case["voltage_kv"])
        if case["case"] != "n_minus_1" or kv < 100:
            continue
        xr = float(case["source_xr"])
        pf = ner_minimum_pf(kv)
        published = net.fault_level_mva(kv, float(case["fault_current_3ph_ka"]))
        for stage in GROWTH["stages_mw"]:
            for scale in GROWTH["fault_level_scale"]:
                ok, outcome, metrics = screen(kv, published * scale, xr, stage, pf, source_pu)
                rows.append({"ConnectionPoint": case["connection_point"], "StageMW": stage, "FaultLevelScale": scale,
                             "FaultLevelMVA": published * scale, **metrics, "Passes": ok, "Outcome": outcome})
            needed = required_fault_mva(kv, xr, stage, pf, source_pu)
            requirements.append({
                "ConnectionPoint": case["connection_point"], "StageMW": stage,
                "PublishedMinN1FaultMVA": published, "RequiredFaultMVA": needed,
                "RequiredFaultKA": needed / (math.sqrt(3.0) * kv),
                "RequiredAsShareOfPublished": needed / published,
                "SystemStrengthHeadroomPercent": 100.0 * (1.0 - needed / published),
            })
    write_csv(RESULTS / "growth_system_strength_screen.csv", rows)
    write_csv(RESULTS / "growth_required_fault_level.csv", requirements)

    frame = pd.DataFrame(rows)
    need = pd.DataFrame(requirements)
    points = list(dict.fromkeys(frame["ConnectionPoint"]))
    fig, axes = plt.subplots(1, len(points) + 1, figsize=(4.4 * (len(points) + 1), 4.6))
    for ax, point in zip(axes, points):
        subset = frame[frame["ConnectionPoint"] == point]
        grid = subset.pivot(index="StageMW", columns="FaultLevelScale", values="Passes").sort_index(ascending=False)
        grid = grid[sorted(grid.columns, reverse=True)]
        ax.imshow(grid.to_numpy().astype(float), cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
        labels = subset.set_index(["StageMW", "FaultLevelScale"])["Outcome"]
        for i, stage in enumerate(grid.index):
            for j, scale in enumerate(grid.columns):
                text = (labels[(stage, scale)].replace("Fails: ", "").replace("Passes", "ok")
                        .replace("No power-flow solution", "no soln").replace("voltage", "V")
                        .replace("trip step", "step").replace(", ", "+"))
                ax.text(j, i, text, ha="center", va="center", fontsize=7)
        ax.set_xticks(range(len(grid.columns)), [f"{c:.0%}" for c in grid.columns], fontsize=8)
        ax.set_yticks(range(len(grid.index)), [f"{s:.0f} MW" for s in grid.index], fontsize=8)
        ax.set_xlabel("Min N-1 fault level vs today")
        ax.set_title(point, fontsize=9)
    last = axes[-1]
    for point in points:
        subset = need[need["ConnectionPoint"] == point]
        last.plot(subset["StageMW"], subset["RequiredAsShareOfPublished"] * 100, marker="o", label=point)
    last.axhline(100, color="#B22222", linestyle="--", label="Today's published min N-1")
    last.set_xlabel("Facility stage (MW)")
    last.set_ylabel("Required fault level (% of today's)")
    last.set_title("System strength needed per stage")
    last.legend(fontsize=7)
    last.grid(alpha=0.25)
    fig.suptitle(f"Staged growth vs declining system strength (V = voltage < {CRITERIA['minimum_voltage_pu']} pu, "
                 f"SCR = SCR < {GROWTH['minimum_scr']:g}, step = trip step > {STEP_LIMIT:g}%, no soln = beyond PV nose)")
    fig.tight_layout()
    save_figure(fig, RESULTS, "growth_system_strength")
    print(need.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
