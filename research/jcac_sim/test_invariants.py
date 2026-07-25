"""Property tests for the controller's own actuation contract.

Why this file exists. The Wave 5 solver audit (PREREG_COORD_GAP) found that
the published controller could move a tenant ±4 replicas and two cache levels
in one interval while every baseline was clamped to ±2 and one level: the
second coordinate-descent sweep re-anchored the move clamps at its own
sweep-1 choice, so coordination compounded the actuation limits. The defect
was real, it was disclosed (RESULTS_MOVE_CLAMP.md), and it was adjudicated by
a pre-registered rerun that made `anchor_moves=True` the quotable controller.

The part worth fixing in the *engineering*: nothing in the test suite could
have caught it. The unit tests check that the controller scales up under
surge and respects budget and cluster caps -- all behavioral, none asserting
the invariants the controller claims about its own moves. A defect in that
class stayed invisible until a dedicated audit went looking, by which point
the numbers were published.

These tests assert the contract directly, over a seeded grid of states and
demands rather than one hand-picked scenario:

  * per-interval actuation clamps (the audited class),
  * state bounds -- replica floor/ceiling, cache on the level ladder, tier in
    the declared set,
  * determinism under a fixed seed.

`test_published_controller_move_bound_is_the_disclosed_2x` deliberately pins
the *unanchored* default's known 2x reach instead of failing on it. That
behavior is the published, bit-reproducible record; the test's job is to stop
it drifting further, and to fail loudly if anyone "fixes" it silently and
invalidates the committed matrices.
"""

from __future__ import annotations

import itertools
import random
import unittest

import model
from controller import DELTA_REPLICAS, ClusterLimits, JCACController
from model import CACHE_LEVELS_MB, TIERS, Demand, TenantConfig, TenantState

# The actuation contract, written as literals on purpose.
#
# Deriving these from DELTA_REPLICAS / CACHE_LEVELS_MB would make the tests
# tautological: widening the lattice would widen the oracle with it and every
# assertion would still pass. (Confirmed by mutation -- an earlier draft of
# this file derived MAX_REPLICA_STEP from the module and did not notice
# DELTA_REPLICAS being widened to +/-4.) These numbers are the frozen
# specification -- the same per-interval limits every baseline is held to,
# which is what makes the comparison fair -- so they belong here as constants
# the implementation must satisfy, not as facts read back from it.
MAX_REPLICA_STEP = 2
MAX_CACHE_LEVEL_STEP = 1
KINDS = ("crud_read", "crud_write", "chat", "embed", "agent")


def demand(rps: dict[str, float], crud_base_ms: float = 50.0) -> Demand:
    return Demand(rps=dict(rps), crud_base_ms=crud_base_ms)


def cache_level_index(cache_mb: int) -> int:
    return CACHE_LEVELS_MB.index(cache_mb)


class ActuationContractTests(unittest.TestCase):
    """Pin the lattice itself against the frozen contract.

    The behavioural tests below assert against literal limits. This one
    asserts that the *implementation's* lattice still matches those literals,
    so widening the lattice fails here -- loudly, naming the contract -- and
    does not quietly redefine what the other tests mean."""

    def test_replica_lattice_matches_the_frozen_clamp(self):
        self.assertEqual(max(DELTA_REPLICAS), MAX_REPLICA_STEP)
        self.assertEqual(min(DELTA_REPLICAS), -MAX_REPLICA_STEP)
        self.assertEqual(DELTA_REPLICAS, (-2, -1, 0, 1, 2))

    def test_cache_ladder_is_the_frozen_one(self):
        # neighbour_cache_levels() moves at most one index; the ladder's
        # values are what the published economy and every record assume.
        self.assertEqual(CACHE_LEVELS_MB, (0, 64, 128, 256, 512, 1024))

    def test_tier_set_is_the_frozen_one(self):
        self.assertEqual(TIERS, ("none", "small", "mid", "large"))


class ControllerInvariantTests(unittest.TestCase):
    """The contract holds across a grid, not just on a chosen example."""

    TENANTS = ("a", "b", "c")

    def setUp(self):
        # These tests never vary the economy or model form; reset so a
        # neighbouring test's override cannot leak in (PlannerCore precedent).
        model.set_economy()
        model.set_model_form()

    def tearDown(self):
        model.set_economy()
        model.set_model_form()

    def _controller(self, **kw) -> JCACController:
        configs = {
            "a": TenantConfig(tenant_id="a", slo_class="premium",
                              hourly_budget_usd=5.0, replica_min=1, replica_max=10),
            "b": TenantConfig(tenant_id="b", slo_class="standard",
                              hourly_budget_usd=5.0, replica_min=2, replica_max=8),
            "c": TenantConfig(tenant_id="c", slo_class="best-effort",
                              hourly_budget_usd=5.0, replica_min=1, replica_max=6),
        }
        kw.setdefault("limits", ClusterLimits(cache_mb=4096, replicas=60))
        return JCACController(configs, **kw)

    def _scenarios(self, seed: int = 20260726, n: int = 24):
        """Seeded (start state, demand sequence) pairs spanning idle, surge,
        overload and collapse -- the regimes where a clamp bug would show."""
        rng = random.Random(seed)
        for _ in range(n):
            states = {
                tid: TenantState(
                    replicas=rng.randint(1, 6),
                    cache_mb=rng.choice(CACHE_LEVELS_MB),
                    tier=rng.choice(TIERS),
                )
                for tid in self.TENANTS
            }
            scale = rng.choice([0.1, 1.0, 20.0, 400.0])
            steps = [
                {tid: demand({k: rng.uniform(0.0, scale) for k in KINDS})
                 for tid in self.TENANTS}
                for _ in range(4)
            ]
            yield states, steps

    def test_anchored_controller_honours_per_interval_clamps(self):
        """The quotable controller: no plan moves further in one interval
        than the lattice allows -- the exact property the audit found the
        published default breaking."""
        for states, steps in self._scenarios():
            ctl = self._controller(anchor_moves=True)
            current = states
            for demands in steps:
                plans = ctl.plan(current, demands)
                for tid, entry in plans.items():
                    before, after = current[tid], entry.state
                    self.assertLessEqual(
                        abs(after.replicas - before.replicas), MAX_REPLICA_STEP,
                        f"{tid}: replicas {before.replicas} -> {after.replicas} "
                        f"exceeds the +/-{MAX_REPLICA_STEP} per-interval clamp")
                    self.assertLessEqual(
                        abs(cache_level_index(after.cache_mb)
                            - cache_level_index(before.cache_mb)), MAX_CACHE_LEVEL_STEP,
                        f"{tid}: cache {before.cache_mb} -> {after.cache_mb} "
                        "moves more than one level in one interval")
                current = {tid: p.state for tid, p in plans.items()}

    def test_published_controller_move_bound_is_the_disclosed_2x(self):
        """The unanchored default is the published, bit-reproducible record.
        Its two sweeps can compound one clamp each, so the reach is 2x -- the
        audited, disclosed behaviour (RESULTS_MOVE_CLAMP.md). Pinned, not
        asserted-away: a wider move is a new defect, and a narrower one means
        the published matrices no longer reproduce."""
        observed_over_single_clamp = False
        for states, steps in self._scenarios():
            ctl = self._controller(anchor_moves=False)
            current = states
            for demands in steps:
                plans = ctl.plan(current, demands)
                for tid, entry in plans.items():
                    delta = abs(entry.state.replicas - current[tid].replicas)
                    self.assertLessEqual(
                        delta, 2 * MAX_REPLICA_STEP,
                        f"{tid}: replica move {delta} exceeds even the "
                        "disclosed two-sweep bound -- a new defect")
                    observed_over_single_clamp |= delta > MAX_REPLICA_STEP
                current = {tid: p.state for tid, p in plans.items()}
        self.assertTrue(
            observed_over_single_clamp,
            "no move exceeded a single clamp: the published controller's "
            "disclosed 2x behaviour appears to have changed, which would "
            "invalidate the committed jcac matrices -- rerun the clamp audit")

    def test_states_stay_within_declared_bounds(self):
        """Replica floor/ceiling, cache on the ladder, tier in the set --
        for both controller postures and every scenario."""
        for anchor in (True, False):
            for states, steps in self._scenarios():
                ctl = self._controller(anchor_moves=anchor)
                current = states
                for demands in steps:
                    plans = ctl.plan(current, demands)
                    for tid, entry in plans.items():
                        config, s = ctl.configs[tid], entry.state
                        self.assertGreaterEqual(s.replicas, config.replica_min, tid)
                        self.assertLessEqual(s.replicas, config.replica_max, tid)
                        self.assertIn(s.cache_mb, CACHE_LEVELS_MB, tid)
                        self.assertIn(s.tier, TIERS, tid)
                    current = {tid: p.state for tid, p in plans.items()}

    def test_cluster_limits_are_never_exceeded(self):
        """Cluster-wide caps bind the joint plan, not just each tenant."""
        limits = ClusterLimits(cache_mb=1024, replicas=12)
        for anchor in (True, False):
            for states, steps in self._scenarios(n=12):
                ctl = self._controller(anchor_moves=anchor, limits=limits)
                # Start inside the caps; the controller must keep it there.
                current = {tid: TenantState(replicas=ctl.configs[tid].replica_min,
                                            cache_mb=0, tier="none")
                           for tid in self.TENANTS}
                del states
                for demands in steps:
                    plans = ctl.plan(current, demands)
                    total_replicas = sum(p.state.replicas for p in plans.values())
                    total_cache = sum(p.state.cache_mb for p in plans.values())
                    self.assertLessEqual(total_replicas, limits.replicas)
                    self.assertLessEqual(total_cache, limits.cache_mb)
                    current = {tid: p.state for tid, p in plans.items()}

    def test_planning_is_deterministic(self):
        """Same construction, same inputs, same plans -- the property every
        seeded matrix and every bit-identical rerun depends on."""
        for states, steps in self._scenarios(n=8):
            runs = []
            for _ in range(2):
                ctl = self._controller(anchor_moves=True)
                current, trace = states, []
                for demands in steps:
                    plans = ctl.plan(current, demands)
                    trace.append({tid: p.state for tid, p in plans.items()})
                    current = trace[-1]
                runs.append(trace)
            self.assertEqual(runs[0], runs[1])

    def test_clamps_hold_from_every_lattice_corner(self):
        """Exhaustive over start states rather than sampled: the corners
        (cache floor/ceiling, replica floor/ceiling) are where an
        off-by-one in the ladder or the bound would hide."""
        ctl = self._controller(anchor_moves=True)
        surge = {tid: demand({"chat": 200.0, "agent": 50.0}) for tid in self.TENANTS}
        collapse = {tid: demand({"crud_read": 0.01}) for tid in self.TENANTS}
        corners = itertools.product((1, 10), (CACHE_LEVELS_MB[0], CACHE_LEVELS_MB[-1]),
                                    (TIERS[0], TIERS[-1]))
        for replicas, cache_mb, tier in corners:
            for demands in (surge, collapse):
                fresh = self._controller(anchor_moves=True)
                current = {tid: TenantState(replicas=replicas, cache_mb=cache_mb,
                                            tier=tier)
                           for tid in self.TENANTS}
                # Clamp is relative to the *clamped* start: a start outside a
                # tenant's own bounds is snapped first, which is the operator's
                # guardrail, not a controller move.
                plans = fresh.plan(current, demands)
                for tid, entry in plans.items():
                    config = fresh.configs[tid]
                    start = max(config.replica_min, min(config.replica_max, replicas))
                    self.assertLessEqual(
                        abs(entry.state.replicas - start), MAX_REPLICA_STEP,
                        f"{tid}: from (r={replicas}, cache={cache_mb}, {tier})")
                    self.assertLessEqual(
                        abs(cache_level_index(entry.state.cache_mb)
                            - cache_level_index(cache_mb)), MAX_CACHE_LEVEL_STEP,
                        f"{tid}: from (r={replicas}, cache={cache_mb}, {tier})")
        del ctl


if __name__ == "__main__":
    unittest.main()
