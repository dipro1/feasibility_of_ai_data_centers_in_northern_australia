"""AI training-load oscillation screen: flicker severity and mode-band risk.

Large training jobs swing power repeatedly between compute and
communication/checkpoint phases. Each swing changes the connection-point
voltage. This screen treats a periodic swing as a stream of rectangular
voltage changes (two per cycle) and estimates short-term flicker severity

    Pst = d / d_lim(r)

where d is the relative voltage change (%) and d_lim(r) the Pst = 1 curve
for rectangular changes at r changes per minute (IEC 61000-3-3 /
AS/NZS 61000.3.3, digitised approximation - VERIFY). For continuous
oscillation Plt = Pst. Results are compared with the indicative HV-EHV
planning levels of IEC 61000-3-7 / AS/NZS 61000.3.7 (NER S5.1.5 refers to
these standards). Allocation of the planning level among customers is not
modelled, so passing here is necessary but not sufficient.

Swing sizes and periods follow measured AI-training behaviour reported by
Choukse et al. (2025): unmitigated compute/communication swings of order 50%
of IT load at periods from below 1 s to tens of seconds, reducible to ~10-20%
with GPU power smoothing. Swings with a frequency inside the band where
line-resonance and inter-plant oscillation modes sit (~0.16-2.5 Hz) are
flagged: they could excite system oscillations and need small-signal / EMT
study regardless of flicker.
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
OSC = CONFIG["oscillation_screening"]
TARGET = CONFIG["connection"]["target_mw"]
FACILITY = CONFIG["facility"]


def pst1_limit_percent(changes_per_minute: float) -> float:
    """Voltage change (%) giving Pst = 1, log-log interpolated on the configured curve."""
    r = np.log10(OSC["pst1_curve_changes_per_min"])
    d = np.log10(OSC["pst1_curve_delta_v_percent"])
    return float(10 ** np.interp(math.log10(changes_per_minute), r, d))


def main() -> None:
    source_pu = CONFIG["network_criteria"]["base_source_voltage_pu"]
    band_low, band_high = OSC["interarea_mode_band_hz"]
    rows = []
    for case in read_csv_rows(SITE_CASES_PATH):
        kv = float(case["voltage_kv"])
        if case["case"] != "n_minus_1" or kv < 100:
            continue
        fault = net.fault_level_mva(kv, float(case["fault_current_3ph_ka"]))
        xr = float(case["source_xr"])
        pf = ner_minimum_pf(kv)
        for fraction in OSC["swing_fraction_of_it"]:
            swing_mw = fraction * FACILITY["it_capacity_mw"] * FACILITY["base_pue"]
            step = abs(net.voltage_step_percent(kv, fault, xr, swing_mw, pf, TARGET, source_pu))
            for period in OSC["period_s"]:
                rate = 2.0 * 60.0 / period
                pst = step / pst1_limit_percent(rate) if not math.isnan(step) else float("nan")
                frequency_hz = 1.0 / period
                in_band = band_low <= frequency_hz <= band_high
                passes_flicker = pst <= OSC["pst_planning_level"] and pst <= OSC["plt_planning_level"]
                rows.append(
                    {
                        "ConnectionPoint": case["connection_point"],
                        "FaultLevelMVA": fault,
                        "SwingFractionOfIT": fraction,
                        "SwingScenario": OSC["swing_labels"].get(f"{fraction:g}", ""),
                        "SwingMW": swing_mw,
                        "PeriodS": period,
                        "OscillationHz": frequency_hz,
                        "ChangesPerMinute": rate,
                        "VoltageChangePercent": step,
                        "Pst1LimitPercent": pst1_limit_percent(rate),
                        "EstimatedPst": pst,
                        "EstimatedPlt": pst,
                        "PassesPlanningLevels": passes_flicker,
                        "InInterAreaModeBand": in_band,
                        "Outcome": ("Exceeds flicker planning level" if not passes_flicker
                                    else "Flicker OK - mode-band risk, small-signal study needed" if in_band
                                    else "Passes screen"),
                    }
                )
    write_csv(RESULTS / "oscillation_flicker_screen.csv", rows)
    frame = pd.DataFrame(rows)

    points = list(dict.fromkeys(frame["ConnectionPoint"]))
    fig, axes = plt.subplots(1, len(points), figsize=(5.2 * len(points), 4.8), sharey=True)
    for ax, point in zip(np.atleast_1d(axes), points):
        subset = frame[frame["ConnectionPoint"] == point]
        grid = subset.pivot(index="SwingFractionOfIT", columns="PeriodS", values="EstimatedPst").sort_index(ascending=False)
        image = ax.imshow(grid.to_numpy(), cmap="RdYlGn_r", vmin=0, vmax=2 * OSC["plt_planning_level"], aspect="auto")
        for (i, j), value in np.ndenumerate(grid.to_numpy()):
            ax.text(j, i, f"{value:.2f}", ha="center", va="center", fontsize=7)
        periods = list(grid.columns)
        band = [j for j, period in enumerate(periods) if band_low <= 1.0 / period <= band_high]
        if band:
            ax.axvspan(min(band) - 0.5, max(band) + 0.5, facecolor="none", edgecolor="#7A5195", linewidth=2, hatch="//", alpha=0.5)
        ax.set_xticks(range(len(periods)), [f"{p:g}" for p in periods], fontsize=7)
        ax.set_yticks(range(len(grid.index)), [f"{f:.0%}" for f in grid.index], fontsize=8)
        ax.set_xlabel("Swing period (s)")
        ax.set_title(f"{point} (min N-1)", fontsize=9)
    np.atleast_1d(axes)[0].set_ylabel("Swing amplitude (% of IT capacity)")
    fig.colorbar(image, ax=axes, shrink=0.8, label=f"Estimated Pst = Plt (planning level {OSC['plt_planning_level']})")
    fig.suptitle("Training-load oscillation (swing sizes and periods per Choukse et al. 2025): estimated flicker severity; hatched = 0.16-2.5 Hz oscillation-mode band", fontsize=10)
    save_figure(fig, RESULTS, "oscillation_flicker_screen")

    worst = frame.loc[frame.groupby("ConnectionPoint")["EstimatedPst"].idxmax()]
    print(worst[["ConnectionPoint", "SwingMW", "PeriodS", "VoltageChangePercent", "EstimatedPst", "Outcome"]].round(3).to_string(index=False))
    limits = frame[frame["PassesPlanningLevels"]].groupby("ConnectionPoint")["SwingFractionOfIT"].max()
    print("Largest swing passing flicker at every period:")
    for point in points:
        subset = frame[frame["ConnectionPoint"] == point]
        ok = subset.groupby("SwingFractionOfIT")["PassesPlanningLevels"].all()
        passing = ok[ok].index
        print(f"  {point}: {passing.max():.0%} of IT" if len(passing) else f"  {point}: none of the tested swings")


if __name__ == "__main__":
    main()
