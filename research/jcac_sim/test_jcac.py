"""Unit tests for the JCAC simulator (W30). Run: python -m unittest -v"""

from __future__ import annotations

import unittest

import baselines
import model
from controller import ClusterLimits, Forecast, JCACController, Weights
from model import Demand, TenantConfig, TenantState, apply_action, evaluate_step, jain_index


def demand(rps: dict, crud_base_ms: float = 50.0) -> Demand:
    return Demand(rps=rps, crud_base_ms=crud_base_ms)


class ModelTests(unittest.TestCase):
    def test_cache_absorbs_work(self):
        d = demand({"chat": 2.0})
        self.assertLess(d.work_units(1024), d.work_units(0))

    def test_congestion_saturates(self):
        self.assertEqual(model.congestion(1000.0, 1), model.MAX_CONGESTION)
        self.assertAlmostEqual(model.congestion(0.0, 2), 1.0)

    def test_overload_violates_slo(self):
        config = TenantConfig(tenant_id="t", slo_class="premium")
        idle = evaluate_step(config, TenantState(replicas=2), demand({"crud_read": 1.0}))
        slammed = evaluate_step(config, TenantState(replicas=1), demand({"crud_read": 500.0}))
        self.assertEqual(idle.violation, 0.0)
        self.assertGreater(slammed.violation, 0.5)

    def test_tier_none_is_an_ai_outage(self):
        config = TenantConfig(tenant_id="t")
        m = evaluate_step(config, TenantState(tier="none"), demand({"chat": 1.0}))
        self.assertGreater(m.violation, 0.9)

    def test_more_replicas_cost_more(self):
        config = TenantConfig(tenant_id="t")
        d = demand({"crud_read": 1.0})
        small = evaluate_step(config, TenantState(replicas=1, cache_mb=0), d)
        big = evaluate_step(config, TenantState(replicas=10, cache_mb=0), d)
        self.assertGreater(big.cost_usd, small.cost_usd)

    def test_jain_index(self):
        self.assertAlmostEqual(jain_index([1.0, 1.0, 1.0]), 1.0)
        self.assertAlmostEqual(jain_index([1.0, 0.0, 0.0]), 1.0 / 3.0)
        self.assertEqual(jain_index([]), 1.0)

    def test_apply_action_clamps_to_bounds(self):
        config = TenantConfig(tenant_id="t", replica_min=2, replica_max=4)
        state = TenantState(replicas=4)
        up = apply_action(config, state, +2, 128, "small")
        down = apply_action(config, TenantState(replicas=2), -2, 128, "small")
        self.assertEqual(up.replicas, 4)
        self.assertEqual(down.replicas, 2)


class ForecastTests(unittest.TestCase):
    def test_trend_extrapolates(self):
        f = Forecast()
        f.observe(demand({"chat": 1.0}))
        f.observe(demand({"chat": 2.0}))
        f.observe(demand({"chat": 3.0}))
        horizon = f.horizon()
        self.assertEqual(len(horizon), model.HORIZON_STEPS)
        self.assertGreater(horizon[0].rps["chat"], 3.0)
        self.assertGreater(horizon[-1].rps["chat"], horizon[0].rps["chat"])

    def test_trend_never_goes_negative(self):
        f = Forecast()
        f.observe(demand({"chat": 3.0}))
        f.observe(demand({"chat": 2.0}))
        f.observe(demand({"chat": 0.5}))
        for d in f.horizon():
            self.assertGreaterEqual(d.rps["chat"], 0.0)


class ControllerTests(unittest.TestCase):
    def _controller(self, budget_usd: float = 5.0, **kw) -> JCACController:
        configs = {
            "a": TenantConfig(tenant_id="a", slo_class="premium", hourly_budget_usd=budget_usd),
            "b": TenantConfig(tenant_id="b", slo_class="best-effort", hourly_budget_usd=budget_usd),
        }
        return JCACController(configs, **kw)

    def test_scales_up_under_surge(self):
        ctl = self._controller()
        states = {"a": TenantState(replicas=1), "b": TenantState(replicas=1)}
        surge = {"a": demand({"crud_read": 400.0}), "b": demand({"crud_read": 0.1})}
        for _ in range(3):  # let the forecast see the surge
            plans = ctl.plan(states, surge)
            states = {tid: p.state for tid, p in plans.items()}
        self.assertGreater(states["a"].replicas, 2)

    def test_respects_budget_cap(self):
        # A budget that cannot even pay for two replicas forces the
        # fallback shed configuration instead of a violation of the cap.
        ctl = self._controller(budget_usd=0.0001)
        states = {"a": TenantState(), "b": TenantState()}
        plans = ctl.plan(states, {"a": demand({"chat": 5.0}), "b": demand({"chat": 5.0})})
        for entry in plans.values():
            self.assertEqual(entry.state.replicas, 1)
            self.assertEqual(entry.state.tier, "none")

    def test_respects_cluster_cache_limit(self):
        ctl = self._controller(limits=ClusterLimits(cache_mb=256, replicas=60))
        states = {"a": TenantState(cache_mb=128), "b": TenantState(cache_mb=128)}
        heavy = {"a": demand({"chat": 10.0}), "b": demand({"chat": 10.0})}
        for _ in range(3):
            plans = ctl.plan(states, heavy)
            states = {tid: p.state for tid, p in plans.items()}
        self.assertLessEqual(states["a"].cache_mb + states["b"].cache_mb, 256)

    def test_hysteresis_holds_config_when_idle(self):
        ctl = self._controller()
        states = {"a": TenantState(), "b": TenantState()}
        idle = {"a": demand({"crud_read": 0.5}), "b": demand({"crud_read": 0.5})}
        ctl.plan(states, idle)
        plans = ctl.plan(states, idle)
        second = {tid: p.state for tid, p in plans.items()}
        third = {tid: p.state for tid, p in ctl.plan(second, idle).items()}
        self.assertEqual(second, third)


class BaselineTests(unittest.TestCase):
    def test_static_never_moves(self):
        configs = {"a": TenantConfig(tenant_id="a")}
        ctl = baselines.StaticController(configs)
        states = {"a": TenantState()}
        out = ctl.plan(states, {"a": demand({"chat": 100.0})})
        self.assertEqual(out["a"], states["a"])

    def test_hpa_scales_on_utilization_only(self):
        configs = {"a": TenantConfig(tenant_id="a")}
        ctl = baselines.HPAController(configs)
        out = ctl.plan({"a": TenantState(replicas=2)}, {"a": demand({"crud_read": 300.0})})
        self.assertGreater(out["a"].replicas, 2)
        self.assertEqual(out["a"].cache_mb, 128)  # never touches the cache
        self.assertEqual(out["a"].tier, "small")  # never touches the tier

    def test_layered_upgrades_tier_under_slow_ai(self):
        configs = {"a": TenantConfig(tenant_id="a", slo_class="premium")}
        ctl = baselines.LayeredController(configs)
        out = ctl.plan(
            {"a": TenantState(replicas=1, cache_mb=0, tier="small")},
            {"a": demand({"agent": 2.0})},
        )
        self.assertEqual(out["a"].tier, "mid")


if __name__ == "__main__":
    unittest.main()
