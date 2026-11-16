"""Hourly GPU-allocation profile from the public Alibaba PAI GPU trace (2020).

Source: Alibaba cluster-trace-gpu-v2020 (Weng et al., "MLaaS in the Wild:
Workload Analysis and Scheduling in Large-Scale Heterogeneous GPU Clusters",
NSDI 2022). The trace has no time-stamped GPU utilisation, so the profile is
the number of GPUs allocated to running tasks (pai_task_table: inst_num x
plan_gpu/100 between start_time and end_time) divided by installed GPUs
(pai_machine_spec). This is the real hour-to-hour variability of a large
production ML cluster (a mix of training and inference jobs), not of a single
synchronous training run.

Downloads pai_task_table and pai_machine_spec (~35 MB) into
inputs/traces/raw (git-ignored) and writes inputs/traces/alibaba_gpu_hourly.csv.
"""

from __future__ import annotations

import argparse
import tarfile
import urllib.request

import numpy as np
import pandas as pd

from studylib.config import INPUTS


BASE_URL = "https://aliopentrace.oss-cn-beijing.aliyuncs.com/v2020GPUTraces/"
RAW = INPUTS / "traces" / "raw"
OUT = INPUTS / "traces" / "alibaba_gpu_hourly.csv"
TASK_COLUMNS = ["job_name", "task_name", "inst_num", "status", "start_time", "end_time",
                "plan_cpu", "plan_mem", "plan_gpu", "gpu_type"]
SPEC_COLUMNS = ["machine", "gpu_type", "cap_cpu", "cap_mem", "cap_gpu"]
EDGE_DAYS = 7  # tasks running before the trace window are missing: drop the first and last week


def fetch(name: str) -> None:
    csv_path = RAW / f"{name}.csv"
    if csv_path.exists():
        return
    RAW.mkdir(parents=True, exist_ok=True)
    archive = RAW / f"{name}.tar.gz"
    request = urllib.request.Request(BASE_URL + f"{name}.tar.gz", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=600) as response:
        archive.write_bytes(response.read())
    with tarfile.open(archive) as tar:
        tar.extractall(RAW, filter="data") if hasattr(tarfile, "data_filter") else tar.extractall(RAW)


def hourly_allocation(tasks: pd.DataFrame) -> pd.Series:
    """Mean GPUs allocated in each hour (exact overlap of task intervals with hours)."""
    tasks = tasks.dropna(subset=["start_time", "end_time", "plan_gpu", "inst_num"])
    tasks = tasks[(tasks["plan_gpu"] > 0) & (tasks["end_time"] > tasks["start_time"])]
    gpus = (tasks["inst_num"] * tasks["plan_gpu"] / 100.0).to_numpy()
    start, end = tasks["start_time"].to_numpy(), tasks["end_time"].to_numpy()
    first_hour = int(start.min() // 3600)
    n = int(end.max() // 3600) - first_hour + 1
    # Integrate a piecewise-constant sum with a difference array on a 60 s grid.
    step = 60
    grid = np.zeros(n * 3600 // step + 2)
    s_idx = ((start - first_hour * 3600) // step).astype(int)
    e_idx = ((end - first_hour * 3600) // step).astype(int)
    np.add.at(grid, s_idx, gpus)
    np.add.at(grid, e_idx, -gpus)
    per_minute = np.cumsum(grid)[: n * 60]
    hourly = per_minute.reshape(n, 60).mean(axis=1)
    return pd.Series(hourly, index=np.arange(first_hour, first_hour + n), name="allocated_gpus")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if OUT.exists() and not args.force:
        print(OUT)
        return
    for name in ("pai_task_table", "pai_machine_spec"):
        fetch(name)
    tasks = pd.read_csv(RAW / "pai_task_table.csv", header=None, names=TASK_COLUMNS)
    spec = pd.read_csv(RAW / "pai_machine_spec.csv", header=None, names=SPEC_COLUMNS)
    capacity = float(spec["cap_gpu"].sum())
    hourly = hourly_allocation(tasks)
    hours_per_day = 24
    trimmed = hourly.iloc[EDGE_DAYS * hours_per_day: len(hourly) - EDGE_DAYS * hours_per_day]
    whole_days = len(trimmed) // hours_per_day * hours_per_day
    trimmed = trimmed.iloc[:whole_days]
    frame = pd.DataFrame({
        "trace_hour": trimmed.index - trimmed.index[0],
        "trace_day": (trimmed.index - trimmed.index[0]) // hours_per_day,
        "hour_of_day": (trimmed.index % hours_per_day),
        "allocated_gpus": trimmed.to_numpy(),
        "installed_gpus": capacity,
    })
    frame["allocation_fraction"] = frame["allocated_gpus"] / capacity
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(OUT, index=False)
    print(OUT)
    print(f"{len(frame)} hours ({len(frame) / 24:.0f} days); installed GPUs {capacity:.0f}; allocation "
          f"mean {frame['allocation_fraction'].mean():.2f}, P5 {frame['allocation_fraction'].quantile(0.05):.2f}, "
          f"P95 {frame['allocation_fraction'].quantile(0.95):.2f}")


if __name__ == "__main__":
    main()
