"""Run the WP11 cells on the testbed, three times each, in a seeded order (programme 2).

Refuses to start unless predictions.json is committed and unchanged: the
predictions come first (plan WP11). Per run:
  1. pools   the cell's (small, mid) replica counts through the actuator
             (actuator/actuate.py), then wait until every pod is ready;
  2. cache   a fresh gateway (rollout restart: its semantic cache and knobs
             live in memory), then each tenant's tier pin and cache MB set
             and read back, so every run starts cold with the cell's posture;
  3. quiet   wait until Prometheus reports no request processing or deferred
             on any model server, twice in a row (a timed-out request can
             still be computing);
  4. load    k6 replays the cell's schedule open loop (load/replay.js),
             without aborting on failures (a failure is a miss, the plan's
             metric), with a request timeout far above any SLO and k6's
             usage report off;
  5. check   scripts/check_run.py on the samples, with Prometheus (load spread
             over pods, CPU throttling); run.json records the times, the
             posture read back, k6's exit, the schedule lag, the power line.
A run whose generator fell behind (p99 schedule lag over MAX_P99_LAG_MS) or
whose posture did not read back is marked invalid and rerun by
--redo-invalid. Order: three rounds, each all 20 cells in a fresh seeded
permutation, so drift over the campaign does not line up with any factor.
A finished valid run is skipped on a rerun.

    python run_cells.py --schedules ../out/schedules --tokens ../out/tokens.json \
        --admin-key-file ../out/admin-key.txt --out ../out/validation
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Sequence

import numpy as np

HERE = Path(__file__).resolve().parent
TESTBED = HERE.parent
REPO = TESTBED.parent
for p in (TESTBED / "actuator", TESTBED / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import actuate  # noqa: E402
from check_run import prometheus_query  # noqa: E402

SEED = 20261114
ROUNDS = 3
CONTEXT = "kind-polyforge-testbed"
NAMESPACE = "pf-testbed"
GATEWAY = "http://127.0.0.1:30081"
PROMETHEUS = "http://127.0.0.1:30090"
K6_ENV = {"VUS": "400", "REQUEST_TIMEOUT": "120s", "ABORT_ON_FAIL": "false", "MAX_FAILED_RATE": "1.0",
          "LEAD_S": "15", "MAX_P99_LAG_MS": "1000", "K6_NO_USAGE_REPORT": "true"}
MAX_P99_LAG_MS = 1000.0
READY_TIMEOUT_S = 600.0
QUIET_TIMEOUT_S = 900.0
POLL_S = 5.0
PREDICTIONS = HERE / "predictions.json"


def run_order(cells: Sequence[int], rounds: int = ROUNDS, seed: int = SEED) -> list[tuple[int, int]]:
    """(cell, run) pairs: round r runs every cell once, in its own permutation."""
    rng = np.random.default_rng(seed)
    return [(int(c), r) for r in range(rounds) for c in rng.permutation(list(cells))]


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)


def predictions_committed(path: Path = PREDICTIONS) -> str:
    """The commit holding predictions.json as it is on disk; raises otherwise."""
    rel = path.relative_to(REPO).as_posix()
    if git("ls-files", "--error-unmatch", rel).returncode != 0:
        raise SystemExit(f"{rel} is not committed: commit the predictions before any cell runs")
    if git("diff", "--quiet", "HEAD", "--", rel).returncode != 0:
        raise SystemExit(f"{rel} differs from the committed version")
    return git("log", "-1", "--format=%H", "--", rel).stdout.strip()


def on_ac_power() -> bool | None:
    """Windows GetSystemPowerStatus: True on the mains, False on battery, None unknown."""
    if os.name != "nt":
        return None

    class Status(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_byte), ("BatteryFlag", ctypes.c_byte),
                    ("BatteryLifePercent", ctypes.c_byte), ("SystemStatusFlag", ctypes.c_byte),
                    ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]
    s = Status()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
        return None
    return {0: False, 1: True}.get(s.ACLineStatus)


def kubectl(*args: str, timeout: float = 600) -> str:
    proc = subprocess.run(["kubectl", "--context", CONTEXT, "-n", NAMESPACE, *args], capture_output=True,
                          text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def wait_ready(actuator: actuate.Actuator, sleep: Callable[[float], None] = time.sleep) -> float:
    t0 = time.time()
    while time.time() - t0 < READY_TIMEOUT_S:
        now = actuator.current()
        if all(spec == ready for spec, ready in now.values()):
            return time.time() - t0
        sleep(POLL_S)
    raise RuntimeError(f"pools not ready after {READY_TIMEOUT_S:.0f} s: {actuator.current()}")


def wait_quiet(query: Callable[[str, float], list], sleep: Callable[[float], None] = time.sleep) -> float:
    """Until no model server reports a request processing or deferred, twice in a row."""
    promql = "sum(llamacpp:requests_processing) + sum(llamacpp:requests_deferred)"
    t0, zeros = time.time(), 0
    while time.time() - t0 < QUIET_TIMEOUT_S:
        result = query(promql, time.time())
        if not result:  # no series is not idle: the servers are not being scraped
            raise RuntimeError(f"Prometheus has no series for {promql!r}")
        busy = float(result[0]["value"][1])
        zeros = zeros + 1 if busy == 0 else 0
        if zeros >= 2:
            return time.time() - t0
        sleep(POLL_S)
    raise RuntimeError(f"model servers still busy after {QUIET_TIMEOUT_S:.0f} s")


HEALTHY_IN_A_ROW = 3
GATEWAY_READY_TIMEOUT_S = 180.0
POSTURE_ATTEMPTS = 5


def gateway_answers(get: Callable[[str], int] | None = None, sleep: Callable[[float], None] = time.sleep,
                    timeout_s: float = GATEWAY_READY_TIMEOUT_S) -> float:
    """Until the gateway's /healthz answers 200 through the NodePort several
    times in a row: `rollout status` returns before kube-proxy routes the
    Service to the new pod (2026-11-14: the first knob writes after a restart
    met closed connections)."""
    import urllib.request

    def default_get(url: str) -> int:
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 (the testbed's own NodePort)
            return resp.status

    get = get or default_get
    t0, ok = time.time(), 0
    while time.time() - t0 < timeout_s:
        try:
            ok = ok + 1 if get(f"{GATEWAY}/healthz") == 200 else 0
        except OSError:
            ok = 0
        if ok >= HEALTHY_IN_A_ROW:
            return time.time() - t0
        sleep(1.0)
    raise RuntimeError(f"the gateway did not answer through its NodePort within {timeout_s:.0f} s")


def fresh_gateway() -> float:
    t0 = time.time()
    kubectl("rollout", "restart", "deployment/ai-gateway")
    kubectl("rollout", "status", "deployment/ai-gateway", "--timeout=300s")
    gateway_answers()
    return time.time() - t0


def set_posture(actuator: actuate.Actuator, pins: dict, sleep: Callable[[float], None] = time.sleep) -> dict:
    """Write and read back every tenant's knobs, retrying a write that met a
    connection error; the errors of the last attempt are kept."""
    tenants = {t: actuate.Knobs(model_tier=tier, cache_size_mb=int(pins["cache_knob_mb"][t]))
               for t, tier in pins["pins"].items()}
    for attempt in range(1, POSTURE_ATTEMPTS + 1):
        record = actuator.apply(actuate.Decision(step=None, t_s=0.0, tenants=tenants))
        if not record["errors"]:
            break
        sleep(2.0 * attempt)
    return {"knobs": record["knobs"], "errors": record["errors"], "attempts": attempt}


def k6_lag_p99(summary_path: Path) -> float:
    """k6's p99 schedule lag; inf when the summary is missing or unreadable
    (k6 died first), which marks the run invalid rather than stopping."""
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        lag = data["metrics"]["wp10_schedule_lag"]
        return float(lag.get("values", lag)["p(99)"])
    except (OSError, ValueError, KeyError, TypeError):
        return float("inf")


def run_one(cell: dict, run: int, schedule_dir: Path, tokens: Path, out: Path, actuator: actuate.Actuator,
            slo_ms: float, commit: str, order: int) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    pins = json.loads((schedule_dir / "pins.json").read_text(encoding="utf-8"))
    rec: dict = {"cell": cell["cell"], "run": run, "order": order, "pools": cell["pools"],
                 "predictions_commit": commit, "ac_power": on_ac_power(), "complete": False}
    record = actuator.apply(actuate.Decision(step=None, t_s=0.0,
                                             replicas={"small": cell["pools"][0], "mid": cell["pools"][1]}))
    rec["pools_applied"] = record.get("applied")
    rec["ready_wait_s"] = wait_ready(actuator)
    rec["gateway_restart_s"] = fresh_gateway()
    rec["posture"] = set_posture(actuator, pins)
    rec["quiet_wait_s"] = wait_quiet(prometheus_query(PROMETHEUS))
    env = {**os.environ, **K6_ENV, "SCHEDULE": str((schedule_dir / "schedule.json").resolve()),
           "TOKENS": str(tokens.resolve()), "GATEWAY_URL": GATEWAY}
    rec["start_unix"] = time.time()
    with (out / "k6.log").open("w", encoding="utf-8") as log:
        rec["k6_exit"] = subprocess.call(
            ["k6", "run", "--no-usage-report", "--summary-export", str(out / "k6-summary.json"),
             "--out", f"csv={out / 'k6-samples.csv.gz'}", str(TESTBED / "load" / "replay.js")],
            env=env, stdout=log, stderr=subprocess.STDOUT)
    rec["end_unix"] = time.time()
    rec["ac_power_end"] = on_ac_power()
    rec["lag_p99_ms"] = k6_lag_p99(out / "k6-summary.json")
    check = subprocess.run([sys.executable, str(TESTBED / "scripts" / "check_run.py"), "--k6-csv",
                            str(out / "k6-samples.csv.gz"), "--slo-ms", str(slo_ms), "--prometheus", PROMETHEUS,
                            "--start", str(rec["start_unix"]), "--end", str(rec["end_unix"]),
                            "--out", str(out / "check.json")], capture_output=True, text=True)
    rec["check_exit"] = check.returncode
    problems = list(rec["posture"]["errors"])
    if rec["check_exit"] == 1:  # a pod far below its fair share: the pool was not what the cell says
        problems.append("load distribution over pods: see check.json")
    elif rec["check_exit"] != 0:
        problems.append(f"check_run failed ({rec['check_exit']}): {check.stderr.strip()[:200]}")
    if rec["lag_p99_ms"] > MAX_P99_LAG_MS:
        problems.append(f"schedule lag p99 {rec['lag_p99_ms']:.0f} ms")
    if not (out / "k6-samples.csv.gz").exists():
        problems.append("no k6 samples")
    rec.update(problems=problems, valid=not problems, complete=True)
    (out / "run.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return rec


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--schedules", type=Path, required=True)
    ap.add_argument("--tokens", type=Path, required=True)
    ap.add_argument("--admin-key-file", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--rounds", type=int, default=ROUNDS)
    ap.add_argument("--only", type=int, nargs="*", default=None, help="cell numbers (default: all)")
    ap.add_argument("--redo-invalid", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="print the order and stop")
    ap.add_argument("--cells", type=Path, default=HERE / "cells.json")
    ap.add_argument("--predictions", type=Path, default=PREDICTIONS)
    args = ap.parse_args(argv)
    design = json.loads(args.cells.read_text(encoding="utf-8"))
    cells = {c["cell"]: c for c in design["cells"]}
    order = [(c, r) for c, r in run_order(sorted(cells), args.rounds) if args.only is None or c in args.only]
    if args.dry_run:
        print(" ".join(f"{c}/{r}" for c, r in order))
        return 0
    commit = predictions_committed(args.predictions.resolve())
    preds = {p["cell"]: p for p in json.loads(args.predictions.read_text(encoding="utf-8"))["predictions"]}
    bounds = actuate.Bounds.load(actuate.DEFAULT_BOUNDS)
    actuator = actuate.Actuator(bounds=bounds, kube=actuate.KubectlKube(bounds.namespace, context=CONTEXT),
                                knobs=actuate.GatewayKnobs(GATEWAY, actuate.admin_key(args.admin_key_file)),
                                log=actuate.jsonl_logger(args.out / "actuator.jsonl"))
    for i, (c, r) in enumerate(order):
        run_dir = args.out / f"cell{c:02d}" / f"run{r}"
        done = run_dir / "run.json"
        if done.exists():
            prev = json.loads(done.read_text(encoding="utf-8"))
            if prev.get("complete") and (prev.get("valid") or not args.redo_invalid):
                continue
        try:
            rec = run_one(cells[c], r, args.schedules / f"cell{c:02d}", args.tokens, run_dir, actuator,
                          preds[c]["slo_ms"], commit, i)
        except Exception as err:  # one run's trouble (a timeout, a kubectl error) must not end the campaign
            rec = {"cell": c, "run": r, "order": i, "complete": True, "valid": False, "k6_exit": None,
                   "lag_p99_ms": float("nan"), "problems": [f"{type(err).__name__}: {err}"[:400]]}
            run_dir.mkdir(parents=True, exist_ok=True)
            done.write_text(json.dumps(rec, indent=1), encoding="utf-8")
        print(f"[{i + 1}/{len(order)}] cell {c} run {r}: k6 {rec['k6_exit']}, lag p99 {rec['lag_p99_ms']:.0f} ms, "
              f"{'valid' if rec['valid'] else 'INVALID: ' + '; '.join(rec['problems'])}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
