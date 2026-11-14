"""The WP17 simulation twin: horizontal with vertical autoscaling (programme 2).

One Deployment of the CPU service (service_plant.py) on one node, 10 s steps.
Arms (plan WP17):
  A1  HPA on CPU utilisation (target 80 %, the autoscaling API's default) with
      the VPA on CPU: the pairing the VPA documentation warns against;
  A2  HPA on request rate per pod (as KEDA drives it; target = the rate one pod
      at the initial CPU serves at 80 %) with the VPA on CPU;
  A3  the per-knob planner over (replicas, CPU): each knob's best move from the
      current state, both applied as they are;
  A4  the joint planner over the same lattice.
References: the best static allocation and a per-step clairvoyant bound.

HPA: research/comparators/k8s_hpa.reconcile (tolerance 0.1, default
behaviour) at every 15 s sync; CPU utilisation = the ready pods' summed usage
over their summed requests (the replica calculator's ratio), desired from the
current replica count (unready-pod adjustments not modelled). VPA: vpa.py,
the recommender sampling every ready pod each minute, the updater each minute,
in place; new pods are admitted at the current target. KEDA polls the rate
every 30 s (its default pollingInterval).
Planners: every PLAN_S, the next interval's rate is predicted as the last one
seen and every lattice point is scored by the plant: J = cost + KAPPA x late
requests, the objective all arms are scored on. Lattice: replicas +-1 ... +-w
(w = 1, 2 or 4), CPU one level down, same or up (plant.cpu_levels), within
[RMIN, RMAX], each point fitting the node. Each decision logs the interaction
index (oscillation of the remainder over the box's feasible points) and the
one-step loss of the per-knob choice (research/theory/INDEX_THEORY.md); a
per-knob choice whose combined move does not fit the node is applied anyway
and counted (the node then leaves pods Pending or defers resizes).
Node: pods are scheduled in creation order while their requests fit NODE_CPU
(the rest stay Pending); a pod serves STARTUP_S after it is scheduled; an
in-place resize that does not fit is deferred and after 5 min the pod is
recreated at the new size (VPA docs/features.md), to wait for the scheduler.
Moves are ordered to free CPU first (scale in before growing pods, shrink pods
before scaling out). Cost = requested core-seconds of scheduled pods x price.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parents[0] / "comparators"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import vpa  # noqa: E402
from service_plant import Plant  # noqa: E402

DT_S = 10.0
PLAN_S = 30.0
HPA_SYNC_S = 15.0
KEDA_POLL_S = 30.0
STARTUP_S = 20.0
RESIZE_DEFER_LIMIT_S = 300.0
NODE_CPU = 6.0
RMIN, RMAX = 1, 8
KAPPA = 1e-4           # USD per late request
HPA_CPU_TARGET = 0.8
INITIAL = (2, 0.5)     # replicas, CPU per pod
EPS = 1e-9


def ticks(now: float, period: float) -> bool:
    """A periodic event at a multiple of `period` falls in [now, now + DT_S)."""
    return math.ceil(now / period - EPS) * period < now + DT_S - EPS


@dataclass(eq=False)
class Pod:
    cpu: float
    created_s: float
    scheduled_s: float | None = None
    resize_to: float | None = None
    resize_since: float | None = None

    def ready(self, now: float) -> bool:
        return self.scheduled_s is not None and now >= self.scheduled_s + STARTUP_S - EPS


@dataclass
class Cluster:
    pods: list = field(default_factory=list)
    resizes: int = 0
    recreations: int = 0
    pending_steps: int = 0

    def start(self, r: int, c: float) -> None:
        self.pods = [Pod(cpu=c, created_s=-STARTUP_S, scheduled_s=-STARTUP_S) for _ in range(r)]

    def scheduled_cpu(self) -> float:
        return sum(p.cpu for p in self.pods if p.scheduled_s is not None)

    def schedule(self, now: float) -> None:
        for p in self.pods:
            if p.scheduled_s is None and self.scheduled_cpu() + p.cpu <= NODE_CPU + EPS:
                p.scheduled_s = now

    def scale_to(self, n: int, cpu: float, now: float) -> None:
        while len(self.pods) < n:
            self.pods.append(Pod(cpu=cpu, created_s=now))
        while len(self.pods) > n:   # ReplicaSet deletes unscheduled, then unready, then the youngest first
            order = sorted(range(len(self.pods)), key=lambda i: (self.pods[i].scheduled_s is not None,
                                                                 self.pods[i].ready(now), -self.pods[i].created_s))
            self.pods.pop(order[0])
        self.schedule(now)

    def resize(self, pod: Pod, cpu: float, now: float) -> None:
        if abs(pod.cpu - cpu) < EPS:
            pod.resize_to = pod.resize_since = None
        elif pod.scheduled_s is None:
            pod.cpu = cpu
        elif self.scheduled_cpu() - pod.cpu + cpu <= NODE_CPU + EPS:
            pod.cpu, pod.resize_to, pod.resize_since = cpu, None, None
            self.resizes += 1
        elif pod.resize_to is None or abs(pod.resize_to - cpu) > EPS:
            pod.resize_to, pod.resize_since = cpu, now

    def tick(self, now: float) -> None:
        for i, p in enumerate(self.pods):
            if p.resize_to is not None and now - p.resize_since >= RESIZE_DEFER_LIMIT_S - EPS:
                self.pods[i] = Pod(cpu=p.resize_to, created_s=now)
                self.recreations += 1
        self.schedule(now)
        self.pending_steps += sum(1 for p in self.pods if p.scheduled_s is None)

    def serving(self, now: float) -> list[Pod]:
        return [p for p in self.pods if p.ready(now)]


class Static:
    def __init__(self, r: int, c: float) -> None:
        self.r, self.c, self.name = r, c, f"static_r{r}_c{c}"

    def start(self, cl: Cluster) -> None:
        cl.start(self.r, self.c)

    def act(self, cl: Cluster, now: float, seen_rate: float, usage: dict, plant: Plant) -> None:
        return


class HpaVpa:
    """`vpa_setting`: "default" (upstream flags) or "fast" (vpa.SETTINGS: a 10 min
    half-life and a 1 h confidence interval, so the VPA can act within the run)."""

    def __init__(self, metric: str, plant: Plant, vpa_setting: str = "default",
                 vpa_min_replicas: int = vpa.MIN_REPLICAS, target: float = HPA_CPU_TARGET) -> None:
        import k8s_hpa

        self.k8s_hpa, self.metric, self.target = k8s_hpa, metric, target
        self.name = ("A1_hpa_cpu_vpa" if metric == "cpu" else "A2_keda_rate_vpa") + f"_{vpa_setting}"
        self.history = k8s_hpa.PoolHistory()
        self.rec = vpa.Recommender(min_allowed=min(plant.cpu_levels), max_allowed=max(plant.cpu_levels),
                                   **vpa.SETTINGS[vpa_setting])
        self.vpa_min_replicas = vpa_min_replicas
        self.target_rps = target * plant.capacity_rps(INITIAL[1])
        self.keda_rate = 0.0

    def start(self, cl: Cluster) -> None:
        cl.start(*INITIAL)

    def act(self, cl: Cluster, now: float, seen_rate: float, usage: dict, plant: Plant) -> None:
        if ticks(now, KEDA_POLL_S):
            self.keda_rate = seen_rate
        if ticks(now, vpa.UPDATER_INTERVAL_S):
            for p in cl.pods:
                if p in usage:
                    self.rec.add_sample(usage[p], now)
            rec = self.rec.recommend()
            for i in vpa.pods_to_update([p.cpu for p in cl.pods], [now - p.created_s for p in cl.pods], rec,
                                        self.vpa_min_replicas):
                cl.resize(cl.pods[i], rec.target, now)
        if ticks(now, HPA_SYNC_S):
            current = len(cl.pods)
            measured = [p for p in cl.pods if p in usage]
            if self.metric == "cpu":
                req = sum(p.cpu for p in measured)
                ratio = (sum(usage[p] for p in measured) / req / self.target) if req > 0 else 1.0
            else:
                ratio = (self.keda_rate / max(1, current)) / self.target_rps
            n, self.history = self.k8s_hpa.reconcile(self.history, math.ceil(now / HPA_SYNC_S - EPS) * HPA_SYNC_S,
                                                      current, ratio, RMIN, RMAX)
            rec = self.rec.recommend()
            cl.scale_to(n, rec.target if rec else INITIAL[1], now)


def lattice_j(plant: Plant, rate: float, r: int, c: float, seconds: float = PLAN_S) -> float:
    out = plant.step(rate, [c] * r, seconds)
    return out["cost"] + KAPPA * out["late"]


def fits(r: int, c: float) -> bool:
    return r * c <= NODE_CPU + EPS


class Planner:
    def __init__(self, joint: bool, width: int) -> None:
        self.joint, self.width = joint, width
        self.name = ("A4_joint" if joint else "A3_perknob") + f"_w{width}"
        self.log: list[dict] = []

    def start(self, cl: Cluster) -> None:
        cl.start(*INITIAL)

    def box(self, plant: Plant, r: int, c: float) -> tuple[list[int], list[float]]:
        levels = list(plant.cpu_levels)
        i = min(range(len(levels)), key=lambda k: abs(levels[k] - c))
        rs = [x for x in range(r - self.width, r + self.width + 1) if RMIN <= x <= RMAX]
        return rs, levels[max(0, i - 1):i + 2]

    def decide(self, plant: Plant, rate: float, r: int, c: float) -> tuple[tuple, dict]:
        rs, cs = self.box(plant, r, c)
        J = {(rr, cc): lattice_j(plant, rate, rr, cc) for rr in rs for cc in cs if fits(rr, cc)}
        if (r, c) not in J:
            J[(r, c)] = lattice_j(plant, rate, r, c)   # the current state is scored even if a resize left it over
        key = lambda rc: (J[rc], rc != (r, c), rc[0] * rc[1])  # noqa: E731 - ties: stay, then cheaper
        joint = min(J, key=key)
        best_r = min((k for k in J if k[1] == c), key=key)[0]
        best_c = min((k for k in J if k[0] == r), key=key)[1]
        perknob = (best_r, best_c)
        rem = [J[(rr, cc)] - J[(rr, c)] - J[(r, cc)] + J[(r, c)] for rr in rs for cc in cs
               if (rr, cc) in J and (rr, c) in J and (r, cc) in J]
        info = {"index": max(rem) - min(rem) if rem else math.nan, "perknob_fits": perknob in J,
                "loss": (J[perknob] - J[joint]) if perknob in J else math.nan}
        return (joint if self.joint else perknob), info

    def act(self, cl: Cluster, now: float, seen_rate: float, usage: dict, plant: Plant) -> None:
        if not ticks(now, PLAN_S) or not cl.pods:
            return
        r, c = len(cl.pods), cl.pods[0].cpu
        (nr, nc), info = self.decide(plant, seen_rate, r, c)
        self.log.append({"t_s": now, **info})
        if nr < r:
            cl.scale_to(nr, nc, now)
        if nc < c:
            for p in list(cl.pods):
                cl.resize(p, nc, now)
        if nr > r:
            cl.scale_to(nr, nc, now)
        if nc > c:
            for p in list(cl.pods):
                cl.resize(p, nc, now)


def run(arm, plant: Plant, rates: np.ndarray) -> dict:
    cl = Cluster()
    arm.start(cl)
    totals = {"cost": 0.0, "late": 0.0, "requests": 0.0}
    seen, usage, last_n, last_dir, reversals = float(rates[0]), {}, len(cl.pods), 0, 0
    for k, rate in enumerate(rates):
        now = k * DT_S
        arm.act(cl, now, seen, usage, plant)
        cl.tick(now)
        serving = cl.serving(now)
        out = plant.step(float(rate), [p.cpu for p in serving], DT_S)
        totals["cost"] += cl.scheduled_cpu() * DT_S / 3600.0 * plant.price_core_hr
        totals["late"] += out["late"]
        totals["requests"] += out["requests"]
        usage = dict(zip(serving, out["usage"]))
        seen = float(rate)
        n = len(cl.pods)
        if n != last_n:
            d = 1 if n > last_n else -1
            reversals += int(last_dir != 0 and d != last_dir)
            last_dir, last_n = d, n
    res = {"arm": arm.name, "J": totals["cost"] + KAPPA * totals["late"], "cost": totals["cost"],
           "attainment": 1.0 - totals["late"] / totals["requests"], "resizes": cl.resizes,
           "recreations": cl.recreations, "reversals": reversals, "pending_pod_steps": cl.pending_steps}
    if isinstance(arm, Planner) and arm.log:
        idx = [d["index"] for d in arm.log if math.isfinite(d["index"])]
        loss = [d["loss"] for d in arm.log if math.isfinite(d["loss"])]
        res.update(decisions=len(arm.log), mean_index=float(np.mean(idx)) if idx else math.nan,
                   mean_loss=float(np.mean(loss)) if loss else math.nan,
                   loss_share=float(np.mean([x > 1e-12 for x in loss])) if loss else math.nan,
                   perknob_overflows=sum(1 for d in arm.log if not d["perknob_fits"]))
    return res


def best_static(plant: Plant, rates: np.ndarray) -> dict:
    runs = [run(Static(r, c), plant, rates) for r in range(RMIN, RMAX + 1) for c in plant.cpu_levels if fits(r, c)]
    return min(runs, key=lambda x: x["J"])


def clairvoyant(plant: Plant, rates: np.ndarray) -> float:
    """Sum over steps of the best fitting allocation for that step's known
    rate, with no actuation delay: a lower bound on every arm's J."""
    points = [(r, c) for r in range(RMIN, RMAX + 1) for c in plant.cpu_levels if fits(r, c)]
    return float(sum(min(lattice_j(plant, float(rate), r, c, DT_S) for r, c in points) for rate in rates))
