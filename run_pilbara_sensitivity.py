"""Sweep uncertain Pilbara source strength, load power factor and source voltage."""

from __future__ import annotations

import matplotlib.pyplot as plt

from run_pilbara_ring_study import SCENARIOS, firm_result, run_scenario
from studylib.config import RESULTS, load_config
from studylib.io import save_figure, write_csv


CONFIG = load_config()
RING = CONFIG["pilbara_ring"]
CRITERIA = CONFIG["network_criteria"]


def main() -> None:
    rows: list[dict[str, object]] = []
    for source_pu in CRITERIA["source_voltage_sensitivity_pu"]:
        for power_factor in CRITERIA["power_factor_sensitivity"]:
            for source_mva in RING["source_sensitivity_mva"]:
                summaries = [
                    run_scenario(
                        name, outage, source_mva, RING["step_mw"], RING["maximum_mw"], power_factor,
                        CRITERIA["minimum_voltage_pu"], source_pu=source_pu, keep_detail=False,
                    )[1]
                    for name, outage in SCENARIOS
                ]
                intact = summaries[0]
                firm = firm_result(summaries)
                rows.append(
                    {
                        "SourceVoltagePu": source_pu,
                        "AssumedSourceFaultMVA": source_mva,
                        "PowerFactor": power_factor,
                        "IntactHostingEstimateMW": intact["LastPassingAddedMW"],
                        "FirmNMinus1HostingEstimateMW": firm["LastPassingAddedMW"],
                        "BindingNMinus1Scenario": firm["Scenario"],
                        "BindingConstraint": firm["BindingConstraint"],
                        "Classification": "Sensitivity range; source strength and line impedances are not utility-validated",
                    }
                )
    write_csv(RESULTS / "pilbara_source_strength_sensitivity.csv", rows)

    target = CONFIG["connection"]["target_mw"]
    voltages = CRITERIA["source_voltage_sensitivity_pu"]
    fig, axes = plt.subplots(1, len(voltages), figsize=(4.6 * len(voltages), 4.6), sharey=True)
    colours = plt.get_cmap("viridis")
    pfs = CRITERIA["power_factor_sensitivity"]
    for ax, source_pu in zip(axes, voltages):
        for index, power_factor in enumerate(pfs):
            subset = [r for r in rows if r["PowerFactor"] == power_factor and r["SourceVoltagePu"] == source_pu]
            ax.plot(
                [r["AssumedSourceFaultMVA"] for r in subset],
                [r["FirmNMinus1HostingEstimateMW"] for r in subset],
                marker="o", linewidth=2, color=colours(index / max(len(pfs) - 1, 1)), label=f"PF {power_factor:.2f}",
            )
        ax.axhline(target, color="#B22222", linestyle="--", linewidth=1.3, label=f"{target:.0f} MW target")
        ax.set_title(f"Source voltage {source_pu:.2f} pu")
        ax.set_xlabel("Assumed 132 kV source fault level (MVA)")
        ax.grid(alpha=0.25)
    axes[0].set_ylabel("Indicative firm N-1 added load (MW)")
    axes[0].legend(fontsize=8)
    fig.suptitle("Karratha 132 kV ring: firm N-1 capacity is capped by the 157 MVA line rating once the source is strong")
    fig.tight_layout()
    save_figure(fig, RESULTS, "pilbara_source_strength_sensitivity")
    firm_values = [float(r["FirmNMinus1HostingEstimateMW"]) for r in rows]
    print(f"Firm N-1 range: {min(firm_values):.0f} to {max(firm_values):.0f} MW over {len(rows)} combinations")


if __name__ == "__main__":
    main()
