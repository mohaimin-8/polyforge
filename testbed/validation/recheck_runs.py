"""Re-run the load-spread check of finished WP11 runs after the check changed (programme 2).

2026-11-14: check_run.py flagged three of the first ten runs for a pod that
"served 0 % of the tokens": a pod being scaled down as the run started (its
series stays in the window) and, once, a tier that got one request on two
pods. check_run.py now judges live pods only and skips a tier with too little
work. This tool runs the fixed check over each finished run's own window
(Prometheus keeps the series) and rewrites run.json: the first verdict is kept
as problems_first_check, the new one replaces problems and valid. Only the
load-spread problem is re-judged; any other problem stands.

    python recheck_runs.py --runs ../out/validation
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTBED = HERE.parent
SPREAD = "load distribution over pods: see check.json"


def recheck(run_dir: Path, prometheus: str) -> dict | None:
    info = run_dir / "run.json"
    rec = json.loads(info.read_text(encoding="utf-8"))
    if SPREAD not in rec.get("problems", []):
        return None
    preds = json.loads((HERE / "predictions.json").read_text(encoding="utf-8"))
    slo = next(p["slo_ms"] for p in preds["predictions"] if p["cell"] == rec["cell"])
    proc = subprocess.run([sys.executable, str(TESTBED / "scripts" / "check_run.py"), "--k6-csv",
                           str(run_dir / "k6-samples.csv.gz"), "--slo-ms", str(slo), "--prometheus", prometheus,
                           "--start", str(rec["start_unix"]), "--end", str(rec["end_unix"]),
                           "--out", str(run_dir / "check.json")], capture_output=True, text=True)
    rest = [p for p in rec["problems"] if p != SPREAD]
    if proc.returncode == 1:
        rest.append(SPREAD)
    elif proc.returncode != 0:
        rest.append(f"recheck failed ({proc.returncode}): {proc.stderr.strip()[:200]}")
    rec.update(problems_first_check=rec["problems"], problems=rest, valid=not rest, check_exit=proc.returncode,
               rechecked_unix=time.time(), recheck_reason="check_run.py judges live pods only and skips tiers "
               "with too little work (2026-11-14)")
    info.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return rec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", type=Path, required=True)
    ap.add_argument("--prometheus", default="http://127.0.0.1:30090")
    args = ap.parse_args(argv)
    for run_dir in sorted(args.runs.glob("cell*/run*")):
        if (run_dir / "run.json").exists():
            rec = recheck(run_dir, args.prometheus)
            if rec is not None:
                print(f"{run_dir.parent.name}/{run_dir.name}: {'valid' if rec['valid'] else 'INVALID'} {rec['problems']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
