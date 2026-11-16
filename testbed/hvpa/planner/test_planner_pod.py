"""The planner pod against a fake Kubernetes API: what it reads, what it patches, in which order."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "research" / "hvpa"))

import lattice  # noqa: E402
import planner_pod as pp  # noqa: E402
from service_plant import Plant  # noqa: E402


def test_cpu_quantities():
    assert pp.cpu_cores("500m") == 0.5 and pp.cpu_cores("1") == 1.0 and pp.cpu_cores("1.5") == 1.5
    assert pp.cpu_quantity(0.25) == "250m" and pp.cpu_quantity(2.0) == "2000m"


class FakeKube:
    def __init__(self, replicas: int, cpu: float) -> None:
        self.n, self.cpu, self.calls = replicas, {f"p{i}": cpu for i in range(replicas)}, []

    def replicas(self) -> int:
        return self.n

    def pods(self) -> list[dict]:
        return [{"metadata": {"name": k}, "spec": {"containers": [
            {"name": "cpuburn", "resources": {"requests": {"cpu": pp.cpu_quantity(v)}}}]}} for k, v in self.cpu.items()]

    def scale(self, n: int) -> int:
        self.calls.append(("scale", n))
        for i in range(self.n, n):
            self.cpu[f"p{i}"] = 0.5          # the template's CPU
        for i in range(n, self.n):
            self.cpu.pop(f"p{i}", None)
        self.n = n
        return 200

    def resize(self, pod: str, cores: float) -> int:
        self.calls.append(("resize", pod, cores))
        self.cpu[pod] = cores
        return 200


def _pod(kube, rate, joint=True, width=2, warmup_s=0.0):
    logs = []
    return pp.PlannerPod(kube, Plant(), width, joint, lambda: rate, logs.append, warmup_s=warmup_s), logs


def test_a_decision_is_the_shared_lattice_decision_and_is_logged():
    kube = FakeKube(2, 0.5)
    pod, logs = _pod(kube, 40.0)
    rec = pod.decide_and_act(0.0)
    expect, info = lattice.decide(Plant(), 40.0, 2, 0.5, 2, True)
    assert tuple(rec["next"]) == expect and rec["index"] == info["index"] and logs == [rec]
    assert kube.n == expect[0] and set(kube.cpu.values()) == {expect[1]}


def test_cpu_is_freed_first_when_shrinking_pods_and_scaling_out():
    kube = FakeKube(2, 2.0)
    pod, _ = _pod(kube, 1.0)      # load seen (a zero rate holds); the move itself is forced below
    pod.target_cpu = 2.0
    pod.kube.calls.clear()
    # force a move that lowers CPU and adds pods: resizes must come before the scale
    orig = lattice.decide
    lattice.decide = lambda *a, **k: ((4, 1.0), {"index": 0.0, "perknob_fits": True, "loss": 0.0})
    try:
        pod.decide_and_act(0.0)
    finally:
        lattice.decide = orig
    scale_at = next(i for i, c in enumerate(kube.calls) if c[0] == "scale")
    shrink = [i for i, c in enumerate(kube.calls) if c[0] == "resize" and c[1] in ("p0", "p1")]
    assert shrink and max(shrink) < scale_at                 # the old pods shrink before pods are added
    assert set(kube.cpu.values()) == {1.0} and kube.n == 4   # the new ones are then brought to 1 core


def test_reconcile_brings_new_pods_to_the_target():
    kube = FakeKube(3, 1.0)
    kube.cpu["p2"] = 0.5          # a new pod at the template's CPU
    pod, logs = _pod(kube, 10.0)
    pod.target_cpu = 1.0
    pod.reconcile(5.0)
    assert kube.cpu["p2"] == 1.0 and logs[-1]["event"] == "reconcile"
    pod.reconcile(10.0)            # nothing left to do: no log
    assert logs[-1]["t"] == 5.0


def test_the_rate_is_the_offered_one_counted_as_requests_arrive():
    assert "cpuburn_arrivals_total" in pp.RATE_QUERY and f"[{pp.RATE_WINDOW_S:g}s]" in pp.RATE_QUERY


def test_the_initial_allocation_holds_until_a_full_window_of_load_was_seen():
    """The twin's first decision sees the first interval's rate; live, the load
    starts after the planner, so a zero or part-window rate is not acted on."""
    kube, rates = FakeKube(2, 0.5), iter([0.0, 2.0, 9.0, 10.0])
    logs = []
    pod = pp.PlannerPod(kube, Plant(), 2, True, lambda: next(rates), logs.append)
    assert pod.warmup_s == pp.RATE_WINDOW_S
    for t in (0.0, 30.0):                     # no load yet, then load for part of the window
        assert pod.decide_and_act(t)["event"] == "hold"
    assert kube.calls == [] and [r["rate_rps"] for r in logs] == [0.0, 2.0]
    assert pod.decide_and_act(60.0)["event"] == "decision"   # a full window since the load was seen
    assert pod.decide_and_act(90.0)["event"] == "decision"
