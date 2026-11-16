"""Latin-hypercube uncertainty analysis for missing Pilbara network parameters.

Outputs the sample table, percentile summary, a running-percentile
convergence check and a one-at-a-time tornado chart showing which assumed
parameter matters most. Ranges are assumptions, so the percentiles describe
spread within those ranges - they are not probabilities of real outcomes.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import qmc

from run_pilbara_ring_study import SCENARIOS, firm_result, run_scenario
from studylib.config import RESULTS, load_config
from studylib.io import save_figure


CONFIG = load_config()
RING = CONFIG["pilbara_ring"]
CRITERIA = CONFIG["network_criteria"]
PARAMETERS = {
    "SourceFaultMVA": RING["source_fault_mva_range"],
    "PowerFactor": RING["power_factor_range"],
    "LineImpedanceMultiplier": RING["line_impedance_multiplier_range"],
    "SourceVoltagePu": RING["source_voltage_pu_range"],
}
CONTINGENCIES = [scenario for scenario in SCENARIOS if scenario[1] is not None]


def firm_capacity(source_mva: float, power_factor: float, impedance: float, source_pu: float) -> dict[str, object]:
    summaries = [
        run_scenario(
            name, outage, source_mva, RING["step_mw"], RING["maximum_mw"], power_factor,
            CRITERIA["minimum_voltage_pu"], impedance, source_pu, keep_detail=False,
        )[1]
        for name, outage in CONTINGENCIES
    ]
    return firm_result(summaries)


def thermal_ceiling_mw() -> float:
    """Firm capacity with an infinitely strong, high-voltage source: pure line-rating limit."""
    return float(firm_capacity(1e6, 1.0, 1.0, max(RING["source_voltage_pu_range"]))["LastPassingAddedMW"])


def main() -> None:
    names = list(PARAMETERS)
    lower = np.array([PARAMETERS[name][0] for name in names])
    upper = np.array([PARAMETERS[name][1] for name in names])
    sampler = qmc.LatinHypercube(d=len(names), seed=CONFIG["random_seed"])
    samples = qmc.scale(sampler.random(int(RING["monte_carlo_samples"])), lower, upper)

    rows = []
    for index, values in enumerate(samples, start=1):
        params = dict(zip(names, values))
        firm = firm_capacity(params["SourceFaultMVA"], params["PowerFactor"], params["LineImpedanceMultiplier"], params["SourceVoltagePu"])
        rows.append({"Sample": index, **params, "FirmNMinus1MW": firm["LastPassingAddedMW"],
                     "BindingScenario": firm["Scenario"], "BindingConstraint": firm["BindingConstraint"]})
    frame = pd.DataFrame(rows)
    frame.to_csv(RESULTS / "pilbara_monte_carlo_samples.csv", index=False)

    target = CONFIG["connection"]["target_mw"]
    capacity = frame["FirmNMinus1MW"].astype(float)
    ceiling = thermal_ceiling_mw()
    thermal_share = float(frame["BindingConstraint"].str.startswith("Thermal").mean())
    summary = pd.DataFrame(
        {
            "Statistic": ["Minimum", "P05", "P50", "P95", "Maximum", f"ProbabilityAtLeast{target:.0f}MW",
                          "ShareOfSamplesThermallyLimited", "LineRatingCeilingMW", "Samples"],
            "Value": [capacity.min(), capacity.quantile(0.05), capacity.quantile(0.50), capacity.quantile(0.95),
                      capacity.max(), float((capacity >= target).mean()), thermal_share, ceiling, len(frame)],
            "Unit": ["MW", "MW", "MW", "MW", "MW", "fraction", "fraction", "MW", "count"],
        }
    )
    summary.to_csv(RESULTS / "pilbara_monte_carlo_summary.csv", index=False)

    # Convergence: running percentiles should flatten well before the last sample.
    running = pd.DataFrame(
        {q: [capacity.iloc[:n].quantile(q) for n in range(10, len(capacity) + 1)] for q in (0.05, 0.5, 0.95)},
        index=range(10, len(capacity) + 1),
    )
    running.to_csv(RESULTS / "pilbara_monte_carlo_convergence.csv", index_label="Samples")

    # Tornado: vary each parameter across its range with the others at mid-range.
    mid = {name: (PARAMETERS[name][0] + PARAMETERS[name][1]) / 2 for name in names}
    base_value = float(firm_capacity(mid["SourceFaultMVA"], mid["PowerFactor"], mid["LineImpedanceMultiplier"], mid["SourceVoltagePu"])["LastPassingAddedMW"])
    tornado = []
    for name in names:
        results = []
        for bound in PARAMETERS[name]:
            params = {**mid, name: bound}
            results.append(float(firm_capacity(params["SourceFaultMVA"], params["PowerFactor"], params["LineImpedanceMultiplier"], params["SourceVoltagePu"])["LastPassingAddedMW"]))
        tornado.append({"Parameter": name, "LowSetting": PARAMETERS[name][0], "HighSetting": PARAMETERS[name][1],
                        "FirmAtLowMW": results[0], "FirmAtHighMW": results[1], "BaseMW": base_value,
                        "SwingMW": abs(results[1] - results[0])})
    tornado_frame = pd.DataFrame(tornado).sort_values("SwingMW")
    tornado_frame.to_csv(RESULTS / "pilbara_tornado.csv", index=False)

    fig, (ax_hist, ax_conv, ax_tor) = plt.subplots(1, 3, figsize=(17, 5.2))
    ax_hist.hist(capacity, bins=25, color="#0072B2", edgecolor="white")
    for quantile, colour, label in ((0.05, "#D55E00", "P05"), (0.50, "#009E73", "P50"), (0.95, "#7A5195", "P95")):
        value = capacity.quantile(quantile)
        ax_hist.axvline(value, color=colour, linewidth=2, label=f"{label}: {value:.0f} MW")
    ax_hist.axvline(ceiling, color="#333333", linestyle=":", linewidth=2, label=f"Line-rating ceiling: {ceiling:.0f} MW")
    ax_hist.set_xlabel("Indicative firm N-1 added load (MW)")
    ax_hist.set_ylabel("Samples")
    ax_hist.set_title(f"Distribution ({len(frame)} LHS samples; target {target:.0f} MW off-scale)")
    ax_hist.legend(fontsize=8)
    ax_hist.grid(axis="y", alpha=0.25)

    for q, colour in ((0.05, "#D55E00"), (0.5, "#009E73"), (0.95, "#7A5195")):
        ax_conv.plot(running.index, running[q], color=colour, label=f"P{int(q * 100):02d}")
    ax_conv.set_xlabel("Number of samples")
    ax_conv.set_ylabel("Running percentile (MW)")
    ax_conv.set_title("Convergence check")
    ax_conv.legend()
    ax_conv.grid(alpha=0.25)

    y = np.arange(len(tornado_frame))
    lows = tornado_frame["FirmAtLowMW"].to_numpy() - base_value
    highs = tornado_frame["FirmAtHighMW"].to_numpy() - base_value
    ax_tor.barh(y, lows, left=base_value, color="#D55E00", label="Parameter at low bound")
    ax_tor.barh(y, highs, left=base_value, color="#0072B2", label="Parameter at high bound")
    ax_tor.axvline(base_value, color="black", linewidth=1)
    ax_tor.set_yticks(y, tornado_frame["Parameter"])
    ax_tor.set_xlabel("Firm N-1 added load (MW)")
    ax_tor.set_title(f"One-at-a-time sensitivity (mid-range base {base_value:.0f} MW)")
    ax_tor.legend(fontsize=8)
    ax_tor.grid(axis="x", alpha=0.25)
    fig.suptitle("Pilbara reduced-ring uncertainty from missing public parameters")
    fig.tight_layout()
    save_figure(fig, RESULTS, "pilbara_monte_carlo")
    print(summary.to_string(index=False))
    print(tornado_frame[["Parameter", "FirmAtLowMW", "FirmAtHighMW", "SwingMW"]].to_string(index=False))


if __name__ == "__main__":
    main()
