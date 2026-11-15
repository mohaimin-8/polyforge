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
T = REPO / "testbed"

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
    ("WP7 self-hosted quality", R / "tierquality", ["selfhosted.py", "report"],
     ["SELFHOSTED.md", "selfhosted_summary.json"], ["data/selfhosted_answers.csv", "data/arc_challenge_test.parquet"]),
    ("WP17 twin pilot report", R / "hvpa", ["twin_pilot.py", "--report-only"], ["TWIN_PILOT.md"], []),
    ("WP11 predictions", T / "validation", ["predict.py", "--calibration", "calibration"], ["predictions.json"],
     ["../../research/cachereplay/data/vectors/gateway/train-00000-of-00006-4feeb3f83346a0e9.f32.npy"]),
    ("WP11 score", T / "validation", ["score.py", "--runs", "runs"], ["VALIDATION.md"], ["runs/actuator.jsonl"]),
    ("WP11 error breakdown", T / "validation", ["diagnose.py", "--runs", "runs"], ["DIAGNOSE.md"],
     ["runs/actuator.jsonl"]),
    ("WP11 host coupling (exploratory)", T / "validation", ["coupling.py", "--runs", "runs"], ["COUPLING.md"],
     ["runs/actuator.jsonl"]),
    ("WP11b predictions (fitted replica scaling)", T / "validation",
     ["predict.py", "--calibration", "calibration", "--cells", "cells_v2.json", "--inputs", "inputs_v2",
      "--replica-scaling", "fitted", "--out", "predictions_v2.json"], ["predictions_v2.json"],
     ["../../research/cachereplay/data/vectors/gateway/train-00000-of-00006-4feeb3f83346a0e9.f32.npy"]),
    ("WP11b score", T / "validation", ["score.py", "--runs", "runs_v2", "--cells", "cells_v2.json", "--predictions",
                                       "predictions_v2.json", "--report", "VALIDATION_V2.md", "--plot",
                                       "validation_v2_bland_altman.png"], ["VALIDATION_V2.md"],
     ["runs_v2/actuator.jsonl"]),
    ("WP11b error breakdown", T / "validation", ["diagnose.py", "--runs", "runs_v2", "--cells", "cells_v2.json",
                                                 "--predictions", "predictions_v2.json", "--out", "DIAGNOSE_V2.md"],
     ["DIAGNOSE_V2.md"], ["runs_v2/actuator.jsonl"]),
    ("WP5 Morris screen, r = 20", R / "sensitivity", ["morris_pilot.py", "--trajectories", "20", "--crn",
                                                     "--report-only"], ["MORRIS_r20_crn.md"],
     ["data/morris_runs_r20_crn.csv.gz"]),
    ("WP5 Sobol indices", R / "sensitivity", ["sobol.py", "--report-only"], ["SOBOL.md"], ["data/sobol_runs.csv.gz"]),
    ("WP5 specification curve, arms re-selected", R / "speccurve", ["full_curve.py", "curve"],
     ["FULL_SPEC_CURVE.md"], ["full/curve_effects.csv.gz"]),
    ("WP12 regimes (a light simulation)", T / "wp12", ["plans.py", "regimes", "--calibration",
                                                        "../validation/calibration", "--scaling", "recorded"],
     ["regimes.json"], ["regimes.json"]),
    ("WP12 tuning selection", T / "wp12", ["plans.py", "tune", "--calibration", "../validation/calibration",
                                           "--scaling", "recorded", "--workers", "1"],
     ["tuning/tuned.json"], ["tuning/journal.csv"]),
    ("WP12 plans (a light simulation)", T / "wp12", ["plans.py", "plans", "--calibration",
                                                      "../validation/calibration", "--scaling", "recorded"],
     ["plans.json"], ["plans.json"]),
    ("WP12 pilot report", T / "wp12", ["after_wp11b.py", "--report-only"], ["PILOT.md", "pilot.json"], ["runs"]),
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
