"""Budget-matched comparators (PREREG_BUDGET_MATCHED, 2026-11-09).

Only the joint controller carried the per-tenant budget filter in the audited
campaigns. These arms put the audited comparators under that same filter,
at their registered tuning; the tests pin that each is its unbudgeted twin
plus the filter and nothing else, that the filter binds, and that the capped
pair still obeys the cluster caps.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL_DIR))

from harness import sim_backend, workloads  # noqa: E402  (also puts research/jcac_sim on sys.path)
from harness.config import RunSpec  # noqa: E402
from harness.systems import SYSTEMS  # noqa: E402
import baselines  # noqa: E402

TWINS = {"hpa_budget_capped": "hpa_fair_capped", "keda_budget_capped": "keda_fair_capped",
         "layered_small_budget": "layered_small_tuned"}
SAME = ("static_cache_mb", "lru_eviction", "tuned_from", "tuned_arm", "params", "tier_min", "gamma", "beta")


@pytest.mark.parametrize("arm,twin", sorted(TWINS.items()))
def test_a_budget_matched_arm_is_its_twin_plus_the_filter(arm, twin):
    for attr in SAME:
        if attr == "tuned_arm":
            continue  # the budget arm reads its twin's per-size tuning, checked below
        assert getattr(SYSTEMS[arm], attr) == getattr(SYSTEMS[twin], attr), attr
    for size in ("small", "medium", "large"):
        assert sim_backend.base_params(SYSTEMS[arm], size) == sim_backend.base_params(SYSTEMS[twin], size)


def test_the_capped_pair_filters_first_then_caps():
    tids, _, configs, _ = workloads.build("ai_cacheable", "uniform", "small", 1, 4)
    params = sim_backend.base_params(SYSTEMS["hpa_budget_capped"], "small")
    ctl = baselines.make_baseline(SYSTEMS["hpa_budget_capped"].controller, configs, **params)
    assert isinstance(ctl, baselines.CapEnforced)
    assert isinstance(ctl.inner, baselines.BudgetFiltered)
    assert isinstance(ctl.inner.inner, baselines.HPAController)
    assert ctl.inner.inner.target_rho == params["target_rho"]
    small = baselines.make_baseline(SYSTEMS["layered_small_budget"].controller, configs,
                                    **sim_backend.base_params(SYSTEMS["layered_small_budget"], "small"))
    assert isinstance(small, baselines.BudgetFiltered)
    assert isinstance(small.inner, baselines.LayeredSmallController)


def _execute(system: str, wl: str, mix: str, size: str, timeseries: bool = False) -> dict:
    spec = RunSpec(experiment="budget_matched_test", backend="sim", system=system, workload=wl,
                   tenant_mix=mix, cluster_size=size, rep=0, steps=120, run_id=f"bm-{system}",
                   seed=11, store_timeseries=timeseries)
    return sim_backend.execute(spec)


# Cells where the unbudgeted twins overrun the budget (probed 2026-11-09).
CELLS = [("agentic", "besteffort_heavy", "medium"), ("ai_cacheable", "besteffort_heavy", "large"),
         ("agentic", "premium_heavy", "large"), ("ai_uncacheable", "besteffort_heavy", "large")]


@pytest.mark.parametrize("arm,twin", sorted(TWINS.items()))
def test_the_filter_binds_and_never_raises_overruns(arm, twin):
    bound = False
    for wl, mix, size in CELLS:
        a = _execute(arm, wl, mix, size)["metrics"]
        b = _execute(twin, wl, mix, size)["metrics"]
        assert a["budget_overrun_step_share"] <= b["budget_overrun_step_share"] + 1e-12
        if b["budget_overrun_step_share"] > 0.0 and a["total_cost_usd"] < b["total_cost_usd"]:
            bound = True
    assert bound, f"{arm}: the budget filter never bound on the probe cells"


@pytest.mark.parametrize("arm", ["hpa_budget_capped", "keda_budget_capped"])
def test_the_capped_pair_obeys_the_cluster_caps(arm):
    wl, mix, size = CELLS[0]
    rows = _execute(arm, wl, mix, size, timeseries=True)["timeseries"]
    limits = workloads.CLUSTER_SIZES[size]
    per_step: dict[int, list] = {}
    for r in rows:
        per_step.setdefault(r["step"], []).append(r)
    for step_rows in per_step.values():
        assert sum(r["replicas"] for r in step_rows) <= limits.limits_replicas
        assert sum(r["cache_mb"] for r in step_rows) <= limits.limits_cache_mb
