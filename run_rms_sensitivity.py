"""Extensions of the open-source RMS study (ANDES).

1. Frequency-response sensitivity (Queensland islanded, minimum condition).
   The reduced-order screen and the RMS model disagree on the islanded
   full-load loss because they assume different amounts and speeds of
   primary frequency response. This sweeps system inertia, the delivered
   share of governor response and the governor time constant, and finds the
   response at which the event leaves the 49.0-51.0 Hz island band.

2. Fault ride-through (Virginia-type event). A near-bolted three-phase fault is
   applied at every bus in turn and cleared after the configured time. If the
   data-centre bus voltage falls below the assumed undervoltage trip
   threshold, the run is repeated with the whole facility disconnecting during
   the fault, so the system sees the fault and a 245 MW load loss together.

Outputs: results/rms_frequency_response_sensitivity.csv,
results/rms_frequency_response_thresholds.csv,
results/rms_fault_ride_through.csv and results/rms_sensitivity.png.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import run_rms_dynamic_study as rms
from studylib.config import RESULTS, load_config
from studylib.io import save_figure, write_csv


CONFIG = load_config()
SENS = CONFIG["rms_sensitivity"]
RMS = CONFIG["rms_study"]
FN = 50.0
BAND_HALF_WIDTH_HZ = 1.0  # island containment band 49.0-51.0 Hz


def system_inertia_mws(condition: str, h_scale: float = 1.0) -> float:
    cond = RMS["conditions"][condition]
    units = sum(sn for sn, _ in cond["units"].values()) * RMS["machine"]["H_s"]
    return h_scale * (units + cond["external"]["Sn"] * cond["external"]["H_s"])


def governed_mva(condition: str) -> float:
    cond = RMS["conditions"][condition]
    return sum(sn for sn, _ in cond["units"].values()) + cond["external"]["Sn"]


def run_case(network, condition, dc_bus, event, opts):
    ss = rms.build(network, condition, dc_bus, event, opts)
    ss.TDS.config.criteria = 0  # judged below from voltage and frequency, not ANDES's angle criterion
    result = rms.simulate(ss)
    if result is None:
        return None
    t, f, code = result
    v = ss.dae.ts.y[:, ss.Bus.v.a[list(ss.Bus.idx.v).index(dc_bus)]]
    return t, f, v, code


def frequency_response(network) -> tuple[pd.DataFrame, dict]:
    spec = SENS["frequency_response"]
    condition, dc_bus = spec["condition"], spec["dc_bus"]
    low, high = RMS["conditions"][condition]["frequency_band_hz"]
    base_inertia = system_inertia_mws(condition)
    pfr_base = governed_mva(condition) / (RMS["governor"]["R"] * FN)
    rows, traces = [], {}
    for inertia in spec["inertia_mws"]:
        for t1 in spec["t1_s"]:
            for share in spec["response_share"]:
                for event in spec["events"]:
                    opts = {"h_scale": inertia / base_inertia, "response_share": share, "t1_s": t1}
                    out = run_case(network, condition, dc_bus, event, opts)
                    row = {"Event": rms.EVENT_LABELS[event], "InertiaMWs": inertia, "GovernorT1s": t1,
                           "ResponseShare": share, "EquivalentPfrMWperHz": share * pfr_base}
                    if out is None:
                        rows.append({**row, "Outcome": "Power flow did not converge"})
                        continue
                    t, f, v, code = out
                    after = t >= RMS["event_time_s"]
                    row.update({"FrequencyNadirHz": float(f[after].min()), "FrequencyZenithHz": float(f[after].max()),
                                "FinalFrequencyHz": float(f[-1]), "Completed": code == 0})
                    row["WithinIslandBand"] = bool(code == 0 and low <= row["FrequencyNadirHz"]
                                                   and row["FrequencyZenithHz"] <= high)
                    row["Outcome"] = "Passes" if row["WithinIslandBand"] else "Leaves island band"
                    rows.append(row)
                    traces[(event, inertia, t1, share)] = (t, f)
                    print(f"{event:12s} H={inertia:6.0f} T1={t1:.1f} share={share:.2f} "
                          f"f=[{row['FrequencyNadirHz']:.3f}, {row['FrequencyZenithHz']:.3f}] {row['Outcome']}",
                          flush=True)
    return pd.DataFrame(rows), traces


def thresholds(frame: pd.DataFrame, pfr_base: float) -> pd.DataFrame:
    """Smallest delivered response share (interpolated) that keeps each event in the band."""
    rows = []
    valid = frame.dropna(subset=["FrequencyZenithHz"])
    for (event, inertia, t1), group in valid.groupby(["Event", "InertiaMWs", "GovernorT1s"]):
        group = group.sort_values("ResponseShare")
        excursion = np.maximum(group["FrequencyZenithHz"] - 50.0, 50.0 - group["FrequencyNadirHz"]).to_numpy()
        share = group["ResponseShare"].to_numpy()
        if (excursion <= BAND_HALF_WIDTH_HZ).all():
            needed, note = float(share.min()), "passes at every tested share (value is the lowest tested)"
        elif (excursion > BAND_HALF_WIDTH_HZ).all():
            needed, note = float("nan"), "fails at every tested share"
        else:
            # excursion falls as the share rises: interpolate where it crosses the band edge
            order = np.argsort(excursion)
            needed = float(np.interp(BAND_HALF_WIDTH_HZ, excursion[order], share[order]))
            note = "interpolated"
        rows.append({"Event": event, "InertiaMWs": inertia, "GovernorT1s": t1, "MinimumResponseShare": needed,
                     "MinimumEquivalentPfrMWperHz": needed * pfr_base if np.isfinite(needed) else float("nan"),
                     "Note": note})
    return pd.DataFrame(rows)


def fault_ride_through(network) -> pd.DataFrame:
    spec = SENS["fault_ride_through"]
    t0 = RMS["event_time_s"]
    rows = []
    for condition in spec["conditions"]:
        low, high = RMS["conditions"][condition]["frequency_band_hz"]
        for dc_bus in spec["dc_buses"]:
            for fault_bus in [bus["bus_id"] for bus in network.buses]:
                base = {"fault_bus": fault_bus, "fault_clear_s": spec["fault_clear_s"], "fault_x_pu": spec["fault_x_pu"],
                        "load_model": "current"}
                out = run_case(network, condition, dc_bus, "fault", base)
                row = {"Condition": RMS["conditions"][condition]["label"], "DataCentreBus": dc_bus, "FaultBus": fault_bus}
                if out is None:
                    rows.append({**row, "Outcome": "Power flow did not converge"})
                    continue
                t, f, v, code = out
                during = (t >= t0) & (t <= t0 + spec["fault_clear_s"])
                vmin = float(v[during].min())
                trips = vmin < spec["trip_threshold_pu"]
                row.update({"MinDcBusVoltagePu": vmin, "DataCentreTrips": bool(trips),
                            "RideThroughZenithHz": float(f[t >= t0].max()),
                            "RideThroughNadirHz": float(f[t >= t0].min())})
                if trips:
                    out = run_case(network, condition, dc_bus, "fault", {**base, "dc_trip_s": t0 + spec["trip_delay_s"]})
                    t, f, v, code = out
                after = t >= t0
                row.update({"FrequencyZenithHz": float(f[after].max()), "FrequencyNadirHz": float(f[after].min()),
                            "FinalVoltagePu": float(v[-1]), "Completed": code == 0})
                recovered = code == 0 and abs(v[-1] - v[t < t0][-1]) < 0.05
                in_band = low <= row["FrequencyNadirHz"] and row["FrequencyZenithHz"] <= high
                row["Outcome"] = ("Did not recover" if not recovered else
                                  "Facility trips; frequency leaves band" if trips and not in_band else
                                  "Facility trips; frequency within band" if trips else
                                  "Rides through" if in_band else "Frequency leaves band")
                rows.append(row)
                print(f"{condition} DC@{dc_bus} fault@{fault_bus:4s} Vmin={vmin:.2f} {row['Outcome']} "
                      f"zenith={row['FrequencyZenithHz']:.3f}", flush=True)
    return pd.DataFrame(rows)


def plot(freq: pd.DataFrame, traces: dict, faults: pd.DataFrame) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(17, 5.2))
    loss = freq[freq["Event"] == rms.EVENT_LABELS["load_loss"]].dropna(subset=["FrequencyZenithHz"])
    styles = {0.5: "-", 2.0: "--", 6.0: ":"}
    for (inertia, t1), group in loss.groupby(["InertiaMWs", "GovernorT1s"]):
        group = group.sort_values("EquivalentPfrMWperHz")
        axes[0].plot(group["EquivalentPfrMWperHz"], group["FrequencyZenithHz"], styles.get(t1, "-."), marker="o",
                     label=f"{inertia:,.0f} MWs, governor T1 {t1:g} s")
    axes[0].axhline(50.0 + BAND_HALF_WIDTH_HZ, color="#B22222", linestyle="--", linewidth=1,
                    label="Island band limit 51.0 Hz")
    reduced = CONFIG["dynamic_screening"]["systems"]["QLD_islanded"]["min"]
    axes[0].axvline(reduced["pfr_mw_per_hz"], color="grey", linestyle=":", label="Reduced-order screen PFR")
    axes[0].set_xlabel("Equivalent primary frequency response (MW/Hz)")
    axes[0].set_ylabel("Peak frequency after 245 MW load loss (Hz)")
    axes[0].set_title("Islanded Queensland: response needed")
    axes[0].legend(fontsize=7)
    axes[0].grid(alpha=0.25)

    spec = SENS["frequency_response"]
    inertia = max(spec["inertia_mws"])
    for share in (1.0, 0.5, 0.3):
        key = ("load_loss", inertia, spec["t1_s"][0], share)
        if key in traces:
            t, f = traces[key]
            axes[1].plot(t, f, label=f"{share:.0%} of base governor response")
    axes[1].axhline(50.0 + BAND_HALF_WIDTH_HZ, color="#B22222", linestyle="--", linewidth=1)
    axes[1].set_title(f"Islanded, {inertia:,.0f} MWs: 245 MW load loss at Bouldercombe")
    axes[1].set_xlabel("Time (s)")
    axes[1].set_ylabel("Centre-of-inertia frequency (Hz)")
    axes[1].legend(fontsize=8)
    axes[1].grid(alpha=0.25)

    for (condition, dc_bus), group in faults.dropna(subset=["MinDcBusVoltagePu"]).groupby(["Condition", "DataCentreBus"]):
        axes[2].plot(group["FaultBus"], group["MinDcBusVoltagePu"], marker="o", linestyle="none",
                     label=f"DC at {dc_bus}, {condition.split('(')[0].strip().lower()}")
    axes[2].axhline(SENS["fault_ride_through"]["trip_threshold_pu"], color="#B22222", linestyle="--", linewidth=1,
                    label="Assumed facility trip threshold")
    axes[2].set_ylabel("Minimum data-centre bus voltage during fault (pu)")
    axes[2].set_xlabel("Faulted bus")
    axes[2].set_title(f"Fault ride-through exposure ({SENS['fault_ride_through']['fault_clear_s'] * 1000:.0f} ms clearing)")
    axes[2].tick_params(axis="x", rotation=60)
    axes[2].legend(fontsize=7)
    axes[2].grid(alpha=0.25)
    fig.suptitle("RMS sensitivity (ANDES, typical machine data): frequency response and fault ride-through")
    fig.tight_layout()
    save_figure(fig, RESULTS, "rms_sensitivity")


def main() -> None:
    network = rms.network_with_fit()
    condition = SENS["frequency_response"]["condition"]
    freq, traces = frequency_response(network)
    write_csv(RESULTS / "rms_frequency_response_sensitivity.csv", freq.to_dict("records"))
    limits = thresholds(freq, governed_mva(condition) / (RMS["governor"]["R"] * FN))
    write_csv(RESULTS / "rms_frequency_response_thresholds.csv", limits.to_dict("records"))
    print(limits.round(3).to_string(index=False))
    faults = fault_ride_through(network)
    write_csv(RESULTS / "rms_fault_ride_through.csv", faults.to_dict("records"))
    plot(freq, traces, faults)
    summary = faults.groupby(["Condition", "DataCentreBus"]).agg(
        FaultBuses=("FaultBus", "count"), TripCases=("DataCentreTrips", "sum"),
        WorstZenithHz=("FrequencyZenithHz", "max"))
    print(summary.to_string())


if __name__ == "__main__":
    main()
