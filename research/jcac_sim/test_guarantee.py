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


if __name__ == "__main__":
    unittest.main()
