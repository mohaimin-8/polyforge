"""After WP11, unattended: WP11b when its rule says so, the post-campaign CPU
work, and WP17 up to its committed predictions (programme 2). Launched now,
it waits.

  0. wait     until testbed/out/validation/after_calibration.log ends with
              "done" (WP11 scored and committed); stop if that chain died;
  1. keep     WP11's run data into testbed/validation/runs/ and commit it with
              DIAGNOSE.md (score.py --runs testbed/validation/runs reproduces
              the score);
  2. WP11b    the rule, fixed on 2026-11-14 while WP11's cells were still
              running: if the WP11 primary prediction misses its attainment
              target (VALIDATION.md), the revised simulator (capacity ~ n **
              beta per tier, beta fitted on the calibration curves:
              predict.py --replica-scaling fitted) is validated on a NEW design
              (cells.py --seed 20261115) with the same protocol: predictions
              committed before any run, three runs per cell, score, run data
              committed. It starts as soon as WP11 ends, on the cluster as WP11
              left it: the same node, the same tenants, nothing of WP17's
              installed beside it;
  3. pilot    rerun research/hvpa/twin_pilot.py (the VPA updater's in-place
              rule changed after the committed pilot) and commit it;
  4. gate     scripts/reproduce_programme2.py, logged;
  5. free     scale the WP10 workloads (namespace pf-testbed) to 0: their CPU
              requests and idle load are not part of WP17's node (restore for
              WP12: `kubectl -n pf-testbed scale deploy --all --replicas=1`;
              Postgres starts empty, so provision the tenants again);
  6. images   WP17's images pulled by digest, the service built, side-loaded
              into kind, verified on the node;
  7. install  metrics-server, KEDA, the VPA (default flags), the service and
              WP17's Prometheus; every Deployment available;
  8. measure  the service at 0.25, 0.5, 1 and 2 cores and the node's free CPU
              (calibrate_service.py);
  9. fit      research/hvpa/calibration.py; STOP if capacity is not linear in
              CPU within 10 % (the twin's form must change first);
 10. predict  research/hvpa/twin_predict.py on the measured plant; commit the
              calibration, its curves and the predictions before any live run.

The first version of this chain (dda7b6f) ran WP11b after WP17's install,
with WP10 scaled down and back up and its tenants provisioned again. On
2026-11-15, before WP11 ended, WP11b moved first so that it measures the node
WP11 measured (no KEDA, VPA, metrics-server or second Prometheus beside it, no
fresh Postgres); the rule itself did not change. Steps 2-4 failing is logged
and WP17 goes on; WP17's own steps stop the chain.

The live pilots are not started here: their regimes and arms come from the
predictions, and a person reads those first. Log:
testbed/out/hvpa/after_wp11.log.

    python after_wp11.py      # detached, under tools/keepawake.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTBED = HERE.parent
REPO = TESTBED.parent
OUT = TESTBED / "out" / "hvpa"
LOG = OUT / "after_wp11.log"
WP11_LOG = TESTBED / "out" / "validation" / "after_calibration.log"
CONTEXT = "kind-polyforge-testbed"
RESEARCH = REPO / "research" / "hvpa"
WP12 = TESTBED / "wp12" / "after_wp11b.py"  # read when it runs, so it can be finished while WP11b runs


def log(msg: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")


def step(cmd: list[str], cwd: Path = REPO, fatal: bool = True) -> int:
    log("+ " + " ".join(cmd))
    with LOG.open("a", encoding="utf-8") as f:
        rc = subprocess.call(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT)
    if rc != 0:
        log(f"exit {rc}" + (": stopping" if fatal else ": noted, going on"))
        if fatal:
            raise SystemExit(rc)
    return rc


def wp11_state() -> str:
    """'done', 'failed' (the chain stopped) or 'running'."""
    if not WP11_LOG.exists():
        return "running"
    lines = WP11_LOG.read_text(encoding="utf-8").strip().splitlines()
    if lines and lines[-1].endswith(" done"):
        return "done"
    if any(": stopping" in line or "not a measurement" in line for line in lines):
        return "failed"
    return "running"


RUN_FILES = ("k6-samples.csv.gz", "k6-summary.json", "check.json", "run.json")


def keep_wp11_runs(src: Path = TESTBED / "out" / "validation", dst: Path = TESTBED / "validation" / "runs") -> int:
    """Copy each WP11 run's data into the repository (the k6 progress log stays
    out): per request latency, status, tenant, cache outcome; no prompt text
    (k6 records no bodies) and no API key (no headers)."""
    n = 0
    for run in sorted(src.glob("cell*/run*")):
        if not (run / "run.json").exists():
            continue
        out = dst / run.parent.name / run.name
        out.mkdir(parents=True, exist_ok=True)
        for f in RUN_FILES:
            if (run / f).exists():
                shutil.copyfile(run / f, out / f)
        n += 1
    if (src / "actuator.jsonl").exists():
        shutil.copyfile(src / "actuator.jsonl", dst / "actuator.jsonl")
    log(f"kept {n} WP11 runs in {dst}")
    return n


def commit(paths: list[str], message: str, repo: Path = REPO) -> None:
    """Commit the paths that exist (a report a non-fatal step did not write is
    logged, not a reason to stop the chain)."""
    present = [p for p in paths if (repo / p).exists()]
    for p in paths:
        if p not in present:
            log(f"not committed, missing: {p}")
    if not present:
        log("nothing to commit")
        return
    step(["git", "add", *present], cwd=repo)
    if subprocess.call(["git", "diff", "--cached", "--quiet"], cwd=repo) != 0:
        step(["git", "commit", "-q", "-m", message], cwd=repo)
    else:
        log("nothing changed to commit")


def main() -> int:
    log("waiting for WP11 to finish")
    while (state := wp11_state()) == "running":
        time.sleep(60)
    if state != "done":
        log("the WP11 chain stopped before its end: not starting (see after_calibration.log)")
        return 5
    py, kc = sys.executable, ["kubectl", "--context", CONTEXT]
    keep_wp11_runs()
    step([py, str(TESTBED / "validation" / "diagnose.py"), "--runs", str(TESTBED / "validation" / "runs")],
         fatal=False)
    commit(["testbed/validation/runs", "testbed/validation/DIAGNOSE.md"],
           "results(testbed): WP11 run data and the error breakdown by cell factor\n\n"
           "score.py --runs testbed/validation/runs reproduces VALIDATION.md; diagnose.py wrote DIAGNOSE.md\n"
           "(descriptive). No prompt text, no keys.")
    met = wp11_primary_met()
    if met is None:
        log("VALIDATION.md has no readable attainment verdict: WP11b not started")
    elif met:
        log("the WP11 primary met its attainment target: WP11b not needed")
    else:
        try:
            wp11b(py)
        except SystemExit as stop:
            log(f"WP11b stopped (exit {stop.code}); WP17 goes on and WP11b is looked at after")
    if WP12.exists():
        log("WP12: the live pilot of the main contrast, on the cluster WP11 left")
        if step([py, str(WP12)], cwd=WP12.parent, fatal=False) != 0:
            log("WP12 stopped; WP17 goes on and WP12 is looked at after")
    else:
        log(f"no {WP12.name}: WP12 not run here")
    if step([py, "twin_pilot.py"], cwd=RESEARCH, fatal=False) == 0:
        commit(["research/hvpa/TWIN_PILOT.md", "research/hvpa/out/twin_pilot.csv"],
               "results(hvpa): twin pilot rerun (in-place updater rule, Azure workloads)\n\n"
               "Exploratory, the a-priori plant.")
    step([py, "scripts/reproduce_programme2.py"], fatal=False)
    step(kc + ["-n", "pf-testbed", "scale", "deployment", "--all", "--replicas=0"])
    rc = wp17(py, kc)
    if rc:
        return rc
    log("done")
    return 0


def wp17(py: str, kc: list[str]) -> int:
    """Steps 6-10: WP17's cluster side up to its committed predictions; 3 when
    the fit says the twin's plant form must change first."""
    lock = ["--lock", str(HERE / "images.lock")]
    for action in ("pull", "build", "load", "verify"):
        step([py, str(TESTBED / "scripts" / "images.py"), action, *lock])
    step([py, str(HERE / "install.py"), "--vpa-setting", "default"])
    for ns in ("kube-system", "keda", "pf-hvpa"):
        step(kc + ["-n", ns, "wait", "--for=condition=available", "deployment", "--all", "--timeout=900s"])
    curves = OUT / "curves"
    step([py, str(HERE / "calibrate_service.py"), "--out", str(curves)])
    rc = step([py, "calibration.py", "--curves", str(curves), "--node-cpu-file", str(curves / "node_cpu.json"),
               "--out", "calibration.json"], cwd=RESEARCH, fatal=False)
    if rc == 3:
        log("capacity is not linear in CPU within 10 %: the twin's plant form must change before it predicts; stopping")
        return 3
    if rc != 0:
        raise SystemExit(rc)
    dst = RESEARCH / "calibration_curves"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(curves, dst)
    step([py, "twin_predict.py", "--calibration", "calibration.json", "--out", "predictions.json"], cwd=RESEARCH)
    commit(["research/hvpa/calibration.json", "research/hvpa/calibration_curves", "research/hvpa/predictions.json"],
           "results(hvpa): WP17 calibration and the twin's predictions, before any live run\n\n"
           "The service measured at 0.25, 0.5, 1 and 2 cores in the cluster, the node's free CPU, the fitted\n"
           "plant, and twin_predict.py's regimes, gains, live choices and tuned targets.")
    log("WP17 is ready for its live pilots, which start after the predictions are read")
    return 0


def wp11_primary_met(report: Path = TESTBED / "validation" / "VALIDATION.md") -> bool | None:
    """The primary prediction's attainment verdict in VALIDATION.md: True for
    'yes', False for 'NO', None when the report or the row is missing."""
    if not report.exists():
        return None
    text = report.read_text(encoding="utf-8")
    start = text.find("## Primary prediction")
    end = text.find("\n## ", start + 1)
    for line in text[start:end if end > 0 else None].splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0] == "attainment":
            return {"yes": True, "NO": False}.get(cells[-1])
    return None


def wp11b(py: str) -> None:
    """Step 2 on the cluster as WP11 left it (WP10 up, tenants provisioned)."""
    val, out = TESTBED / "validation", TESTBED / "out"
    log("WP11 primary missed its attainment target: WP11b, the fitted-scaling simulator on a new design")
    step([py, "cells.py", "--seed", "20261115", "--out", "cells_v2.json"], cwd=val)
    step([py, "cell_schedules.py", "cells", "--cells", "cells_v2.json", "--curves", "calibration",
          "--out", str(out / "schedules_v2"), "--inputs", "inputs_v2"], cwd=val)
    step([py, "predict.py", "--calibration", "calibration", "--cells", "cells_v2.json", "--inputs", "inputs_v2",
          "--replica-scaling", "fitted", "--out", "predictions_v2.json"], cwd=val)
    commit(["testbed/validation/cells_v2.json", "testbed/validation/inputs_v2", "testbed/validation/predictions_v2.json"],
           "results(testbed): WP11b predictions, committed before any of its cells runs\n\n"
           "The simulator with capacity ~ n ** beta per tier (beta fitted on the calibration curves) on a new\n"
           "design (seed 20261115), by the rule fixed while WP11 ran (testbed/hvpa/after_wp11.py).")
    runner = [py, "run_cells.py", "--cells", "cells_v2.json", "--predictions", "predictions_v2.json",
              "--schedules", str(out / "schedules_v2"), "--tokens", str(out / "tokens.json"),
              "--admin-key-file", str(out / "admin-key.txt"), "--out", str(out / "validation_v2")]
    step(runner, cwd=val)
    step(runner + ["--redo-invalid"], cwd=val)
    step([py, "score.py", "--runs", str(out / "validation_v2"), "--cells", "cells_v2.json", "--predictions",
          "predictions_v2.json", "--report", "VALIDATION_V2.md", "--plot", "validation_v2_bland_altman.png"], cwd=val)
    keep_wp11_runs(out / "validation_v2", val / "runs_v2")
    step([py, "diagnose.py", "--runs", "runs_v2", "--cells", "cells_v2.json", "--predictions", "predictions_v2.json",
          "--out", "DIAGNOSE_V2.md"], cwd=val, fatal=False)
    commit(["testbed/validation/VALIDATION_V2.md", "testbed/validation/validation_v2_bland_altman.png",
            "testbed/validation/runs_v2", "testbed/validation/DIAGNOSE_V2.md"],
           "results(testbed): WP11b scored, the revised simulator against the testbed on new cells")


if __name__ == "__main__":
    sys.exit(main())
