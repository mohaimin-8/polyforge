"""W33/W35a reproducibility check: run the same experiment cells under two
disjoint repetition sets and require the per-metric distributions to be
statistically equivalent (two-sample Kolmogorov-Smirnov, p > 0.1).

This is the sim-backend analogue of "same experiment run twice produces
statistically equivalent results": rep groups A and B see different
Poisson jitter draws of the same scenario, so if the harness is sound the
two groups are samples from one distribution. A small p means seed
leakage, order dependence, or shared mutable state across runs.

Four metrics are tested jointly, so the p > 0.1 gate is applied at the
*family* level via Holm-Bonferroni — four naive per-metric tests at 0.1
false-alarm ~34% of the time on sound harnesses, which is exactly the
false positive the first smoke run produced (see eval/SMOKE_BUGS.md #3).

Usage (from eval/):  python scripts/ks_check.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from scipy import stats

EVAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL_DIR))

from harness import sim_backend  # noqa: E402
from harness.config import ExperimentSpec, expand  # noqa: E402

P_THRESHOLD = 0.1
METRICS = ("total_cost_usd", "mean_violation", "mean_jain", "cache_hit_rate")


def group(name: str, rep_offset: int) -> dict[str, list[float]]:
    # Two groups = two experiment names: run identity (and thus every
    # seed) differs while the scenario stays identical.
    spec = ExperimentSpec(
        name=name,
        steps=120,
        reps=12,
        systems=["hpa"],
        workloads=["ai_cacheable"],
        tenant_mixes=["whale"],
        cluster_sizes=["medium"],
        output="results/ks_check.duckdb",
    )
    values: dict[str, list[float]] = {m: [] for m in METRICS}
    for run in expand(spec):
        metrics = sim_backend.execute(run)["metrics"]
        for m in METRICS:
            values[m].append(metrics[m])
    del rep_offset
    return values


def main() -> None:
    print("running group A (12 reps)...")
    a = group("ks_group_a", 0)
    print("running group B (12 reps)...")
    b = group("ks_group_b", 100)

    tests = []
    for m in METRICS:
        stat, p = stats.ks_2samp(a[m], b[m])
        tests.append((p, m, stat))
        print(f"  {m:22s} KS={stat:.3f}  p={p:.3f}")

    # Holm-Bonferroni at familywise alpha = P_THRESHOLD: reject the most
    # significant test only if p <= alpha/m, the next only if <= alpha/(m-1), ...
    failures = []
    for i, (p, m, _) in enumerate(sorted(tests)):
        if p <= P_THRESHOLD / (len(tests) - i):
            failures.append((m, p))
        else:
            break

    if failures:
        print(f"reproducibility check FAILED (Holm at family alpha {P_THRESHOLD}): "
              f"{failures} — the harness is producing rep groups from different "
              "distributions")
        raise SystemExit(1)
    print(f"reproducibility: {len(METRICS)} metrics equivalent across independent "
          f"rep groups (Holm-corrected KS, familywise alpha {P_THRESHOLD})")


if __name__ == "__main__":
    main()
