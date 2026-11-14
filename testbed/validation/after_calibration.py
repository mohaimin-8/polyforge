"""WP11 from the calibration to the score, unattended (programme 2).

  1. wait   until calibrate.py has written all five curves; stop if a curve
            lost more than MAX_FAILED of its requests (it is not a
            measurement then; rerun that one by hand);
  2. build  the cells' schedules and request tables (cell_schedules.py) and the
            predictions (predict.py), and copy the curves (latencies and
            timings, no prompt text) to validation/calibration/;
  3. commit the predictions, the request tables and the curves, before any
            cell runs (plan WP11; run_cells.py checks it);
  4. run    every cell three times (run_cells.py), then once more for any run
            marked invalid;
  5. score  score.py, and commit VALIDATION.md with its plot.
Each step's output goes to testbed/out/validation/after_calibration.log;
the first failure stops the chain with its exit status.

    python after_calibration.py      # launched detached, under tools/keepawake.py
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
OUT = TESTBED / "out"
CURVES = OUT / "curves"
NAMES = ("small_r1", "mid_r1", "small_r2", "small_r3", "mid_r2")
MAX_FAILED = 0.01
LOG = OUT / "validation" / "after_calibration.log"


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")


def step(cmd: list[str], cwd: Path = HERE) -> None:
    log("+ " + " ".join(cmd))
    with LOG.open("a", encoding="utf-8") as f:
        rc = subprocess.call(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT)
    if rc != 0:
        log(f"exit {rc}: stopping")
        raise SystemExit(rc)


def curves_ready() -> bool:
    return all((CURVES / n / "load_curve_meta.json").exists() for n in NAMES)


def failed_share(name: str) -> float:
    import csv

    rows = list(csv.DictReader((CURVES / name / "load_curve_requests.csv").open(encoding="utf-8")))
    return sum(1 for r in rows if r.get("error")) / max(1, len(rows))


def main() -> int:
    log("waiting for the calibration curves")
    while not curves_ready():
        time.sleep(30)
    time.sleep(60)   # calibrate.py's last write and pool reset
    for n in NAMES:
        share = failed_share(n)
        log(f"{n}: failed share {share:.4f}")
        if share > MAX_FAILED:
            log(f"{n} lost {share:.1%} of its requests: not a measurement; stopping")
            return 4
    py = sys.executable
    step([py, "cell_schedules.py", "cells", "--curves", str(CURVES), "--out", str(OUT / "schedules")])
    step([py, "predict.py", "--calibration", str(CURVES)])
    for n in NAMES:
        dst = HERE / "calibration" / n
        dst.mkdir(parents=True, exist_ok=True)
        for f in ("load_curve_requests.csv", "load_curve_meta.json"):
            shutil.copyfile(CURVES / n / f, dst / f)
    preds = json.loads((HERE / "predictions.json").read_text(encoding="utf-8"))
    log("predictions: " + "; ".join(f"cell {p['cell']} att {p['attainment']:.3f}" for p in preds["predictions"]))
    step(["git", "add", "testbed/validation/predictions.json", "testbed/validation/inputs",
          "testbed/validation/calibration"], cwd=REPO)
    step(["git", "commit", "-q", "-m", "results(testbed): WP11 predictions, committed before any cell runs\n\n"
          "predictions.json from predict.py on the in-cluster calibration curves (validation/calibration/,\n"
          "latencies and timings only); the cells' request tables (validation/inputs/, no prompt text)."],
         cwd=REPO)
    runner = [py, "run_cells.py", "--schedules", str(OUT / "schedules"), "--tokens", str(OUT / "tokens.json"),
              "--admin-key-file", str(OUT / "admin-key.txt"), "--out", str(OUT / "validation")]
    step(runner)
    step(runner + ["--redo-invalid"])   # one more try for runs marked invalid; valid ones are skipped
    step([py, "score.py", "--runs", str(OUT / "validation")])
    step(["git", "add", "testbed/validation/VALIDATION.md", "testbed/validation/validation_bland_altman.png"],
         cwd=REPO)
    step(["git", "commit", "-q", "-m", "results(testbed): WP11 scored, the simulator against the testbed\n\n"
          "VALIDATION.md and the Bland-Altman plot from score.py over the valid runs."], cwd=REPO)
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
