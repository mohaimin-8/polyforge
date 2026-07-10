"""W35c result-database validation: the checks that keep papers from
being retracted. Exits non-zero unless every check is green.

Usage (from eval/):
    python scripts/validate_results.py experiments/full.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL_DIR))

from harness import results  # noqa: E402
from harness.config import expand, load  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("experiment", help="experiment YAML the DB was produced by")
    args = ap.parse_args()

    spec = load(args.experiment)
    expected = len(expand(spec))
    con = results.connect((EVAL_DIR.parent / spec.output).resolve())
    report = results.validate(con, spec.name, expected)

    # Beyond the structural checks: every expected run_id must be present —
    # a DB with 1,800 valid rows of which 3 are stale duplicates from an
    # old schema would pass a bare count.
    expected_ids = {r.run_id for r in expand(spec)}
    present = results.valid_run_ids(con, spec.name)
    report["missing_run_ids"] = sorted(expected_ids - present)[:10]
    report["unexpected_run_ids"] = sorted(present - expected_ids)[:10]
    report["ok"] = report["ok"] and not report["missing_run_ids"] and not report["unexpected_run_ids"]

    print(json.dumps(report, indent=2))
    if not report["ok"]:
        raise SystemExit(1)
    print("validation: all green")


if __name__ == "__main__":
    main()
