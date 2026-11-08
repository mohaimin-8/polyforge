"""Request accounting for shedding (audit 2026-11-09).

`tier_none_step_share` counts tenant-steps on which AI was shed; it says
nothing about how many requests went unanswered. These tests pin the
request-level counters that do: a shed request is an AI request that missed
the cache on a step whose tier is "none"; the counters are reporting-only, so
every published output is unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL_DIR))

from harness import results, workloads  # noqa: E402  (also puts research/jcac_sim on sys.path)
from harness.config import RunSpec  # noqa: E402
from harness.sim_backend import execute  # noqa: E402
import model  # noqa: E402
import simulate  # noqa: E402

CONFIG = model.TenantConfig("a")
DEMAND = model.Demand(rps={"chat": 4.0, "embed": 2.0, "agent": 1.0, "crud_read": 3.0})
AI_RPS, ALL_RPS = 7.0, 10.0
ACCOUNTING = ("ai_shed_request_share", "shed_request_share", "cost_per_served_request")


def step(tier: str, cache_mb: int) -> model.StepMetrics:
    return model.evaluate_step(CONFIG, model.TenantState(replicas=4, cache_mb=cache_mb, tier=tier), DEMAND)


def test_offered_requests_are_rate_times_interval():
    m = step("small", 256)
    assert m.requests == pytest.approx(ALL_RPS * model.CONTROL_INTERVAL_S)
    assert m.ai_requests == pytest.approx(AI_RPS * model.CONTROL_INTERVAL_S)


def test_a_served_tier_sheds_nothing():
    for tier in ("small", "mid", "large"):
        assert step(tier, 0).ai_shed_requests == 0.0
        assert step(tier, 256).ai_shed_requests == 0.0


def test_shedding_without_a_cache_drops_every_ai_request():
    m = step("none", 0)
    assert m.ai_shed_requests == pytest.approx(m.ai_requests)


def test_cache_hits_are_answered_while_ai_is_shed():
    m = step("none", 256)
    hit = model.hit_rate(256)
    expected = sum(rate * (1.0 - hit * model.CACHEABLE_FRACTION[kind])
                   for kind, rate in DEMAND.rps.items() if kind in model.AI_KINDS)
    assert m.ai_shed_requests == pytest.approx(expected * model.CONTROL_INTERVAL_S)
    assert 0.0 < m.ai_shed_requests < m.ai_requests


def test_crud_only_demand_sheds_nothing():
    m = model.evaluate_step(CONFIG, model.TenantState(tier="none"), model.Demand(rps={"crud_read": 5.0}))
    assert (m.ai_requests, m.ai_shed_requests) == (0.0, 0.0)
    assert m.requests == pytest.approx(5.0 * model.CONTROL_INTERVAL_S)


def _run(arm: str, wl: str = "ai_cacheable", seed: int = 5) -> simulate.RunResult:
    tids, buckets, configs, limits = workloads.build(wl, "uniform", "small", seed, 40)
    params = {"anchor_moves": True, "converge_sweeps": True} if arm == "jcac" else None
    return simulate.run(arm, tids, buckets, configs=configs, limits=limits, collect_rows=False,
                        controller_params=params, jitter_seed=seed)


@pytest.mark.parametrize("arm", ["jcac", "hpa", "static"])
def test_run_shares_are_bounded_and_consistent(arm):
    r = _run(arm)
    assert 0.0 <= r.shed_request_share <= r.ai_shed_request_share <= 1.0
    assert r.cost_per_served_request > 0.0
    if r.tier_none_step_share == 0.0:
        assert r.ai_shed_request_share == 0.0


def test_a_shedding_run_reports_shed_requests():
    # The published JC sheds AI on a share of tenant-steps in the main matrix;
    # find a cell where it does and check the request count follows.
    for wl in ("agentic", "ai_uncacheable", "ai_cacheable", "crud_bursty"):
        r = _run("jcac", wl)
        if r.tier_none_step_share > 0.0:
            assert r.ai_shed_request_share > 0.0
            return
    pytest.skip("no shedding cell among the probes")


def test_accounting_is_reporting_only():
    # Every field `summary()` reports, and the scored aggregates, are what they
    # were; the new counters sit beside them. The golden digests in
    # test_solver_convergence pin the same property bit-for-bit.
    r = _run("jcac")
    assert set(r.summary()) & set(ACCOUNTING) == set()
    assert r.total_cost_usd > 0.0 and 0.0 <= r.mean_violation <= 1.0


def test_accounting_reaches_the_result_store(tmp_path):
    spec = RunSpec(experiment="shed_accounting_test", backend="sim", system="jcac_converged",
                   workload="ai_cacheable", tenant_mix="uniform", cluster_size="small", rep=0,
                   steps=20, run_id="shed-accounting-test", seed=5, store_timeseries=False)
    outcome = execute(spec)
    for column in ACCOUNTING:
        assert column in outcome["metrics"]
    con = results.connect(tmp_path / "t.duckdb")
    try:
        results.record(con, spec, "valid", 1, outcome)
        row = con.execute("SELECT " + ", ".join(ACCOUNTING) + " FROM metrics").fetchone()
    finally:
        con.close()
    assert row == tuple(outcome["metrics"][c] for c in ACCOUNTING)
