"""Regenerate programme 2's reports from committed data and check them against
the committed text (the programme-2 counterpart of scripts/reproduce.py).

Each step rebuilds one report from data already in the repository (no
simulation is re-run unless noted), then `git diff` says whether the report
changed. A report that changes is drift: either the data, the code or the
committed text no longer agree. Steps that need files the repository does
not ship (downloaded datasets, third-party code) are skipped when the files
are absent, and say so.

On another platform the last digits of floating-point results can differ
(2026-11-17, GitHub's Linux runners against the Windows laptop that wrote the
records: at most 9e-9 relative, in plant 2's seeded bootstrap fits). A report
whose text is unchanged and whose numbers all agree within TOLERANCE is
reported as `numeric` and passes; any other change, and any binary file, is
drift. `--strict` asks for byte identity, the claim on the records' own
platform.

Two steps touch more than their report. WP5's specification curve rebuilds
full/curve_effects.csv.gz and full_spec_curve.png from WP9's runs, so off the
Linux runners that produced them their last digits and the image's metadata
can differ while the report stays identical (`git checkout -- <file>`
restores the record). WP5's Sobol rebuild reads its committed runs exactly
and, since 2026-11-20, no longer writes them back.

    python scripts/reproduce_programme2.py            # every step
    python scripts/reproduce_programme2.py --strict   # numbers byte for byte too
    python scripts/reproduce_programme2.py --list     # the steps, run nothing

It runs only in a git checkout of the repository: the comparison reads the committed text through
git, and before 2026-11-20 a run outside one (the deposit's repository/, a git archive) read every
report as identical. In the deposit, commit the shipped files once first: git init -q && git add -A
&& git -c user.name=deposit -c user.email=deposit@invalid commit -qm deposit
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

NUMBER = re.compile(rb"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
TOLERANCE = 1e-6   # relative; the cross-platform differences seen are 1e-16 to 9e-9
NOT_A_CHECKOUT = ("not a git checkout of this repository: every rebuilt report is compared with the committed "
                  "text through git, and without it each comparison would read as identical. In the deposit's "
                  "repository/ folder, commit the shipped files once first: git init -q && git add -A && "
                  "git -c user.name=deposit -c user.email=deposit@invalid commit -qm deposit")

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
    ("WP17 plant fit", R / "hvpa", ["calibration.py", "--curves", "calibration_curves", "--node-cpu-file",
                                    "calibration_curves/node_cpu.json", "--out", "calibration.json"],
     ["calibration.json"], ["calibration_curves/node_cpu.json"]),
    ("WP17 twin predictions (a simulation)", R / "hvpa", ["twin_predict.py", "--calibration", "calibration.json",
                                                          "--out", "predictions.json"],
     ["predictions.json"], ["calibration_curves/node_cpu.json", "predictions.json"]),
    ("WP17 tuning-grid edge (a light simulation)", R / "hvpa", ["grid_edge.py"], ["GRID_EDGE.md"],
     ["predictions.json"]),
    ("WP17 live pilot report", R / "hvpa", ["../../testbed/hvpa/pilot.py", "--report-only", "--predictions",
                                            "predictions.json", "--calibration", "calibration.json", "--out",
                                            "../../testbed/hvpa/pilot_runs", "--report", "PILOT_LIVE.md"],
     ["PILOT_LIVE.md"], ["PILOT_LIVE.md", "../../testbed/hvpa/pilot_runs"]),
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


def run(step, strict: bool = False) -> tuple[str, str]:
    name, cwd, cmd, reports, needs = step
    missing = [n for n in needs if not (cwd / n).exists()]
    if missing:
        return "skipped", f"needs {', '.join(missing)}"
    paths = [(cwd / r).relative_to(REPO).as_posix() for r in reports]
    tracked = subprocess.run(["git", "ls-files", "--", *paths], cwd=REPO, capture_output=True, text=True)
    if tracked.returncode != 0:
        return "failed", f"git ls-files: {first_line(tracked)}"
    untracked = sorted(set(paths) - set(tracked.stdout.splitlines()))
    if untracked:   # git diff ignores an untracked file: it would read as identical
        return "failed", f"not tracked by git, so nothing to compare with: {', '.join(untracked)}"
    t0 = time.time()
    proc = subprocess.run([sys.executable, *cmd], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        return "failed", proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else f"exit {proc.returncode}"
    stat = subprocess.run(["git", "diff", "--ignore-cr-at-eol", "--stat", "--", *paths], cwd=REPO,
                          capture_output=True, text=True)
    if stat.returncode != 0:   # an error prints nothing on stdout: never read that as identical
        return "failed", f"git diff: {first_line(stat)}"
    diff = stat.stdout.strip()
    if not diff:
        return "identical", f"{time.time() - t0:.0f} s"
    changed = subprocess.run(["git", "diff", "--ignore-cr-at-eol", "--name-only", "--", *paths], cwd=REPO,
                             capture_output=True, text=True).stdout.split()
    worst = [numbers_only(rel, REPO) for rel in changed]
    if not strict and changed and all(w is not None and w <= TOLERANCE for w in worst):
        return "numeric", f"numbers only, largest relative difference {max(worst):.1e} ({diff.splitlines()[-1]})"
    return "drift", diff.splitlines()[-1]


def first_line(proc: subprocess.CompletedProcess) -> str:
    text = (proc.stderr or "").strip()
    return text.splitlines()[0] if text else f"exit {proc.returncode}"


def in_checkout(repo: Path) -> bool:
    """Whether repo is the top of a git work tree (not a plain folder, nor one inside another repository)."""
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=repo, capture_output=True, text=True)
    if top.returncode != 0:
        return False
    return os.path.normcase(Path(top.stdout.strip()).resolve()) == os.path.normcase(repo.resolve())


def numbers_only(rel: str, repo: Path = REPO) -> float | None:
    """The largest relative difference between the committed and the rebuilt
    file when they differ in numbers only, else None (text or binary drift)."""
    old = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=repo, capture_output=True).stdout
    new = (repo / rel).read_bytes()
    if b"\0" in old or b"\0" in new:
        return None
    old, new = old.replace(b"\r\n", b"\n"), new.replace(b"\r\n", b"\n")
    if NUMBER.sub(b"#", old) != NUMBER.sub(b"#", new):
        return None
    worst = 0.0
    for a, b in zip(NUMBER.findall(old), NUMBER.findall(new)):
        if a != b:
            x, y = float(a), float(b)
            scale = max(abs(x), abs(y))
            worst = max(worst, abs(x - y) / scale if scale else 0.0)
    return worst


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--strict", action="store_true", help="numbers must match byte for byte too")
    args = ap.parse_args(argv)
    if args.list:
        for name, cwd, cmd, reports, _ in STEPS:
            print(f"{name}: (cd {cwd.relative_to(REPO)} && python {' '.join(cmd)}) -> {', '.join(reports)}")
        return 0
    if not in_checkout(REPO):
        print(NOT_A_CHECKOUT, file=sys.stderr)
        return 2
    bad = numeric = 0
    for step in STEPS:
        status, note = run(step, args.strict)
        bad += status in ("drift", "failed")
        numeric += status == "numeric"
        print(f"{status:9s} {step[0]}: {note}", flush=True)
    tail = f" ({numeric} step(s) differ in the last digits of numbers only, within {TOLERANCE:g})" if numeric else ""
    print(("programme 2 reproduces" if not bad else f"{bad} step(s) drifted or failed") + tail)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
