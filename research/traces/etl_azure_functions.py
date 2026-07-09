"""ETL: Azure Functions 2019 invocation trace -> normalized trace_event.

Real input (https://github.com/Azure/AzurePublicDataset, Microsoft Azure
Functions Trace 2019): per-function CSVs where each row is
(HashOwner, HashApp, HashFunction, Trigger, 1..1440) — invocation counts
per minute of one day. Owners map to tenants; triggers map to request
kinds; per-minute counts expand into events spread uniformly inside the
minute (the trace's resolution floor — documented limitation).

Usage:
    python etl_azure_functions.py --input invocations_per_function_md.anon.d01.csv --out azure_d01
    python etl_azure_functions.py --synthetic --seed 42 --out azure_synth

--synthetic emits raw rows in the *same format the real CSV uses* and
pushes them through the identical normalize() path, so the transform code
is exercised end to end before the multi-GB download ever happens. The
synthetic generator reproduces the trace's headline property (Shahrad et
al., ATC 2020): heavy-tailed per-function invocation rates — most
functions are idle most minutes while a small fraction dominates traffic.
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

import common

TRIGGER_KIND = {
    "http": "crud_read",
    "queue": "crud_write",
    "timer": "batch",
    "event": "batch",
    "storage": "crud_write",
    "orchestration": "agent",
    "others": "crud_read",
}

# The invocation trace carries no payload sizes or durations; both are
# sampled from documented distributions (duration percentiles are published
# in the companion durations CSV; payloads follow the lognormal shape used
# for HTTP bodies throughout the platform benchmarks). Seeded, so the
# normalized trace is reproducible.
PAYLOAD_LOGNORM_MEAN, PAYLOAD_LOGNORM_SIGMA = 6.0, 1.2  # median ~400B
LATENCY_LOGNORM_MEAN, LATENCY_LOGNORM_SIGMA = 3.4, 0.9  # median ~30ms


def synthetic_raw(seed: int, functions: int = 200, minutes: int = 60) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    owners = [f"owner{h:02d}" for h in range(12)]
    rows = []
    triggers = list(TRIGGER_KIND)
    # Pareto-distributed base rate: a few hot functions, a long idle tail.
    base = rng.pareto(1.5, functions) + 0.05
    for i in range(functions):
        counts = rng.poisson(base[i], minutes)
        row = {
            "HashOwner": owners[int(rng.integers(len(owners)))],
            "HashApp": f"app{i % 40:02d}",
            "HashFunction": f"fn{i:04d}",
            "Trigger": triggers[int(rng.integers(len(triggers)))],
        }
        row.update({str(m + 1): int(counts[m]) for m in range(minutes)})
        rows.append(row)
    return pd.DataFrame(rows)


def normalize(raw: pd.DataFrame, seed: int, base_ms: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    minute_cols = [c for c in raw.columns if str(c).isdigit()]
    counts = raw[minute_cols].fillna(0).to_numpy(dtype="int64")
    tenants = raw["HashOwner"].to_numpy()
    kinds = (
        raw["Trigger"].astype(str).str.lower().map(lambda t: TRIGGER_KIND.get(t, "crud_read")).to_numpy()
    )
    events = []
    for i in range(len(raw)):
        for j, col in enumerate(minute_cols):
            count = int(counts[i, j])
            if count <= 0:
                continue
            offsets = np.sort(rng.uniform(0, 60_000, count))
            start = base_ms + (int(col) - 1) * 60_000
            for off in offsets:
                events.append((int(start + off), tenants[i], kinds[i]))
    frame = pd.DataFrame(events, columns=["timestamp_ms", "tenant_id", "request_kind"])
    n = len(frame)
    frame["payload_bytes"] = rng.lognormal(PAYLOAD_LOGNORM_MEAN, PAYLOAD_LOGNORM_SIGMA, n).astype("int64").clip(32, 512_000)
    frame["expected_latency_ms"] = rng.lognormal(LATENCY_LOGNORM_MEAN, LATENCY_LOGNORM_SIGMA, n).round(3).clip(0.5, 120_000)
    return common.validate(frame)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", help="real invocations_per_function CSV")
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", required=True, help="output stem (no extension)")
    args = parser.parse_args()

    if args.synthetic:
        raw = synthetic_raw(args.seed)
    elif args.input:
        raw = pd.read_csv(args.input)
    else:
        parser.error("provide --input or --synthetic")

    frame = normalize(raw, args.seed)
    path = common.write(frame, args.out)
    print(f"{path}: {len(frame)} events, {frame['tenant_id'].nunique()} tenants, hash {common.stream_hash(frame)[:16]}")


if __name__ == "__main__":
    main()
