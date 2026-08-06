"""Formal SLO guarantee for the JCAC controller (main path M3 / map T17).

This module is **analysis only**. It imports the plant and reads its frozen
constants; it never mutates them and nothing in the simulation path imports it,
so every committed campaign replays bit-for-bit (R4).

FINDING (2026-08-06): THE CLASSICAL DEVICE COLLAPSES ON THIS PLANT

T17 asked for a terminal invariant set plus a recursive-feasibility argument.
Building the exact construction below showed that argument is **vacuous here**,
and that is recorded rather than papered over (the task's own RISK clause):

  * the plant is memoryless -- violation depends on the configuration held and
    the demand that arrives, never on a backlog carried forward -- so no state
    is a trap and none has to be avoided to stay recoverable;
  * the controller may always hold still, so *any* configuration that meets the
    SLO at every point of the orbit is trivially control-invariant;
  * replicas are cheap. At the `flash` peak (6.0x of flash_crud's published
    base) six replicas clear the SLO and ten cost $0.00134 per interval against
    a $0.01389 budget -- so static over-provisioning satisfies the constraint
    inside both the replica ceiling and the budget, and the +/-2 clamp never
    binds.

So "is there a terminal set?" collapses into "is the peak demand servable at
all?", a static capacity question that the actuation clamps play no part in. A
recursive-feasibility theorem over this lattice would be true and empty.

What does bind is **the per-tenant budget against AI tier spend**: on agentic
demand the cheapest affordable configuration still carries violation 0.1725
while the budget-free optimum is 0.0. That is the same mechanism session 29's
risk-MPC null diagnosed empirically ("the knob was fighting the Budget CRD, not
the demand"). The non-vacuous formal object for this system is therefore a
cost-constrained attainability floor -- the least violation any controller can
reach under a given budget -- which `attainable_peak` computes with
`enforce_budget` on. Read the module in that light: the invariant-set machinery
is kept because it computes that floor exactly, not because recursive
feasibility is the interesting property.

WHAT IS PROVED HERE

The plant (`model.evaluate_step`) is a *static* map: the SLO outcome of an
interval depends only on the configuration held during it and the demand that
arrives, with no backlog carried across intervals. The controller may not jump
anywhere it likes between intervals — `DELTA_REPLICAS` clamps replicas to +/-2
and `neighbor_cache_levels` clamps the cache to one level, both pinned by
`test_invariants.py`. So the question "can this controller hold the SLO?" is a
constrained reachability question on a finite graph, and it can be answered
*exactly* rather than bounded heuristically:

  * the per-tenant configuration space is finite -- replicas in
    [replica_min, replica_max] x 6 cache levels x 4 tiers (<= 240 states);
  * the demand each campaign replays is a deterministic periodic function of
    the step (`workloads._shape_factor` plus a per-tenant phase fixed once per
    run -- there is no per-step noise), so the disturbance set is a finite
    orbit, not a stochastic ball that has to be over-approximated.

On that product space (orbit position x configuration) the maximal
control-invariant set is the greatest fixed point of

    S_0     = { (k, x) : violation(x, d_k) <= theta }
    S_{i+1} = { (k, x) in S_i : exists u in reach(x) with (k+1, u) in S_i }

which `terminal_set` computes by iterating to convergence. The iteration is
monotone decreasing on a finite set, so it terminates, and its limit is the
largest set of states from which the constraint can be honoured forever --
i.e. recursive feasibility holds on S and nowhere larger. `attainable_peak`
then finds the smallest theta whose fixed point is non-empty by searching the
finite set of violation values the lattice can produce, giving the exact
**unavoidable peak violation**: no controller with this actuation authority can
hold the tenant below it, whatever its objective or tuning.

SOUNDNESS OF THE OMISSIONS

Three couplings are deliberately left out: the per-tenant budget filter, the
cluster-wide replica/cache caps, and the other tenants competing for them. All
three only ever *remove* candidates from the controller's lattice. Removing
candidates can raise the attainable floor but never lower it, so the number
this module reports is a valid **lower** bound on the unavoidable violation of
the real, more constrained system -- the direction that makes "measured
violation >= floor" an honest check. It is deliberately NOT an upper bound on
what the deployed controller does: that controller minimises J (cost + SLO +
fairness), so it may knowingly spend violation to save money. Read the floor as
a statement about the *plant and its actuation authority*, not about JCAC's
tuning.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import model
from model import CACHE_LEVELS_MB, TIERS, Demand, TenantConfig, TenantState

# The frozen per-interval actuation clamps. Stated as literals for the same
# reason `test_invariants.py` states them as literals: an oracle derived from
# the implementation cannot catch the implementation drifting. `test_guarantee`
# pins these against the controller's real lattice.
MAX_REPLICA_STEP = 2
MAX_CACHE_LEVEL_STEP = 1


@dataclass(frozen=True)
class Actuation:
    """How far the controller may move one tenant in one interval.

    Defaults are the published clamps. The parameters exist so the checker can
    be mutation-tested (widen the authority and a set that was empty must stop
    being empty) and so a future actuation redesign can be evaluated before it
    is built -- not so a caller can quietly relax the contract.
    """

    max_replica_step: int = MAX_REPLICA_STEP
    max_cache_level_step: int = MAX_CACHE_LEVEL_STEP
    tier_unclamped: bool = True


def admissible_states(config: TenantConfig) -> tuple[TenantState, ...]:
    """Every configuration this tenant's bounds allow. Finite by construction."""
    out = []
    for replicas in range(config.replica_min, config.replica_max + 1):
        for cache_mb in CACHE_LEVELS_MB:
            for tier in TIERS:
                if config.knob_admits(cache_mb, tier):
                    out.append(TenantState(replicas=replicas, cache_mb=cache_mb, tier=tier))
    return tuple(out)


def reach(config: TenantConfig, state: TenantState, act: Actuation = Actuation()
          ) -> tuple[TenantState, ...]:
    """States reachable from `state` in one control interval.

    Mirrors the controller's candidate enumeration: replicas move by at most
    `max_replica_step` and are then clamped into the tenant's band exactly as
    `model.apply_action` clamps them; the cache moves at most
    `max_cache_level_step` levels; the tier is unclamped (the published
    controller enumerates all four every interval). Knob bounds filter the
    result, so a pinned knob yields a reach set that cannot leave its pin.
    """
    if state.cache_mb in CACHE_LEVELS_MB:
        ci = CACHE_LEVELS_MB.index(state.cache_mb)
    else:
        ci = 0
    lo = max(0, ci - act.max_cache_level_step)
    hi = min(len(CACHE_LEVELS_MB) - 1, ci + act.max_cache_level_step)
    cache_options = CACHE_LEVELS_MB[lo:hi + 1]
    tier_options = TIERS if act.tier_unclamped else (state.tier,)

    out = []
    for delta in range(-act.max_replica_step, act.max_replica_step + 1):
        replicas = max(config.replica_min, min(config.replica_max, state.replicas + delta))
        for cache_mb in cache_options:
            for tier in tier_options:
                if not config.knob_admits(cache_mb, tier):
                    continue
                out.append(replace(state, replicas=replicas, cache_mb=cache_mb, tier=tier))
    return tuple(dict.fromkeys(out))  # de-duplicated, order-stable


def violation_of(config: TenantConfig, state: TenantState, demand: Demand) -> float:
    """The plant's own violation for holding `state` through `demand`."""
    return model.evaluate_step(config, state, demand).violation


def budget_per_step(config: TenantConfig) -> float:
    """The tenant's spend allowance for one control interval — the same
    arithmetic `controller._best_for_tenant` applies before it admits a
    candidate."""
    return config.hourly_budget_usd * model.CONTROL_INTERVAL_S / 3600.0


def affordable(config: TenantConfig, state: TenantState, demand: Demand) -> bool:
    """Whether holding `state` through `demand` stays inside the budget.

    This is the constraint that actually binds on this plant (see the module
    docstring): the controller drops any candidate costing more than one
    interval's allowance, and on AI traffic that exclusion — not the actuation
    clamp — is what puts violation out of reach.
    """
    return model.evaluate_step(config, state, demand).cost_usd <= budget_per_step(config)


def terminal_set(
    config: TenantConfig,
    demands: list[Demand],
    act: Actuation = Actuation(),
    threshold: float = 0.0,
    periodic: bool = True,
    enforce_budget: bool = True,
) -> frozenset[tuple[int, TenantState]]:
    """Maximal control-invariant set at `threshold`, as (step, state) pairs.

    `demands[k]` is the demand the tenant meets while holding the state indexed
    by k. With `periodic` the orbit wraps (k -> (k+1) % n), which is the exact
    situation the campaigns replay; without it the last index has no successor
    and is treated as terminal (a finite-horizon backward induction).

    `enforce_budget` keeps only candidates the controller could actually afford,
    mirroring its budget filter. It defaults on because that filter is the
    binding constraint; turning it off reproduces the degenerate regime in the
    module docstring, where static over-provisioning makes the set trivially
    non-empty.

    Greatest fixed point of a monotone decreasing operator on a finite set, so
    the loop terminates; the result is the largest set on which the constraint
    can be honoured forever.
    """
    n = len(demands)
    if n == 0:
        return frozenset()
    states = admissible_states(config)
    reach_cache = {x: reach(config, x, act) for x in states}

    survivors = {
        (k, x)
        for k in range(n)
        for x in states
        if violation_of(config, x, demands[k]) <= threshold
        and (not enforce_budget or affordable(config, x, demands[k]))
    }
    while True:
        pruned = set()
        for k, x in survivors:
            if not periodic and k == n - 1:
                pruned.add((k, x))  # no successor to constrain
                continue
            nxt = (k + 1) % n
            if any((nxt, u) in survivors for u in reach_cache[x]):
                pruned.add((k, x))
        if pruned == survivors:
            return frozenset(survivors)
        survivors = pruned


def attainable_peak(
    config: TenantConfig,
    demands: list[Demand],
    act: Actuation = Actuation(),
    periodic: bool = True,
    enforce_budget: bool = True,
) -> float:
    """The unavoidable peak violation: the smallest theta for which some
    trajectory can hold `violation <= theta` for ever.

    With `enforce_budget` on (the default) this is the **cost-constrained
    attainability floor** -- the least violation any controller can reach on
    this demand under this tenant's budget, whatever its objective or tuning.
    It is the non-vacuous formal object for this plant; see the module
    docstring for why the unconstrained version is not.

    Exact rather than numerically bisected: the lattice can only produce
    finitely many violation values, so the answer is one of them. Feasibility
    is monotone in theta -- raising it only adds states to S_0, and the fixed
    point operator is monotone -- so the finite candidate list is searched by
    binary search, which is exact here precisely because the answer is
    guaranteed to be a member of the list. Returns 0.0 when a zero-violation
    invariant set exists, and 1.0 when even the saturating ceiling cannot be
    held.
    """
    states = admissible_states(config)
    candidates = sorted({
        violation_of(config, x, d) for x in states for d in demands
    })
    if not candidates:
        return 1.0

    def feasible(theta: float) -> bool:
        return bool(terminal_set(config, demands, act, threshold=theta,
                                 periodic=periodic, enforce_budget=enforce_budget))

    if feasible(candidates[0]):
        return candidates[0]
    lo, hi = 0, len(candidates) - 1
    if not feasible(candidates[hi]):
        return 1.0
    while hi - lo > 1:  # invariant: candidates[lo] infeasible, candidates[hi] feasible
        mid = (lo + hi) // 2
        if feasible(candidates[mid]):
            hi = mid
        else:
            lo = mid
    return candidates[hi]


def replica_floor(config: TenantConfig, demand: Demand, cap: int | None = None) -> int | None:
    """Fewest replicas that meet the SLO for `demand`, cache and tier chosen
    freely. Reported for diagnosis -- the climb between consecutive floors is
    what the +/-2 clamp has to cover -- and never used as the guarantee itself,
    because it ignores that the cache cannot jump levels arbitrarily.
    """
    ceiling = config.replica_max if cap is None else cap
    best = None
    for cache_mb in CACHE_LEVELS_MB:
        for tier in TIERS:
            if not config.knob_admits(cache_mb, tier):
                continue
            for n in range(config.replica_min, ceiling + 1):
                state = TenantState(replicas=n, cache_mb=cache_mb, tier=tier)
                if violation_of(config, state, demand) <= 0.0:
                    if best is None or n < best:
                        best = n
                    break
    return best
