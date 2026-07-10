"""W35c spot-check: replay N random valid runs and compare every metric to
the stored row. Tolerance is ±3% (roadmap contract); the sim backend is
deterministic so any drift at all means the harness leaks state between
runs — dig in, don't shrug.

Usage (from eval/):
    python scripts/spot_check.py experiments/full.yaml --n 10 --seed 2026
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL_DIR))

from harness import results, sim_backend  # noqa: E402
from harness.config import expand, load  # noqa: E402
from harness.results import METRIC_COLUMNS  # noqa: E402

TOLERANCE = 0.03


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("experiment")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--seed", type=int, default=2026, help="which runs get sampled")
    args = ap.parse_args()

    spec = load(args.experiment)
    if spec.backend != "sim":
        raise SystemExit("spot_check replays on the sim backend only")
    con = results.connect((EVAL_DIR.parent / spec.output).resolve())

    by_id = {r.run_id: r for r in expand(spec)}
    valid = sorted(results.valid_run_ids(con, spec.name))
    if not valid:
        raise SystemExit("no valid runs to check")
    sample = random.Random(args.seed).sample(valid, min(args.n, len(valid)))

    worst = 0.0
    failures = []
    for run_id in sample:
        run = by_id[run_id]
        stored = dict(
            zip(METRIC_COLUMNS, con.execute(
                f"SELECT {', '.join(METRIC_COLUMNS)} FROM metrics WHERE run_id = ?",
                [run_id],
            ).fetchone())
        )
        replayed = sim_backend.execute(run)["metrics"]
        for name in METRIC_COLUMNS:
            a, b = stored[name], replayed[name]
            denom = max(abs(a), abs(b), 1e-9)
            drift = abs(a - b) / denom
            worst = max(worst, drift)
            if drift > TOLERANCE:
                failures.append(f"{run_id} {name}: stored={a} replayed={b} drift={drift:.4f}")
        print(f"  {run_id} {run.system:16s} {run.workload:16s} ok (max drift so far {worst:.2e})")

    if failures:
        print("SPOT-CHECK FAILURES (harness is non-deterministic):")
        for f in failures:
            print("  " + f)
        raise SystemExit(1)
    print(f"spot-check: {len(sample)} runs replayed, worst drift {worst:.2e} (tolerance {TOLERANCE})")


if __name__ == "__main__":
    main()
