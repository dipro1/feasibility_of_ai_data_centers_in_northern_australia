"""Plot the regional-equivalent screening as PNG and editable SVG figures."""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from studylib.config import RESULTS, load_config
from studylib.io import save_figure


def main() -> None:
    config = load_config()
    target = config["connection"]["target_mw"]
    reference = config["connection"]["reference_mw"]
    sweep_max = config["regional_equivalent"]["maximum_mw"]
    minimum_v = config["network_criteria"]["minimum_voltage_pu"]
    detail = pd.read_csv(RESULTS / "regional_equivalent_sweep_detail.csv")
    summary = pd.read_csv(RESULTS / "regional_equivalent_sweep_summary.csv")
    sensitivity = pd.read_csv(RESULTS / "regional_equivalent_pf_voltage_sensitivity.csv")

    transmission = summary[summary["VoltageKV"] >= 100]
    fig, (ax_v, ax_c) = plt.subplots(1, 2, figsize=(13, 5.6), gridspec_kw={"width_ratios": [1.5, 1]})
    colours = plt.get_cmap("tab10")
    for index, row in enumerate(transmission.itertuples()):
        curve = detail[(detail["SiteId"] == row.SiteId) & (detail["Case"] == row.Case)]
        ax_v.plot(curve["LoadMW"], curve["PointVoltagePu"], color=colours(index), linewidth=2,
                  linestyle="-" if row.Case == "intact" else "--",
                  label=f"{row.ConnectionPoint} {row.Case.replace('_', ' ')}")
    ax_v.axhline(minimum_v, color="#B22222", linestyle=":", label=f"{minimum_v:g} pu criterion")
    ax_v.axvline(target, color="#444444", linestyle="--", linewidth=1, label=f"{target:.0f} MW target")
    ax_v.set_xlabel("Added data-centre load (MW)")
    ax_v.set_ylabel("Connection-point voltage (pu)")
    ax_v.set_title("Voltage response at base power factor")
    ax_v.grid(alpha=0.25)
    ax_v.legend(fontsize=7)

    labels = [f"{r.ConnectionPoint}\n{r.Case.replace('_', ' ')}" for r in summary.itertuples()]
    values = summary["LastPassingMW"].to_numpy()
    bar_colours = ["#2E8B57" if v >= target else "#C0392B" for v in values]
    y = np.arange(len(values))
    ax_c.barh(y, values, color=bar_colours)
    ax_c.axvline(target, color="#444444", linestyle="--", label=f"{target:.0f} MW target")
    ax_c.axvline(reference, color="#888888", linestyle=":", label=f"{reference:.0f} MW reference")
    ax_c.set_yticks(y, labels, fontsize=7)
    ax_c.invert_yaxis()
    ax_c.set_xlabel("Screening limit (MW)")
    ax_c.set_title("First-limiting screening threshold")
    for yi, value in zip(y, values):
        ax_c.text(value + 5, yi, f"{value:.0f}", va="center", fontsize=7)
    ax_c.grid(axis="x", alpha=0.25)
    ax_c.legend(fontsize=7, loc="lower right")
    fig.suptitle("Public-data Thevenin screening (not a connection approval)", fontsize=13)
    fig.tight_layout()
    save_figure(fig, RESULTS, "regional_equivalent_screening")

    cases = sensitivity[sensitivity["SiteId"].isin(transmission["SiteId"])]
    groups = list(cases.groupby(["ConnectionPoint", "Case"], sort=False))
    fig2, axes = plt.subplots(1, len(groups), figsize=(2.6 * len(groups), 3.4), sharey=True)
    for ax, ((point, case), frame) in zip(np.atleast_1d(axes), groups):
        grid = frame.pivot(index="PowerFactor", columns="SourceVoltagePu", values="LastPassingMW").sort_index(ascending=False)
        image = ax.imshow(grid.to_numpy(), cmap="RdYlGn", vmin=0, vmax=2 * target, aspect="auto")
        for (i, j), value in np.ndenumerate(grid.to_numpy()):
            text = f">={value:.0f}" if value >= sweep_max else f"{value:.0f}"
            ax.text(j, i, text, ha="center", va="center", fontsize=8)
        ax.set_xticks(range(grid.shape[1]), [f"{v:.2f}" for v in grid.columns], fontsize=7)
        ax.set_yticks(range(grid.shape[0]), [f"{v:.2f}" for v in grid.index], fontsize=7)
        ax.set_title(f"{point.replace(' 275 kV', '')}\n{case.replace('_', ' ')}", fontsize=8)
        ax.set_xlabel("Source voltage (pu)", fontsize=7)
    np.atleast_1d(axes)[0].set_ylabel("Load power factor")
    fig2.colorbar(image, ax=axes, shrink=0.8, label="Voltage-limited MW")
    fig2.suptitle(f"Voltage-limited capacity vs load PF and pre-connection source voltage (colour midpoint = {target:.0f} MW target)", fontsize=10, y=1.06)
    save_figure(fig2, RESULTS, "regional_pf_voltage_sensitivity")
    print(RESULTS / "regional_equivalent_screening.png")


if __name__ == "__main__":
    main()
