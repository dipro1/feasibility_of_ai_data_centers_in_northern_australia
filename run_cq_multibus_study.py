"""Reduced multi-bus Central Queensland 275 kV study (OpenDSS).

Moves beyond single-bus Thevenin screening: a load at one bus now affects
voltages across the region, outages are real topological line outages, and
two data centres can be studied together. Source impedances are fitted to the
published minimum system-normal fault levels and validated against the
published minimum N-1 values. Topology and route lengths come from OpenStreetMap
(build_cq_topology.py) with circuit counts from the TAPR where published.
Background flows and ratings are unknown, so network results are
incremental; a corridor-level thermal proxy shifts Powerlink's published
Gladstone grid-section duration curve by each case's added section flow.
"""

from __future__ import annotations

import math

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from studylib import multibus as mb
from studylib.config import RESULTS, load_config, ner_minimum_pf
from studylib.io import save_figure, write_csv


CONFIG = load_config()
MB = CONFIG["multibus"]
MIN_V = CONFIG["network_criteria"]["minimum_voltage_pu"]
TARGET = CONFIG["connection"]["target_mw"]
MAX_MW = CONFIG["regional_equivalent"]["maximum_mw"]


def solve_with_loads(network, loads: dict[str, float], pf: float, outaged: set[str] | None = None):
    mb.build_opendss(network, outaged, CONFIG["network_criteria"]["base_source_voltage_pu"])
    for bus, mw in loads.items():
        mb.add_load(f"dc_{bus}", bus, network.kv, mw, pf)
    import opendssdirect as dss
    dss.Text.Command("solve")
    converged = bool(dss.Solution.Converged())
    return converged, (mb.bus_voltages() if converged else {}), (mb.line_flows_mw() if converged else {})


def passes(network, bus: str, mw: float, pf: float, outaged=None) -> tuple[bool, str, float]:
    converged, voltages, _ = solve_with_loads(network, {bus: mw}, pf, outaged)
    if not converged:
        return False, "convergence", float("nan")
    limiting = min(voltages, key=voltages.get)
    return voltages[limiting] >= MIN_V, limiting, voltages[limiting]


def hosting(network, bus: str, pf: float, outaged=None) -> tuple[float, str]:
    if passes(network, bus, MAX_MW, pf, outaged)[0]:
        return MAX_MW, "sweep maximum"
    low, high = 0, int(MAX_MW)
    limiting = ""
    while high - low > 1:
        middle = (low + high) // 2
        ok, bus_limit, _ = passes(network, bus, float(middle), pf, outaged)
        if ok:
            low = middle
        else:
            high, limiting = middle, bus_limit
    return float(low), limiting


def main() -> None:
    network = mb.load_network(MB["model_dir"], MB["source_xr"])
    fit = mb.fit_sources(network)
    pf = ner_minimum_pf(network.kv)

    model_normal = mb.fault_levels(network)
    model_n1 = mb.n_minus_1_fault_levels(network)
    opendss_levels = mb.opendss_fault_levels(network)
    published_normal = network.published_mva("min_normal_fault_ka")
    published_n1 = network.published_mva("min_n1_fault_ka")
    calibration = [
        {
            "Bus": bus_id,
            "Name": network.bus(bus_id)["name"],
            "PublishedMinNormalMVA": published_normal[bus_id],
            "ModelMinNormalMVA": model_normal[bus_id],
            "OpenDSSFaultStudyMVA": opendss_levels[bus_id],
            "FitErrorPercent": 100 * (model_normal[bus_id] / published_normal[bus_id] - 1),
            "PublishedMinN1MVA": published_n1[bus_id],
            "ModelMinN1MVA": model_n1[bus_id][0],
            "ModelWorstOutage": model_n1[bus_id][1],
            "N1ValidationErrorPercent": 100 * (model_n1[bus_id][0] / published_n1[bus_id] - 1),
        }
        for bus_id in network.bus_ids
    ]
    write_csv(RESULTS / "cq_multibus_calibration.csv", calibration)
    write_csv(RESULTS / "cq_multibus_sources.csv",
              [{"SourceBus": s, "FittedFaultContributionMVA": v, "SourceXR": network.source_xr} for s, v in network.source_mva.items()]
              + [{"SourceBus": "LINE_IMPEDANCE_MULTIPLIER", "FittedFaultContributionMVA": fit["line_multiplier"], "SourceXR": ""}])

    regional = pd.read_csv(RESULTS / "regional_equivalent_sweep_summary.csv")
    single_bus = {row.ConnectionPoint.replace(" 275 kV", ""): row.LastPassingMW
                  for row in regional[regional["Case"] == "intact"].itertuples()}
    host_rows = []
    for bus in MB["candidate_buses"]:
        intact, intact_limit = hosting(network, bus, pf)
        worst = (float("inf"), "", "")
        for contingency in network.contingencies:
            value, limit_bus = hosting(network, bus, pf, {contingency["element_id"]})
            if value < worst[0]:
                worst = (value, contingency["element_id"], limit_bus)
        name = network.bus(bus)["name"]
        host_rows.append({
            "Bus": bus, "Name": name, "PowerFactor": pf,
            "IntactHostingMW": intact, "IntactLimitingBus": intact_limit,
            "WorstN1HostingMW": worst[0], "WorstOutage": worst[1], "N1LimitingBus": worst[2],
            "SingleBusTheveninIntactMW": single_bus.get(name, np.nan),
            "TargetPassesN1": worst[0] >= TARGET,
        })
        print(f"{name}: intact {intact:.0f} MW, worst N-1 {worst[0]:.0f} MW ({worst[1]})", flush=True)
    write_csv(RESULTS / "cq_multibus_hosting.csv", host_rows)

    impact_rows, flow_rows = [], []
    base_converged, base_v, _ = solve_with_loads(network, {}, pf)
    cases = {f"{TARGET:.0f} MW at {b}": {b: TARGET} for b in MB["two_site_case"]}
    cases[f"{TARGET:.0f} MW at both " + " and ".join(MB["two_site_case"])] = {b: TARGET for b in MB["two_site_case"]}
    two_site_n1 = []
    for label, loads in cases.items():
        converged, voltages, flows = solve_with_loads(network, loads, pf)
        for bus_id in network.bus_ids:
            impact_rows.append({"Case": label, "Bus": bus_id, "VoltagePu": voltages.get(bus_id, np.nan),
                                "VoltageChangePercent": 100 * (voltages.get(bus_id, np.nan) - base_v[bus_id]) / base_v[bus_id]})
        total = sum(loads.values())
        for line, mw in sorted(flows.items(), key=lambda item: -abs(item[1])):
            flow_rows.append({"Case": label, "Line": line, "IncrementalFlowMW": mw, "ShareOfAddedLoadPercent": 100 * abs(mw) / total})
        worst_v, worst_outage = float("inf"), ""
        for contingency in network.contingencies:
            ok, vv, _ = solve_with_loads(network, loads, pf, {contingency["element_id"]})
            value = min(vv.values()) if ok else float("nan")
            if not ok or value < worst_v:
                worst_v, worst_outage = value, contingency["element_id"]
        two_site_n1.append({"Case": label, "IntactMinVoltagePu": min(voltages.values()), "WorstN1MinVoltagePu": worst_v,
                            "WorstOutage": worst_outage, "PassesN1": worst_v >= MIN_V})
    write_csv(RESULTS / "cq_multibus_voltage_impact.csv", impact_rows)
    write_csv(RESULTS / "cq_multibus_incremental_flows.csv", flow_rows)
    write_csv(RESULTS / "cq_multibus_multi_site.csv", two_site_n1)

    section_rows = grid_section_proxy(flow_rows, cases)
    write_csv(RESULTS / "cq_grid_section_proxy.csv", section_rows)
    plot(network, calibration, host_rows, impact_rows, flow_rows, cases)
    plot_section(section_rows)
    print(pd.DataFrame(two_site_n1).round(4).to_string(index=False))
    print(f"Fit RMS error {100 * fit['rms_log_error']:.1f}% (log), line multiplier {fit['line_multiplier']:.2f}")


def section_curve():
    sec = MB["grid_section"]
    return np.array(sec["duration_curve_percent"], dtype=float), np.array(sec["duration_curve_mw"], dtype=float)


def grid_section_proxy(flow_rows, cases) -> list[dict[str, object]]:
    """Shift the historical section duration curve by each case's incremental section flow."""
    sec = MB["grid_section"]
    percent, mw = section_curve()
    hours = 8760.0
    limit = float(np.interp(100.0 * sec["constrained_hours_2024_25"] / hours, percent, mw))
    fine = np.linspace(0, 100, 2001)
    curve = np.interp(fine, percent, mw)
    flows = pd.DataFrame(flow_rows)
    rows = []
    for label in ["Existing (2024/25)"] + list(cases):
        delta = 0.0 if label.startswith("Existing") else float(
            flows[(flows["Case"] == label) & (flows["Line"].isin(sec["circuits"]))]["IncrementalFlowMW"].sum())
        share_above = float(((curve + delta) > limit).mean())
        rows.append({
            "GridSection": sec["name"], "Case": label, "IncrementalSectionFlowMW": delta,
            "EffectiveLimitMW": limit, "PeakSectionFlowMW": float(mw.max() + delta),
            "HoursPerYearAboveLimit": share_above * hours,
            "Interpretation": ("Reference" if label.startswith("Existing") else
                               "Relieves the section" if delta < 0 else "Adds constraint hours (re-dispatch or augmentation needed)"),
        })
    return rows


def plot_section(section_rows) -> None:
    percent, mw = section_curve()
    frame = pd.DataFrame(section_rows)
    fig, ax = plt.subplots(figsize=(10, 5))
    colours = {"Existing (2024/25)": "#555555"}
    palette = ["#0072B2", "#D55E00", "#009E73"]
    for i, row in enumerate(frame.itertuples()):
        colour = colours.get(row.Case, palette[(i - 1) % len(palette)])
        ax.plot(percent, mw + row.IncrementalSectionFlowMW, color=colour, linewidth=2 if i == 0 else 1.5,
                label=f"{row.Case} ({row.IncrementalSectionFlowMW:+.0f} MW, {row.HoursPerYearAboveLimit:.0f} h/yr above limit)")
    limit = frame["EffectiveLimitMW"].iloc[0]
    ax.axhline(limit, color="#B22222", linestyle="--", label=f"Effective limit ~{limit:.0f} MW (54 constrained h in 2024/25)")
    ax.set_xlabel("Percentage of time (%)")
    ax.set_ylabel("Gladstone grid-section transfer into Gladstone zone (MW)")
    ax.set_title("Gladstone grid section: historical duration curve shifted by each data-centre case")
    ax.set_xlim(0, 30)
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8)
    fig.text(0.01, 0.01, "Curve digitised from Powerlink TAPR 2025 Fig. 6.17 (approximate); indicative corridor proxy, not a thermal study.", fontsize=7)
    fig.tight_layout()
    save_figure(fig, RESULTS, "cq_grid_section_proxy")


def plot(network, calibration, host_rows, impact_rows, flow_rows, cases) -> None:
    cal = pd.DataFrame(calibration)
    fig, axes = plt.subplots(1, 3, figsize=(19, 5.8))
    ax = axes[0]
    ax.scatter(cal["PublishedMinNormalMVA"], cal["ModelMinNormalMVA"], color="#0072B2", label="System normal (fitted)")
    ax.scatter(cal["PublishedMinN1MVA"], cal["ModelMinN1MVA"], color="#D55E00", marker="^", label="N-1 (validation, not fitted)")
    for row in cal.itertuples():
        ax.annotate(row.Bus, (row.PublishedMinN1MVA, row.ModelMinN1MVA), fontsize=6, xytext=(3, -8), textcoords="offset points")
    limit = max(cal[["PublishedMinNormalMVA", "ModelMinNormalMVA", "ModelMinN1MVA"]].max()) * 1.05
    ax.plot([0, limit], [0, limit], color="#444444", linestyle="--", linewidth=1)
    ax.set_xlabel("Published minimum fault level (MVA)")
    ax.set_ylabel("Model fault level (MVA)")
    ax.set_title("Calibration (fitted) and N-1 validation")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25)

    hosts = pd.DataFrame(host_rows)
    x = np.arange(len(hosts))
    axes[1].bar(x - 0.27, hosts["SingleBusTheveninIntactMW"], 0.27, label="Single-bus Thevenin, intact", color="#BBBBBB")
    axes[1].bar(x, hosts["IntactHostingMW"], 0.27, label="Multi-bus, intact", color="#0072B2")
    axes[1].bar(x + 0.27, hosts["WorstN1HostingMW"], 0.27, label="Multi-bus, worst line outage", color="#D55E00")
    axes[1].axhline(TARGET, color="#B22222", linestyle="--", label=f"{TARGET:.0f} MW target")
    axes[1].set_xticks(x, hosts["Name"], rotation=30, ha="right", fontsize=8)
    axes[1].set_ylabel("Voltage-limited hosting (MW)")
    axes[1].set_title("Hosting by bus (system-wide 0.95 pu criterion)")
    axes[1].legend(fontsize=7)
    axes[1].grid(axis="y", alpha=0.25)

    ax = axes[2]
    label = list(cases)[-1]
    impact = pd.DataFrame(impact_rows)
    impact = impact[impact["Case"] == label].set_index("Bus")
    flows = pd.DataFrame(flow_rows)
    flows = flows[flows["Case"] == label]
    coords = {b["bus_id"]: (float(b["longitude"]), float(b["latitude"])) for b in network.buses}
    per_corridor = {}
    for row in flows.itertuples():
        parts = row.Line.split("_")
        key = (parts[0], parts[1])
        per_corridor[key] = per_corridor.get(key, 0.0) + abs(row.IncrementalFlowMW)
    max_flow = max(per_corridor.values())
    for (a, b), mw in per_corridor.items():
        (x1, y1), (x2, y2) = coords[a], coords[b]
        ax.plot([x1, x2], [y1, y2], color="#555555", linewidth=0.8 + 6 * mw / max_flow, alpha=0.7, zorder=1)
        if mw / max_flow > 0.15:
            ax.text((x1 + x2) / 2, (y1 + y2) / 2, f"{mw:.0f}", fontsize=6, color="#222222")
    values = impact["VoltageChangePercent"]
    scatter = ax.scatter([coords[b][0] for b in impact.index], [coords[b][1] for b in impact.index],
                         c=values, cmap="viridis", s=120, zorder=2, edgecolor="black")
    for bus_id in impact.index:
        ax.annotate(bus_id, coords[bus_id], fontsize=7, xytext=(5, 4), textcoords="offset points")
    plt.colorbar(scatter, ax=ax, label="Voltage change (%)")
    ax.set_title(f"{label}: voltage change and incremental MW flow\n(OpenStreetMap topology; drawing positions approximate)", fontsize=9)
    ax.set_xlabel("Longitude (approx.)")
    ax.set_ylabel("Latitude (approx.)")
    fig.suptitle("Reduced Central Queensland 275 kV multi-bus equivalent (OSM topology, fitted to TAPR fault levels)")
    fig.tight_layout()
    save_figure(fig, RESULTS, "cq_multibus_study")


if __name__ == "__main__":
    main()
