"""PREREG_TRACE_PARITY.md — the campaign runner for both real traces.

Re-scores the two real-demand headlines (`RESULTS_TRACE2.md` −70.4%,
`RESULTS_TRACE_AZURE.md` −42.5%) against comparators that pay no 1.4581x
LRU charge and are deployed with a competently pre-sized 512 MB cache.

Every frozen protocol constant — windows, window construction, demand
scale, jitter seeds, tenants, cluster limits, tuned parameters, the
composite objective — is *called* out of the committed
`trace_matrix{,2,_azure}.py`, never reimplemented here. This file adds
exactly two things: the arm list of `PREREG_TRACE_PARITY.md` §Design, and
its own output paths.

**Nothing published is overwritten.** The committed
`trace_replay{,2,_azure}_runs.csv` and their records are read-only inputs
to the adjudication (R1); this campaign writes `trace_parity_*_runs.csv`.

    python trace_parity.py --trace burstgpt
    python trace_parity.py --trace azure
    python trace_parity.py --trace azure --smoke   # one job, timing probe
"""

from __future__ import annotations

import argparse
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

import trace_matrix as tm
import trace_matrix2 as tm2
import trace_matrix_azure as az

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT = REPO_ROOT / "eval" / "results"

# --- PREREG_TRACE_PARITY §Design (frozen) --------------------------------
# The five published arms UNCHANGED (they replicate, and they supply the two
# severity metrics the committed CSVs lack) plus the two fair comparators.
SYSTEMS_UNDER_TEST = [
    "jcac", "jcac_v2", "hpa", "keda", "firm", "hpa_fair", "keda_fair",
]

# PREREG_VIOLATION_PARITY §Design: the WP1 arms plus the frozen geometric
# beta ladder. Selected with `--campaign violation`, which writes its own
# CSVs — the WP1 outputs are never overwritten (R1).
VIOLATION_PARITY_ARMS = SYSTEMS_UNDER_TEST + [
    "jcac_b4", "jcac_b8", "jcac_b16", "jcac_b32", "jcac_b64",
]

TRACES = {
    "burstgpt": OUT / "trace_parity_burstgpt_runs.csv",
    "azure": OUT / "trace_parity_azure_runs.csv",
}

VIOLATION_TRACES = {
    "burstgpt": OUT / "violation_parity_burstgpt_runs.csv",
    "azure": OUT / "violation_parity_azure_runs.csv",
}

CAMPAIGNS = {
    "parity": (SYSTEMS_UNDER_TEST, TRACES),
    "violation": (VIOLATION_PARITY_ARMS, VIOLATION_TRACES),
}


def _burstgpt_jobs() -> tuple[list[tuple], dict]:
    """Jobs and pool kwargs for the PREREG_TRACE2 protocol (96 x 6 h)."""
    df = tm.load_events()
    segs = tm.segments_of(df)
    k = tm.scale_factor(df, segs)
    starts = tm2.window_starts(segs)
    print(f"burstgpt: {len(starts)} windows x {tm.WINDOW_H}h, k={k:.1f}, "
          f"seed base {tm2.SEED_BASE}")
    jobs = [(system, widx, start_s)
            for widx, start_s in enumerate(starts)
            for system in _ARMS]
    return jobs, {"initializer": tm2._init_worker}


def _azure_jobs() -> tuple[list[tuple], dict]:
    """Jobs and pool kwargs for the PREREG_TRACE_AZURE protocol (72 x 3 h)."""
    rates, k = az.load_rates()
    print(f"azure: {az.N_WINDOWS} windows x {az.WINDOW_H}h, k={k:.3f}, "
          f"seed base {az.SEED_BASE}")
    jobs = [(system, widx)
            for widx in range(az.N_WINDOWS)
            for system in _ARMS]
    return jobs, {"initializer": az._init_worker, "initargs": (rates,)}


_ARMS: list[str] = SYSTEMS_UNDER_TEST   # rebound by main() per --campaign

BUILDERS = {"burstgpt": _burstgpt_jobs, "azure": _azure_jobs}
RUNNERS = {"burstgpt": tm2._run_job, "azure": az._run_job}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trace", choices=sorted(TRACES), required=True)
    ap.add_argument("--campaign", choices=sorted(CAMPAIGNS), default="parity",
                    help="'parity' = WP1 arms; 'violation' = + the frozen beta ladder")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--smoke", action="store_true",
                    help="run one job per arm and print timings; writes nothing")
    args = ap.parse_args()

    global _ARMS
    _ARMS, out_map = CAMPAIGNS[args.campaign]
    jobs, pool_kwargs = BUILDERS[args.trace]()
    runner = RUNNERS[args.trace]
    out_csv = out_map[args.trace]

    if args.smoke:
        # Timing probe only — these rows are discarded and never scored.
        pool_kwargs["initializer"](*pool_kwargs.get("initargs", ()))
        for system in ("jcac", "hpa", "hpa_fair"):
            job = next(j for j in jobs if j[0] == system and j[1] == 0)
            t0 = time.perf_counter()
            row = runner(job)
            print(f"  smoke {system:10s} {time.perf_counter() - t0:6.1f}s "
                  f"steps={row['steps']} cost={row['total_cost_usd']:.2f} "
                  f"hit={row['cache_hit_rate']:.4f} (discarded)")
        return

    print(f"{len(jobs)} runs over {len(_ARMS)} arms ({args.campaign})")
    t0 = time.perf_counter()
    rows = []
    with ProcessPoolExecutor(max_workers=args.workers, **pool_kwargs) as pool:
        for i, row in enumerate(pool.map(runner, jobs), 1):
            rows.append(row)
            if i % 40 == 0 or i == len(jobs):
                el = time.perf_counter() - t0
                print(f"  [{i}/{len(jobs)}] {el / 60:.1f} min elapsed, "
                      f"eta {el / i * (len(jobs) - i) / 60:.1f} min", flush=True)
    runs = pd.DataFrame(rows).sort_values(["system", "window"])
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    runs.to_csv(out_csv, index=False)
    print(f"wrote {out_csv} ({len(runs)} runs)")


if __name__ == "__main__":
    main()
