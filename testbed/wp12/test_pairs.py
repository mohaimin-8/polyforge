"""pairs.py without a cluster: order, measurement, validity, resumption, report."""

import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import pairs  # noqa: E402
import tb_planner as tbp  # noqa: E402

import cell_schedules as cs  # noqa: E402

TESTBED = {"tiers": {"small": {"cores_per_pod": 1}, "mid": {"cores_per_pod": 2}}, "pins": {"t00": "small", "t01": "mid"},
           "slo_ms": 9000.0, "price_core_hr": 0.36}
START = 1_000_000.0


def decision(t, applied, previous=None, step=None):
    return {"event": "decision", "step": step, "applied_unix": t, "applied": applied,
            "previous": previous or applied, "errors": []}


def test_pairs_are_abba_and_the_regimes_alternate():
    assert pairs.order(0) == ("joint", "per_knob") and pairs.order(1) == ("per_knob", "joint")
    assert pairs.run_order(("low", "high"), 3) == [("low", 0), ("high", 0), ("high", 1), ("low", 1), ("low", 2),
                                                    ("high", 2)]


def test_cost_is_the_cores_held_over_the_scored_window():
    records = [decision(START - 100, {"small": 1, "mid": 1}),
               {"event": "decision", "applied_unix": START - 50, "errors": [], "knobs": {}},   # knobs only
               decision(START + 200, {"small": 3, "mid": 1}, {"small": 1, "mid": 1}),
               decision(START + 400, {"small": 1, "mid": 2}, {"small": 3, "mid": 1})]
    timeline = pairs.cores_timeline(records, {"small": 1, "mid": 2})
    assert timeline == [(START - 100, 3), (START + 200, 5), (START + 400, 5)]
    cost = pairs.held_cost(timeline, START + 120, START + 600, 0.36)
    assert cost == pytest.approx((3 * 80 + 5 * 200 + 5 * 200) / 3600 * 0.36)
    with pytest.raises(ValueError):
        pairs.held_cost(timeline, START - 200, START + 600, 0.36)


def table_and_samples():
    table, samples = [], []
    for i in range(40):
        t = 5.0 + 15.0 * i
        tenant = "t00" if i % 2 == 0 else "t01"
        table.append({"seq": i, "t": t, "tenant": tenant, "warmup": t < cs.WARMUP_S})
        if i == 30:
            continue                                                   # no sample: late
        status = "0" if i == 31 else "200"                             # a failure: late
        latency = 12000.0 if i == 32 else 3000.0                       # over the SLO: late
        samples.append({"seq": i, "tenant": tenant, "tier": "small", "cache": "hit" if i == 33 else "miss",
                        "warmup": t < cs.WARMUP_S, "status": status, "latency_ms": latency})
    return table, samples


def test_late_shares_count_missing_failed_and_slow_requests_and_skip_the_warm_up():
    table, samples = table_and_samples()
    n, late, missing = pairs.late_shares(table, samples, 9000.0, ["t00", "t01"])
    scored = [r for r in table if not r["warmup"]]
    assert n.sum() == len(scored) and late.sum() == 3 and missing == 1
    assert n.shape == (cs.STEPS - tbp.WARMUP_STEPS, 2)


def test_measure_gives_attainment_cost_and_the_protocols_j():
    table, samples = table_and_samples()
    records = [decision(START - 100, {"small": 1, "mid": 1})]
    m = pairs.measure(table, samples, records, START, TESTBED)
    scored = sum(1 for r in table if not r["warmup"])
    assert m["requests"] == scored and m["attainment"] == pytest.approx(1 - 3 / scored)
    cost = 3 * (cs.DURATION_S - cs.WARMUP_S) / 3600 * 0.36
    assert m["total_cost_usd"] == pytest.approx(cost) and m["cost_per_1k"] == pytest.approx(1000 * cost / scored)
    n, late, _ = pairs.late_shares(table, samples, 9000.0, ["t00", "t01"])
    want = tbp.j_of(cost, n.shape[0], np.divide(late, n, out=np.zeros_like(late), where=n > 0))
    assert m["J"] == pytest.approx(want["J"]) and m["missing_samples"] == 1
    assert m["hit_rate"] == pytest.approx(1 / sum(1 for s in samples if not s["warmup"] and s["status"] == "200"))


def test_stable_windows_start_after_ready_and_end_at_the_next_change():
    records = [decision(START - 100, {"small": 2, "mid": 1}),
               decision(START + 100, {"small": 2, "mid": 1}),                          # no change for small
               decision(START + 300, {"small": 3, "mid": 1}, {"small": 2, "mid": 1}),
               {"event": "ready", "tier": "small", "replicas": 3, "ready_unix": START + 330},
               decision(START + 500, {"small": 1, "mid": 1}, {"small": 3, "mid": 1})]
    got = pairs.stable_windows(records, "small", START + 120, START + 600)
    s = pairs.SETTLE_S
    assert got == [(START + 120, START + 300, 2), (START + 330 + s, START + 500, 3)]
    assert pairs.stable_windows(records, "mid", START + 120, START + 600) == []     # one pod: no spread to judge
    never_ready = records[:3] + records[4:]
    assert pairs.stable_windows(never_ready, "small", START + 120, START + 600) == [(START + 120, START + 300, 2)]


def test_spread_is_judged_only_inside_stable_windows():
    records = [decision(START - 100, {"small": 2, "mid": 1})]

    def query(promql, t):
        if promql.startswith("count by"):
            return [{"metric": {"tier": "small", "pod": p}, "value": [t, "1"]} for p in ("a", "b")]
        return [{"metric": {"tier": "small", "pod": "a"}, "value": [t, "9000"]},
                {"metric": {"tier": "small", "pod": "b"}, "value": [t, "1000"]},
                {"metric": {"tier": "mid", "pod": "m"}, "value": [t, "5000"]}]

    r = pairs.spread_findings(query, records, ["small", "mid"], START + 120, START + 600)
    assert len(r["windows"]) == 1 and r["windows"][0]["tier"] == "small"
    assert len(r["findings"]) == 1 and "b served 10.0%" in r["findings"][0]


def good_record():
    return {"posture": {"errors": []}, "replay_failed": 0, "replay_alive": False, "replay_error": None, "k6_exit": 0,
            "lag_p99_ms": 3.0, "measured": {"missing_samples": 0, "requests": 400.0}, "spread": {"findings": []},
            "ac_power": True, "ac_power_end": True}


@pytest.mark.parametrize("change, expect", [
    ({}, None),
    ({"posture": {"errors": ["t00: read-back differs"]}}, "read-back"),
    ({"replay_failed": 2}, "2 plan decisions"),
    ({"replay_alive": True}, "did not finish"),
    ({"k6_exit": 107}, "k6 exit 107"),
    ({"k6_exit": 99, "lag_p99_ms": 2500.0}, "schedule lag"),
    ({"measured": {"missing_samples": 9, "requests": 400.0}}, "no sample"),
    ({"measured": {}}, "not measured"),
    ({"spread": {"findings": ["x"]}}, "load spread"),
    ({"ac_power_end": False}, "battery"),
])
def test_a_run_is_a_measurement_only_without_problems(change, expect):
    problems = pairs.problems_of({**good_record(), **change})
    if expect is None:
        assert problems == []
    else:
        assert len(problems) == 1 and expect in problems[0]


def test_sample_sizes_are_the_plans_formulas():
    diffs = [0.01, 0.03, -0.01, 0.02, 0.00, 0.01]
    s = pairs.sample_sizes(diffs, comparator_mean=0.5, predicted_diff=-0.02)
    sd = np.std(diffs, ddof=1)
    assert s["sigma_d"] == pytest.approx(sd)
    assert s["n_superiority"] == math.ceil(((1.645 + 1.282) * sd / 0.02) ** 2)
    assert s["n_equivalence"] == math.ceil(((1.645 + 1.645) * sd / (0.02 * 0.5)) ** 2)
    assert pairs.sample_sizes(diffs, 0.5, 0.0)["n_superiority"] is None


def test_a_pair_arm_retries_an_invalid_run_and_a_rerun_repeats_nothing(tmp_path, monkeypatch):
    calls = []

    def fake_run_arm(plan, testbed, schedule_dir, table, tokens, admin_key, out):
        calls.append(out.name)
        rec = {"valid": len(calls) > 1, "problems": [] if len(calls) > 1 else ["lag"], "measured": {"J": 1.0}}
        out.mkdir(parents=True, exist_ok=True)
        (out / "run.json").write_text(json.dumps(rec))
        return rec

    monkeypatch.setattr(pairs, "run_arm", fake_run_arm)
    monkeypatch.setattr(pairs.cs, "read_table", lambda path: [])
    plan = {"schedule": 12000, "regime": "low", "k": 0, "arm": "joint"}
    rec = pairs.run_pair_arm(plan, TESTBED, tmp_path / "s", tmp_path / "tokens.json", "key", tmp_path / "runs")
    assert rec["valid"] and calls == ["attempt1", "attempt2"]
    assert pairs.run_pair_arm(plan, TESTBED, tmp_path / "s", tmp_path / "tokens.json", "key", tmp_path / "runs")["valid"]
    assert calls == ["attempt1", "attempt2"]

    calls.clear()
    monkeypatch.setattr(pairs, "run_arm", lambda *a: (calls.append(1), {"valid": False, "problems": ["x"]})[1])
    plan2 = {**plan, "k": 1}
    assert pairs.run_pair_arm(plan2, TESTBED, tmp_path / "s", tmp_path / "t.json", "key", tmp_path / "runs") is None
    assert len(calls) == pairs.ATTEMPTS


def test_the_report_has_a_row_per_regime_and_metric():
    plans = {"rules": {"scaling": "fitted"},
             "summary": {r: {"joint_mean_index": 0.02, "predicted_mean_diff": {"J": -0.01, "cost_per_1k": 0.01,
                                                                               "attainment": 0.0}}
                         for r in ("low", "high")}}
    runs = {(r, k): {"joint": {"J": 0.5, "cost_per_1k": 0.3, "attainment": 0.6},
                     "per_knob": {"J": 0.5 + 0.01 * k, "cost_per_1k": 0.3, "attainment": 0.6}}
            for r in ("low", "high") for k in range(4)}
    summary, text = pairs.report(plans, runs, ["high pair 4"])
    assert summary["low"]["J"]["pairs"] == 4 and summary["low"]["J"]["mean_diff"] == pytest.approx(0.015)
    assert text.count("| low |") == 3 and text.count("| high |") == 3 and "high pair 4" in text
