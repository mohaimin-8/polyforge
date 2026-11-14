"""The WP11 orchestrator's pure parts: the run order, the waits, the k6 lag read."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import run_cells  # noqa: E402


def test_each_round_runs_every_cell_once_in_its_own_order():
    order = run_cells.run_order(range(20))
    assert len(order) == 60
    rounds = [[c for c, r in order if r == k] for k in range(3)]
    assert all(sorted(r) == list(range(20)) for r in rounds)
    assert rounds[0] != rounds[1] != rounds[2]
    assert order == run_cells.run_order(range(20))  # seeded


def test_quiet_needs_two_idle_readings_in_a_row():
    readings = iter([2.0, 0.0, 1.0, 0.0, 0.0])
    calls = []

    def query(promql, at):
        calls.append(promql)
        return [{"value": [at, str(next(readings))]}]

    run_cells.wait_quiet(query, sleep=lambda s: None)
    assert len(calls) == 5 and "requests_deferred" in calls[0]


def test_quiet_refuses_a_missing_series():
    with pytest.raises(RuntimeError, match="no series"):
        run_cells.wait_quiet(lambda promql, at: [], sleep=lambda s: None)


class FakeActuator:
    def __init__(self, states):
        self.states = iter(states)

    def current(self):
        return next(self.states)


def test_ready_waits_until_every_tier_is_ready():
    act = FakeActuator([{"small": (2, 1), "mid": (1, 1)}, {"small": (2, 2), "mid": (1, 1)}])
    run_cells.wait_ready(act, sleep=lambda s: None)


def test_lag_is_read_from_either_summary_layout(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"metrics": {"wp10_schedule_lag": {"p(99)": 12.5}}}))
    assert run_cells.k6_lag_p99(p) == 12.5
    p.write_text(json.dumps({"metrics": {"wp10_schedule_lag": {"values": {"p(99)": 7.0}}}}))
    assert run_cells.k6_lag_p99(p) == 7.0


def test_k6_never_reports_usage_and_does_not_abort_on_failures():
    assert run_cells.K6_ENV["K6_NO_USAGE_REPORT"] == "true" and run_cells.K6_ENV["ABORT_ON_FAIL"] == "false"


def test_a_missing_prediction_file_refuses_to_run(tmp_path):
    with pytest.raises((SystemExit, ValueError)):
        run_cells.predictions_committed(run_cells.REPO / "testbed" / "validation" / "no_such_file.json")


def test_a_missing_k6_summary_reads_as_infinite_lag(tmp_path):
    assert run_cells.k6_lag_p99(tmp_path / "absent.json") == float("inf")
    (tmp_path / "bad.json").write_text("{not json")
    assert run_cells.k6_lag_p99(tmp_path / "bad.json") == float("inf")


def test_the_gateway_must_answer_several_times_in_a_row():
    answers = iter([OSError("closed"), 200, 503, 200, 200, 200])

    def get(url):
        a = next(answers)
        if isinstance(a, Exception):
            raise a
        return a

    run_cells.gateway_answers(get=get, sleep=lambda s: None)
    with pytest.raises(RuntimeError, match="did not answer"):
        run_cells.gateway_answers(get=lambda u: 503, sleep=lambda s: None, timeout_s=0.0)


class FlakyActuator:
    def __init__(self, failures):
        self.failures, self.calls = failures, 0

    def apply(self, decision):
        self.calls += 1
        errs = ["t00: Remote end closed connection"] if self.calls <= self.failures else []
        return {"knobs": {"t00": {"ok": not errs}}, "errors": errs}


def test_posture_is_retried_after_connection_errors():
    pins = {"pins": {"t00": "small"}, "cache_knob_mb": {"t00": 1}}
    ok = run_cells.set_posture(FlakyActuator(2), pins, sleep=lambda s: None)
    assert ok["errors"] == [] and ok["attempts"] == 3
    bad = run_cells.set_posture(FlakyActuator(99), pins, sleep=lambda s: None)
    assert bad["errors"] and bad["attempts"] == run_cells.POSTURE_ATTEMPTS
