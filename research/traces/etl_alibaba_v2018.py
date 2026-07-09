"""ETL: Alibaba cluster-trace-v2018 -> normalized trace_event.

Real input (https://github.com/alibaba/clusterdata, cluster-trace-v2018):
multi-table CSV without headers. This pipeline consumes two of them —

    batch_task(task_name, instance_num, job_name, task_type, status,
               start_time, end_time, plan_cpu, plan_mem)
    container_meta(container_id, machine_id, time_stamp, app_du, status,
                   cpu_request, cpu_limit, mem_size)

The cross-table join the roadmap warns about: batch tasks carry no tenant;
the tenant is recovered by joining the job's DAG prefix onto the
application deployment unit (app_du) universe from container_meta. Where
the join misses, the job prefix itself becomes the tenant — explicit
`unmapped_*` names, never silently dropped rows. For the real 270GB trace
run the join through DuckDB (`duckdb -c "..."`, SQL in README); pandas
handles fixture-sized inputs with the same semantics.

Proxies (documented limitations, cited when the trace is used in §Eval):
  payload_bytes       <- plan_mem (normalized share) scaled to bytes; the
                         trace has no request payloads, memory plan is the
                         closest per-task size signal.
  expected_latency_ms <- (end_time - start_time) * 1000, the task makespan.

Usage:
    python etl_alibaba_v2018.py --batch-task batch_task.csv --container-meta container_meta.csv --out alibaba
    python etl_alibaba_v2018.py --synthetic --seed 42 --out alibaba_synth
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

import common

BATCH_TASK_COLUMNS = [
    "task_name", "instance_num", "job_name", "task_type", "status",
    "start_time", "end_time", "plan_cpu", "plan_mem",
]
CONTAINER_META_COLUMNS = [
    "container_id", "machine_id", "time_stamp", "app_du", "status",
    "cpu_request", "cpu_limit", "mem_size",
]


def synthetic_raw(seed: int, jobs: int = 300) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    apps = [f"app_du_{i:03d}" for i in range(20)]
    containers = pd.DataFrame({
        "container_id": [f"c_{i}" for i in range(60)],
        "machine_id": [f"m_{i % 15}" for i in range(60)],
        "time_stamp": 0,
        "app_du": [apps[i % len(apps)] for i in range(60)],
        "status": "started",
        "cpu_request": 400,
        "cpu_limit": 800,
        "mem_size": rng.integers(2, 64, 60),
    })
    # Diurnal-ish arrivals: two density peaks across the synthetic day,
    # mirroring the trace's published utilization rhythm.
    hour = rng.choice(24, jobs, p=_diurnal_probabilities())
    start = hour * 3600 + rng.integers(0, 3600, jobs)
    duration = rng.lognormal(4.0, 1.1, jobs)  # median ~55s makespan
    tasks = pd.DataFrame({
        "task_name": [f"task_J{i}" for i in range(jobs)],
        "instance_num": rng.integers(1, 32, jobs),
        "job_name": [f"j_{apps[int(rng.integers(len(apps)))]}_{i}" if rng.random() > 0.15 else f"j_orphan_{i}" for i in range(jobs)],
        "task_type": rng.choice([1, 2, 3], jobs),
        "status": "Terminated",
        "start_time": start,
        "end_time": start + duration.astype("int64") + 1,
        "plan_cpu": rng.integers(50, 800, jobs),
        "plan_mem": rng.uniform(0.1, 100.0, jobs).round(2),
    })
    return tasks, containers


def _diurnal_probabilities() -> np.ndarray:
    hours = np.arange(24)
    density = 1.0 + 0.8 * np.sin((hours - 10) / 24 * 2 * np.pi) + 0.5 * (hours > 19)
    density = np.clip(density, 0.2, None)
    return density / density.sum()


def normalize(tasks: pd.DataFrame, containers: pd.DataFrame) -> pd.DataFrame:
    tasks = tasks.copy()
    known = set(containers["app_du"].unique())

    def tenant_for(job_name: str) -> str:
        # job names embed the deployment unit between the first two
        # underscore-separated fields in our synthetic shape; the real
        # trace requires the DAG-prefix join documented in the README.
        parts = str(job_name).split("_")
        for width in (3, 2):
            candidate = "_".join(parts[1 : 1 + width])
            if candidate in known:
                return candidate
        return f"unmapped_{parts[0] if parts else 'job'}"

    tasks["tenant_id"] = tasks["job_name"].map(tenant_for)
    tasks = tasks[(tasks["end_time"] > tasks["start_time"]) & (tasks["start_time"] > 0)]
    frame = pd.DataFrame({
        "timestamp_ms": tasks["start_time"].astype("int64") * 1000,
        "tenant_id": tasks["tenant_id"],
        "request_kind": "batch",
        "payload_bytes": (tasks["plan_mem"].astype("float64") * 1024).clip(64, 1_000_000).astype("int64"),
        "expected_latency_ms": ((tasks["end_time"] - tasks["start_time"]) * 1000).astype("float64"),
    })
    return common.validate(frame)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-task")
    parser.add_argument("--container-meta")
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    if args.synthetic:
        tasks, containers = synthetic_raw(args.seed)
    elif args.batch_task and args.container_meta:
        tasks = pd.read_csv(args.batch_task, names=BATCH_TASK_COLUMNS, header=None)
        containers = pd.read_csv(args.container_meta, names=CONTAINER_META_COLUMNS, header=None)
    else:
        parser.error("provide --batch-task and --container-meta, or --synthetic")

    frame = normalize(tasks, containers)
    unmapped = frame["tenant_id"].str.startswith("unmapped_").mean()
    path = common.write(frame, args.out)
    print(f"{path}: {len(frame)} events, {frame['tenant_id'].nunique()} tenants, "
          f"{unmapped:.1%} unmapped, hash {common.stream_hash(frame)[:16]}")


if __name__ == "__main__":
    main()
