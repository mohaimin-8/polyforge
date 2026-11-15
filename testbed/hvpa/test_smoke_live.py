"""smoke_live.py: the check's schedule, its checks, and its gate on the committed predictions."""

import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import smoke_live as sm  # noqa: E402

BASE = 4.0
ACTS = [["scale", 3, 200], ["resize", "cpuburn-a", 200]]
GOOD = {"arm": "A4", "k6_job": "succeeded", "k6": {"requests": 900, "attainment": 0.9},
        "power": {"start": True, "end": True},
        "watch": {"cost": 0.01, "max_gap_s": 10.0, "max_replicas": 4, "resizes": 2},
        "decisions": [{"event": "decision", "rate_rps": 3.9, "acts": ACTS}] * 14}
A1_OBJECTS = {"hpa": [{"currentMetrics": [{"type": "Resource",
                                           "resource": {"name": "cpu", "current": {"averageUtilization": 85}}}]}],
              "vpa": [{"recommendation": {"containerRecommendations": [{"containerName": "cpuburn",
                                                                         "target": {"cpu": "600m"}}]}}]}
A1 = {**{k: v for k, v in GOOD.items() if k != "decisions"}, "arm": "A1", "objects": A1_OBJECTS}
PREDS = {"regimes": [{"width": 2, "sigma": 0.2, "gain_vs_A3": 0.3, "gain_vs_A1": 0.1}],
         "live_regimes": [{"width": 2, "sigma": 0.2}],
         "tuned_targets": {"0.2": {"cpu_default": 0.4, "rate_default": 0.5}}}


@pytest.fixture
def base_rate(monkeypatch):
    import workloads

    monkeypatch.setattr(workloads, "base_rps", lambda plant: BASE)


def test_the_schedule_is_low_high_low_at_the_twins_base_rate(base_rate):
    r = sm.smoke_regime(types.SimpleNamespace(slo_s=0.5))
    assert (r["step_s"], r["slo_s"], r["shape"], r["sigma"]) == (10.0, 0.5, "smoke", 0.0)
    assert r["rates"] == [BASE] * 12 + [3 * BASE] * 18 + [BASE] * 12


def test_a_working_planner_run_passes_and_each_fault_is_named():
    assert sm.checks(GOOD, BASE) == []
    assert sm.checks({**GOOD, "decisions": GOOD["decisions"][:3]}, BASE) == ["3 planner decisions, fewer than 6"]
    refused = GOOD["decisions"][:13] + [{"event": "decision", "rate_rps": 3.9, "acts": [["resize", "p", 403]]}]
    assert sm.checks({**GOOD, "decisions": refused}, BASE) == [
        "1 planner acts not answered 2xx, the first ['resize', 'p', 403]"]
    blind = [{**d, "rate_rps": 0.0} for d in GOOD["decisions"]]
    assert sm.checks({**GOOD, "decisions": blind}, BASE)[0].startswith("the planner read no rate near the load")
    still = {**GOOD, "watch": {**GOOD["watch"], "max_replicas": 2, "resizes": 0}}
    assert sm.checks(still, BASE)[0].startswith("the service never grew")
    assert sm.checks({**GOOD, "k6": {"requests": 0, "attainment": float("nan")}}, BASE) == ["k6 sent no request"]
    assert sm.checks({**GOOD, "power": {"start": True, "end": False}}, BASE) == ["on battery"]


def test_an_a1_run_needs_the_hpas_reading_and_a_vpa_recommendation():
    assert sm.checks(A1, BASE) == []
    blind = {**A1, "objects": {**A1_OBJECTS, "hpa": [{"currentMetrics": None}]}}
    assert sm.checks(blind, BASE) == ["the HPA read no CPU utilisation (metrics-server)"]
    cold = {**A1, "objects": {**A1_OBJECTS, "vpa": [{"conditions": []}]}}
    assert sm.checks(cold, BASE) == ["the VPA had no recommendation by the run's end"]
    unread = {**A1, "objects": {"error": "RuntimeError: kubectl get"}}
    assert sm.checks(unread, BASE) == ["the HPA's and the VPA's status could not be read (RuntimeError: kubectl get)"]


def test_a_run_that_raised_reports_only_its_error():
    assert sm.checks({"arm": "A3", "error": "RuntimeError: kubectl apply"}, BASE) == ["RuntimeError: kubectl apply"]


@pytest.fixture
def no_cluster(monkeypatch):
    """Any kubectl call the test did not stand in for fails instead of reaching the cluster."""
    import live

    def refuse(*args, **kwargs):
        raise AssertionError(f"kubectl {args} reached in a test")

    monkeypatch.setattr(live, "kubectl", refuse)
    monkeypatch.setattr(sm.run_pilot, "kubectl", refuse)
    monkeypatch.setattr(live, "run", lambda *a, **k: refuse("run"))
    monkeypatch.setattr(live, "reset", lambda: refuse("reset"))


def test_no_live_run_before_the_predictions_are_committed(monkeypatch, no_cluster):
    monkeypatch.setattr(sm.run_pilot, "committed_unchanged", lambda path: False)
    assert sm.main() == 3


def smoke_main(tmp_path, monkeypatch, fake_run, kubectl=lambda *args: None):
    import live
    import service_plant

    (tmp_path / "p.json").write_text(json.dumps(PREDS), encoding="utf-8")
    monkeypatch.setattr(sm.run_pilot, "committed_unchanged", lambda path: True)
    monkeypatch.setattr(sm.run_pilot, "PREDICTIONS", tmp_path / "p.json")
    monkeypatch.setattr(sm.run_pilot, "CALIBRATION", tmp_path / "cal.json")
    monkeypatch.setattr(sm.run_pilot, "kubectl", kubectl)
    monkeypatch.setattr(sm, "OUT", tmp_path / "smoke_runs")
    monkeypatch.setattr(sm, "SCRATCH", tmp_path / "scratch")
    monkeypatch.setattr(sm, "a1_objects", lambda: A1_OBJECTS)
    monkeypatch.setattr(service_plant, "from_calibration", lambda path: types.SimpleNamespace(slo_s=0.5))
    monkeypatch.setattr(live, "run", fake_run)
    resets = []
    monkeypatch.setattr(live, "reset", lambda: resets.append(1))
    rc = sm.main()
    return rc, (tmp_path / "smoke_runs" / "SMOKE.md").read_text(encoding="utf-8"), resets


def test_the_smoke_runs_each_pilot_arm_at_its_targets_then_resets(tmp_path, monkeypatch, base_rate, no_cluster):
    calls = []

    def fake_run(arm, target, regime, calibration, out):
        calls.append((arm, target, len(regime["rates"]), out.name))
        return dict(A1) if arm == "A1" else {**GOOD, "arm": arm}

    rc, text, resets = smoke_main(tmp_path, monkeypatch, fake_run)
    assert rc == 0 and resets == [1]
    assert calls == [("A4", 2.0, 42, "scratch"), ("A3", 2.0, 42, "scratch"), ("A1", 0.4, 42, "scratch")]
    assert "Every check passed." in text and "| A1 | 0.4 | succeeded | 900 | 0.900 | 4 | 2 | - | none |" in text
    assert json.loads((tmp_path / "smoke_runs" / "A4.json").read_text(encoding="utf-8"))["problems"] == []


def test_a_cluster_that_is_not_ready_runs_nothing_and_says_so(tmp_path, monkeypatch, base_rate, no_cluster):
    import subprocess

    def not_ready(*args):
        raise subprocess.CalledProcessError(1, "kubectl", stderr="timed out waiting for the condition\n")

    rc, text, resets = smoke_main(tmp_path, monkeypatch, lambda *a: pytest.fail("a run started"),
                                  kubectl=not_ready)
    assert rc == 1 and resets == []
    assert text == "# WP17 live smoke\n\nNot run: the cluster was not ready (timed out waiting for the condition).\n"


def test_a_failed_check_or_reset_fails_the_smoke_and_says_which(tmp_path, monkeypatch, base_rate, no_cluster):
    def fake_run(arm, target, regime, calibration, out):
        if arm == "A3":
            raise RuntimeError("kubectl apply: forbidden")
        return dict(A1) if arm == "A1" else {**GOOD, "arm": arm}

    rc, text, _ = smoke_main(tmp_path, monkeypatch, fake_run)
    assert rc == 1 and "Failed: A3." in text and "RuntimeError: kubectl apply: forbidden" in text
