"""The WP17 live runner's pure parts: schedule, Job, k6 summary, watch, ABBA."""

import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import live  # noqa: E402


def test_the_schedule_is_the_twins_draw_and_reproducible():
    sys.path.insert(0, str(live.REPO / "research" / "hvpa"))
    from service_plant import Plant

    a = live.schedule("step_up", 0.2, 3, 0.5, Plant())
    b = live.schedule("step_up", 0.2, 3, 0.5, Plant())
    assert a == b and len(a["rates"]) == 360 and a["step_s"] == 10 and a["slo_s"] == 0.5
    assert live.schedule("step_up", 0.2, 4, 0.5, Plant())["rates"] != a["rates"]


def test_the_k6_job_runs_the_script_without_usage_report_on_one_core():
    c = live.k6_job("x")["spec"]["template"]["spec"]["containers"][0]
    assert c["image"] == live.K6_IMAGE and c["imagePullPolicy"] == "Never"
    assert "--no-usage-report" in c["command"] and c["resources"]["limits"]["cpu"] == "1"
    assert {"name": "K6_NO_USAGE_REPORT", "value": "true"} in c["env"]


def test_k6_summary_counts_dropped_requests_as_late():
    summary = {"metrics": {"wp17_on_time": {"values": {"rate": 0.9, "passes": 90, "fails": 10}},
                           "iterations": {"values": {"count": 100}},
                           "dropped_iterations": {"values": {"count": 20}},
                           "wp17_latency": {"values": {"avg": 12.0, "p(95)": 40.0}}}}
    log = f"noise\n{live.BEGIN}\n{json.dumps(summary)}\n{live.END}\n"
    k = live.parse_k6(log)
    assert k["requests"] == 120 and k["attainment"] == pytest.approx(90 / 120) and k["latency_ms"]["p(95)"] == 40.0


def test_watch_integrates_cost_and_counts_resizes_and_reversals():
    pods = lambda *cpus: [{"name": f"p{i}", "cpu": c, "scheduled": True, "ready": True} for i, c in enumerate(cpus)]  # noqa: E731
    polls = [{"t": 0.0, "pods": pods(0.5, 0.5)}, {"t": 10.0, "pods": pods(1.0, 1.0, 1.0)},
             {"t": 20.0, "pods": pods(1.0, 1.0)}, {"t": 30.0, "pods": pods(1.0, 1.0, 1.0)}]
    w = live.watch_record(polls, price_core_hr=3600.0)
    assert w["cost"] == pytest.approx(1.0 * 10 + 3.0 * 10 + 2.0 * 10)
    assert w["resizes"] == 2 and w["reversals"] == 2 and w["max_replicas"] == 3
    assert w["max_gap_s"] == 10.0                       # the host-stall check reads it


def test_abba_order_and_quantities():
    assert live.abba("A4", "A3", 2) == ["A4", "A3", "A3", "A4"]
    assert live.abba("A4", "A1", 4) == ["A4", "A1", "A1", "A4", "A4", "A1", "A1", "A4"]
    assert live.cpu_cores("250m") == 0.25 and live.cpu_cores("2") == 2.0
    assert math.isnan(live.parse_k6(f"{live.BEGIN}\n" + json.dumps({"metrics": {"wp17_on_time": {"values": {}}}})
                                    + f"\n{live.END}")["attainment"])
