"""WP17 live runs: one arm on one regime, and paired runs in ABBA order (programme 2).

One run:
  1. reset   arm objects deleted (HPA, ScaledObject, VPA, planner) with the
             VPA's checkpoints, the VPA recommender restarted (its in-memory
             history goes with it), the service back to 2 pods x 0.5 core
             through a rollout, every pod ready;
  2. arm     arms.render(arm, target) applied; A3/A4 get the ConfigMap
             wp17-planner (lattice.py, service_plant.py, planner_pod.py and
             the fitted plant calibration.json);
  3. load    a k6 Job in the cluster (load/rate.js on the regime's schedule,
             1 core, requests = limits);
  4. watch   every POLL_S the service's pods from the API: requested CPU of
             scheduled pods (cost = core-seconds x price), the replica count
             (direction reversals), each pod's CPU (resizes);
  5. result  k6's summary (attainment counts dropped iterations as late), the
             planner's decision log (index, one-step loss), all in one JSON.
A pair is two arms on the same regime and seed in ABBA order (plan WP12): A
then B, B then A, so drift over a session does not favour either arm.

    python live.py run --arm A4 --target 2 --regime step_up:0.2:7 --calibration cal.json --out ../out/hvpa/live
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Sequence

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "research" / "hvpa"))

import hvpa_arms as arms  # noqa: E402  (not `arms`: research/comparators has one)

CONTEXT, NAMESPACE = "kind-polyforge-testbed", "pf-hvpa"
K6_IMAGE = "docker.io/grafana/k6:2.1.0"
POLL_S = 10.0
STEP_S = 10.0
INITIAL = (2, 0.5)
RECOMMENDER = "vpa-vertical-pod-autoscaler-recommender"
BEGIN, END = "-----BEGIN WP17 LOAD-----", "-----END WP17 LOAD-----"


def schedule(shape: str, sigma: float, seed: int, slo_s: float, plant) -> dict:
    """The regime's per-step rates, as the twin draws them (workloads.py)."""
    import numpy as np

    import workloads

    rng = np.random.default_rng([20261114, int(sigma * 100), workloads.all_shapes().index(shape), seed])
    rates = workloads.noisy(workloads.shape(shape, workloads.base_rps(plant)), sigma, rng)
    return {"step_s": STEP_S, "rates": [round(float(r), 4) for r in rates], "slo_s": slo_s,
            "shape": shape, "sigma": sigma, "seed": seed}


def k6_job(name: str) -> dict:
    return {"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": name, "namespace": NAMESPACE},
            "spec": {"backoffLimit": 0, "ttlSecondsAfterFinished": 86400, "template": {"spec": {
                "restartPolicy": "Never", "enableServiceLinks": False,
                "containers": [{"name": "k6", "image": K6_IMAGE, "imagePullPolicy": "Never",
                                "command": ["k6", "run", "--no-usage-report", "-e", "SCHEDULE=/m/schedule.json",
                                            "/m/rate.js"],
                                "env": [{"name": "K6_NO_USAGE_REPORT", "value": "true"}],
                                "resources": {"requests": {"cpu": "1", "memory": "512Mi"},
                                              "limits": {"cpu": "1", "memory": "512Mi"}},
                                "volumeMounts": [{"name": "m", "mountPath": "/m", "readOnly": True}]}],
                "volumes": [{"name": "m", "configMap": {"name": "wp17-load"}}]}}}}


def parse_k6(log: str) -> dict:
    data = json.loads(log[log.index(BEGIN) + len(BEGIN):log.index(END)])
    m = data["metrics"]
    on_time = m["wp17_on_time"]["values"]
    started = m["iterations"]["values"]["count"] if "iterations" in m else 0
    dropped = m["dropped_iterations"]["values"]["count"] if "dropped_iterations" in m else 0
    lat = m["wp17_latency"]["values"] if "wp17_latency" in m else {}
    return {"requests": started + dropped, "dropped": dropped, "on_time": on_time.get("passes", 0),
            "attainment": on_time.get("passes", 0) / (started + dropped) if started + dropped else float("nan"),
            "latency_ms": {k: lat.get(k) for k in ("avg", "med", "p(95)", "p(99)", "max")}}


def on_ac_power() -> bool | None:
    """Windows GetSystemPowerStatus: True on the mains, False on battery, None
    unknown (testbed/validation/run_cells.py's check; WP14 lost runs to power
    flapping)."""
    import ctypes
    import os

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


def cpu_cores(q: str) -> float:
    q = str(q)
    return float(q[:-1]) / 1000.0 if q.endswith("m") else float(q)


def watch_record(polls: list[dict], price_core_hr: float) -> dict:
    """Cost, reversals and resizes from the pod polls ({t, pods: [{name, cpu, scheduled, ready}]})."""
    cost, resizes, reversals, last_dir, seen = 0.0, 0, 0, 0, {}
    counts = [len(p["pods"]) for p in polls]
    for a, b in zip(polls, polls[1:]):
        cost += sum(x["cpu"] for x in a["pods"] if x["scheduled"]) * (b["t"] - a["t"]) / 3600.0 * price_core_hr
    for p in polls:
        for x in p["pods"]:
            if x["name"] in seen and abs(seen[x["name"]] - x["cpu"]) > 1e-9:
                resizes += 1
            seen[x["name"]] = x["cpu"]
    for a, b in zip(counts, counts[1:]):
        if a != b:
            d = 1 if b > a else -1
            reversals += int(last_dir != 0 and d != last_dir)
            last_dir = d
    gaps = [b["t"] - a["t"] for a, b in zip(polls, polls[1:])]
    return {"cost": cost, "resizes": resizes, "reversals": reversals, "max_replicas": max(counts, default=0),
            "polls": len(polls), "max_gap_s": max(gaps, default=0.0)}


def abba(a: str, b: str, pairs: int) -> list[str]:
    return [x for _ in range(pairs) for x in (a, b, b, a)][: 2 * pairs] if pairs else []


def kubectl(*args: str, stdin: str | None = None, check: bool = True, timeout: float = 900) -> str:
    proc = subprocess.run(["kubectl", "--context", CONTEXT, *args], input=stdin, capture_output=True, text=True,
                          timeout=timeout)
    if check and proc.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args[:4])}: {proc.stderr.strip()[:400]}")
    return proc.stdout


def poll_pods() -> dict:
    out = json.loads(kubectl("-n", NAMESPACE, "get", "pods", "-l", "app.kubernetes.io/name=cpuburn", "-o", "json"))
    pods = []
    for p in out["items"]:
        if p["metadata"].get("deletionTimestamp"):
            continue
        c = next(c for c in p["spec"]["containers"] if c["name"] == "cpuburn")
        conds = {x["type"]: x["status"] for x in p.get("status", {}).get("conditions", [])}
        pods.append({"name": p["metadata"]["name"], "cpu": cpu_cores(c["resources"]["requests"]["cpu"]),
                     "scheduled": conds.get("PodScheduled") == "True", "ready": conds.get("Ready") == "True"})
    return {"t": time.time(), "pods": pods}


def reset() -> None:
    ns = ["-n", NAMESPACE]
    for kind in ("hpa", "scaledobject.keda.sh", "verticalpodautoscaler.autoscaling.k8s.io",
                 "verticalpodautoscalercheckpoint.autoscaling.k8s.io", "deployment/planner"):
        target = [kind] if "/" in kind else [kind, "--all"]
        kubectl(*ns, "delete", *target, "--ignore-not-found", "--wait=true", check=False)
    kubectl("-n", "kube-system", "rollout", "restart", f"deployment/{RECOMMENDER}", check=False)
    kubectl("-n", "kube-system", "rollout", "status", f"deployment/{RECOMMENDER}", "--timeout=300s", check=False)
    patch = {"spec": {"replicas": INITIAL[0], "template": {"spec": {"containers": [{"name": "cpuburn", "resources": {
        "requests": {"cpu": f"{int(INITIAL[1] * 1000)}m"}, "limits": {"cpu": f"{int(INITIAL[1] * 1000)}m"}}}]}}}}
    kubectl(*ns, "patch", "deployment", "cpuburn", "--type", "strategic", "-p", json.dumps(patch))
    kubectl(*ns, "rollout", "restart", "deployment/cpuburn")
    kubectl(*ns, "rollout", "status", "deployment/cpuburn", "--timeout=300s")


def configmap(name: str, files: dict[str, str]) -> None:
    cm = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": name, "namespace": NAMESPACE}, "data": files}
    kubectl("apply", "-f", "-", stdin=yaml.safe_dump(cm))


def run(arm: str, target: float, regime: dict, calibration: Path, out: Path,
        poll: Callable[[], dict] = poll_pods) -> dict:
    reset()
    if arm in ("A3", "A4"):
        files = {n: (REPO / "research" / "hvpa" / n).read_text(encoding="utf-8") for n in ("lattice.py", "service_plant.py")}
        files["planner_pod.py"] = (HERE / "planner" / "planner_pod.py").read_text(encoding="utf-8")
        files["calibration.json"] = calibration.read_text(encoding="utf-8")
        configmap("wp17-planner", files)
    objs = arms.render(arm, target)
    if objs:
        kubectl("apply", "-f", "-", stdin=yaml.safe_dump_all(objs))
    configmap("wp17-load", {"rate.js": (HERE / "load" / "rate.js").read_text(encoding="utf-8"),
                            "schedule.json": json.dumps(regime)})
    name = f"wp17-load-{int(time.time())}"
    power_start = on_ac_power()
    kubectl("apply", "-f", "-", stdin=yaml.safe_dump(k6_job(name)))
    polls, t0, outcome = [], time.time(), "timeout"
    deadline = t0 + len(regime["rates"]) * regime["step_s"] + 600
    while time.time() < deadline:
        polls.append(poll())
        status = json.loads(kubectl("-n", NAMESPACE, "get", "job", name, "-o", "json")).get("status", {})
        if status.get("succeeded") or status.get("failed"):
            outcome = "succeeded" if status.get("succeeded") else "failed"
            break
        time.sleep(POLL_S)
    log = kubectl("-n", NAMESPACE, "logs", f"job/{name}", check=False)
    if outcome == "timeout":   # a load still running would load the next run
        kubectl("-n", NAMESPACE, "delete", "job", name, "--wait=true", check=False)
    try:
        k6, k6_error = parse_k6(log), ""
    except (ValueError, KeyError) as err:   # no summary block: the run is not a measurement
        k6, k6_error = None, f"{type(err).__name__}: {err}"[:300]
    plant = json.loads(calibration.read_text(encoding="utf-8"))
    rec = {"arm": arm, "target": target, "regime": {k: v for k, v in regime.items() if k != "rates"},
           "start_unix": t0, "end_unix": time.time(), "k6_job": outcome, "k6": k6, "k6_error": k6_error,
           "power": {"start": power_start, "end": on_ac_power()},
           "watch": watch_record(polls, plant.get("price_core_hr", 0.526 / 4))}
    if arm in ("A3", "A4"):
        decisions = [json.loads(line) for line in kubectl("-n", NAMESPACE, "logs", "deployment/planner").splitlines()
                     if line.startswith("{")]
        rec["decisions"] = [d for d in decisions if d.get("event") == "decision"]
        rec["holds"] = [d for d in decisions if d.get("event") == "hold"]   # before a full window of load
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{arm}_{int(t0)}.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return rec


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--arm", required=True, choices=("A1", "A2", "A3", "A4"))
    r.add_argument("--target", type=float, required=True)
    r.add_argument("--regime", required=True, help="shape:sigma:seed, e.g. step_up:0.2:7")
    r.add_argument("--calibration", type=Path, required=True)
    r.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    import service_plant

    shape, sigma, seed = args.regime.split(":")
    plant = service_plant.from_calibration(args.calibration)
    reg = schedule(shape, float(sigma), int(seed), plant.slo_s, plant)
    rec = run(args.arm, args.target, reg, args.calibration, args.out)
    print(json.dumps({"arm": rec["arm"], "k6_job": rec["k6_job"], "cost": rec["watch"]["cost"],
                      "attainment": rec["k6"]["attainment"] if rec["k6"] else None}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
