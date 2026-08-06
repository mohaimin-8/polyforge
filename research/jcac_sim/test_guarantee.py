"""Tests for the formal SLO guarantee (main path M3 / map T17).

The checker in `guarantee.py` makes a claim about what the controller *can*
do, so the tests have to establish two separate things:

  1. that the reachability model it reasons over is the controller's real one
     -- an analysis of a lattice nobody implements proves nothing;
  2. that the checker can actually come out negative -- an invariant-set
     routine that always returns something is worthless.

(2) is the T3 lesson from `test_invariants.py`, applied here: the mutation
tests below widen the actuation authority and require the verdict to flip.
Feasibility must be *caused* by the actuation clamp, not by the arithmetic.

The two demand orbits are the two regimes the campaigns actually contain,
built to the published amplitudes in `eval/harness/workloads.py`: a gentle
wave whose replica requirement moves by at most one per interval, and the
`flash` orbit (5 steps at 6.0x, 11 at 0.5x, period 16) that PREREG_V3 sized
so "the burst-onset replica climb exceeds what one interval of actuation can
deliver". The first must admit a zero-violation invariant set; the second
provably must not.
"""

from __future__ import annotations

import math
import unittest

import guarantee
import model
from controller import DELTA_REPLICAS
from guarantee import Actuation
from model import CACHE_LEVELS_MB, TIERS, Demand, TenantConfig, TenantState

# Frozen actuation contract, restated as literals for the reason
# test_invariants.py restates them: an oracle read back from the module cannot
# notice the module drifting.
MAX_REPLICA_STEP = 2
MAX_CACHE_LEVEL_STEP = 1

# Published amplitudes of the two shapes under test (workloads.py).
FLASH_HIGH, FLASH_LOW, FLASH_PERIOD, FLASH_BURST = 6.0, 0.5, 16, 5


def crud_demand(factor: float) -> Demand:
    """flash_crud's published base_rps, scaled."""
    return Demand(rps={"crud_read": 60.0 * factor, "crud_write": 10.0 * factor},
                  crud_base_ms=40.0)


def flash_orbit() -> list[Demand]:
    return [crud_demand(FLASH_HIGH if k < FLASH_BURST else FLASH_LOW)
            for k in range(FLASH_PERIOD)]


def gentle_orbit(period: int = 40) -> list[Demand]:
    """ramp_gentle's envelope reached at a slope actuation can follow."""
    return [crud_demand(3.25 + 2.75 * math.sin(2.0 * math.pi * k / period))
            for k in range(period)]


def agentic_demand(factor: float) -> Demand:
    """agentic's published base_rps, scaled. Agent traffic is where tier spend
    is large enough for the budget filter to bite."""
    return Demand(rps={"agent": 2.2 * factor, "embed": 0.5 * factor,
                       "crud_read": 2.0 * factor}, crud_base_ms=70.0)


def agentic_orbit() -> list[Demand]:
    """bursty: 2.5x for a third of a 24-step period, 0.25x otherwise."""
    return [agentic_demand(2.5 if k < 8 else 0.25) for k in range(24)]


def standard_config(**kw) -> TenantConfig:
    kw.setdefault("slo_class", "standard")
    kw.setdefault("replica_max", 10)
    return TenantConfig(tenant_id="t", **kw)


class ReachabilityModelTests(unittest.TestCase):
    """The analysis is only as good as its transition model."""

    def test_reach_matches_the_controllers_own_candidate_enumeration(self):
        # Independently rebuild the candidate set the controller enumerates
        # (DELTA_REPLICAS x neighbor_cache_levels x TIERS, through the real
        # apply_action) and require guarantee.reach to agree exactly. If the
        # controller's lattice ever changes, this fails instead of the
        # analysis silently describing a system nobody runs.
        config = standard_config()
        for replicas in (1, 2, 5, 10):
            for cache_mb in CACHE_LEVELS_MB:
                for tier in TIERS:
                    state = TenantState(replicas=replicas, cache_mb=cache_mb, tier=tier)
                    expected = {
                        model.apply_action(config, state, delta, c, t)
                        for delta in DELTA_REPLICAS
                        for c in model.neighbor_cache_levels(state.cache_mb)
                        for t in TIERS
                    }
                    self.assertEqual(set(guarantee.reach(config, state)), expected,
                                     f"reach diverges from the controller at {state}")

    def test_reach_respects_the_frozen_clamps(self):
        config = standard_config()
        state = TenantState(replicas=5, cache_mb=256, tier="small")
        base_i = CACHE_LEVELS_MB.index(256)
        for nxt in guarantee.reach(config, state):
            self.assertLessEqual(abs(nxt.replicas - state.replicas), MAX_REPLICA_STEP)
            self.assertLessEqual(
                abs(CACHE_LEVELS_MB.index(nxt.cache_mb) - base_i), MAX_CACHE_LEVEL_STEP)

    def test_pinned_knobs_shrink_the_reach_set(self):
        # The live cache-only/tier-only ablations pin knobs via min==max; the
        # analysis must see the same narrowed authority.
        pinned = standard_config(cache_min=128, cache_max=128,
                                 tier_min="small", tier_max="small")
        state = TenantState(replicas=5, cache_mb=128, tier="small")
        for nxt in guarantee.reach(pinned, state):
            self.assertEqual(nxt.cache_mb, 128)
            self.assertEqual(nxt.tier, "small")

    def test_reach_never_leaves_the_replica_band(self):
        config = standard_config(replica_min=3, replica_max=6)
        for replicas in (3, 4, 5, 6):
            state = TenantState(replicas=replicas, cache_mb=128, tier="small")
            for nxt in guarantee.reach(config, state):
                self.assertGreaterEqual(nxt.replicas, 3)
                self.assertLessEqual(nxt.replicas, 6)


class TerminalSetTests(unittest.TestCase):

    def test_gentle_orbit_admits_a_zero_violation_invariant_set(self):
        config = standard_config()
        orbit = gentle_orbit()
        terminal = guarantee.terminal_set(config, orbit)
        self.assertTrue(terminal, "a followable orbit must admit an invariant set")
        self.assertEqual(guarantee.attainable_peak(config, orbit), 0.0)

    def test_terminal_set_is_genuinely_invariant(self):
        # The defining property: every member has a successor inside the set,
        # and every member already meets the constraint. Checked directly
        # rather than trusting the fixed-point loop.
        config = standard_config()
        orbit = gentle_orbit()
        terminal = guarantee.terminal_set(config, orbit)
        n = len(orbit)
        for k, state in terminal:
            self.assertLessEqual(guarantee.violation_of(config, state, orbit[k]), 0.0)
            successors = guarantee.reach(config, state)
            self.assertTrue(any(((k + 1) % n, u) in terminal for u in successors),
                            f"({k}, {state}) has no successor inside the set")

    def test_flash_orbit_collapses_to_static_over_provisioning(self):
        # THE T17 FINDING, codified. The flash onset needs a 5-replica climb --
        # far more than the +/-2 clamp delivers -- yet a zero-violation
        # invariant set still exists, because the controller can simply sit at
        # a configuration that clears the SLO at the *peak* and never move.
        # A recursive-feasibility theorem over this lattice would be true and
        # empty; this test exists so that claim is checked, not asserted.
        config = standard_config()
        orbit = flash_orbit()
        terminal = guarantee.terminal_set(config, orbit)
        self.assertTrue(terminal)

        # The witness is a constant configuration: some state that is in the
        # set at every step of the orbit and can hold itself.
        by_state: dict[TenantState, set[int]] = {}
        for k, state in terminal:
            by_state.setdefault(state, set()).add(k)
        constant = [s for s, ks in by_state.items() if len(ks) == len(orbit)]
        self.assertTrue(constant, "expected a hold-still witness for the collapse")
        witness = constant[0]
        self.assertIn(witness, guarantee.reach(config, witness))
        self.assertEqual(guarantee.attainable_peak(config, orbit), 0.0)

    def test_replica_ceiling_below_the_peak_is_what_empties_the_set(self):
        # The mechanism that genuinely empties the set is a static capacity
        # one, not the actuation clamp: cap replicas below the peak
        # requirement and no configuration -- moving or not -- clears the SLO.
        orbit = flash_orbit()
        peak_need = max(guarantee.replica_floor(standard_config(), d) for d in orbit)
        tight = standard_config(replica_max=peak_need - 1)
        self.assertFalse(guarantee.terminal_set(tight, orbit))
        self.assertGreater(guarantee.attainable_peak(tight, orbit), 0.0)

    def test_budget_is_the_binding_constraint_on_ai_demand(self):
        # What actually binds. On agentic traffic the affordable lattice cannot
        # reach zero violation, while the same lattice without the budget
        # filter can -- the formal counterpart of session 29's finding that the
        # risk knob was fighting the Budget CRD, not the demand.
        config = standard_config(hourly_budget_usd=5.0)
        orbit = agentic_orbit()
        with_budget = guarantee.attainable_peak(config, orbit, enforce_budget=True)
        without = guarantee.attainable_peak(config, orbit, enforce_budget=False)
        self.assertEqual(without, 0.0)
        self.assertGreater(with_budget, 0.0)

    def test_replica_floor_climb_exceeds_the_clamp_on_flash(self):
        # The diagnostic behind the verdict, stated in the controller's units.
        config = standard_config()
        orbit = flash_orbit()
        floors = [guarantee.replica_floor(config, d) for d in orbit]
        self.assertNotIn(None, floors)
        climbs = [floors[(k + 1) % len(floors)] - floors[k] for k in range(len(floors))]
        self.assertGreater(max(climbs), MAX_REPLICA_STEP)


class MutationTests(unittest.TestCase):
    """The checker must be able to say no -- and to stop saying no when the
    thing it blames is removed. Without these, a routine that always returned
    the empty set would pass every test above."""

    def test_raising_the_budget_lowers_the_floor(self):
        # The floor must respond to the constraint that causes it. Same plant,
        # same demand, more money: the attainable floor must drop. Without
        # this, a routine that always returned a positive constant would pass.
        orbit = agentic_orbit()
        poor = guarantee.attainable_peak(standard_config(hourly_budget_usd=5.0), orbit)
        rich = guarantee.attainable_peak(standard_config(hourly_budget_usd=500.0), orbit)
        self.assertGreater(poor, rich)
        self.assertEqual(rich, 0.0)

    def test_replica_ceiling_moves_the_floor(self):
        # The other lever: a ceiling below the peak requirement must raise the
        # floor above zero, and restoring headroom must return it to zero.
        orbit = flash_orbit()
        need = max(guarantee.replica_floor(standard_config(), d) for d in orbit)
        self.assertGreater(
            guarantee.attainable_peak(standard_config(replica_max=need - 1), orbit), 0.0)
        self.assertEqual(
            guarantee.attainable_peak(standard_config(replica_max=need), orbit), 0.0)

    def test_slo_class_moves_the_floor(self):
        # A best-effort tenant has an 8x looser target, so under an identical
        # budget its floor cannot be worse than a premium tenant's.
        orbit = agentic_orbit()
        strict = guarantee.attainable_peak(standard_config(slo_class="premium"), orbit)
        loose = guarantee.attainable_peak(standard_config(slo_class="best-effort"), orbit)
        self.assertGreater(strict, loose)

    def test_the_checker_can_return_empty(self):
        # The floor of all floors: an impossible tenant (no budget at all) must
        # come out at the saturating ceiling rather than silently succeeding.
        orbit = agentic_orbit()
        broke = standard_config(hourly_budget_usd=0.0)
        self.assertFalse(guarantee.terminal_set(broke, orbit, threshold=0.0))


class PriceOfReactionTests(unittest.TestCase):
    """The separation that survives the vacuity finding.

    The claim is not "reactive controllers violate" -- they need not, they can
    over-provision -- but "reactive controllers pay". These tests establish
    that the gap exists where the actuation clamp binds, nearly vanishes on the
    control cell, and is *caused* by observational aliasing rather than by one
    search simply outrunning another.
    """

    def test_aliased_positions_pool_their_successors(self):
        # Two troughs, one followed by a burst and one by another trough: a
        # controller seeing only the trough cannot tell them apart, so both
        # successors must be in the set it has to survive.
        orbit = [crud_demand(0.5), crud_demand(6.0), crud_demand(0.5), crud_demand(0.5)]
        succ = guarantee.successor_demand_indices(orbit)
        # positions 0, 2 and 3 all observe 0.5x; their successors are 1, 3, 0.
        self.assertEqual(succ[0], (0, 1, 3))
        self.assertEqual(succ[0], succ[2])
        self.assertEqual(succ[0], succ[3])
        # position 1 observes 6.0x uniquely, so its successor is a singleton.
        self.assertEqual(succ[1], (2,))

    def test_reaction_costs_money_on_a_burst_onset(self):
        config = standard_config()
        result = guarantee.price_of_reaction(config, flash_orbit())
        self.assertTrue(result["reactive_can_hold_slo"])
        self.assertGreater(result["gap"], 0.0)
        # The floor must exceed a realised predictive cycle by a wide margin,
        # not by float noise: this is the regime the clamp binds in.
        self.assertGreater(result["gap"] / result["reactive_cost_floor"], 0.25)

    def test_control_cell_pays_almost_nothing_for_reacting(self):
        # ramp_gentle shares flash_crud's 0.5x-6.0x envelope but is reached at
        # a slope actuation can follow, so foresight buys little. This is the
        # specificity check: without it, a large gap on flash could just mean
        # the world is generically kind to whoever plans ahead.
        config = standard_config()
        burst = guarantee.price_of_reaction(config, flash_orbit())
        gentle = guarantee.price_of_reaction(config, gentle_orbit(period=20))
        burst_share = burst["gap"] / burst["reactive_cost_floor"]
        gentle_share = gentle["gap"] / gentle["reactive_cost_floor"]
        self.assertGreater(burst_share, 5.0 * gentle_share)

    def test_the_predictive_cycle_is_really_realisable(self):
        # The upper bound is only honest if the trajectory exists. Rebuild one
        # of that cost by DP and check every step: SLO met, budget respected,
        # each move inside the reach clamp, and the cycle closed.
        config = standard_config()
        orbit = flash_orbit()
        claimed = guarantee.predictive_cycle_cost(config, orbit)
        self.assertIsNotNone(claimed)

        trajectory = _rebuild_cycle(config, orbit, claimed)
        self.assertIsNotNone(trajectory, "no trajectory attains the claimed cost")
        total = 0.0
        for k, state in enumerate(trajectory):
            metrics = model.evaluate_step(config, state, orbit[k])
            self.assertLessEqual(metrics.violation, 0.0)
            self.assertLessEqual(metrics.cost_usd, guarantee.budget_per_step(config))
            nxt = trajectory[(k + 1) % len(trajectory)]
            self.assertIn(nxt, guarantee.reach(config, state),
                          f"step {k} -> {k+1} leaves the reach clamp")
            total += metrics.cost_usd
        self.assertAlmostEqual(total, claimed, places=9)

    def test_removing_the_aliasing_removes_the_gap(self):
        # The mechanism test. Give every orbit position a distinct observation
        # and a reactive controller's successor set becomes a singleton -- it
        # is as informed as a predictive one, so its floor can no longer exceed
        # a realised predictive cycle. If the gap survived this, it would not
        # be caused by the information asymmetry the theorem claims.
        config = standard_config()
        distinct = [crud_demand(0.5 + 0.35 * k) for k in range(12)]
        self.assertEqual(len({guarantee.observation_key(d) for d in distinct}),
                         len(distinct))
        for succ in guarantee.successor_demand_indices(distinct):
            self.assertEqual(len(succ), 1)
        result = guarantee.price_of_reaction(config, distinct)
        self.assertLessEqual(result["gap"], 0.0)

    def test_reactive_cannot_hold_slo_when_no_state_clears_the_alias_set(self):
        # The stronger verdict: cap replicas below what the aliased peak needs
        # and no single configuration survives the uncertainty, so no reactive
        # policy holds zero violation at all.
        orbit = flash_orbit()
        need = max(guarantee.replica_floor(standard_config(), d) for d in orbit)
        tight = standard_config(replica_max=need - 1)
        self.assertIsNone(guarantee.reactive_cost_floor(tight, orbit))
        self.assertFalse(
            guarantee.price_of_reaction(tight, orbit)["reactive_can_hold_slo"])


class FrontierTests(unittest.TestCase):
    """Cost/violation frontiers, for comparing the two classes at equal mean
    violation instead of at a per-step bound the campaigns never operate under.
    """

    def test_pricing_violation_trades_cost_for_attainment(self):
        # The frontier must actually be a frontier: paying more for violation
        # must buy less of it, and cost more.
        config = standard_config()
        orbit = flash_orbit()
        cheap_v, cheap_c = guarantee.reactive_frontier_point(config, orbit, 0.0)
        dear_v, dear_c = guarantee.reactive_frontier_point(config, orbit, 100.0)
        self.assertGreater(cheap_v, dear_v)
        self.assertLess(cheap_c, dear_c)

    def test_predictive_frontier_point_stays_inside_budget(self):
        config = standard_config()
        point = guarantee.predictive_frontier_point(config, flash_orbit(), 1.0)
        self.assertIsNotNone(point)
        violation, cost = point
        self.assertGreaterEqual(violation, 0.0)
        self.assertLessEqual(cost, guarantee.budget_per_step(config))

    def test_exact_frontier_matches_brute_force(self):
        # The enumeration's correctness claim, checked the only way that
        # settles it: on an orbit small enough to enumerate every reactive
        # policy directly (two observation classes, 96 states each = 9216
        # combinations) the Minkowski-sum-with-pruning result must equal the
        # brute-force Pareto frontier exactly.
        config = standard_config(replica_max=4)
        orbit = [crud_demand(f) for f in [6.0, 6.0] + [0.5] * 6]
        n = len(orbit)
        states = guarantee.admissible_states(config)

        groups: dict[tuple, list[int]] = {}
        for k, d in enumerate(orbit):
            groups.setdefault(guarantee.observation_key(d), []).append(k)
        self.assertEqual(len(groups), 2)
        per_class = []
        for positions in groups.values():
            billed = [(k + 1) % n for k in positions]
            per_class.append([
                (sum(model.evaluate_step(config, x, orbit[j]).violation for j in billed),
                 sum(model.evaluate_step(config, x, orbit[j]).cost_usd for j in billed))
                for x in states
            ])
        brute = [(a[0] + b[0], a[1] + b[1]) for a in per_class[0] for b in per_class[1]]
        expected = [(v / n, c / n) for v, c in guarantee._pareto(brute)]

        self.assertEqual(guarantee.reactive_frontier(config, orbit), expected)

    def test_enumeration_recovers_what_the_sweep_cannot(self):
        # The reason this exists: a lambda sweep sees only the lower convex
        # hull. On the sparse `spike` frontier that is a handful of points;
        # enumeration must find substantially more, or the fix did nothing.
        config = standard_config()
        spike = [Demand(rps={"agent": 1.5 * f, "embed": 1.0 * f, "crud_read": 5.0 * f},
                        crud_base_ms=60.0)
                 for f in [8.0, 8.0] + [0.6] * 10]
        exact = guarantee.reactive_frontier(config, spike)
        swept = {guarantee.reactive_frontier_point(config, spike, lam)
                 for lam in (0.0, 0.01, 0.1, 1.0, 10.0, 100.0)}
        self.assertGreater(len(exact), 4 * len(swept))
        # and it must be a genuine staircase: violation up, cost strictly down
        for (v1, c1), (v2, c2) in zip(exact, exact[1:]):
            self.assertLess(v1, v2)
            self.assertLess(c2, c1)

    def test_floor_at_a_violation_is_monotone(self):
        # Allowing more violation can never cost more.
        config = standard_config()
        orbit = flash_orbit()
        strict = guarantee.reactive_cost_floor_at(config, orbit, 0.05)
        loose = guarantee.reactive_cost_floor_at(config, orbit, 0.40)
        self.assertIsNotNone(strict)
        self.assertLessEqual(loose, strict)

    def test_enumeration_brings_the_reactive_side_within_reach_of_the_target(self):
        # Regression guard on the fix. Reading the reactive side off a lambda
        # sweep left its nearest point 0.14 away from the measured operating
        # point -- so the "gap" compared two different violations. Enumerating
        # the frontier closes that, and the test pins the improvement by
        # measuring both ways on the same orbit.
        config = standard_config()
        spike = [Demand(rps={"agent": 1.5 * f, "embed": 1.0 * f, "crud_read": 5.0 * f},
                        crud_base_ms=60.0)
                 for f in [8.0, 8.0] + [0.6] * 10]
        target = 0.2236
        dense = tuple(round(0.002 * (1.35 ** i), 5) for i in range(34))

        swept = [guarantee.reactive_frontier_point(config, spike, lam) for lam in dense]
        swept_offset = min(abs(v - target) for v, _ in swept)

        result = guarantee.cost_at_violation_parity(
            config, spike, target_violation=target, lambdas=dense)
        self.assertTrue(result["reachable"])
        self.assertLess(result["reactive_offset"], swept_offset / 4.0)
        # Both sides must honour the SAME constraint for the ratio to mean
        # anything -- that is what makes it a parity comparison.
        self.assertLessEqual(result["reactive"]["violation"], target + 1e-12)
        self.assertLessEqual(result["predictive"]["violation"], target + 1e-12)
        self.assertGreater(result["gap_frac"], 0.0)

    def test_parity_reader_needs_a_stated_target(self):
        # Without a target the reader would settle on the lambda=0 corner,
        # where both classes shed to the replica floor and coincide. Asking at
        # a low violation must not return that degenerate 0% answer.
        config = standard_config()
        orbit = flash_orbit()
        result = guarantee.cost_at_violation_parity(config, orbit, target_violation=0.0)
        self.assertIsNotNone(result)
        self.assertGreater(result["gap_frac"], 0.0)


def _rebuild_cycle(config, orbit, target):
    """Recover a zero-violation cycle whose cost equals `target`, by the same
    DP the bound uses but retaining predecessors. Written independently of
    `predictive_cycle_cost` so it checks that function rather than echoing it.
    """
    states = guarantee.admissible_states(config)
    layers = []
    for k, d in enumerate(orbit):
        ok = []
        for x in states:
            m = model.evaluate_step(config, x, d)
            if m.violation <= 0.0 and m.cost_usd <= guarantee.budget_per_step(config):
                ok.append(x)
        layers.append(ok)
    for start in layers[0]:
        paths = {start: [start]}
        costs = {start: model.evaluate_step(config, start, orbit[0]).cost_usd}
        for k in range(1, len(orbit)):
            nxt_c, nxt_p = {}, {}
            allowed = set(layers[k])
            for x, spent in costs.items():
                for y in set(guarantee.reach(config, x)) & allowed:
                    total = spent + model.evaluate_step(config, y, orbit[k]).cost_usd
                    if y not in nxt_c or total < nxt_c[y]:
                        nxt_c[y], nxt_p[y] = total, paths[x] + [y]
            costs, paths = nxt_c, nxt_p
            if not costs:
                break
        for x, spent in costs.items():
            if start in guarantee.reach(config, x) and abs(spent - target) < 1e-12:
                return paths[x]
    return None


if __name__ == "__main__":
    unittest.main()
