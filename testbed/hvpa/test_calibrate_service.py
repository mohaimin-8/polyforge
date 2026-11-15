"""The WP17 step-3 Job: what runs where, with which limits."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import calibrate_service as cal  # noqa: E402


def test_the_job_runs_the_curve_tool_on_one_core_against_the_service():
    j = cal.job(0.25, 60)
    c = j["spec"]["template"]["spec"]["containers"][0]
    assert j["metadata"]["name"] == "wp17-curve-c250m"
    assert c["image"] == cal.PYTHON_IMAGE and c["imagePullPolicy"] == "Never"
    assert c["command"][2:] == ["/m/cpu_curve.py", "--base-url", "http://cpuburn.pf-hvpa", "--cpu", "0.25",
                                "--level-seconds", "60"]
    assert c["resources"]["limits"]["cpu"] == "1" and c["resources"]["requests"]["cpu"] == "1"
    assert j["spec"]["backoffLimit"] == 0


def test_the_levels_are_the_plans():
    assert cal.LEVELS == (0.25, 0.5, 1.0, 2.0)


def test_the_python_image_is_the_one_wp10_side_loads():
    lock = (Path(__file__).resolve().parents[1] / "images.lock").read_text(encoding="utf-8")
    repo, tag = cal.PYTHON_IMAGE.rsplit(":", 1)
    assert f"{repo}\t{tag}\t" in lock


def test_free_cpu_counts_everything_but_the_service_and_its_jobs():
    def pod(ns, name, cpu, labels=None, phase="Running"):
        return {"metadata": {"namespace": ns, "name": name, "labels": labels or {}}, "status": {"phase": phase},
                "spec": {"containers": [{"resources": {"requests": {"cpu": cpu}}}]}}

    node = {"status": {"allocatable": {"cpu": "8"}}}
    pods = [pod("kube-system", "etcd", "100m"), pod("kube-system", "api", "250m"),
            pod("pf-hvpa", "cpuburn-1", "2", {"app.kubernetes.io/name": "cpuburn"}),
            pod("pf-hvpa", "wp17-curve-c250m-x", "1", {"job-name": "wp17-curve-c250m"}),
            pod("pf-testbed", "old-job", "1", phase="Succeeded"), pod("keda", "operator", "1")]
    rec = cal.free_cpu(node, pods)
    assert rec["requested_by_others"] == pytest.approx(1.35)
    assert rec["node_cpu"] == pytest.approx(8 - 1.35 - 1.25)
    pods.append(pod("pf-hvpa", "wp17-cap-r4-c1000m-x", "1", {"job-name": "wp17-cap-r4-c1000m"}))
    assert cal.free_cpu(node, pods)["requested_by_others"] == pytest.approx(1.35)


def test_a_horizontal_point_measures_the_closed_loop_rate_alone_at_its_pods():
    j = cal.job(1.0, 60, replicas=4)
    c = j["spec"]["template"]["spec"]["containers"][0]
    assert j["metadata"]["name"] == "wp17-cap-r4-c1000m"
    assert c["command"][-4:] == ["--replicas", "4", "--phases", "capacity"]
    assert cal.curve_dir(Path("o"), 1.0, 4) == Path("o") / "replicas" / "r4c1000m"
    assert cal.curve_dir(Path("o"), 0.25) == Path("o") / "c250m"


def test_only_the_horizontal_points_that_fit_the_node_are_measured():
    assert cal.replica_points(6.0) == list(cal.REPLICA_POINTS)
    assert cal.replica_points(4.6) == [(2, 1.0), (4, 1.0), (8, 0.5), (2, 2.0)]
    assert all(r <= cal.RMAX for r, _ in cal.replica_points(100.0))


def test_a_failed_job_stops_at_once_and_a_finished_one_returns(monkeypatch):
    states = iter([{"status": {"active": 1}}, {"status": {"succeeded": 1}}])
    monkeypatch.setattr(cal, "kubectl", lambda *a, **k: __import__("json").dumps(next(states)))
    slept = []
    cal.wait_job("wp17-curve-c250m", sleep=slept.append, clock=lambda: 0.0)
    assert slept == [cal.JOB_POLL_S]
    monkeypatch.setattr(cal, "kubectl", lambda *a, **k: '{"status": {"failed": 1}}')
    monkeypatch.setattr(cal.subprocess, "run", lambda *a, **k: type("P", (), {"stdout": "Traceback: boom"})())
    with pytest.raises(RuntimeError, match="boom"):
        cal.wait_job("wp17-curve-c250m", sleep=slept.append, clock=lambda: 0.0)
    monkeypatch.setattr(cal, "kubectl", lambda *a, **k: '{"status": {"active": 1}}')
    ticks = iter([0.0, 0.0, 1e9])
    with pytest.raises(TimeoutError):
        cal.wait_job("x", sleep=lambda s: None, clock=lambda: next(ticks))


def test_the_curve_tool_passes_load_curve_only_arguments_it_accepts():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "measure"))
    sys.path.insert(0, str(Path(__file__).resolve().parent / "measure"))
    import cpu_curve
    import load_curve
    for cpu in cal.LEVELS:
        args = cpu_curve.argparse.Namespace(replicas=1, base_url="http://x", level_seconds=60.0, phases="all",
                                            quick=False, cpu=cpu)
        parsed = load_curve.parse_args(cpu_curve.protocol_args(args))   # exits on an argument it refuses
        assert parsed.phases == "all" and parsed.replicas == 1
    args = cpu_curve.argparse.Namespace(replicas=6, base_url="http://x", level_seconds=60.0, phases="capacity",
                                        quick=False, cpu=1.0)
    parsed = load_curve.parse_args(cpu_curve.protocol_args(args))
    assert parsed.phases == "capacity" and parsed.replicas == 6
