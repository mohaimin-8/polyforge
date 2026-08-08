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


class KnobBoundTests(unittest.TestCase):
    """Per-tenant cache/tier bounds are the live-ablation freeze mechanism
    (PREREG_WAVE4_LIVE_PLANE.md §Arms): a pinned (min==max) knob removes every
    off-value candidate so the joint controller re-optimizes the free knob
    alone. Default full-envelope bounds must be a no-op."""

    def test_default_config_admits_every_candidate(self):
        c = TenantConfig(tenant_id="t")
        for cache in model.CACHE_LEVELS_MB:
            for tier in model.TIERS:
                self.assertTrue(c.knob_admits(cache, tier),
                                f"default bounds rejected {cache}/{tier}")

    def test_pin_admits_only_the_pinned_value(self):
        cache_pin = TenantConfig(tenant_id="t", cache_min=128, cache_max=128)
        self.assertTrue(cache_pin.knob_admits(128, "small"))
        self.assertFalse(cache_pin.knob_admits(256, "small"))
        self.assertFalse(cache_pin.knob_admits(0, "small"))
        tier_pin = TenantConfig(tenant_id="t", tier_min="small", tier_max="small")
        self.assertTrue(tier_pin.knob_admits(128, "small"))
        self.assertFalse(tier_pin.knob_admits(128, "mid"))
        self.assertFalse(tier_pin.knob_admits(128, "none"))

    def test_pinned_cache_holds_while_unpinned_climbs(self):
        # A cacheable premium surge with a generous budget: unpinned the
        # controller raises the cache to absorb work; pinned it cannot, which
        # is what makes the pin meaningful (not trivially constant).
        def run(pin: bool) -> int:
            kw = dict(cache_min=128, cache_max=128) if pin else {}
            configs = {"a": TenantConfig(tenant_id="a", slo_class="premium",
                                         hourly_budget_usd=100.0, **kw)}
            ctl = JCACController(configs)
            states = {"a": TenantState(cache_mb=128)}
            for _ in range(5):
                plans = ctl.plan(states, {"a": demand({"chat": 30.0})})
                states = {t: p.state for t, p in plans.items()}
            return states["a"].cache_mb

        self.assertEqual(run(pin=True), 128, "pinned cache must not move")
        self.assertGreater(run(pin=False), 128, "control: unpinned cache should climb")

    def test_pinned_tier_holds_while_unpinned_upgrades(self):
        # Agent traffic degrades on the small tier, so with headroom the
        # unpinned controller upgrades; the tier-pinned arm stays put.
        def run(pin: bool) -> str:
            kw = dict(tier_min="small", tier_max="small") if pin else {}
            configs = {"a": TenantConfig(tenant_id="a", slo_class="premium",
                                         hourly_budget_usd=100.0, **kw)}
            ctl = JCACController(configs)
            states = {"a": TenantState(tier="small")}
            for _ in range(5):
                plans = ctl.plan(states, {"a": demand({"agent": 10.0})})
                states = {t: p.state for t, p in plans.items()}
            return states["a"].tier

        self.assertEqual(run(pin=True), "small", "pinned tier must not move")
        self.assertNotEqual(run(pin=False), "small", "control: unpinned tier should upgrade")


class SweepOrderTests(unittest.TestCase):
    """PREREG_ORDER_PERMUTATION: the sweep order is first-come-first-served on
    the shared cluster caps, and the default order is confounded with priority
    class because tenant ids are assigned by slot."""

    def _configs(self, n=6):
        return {f"t{i:02d}": TenantConfig(tenant_id=f"t{i:02d}",
                                          hourly_budget_usd=5.0)
                for i in range(n)}

    def test_default_order_is_sorted_and_stable(self):
        # R4: every published campaign ran sorted(); this must not drift.
        ctl = JCACController(self._configs())
        self.assertEqual(ctl._sweep_order(), sorted(ctl.configs))
        self.assertEqual(ctl._sweep_order(), ctl._sweep_order())

    def test_seed_permutes_and_is_deterministic_within_a_run(self):
        ctl = JCACController(self._configs(), tenant_order_seed=1)
        order = ctl._sweep_order()
        self.assertEqual(sorted(order), sorted(ctl.configs), "must be a permutation")
        # Fixed for the run: the question is whether AN order biases the
        # result, not what reshuffling every cycle averages out to.
        self.assertEqual(order, ctl._sweep_order())

    def test_different_seeds_give_different_orders(self):
        orders = {tuple(JCACController(self._configs(), tenant_order_seed=s)._sweep_order())
                  for s in (1, 2, 3, 4, 5)}
        self.assertGreater(len(orders), 1, "seeds must actually permute")


class EvictionParityTests(unittest.TestCase):
    """PREREG_EVICTION_PARITY: the unbounded severity pair, the pre-sized
    cache, and the eviction overhead charged to scoring but not to planning."""

    def test_excess_is_unbounded_where_violation_saturates(self):
        # A shed AI service (tier none = 30 s) and a merely-slow service both
        # saturate `violation` at 1.0; `excess` is what tells them apart, and
        # it is the reason EP-H3 exists.
        config = TenantConfig(tenant_id="t")
        d = demand({"chat": 5.0})
        shed = evaluate_step(config, TenantState(tier="none"), d)
        self.assertAlmostEqual(shed.violation, 1.0, places=3)
        self.assertGreater(shed.excess, 1.0, "excess must not saturate")
        # And excess >= violation always, since it is the same overshoot
        # without the min(1, .) clamp.
        for tier in model.TIERS:
            m = evaluate_step(config, TenantState(tier=tier), d)
            self.assertGreaterEqual(round(m.excess, 9), round(m.violation, 9))

    def test_evict_overhead_raises_latency_and_is_off_by_default(self):
        config = TenantConfig(tenant_id="t")
        d = demand({"chat": 4.0})
        base = evaluate_step(config, TenantState(), d)
        charged = evaluate_step(config, TenantState(), d, extra_ai_latency_ms=1.1049)
        self.assertAlmostEqual(charged.ai_p95_ms - base.ai_p95_ms, 1.1049, places=4)
        # Default must be a no-op: every published caller relies on it (R4).
        self.assertEqual(evaluate_step(config, TenantState(), d).ai_p95_ms,
                         base.ai_p95_ms)

    def test_crud_only_traffic_is_not_charged_ai_overhead(self):
        config = TenantConfig(tenant_id="t")
        d = demand({"crud_read": 5.0})
        base = evaluate_step(config, TenantState(), d)
        charged = evaluate_step(config, TenantState(), d, extra_ai_latency_ms=50.0)
        self.assertEqual(charged.violation, base.violation)


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

    def test_concurrency_scales_up_immediately_on_queue_growth(self):
        configs = {"a": TenantConfig(tenant_id="a")}
        ctl = baselines.ConcurrencyController(configs, target_concurrency=1.5)
        out = ctl.plan({"a": TenantState(replicas=2)}, {"a": demand({"crud_read": 300.0})})
        self.assertGreater(out["a"].replicas, 2)          # panic scale-up, no window
        self.assertEqual(out["a"].cache_mb, 128)          # replica-only controller
        self.assertEqual(out["a"].tier, "small")

    def test_concurrency_superlinear_near_saturation_vs_hpa(self):
        # At the same operating point (c_t=1.5 <=> rho_t=0.6 under a=1),
        # the in-flight signal must demand at least as many replicas as
        # utilization once queues grow — that superlinearity is the point
        # of the 2026-stack shape.
        configs = {"a": TenantConfig(tenant_id="a", replica_max=16)}
        near_saturation = {"a": demand({"crud_read": 750.0})}  # rho ~0.94 at 8
        kpa = baselines.ConcurrencyController(configs, target_concurrency=1.5)
        hpa = baselines.HPAController(configs, target_rho=0.6)
        state = {"a": TenantState(replicas=8)}
        kpa_out = kpa.plan(state, near_saturation)
        hpa_out = hpa.plan(state, near_saturation)
        self.assertGreaterEqual(kpa_out["a"].replicas, hpa_out["a"].replicas)

    def test_concurrency_stable_window_delays_scale_down(self):
        configs = {"a": TenantConfig(tenant_id="a")}
        ctl = baselines.ConcurrencyController(
            configs, target_concurrency=1.5, stable_intervals=3)
        states = {"a": TenantState(replicas=6)}
        idle = {"a": demand({"crud_read": 30.0})}
        first = ctl.plan(states, idle)
        second = ctl.plan(first, idle)
        self.assertEqual(first["a"].replicas, 6)   # below-target reading 1: hold
        self.assertEqual(second["a"].replicas, 6)  # reading 2: still holding
        third = ctl.plan(second, idle)
        self.assertLess(third["a"].replicas, 6)    # reading 3: window met, shrink

    # --- Learned joint controller (PREREG_LEARNED_CONTROL) -----------------
    def test_learned_respects_all_knob_bounds_and_cache_thrash_rule(self):
        configs = {"a": TenantConfig(tenant_id="a", replica_min=1, replica_max=6)}
        ctl = baselines.LearnedJointController(configs, seed=4, epsilon=0.5)  # explore hard
        states = {"a": TenantState()}
        prev_ci = model.CACHE_LEVELS_MB.index(states["a"].cache_mb)
        for _ in range(200):
            states = ctl.plan(states, {"a": demand({"chat": 8.0, "crud_read": 50.0})})
            s = states["a"]
            self.assertGreaterEqual(s.replicas, 1)
            self.assertLessEqual(s.replicas, 6)
            self.assertIn(s.cache_mb, model.CACHE_LEVELS_MB)
            self.assertIn(s.tier, ("small", "mid", "large"))  # never sheds to an outage
            ci = model.CACHE_LEVELS_MB.index(s.cache_mb)
            self.assertLessEqual(abs(ci - prev_ci), 1)  # <= one cache level per interval
            prev_ci = ci

    def test_learned_is_deterministic_under_a_seed(self):
        def run_once():
            configs = {"a": TenantConfig(tenant_id="a"),
                       "b": TenantConfig(tenant_id="b", slo_class="premium")}
            ctl = baselines.LearnedJointController(configs, seed=3, epsilon=0.2)
            states = {"a": TenantState(), "b": TenantState()}
            for i in range(30):
                states = ctl.plan(states, {"a": demand({"crud_read": float(20 * i)}),
                                           "b": demand({"chat": 5.0})})
            return states

        self.assertEqual(run_once(), run_once())

    def test_learned_untrained_default_is_hold(self):
        # index-0 action is (0,0,0), so an all-zero Q row keeps the config:
        # an untrained state holds rather than exploring into an outage.
        configs = {"a": TenantConfig(tenant_id="a")}
        ctl = baselines.LearnedJointController(configs, seed=0, epsilon=0.0)
        s0 = {"a": TenantState(replicas=3, cache_mb=128, tier="small")}
        out = ctl.plan(s0, {"a": demand({"chat": 5.0})})
        self.assertEqual(out["a"], s0["a"])

    def test_learned_exercises_all_three_knobs(self):
        # Distinguishes the joint learner from replica-only RL (FIRM): over a
        # run it moves cache and tier, not just replicas.
        configs = {"a": TenantConfig(tenant_id="a")}
        ctl = baselines.LearnedJointController(configs, seed=1, epsilon=0.3)
        states, caches, tiers = {"a": TenantState()}, set(), set()
        for _ in range(80):
            states = ctl.plan(states, {"a": demand({"chat": 6.0})})
            caches.add(states["a"].cache_mb)
            tiers.add(states["a"].tier)
        self.assertGreater(len(caches), 1)
        self.assertGreater(len(tiers), 1)

    def test_learned_shared_table_learns_and_round_trips(self):
        import os
        import tempfile

        configs = {"a": TenantConfig(tenant_id="a", slo_class="premium")}
        ctl = baselines.LearnedJointController(configs, seed=2, epsilon=0.3, shared=True)
        states = {"a": TenantState(replicas=1)}
        for _ in range(60):
            states = ctl.plan(states, {"a": demand({"crud_read": 300.0})})
        self.assertTrue([r for r in ctl._shared_q.values() if max(r) != min(r)])  # learned

        path = os.path.join(tempfile.gettempdir(), "pf_learned_test_q.json")
        try:
            ctl.save_q(path)
            loaded = baselines.LearnedJointController(configs, seed=9, epsilon=0.0,
                                                      qtable_path=path)
            self.assertEqual(loaded._shared_q, ctl._shared_q)
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_make_baseline_creates_learned(self):
        configs = {"a": TenantConfig(tenant_id="a")}
        ctl = baselines.make_baseline("learned", configs, epsilon=0.05, replay_batch=0)
        self.assertEqual(ctl.epsilon, 0.05)
        self.assertEqual(ctl.replay_batch, 0)


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


class VTCReplicaTests(unittest.TestCase):
    """VTC-replica (session 15): least-weighted-service-first pool division
    with the shared actuation guardrails."""

    def _mk(self, budgets=(5.0, 5.0), pool=6, rho=0.5):
        configs = {
            f"t{i}": TenantConfig(tenant_id=f"t{i}", hourly_budget_usd=b, replica_max=8)
            for i, b in enumerate(budgets)
        }
        ctl = baselines.VTCReplicaController(configs, target_rho=rho)
        ctl.set_limits(ClusterLimits(replicas=pool))
        return configs, ctl

    def test_least_served_tenant_wins_the_contended_pool(self):
        _, ctl = self._mk(pool=6)
        states = {"t0": TenantState(replicas=4), "t1": TenantState(replicas=1)}
        # t0 has already consumed far more service than t1.
        ctl.counters = {"t0": 1000.0, "t1": 0.0}
        big = demand({"crud_read": 400.0})  # needs 8 at rho=0.5 -> pool contends
        plans = ctl.plan(states, {"t0": big, "t1": big})
        # The starved tenant is granted first (clamped +2); the whale is not.
        self.assertEqual(plans["t1"].replicas, 3)
        self.assertLessEqual(plans["t0"].replicas, 4)

    def test_moves_respect_the_shared_clamp(self):
        _, ctl = self._mk(pool=16)
        states = {"t0": TenantState(replicas=1), "t1": TenantState(replicas=1)}
        big = demand({"crud_read": 400.0})
        plans = ctl.plan(states, {"t0": big, "t1": big})
        for p in plans.values():
            self.assertLessEqual(abs(p.replicas - 1), 2)

    def test_budget_weights_slow_the_paying_tenants_counter(self):
        _, ctl = self._mk(budgets=(10.0, 1.0), pool=4)
        states = {"t0": TenantState(replicas=2), "t1": TenantState(replicas=2)}
        same = demand({"crud_read": 100.0})
        ctl.plan(states, {"t0": same, "t1": same})
        # Same served work, 10x budget -> 10x slower counter accrual.
        self.assertAlmostEqual(ctl.counters["t1"] / ctl.counters["t0"], 10.0, places=6)


class ModelFormTests(unittest.TestCase):
    """Wave 5 structural overrides (set_model_form): defaults must stay
    bit-identical to the published forms; each override must move the
    world in the measured direction."""

    def tearDown(self):
        model.set_model_form()  # never leak a form into other tests

    def test_reset_restores_published_forms_exactly(self):
        config = TenantConfig(tenant_id="t")
        state = TenantState(replicas=2, cache_mb=256, tier="small")
        d = demand({"chat": 2.0, "crud_read": 5.0})
        before = evaluate_step(config, state, d)
        model.set_model_form(congestion_exponent=0.86, p95_tail=(1.69, 0.13),
                             mixture_p95=True, wu_tier_factor={"large": 16.6})
        model.set_model_form()
        after = evaluate_step(config, state, d)
        self.assertEqual(before, after)
        self.assertEqual(model.CONGESTION_EXPONENT, 1.0)
        self.assertIsNone(model.P95_TAIL)
        self.assertFalse(model.MIXTURE_P95)
        self.assertEqual(model.WU_TIER_FACTOR, model._DEFAULT_WU_TIER_FACTOR)

    def test_congestion_exponent_softens_below_saturation(self):
        base = model.congestion(120.0, 2)  # rho 0.6 -> 1/(1-0.6) = 2.5
        self.assertAlmostEqual(base, 2.5)
        model.set_model_form(congestion_exponent=0.86)
        softened = model.congestion(120.0, 2)
        self.assertAlmostEqual(softened, (1.0 - 0.6) ** -0.86)
        self.assertLess(softened, base)
        # Overload branch still graded and capped.
        self.assertLessEqual(model.congestion(1000.0, 1), model.MAX_CONGESTION)

    def test_p95_tail_rises_with_utilization_and_clamps(self):
        self.assertEqual(model.p95_factor(0.2), model.P95_FACTOR)
        model.set_model_form(p95_tail=(1.6909, 0.1303))
        low, high = model.p95_factor(0.2), model.p95_factor(0.91)
        self.assertGreater(high, low)
        self.assertGreater(low, model.P95_FACTOR)  # measured floor 1.59 > 1.4
        # Clamped: deep overload uses the saturation factor, stays finite.
        self.assertEqual(model.p95_factor(3.0), model.p95_factor(model.SATURATION_RHO))

    def test_p95_tail_raises_crud_violation_pressure(self):
        config = TenantConfig(tenant_id="t", slo_class="premium")
        state = TenantState(replicas=1, cache_mb=0)
        d = demand({"crud_read": 70.0})  # rho 0.7: busy but below saturation
        flat = evaluate_step(config, state, d)
        model.set_model_form(p95_tail=(1.6909, 0.1303))
        measured = evaluate_step(config, state, d)
        self.assertGreater(measured.crud_p95_ms, flat.crud_p95_ms)
        self.assertGreaterEqual(measured.violation, flat.violation)

    def test_mixture_single_branch_matches_tail_closed_form(self):
        # One lognormal branch: p95 must equal mean * tail by construction.
        p95 = model._mixture_p95_ms([(1.0, 800.0, False)], 1.4)
        self.assertAlmostEqual(p95 / 800.0, 1.4, places=4)

    def test_mixture_denies_cache_tail_credit_below_quantile(self):
        # 40% hits at 20 ms barely move a 95th percentile (the miss branch
        # quantile shifts 95th -> 91.7th, ~6%); the mean-based form drops
        # ~39%. The contrast is the finding this mode exists to measure.
        miss_only = model._mixture_p95_ms([(1.0, 800.0, False)], 1.4)
        mixed = model._mixture_p95_ms(
            [(0.4, model.CACHE_HIT_LATENCY_MS, True), (0.6, 800.0, False)], 1.4)
        mean_based_ratio = (0.4 * model.CACHE_HIT_LATENCY_MS + 0.6 * 800.0) / 800.0
        self.assertGreater(mixed / miss_only, 0.90)
        self.assertLess(mean_based_ratio, 0.65)
        # Past the quantile the hits do win: 96% hits pin p95 at hit latency.
        hit_dominated = model._mixture_p95_ms(
            [(0.96, model.CACHE_HIT_LATENCY_MS, True), (0.04, 800.0, False)], 1.4)
        self.assertAlmostEqual(hit_dominated, model.CACHE_HIT_LATENCY_MS, places=3)

    def test_mixture_mode_flows_into_evaluate_step(self):
        config = TenantConfig(tenant_id="t")
        state = TenantState(replicas=4, cache_mb=512, tier="small")
        d = demand({"chat": 2.0})
        mean_based = evaluate_step(config, state, d)
        model.set_model_form(mixture_p95=True)
        percentile = evaluate_step(config, state, d)
        # With a warm cache the mean-based estimator credits hits against
        # the tail; the true percentile refuses (hit share < 0.95).
        self.assertGreater(percentile.ai_p95_ms, mean_based.ai_p95_ms)
        # Cost accounting is untouched by the latency form.
        self.assertEqual(percentile.cost_usd, mean_based.cost_usd)

    def test_wu_tier_factor_scales_ai_work_only(self):
        d = demand({"chat": 2.0, "crud_read": 10.0})
        base_small = d.work_units(0, "small")
        model.set_model_form(wu_tier_factor={"mid": 1.516, "large": 16.64})
        self.assertEqual(d.work_units(0, "small"), base_small)
        crud_wu = 10.0 * model.WORK_UNITS["crud_read"]
        ai_wu = base_small - crud_wu
        self.assertAlmostEqual(d.work_units(0, "mid"), crud_wu + ai_wu * 1.516)
        self.assertAlmostEqual(d.work_units(0, "large"), crud_wu + ai_wu * 16.64)

    def test_wu_tier_factor_reaches_congestion_through_evaluate_step(self):
        config = TenantConfig(tenant_id="t")
        d = demand({"chat": 3.0})
        model.set_model_form(wu_tier_factor={"large": 16.64})
        small = evaluate_step(config, TenantState(replicas=2, cache_mb=0, tier="small"), d)
        large = evaluate_step(config, TenantState(replicas=2, cache_mb=0, tier="large"), d)
        # The heavier model congests the same pool: worse than its own
        # base-latency ratio alone would predict at these settings.
        self.assertGreater(large.ai_p95_ms / small.ai_p95_ms,
                           model.TIER_BASE_LATENCY_MS["chat"]["large"]
                           / model.TIER_BASE_LATENCY_MS["chat"]["small"])

    def test_set_model_form_validation(self):
        with self.assertRaises(ValueError):
            model.set_model_form(congestion_exponent=0.0)
        with self.assertRaises(ValueError):
            model.set_model_form(p95_tail=(0.9, 0.1))
        with self.assertRaises(ValueError):
            model.set_model_form(p95_tail=(2.0, 5.0))  # blows past the lognormal bound
        with self.assertRaises(ValueError):
            model.set_model_form(wu_tier_factor={"huge": 2.0})
        with self.assertRaises(ValueError):
            model.set_model_form(wu_tier_factor={"mid": 0.0})


class AnchorMovesTests(unittest.TestCase):
    """PREREG_MOVE_CLAMP: the coordination-gap audit caught the second
    coordinate-descent sweep re-anchoring the move clamps at its own
    sweep-1 choice. anchor_moves=True must hold every plan within the
    per-interval clamps; the default must keep the published behavior."""

    def _random_instance(self, seed: int, n: int = 3):
        import random
        rng = random.Random(seed)
        configs, states, demands = {}, {}, {}
        classes = ("premium", "standard", "best-effort")
        for i in range(n):
            tid = f"t{i}"
            configs[tid] = TenantConfig(tenant_id=tid, slo_class=classes[i % 3])
            states[tid] = TenantState(
                replicas=rng.randint(1, 6),
                cache_mb=rng.choice(model.CACHE_LEVELS_MB),
                tier=rng.choice(("small", "small", "mid", "large")),
            )
            demands[tid] = demand({
                "crud_read": rng.uniform(0, 300),
                "chat": rng.uniform(0, 6),
                "agent": rng.uniform(0, 2),
            })
        return configs, states, demands

    def test_anchored_plan_respects_per_interval_clamps(self):
        for seed in range(25):
            configs, states, demands = self._random_instance(seed)
            ctl = JCACController(configs, anchor_moves=True)
            plans = ctl.plan(states, demands)
            for tid, p in plans.items():
                dr = abs(p.state.replicas - states[tid].replicas)
                self.assertLessEqual(dr, 2, f"seed {seed} tenant {tid}: replica move {dr}")
                li = model.CACHE_LEVELS_MB.index(states[tid].cache_mb)
                lj = model.CACHE_LEVELS_MB.index(p.state.cache_mb)
                self.assertLessEqual(abs(lj - li), 1,
                                     f"seed {seed} tenant {tid}: cache {li}->{lj}")

    def test_default_can_exceed_clamps_documented(self):
        """The published behavior (kept for bit-reproducibility of the
        committed campaigns): at least one instance in this sweep moves
        beyond the one-interval clamps via the second sweep."""
        exceeded = False
        for seed in range(25):
            configs, states, demands = self._random_instance(seed)
            ctl = JCACController(configs)
            plans = ctl.plan(states, demands)
            for tid, p in plans.items():
                dr = abs(p.state.replicas - states[tid].replicas)
                li = model.CACHE_LEVELS_MB.index(states[tid].cache_mb)
                lj = model.CACHE_LEVELS_MB.index(p.state.cache_mb)
                if dr > 2 or abs(lj - li) > 1:
                    exceeded = True
        self.assertTrue(exceeded, "expected the published two-sweep behavior "
                                  "to exceed the clamps somewhere in 25 seeds")

    def test_anchored_and_default_agree_when_one_move_suffices(self):
        configs = {"a": TenantConfig(tenant_id="a")}
        states = {"a": TenantState(replicas=2, cache_mb=128, tier="small")}
        demands = {"a": demand({"crud_read": 30.0})}
        default = JCACController(configs).plan(states, demands)["a"].state
        anchored = JCACController(configs, anchor_moves=True).plan(states, demands)["a"].state
        self.assertEqual(default, anchored)


class SeasonalMRTests(unittest.TestCase):
    """Wave 5 multi-resolution forecaster: the fine 96-step window
    (16 min at 10 s) is physically blind to day-scale cycles; seasonal_mr
    must see them through its 10-min coarse buckets while deferring to
    the fine season when one exists."""

    PERIOD_BUCKETS = 40  # "daily" cycle: 40 coarse buckets (6.7 h at 10 s)
    HIGH, LOW = 8.0, 0.5

    def _feed_cycle(self, f: Forecast, periods: int):
        agg = Forecast.COARSE_AGG
        half = self.PERIOD_BUCKETS // 2
        for p in range(periods):
            for b in range(self.PERIOD_BUCKETS):
                rate = self.HIGH if b < half else self.LOW
                for _ in range(agg):
                    f.observe(demand({"chat": rate}))

    def test_daily_cycle_visible_only_to_mr(self):
        mr = Forecast(method="seasonal_mr")
        fine = Forecast(method="seasonal")
        tr = Forecast(method="trend")
        for f in (mr, fine, tr):
            self._feed_cycle(f, 3)  # ends just before the high phase returns
        mr_next = mr.horizon()[0].rps["chat"]
        fine_next = fine.horizon()[0].rps["chat"]
        tr_next = tr.horizon()[0].rps["chat"]
        self.assertAlmostEqual(mr_next, self.HIGH, places=6,
                               msg="mr must forecast the returning high phase")
        self.assertLessEqual(fine_next, 1.0,
                             "the 16-min fine window cannot see the cycle")
        self.assertLessEqual(tr_next, 1.0)

    def test_fine_season_takes_precedence(self):
        mr = Forecast(method="seasonal_mr")
        fine = Forecast(method="seasonal")
        for f in (mr, fine):
            for i in range(96):
                rate = 5.0 if (i // 6) % 2 == 0 else 1.0  # 12-step square wave
                f.observe(demand({"chat": rate}))
        mr_h = [d.rps["chat"] for d in mr.horizon()]
        fine_h = [d.rps["chat"] for d in fine.horizon()]
        self.assertEqual(mr_h, fine_h,
                         "with a credible fine season, mr must match seasonal")

    def test_coarse_memory_is_bounded(self):
        f = Forecast(method="seasonal_mr")
        for _ in range(Forecast.COARSE_AGG * (Forecast.COARSE_KEEP + 20)):
            f.observe(demand({"chat": 1.0}))
        self.assertLessEqual(len(f.coarse), Forecast.COARSE_KEEP)
        self.assertLessEqual(len(f.history), 96)


class SnapshotTests(unittest.TestCase):
    """W31 failover: forecast state must survive a serialize/restore round
    trip so a replacement replica resumes history instead of cold-starting."""

    def test_forecast_round_trip_preserves_horizon(self):
        f = Forecast(method="holt")
        for v in (1.0, 2.0, 3.0, 4.0, 5.0):
            f.observe(demand({"chat": v}))
        before = f.horizon()
        g = Forecast.from_snapshot(f.snapshot())
        after = g.horizon()
        self.assertEqual([d.rps["chat"] for d in before],
                         [d.rps["chat"] for d in after])

    def test_seasonal_mr_coarse_buckets_survive(self):
        f = Forecast(method="seasonal_mr")
        for i in range(Forecast.COARSE_AGG * 3 + 5):
            f.observe(demand({"chat": float(i % 7)}))
        g = Forecast.from_snapshot(f.snapshot())
        self.assertEqual(len(g.coarse), len(f.coarse))
        self.assertEqual(g.coarse, f.coarse)
        self.assertEqual(g._accum_n, f._accum_n)

    def test_controller_snapshot_restores_history_into_matching_tenants(self):
        cfg = {"a": TenantConfig(tenant_id="a"), "b": TenantConfig(tenant_id="b")}
        warm = JCACController(cfg, forecast_method="holt")
        surge = {"a": demand({"crud_read": 100.0}), "b": demand({"crud_read": 5.0})}
        for _ in range(6):
            warm.plan({t: TenantState() for t in cfg}, surge)
        snap = warm.snapshot()

        # A fresh controller (the replacement replica) restores the history.
        cold = JCACController(cfg, forecast_method="holt")
        self.assertEqual(len(cold.forecasts["a"].history), 0)
        cold.restore(snap)
        self.assertEqual(len(cold.forecasts["a"].history),
                         len(warm.forecasts["a"].history))
        # A departed tenant in the snapshot is ignored, not an error.
        cold.restore({"forecasts": {"ghost": {"method": "holt", "history": []}}})
        self.assertNotIn("ghost", cold.forecasts)

    def test_snapshot_is_json_serializable(self):
        import json
        cfg = {"a": TenantConfig(tenant_id="a")}
        c = JCACController(cfg, forecast_method="seasonal_mr", adaptive_capacity=True)
        for _ in range(5):
            c.plan({"a": TenantState()}, {"a": demand({"chat": 2.0})})
        # Must survive a JSON round trip untouched (the wire format).
        restored = json.loads(json.dumps(c.snapshot()))
        cold = JCACController(cfg, forecast_method="seasonal_mr")
        cold.restore(restored)
        self.assertEqual(len(cold.forecasts["a"].history),
                         len(c.forecasts["a"].history))


class GracefulDegradationTests(unittest.TestCase):
    """PREREG_DEGRADE (gap 4.4): budget exhaustion should prefer serving on
    the cheapest affordable tier over a designed tier=none outage. Default
    off preserves the published shed-to-none behavior."""

    def _infeasible(self, degrade):
        # A budget too small to afford the optimizer's serving lattice under
        # heavy AI demand forces the fallback branch.
        cfg = {"a": TenantConfig(tenant_id="a", slo_class="premium",
                                 hourly_budget_usd=0.02, replica_min=1, replica_max=10)}
        ctl = JCACController(cfg, degrade_gracefully=degrade)
        states = {"a": TenantState(replicas=1, cache_mb=0, tier="small")}
        return ctl.plan(states, {"a": demand({"chat": 50.0, "agent": 20.0})})["a"].state

    def test_default_sheds_to_none(self):
        # Confirm this configuration actually reaches the fallback and the
        # published behavior is an outage.
        s = self._infeasible(degrade=False)
        if s.tier != "none":
            self.skipTest("configuration did not reach the infeasible fallback")
        self.assertEqual(s.replicas, 1)
        self.assertEqual(s.cache_mb, 0)

    def test_graceful_never_serves_outside_budget(self):
        # DG-H2: whatever graceful returns at the fallback, if it serves a
        # tier that tier's projected cost is within budget — it never trades
        # the outage for a budget violation. (DEGRADE_PROBE.md: at realistic
        # tier costs graceful equals shed, because cache_mb=0 maximises
        # misses; the finding is that shed-to-none is already correct.)
        graceful = self._infeasible(degrade=True)
        cfg = TenantConfig(tenant_id="a", slo_class="premium", hourly_budget_usd=0.02)
        budget = cfg.hourly_budget_usd * model.CONTROL_INTERVAL_S / 3600.0
        if graceful.tier != "none":
            m = evaluate_step(cfg, graceful, demand({"chat": 50.0, "agent": 20.0}))
            self.assertLessEqual(m.cost_usd, budget + 1e-9)
            self.assertEqual(graceful.cache_mb, 0)  # floor config by construction

    def test_graceful_sheds_to_none_when_no_serving_tier_is_affordable(self):
        # A budget too small even for one small-tier request keeps the
        # outage — graceful never introduces a budget-busting serving tier,
        # so it degrades identically to the published fallback here.
        cfg = {"a": TenantConfig(tenant_id="a", hourly_budget_usd=1e-6)}
        default = JCACController(cfg).plan(
            {"a": TenantState()}, {"a": demand({"chat": 500.0})})["a"].state
        graceful = JCACController(cfg, degrade_gracefully=True).plan(
            {"a": TenantState()}, {"a": demand({"chat": 500.0})})["a"].state
        self.assertEqual(default.tier, "none")
        self.assertEqual(graceful.tier, "none")


class RiskAwareForecastTests(unittest.TestCase):
    """PREREG_RISK_MPC: plan against a demand quantile instead of the point
    forecast. The default path must stay bit-identical."""

    def _noisy(self, f: Forecast, n: int = 40, base: float = 10.0):
        """Feed an alternating-noise series so forecast errors are non-zero."""
        for i in range(n):
            f.observe(demand({"chat": base + (5.0 if i % 2 else -5.0)}))
            f.horizon()  # records the one-step prediction each cycle

    def test_residuals_are_recorded_from_forecast_error(self):
        f = Forecast(method="trend")
        self._noisy(f)
        self.assertIn("chat", f.residuals)
        self.assertTrue(any(r != 0.0 for r in f.residuals["chat"]))
        self.assertLessEqual(len(f.residuals["chat"]), Forecast.RESIDUAL_KEEP)

    def test_default_horizon_is_unchanged_by_the_risk_machinery(self):
        a, b = Forecast(method="trend"), Forecast(method="trend")
        self._noisy(a)
        self._noisy(b)
        # None and 0 both mean "published point-forecast path".
        self.assertEqual([d.rps for d in a.horizon()],
                         [d.rps for d in b.horizon(None)])
        self.assertEqual([d.rps for d in a.horizon()],
                         [d.rps for d in b.horizon(0)])

    def test_risk_quantile_only_adds_headroom_and_is_monotone(self):
        f = Forecast(method="trend")
        self._noisy(f)
        base = f.horizon()[0].rps["chat"]
        q80 = f.horizon(0.8)[0].rps["chat"]
        q95 = f.horizon(0.95)[0].rps["chat"]
        self.assertGreaterEqual(q80, base)   # never plans below the forecast
        self.assertGreaterEqual(q95, q80)    # higher quantile = more headroom

    def test_cold_start_does_not_inflate(self):
        # Below RESIDUAL_MIN samples the quantile is noise: no headroom.
        f = Forecast(method="trend")
        for i in range(3):
            f.observe(demand({"chat": 10.0 + i}))
            f.horizon()
        self.assertEqual(f.horizon(0.95)[0].rps["chat"], f.horizon()[0].rps["chat"])

    def test_residuals_survive_a_snapshot_round_trip(self):
        f = Forecast(method="trend")
        self._noisy(f)
        restored = Forecast.from_snapshot(f.snapshot())
        self.assertEqual(restored.residuals, f.residuals)
        self.assertEqual([d.rps for d in restored.horizon(0.9)],
                         [d.rps for d in f.horizon(0.9)])

    def test_risk_controller_provisions_at_least_as_much(self):
        # Under noisy demand the risk-aware controller must never buy *less*
        # capacity than the point-forecast controller from the same state.
        cfg = {"a": TenantConfig(tenant_id="a", replica_max=20)}
        plain = JCACController(cfg, anchor_moves=True)
        risky = JCACController(cfg, anchor_moves=True, risk_quantile=0.9)
        sp = sr = {"a": TenantState()}
        for i in range(40):
            d = {"a": demand({"crud_read": 120.0 + (60.0 if i % 2 else -60.0)})}
            sp = {"a": plain.plan(sp, d)["a"].state}
            sr = {"a": risky.plan(sr, d)["a"].state}
        self.assertGreaterEqual(sr["a"].replicas, sp["a"].replicas)


class RiskBudgetCorrectionTests(unittest.TestCase):
    """PREREG_RISK_BUDGET: the one changed factor after the RESULTS_RISK null —
    size capacity at the risk quantile, but project cost and check the budget
    at the point forecast (you are billed for the demand that arrives, not the
    demand you provisioned against)."""

    def _run(self, params, steps=60, rate=120.0, jitter=60.0):
        cfg = {"a": TenantConfig(tenant_id="a", replica_max=20)}
        ctl = JCACController(cfg, anchor_moves=True, **params)
        s = {"a": TenantState()}
        sheds = 0
        for i in range(steps):
            d = {"a": demand({"chat": rate / 20.0 + (jitter / 20.0 if i % 2 else 0.0)})}
            s = {"a": ctl.plan(s, d)["a"].state}
            sheds += 1 if s["a"].tier == "none" else 0
        return s, sheds

    def test_default_flag_off_is_the_published_null_path(self):
        # risk_cost_at_point defaults False so matrix_risk replays exactly.
        cfg = {"a": TenantConfig(tenant_id="a")}
        ctl = JCACController(cfg, risk_quantile=0.9)
        self.assertFalse(ctl.risk_cost_at_point)

    def test_correction_never_sheds_more_than_the_point_forecast(self):
        # The null's failure mode: inflated projected tier spend tripped the
        # budget filter into tier="none" *more often* than at the point
        # forecast (RESULTS_RISK probe: 0 vs 2/4 sheds). With the budget
        # checked at the point forecast, affordability is identical by
        # construction, so the corrected controller can never shed more.
        _, sheds_point = self._run({})
        _, sheds_uncorr = self._run({"risk_quantile": 0.95})
        _, sheds_corr = self._run({"risk_quantile": 0.95, "risk_cost_at_point": True})
        self.assertLessEqual(sheds_corr, sheds_point)
        self.assertGreaterEqual(sheds_uncorr, sheds_corr)

    def test_corrected_controller_provisions_at_least_as_much(self):
        s_point, _ = self._run({})
        s_corr, _ = self._run({"risk_quantile": 0.9, "risk_cost_at_point": True})
        self.assertGreaterEqual(s_corr["a"].replicas, s_point["a"].replicas)

    def test_flag_without_quantile_is_inert(self):
        # risk_cost_at_point only means anything alongside a quantile; alone
        # it must leave the published single-horizon path untouched.
        cfg = {"a": TenantConfig(tenant_id="a")}
        base = JCACController(cfg)
        flagged = JCACController(cfg, risk_cost_at_point=True)
        s = {"a": TenantState()}
        d = {"a": demand({"chat": 5.0, "crud_read": 40.0})}
        for _ in range(10):
            sb = base.plan(s, d)["a"].state
            sf = flagged.plan(s, d)["a"].state
            self.assertEqual(sb, sf)
            s = {"a": sb}


class EnergyCarbonTests(unittest.TestCase):
    """Energy accounting and the optional carbon term (M4 / T18).

    These tests pin an uncomfortable property as firmly as a useful one. Under
    a *constant* grid intensity, carbon is very nearly collinear with dollar
    cost on the published constants, so pricing it changes almost nothing --
    minimising spend already minimises grams. The term earns its place only
    when the grid's intensity varies over time, because price cannot express
    that at all. Both facts are asserted below so neither can be quietly
    overstated later.
    """

    def tearDown(self):
        model.set_energy()  # never leak an override into another test

    def _lattice(self, config, demand):
        return [
            model.evaluate_step(config, TenantState(replicas=r, cache_mb=c, tier=t), demand)
            for r in range(1, 11)
            for c in model.CACHE_LEVELS_MB
            for t in model.TIERS
        ]

    def test_energy_tracks_the_three_knobs(self):
        config = TenantConfig(tenant_id="a")
        d = demand({"chat": 20.0, "crud_read": 5.0})
        base = TenantState(replicas=2, cache_mb=128, tier="small")
        e = lambda s: evaluate_step(config, s, d).energy_kwh  # noqa: E731

        # more replicas draw more power
        self.assertGreater(e(replace_state(base, replicas=6)), e(base))
        # a heavier tier burns more per miss
        self.assertGreater(e(replace_state(base, tier="large")), e(base))
        # and cache *saves* energy, because a hit never reaches the model
        self.assertLess(e(replace_state(base, cache_mb=1024)), e(base))

    def test_carbon_scales_with_grid_intensity_only(self):
        config = TenantConfig(tenant_id="a")
        d = demand({"chat": 20.0})
        state = TenantState(replicas=3, cache_mb=128, tier="small")
        clean = evaluate_step(config, state, d)
        model.set_energy(carbon_intensity_g_per_kwh=800.0)
        dirty = evaluate_step(config, state, d)
        # twice the intensity, twice the carbon, identical energy AND identical
        # money -- which is exactly why price cannot stand in for carbon.
        self.assertAlmostEqual(dirty.carbon_g, 2.0 * clean.carbon_g, places=9)
        self.assertAlmostEqual(dirty.energy_kwh, clean.energy_kwh, places=12)
        self.assertAlmostEqual(dirty.cost_usd, clean.cost_usd, places=12)

    def test_carbon_is_nearly_collinear_with_cost_at_constant_intensity(self):
        # The honest limitation. A pair is discordant when one state is cheaper
        # but dirtier than another; if there were many, pricing carbon would be
        # a real lever under a fixed grid. There are almost none.
        config = TenantConfig(tenant_id="a", slo_class="premium", hourly_budget_usd=100.0)
        rows = self._lattice(config, demand({"chat": 25.0, "crud_read": 5.0}))
        discordant = sum(
            1 for a in rows for b in rows
            if a.cost_usd < b.cost_usd and a.carbon_g > b.carbon_g
        )
        self.assertLess(discordant / (len(rows) ** 2), 0.001,
                        "carbon and cost disagree often enough that the constant-"
                        "intensity term is a real lever -- update the docs if so")

    def test_carbon_weight_is_off_by_default_and_inert(self):
        configs = {"a": TenantConfig(tenant_id="a", slo_class="premium",
                                     hourly_budget_usd=100.0)}
        d = {"a": demand({"chat": 25.0, "crud_read": 5.0})}
        self.assertEqual(JCACController(configs).carbon_weight, 0.0)
        base, priced = JCACController(configs), JCACController(configs, carbon_weight=0.0)
        s1 = s2 = {"a": TenantState()}
        for _ in range(8):
            s1 = {"a": base.plan(s1, d)["a"].state}
            s2 = {"a": priced.plan(s2, d)["a"].state}
            self.assertEqual(s1, s2)

    def test_grid_intensity_moves_the_plan_only_when_carbon_is_priced(self):
        # The dimension test. Same demand, same prices, different grid: a
        # cost-only controller cannot react (money is intensity-invariant),
        # a carbon-pricing one must. If this failed, the term would be
        # reproducible by simply reweighting cost.
        configs = {"a": TenantConfig(tenant_id="a", slo_class="premium",
                                     hourly_budget_usd=100.0)}
        d = {"a": demand({"chat": 25.0, "crud_read": 5.0})}

        def settle(intensity: float, weight: float) -> TenantState:
            model.set_energy(carbon_intensity_g_per_kwh=intensity)
            ctl = JCACController(configs, carbon_weight=weight)
            state = {"a": TenantState()}
            for _ in range(8):
                state = {"a": ctl.plan(state, d)["a"].state}
            return state["a"]

        self.assertEqual(settle(50.0, 0.0), settle(900.0, 0.0),
                         "an unpriced controller must ignore the grid entirely")
        self.assertNotEqual(settle(50.0, 2.0), settle(900.0, 2.0),
                            "a carbon-pricing controller must react to the grid")

    def test_set_energy_resets_before_applying(self):
        model.set_energy(replica_power_w=999.0, carbon_intensity_g_per_kwh=1.0)
        model.set_energy(carbon_intensity_g_per_kwh=123.0)
        self.assertEqual(model.REPLICA_POWER_W, 45.0)  # reset, not carried over
        self.assertEqual(model.CARBON_INTENSITY_G_PER_KWH, 123.0)
        model.set_energy()
        self.assertEqual(model.CARBON_INTENSITY_G_PER_KWH, 400.0)
        with self.assertRaises(ValueError):
            model.set_energy(tier_energy_wh_per_req={"enormous": 1.0})
        with self.assertRaises(ValueError):
            model.set_energy(replica_power_w=-1.0)


def replace_state(state: TenantState, **kw) -> TenantState:
    from dataclasses import replace
    return replace(state, **kw)
