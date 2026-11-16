"""WP17 arms A3 and A4 in the cluster: the per-knob or joint planner as a pod
(programme 2). Standard library only (python image of WP10); the decision is
research/hvpa/lattice.py, the twin's own, on the plant research/hvpa/
calibration.py fitted (all mounted from a ConfigMap).

Every PLAN_S seconds: the request rate of the last 30 s from WP17's
Prometheus, the Deployment's replica count and its pods' CPU from the
Kubernetes API, then lattice.decide. The rate is the offered one, requests
counted as they arrive (the twin's planner sees the offered rate; completed
requests would cap at what saturated pods serve). Until a full rate window of
load has been seen, the initial allocation holds: the twin's first decision
sees the first interval's rate, while a live run's load starts after the
planner, so a zero or part-window reading is logged as a hold, not acted on. Acting frees CPU first, as the twin does:
scale in before growing pods, shrink pods before scaling out. Replicas
through the Deployment's scale subresource; CPU through each pod's resize
subresource (in place, requests = limits). Between decisions, every
RECONCILE_S seconds, a pod whose CPU differs from the current target (a new
pod starts at the Deployment template's CPU) is resized to it. One JSON line
per decision and per resize on stdout, with the API's answers.

    python planner_pod.py --mode joint --width 2 --calibration /m/calibration.json
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

import lattice
import service_plant

SA = Path("/var/run/secrets/kubernetes.io/serviceaccount")
API = "https://kubernetes.default.svc"
DEPLOYMENT, CONTAINER, SELECTOR = "cpuburn", "cpuburn", "app.kubernetes.io/name=cpuburn"
RATE_WINDOW_S = 30.0
RATE_QUERY = f"sum(rate(cpuburn_arrivals_total[{RATE_WINDOW_S:g}s]))"
RECONCILE_S = 5.0


def cpu_cores(quantity: str) -> float:
    """A Kubernetes CPU quantity ("500m", "1", "1.5") in cores."""
    q = str(quantity).strip()
    return float(q[:-1]) / 1000.0 if q.endswith("m") else float(q)


def cpu_quantity(cores: float) -> str:
    return f"{int(round(cores * 1000))}m"


class Kube:
    """The few calls the planner makes, with the pod's service account."""

    def __init__(self, namespace: str, token: str, context: ssl.SSLContext | None, base: str = API,
                 opener: Callable = urllib.request.urlopen) -> None:
        self.ns, self.token, self.ctx, self.base, self.open = namespace, token, context, base, opener

    @classmethod
    def in_cluster(cls) -> "Kube":
        ctx = ssl.create_default_context(cafile=str(SA / "ca.crt"))
        return cls((SA / "namespace").read_text().strip(), (SA / "token").read_text().strip(), ctx)

    def call(self, method: str, path: str, body: dict | None = None,
             content_type: str = "application/merge-patch+json") -> tuple[int, dict]:
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {self.token}", "Content-Type": content_type,
                                              "Accept": "application/json"})
        try:
            with self.open(req, timeout=15, context=self.ctx) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as err:
            return err.code, {"error": err.read()[:300].decode("utf-8", "replace")}

    def replicas(self) -> int:
        _, body = self.call("GET", f"/apis/apps/v1/namespaces/{self.ns}/deployments/{DEPLOYMENT}/scale")
        return int(body["spec"]["replicas"])

    def pods(self) -> list[dict]:
        q = urllib.parse.urlencode({"labelSelector": SELECTOR})
        _, body = self.call("GET", f"/api/v1/namespaces/{self.ns}/pods?{q}")
        return [p for p in body.get("items", []) if not p["metadata"].get("deletionTimestamp")]

    def scale(self, n: int) -> int:
        status, _ = self.call("PATCH", f"/apis/apps/v1/namespaces/{self.ns}/deployments/{DEPLOYMENT}/scale",
                              {"spec": {"replicas": n}})
        return status

    def resize(self, pod: str, cores: float) -> int:
        q = cpu_quantity(cores)
        body = {"spec": {"containers": [{"name": CONTAINER, "resources": {"requests": {"cpu": q}, "limits": {"cpu": q}}}]}}
        status, _ = self.call("PATCH", f"/api/v1/namespaces/{self.ns}/pods/{pod}/resize", body,
                              "application/strategic-merge-patch+json")
        return status


def pod_cpu(pod: dict) -> float:
    c = next(c for c in pod["spec"]["containers"] if c["name"] == CONTAINER)
    return cpu_cores(c["resources"]["requests"]["cpu"])


def prometheus_rate(base: str, opener: Callable = urllib.request.urlopen) -> float:
    url = f"{base.rstrip('/')}/api/v1/query?" + urllib.parse.urlencode({"query": RATE_QUERY})
    with opener(url, timeout=10) as resp:
        result = json.loads(resp.read())["data"]["result"]
    return float(result[0]["value"][1]) if result else 0.0


class PlannerPod:
    def __init__(self, kube: Kube, plant, width: int, joint: bool, rate: Callable[[], float],
                 log: Callable[[dict], None], warmup_s: float = RATE_WINDOW_S) -> None:
        self.kube, self.plant, self.width, self.joint, self.rate, self.log = kube, plant, width, joint, rate, log
        self.target_cpu: float | None = None
        self.warmup_s = warmup_s
        self.load_seen_at: float | None = None

    def holding(self, now: float, rate: float) -> bool:
        """True until warmup_s have passed since the first reading with load in it."""
        if self.load_seen_at is None and rate > 0:
            self.load_seen_at = now
        return self.load_seen_at is None or now - self.load_seen_at < self.warmup_s

    def resize_all(self, cores: float, pods: list[dict]) -> list[tuple[str, int]]:
        return [(p["metadata"]["name"], self.kube.resize(p["metadata"]["name"], cores))
                for p in pods if abs(pod_cpu(p) - cores) > 1e-9]

    def decide_and_act(self, now: float) -> dict:
        rate = self.rate()
        if self.holding(now, rate):
            rec = {"event": "hold", "t": now, "rate_rps": rate}
            self.log(rec)
            return rec
        pods = self.kube.pods()
        r = self.kube.replicas()
        c = self.target_cpu if self.target_cpu is not None else (pod_cpu(pods[0]) if pods else 0.5)
        (nr, nc), info = lattice.decide(self.plant, rate, r, c, self.width, self.joint)
        acts = []
        if nr < r:
            acts.append(("scale", nr, self.kube.scale(nr)))
        if nc < c:
            acts += [("resize", name, st) for name, st in self.resize_all(nc, pods)]
        if nr > r:
            acts.append(("scale", nr, self.kube.scale(nr)))
        if nc > c:
            acts += [("resize", name, st) for name, st in self.resize_all(nc, pods)]
        if nr != r:   # pods the scale created start at the template's CPU: bring them to the target now
            acts += [("resize", name, st) for name, st in self.resize_all(nc, self.kube.pods())]
        self.target_cpu = nc
        rec = {"event": "decision", "t": now, "rate_rps": rate, "replicas": r, "cpu": c, "next": [nr, nc],
               "acts": acts, **info}
        self.log(rec)
        return rec

    def reconcile(self, now: float) -> None:
        if self.target_cpu is None:
            return
        done = self.resize_all(self.target_cpu, self.kube.pods())
        if done:
            self.log({"event": "reconcile", "t": now, "cpu": self.target_cpu, "resized": done})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mode", choices=("joint", "perknob"), required=True)
    ap.add_argument("--width", type=int, choices=(1, 2, 4), required=True)
    ap.add_argument("--calibration", type=Path, required=True)
    ap.add_argument("--prometheus", default="http://prometheus.pf-hvpa:9090")
    args = ap.parse_args(argv)
    plant = service_plant.from_calibration(args.calibration)
    pod = PlannerPod(Kube.in_cluster(), plant, args.width, args.mode == "joint",
                     lambda: prometheus_rate(args.prometheus),
                     lambda rec: print(json.dumps(rec), flush=True))
    next_plan = time.time()
    while True:
        now = time.time()
        if now >= next_plan:
            pod.decide_and_act(now)
            next_plan += lattice.PLAN_S
        else:
            pod.reconcile(now)
        time.sleep(min(RECONCILE_S, max(0.0, next_plan - time.time())))


if __name__ == "__main__":
    sys.exit(main())
