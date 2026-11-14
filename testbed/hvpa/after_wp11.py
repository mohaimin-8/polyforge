"""After WP11, unattended: the post-campaign CPU work and WP17 up to its
committed predictions (programme 2). Launched now, it waits.

  0. wait     until testbed/out/validation/after_calibration.log ends with
              "done" (WP11 scored and committed); stop if that chain died;
  1. free     scale the WP10 workloads (namespace pf-testbed) to 0: their CPU
              requests and idle load are not part of WP17's node (restore for
              WP12: `kubectl -n pf-testbed scale deploy --all --replicas=1`);
  2. pilot    rerun research/hvpa/twin_pilot.py (the VPA updater's in-place
              rule changed after the committed pilot) and commit it;
  3. gate     scripts/reproduce_programme2.py, logged (a failure is reported,
              not fatal);
  4. images   WP17's images pulled by digest, the service built, side-loaded
              into kind, verified on the node;
  5. install  metrics-server, KEDA, the VPA (default flags), the service and
              WP17's Prometheus; every Deployment available;
  6. measure  the service at 0.25, 0.5, 1 and 2 cores and the node's free CPU
              (calibrate_service.py);
  7. fit      research/hvpa/calibration.py; STOP if capacity is not linear in
              CPU within 10 % (the twin's form must change first);
  8. predict  research/hvpa/twin_predict.py on the measured plant; commit the
              calibration, its curves and the predictions before any live run.
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


def commit(paths: list[str], message: str) -> None:
    step(["git", "add", *paths])
    if subprocess.call(["git", "diff", "--cached", "--quiet"], cwd=REPO) != 0:
        step(["git", "commit", "-q", "-m", message])
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
    step(kc + ["-n", "pf-testbed", "scale", "deployment", "--all", "--replicas=0"])
    step([py, "twin_pilot.py"], cwd=RESEARCH)
    commit(["research/hvpa/TWIN_PILOT.md", "research/hvpa/out/twin_pilot.csv"],
           "results(hvpa): twin pilot rerun (in-place updater rule, Azure workloads)\n\nExploratory, the a-priori plant.")
    step([py, "scripts/reproduce_programme2.py"], fatal=False)
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
    log("done: WP17 is ready for its live pilots, which start after the predictions are read")
    return 0


if __name__ == "__main__":
    sys.exit(main())
