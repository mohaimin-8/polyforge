"""Unit tests of the WP10 replica and knob actuator (no cluster, no gateway)."""

from __future__ import annotations

import io
import json
import subprocess
import urllib.error
from pathlib import Path

import pytest

import actuate as act

BOUNDS = act.Bounds(namespace="pf-testbed", core_budget=5, tiers={
    "small": act.TierBounds("llama-small", 1, 3, 1),
    "mid": act.TierBounds("llama-mid", 1, 2, 2),
})


class FakeKube:
    """Deployments whose ready count reaches spec after `lag` polls."""

    def __init__(self, spec: dict[str, int], lag: int = 0) -> None:
        self.spec = dict(spec)
        self.ready = dict(spec)
        self.lag = lag
        self.polls: dict[str, int] = {}
        self.scaled: list[tuple[str, int]] = []

    def replicas(self, deployment: str) -> tuple[int, int]:
        n = self.polls.get(deployment, 0) + 1
        self.polls[deployment] = n
        if n > self.lag:
            self.ready[deployment] = self.spec[deployment]
        return self.spec[deployment], self.ready[deployment]

    def scale(self, deployment: str, replicas: int) -> None:
        self.scaled.append((deployment, replicas))
        self.spec[deployment] = replicas
        self.polls[deployment] = 0
        if replicas < self.ready[deployment]:
            self.ready[deployment] = replicas  # terminating pods leave the ready count at once


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += max(s, 0.0)


class FakeResponse(io.BytesIO):
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class FakeGateway:
    """The gateway's knob endpoint semantics (knobs.go putKnobs/getKnobs)."""

    def __init__(self) -> None:
        self.knobs: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []

    def __call__(self, req, timeout: float):
        tenant = req.full_url.split("/admin/tenants/")[1].split("/")[0]
        self.calls.append((req.get_method(), tenant))
        assert req.headers["X-polyforge-admin-key"] == "secret"
        if req.get_method() == "PUT":
            body = json.loads(req.data)
            self.knobs[tenant] = {"model_tier": body["model_tier"], "cache_size_mb": body["cache_size_mb"]}
            return FakeResponse(json.dumps({"tenant_id": tenant, **self.knobs[tenant]}).encode())
        if tenant not in self.knobs:
            raise urllib.error.HTTPError(req.full_url, 404, "not found", {}, io.BytesIO(b"{}"))
        return FakeResponse(json.dumps({"tenant_id": tenant, **self.knobs[tenant]}).encode())


def make(kube=None, gateway=None, clock=None):
    log: list[dict] = []
    knobs = act.GatewayKnobs("http://gw", "secret", opener=gateway) if gateway else None
    return act.Actuator(bounds=BOUNDS, kube=kube, knobs=knobs, log=log.append,
                        clock=clock or FakeClock()), log


def test_bounds_load_reads_the_shipped_file():
    bounds = act.Bounds.load(act.DEFAULT_BOUNDS)
    assert bounds.namespace == "pf-testbed"
    assert bounds.tiers["small"].deployment == "llama-small"
    assert bounds.core_budget >= bounds.tiers["small"].min + 2 * bounds.tiers["mid"].min


def test_bounds_rejects_inverted_band(tmp_path: Path):
    path = tmp_path / "b.json"
    path.write_text(json.dumps({"namespace": "x", "core_budget": 4,
                                "tiers": {"small": {"deployment": "d", "min": 3, "max": 1, "cores_per_pod": 1}}}))
    with pytest.raises(ValueError):
        act.Bounds.load(path)


@pytest.mark.parametrize("obj, message", [
    ({"replicas": {"large": 1}}, "unknown tier"),
    ({"replicas": {"small": -1}}, "non-negative"),
    ({"replicas": {"small": True}}, "non-negative"),
    ({"replicas": {"small": 1.5}}, "non-negative"),
    ({"bogus": 1}, "unknown decision fields"),
    ({"tenants": {"t00": {"model_tier": "large"}}}, "no tier backend"),
    ({"tenants": {"t00": {"cache_size_mb": -5}}}, "non-negative"),
    ({"tenants": {"t00": {"replicas": 2}}}, "knobs are"),
])
def test_parse_decision_rejects_malformed(obj, message):
    with pytest.raises(act.DecisionError, match=message):
        act.parse_decision(obj, BOUNDS)


def test_parse_decision_accepts_unpin_and_partial_knobs():
    d = act.parse_decision({"step": 3, "t_s": 30, "replicas": {"small": 2},
                            "tenants": {"t00": {"model_tier": "none"}, "t01": {"cache_size_mb": 0}}}, BOUNDS)
    assert d.step == 3 and d.t_s == 30.0 and d.replicas == {"small": 2}
    assert d.tenants["t00"] == act.Knobs(model_tier="none")
    assert d.tenants["t01"] == act.Knobs(cache_size_mb=0)


def test_clamp_keeps_unrequested_tiers_and_clamps_to_band():
    target, clamped = act.clamp_replicas({"small": 9}, {"small": 1, "mid": 1}, BOUNDS)
    assert target == {"small": 3, "mid": 1} and clamped == ["small"]
    target, clamped = act.clamp_replicas({"mid": 0}, {"small": 2, "mid": 2}, BOUNDS)
    assert target == {"small": 2, "mid": 1} and clamped == ["mid"]


def test_clamp_refuses_over_budget_rather_than_repairing():
    with pytest.raises(act.DecisionError, match="cores"):
        act.clamp_replicas({"small": 2, "mid": 2}, {"small": 1, "mid": 1}, BOUNDS)


def test_kubectl_kube_builds_commands_and_parses_status():
    calls = []

    def runner(args):
        calls.append(list(args))
        if "get" in args:
            out = json.dumps({"spec": {"replicas": 2}, "status": {"readyReplicas": 1}})
            return subprocess.CompletedProcess(args, 0, out, "")
        return subprocess.CompletedProcess(args, 0, "scaled", "")

    kube = act.KubectlKube("pf-testbed", context="kind-polyforge-testbed", run=runner)
    assert kube.replicas("llama-small") == (2, 1)
    kube.scale("llama-small", 3)
    assert calls[0][:4] == ["--context", "kind-polyforge-testbed", "--namespace", "pf-testbed"]
    assert calls[1][4:] == ["scale", "deployment/llama-small", "--replicas=3"]


def test_kubectl_kube_zero_ready_and_errors():
    ok = act.KubectlKube("ns", run=lambda a: subprocess.CompletedProcess(a, 0, json.dumps({"spec": {"replicas": 0}}), ""))
    assert ok.replicas("d") == (0, 0)
    bad = act.KubectlKube("ns", run=lambda a: subprocess.CompletedProcess(a, 1, "", "not found"))
    with pytest.raises(RuntimeError, match="not found"):
        bad.replicas("d")


def test_apply_scales_only_changed_tiers_and_logs_lag():
    kube = FakeKube({"llama-small": 1, "llama-mid": 1})
    clock = FakeClock(1000.0)
    actuator, log = make(kube=kube, clock=clock)
    record = actuator.apply(act.parse_decision({"step": 1, "replicas": {"small": 2, "mid": 1}}, BOUNDS),
                            due_unix=999.5)
    assert kube.scaled == [("llama-small", 2)]
    assert record["previous"] == {"small": 1, "mid": 1} and record["applied"] == {"small": 2, "mid": 1}
    assert record["lag_s"] == pytest.approx(0.5)
    assert log == [record]


def test_apply_writes_knobs_and_reads_them_back():
    gw = FakeGateway()
    actuator, _ = make(gateway=gw)
    record = actuator.apply(act.parse_decision(
        {"tenants": {"t00": {"model_tier": "mid", "cache_size_mb": 64}}}, BOUNDS))
    assert record["knobs"]["t00"] == {"ok": True, "model_tier": "mid", "cache_size_mb": 64}
    assert gw.calls == [("PUT", "t00"), ("GET", "t00")]
    # A later partial decision keeps the other knob.
    record = actuator.apply(act.parse_decision({"tenants": {"t00": {"cache_size_mb": 8}}}, BOUNDS))
    assert gw.knobs["t00"] == {"model_tier": "mid", "cache_size_mb": 8}
    assert record["errors"] == []


def test_partial_knobs_for_an_unknobbed_tenant_are_an_error_not_a_disabled_cache():
    gw = FakeGateway()
    actuator, _ = make(gateway=gw)
    record = actuator.apply(act.parse_decision({"tenants": {"t09": {"model_tier": "small"}}}, BOUNDS))
    assert "no knobs yet" in record["errors"][0]
    assert "t09" not in gw.knobs


def test_unpin_reads_back_as_accepted():
    gw = FakeGateway()
    actuator, _ = make(gateway=gw)
    record = actuator.apply(act.parse_decision(
        {"tenants": {"t00": {"model_tier": "none", "cache_size_mb": 0}}}, BOUNDS))
    assert record["knobs"]["t00"]["ok"] is True


def test_knobs_without_gateway_are_refused():
    actuator, _ = make(kube=FakeKube({"llama-small": 1, "llama-mid": 1}))
    with pytest.raises(act.DecisionError, match="knob actuation is off"):
        actuator.apply(act.parse_decision({"tenants": {"t00": {"model_tier": "small", "cache_size_mb": 1}}}, BOUNDS))


def test_replay_applies_on_schedule_watches_readiness_and_counts_refusals():
    kube = FakeKube({"llama-small": 1, "llama-mid": 1}, lag=3)
    clock = FakeClock(0.0)
    actuator, log = make(kube=kube, clock=clock)
    plan = [act.parse_decision(o, BOUNDS) for o in (
        {"step": 0, "t_s": 0, "replicas": {"small": 2}},
        {"step": 1, "t_s": 10, "replicas": {"small": 3, "mid": 2}},  # 7 cores: refused
        {"step": 2, "t_s": 20, "replicas": {"small": 1}},
    )]
    failed = act.replay(actuator, plan, start_unix=100.0, sleep=clock.sleep)
    assert failed == 1
    events = [(r["event"], r.get("step")) for r in log]
    assert events[0] == ("decision", 0)
    assert ("ready", 0) in events and ("refused", 1) in events and ("decision", 2) in events
    first = next(r for r in log if r["event"] == "decision")
    assert first["due_unix"] == 100.0 and first["applied_unix"] >= 100.0
    up = next(r for r in log if r["event"] == "ready" and r["step"] == 0)
    assert up["direction"] == "up" and up["after_s"] >= 2.0
    down = next(r for r in log if r["event"] == "ready" and r["step"] == 2)
    assert down["direction"] == "down" and down["replicas"] == 1


def test_replay_fail_fast_raises_on_refusal():
    kube = FakeKube({"llama-small": 1, "llama-mid": 1})
    clock = FakeClock(0.0)
    actuator, _ = make(kube=kube, clock=clock)
    plan = [act.parse_decision({"t_s": 0, "replicas": {"small": 3, "mid": 2}}, BOUNDS)]
    with pytest.raises(act.DecisionError):
        act.replay(actuator, plan, start_unix=0.0, sleep=clock.sleep, fail_fast=True)


def test_read_plan_sorts_and_reports_bad_lines(tmp_path: Path):
    good = tmp_path / "plan.jsonl"
    good.write_text('{"t_s": 20, "replicas": {"small": 1}}\n\n{"t_s": 0, "replicas": {"small": 2}}\n')
    assert [d.t_s for d in act.read_plan(good, BOUNDS)] == [0.0, 20.0]
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"t_s": 0, "replicas": {"huge": 1}}\n')
    with pytest.raises(act.DecisionError, match="bad.jsonl:1"):
        act.read_plan(bad, BOUNDS)


def test_example_plan_fits_the_shipped_bounds():
    bounds = act.Bounds.load(act.DEFAULT_BOUNDS)
    current = {t: tb.min for t, tb in bounds.tiers.items()}
    for decision in act.read_plan(act.HERE / "example_plan.jsonl", bounds):
        current, _ = act.clamp_replicas(decision.replicas, current, bounds)


def test_admin_key_comes_from_file_or_env_and_is_never_in_repr(tmp_path: Path, monkeypatch):
    key_file = tmp_path / "admin-key.txt"
    key_file.write_text("from-file\n")
    monkeypatch.delenv("PF_ADMIN_KEY", raising=False)
    assert act.admin_key(str(key_file)) == "from-file"
    monkeypatch.setenv("PF_ADMIN_KEY", "from-env")
    assert act.admin_key(None) == "from-env"
    monkeypatch.delenv("PF_ADMIN_KEY")
    with pytest.raises(SystemExit):
        act.admin_key(None)
    assert "from-file" not in repr(act.GatewayKnobs("http://gw", "from-file"))
