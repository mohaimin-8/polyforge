"""Regenerate programme 2's reports from committed data and check them against
the committed text (the programme-2 counterpart of scripts/reproduce.py).

Each step rebuilds one report from data already in the repository (no
simulation is re-run unless noted), then `git diff` says whether the report
changed. A report that changes is drift: either the data, the code or the
committed text no longer agree. Steps that need files the repository does
not ship (downloaded datasets, third-party code) are skipped when the files
are absent, and say so.

    python scripts/reproduce_programme2.py            # every step
    python scripts/reproduce_programme2.py --list     # the steps, run nothing
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
R = REPO / "research"

# (name, working directory, command, reports checked, files required beyond the repository)
STEPS = [
    ("plant 2 constants", R / "plant2", ["plant.py"], ["CONSTANTS.md"], []),
    ("plant 2 fits (seeded bootstrap)", R / "plant2", ["fits.py"], ["FITS.md", "data/fits.json"], []),
    ("theory property checks", R / "theory", ["property_check.py", "report"], ["PROPERTY_CHECKS.md"], []),
    ("population report", R / "population", ["population.py"], ["POPULATION.md", "data/population.json"], []),
    ("Morris screen report", R / "sensitivity", ["morris_pilot.py", "--trajectories", "10", "--report-only"],
     ["MORRIS_PILOT.md"], []),
    ("gate reach report", R / "controls", ["gate_reach.py", "--report-only"], ["GATE_REACH.md"], []),
    ("random-half check", R / "controls", ["random_sample_check.py"], ["RANDOM_SAMPLE_CHECK.md"], []),
    ("WP9 sample sizes", R / "comparators", ["sample_size.py"], ["SAMPLE_SIZE.md"], []),
    ("specification curve pilot", R / "speccurve", ["pilot_curve.py"], ["SPEC_CURVE_PILOT.md"], []),
    ("RouterBench tiers", R / "tierquality", ["tiers.py"], ["TIERS.md", "tier_summary.json"],
     ["data/routerbench_0shot.pkl", "data/routerbench_5shot.pkl"]),
    ("RouteBalance results", R / "external" / "routebalance", ["-I", "analyse.py", "report"], ["RESULTS.md"],
     ["upstream/route-balance-ac49feb0e46662223ed6cb0b638bf2bbe705deb2/data/labels/qwen_eval.jsonl"]),
]


def run(step) -> tuple[str, str]:
    name, cwd, cmd, reports, needs = step
    missing = [n for n in needs if not (cwd / n).exists()]
    if missing:
        return "skipped", f"needs {', '.join(missing)}"
    t0 = time.time()
    proc = subprocess.run([sys.executable, *cmd], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        return "failed", proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else f"exit {proc.returncode}"
    paths = [str((cwd / r).relative_to(REPO)) for r in reports]
    diff = subprocess.run(["git", "diff", "--ignore-cr-at-eol", "--stat", "--", *paths], cwd=REPO,
                          capture_output=True, text=True).stdout.strip()
    return ("drift", diff.splitlines()[-1]) if diff else ("identical", f"{time.time() - t0:.0f} s")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    if args.list:
        for name, cwd, cmd, reports, _ in STEPS:
            print(f"{name}: (cd {cwd.relative_to(REPO)} && python {' '.join(cmd)}) -> {', '.join(reports)}")
        return 0
    bad = 0
    for step in STEPS:
        status, note = run(step)
        bad += status in ("drift", "failed")
        print(f"{status:9s} {step[0]}: {note}", flush=True)
    print("programme 2 reproduces" if not bad else f"{bad} step(s) drifted or failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
