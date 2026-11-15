"""WP17 step 3 from the host: the service's load curve at each CPU level (programme 2).

For CPU per pod in {0.25, 0.5, 1, 2} cores, one pod: the Deployment is set to
one replica at that CPU (requests = limits, so a rollout gives a clean pod),
then a Job runs measure/cpu_curve.py in the cluster (python image of WP10,
already side-loaded; the two scripts mounted from a ConfigMap; the load
generator held to 1 core, so it is not the bottleneck at these rates), and
its log is parsed into the two files research/plant2/fits.py reads, under
--out/c<level>/. research/hvpa/calibration.py fits the plant from them.

Then the horizontal points (added before any WP17 measurement: WP11 found
pods sub-linear and tiers coupled on this one host): the closed-loop rate
alone (cpu_curve.py --phases capacity) at r pods of c cores each, for every
REPLICA_POINTS pair whose r x c fits the node's free CPU, under
--out/replicas/r<R>c<M>m/. A Job that fails stops the calibration at once
with its log (it would otherwise be waited on to the timeout).

    python calibrate_service.py --out ../out/hvpa/curves [--level-seconds 60]
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
TESTBED = HERE.parent
CONTEXT, NAMESPACE = "kind-polyforge-testbed", "pf-hvpa"
LEVELS = (0.25, 0.5, 1.0, 2.0)
REPLICA_POINTS = ((2, 1.0), (4, 1.0), (5, 1.0), (6, 1.0), (8, 0.5), (2, 2.0), (3, 2.0))   # (pods, cores per pod)
RMAX = 8                       # research/hvpa/lattice.py
JOB_TIMEOUT_S, JOB_POLL_S = 5400.0, 10.0
PYTHON_IMAGE = "docker.io/library/python:3.13.17-slim-bookworm"
sys.path.insert(0, str(HERE / "measure"))

import cpu_curve  # noqa: E402


def kubectl(*args: str, stdin: str | None = None, timeout: float = 3600) -> str:
    proc = subprocess.run(["kubectl", "--context", CONTEXT, "-n", NAMESPACE, *args], input=stdin,
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args[:3])}: {proc.stderr.strip()[:400]}")
    return proc.stdout


def job_name(cpu: float, replicas: int = 1) -> str:
    return f"wp17-curve-c{int(cpu * 1000)}m" if replicas == 1 else f"wp17-cap-r{replicas}-c{int(cpu * 1000)}m"


def job(cpu: float, level_seconds: float, replicas: int = 1) -> dict:
    """The curve Job: the full protocol at one pod, the closed-loop rate alone at more."""
    name = job_name(cpu, replicas)
    extra = [] if replicas == 1 else ["--replicas", str(replicas), "--phases", "capacity"]
    return {"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": name, "namespace": NAMESPACE},
            "spec": {"backoffLimit": 0, "ttlSecondsAfterFinished": 86400, "template": {"spec": {
                "restartPolicy": "Never", "enableServiceLinks": False,
                "securityContext": {"runAsNonRoot": True, "runAsUser": 65532, "seccompProfile": {"type": "RuntimeDefault"}},
                "containers": [{
                    "name": "curve", "image": PYTHON_IMAGE, "imagePullPolicy": "Never",
                    "command": ["python", "-u", "/m/cpu_curve.py", "--base-url", f"http://cpuburn.{NAMESPACE}",
                                "--cpu", f"{cpu:g}", "--level-seconds", f"{level_seconds:g}", *extra],
                    "resources": {"requests": {"cpu": "1", "memory": "256Mi"}, "limits": {"cpu": "1", "memory": "256Mi"}},
                    "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                        "capabilities": {"drop": ["ALL"]}},
                    "volumeMounts": [{"name": "m", "mountPath": "/m", "readOnly": True},
                                     {"name": "tmp", "mountPath": "/tmp"}]}],
                "volumes": [{"name": "m", "configMap": {"name": "wp17-measure"}}, {"name": "tmp", "emptyDir": {}}]}}}}


LIVE_RESERVE = 1.25   # the live runs' k6 Job (1 core) and planner pod (0.25 core)


def free_cpu(node: dict, pods: list[dict]) -> dict:
    """Cores the node leaves for the service: allocatable CPU minus the requests
    of every running pod except the service and the calibration's own Jobs,
    minus LIVE_RESERVE. The twin's node size (calibration.py --node-cpu-file)."""
    alloc = cpu_cores(node["status"]["allocatable"]["cpu"])
    others = []
    for p in pods:
        labels = p["metadata"].get("labels") or {}
        if p.get("status", {}).get("phase") in ("Succeeded", "Failed") or labels.get("app.kubernetes.io/name") == "cpuburn" \
                or str(labels.get("job-name", "")).startswith(("wp17-curve", "wp17-cap")):
            continue
        req = sum(cpu_cores(c.get("resources", {}).get("requests", {}).get("cpu", "0")) for c in p["spec"]["containers"])
        if req:
            others.append({"pod": f"{p['metadata']['namespace']}/{p['metadata']['name']}", "cpu": req})
    used = sum(o["cpu"] for o in others)
    return {"allocatable": alloc, "requested_by_others": used, "reserved_for_load_and_planner": LIVE_RESERVE,
            "node_cpu": alloc - used - LIVE_RESERVE, "others": others}


def cpu_cores(q: str) -> float:
    q = str(q)
    return float(q[:-1]) / 1000.0 if q.endswith("m") else float(q)


def set_level(cpu: float, replicas: int = 1) -> None:
    patch = {"spec": {"replicas": replicas, "template": {"spec": {"containers": [{
        "name": "cpuburn", "resources": {"requests": {"cpu": f"{cpu:g}"}, "limits": {"cpu": f"{cpu:g}"}}}]}}}}
    kubectl("patch", "deployment", "cpuburn", "--type", "strategic", "-p", json.dumps(patch))
    kubectl("rollout", "status", "deployment/cpuburn", "--timeout=300s")


def replica_points(node_cpu: float) -> list[tuple[int, float]]:
    """The horizontal points that fit the node's free CPU and the lattice."""
    return [(r, c) for r, c in REPLICA_POINTS if r <= RMAX and r * c <= node_cpu + 1e-9]


def wait_job(name: str, timeout_s: float = JOB_TIMEOUT_S, poll_s: float = JOB_POLL_S,
             sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None:
    """Until the Job succeeds; a failed Job raises at once with its log's tail."""
    deadline = clock() + timeout_s
    while clock() < deadline:
        status = json.loads(kubectl("get", f"job/{name}", "-o", "json")).get("status", {})
        if status.get("succeeded"):
            return
        if status.get("failed"):
            logs = subprocess.run(["kubectl", "--context", CONTEXT, "-n", NAMESPACE, "logs", f"job/{name}",
                                   "--tail", "20"], capture_output=True, text=True).stdout
            raise RuntimeError(f"job {name} failed: {logs.strip()[-1200:]}")
        sleep(poll_s)
    raise TimeoutError(f"job {name} did not finish within {timeout_s:.0f} s")


def curve_dir(out: Path, cpu: float, replicas: int = 1) -> Path:
    return out / f"c{int(cpu * 1000)}m" if replicas == 1 else out / "replicas" / f"r{replicas}c{int(cpu * 1000)}m"


def measure(cpu: float, level_seconds: float, out: Path, replicas: int = 1) -> Path:
    j = job(cpu, level_seconds, replicas)
    name = j["metadata"]["name"]
    subprocess.run(["kubectl", "--context", CONTEXT, "-n", NAMESPACE, "delete", "job", name, "--ignore-not-found"],
                   capture_output=True, text=True)
    kubectl("apply", "-f", "-", stdin=yaml.safe_dump(j))
    wait_job(name)
    meta, body = cpu_curve.parse(kubectl("logs", f"job/{name}"))
    d = curve_dir(out, cpu, replicas)
    d.mkdir(parents=True, exist_ok=True)
    (d / "load_curve_requests.csv").write_text(body, encoding="utf-8")
    (d / "load_curve_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return d


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--level-seconds", type=float, default=60.0)
    args = ap.parse_args(argv)
    cm = kubectl("create", "configmap", "wp17-measure", "--dry-run=client", "-o", "yaml",
                 f"--from-file={TESTBED / 'measure' / 'load_curve.py'}", f"--from-file={HERE / 'measure' / 'cpu_curve.py'}")
    kubectl("apply", "-f", "-", stdin=cm)
    node = json.loads(subprocess.run(["kubectl", "--context", CONTEXT, "get", "nodes", "-o", "json"],
                                     capture_output=True, text=True, check=True).stdout)["items"][0]
    pods = json.loads(subprocess.run(["kubectl", "--context", CONTEXT, "get", "pods", "-A", "-o", "json"],
                                     capture_output=True, text=True, check=True).stdout)["items"]
    args.out.mkdir(parents=True, exist_ok=True)
    rec = free_cpu(node, pods)
    (args.out / "node_cpu.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
    print(f"node leaves {rec['node_cpu']:.2f} cores for the service", flush=True)
    for cpu in LEVELS:
        if (curve_dir(args.out, cpu) / "load_curve_meta.json").exists():
            continue
        set_level(cpu)
        print(f"{cpu} cores -> {measure(cpu, args.level_seconds, args.out)}", flush=True)
    points = replica_points(rec["node_cpu"])
    skipped = [p for p in REPLICA_POINTS if p not in points]
    if skipped:
        print(f"horizontal points that do not fit {rec['node_cpu']:.2f} cores, not measured: {skipped}", flush=True)
    for r, cpu in points:
        if (curve_dir(args.out, cpu, r) / "load_curve_meta.json").exists():
            continue
        set_level(cpu, r)
        print(f"{r} x {cpu} cores -> {measure(cpu, args.level_seconds, args.out, r)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
