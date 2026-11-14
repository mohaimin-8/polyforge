"""The 20 validation cells of WP11, fixed before any testbed run.

Plan, WP11: "Choose 20 cells by a Latin-hypercube over demand level,
burstiness, pool sizes, cache size and tier mix. None may have been used for
fitting. For each cell, write down the simulator's prediction of mean and p95
latency, attainment, throughput and cost, and commit it. Run the cell on the
testbed three times. Report per metric: median absolute percentage error,
rank correlation, and a Bland-Altman plot. Targets fixed in advance: rank
correlation >= 0.8 and median error <= 25% on attainment and cost."

Factors (testbed limits from testbed/actuator/bounds.json: small pods 1-3 at
1 core, mid pods 1-2 at 2 cores, a 5-core budget):
  demand        target utilisation of the small tier, 0.30-0.90 (continuous)
  tier mix      share of requests sent to the small tier, 0.50-1.00 (continuous)
  burstiness    source stream: a Poisson-like conversation stream, a smooth busy
                stream, a bursty API stream (categorical, 3 levels)
  pools         (small, mid) replicas within the core budget (categorical, 4 levels)
  cache         gateway semantic cache: off, small, large (categorical, 3 levels;
                mapped to the gateway's capacity setting when the testbed is up)

Continuous factors: one value per stratum of 20 equal strata, strata
permuted independently (a Latin hypercube). Categorical factors: each level
repeated as evenly as 20 allows, in an independent random order. Seeded.
The cells' streams are drawn from the population's EVALUATION half's
windows, never used to fit any plant constant.

    python cells.py      # writes cells.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SEED = 20261114
N = 20
CONTINUOUS = {"target_util": (0.30, 0.90), "small_share": (0.50, 1.00)}
CATEGORICAL = {
    "stream": ["burstgpt/ChatGPT/conv", "azure2024/conv", "burstgpt/ChatGPT/api"],
    "pools": [[1, 1], [2, 1], [3, 1], [1, 2]],
    "cache": ["off", "small", "large"],
}
TARGETS = {"rank_correlation_min": 0.8, "median_abs_pct_error_max": 0.25,
           "metrics_with_targets": ["attainment", "cost"],
           "metrics_reported": ["mean_latency", "p95_latency", "attainment", "throughput", "cost"]}


def design(seed: int = SEED, n: int = N) -> list[dict]:
    rng = np.random.default_rng(seed)
    cells = [{"cell": i} for i in range(n)]
    for name, (lo, hi) in CONTINUOUS.items():
        u = (rng.permutation(n) + rng.random(n)) / n
        for c, v in zip(cells, lo + u * (hi - lo)):
            c[name] = round(float(v), 4)
    for name, levels in CATEGORICAL.items():
        reps = [levels[i % len(levels)] for i in range(n)]
        for c, j in zip(cells, rng.permutation(n)):
            c[name] = reps[j]
    for c in cells:
        c["cores"] = c["pools"][0] * 1 + c["pools"][1] * 2
    return cells


def main() -> int:
    cells = design()
    if any(c["cores"] > 5 for c in cells):
        raise SystemExit("a cell exceeds the 5-core budget")
    out = {"protocol": "plan WP11; testbed/validation/cells.py", "seed": SEED, "targets": TARGETS,
           "runs_per_cell": 3, "predictions": "written and committed after the in-cluster tier measurements, "
           "before any cell runs", "cells": cells}
    (HERE / "cells.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    for c in cells:
        print(c)
    return 0


if __name__ == "__main__":
    sys.exit(main())
