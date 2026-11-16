"""Open-source RMS dynamic study of the data-centre events (ANDES).

Builds an RMS model of the reduced Central Queensland 275 kV network (the
same buses and OpenStreetMap-derived lines as the multi-bus study, with the
fitted line multiplier) with synchronous machines (GENROU), governors (TGOV1)
and exciters (SEXS), plus an external equivalent for the rest of Queensland
(islanded) or the NEM (interconnected). The data centre is a constant-power
load at a candidate bus. For each operating condition and candidate bus it
runs the supervisor's event list:

* sudden full-load loss and pickup;
* a repetitive training-load swing (30% of IT, 2 s period);
* a trip of 200 MW on-site PV and of a 50 MW battery (import rises);
* a network outage (most-loaded adjacent 275 kV circuit) at full load.

and reports frequency nadir/zenith, RoCoF, connection-point voltage
excursion and recovery, and stability. Parameters are typical values; this
complements, and does not replace, PowerFactory/PSS/E studies with
network-owner models.
"""

from __future__ import annotations

import math

import andes
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from studylib import multibus as mb
from studylib.config import RESULTS, load_config, ner_minimum_pf
from studylib.io import save_figure, write_csv


CONFIG = load_config()
RMS = CONFIG["rms_study"]
MB = CONFIG["multibus"]
TARGET = CONFIG["connection"]["target_mw"]
BASE_MVA = 100.0
FN = 50.0
andes.config_logger(stream_level=40)


def network_with_fit():
    network = mb.load_network(MB["model_dir"], MB["source_xr"])
    mb.fit_sources(network)
    return network


def build(network, condition: str, dc_bus: str, event: str, opts: dict | None = None):
    """Build the ANDES case. ``opts`` (used by run_rms_sensitivity.py) may set:
    h_scale (multiplies every machine's H), response_share (aggregate governor
    response as a share of the base: droop R / share; 0 freezes all governors),
    t1_s (governor valve time constant), fault_bus / fault_clear_s (bolted
    three-phase bus fault at the event time), dc_trip_s (data centre trips at
    this time) and load_model ("power" or "current")."""
    opts = opts or {}
    cond = RMS["conditions"][condition]
    kv = network.kv
    zbase = kv**2 / BASE_MVA
    ss = andes.System()
    for bus in network.buses:
        ss.add("Bus", dict(idx=bus["bus_id"], name=bus["bus_id"], Vn=kv, v0=1.0))
    for branch in network.branches:
        z = network.branch_impedance(branch)
        b = RMS["line_charging_us_per_km"] * 1e-6 * float(branch["length_km"]) * zbase
        ss.add("Line", dict(idx=branch["branch_id"], bus1=branch["from_bus"], bus2=branch["to_bus"], Vn1=kv, Vn2=kv,
                            r=z.real / zbase, x=z.imag / zbase, b=b, fn=FN))

    machine, gov, exc = RMS["machine"], RMS["governor"], RMS["exciter"]
    h_scale = float(opts.get("h_scale", 1.0))
    share = float(opts.get("response_share", 1.0))
    t1 = float(opts.get("t1_s", gov["T1"]))

    def add_machine(idx, bus, sn, h, p_mw, slack=False, vmin=None):
        h = h * h_scale
        gen = dict(idx=idx, bus=bus, Vn=kv, Sn=sn, v0=1.03, p0=p_mw / BASE_MVA, pmax=sn / BASE_MVA, pmin=0,
                   qmax=0.6 * sn / BASE_MVA, qmin=-0.4 * sn / BASE_MVA)
        ss.add("Slack" if slack else "PV", gen)
        ss.add("GENROU", dict(idx=f"syn_{idx}", bus=bus, gen=idx, Sn=sn, Vn=kv, fn=FN, M=2 * h, D=machine["D"],
                              ra=machine["ra"], xl=machine["xl"], xd=machine["xd"], xq=machine["xq"], xd1=machine["xd1"],
                              xq1=machine["xq1"], xd2=machine["xd2"], xq2=machine["xq2"], Td10=machine["Td10"],
                              Tq10=machine["Tq10"], Td20=machine["Td20"], Tq20=machine["Tq20"]))
        if share <= 0:
            # Frozen governor: valve held at its initial position (no primary response).
            pu = p_mw / sn
            limits = dict(R=gov["R"], VMAX=pu, VMIN=pu)
        else:
            limits = dict(R=gov["R"] / share, VMAX=gov["VMAX"], VMIN=gov["VMIN"] if vmin is None else vmin)
        ss.add("TGOV1", dict(idx=f"gov_{idx}", syn=f"syn_{idx}", wref0=1.0, T1=t1, T2=gov["T2"], T3=gov["T3"], **limits))
        ss.add("SEXS", dict(idx=f"exc_{idx}", syn=f"syn_{idx}", **exc))

    for bus, (sn, p_mw) in cond["units"].items():
        add_machine(f"G_{bus}", bus, sn, machine["H_s"], p_mw)
    ext = cond["external"]
    # The external equivalent stands for a whole system that can import or export, so its
    # governor range is two-sided (Central Queensland exports south in the high-demand case).
    add_machine("G_EXT", ext["bus"], ext["Sn"], ext["H_s"], 0.0, slack=True, vmin=-1.0)

    q_ratio = RMS["background_q_ratio"]
    for bus, mw in RMS["background_load_mw"].items():
        p = mw * cond["load_scale"]
        ss.add("PQ", dict(idx=f"bg_{bus}", bus=bus, Vn=kv, p0=p / BASE_MVA, q0=q_ratio * p / BASE_MVA))

    pf = ner_minimum_pf(kv)
    tan_phi = math.tan(math.acos(pf))
    events = RMS["events"]
    t0 = RMS["event_time_s"]
    swing_mw = events["swing_fraction_of_it"] * CONFIG["facility"]["it_capacity_mw"] * CONFIG["facility"]["base_pue"]
    dc_on = event != "load_pickup"
    base_dc = TARGET - swing_mw if event == "training_swing" else TARGET
    ss.add("PQ", dict(idx="dc", bus=dc_bus, Vn=kv, p0=base_dc / BASE_MVA, q0=base_dc * tan_phi / BASE_MVA, u=1 if dc_on else 0))
    if event == "load_loss":
        ss.add("Toggle", dict(idx="ev", model="PQ", dev="dc", t=t0))
    elif event == "load_pickup":
        ss.add("Toggle", dict(idx="ev", model="PQ", dev="dc", t=t0))
    elif event == "training_swing":
        ss.add("PQ", dict(idx="swing", bus=dc_bus, Vn=kv, p0=swing_mw / BASE_MVA, q0=swing_mw * tan_phi / BASE_MVA, u=1))
        half = events["swing_period_s"] / 2.0
        for k in range(2 * int(events["swing_cycles"])):
            ss.add("Toggle", dict(idx=f"sw{k}", model="PQ", dev="swing", t=t0 + k * half))
    elif event in ("pv_trip", "bess_trip"):
        mw = events["pv_mw"] if event == "pv_trip" else events["bess_mw"]
        # On-site generation is a negative load at the connection point; tripping it raises grid import.
        ss.add("PQ", dict(idx="onsite", bus=dc_bus, Vn=kv, p0=-mw / BASE_MVA, q0=0.0))
        ss.add("Toggle", dict(idx="ev", model="PQ", dev="onsite", t=t0))
    elif event == "network_outage":
        ss.add("Toggle", dict(idx="ev", model="Line", dev=events["outage_line"][dc_bus], t=t0))
    elif event == "fault":
        ss.add("Fault", dict(idx="flt", bus=opts["fault_bus"], tf=t0, tc=t0 + float(opts["fault_clear_s"]),
                             xf=float(opts.get("fault_x_pu", 1e-4)), rf=0.0))
    else:
        raise ValueError(event)
    if opts.get("dc_trip_s") is not None:
        ss.add("Toggle", dict(idx="dctrip", model="PQ", dev="dc", t=float(opts["dc_trip_s"])))
    ss.setup()
    # Constant power for the standard events; constant current for faults, where
    # constant-power loads have no solution at near-zero voltage.
    model = opts.get("load_model", "power")
    for key in ("p2p", "q2q", "p2i", "q2i", "p2z", "q2z"):
        setattr(ss.PQ.config, key, 0.0)
    for key in (("p2p", "q2q") if model == "power" else ("p2i", "q2i")):
        setattr(ss.PQ.config, key, 1.0)
    return ss


def simulate(ss):
    ss.PFlow.run()
    if not ss.PFlow.converged:
        return None
    ss.TDS.config.tf = RMS["duration_s"]
    ss.TDS.config.no_tqdm = 1
    ss.TDS.run()
    t = np.asarray(ss.dae.ts.t)
    omegas = ss.dae.ts.x[:, ss.GENROU.omega.a]
    inertia = np.asarray(ss.GENROU.M.v) * np.asarray(ss.GENROU.Sn.v)
    f_coi = FN * (omegas @ inertia) / inertia.sum()
    return t, f_coi, ss.exit_code


def metrics(ss, t, f, dc_bus, band, exit_code):
    v = ss.dae.ts.y[:, ss.Bus.v.a[list(ss.Bus.idx.v).index(dc_bus)]]
    t0 = RMS["event_time_s"]
    pre = v[t < t0][-1]
    after = t >= t0
    window = after & (t <= t0 + 0.5)
    final = v[-1]
    deviation = np.abs(v - final) / final * 100
    outside = np.where(after & (deviation > RMS["voltage_recovery_band_percent"]))[0]
    recovery = float(t[outside[-1]] - t0) if len(outside) else 0.0
    low, high = band
    stable = exit_code == 0 and np.isfinite(v).all() and 0.5 < v.min() and v.max() < 1.5
    return {
        "PreEventVoltagePu": float(pre),
        "MinVoltagePu": float(v[after].min()),
        "MaxVoltagePu": float(v[after].max()),
        "FinalVoltagePu": float(final),
        "MaxVoltageStepPercent": float(100 * max(abs(v[after].min() - pre), abs(v[after].max() - pre)) / pre),
        "VoltageRecoveryTimeS": recovery,
        "FrequencyNadirHz": float(f[after].min()),
        "FrequencyZenithHz": float(f[after].max()),
        "RoCoF500msHzPerS": float((f[window][-1] - f[window][0]) / (t[window][-1] - t[window][0])),
        "FinalFrequencyHz": float(f[-1]),
        "WithinFrequencyBand": bool(low <= f[after].min() and f[after].max() <= high),
        "Stable": bool(stable),
    }


EVENT_LABELS = {
    "load_loss": "Sudden facility load loss",
    "load_pickup": "Sudden full load pickup",
    "training_swing": "Repetitive training swing (30% IT, 2 s)",
    "pv_trip": "On-site PV trip (200 MW)",
    "bess_trip": "BESS trip (50 MW)",
    "network_outage": "Adjacent 275 kV circuit outage",
}


def main() -> None:
    network = network_with_fit()
    rows, traces = [], {}
    for condition, cond in RMS["conditions"].items():
        for dc_bus in RMS["dc_buses"]:
            for event, label in EVENT_LABELS.items():
                ss = build(network, condition, dc_bus, event)
                result = simulate(ss)
                if result is None:
                    rows.append({"Condition": cond["label"], "DataCentreBus": dc_bus, "Event": label, "Stable": False,
                                 "Outcome": "Power flow did not converge"})
                    continue
                t, f, code = result
                m = metrics(ss, t, f, dc_bus, cond["frequency_band_hz"], code)
                outcome = ("Unstable / simulation failed" if not m["Stable"] else
                           "Leaves frequency band" if not m["WithinFrequencyBand"] else
                           f"Voltage step above {CONFIG['connection']['load_trip_step_limit_percent']:g}%"
                           if m["MaxVoltageStepPercent"] > CONFIG["connection"]["load_trip_step_limit_percent"] else "Passes")
                rows.append({"Condition": cond["label"], "DataCentreBus": dc_bus, "Event": label, **m, "Outcome": outcome})
                v = ss.dae.ts.y[:, ss.Bus.v.a[list(ss.Bus.idx.v).index(dc_bus)]]
                traces[(condition, dc_bus, event)] = (t, f, v)
                print(f"{condition:3s} {dc_bus} {label:42s} {outcome}", flush=True)
    write_csv(RESULTS / "rms_dynamic_events.csv", rows)

    fig, axes = plt.subplots(2, 2, figsize=(15, 8.5))
    colours = plt.get_cmap("tab10")
    for column, condition in enumerate(RMS["conditions"]):
        band = RMS["conditions"][condition]["frequency_band_hz"]
        for index, (event, label) in enumerate(EVENT_LABELS.items()):
            key = (condition, "BOUL", event)
            if key not in traces:
                continue
            t, f, v = traces[key]
            axes[0][column].plot(t, f, color=colours(index), label=label, linewidth=1.2)
            axes[1][column].plot(t, v, color=colours(index), label=label, linewidth=1.2)
        for value in band:
            axes[0][column].axhline(value, color="#B22222", linestyle="--", linewidth=1)
        axes[0][column].set_title(f"{RMS['conditions'][condition]['label']}: centre-of-inertia frequency")
        axes[0][column].set_ylabel("Frequency (Hz)")
        axes[1][column].set_title("Bouldercombe 275 kV voltage")
        axes[1][column].set_ylabel("Voltage (pu)")
        axes[1][column].set_xlabel("Time (s)")
        for ax in (axes[0][column], axes[1][column]):
            ax.grid(alpha=0.25)
    axes[0][0].legend(fontsize=7)
    fig.suptitle("Open-source RMS study (ANDES), data centre at Bouldercombe: typical machine parameters, not network-owner models")
    fig.tight_layout()
    save_figure(fig, RESULTS, "rms_dynamic_study")
    frame = pd.DataFrame(rows)
    print(frame[["Condition", "DataCentreBus", "Event", "FrequencyNadirHz", "FrequencyZenithHz", "MaxVoltageStepPercent",
                 "VoltageRecoveryTimeS", "Outcome"]].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
