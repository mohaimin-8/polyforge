"""WP12 end to end, started by the post-WP11 chain on the cluster WP11 left (programme 2).

testbed/hvpa/after_wp11.py runs this after WP11 (and WP11b, when it ran) and
before WP17 takes the node. Steps, each logged to out/wp12/after_wp11b.log;
any failure stops WP12 (exit 1) and the chain goes on with WP17:
  1. form    the planners' replica scaling, by the rule fixed before WP11 was
             scored: linear when WP11's primary met its attainment target,
             otherwise WP11b's fitted scaling (the plan's refit). The first
             half of gate G3 (the simulator validated) is read from
             VALIDATION.md, else VALIDATION_V2.md, and recorded;
  2. design  plans.py regimes (the low- and high-index load levels), then
             plans.py design: schedules with prompts in out/wp12/schedules
             (never committed); regimes.json, design.json and the request
             tables committed;
  3. tune    plans.py tune (CPU only, nothing else running), committed;
  4. plans   plans.py plans, committed BEFORE any live run;
  5. live    pairs.py, the ABBA pairs (about 5 hours);
  6. keep    run records without prompt text into wp12/runs; PILOT.md with the
             G3 section, pilot.json and the runs committed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTBED = HERE.parent
REPO = TESTBED.parent
OUT = TESTBED / "out" / "wp12"
LOG = OUT / "after_wp11b.log"
CALIBRATION = TESTBED / "validation" / "calibration"
VALIDATION = TESTBED / "validation" / "VALIDATION.md"
VALIDATION_V2 = TESTBED / "validation" / "VALIDATION_V2.md"
RUN_FILES = ("run.json", "k6-summary.json", "actuator.jsonl", "k6-samples.csv.gz")
TUNE_WORKERS = 4
G3_MAX_PAIRS = 40

sys.path.append(str(TESTBED / "hvpa"))  # appended: testbed/hvpa has its own arms.py
from after_wp11 import wp11_primary_met  # noqa: E402


def log(msg: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


def step(cmd: list[str], cwd: Path = HERE) -> None:
    log("$ " + " ".join(cmd))
    with LOG.open("a", encoding="utf-8") as fh:
        rc = subprocess.call(cmd, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT)
    if rc != 0:
        log(f"exit {rc}: WP12 stops here")
        raise SystemExit(1)


def commit(paths: list[str], message: str) -> None:
    present = [p for p in paths if (REPO / p).exists()]
    for p in sorted(set(paths) - set(present)):
        log(f"not committed, missing: {p}")
    if not present:
        return
    step(["git", "add", *present], cwd=REPO)
    if subprocess.call(["git", "diff", "--cached", "--quiet"], cwd=REPO) != 0:
        step(["git", "commit", "-q", "-m", message], cwd=REPO)
    else:
        log("nothing changed to commit")


def form_rule(primary: bool | None, refit: bool | None) -> tuple[str, bool | None, str]:
    """(scaling, simulator validated, where the verdict came from)."""
    if primary:
        return "linear", True, "VALIDATION.md (WP11 primary)"
    return "fitted", refit, "VALIDATION_V2.md (WP11b)" if refit is not None else "no WP11b verdict"


def keep_runs(src: Path, dst: Path) -> int:
    n = 0
    for run in sorted(src.glob("*/pair*/*/attempt*/run.json")):
        target = dst / run.parent.relative_to(src)
        target.mkdir(parents=True, exist_ok=True)
        for name in RUN_FILES:
            if (run.parent / name).exists():
                shutil.copy2(run.parent / name, target / name)
        n += 1
    return n


def g3_section(pilot: dict, validated: bool | None, source: str, contrast: dict | None = None) -> str:
    """Gate G3 (plan WP12): the simulator validated, and the pairs needed per
    regime feasible; judged on J, equivalence in the low regime and
    superiority in the high one, every other number beside it in PILOT.md."""
    if contrast is not None:
        source += (f"; highest over lowest mean index among the operating points {contrast['ratio']:.2f}x "
                   f"({'an index contrast' if contrast['contrast'] else 'no index contrast: the testbed has no high-index regime, the regimes span the operating points'})")
    rows, feasible = [], []
    for regime, test in (("low", "n_equivalence"), ("high", "n_superiority")):
        n = pilot["summary"].get(regime, {}).get("J", {}).get(test)
        ok = n is not None and n <= G3_MAX_PAIRS
        feasible.append(ok)
        rows.append(f"| {regime} | {test.split('_')[1]} | {n} | {'yes' if ok else 'NO'} |")
    verdict = "met" if validated and all(feasible) else "NOT met"
    return "\n".join(["", "## Gate G3", "",
                      f"Simulator validated: {validated} ({source}). Pairs needed on J, at most {G3_MAX_PAIRS} per "
                      "regime:", "", "| regime | test | pairs needed | feasible |", "|---|---|---:|---|", *rows, "",
                      f"G3 {verdict}.", ""])


def main() -> int:
    py = sys.executable
    scaling, validated, source = form_rule(wp11_primary_met(VALIDATION), wp11_primary_met(VALIDATION_V2))
    log(f"WP12 starts: replica scaling {scaling}; simulator validated {validated} ({source})")
    step([py, "plans.py", "regimes", "--calibration", str(CALIBRATION), "--scaling", scaling])
    step([py, "plans.py", "design", "--curves", str(CALIBRATION), "--out-schedules", str(OUT / "schedules")])
    commit(["testbed/wp12/regimes.json", "testbed/wp12/design.json", "testbed/wp12/inputs"],
           "feat(testbed): WP12 design, the low- and high-index load regimes, live and tuning schedules\n\n"
           "Regimes chosen by the joint planner's interaction index in the calibrated simulator, before any\n"
           "live run. Request tables without prompt text; the schedules with prompts stay in testbed/out.")
    step([py, "plans.py", "tune", "--calibration", str(CALIBRATION), "--scaling", scaling,
          "--workers", str(TUNE_WORKERS)])
    commit(["testbed/wp12/tuning"], f"results(testbed): WP12 tuning, WP9's 64 configurations per planner arm "
                                    f"({scaling} replica scaling)")
    step([py, "plans.py", "plans", "--calibration", str(CALIBRATION), "--scaling", scaling])
    commit(["testbed/wp12/plans.json"],
           "results(testbed): WP12 plans of both arms on every live schedule, committed before any live run")
    out = TESTBED / "out"
    step([py, "pairs.py", "--schedules", str(OUT / "schedules"), "--tokens", str(out / "tokens.json"),
          "--admin-key-file", str(out / "admin-key.txt"), "--out", str(OUT / "runs")])
    log(f"kept {keep_runs(OUT / 'runs', HERE / 'runs')} run records")
    pilot = json.loads((HERE / "pilot.json").read_text(encoding="utf-8"))
    contrast = json.loads((HERE / "regimes.json").read_text(encoding="utf-8")).get("index_contrast")
    with (HERE / "PILOT.md").open("a", encoding="utf-8") as fh:
        fh.write(g3_section(pilot, validated, source, contrast))
    commit(["testbed/wp12/PILOT.md", "testbed/wp12/pilot.json", "testbed/wp12/runs"],
           "results(testbed): WP12 live pilot, sigma_d and the pairs needed per regime, gate G3")
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
