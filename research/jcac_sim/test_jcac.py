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

    def test_static_overprovisioned_pins_to_peak(self):
        configs = {"a": TenantConfig(tenant_id="a", replica_max=8)}
        ctl = baselines.StaticController(configs, overprovisioned=True)
        out = ctl.plan({"a": TenantState()}, {"a": demand({"crud_read": 1.0})})
        self.assertEqual(out["a"].replicas, 8)
        self.assertEqual(out["a"].cache_mb, model.CACHE_LEVELS_MB[-1])
        # And it never moves after that.
        again = ctl.plan(out, {"a": demand({"chat": 100.0})})
        self.assertEqual(again["a"], out["a"])

    def test_firm_learns_to_scale_out_of_violation(self):
        configs = {"a": TenantConfig(tenant_id="a", slo_class="premium")}
        ctl = baselines.FIRMReplicaController(configs, seed=7, epsilon=0.05)
        states = {"a": TenantState(replicas=1)}
        overload = {"a": demand({"crud_read": 400.0})}
        for _ in range(40):  # enough steps for the Q-table to converge
            states = ctl.plan(states, overload)
        self.assertGreater(states["a"].replicas, 2)
        self.assertEqual(states["a"].cache_mb, 128)  # replica-only controller
        self.assertEqual(states["a"].tier, "small")

    def test_firm_is_deterministic_under_a_seed(self):
        def run_once():
            configs = {"a": TenantConfig(tenant_id="a")}
            ctl = baselines.FIRMReplicaController(configs, seed=3)
            states = {"a": TenantState()}
            for i in range(20):
                states = ctl.plan(states, {"a": demand({"crud_read": float(20 * i)})})
            return states["a"]

        self.assertEqual(run_once(), run_once())

    def test_gptcache_grows_cache_on_cacheable_traffic(self):
        configs = {"a": TenantConfig(tenant_id="a")}
        ctl = baselines.GPTCacheLRUController(configs)
        states = {"a": TenantState(cache_mb=0)}
        chatty = {"a": demand({"chat": 5.0})}
        for _ in range(len(model.CACHE_LEVELS_MB)):
            states = ctl.plan(states, chatty)
        self.assertEqual(states["a"].cache_mb, model.CACHE_LEVELS_MB[-1])

    def test_make_baseline_forwards_tuning_params(self):
        configs = {"a": TenantConfig(tenant_id="a")}
        ctl = baselines.make_baseline("hpa", configs, target_rho=0.4)
        self.assertEqual(ctl.target_rho, 0.4)
        with self.assertRaises(ValueError):
            baselines.make_baseline("nope", configs)


class ForecastMethodTests(unittest.TestCase):
    def _observe_series(self, method: str, series: list[float]) -> Forecast:
        f = Forecast(method=method)
        for v in series:
            f.observe(demand({"chat": v}))
        return f

    def test_persistence_is_flat(self):
        f = self._observe_series("persistence", [1.0, 2.0, 9.0])
        self.assertTrue(all(d.rps["chat"] == 9.0 for d in f.horizon()))

    def test_holt_tracks_level_without_chasing_one_spike(self):
        f = self._observe_series("holt", [10.0] * 30 + [40.0])  # single spike
        first = f.horizon()[0].rps["chat"]
        self.assertGreater(first, 10.0)  # it responds...
        self.assertLess(first, 40.0)  # ...but does not swallow the spike whole
        g = self._observe_series("trend", [10.0] * 30 + [40.0])
        self.assertGreater(g.horizon()[0].rps["chat"], first)  # trend overreacts more

    def test_seasonal_detects_a_square_wave_period(self):
        period, cycles = 12, 6
        wave = ([50.0] * 4 + [5.0] * 8) * cycles
        f = self._observe_series("seasonal", wave)
        # History ends at a period boundary, so the next steps are the
        # burst phase: a period-aware forecast predicts the burst *before*
        # it arrives; trend, looking at the recent quiet phase, cannot.
        self.assertGreater(f.horizon()[0].rps["chat"], 25.0)
        t = self._observe_series("trend", wave)
        self.assertLess(t.horizon()[0].rps["chat"], 25.0)

    def test_seasonal_falls_back_to_trend_without_periodicity(self):
        # A pure ramp has no season; raw autocorrelation would claim one
        # (same-signed deviations), which is why the detector detrends.
        ramp = [float(i) for i in range(30)]
        f = self._observe_series("seasonal", ramp)
        t = self._observe_series("trend", ramp)
        self.assertEqual(
            [d.rps["chat"] for d in f.horizon()],
            [d.rps["chat"] for d in t.horizon()],
        )


class AdaptiveCapacityTests(unittest.TestCase):
    def test_feedback_shrinks_then_recovers_capacity_trust(self):
        configs = {"a": TenantConfig(tenant_id="a")}
        ctl = JCACController(configs, adaptive_capacity=True)
        ctl.plan({"a": TenantState()}, {"a": demand({"crud_read": 10.0})})
        projected = ctl._projected["a"]
        ctl.observe_feedback({"a": projected + 0.5})  # world much worse than promised
        shrunk = ctl.capacity_scale["a"]
        self.assertLess(shrunk, 1.0)
        ctl.plan({"a": TenantState()}, {"a": demand({"crud_read": 10.0})})
        ctl.observe_feedback({"a": 0.0})  # world fine again
        self.assertGreater(ctl.capacity_scale["a"], shrunk)
        self.assertLessEqual(ctl.capacity_scale["a"], 1.0)

    def test_non_adaptive_controller_ignores_feedback(self):
        configs = {"a": TenantConfig(tenant_id="a")}
        ctl = JCACController(configs)
        ctl.plan({"a": TenantState()}, {"a": demand({"crud_read": 10.0})})
        ctl.observe_feedback({"a": 1.0})
        self.assertEqual(ctl.capacity_scale["a"], 1.0)


class TransitionCostTests(unittest.TestCase):
    def test_effective_state_lags_growth_not_shrink(self):
        import simulate

        prev = TenantState(replicas=2, cache_mb=128)
        up = simulate.effective_state(prev, TenantState(replicas=5, cache_mb=512))
        self.assertEqual(up.replicas, 2)  # startup lag
        self.assertEqual(up.cache_mb, 320)  # half-warm
        down = simulate.effective_state(prev, TenantState(replicas=1, cache_mb=64))
        self.assertEqual((down.replicas, down.cache_mb), (1, 64))  # instant

    def test_transition_costs_punish_a_reactive_scaler(self):
        import simulate

        # Bursty demand: HPA scales after each burst arrives, so under
        # startup lag its new replicas always miss the burst they were
        # bought for.
        buckets = []
        for step in range(40):
            rate = 260.0 if (step // 5) % 2 == 0 else 10.0
            buckets.append({"a": demand({"crud_read": rate})})
        ideal = simulate.run("hpa", ["a"], buckets, collect_rows=False)
        laggy = simulate.run("hpa", ["a"], buckets, collect_rows=False,
                             transition_costs=True)
        self.assertGreater(laggy.mean_violation, ideal.mean_violation)


class InterferenceInjectionTests(unittest.TestCase):
    """v2 Phase 4: noisy-neighbor interference (simulate)."""

    def test_only_dominant_tenant_past_gate_is_flagged(self):
        import simulate

        states = {t: TenantState(replicas=4) for t in ("w", "a", "b", "c")}
        # w offers ~10x the others' work: it clears the 2x-fair-share gate.
        demands = {"w": demand({"crud_read": 400.0}), "a": demand({"crud_read": 5.0}),
                   "b": demand({"crud_read": 5.0}), "c": demand({"crud_read": 5.0})}
        scores = simulate.interference_scores(states, demands)
        self.assertEqual(list(scores), ["w"])
        self.assertGreater(scores["w"], 0.0)

    def test_balanced_cluster_flags_nobody(self):
        import simulate

        states = {t: TenantState(replicas=4) for t in ("a", "b", "c", "d")}
        demands = {t: demand({"crud_read": 20.0}) for t in ("a", "b", "c", "d")}
        self.assertEqual(simulate.interference_scores(states, demands), {})

    def test_victims_lose_capacity_not_the_aggressor(self):
        import simulate

        serving = TenantState(replicas=8, cache_mb=128, tier="mid")
        hit = simulate.interfered_state(serving, theta=1.0)
        self.assertEqual(hit.replicas, 4)  # up to 50% stolen at theta=1
        self.assertEqual((hit.cache_mb, hit.tier), (128, "mid"))
        self.assertEqual(simulate.interfered_state(serving, theta=0.0), serving)

    def test_stolen_capacity_raises_a_loaded_victims_violation(self):
        # The mechanic in isolation: a victim sized for its own load
        # violates more once a neighbor steals half its replicas.
        config = TenantConfig(tenant_id="v", slo_class="premium")
        loaded = TenantState(replicas=4, cache_mb=0, tier="none")
        d = demand({"crud_read": 150.0})  # fine on 4 replicas, not on 2
        import simulate

        clean = evaluate_step(config, loaded, d)
        stolen = evaluate_step(config, simulate.interfered_state(loaded, 1.0), d)
        self.assertGreater(stolen.violation, clean.violation)

    def test_injection_is_a_noop_without_a_dominant_tenant(self):
        # A balanced cluster injects nothing, so the run is identical with
        # and without the flag — injection only bites past the gate.
        import simulate

        ids = ["a", "b", "c", "d"]
        buckets = [{t: demand({"crud_read": 40.0}) for t in ids} for _ in range(20)]
        off = simulate.run("jcac", ids, buckets, collect_rows=False, jitter_seed=7)
        on = simulate.run("jcac", ids, buckets, collect_rows=False, jitter_seed=7,
                          interference_injection=True)
        self.assertEqual(on.mean_violation, off.mean_violation)
        self.assertEqual(on.total_cost_usd, off.total_cost_usd)


class IsoCostBaselineTests(unittest.TestCase):
    """v2 Phase 2: pinned iso-cost baseline controllers (baselines)."""

    def test_static_fixed_replicas_pins_and_clamps(self):
        configs = {"t": TenantConfig(tenant_id="t", replica_max=6)}
        ctl = baselines.StaticController(configs, fixed_replicas=3)
        out = ctl.plan({"t": TenantState(replicas=1)}, {"t": demand({"crud_read": 1.0})})
        self.assertEqual(out["t"].replicas, 3)
        capped = baselines.StaticController(configs, fixed_replicas=99)
        out2 = capped.plan({"t": TenantState(replicas=1)}, {"t": demand({"crud_read": 1.0})})
        self.assertEqual(out2["t"].replicas, 6)  # clamped to replica_max

    def test_gptcache_fixed_cache_holds_level(self):
        configs = {"t": TenantConfig(tenant_id="t", replica_max=6)}
        ctl = baselines.GPTCacheLRUController(configs, fixed_cache_mb=128)
        # From a 0 cache it walks toward 128 (one level/interval), never past.
        state = TenantState(replicas=2, cache_mb=64)
        out = ctl.plan({"t": state}, {"t": demand({"chat": 4.0})})
        self.assertLessEqual(out["t"].cache_mb, 128)


class SimulateTests(unittest.TestCase):
    def _buckets(self, steps: int = 12):
        return [
            {"a": demand({"crud_read": 30.0, "chat": 2.0}), "b": demand({"chat": 4.0})}
            for _ in range(steps)
        ]

    def test_jitter_is_seeded_and_changes_demand(self):
        import simulate

        buckets = self._buckets()
        j1 = simulate.jitter_buckets(buckets, seed=1)
        j2 = simulate.jitter_buckets(buckets, seed=1)
        j3 = simulate.jitter_buckets(buckets, seed=2)
        self.assertEqual(
            [b["a"].rps for b in j1], [b["a"].rps for b in j2]
        )
        self.assertNotEqual(
            [b["a"].rps for b in j1], [b["a"].rps for b in j3]
        )

    def test_miss_cost_factor_only_scales_tier_spend(self):
        import simulate

        buckets = self._buckets()
        base = simulate.run("static", ["a", "b"], buckets, collect_rows=False)
        lru = simulate.run(
            "static", ["a", "b"], buckets, collect_rows=False, miss_cost_factor=2.0
        )
        self.assertGreater(lru.total_cost_usd, base.total_cost_usd)
        # Same decisions, so violation profile is untouched.
        self.assertEqual(lru.mean_violation, base.mean_violation)

    def test_plan_transform_blinds_controller_not_scoring(self):
        import simulate

        buckets = self._buckets()

        def blind(demands):
            return {tid: Demand(rps={}, crud_base_ms=d.crud_base_ms) for tid, d in demands.items()}

        seeing = simulate.run("hpa", ["a", "b"], buckets, collect_rows=False)
        blinded = simulate.run(
            "hpa", ["a", "b"], buckets, collect_rows=False, plan_demand_transform=blind
        )
        # A blinded HPA sees zero demand, scales to the floor, and violates more.
        self.assertGreaterEqual(blinded.mean_violation, seeing.mean_violation)

    def test_summary_reports_hit_rate_and_p95(self):
        import simulate

        r = simulate.run("gptcache", ["a", "b"], self._buckets(), collect_rows=True)
        s = r.summary()
        self.assertGreater(s["cache_hit_rate"], 0.0)
        self.assertGreater(s["ai_p95_ms"], 0.0)
        self.assertGreater(s["crud_p95_ms"], 0.0)
        self.assertIn("cache_hit_rate", r.rows[0])


if __name__ == "__main__":
    unittest.main()
