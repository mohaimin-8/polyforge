"""Plan-cycle latency of the two solver configurations, side by side.

ENGINEERING microbenchmark, descriptive (no hypothesis, no gate): the
published planner (two fixed sweeps, unanchored) against the configuration
every post-audit campaign evaluates (anchored moves, sweeps to a fixed
point), timed in the same process on the same machine with the same seeded
payloads as `planner_scaling.py`, so the two numbers are comparable with each
other. Absolute times are machine-dependent; the ratio is the reading.

    python planner_timing_solvers.py     # writes PLANNER_TIMING_SOLVERS.md
"""

from __future__ import annotations

import platform
import sys
import time
from pathlib import Path
from statistics import median

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "planner"))

import stats  # noqa: E402  (research/analysis/stats.py)
from planner import PlannerCore  # noqa: E402
from planner_scaling import payload  # noqa: E402  (identical synthetic tenants)

OUT = stats.record_path("PLANNER_TIMING_SOLVERS.md")
TENANT_COUNTS = (8, 16, 32)
CALLS_PER_N = 20
SEED = 42
CONFIGS = (("two-sweep (published default)", {}),
           ("convergent (anchored moves, sweeps to a fixed point)",
            {"anchor_moves": True, "converge_sweeps": True}))


def time_config(kwargs: dict[str, bool], n: int) -> tuple[float, float]:
    rng = np.random.default_rng(SEED)
    core = PlannerCore(**kwargs)
    core.plan(payload(n, rng))  # warm-up: controller construction
    times = []
    for _ in range(CALLS_PER_N):
        body = payload(n, rng)
        t0 = time.perf_counter()
        core.plan(body)
        times.append(time.perf_counter() - t0)
    return median(times), sorted(times)[int(0.95 * (len(times) - 1))]


def main() -> int:
    rows = []
    for n in TENANT_COUNTS:
        for name, kwargs in CONFIGS:
            med, p95 = time_config(kwargs, n)
            rows.append((n, name, med, p95))
            print(f"n={n:3d} {name:52s} median {med * 1000:7.1f} ms  p95 {p95 * 1000:7.1f} ms", flush=True)
    lines = ["# Planner plan-cycle latency: two-sweep vs convergent solver", "",
             "Engineering microbenchmark, descriptive (see `planner_timing_solvers.py`). "
             f"In-process `PlannerCore.plan`, single CPU thread, {CALLS_PER_N} timed calls per "
             f"configuration and tenant count after one warm-up, the payloads of `planner_scaling.py` "
             f"(seed {SEED}, re-seeded per configuration so both see identical inputs). "
             f"Machine: {platform.processor() or platform.machine()}, Python {platform.python_version()}. "
             "Absolute times are machine-dependent.", "",
             "| tenants | solver | median / cycle | p95 / cycle |", "|---|---|---:|---:|"]
    for n, name, med, p95 in rows:
        lines.append(f"| {n} | {name} | {med * 1000:.1f} ms | {p95 * 1000:.1f} ms |")
    lines.append("")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
