"""Hourly DC power flow on the Central Queensland multi-bus model (one year).

For every hour of the most recent 365 days:

* injections: metered net output of the Central Queensland units mapped to
  each bus (AEMO Dispatch SCADA, inputs/grid/cq_bus_hourly.csv);
* loads: the indicative zone loads of the RMS study, with a fixed industrial
  share and the remainder scaled by Queensland operational demand;
* the external system (each source bus tied to a common reference through its
  fitted source impedance) balances the hour.

Line flows follow from a DC (lossless, flat-voltage) power flow. Single-line
outages use line outage distribution factors (LODF). The study then:

1. validates the model against Powerlink's published 2024/25 flow duration
   curves for the Gladstone and CQ-SQ grid sections;
2. adds the data centre at Bouldercombe or Calliope River (245 MW, or 200 MW
   when the on-site PV/battery design caps import) and reports, for every
   275 kV circuit, the change in flow, intact and worst N-1 loading against an
   assumed typical rating, and hours above that rating.

Ratings are not published, so loadings are against an assumed 900 MVA per
circuit (VERIFY); the change caused by the data centre does not depend on the
rating. Outputs: results/hourly_flow_*.csv and results/hourly_power_flow.png.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from studylib import multibus as mb
from studylib.config import INPUTS, PRICE_DIR, RESULTS, load_config
from studylib.io import save_figure, write_csv


CONFIG = load_config()
HF = CONFIG["hourly_flow"]
MB = CONFIG["multibus"]
RMS = CONFIG["rms_study"]
REF = "EXT"


def load_inputs() -> tuple[pd.DataFrame, pd.Series]:
    generation = pd.read_csv(INPUTS / "grid" / "cq_bus_hourly.csv", parse_dates=["timestamp"]).set_index("timestamp")
    demand = pd.read_csv(PRICE_DIR / "QLD1_recent_hourly.csv", parse_dates=["timestamp"]).set_index("timestamp")["demand_mw"]
    hours = generation.index.intersection(demand.index)
    return generation.loc[hours], demand.loc[hours]


class DcModel:
    """B-theta model with the external system as a reference node."""

    def __init__(self, network: mb.Network):
        self.network = network
        self.buses = network.bus_ids
        self.index = {bus: i for i, bus in enumerate(self.buses)}
        elements = []  # (id, from, to, reactance ohm); to=None means tie to the external reference
        for branch in network.branches:
            elements.append((branch["branch_id"], branch["from_bus"], branch["to_bus"],
                             network.branch_impedance(branch).imag))
        for bus, mva in network.source_mva.items():
            elements.append((f"{REF}_{bus}", bus, None, mb.source_impedance(network.kv, mva, network.source_xr).imag))
        self.ids = [e[0] for e in elements]
        n, m = len(self.buses), len(elements)
        self.incidence = np.zeros((m, n))
        self.b = np.zeros(m)
        for k, (_, frm, to, x) in enumerate(elements):
            self.incidence[k, self.index[frm]] = 1.0
            if to is not None:
                self.incidence[k, self.index[to]] = -1.0
            self.b[k] = 1.0 / x
        bbus = self.incidence.T @ (self.b[:, None] * self.incidence)
        # PTDF: element flow per MW injected at each bus (withdrawn at the reference)
        self.ptdf = (self.b[:, None] * self.incidence) @ np.linalg.inv(bbus)
        self.lodf = self._lodf()

    def _lodf(self) -> np.ndarray:
        """LODF[l, k]: change in flow on l per MW pre-outage flow on k when k is lost."""
        m = len(self.ids)
        # flow on l for a 1 MW transfer from the from-bus to the to-bus of k
        transfer = self.ptdf @ self.incidence.T  # (m, m)
        lodf = np.full((m, m), np.nan)
        for k in range(m):
            denominator = 1.0 - transfer[k, k]
            if abs(denominator) < 1e-6:
                continue  # outage of k splits the network (radial element)
            lodf[:, k] = transfer[:, k] / denominator
            lodf[k, k] = -1.0
        return lodf

    def flows(self, injections: np.ndarray) -> np.ndarray:
        """Element flows (m x hours) for bus injections (n x hours, MW, generation positive)."""
        return self.ptdf @ injections


def bus_injections(model: DcModel, generation: pd.DataFrame, demand: pd.Series, extra_load: dict[str, float],
                   multipliers: dict[str, float] | None = None) -> np.ndarray:
    share = HF["industrial_load_share"]
    scale = share + (1.0 - share) * demand.to_numpy() / demand.mean()
    multipliers = multipliers or {}
    inj = np.zeros((len(model.buses), len(demand)))
    for bus, mw in RMS["background_load_mw"].items():
        inj[model.index[bus]] -= mw * multipliers.get(bus, 1.0) * scale
    for bus in generation.columns:
        inj[model.index[bus]] += generation[bus].to_numpy()
    for bus, mw in extra_load.items():
        inj[model.index[bus]] -= mw
    return inj


def section_flow(model: DcModel, flows: np.ndarray, lines: list[str], external_from: list[str] = ()) -> np.ndarray:
    pos = {e: k for k, e in enumerate(model.ids)}
    total = sum(flows[pos[line]] for line in lines)
    for bus in external_from:
        total = total + flows[pos[f"{REF}_{bus}"]]
    return total


def duration_points(values: np.ndarray, percents) -> np.ndarray:
    return np.percentile(values, 100 - np.asarray(percents, dtype=float))


def calibrate(model: DcModel, generation: pd.DataFrame, demand: pd.Series) -> tuple[dict[str, float], dict]:
    """Fit two load multipliers (Gladstone-zone loads; Nebo, i.e. transfer to North Queensland)
    so the modelled Gladstone and CQ-SQ duration curves match the published 2024/25 curves."""
    from scipy.optimize import least_squares

    groups = HF["calibration"]["groups"]
    glad, cqsq = MB["grid_section"], HF["cq_sq_section"]

    def multipliers(x):
        return {bus: float(x[i]) for i, buses in enumerate(groups.values()) for bus in buses}

    def residuals(x):
        flows = model.flows(bus_injections(model, generation, demand, {}, multipliers(x)))
        out = []
        for series, percent, published in [
            (section_flow(model, flows, glad["circuits"]), glad["duration_curve_percent"], glad["duration_curve_mw"]),
            (section_flow(model, flows, cqsq["lines"], cqsq["external_from"]), cqsq["duration_curve_percent"],
             cqsq["duration_curve_mw"])]:
            # Interior points only: the extremes of a one-year curve are dominated by single events.
            keep = [(p, v) for p, v in zip(percent, published) if 3 <= p <= 97]
            out.extend(duration_points(series, [p for p, _ in keep]) - np.array([v for _, v in keep]))
        return np.asarray(out)

    lower, upper = HF["calibration"]["bounds"]
    result = least_squares(residuals, x0=np.ones(len(groups)), bounds=(lower, upper))
    names = list(groups)
    before = residuals(np.ones(len(groups)))
    info = {"multipliers": {name: float(v) for name, v in zip(names, result.x)},
            "rms_error_before_mw": float(np.sqrt(np.mean(before**2))),
            "rms_error_after_mw": float(np.sqrt(np.mean(result.fun**2)))}
    return multipliers(result.x), info


def validate(model: DcModel, flows: np.ndarray) -> list[dict[str, object]]:
    rows = []
    glad = MB["grid_section"]
    cqsq = HF["cq_sq_section"]
    checks = [
        ("Gladstone", section_flow(model, flows, glad["circuits"]), glad["duration_curve_percent"], glad["duration_curve_mw"]),
        ("CQ-SQ", section_flow(model, flows, cqsq["lines"], cqsq["external_from"]),
         cqsq["duration_curve_percent"], cqsq["duration_curve_mw"]),
    ]
    for name, series, percent, published in checks:
        modelled = duration_points(series, percent)
        for p, pub, mod in zip(percent, published, modelled):
            rows.append({"GridSection": name, "PercentOfTime": p, "PublishedMW_2024_25": pub, "ModelMW": float(mod),
                         "DifferenceMW": float(mod - pub)})
    return rows


def circuit_rows(model: DcModel, flows_by_case: dict[str, np.ndarray]) -> list[dict[str, object]]:
    rating = float(HF["assumed_rating_mva"]["default"])
    base = flows_by_case["Existing system"]
    lines = [k for k, e in enumerate(model.ids) if not e.startswith(REF)]
    rows = []
    for case, flows in flows_by_case.items():
        # Worst post-contingency flow on each line over all single-line outages (LODF), hour by hour.
        post = np.abs(flows[lines])
        for k in lines:
            if np.isnan(model.lodf[0, k]):
                continue
            after = flows[lines] + model.lodf[lines, k][:, None] * flows[k][None, :]
            after[lines.index(k)] = 0.0
            post = np.maximum(post, np.abs(after))
        for i, k in enumerate(lines):
            intact = np.abs(flows[k])
            rows.append({
                "Case": case, "Circuit": model.ids[k],
                "MeanChangeMW": float(np.mean(flows[k] - base[k])),
                "P99IntactMW": float(np.percentile(intact, 99)), "MaxIntactMW": float(intact.max()),
                "MaxN1MW": float(post[i].max()), "AssumedRatingMVA": rating,
                "MaxN1LoadingPercent": float(100 * post[i].max() / rating),
                "HoursN1AboveRating": int((post[i] > rating).sum()),
                "HoursN1Above90Percent": int((post[i] > 0.9 * rating).sum()),
            })
    frame = pd.DataFrame(rows)
    existing = frame[frame["Case"] == "Existing system"].set_index("Circuit")["MaxN1LoadingPercent"]
    frame["ChangeInMaxN1LoadingPoints"] = frame["MaxN1LoadingPercent"] - frame["Circuit"].map(existing)
    frame["Note"] = np.where(frame["Circuit"].map(existing) > 100,
                             "Above assumed rating even without the data centre: reduced-model outlet or rating assumption (VERIFY)",
                             "")
    return frame.to_dict("records")


def section_rows(model: DcModel, flows_by_case: dict[str, np.ndarray]) -> list[dict[str, object]]:
    glad = MB["grid_section"]
    percent, mw = np.asarray(glad["duration_curve_percent"]), np.asarray(glad["duration_curve_mw"])
    limit = float(np.interp(100.0 * glad["constrained_hours_2024_25"] / 8760.0, percent, mw))
    base = section_flow(model, flows_by_case["Existing system"], glad["circuits"])
    # Hours above the effective limit, with the modelled section flow bias-corrected to the
    # published curve so the constraint hours of the existing system match Powerlink's.
    bias = float(np.median(base) - np.interp(50, percent, mw))
    rows = []
    for case, flows in flows_by_case.items():
        series = section_flow(model, flows, glad["circuits"]) - bias
        rows.append({"Case": case, "Note": "Model spread is narrower than the published curve; use the shifted published "
                                           "curve (cq_grid_section_proxy.csv) for constraint hours",
                     "MeanSectionFlowMW": float(series.mean()),
                     "MeanChangeMW": float((series + bias - base).mean()),
                     "P99SectionFlowMW": float(np.percentile(series, 99)), "EffectiveLimitMW": limit,
                     "HoursAboveEffectiveLimit": int((series > limit).sum()), "ModelBiasRemovedMW": bias})
    return rows


def plot(model, validation, flows_by_case, circuits) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(18, 5.4))
    frame = pd.DataFrame(validation)
    for ax, name in zip(axes[:2], ["Gladstone", "CQ-SQ"]):
        group = frame[frame["GridSection"] == name]
        ax.plot(group["PercentOfTime"], group["PublishedMW_2024_25"], "k-", linewidth=2, label="Powerlink TAPR 2025 (2024/25)")
        lines = MB["grid_section"]["circuits"] if name == "Gladstone" else HF["cq_sq_section"]["lines"]
        external = [] if name == "Gladstone" else HF["cq_sq_section"]["external_from"]
        for case, colour in [("Existing system", "#0072B2"), ("245 MW at Bouldercombe", "#009E73"),
                             ("245 MW at Calliope River", "#D55E00")]:
            series = section_flow(model, flows_by_case[case], lines, external)
            pct = np.linspace(0, 100, 201)
            ax.plot(pct, duration_points(series, pct), color=colour, linewidth=1.4,
                    linestyle="-" if case == "Existing system" else "--", label=f"Model: {case}")
        ax.set_title(f"{name} grid section: model (Nov 2025-Nov 2026) vs published")
        ax.set_xlabel("Percentage of time (%)")
        ax.set_ylabel("Section transfer (MW)")
        ax.grid(alpha=0.25)
        ax.legend(fontsize=7)
    table = pd.DataFrame(circuits)
    pivot = table.pivot(index="Circuit", columns="Case", values="MaxN1LoadingPercent")
    order = pivot["Existing system"].sort_values(ascending=False).index[:10]
    cases = ["Existing system", "245 MW at Bouldercombe", "245 MW at Calliope River"]
    y = np.arange(len(order))
    for i, (case, colour) in enumerate(zip(cases, ["#0072B2", "#009E73", "#D55E00"])):
        axes[2].barh(y + (i - 1) * 0.27, pivot.loc[order, case], height=0.27, color=colour, label=case)
    axes[2].axvline(100, color="#B22222", linestyle="--", linewidth=1, label="Assumed rating (900 MVA)")
    axes[2].set_yticks(y)
    axes[2].set_yticklabels(order, fontsize=8)
    axes[2].invert_yaxis()
    axes[2].set_xlabel("Maximum N-1 loading over the year (% of assumed rating)")
    axes[2].set_title("Most loaded circuits under single-line outages")
    axes[2].legend(fontsize=7)
    axes[2].grid(alpha=0.25, axis="x")
    fig.suptitle("Hourly DC power flow, Central Queensland 275 kV (metered AEMO unit output; assumed ratings and zone loads)")
    fig.tight_layout()
    save_figure(fig, RESULTS, "hourly_power_flow")


def main() -> None:
    network = mb.load_network(MB["model_dir"], MB["source_xr"])
    mb.fit_sources(network)
    model = DcModel(network)
    generation, demand = load_inputs()
    print(f"{len(demand)} hours, {demand.index.min()} to {demand.index.max()}")
    multipliers, info = calibrate(model, generation, demand)
    print("Calibration:", info)
    write_csv(RESULTS / "hourly_flow_calibration.csv",
              [{"LoadGroup": name, "Buses": " ".join(HF["calibration"]["groups"][name]), "Multiplier": value,
                "RmsErrorBeforeMW": info["rms_error_before_mw"], "RmsErrorAfterMW": info["rms_error_after_mw"]}
               for name, value in info["multipliers"].items()])
    flows_by_case = {case: model.flows(bus_injections(model, generation, demand, extra, multipliers))
                     for case, extra in HF["cases"].items()}
    validation = validate(model, flows_by_case["Existing system"])
    write_csv(RESULTS / "hourly_flow_validation.csv", validation)
    circuits = circuit_rows(model, flows_by_case)
    write_csv(RESULTS / "hourly_flow_circuits.csv", circuits)
    sections = section_rows(model, flows_by_case)
    write_csv(RESULTS / "hourly_flow_gladstone_section.csv", sections)
    plot(model, validation, flows_by_case, circuits)
    print(pd.DataFrame(validation).round(0).to_string(index=False))
    print(pd.DataFrame(sections).round(1).to_string(index=False))
    table = pd.DataFrame(circuits)
    print(table[table["Case"].isin(["Existing system", "245 MW at Bouldercombe", "245 MW at Calliope River"])]
          .pivot(index="Circuit", columns="Case", values="MaxN1LoadingPercent").round(0).sort_values("Existing system").to_string())


if __name__ == "__main__":
    main()
