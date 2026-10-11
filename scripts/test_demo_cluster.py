"""demo_cluster.py drives kind, helm and the harness; its link verdicts are tested here.

Run: python -m pytest scripts/test_demo_cluster.py -q
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "demo_cluster", Path(__file__).resolve().parent / "demo_cluster.py")
dc = importlib.util.module_from_spec(spec)
sys.modules["demo_cluster"] = dc  # @dataclass resolves its module here
spec.loader.exec_module(dc)


def _deploys(**ready):
    return {"items": [{"metadata": {"name": n}, "spec": {"replicas": s}, "status": {"readyReplicas": r}}
                      for n, (s, r) in ready.items()]}


def _policy(tenant, replicas=2, cache=128, tier="small", source="manual", applied="True"):
    return {"spec": {"tenantRef": tenant, "replicas": replicas, "cacheSizeMB": cache,
                     "modelTier": tier},
            "status": {"lastPlanSource": source,
                       "conditions": [{"type": "Applied", "status": applied}]}}


ALL_UP = {name: (1, 1) for name in dc.COMPONENTS} | {"polyforge-control-plane": (4, 4)}


def test_deployments_and_policies_parse():
    deps = dc.deployments(_deploys(**{"postgres": (1, 1), "nats": (1, None)}))
    assert deps == {"postgres": (1, 1), "nats": (1, 0)}
    rows = dc.policies({"items": [_policy("t00", source="planner")]})
    assert rows == [dc.Policy("t00", 2, 128, "small", "planner", True)]


def test_links_pass_in_the_order_the_loop_comes_up():
    t = dc.LinkTracker()
    t.update(dc.deployments(_deploys(**ALL_UP)), [], None)
    assert t.status("layers") == "PASS" and t.status("applied") == "FAIL"
    manual = [_policy("t00"), _policy("t01")]
    t.update(dc.deployments(_deploys(**ALL_UP)), dc.policies({"items": manual}), 0)
    assert t.status("applied") == "PASS" and t.status("telemetry") == "FAIL"
    assert t.status("replicas") == "PASS"  # 2 + 2 == 4 ready control-plane pods
    planned = [_policy("t00", cache=512, source="planner"), _policy("t01", source="planner")]
    t.update(dc.deployments(_deploys(**ALL_UP)), dc.policies({"items": planned}), 37)
    assert t.status("telemetry") == "PASS" and t.status("planner") == "PASS"
    assert t.status("acting") == "PASS"
    assert "t00" in t.detail("acting") and "512" in t.detail("acting")


def test_a_planner_that_holds_every_knob_is_a_warning_not_a_failure():
    t = dc.LinkTracker()
    held = [_policy("t00", source="planner")]
    for _ in range(3):
        t.update(dc.deployments(_deploys(**ALL_UP)), dc.policies({"items": held}), 10)
    assert t.status("acting") == "WARN"


def test_replicas_link_needs_the_deployment_to_match_the_policies():
    t = dc.LinkTracker()
    t.update(dc.deployments(_deploys(**{"polyforge-control-plane": (5, 5)})),
             dc.policies({"items": [_policy("t00"), _policy("t01")]}), None)
    assert t.status("replicas") == "FAIL"


def test_run_log_gives_the_audit_count_and_the_verdict():
    log = '[audit] 256 record(s) on the backbone\n{\n  "valid_runs": 1,\n  "ok": true\n}\n'
    assert dc.parse_run_log(log) == (256, True)
    assert dc.parse_run_log('{"ok": false}') == (None, False)
    t = dc.LinkTracker()
    t.finish(log)
    assert t.status("audit") == "PASS"
    t.finish('[audit] 0 record(s) on the backbone\n"ok": true')
    assert t.status("audit") == "FAIL"


def test_every_link_is_reported_once_in_order():
    rows = dc.LinkTracker().rows()
    assert [key for key, *_ in rows] == [key for key, _ in dc.LINKS]
    assert all(status == "FAIL" for _, _, status, _ in rows)
