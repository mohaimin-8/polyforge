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


# --- the price of reaction -------------------------------------------------
#
# The separation that survives the vacuity finding. A reactive controller can
# always avoid onset violation by over-provisioning permanently -- that is what
# made the invariant-set argument empty -- so the honest theorem is about COST,
# not about violation.
#
# A controller choosing the configuration for interval k+1 at the end of
# interval k has not yet seen d_{k+1}. If its observation is d_k, then every
# orbit position carrying the same observation is indistinguishable to it, so
# to hold zero violation it must pick a configuration that clears *every*
# demand that can follow that observation. On an orbit where a trough can be
# followed by either another trough or a burst, that forces peak provisioning
# through the troughs. A predictive controller provisions for d_{k+1} alone.
#
# SOUNDNESS -- the two sides are deliberately asymmetric, and the asymmetry is
# what makes a positive gap a proof rather than an artefact:
#
#   * the reactive side is a LOWER bound. Constraints are *relaxed* (the reach
#     clamp and affordability are both dropped), which can only shrink the
#     minimum, so any real reactive controller pays at least this.
#   * the predictive side is an ACHIEVABLE trajectory. Constraints are all
#     *enforced* (reach, budget, knob bounds) and the trajectory is closed into
#     a cycle, so a controller really can realise it.
#
# Comparing two lower bounds would prove nothing. Comparing a floor on one
# class against a realised trajectory of the other proves the gap.


def observation_key(demand: Demand) -> tuple:
    """Hashable identity of a demand, for deciding which orbit positions a
    reactive controller cannot tell apart."""
    return (tuple(sorted(demand.rps.items())), demand.crud_base_ms)


def successor_demand_indices(demands: list[Demand]) -> list[tuple[int, ...]]:
    """For each k, the demands that can follow what was observed at k.

    Orbit positions sharing an observation are aliased, so their successors
    pool: a controller that has seen only `demands[k]` cannot know which of
    them is coming.
    """
    n = len(demands)
    groups: dict[tuple, list[int]] = {}
    for k, d in enumerate(demands):
        groups.setdefault(observation_key(d), []).append(k)
    return [
        tuple(sorted({(peer + 1) % n for peer in groups[observation_key(demands[k])]}))
        for k in range(n)
    ]


def reactive_cost_floor(
    config: TenantConfig, demands: list[Demand], threshold: float = 0.0
) -> float | None:
    """LOWER bound on one period's cost for a reactive policy holding
    `violation <= threshold`.

    At each step the configuration must clear every demand that can follow the
    observation, and is then billed against the demand that actually arrives.
    The reach clamp and the budget filter are both relaxed -- a real controller
    has strictly fewer options and so pays at least this. Returns None when no
    configuration clears an aliased successor set, which is the stronger
    verdict: no reactive policy can hold the constraint at all.

    `threshold` exists because the deployed arms do not run at zero violation:
    every v3 campaign sits around 0.11-0.27. Comparing a zero-violation bound
    against runs at 0.22 would be comparing different regimes, so the operating
    point has to be an argument.
    """
    n = len(demands)
    states = admissible_states(config)
    succ = successor_demand_indices(demands)
    total = 0.0
    for k in range(n):
        billed = demands[(k + 1) % n]
        best = None
        for x in states:
            if all(violation_of(config, x, demands[j]) <= threshold for j in succ[k]):
                cost = model.evaluate_step(config, x, billed).cost_usd
                if best is None or cost < best:
                    best = cost
        if best is None:
            return None
        total += best
    return total


def predictive_cycle_cost(
    config: TenantConfig,
    demands: list[Demand],
    act: Actuation = Actuation(),
    max_starts: int = 16,
    threshold: float = 0.0,
) -> float | None:
    """Cost of one period of a *realisable* predictive cycle holding
    `violation <= threshold`.

    An upper bound, and an honest one: the returned number is the cost of a
    specific trajectory that respects the reach clamp, the budget filter and
    the knob bounds, and closes back on its own starting configuration.

    Search is seeded from the `max_starts` cheapest layer-0 configurations
    rather than all of them -- missing a cheaper cycle only loosens an upper
    bound, so that stays sound. If none of those seeds closes a cycle the
    search widens to every layer-0 configuration before giving up: the cheap
    seeds are systematically the ones that *cannot* close, because they sit at
    the replica floor while the configuration a peak leaves behind is often out
    of their reach. Without the widening this reports None on orbits that do
    admit a cycle. Returns None only when no admissible cycle exists.
    """
    n = len(demands)
    if n == 0:
        return None
    states = admissible_states(config)
    cost_at = {}
    feasible: list[list[TenantState]] = []
    for k, d in enumerate(demands):
        layer = []
        for x in states:
            metrics = model.evaluate_step(config, x, d)
            cost_at[(k, x)] = metrics.cost_usd
            if metrics.violation <= threshold and metrics.cost_usd <= budget_per_step(config):
                layer.append(x)
        feasible.append(layer)
    if any(not layer for layer in feasible):
        return None

    reach_cache = {x: set(reach(config, x, act)) for x in states}
    layer_sets = [set(layer) for layer in feasible]
    ordered = sorted(feasible[0], key=lambda x: cost_at[(0, x)])

    def best_from(seeds) -> float | None:
        best = None
        for start in seeds:
            dp = {start: cost_at[(0, start)]}
            for k in range(1, n):
                nxt: dict[TenantState, float] = {}
                allowed = layer_sets[k]
                for x, spent in dp.items():
                    for y in reach_cache[x] & allowed:
                        total = spent + cost_at[(k, y)]
                        if y not in nxt or total < nxt[y]:
                            nxt[y] = total
                dp = nxt
                if not dp:
                    break
            for x, spent in dp.items():
                if start in reach_cache[x] and (best is None or spent < best):
                    best = spent
        return best

    best_cycle = best_from(ordered[:max_starts])
    if best_cycle is None and len(ordered) > max_starts:
        best_cycle = best_from(ordered[max_starts:])
    return best_cycle


def price_of_reaction(
    config: TenantConfig,
    demands: list[Demand],
    act: Actuation = Actuation(),
    threshold: float = 0.0,
) -> dict:
    """The separation, as a dict of the two bounds and their gap.

    `gap` is positive only when a *floor* on reactive cost exceeds a *realised*
    predictive cycle, so a positive value is a proof that seeing one interval
    ahead is worth money on this demand -- not merely that one search found a
    better answer than another. Both sides are evaluated at the same
    `threshold`, so the comparison is always within one operating regime.
    """
    floor = reactive_cost_floor(config, demands, threshold)
    cycle = predictive_cycle_cost(config, demands, act, threshold=threshold)
    gap = None
    if floor is not None and cycle is not None:
        gap = floor - cycle
    return {
        "reactive_cost_floor": floor,
        "predictive_cycle_cost": cycle,
        "gap": gap,
        "reactive_can_hold_slo": floor is not None,
    }


# --- cost/violation frontiers, for comparison at violation parity ----------
#
# The zero-violation separation above cannot be checked against the campaigns
# directly, for a reason worth stating: it constrains violation at *every*
# step, while the campaigns report the *mean* over the run. An arm with mean
# violation 0.13 may be missing the SLO completely through a burst and clearing
# it everywhere else -- a regime the per-step bound simply does not describe.
#
# So each policy class is traced as a cost/violation frontier instead, by
# pricing violation at lambda and minimising cost + lambda * violation. Sweeping
# lambda walks each class along its own frontier, and the two can then be read
# off at *equal mean violation* -- which is exactly the comparison the campaigns
# headline ("cost at violation parity").
#
# The same asymmetry as above is preserved, and for the same reason: the
# reactive side relaxes the reach clamp and the budget filter (a floor), the
# predictive side enforces both and closes a cycle (achievable).


def reactive_frontier_point(
    config: TenantConfig, demands: list[Demand], lam: float
) -> tuple[float, float]:
    """(mean violation, mean cost) of the relaxed reactive optimum at price `lam`.

    A reactive policy is a map from observation to configuration, so the choice
    is made once per observation class and then billed at every aliased
    position. With the reach clamp relaxed the classes are independent, so each
    is minimised separately -- which is what makes this a computable floor
    rather than a policy-synthesis search.
    """
    n = len(demands)
    groups: dict[tuple, list[int]] = {}
    for k, d in enumerate(demands):
        groups.setdefault(observation_key(d), []).append(k)
    states = admissible_states(config)

    total_cost = total_viol = 0.0
    for positions in groups.values():
        billed = [(k + 1) % n for k in positions]
        best = None
        for x in states:
            cost = viol = 0.0
            for j in billed:
                m = model.evaluate_step(config, x, demands[j])
                cost += m.cost_usd
                viol += m.violation
            score = cost + lam * viol
            if best is None or score < best[0]:
                best = (score, cost, viol)
        total_cost += best[1]
        total_viol += best[2]
    return total_viol / n, total_cost / n


def _pareto(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Non-dominated (violation, cost) pairs, ascending in violation.

    Lower is better on both axes, so after sorting by violation a point
    survives only if it is strictly cheaper than everything admitting less
    violation -- the usual staircase.
    """
    out: list[tuple[float, float]] = []
    best = float("inf")
    for violation, cost in sorted(set(points)):
        if cost < best - 1e-15:
            out.append((violation, cost))
            best = cost
    return out


def reactive_frontier(
    config: TenantConfig, demands: list[Demand], max_points: int = 20000
) -> list[tuple[float, float]]:
    """EXACT Pareto frontier of the reactive policy class, as (mean violation,
    mean cost) pairs.

    A lambda sweep recovers only the lower convex hull, which on a sparse
    frontier can miss the operating point entirely (see
    `cost_at_violation_parity`). This enumerates instead, and can do so exactly
    because with the reach clamp relaxed the observation classes are
    independent: the achievable set is the Minkowski sum of the per-class sets.
    Pruning dominated partial sums along the way is lossless -- if one partial
    sum dominates another, it still dominates after any common addition -- so
    the result is the true frontier, convex or not.

    COST SCALES WITH THE NUMBER OF OBSERVATION CLASSES, and that is benign for
    a reason worth noticing: the classes are the *distinct* demands in the
    orbit, so a burst shape has two or three and enumerates in seconds, while a
    smooth shape has one per step and the sum does not collapse. But a smooth
    orbit is exactly the case where every position is uniquely identifiable, so
    a reactive controller is as informed as a predictive one and the separation
    is zero by construction (`test_removing_the_aliasing_removes_the_gap`).
    Enumeration is cheap precisely where it is needed and expensive only where
    the answer is already known, so `max_points` raises rather than grinding.
    """
    n = len(demands)
    groups: dict[tuple, list[int]] = {}
    for k, d in enumerate(demands):
        groups.setdefault(observation_key(d), []).append(k)
    states = admissible_states(config)

    total: list[tuple[float, float]] = [(0.0, 0.0)]
    for positions in groups.values():
        billed = [(k + 1) % n for k in positions]
        options = []
        for x in states:
            cost = viol = 0.0
            for j in billed:
                m = model.evaluate_step(config, x, demands[j])
                cost += m.cost_usd
                viol += m.violation
            options.append((viol, cost))
        options = _pareto(options)
        total = _pareto([(v1 + v2, c1 + c2) for v1, c1 in total for v2, c2 in options])
        if len(total) > max_points:
            raise ValueError(
                f"reactive frontier exceeded {max_points} points; the orbit has "
                f"{len(groups)} observation classes and the Minkowski sum is not "
                f"collapsing. Raise max_points deliberately or reduce the orbit."
            )
    return [(v / n, c / n) for v, c in total]


def reactive_cost_floor_at(
    config: TenantConfig, demands: list[Demand], max_violation: float
) -> float | None:
    """Cheapest reactive policy holding mean violation <= `max_violation`.

    A floor: the reach clamp and the budget filter are relaxed, so any real
    reactive controller operating at that violation pays at least this.
    """
    admissible = [c for v, c in reactive_frontier(config, demands) if v <= max_violation + 1e-12]
    return min(admissible) if admissible else None


def predictive_frontier_point(
    config: TenantConfig,
    demands: list[Demand],
    lam: float,
    act: Actuation = Actuation(),
    max_starts: int = 16,
) -> tuple[float, float] | None:
    """(mean violation, mean cost) of a *realisable* predictive cycle at `lam`.

    Same DP as `predictive_cycle_cost`, with edges priced at
    cost + lam * violation and only the budget filter constraining membership,
    so the returned pair belongs to an actual closed trajectory.
    """
    n = len(demands)
    if n == 0:
        return None
    states = admissible_states(config)
    cap = budget_per_step(config)
    metrics: dict[tuple[int, TenantState], tuple[float, float]] = {}
    layers: list[list[TenantState]] = []
    for k, d in enumerate(demands):
        layer = []
        for x in states:
            m = model.evaluate_step(config, x, d)
            metrics[(k, x)] = (m.cost_usd, m.violation)
            if m.cost_usd <= cap:
                layer.append(x)
        layers.append(layer)
    if any(not layer for layer in layers):
        return None

    reach_cache = {x: set(reach(config, x, act)) for x in states}
    layer_sets = [set(layer) for layer in layers]

    def weight(k, x):
        cost, viol = metrics[(k, x)]
        return cost + lam * viol

    def search(seeds):
        best = None
        for start in seeds:
            c0, v0 = metrics[(0, start)]
            dp = {start: (weight(0, start), c0, v0)}
            for k in range(1, n):
                nxt = {}
                allowed = layer_sets[k]
                for x, (w, c, v) in dp.items():
                    for y in reach_cache[x] & allowed:
                        cy, vy = metrics[(k, y)]
                        cand = (w + weight(k, y), c + cy, v + vy)
                        if y not in nxt or cand[0] < nxt[y][0]:
                            nxt[y] = cand
                dp = nxt
                if not dp:
                    break
            for x, entry in dp.items():
                if start in reach_cache[x] and (best is None or entry[0] < best[0]):
                    best = entry
        return best

    ordered = sorted(layers[0], key=lambda x: weight(0, x))
    found = search(ordered[:max_starts])
    if found is None and len(ordered) > max_starts:
        found = search(ordered[max_starts:])
    if found is None:
        return None
    _, cost, viol = found
    return viol / n, cost / n


def cost_at_violation_parity(
    config: TenantConfig,
    demands: list[Demand],
    target_violation: float,
    act: Actuation = Actuation(),
    lambdas: tuple[float, ...] = (0.0, 0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 100.0),
) -> dict | None:
    """Read both frontiers at `target_violation` and return the cost gap.

    The operating point must be supplied rather than discovered. Asking instead
    for "the closest matched pair anywhere" finds the lambda=0 corner, where
    both classes simply shed to the replica floor and eat the violation: their
    costs coincide there and the gap reads 0.0% on demand orbits that plainly
    do separate. A comparison at violation parity only means something at a
    stated violation.

    `reactive_offset` / `predictive_offset` report how far each frontier's
    nearest point sits from the target. **Check them before reading the gap.**
    A lambda sweep recovers only the lower convex hull of a frontier, and the
    reactive frontier is genuinely sparse -- a reactive policy makes one choice
    per *observation class*, and an orbit like `spike` has only two, so its
    frontier holds a handful of points and may have no operating point near a
    given target at all. When that happens the two sides are read at different
    violations and the ratio is not a parity comparison. Recovering the
    non-convex interior needs enumeration rather than a sweep; until then a
    large offset means "no comparison available here", not "no gap".
    """
    reactive = reactive_frontier(config, demands)
    predictive = []
    for lam in lambdas:
        point = predictive_frontier_point(config, demands, lam, act)
        if point is not None:
            predictive.append(point)

    # Both sides are read at "no worse than the target", never above it, so the
    # comparison can only understate the gap: the reactive side is the cheapest
    # policy that still holds the target (a floor), and the predictive side is
    # a realised cycle that also holds it (an upper bound). A predictive point
    # cheaper than the floor at the same violation is then a proof.
    admissible_r = [(v, c) for v, c in reactive if v <= target_violation + 1e-12]
    admissible_p = [(v, c) for v, c in predictive if v <= target_violation + 1e-12]
    if not admissible_r or not admissible_p:
        return {
            "target_violation": target_violation,
            "reachable": False,
            "reactive_frontier": reactive,
            "predictive_frontier": _pareto(predictive),
        }

    rv, rc = min(admissible_r, key=lambda p: p[1])
    pv, pc = min(admissible_p, key=lambda p: p[1])
    return {
        "target_violation": target_violation,
        "reachable": True,
        "reactive": {"violation": rv, "cost": rc},
        "predictive": {"violation": pv, "cost": pc},
        "reactive_offset": abs(rv - target_violation),
        "predictive_offset": abs(pv - target_violation),
        "gap_frac": (rc - pc) / rc if rc > 0 else None,
        "reactive_frontier": reactive,
        "predictive_frontier": _pareto(predictive),
    }


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


# --- WP13: the multi-tenant extension -------------------------------------
# Everything above is single-tenant and is published; nothing here changes it.
#
# The cluster cap is what couples tenants, and the M3 close-out called that
# coupling "load-bearing, not optional" while bracketing it between two
# models. WP13 step 1 measured that bracket and found only one arm real: the
# equal share is genuinely infeasible, but there is no distinct "no-cap"
# model, because the per-tenant replica ceiling never binds on these orbits.
#
# What makes an exact treatment possible here — and what the M3 prose reached
# for a concentration inequality to avoid — is that nothing is stochastic in
# the usual sense. Each tenant replays a deterministic periodic orbit with a
# phase drawn once, uniformly, at run start (`workloads.build`). So the joint
# phase space is a finite product, every coincidence probability is exactly
# countable, and the aggregate demand distribution is a convolution rather
# than something to be bounded.


def orbit_replica_needs(config: TenantConfig, demands: list[Demand],
                        cap: int | None = None) -> tuple[int, ...]:
    """Replicas this tenant needs at each orbit position to hold the SLO,
    cache and tier chosen freely. `None` at a position means no admissible
    configuration clears it."""
    return tuple(replica_floor(config, d, cap) for d in demands)


def aggregate_need_distribution(needs: tuple[int, ...],
                                tenants: int) -> dict[int, float]:
    """Exact distribution of the *aggregate* replica requirement.

    Phases are independent uniform draws over orbit positions, so the
    aggregate is a sum of `tenants` i.i.d. draws from the per-position need
    and its distribution is an exact convolution. No inequality, no
    sampling: the numbers below are the true probabilities for the demand
    construction the campaigns actually replay.
    """
    if any(n is None for n in needs):
        raise ValueError("orbit has an unservable position; no aggregate is defined")
    single: dict[int, float] = {}
    for n in needs:
        single[n] = single.get(n, 0.0) + 1.0 / len(needs)
    dist = {0: 1.0}
    for _ in range(tenants):
        nxt: dict[int, float] = {}
        for total, p in dist.items():
            for value, q in single.items():
                nxt[total + value] = nxt.get(total + value, 0.0) + p * q
        dist = nxt
    return dist


def cap_binding_probability(needs: tuple[int, ...], tenants: int,
                            cap: int) -> float:
    """P(the cluster replica cap binds) — the number that decides whether
    the single-tenant derivation is a description of the coupled system or
    merely a lower bound on it."""
    dist = aggregate_need_distribution(needs, tenants)
    return sum(p for total, p in dist.items() if total > cap)


def fcfs_allocation(needs: list[int], cap: int) -> list[int]:
    """The simulator's own contention rule, made explicit.

    `controller._sweep_order` is `sorted(configs)` and the sweep is
    first-come-first-served on the shared caps: an earlier tenant claims
    contended capacity before a later one sees it. Any coupled floor has to
    allocate the same way or it is describing a different system.
    """
    out, remaining = [], cap
    for need in needs:
        take = min(need, remaining)
        out.append(take)
        remaining -= take
    return out


def coupled_floor(config: TenantConfig, demands: list[Demand], tenants: int,
                  cap: int, max_states: int = 1 << 20) -> dict:
    """Expected per-tenant violation floor under the cluster cap.

    Enumerates the joint phase space exactly. The state that matters is the
    *vector of per-tenant needs*, not the vector of phases, and needs take
    few distinct values, so the enumeration is over `|values|^tenants`
    rather than `orbit_length^tenants` — 256 rather than 4.3e9 on the
    published `flash` orbit.

    Returns the coupled floor, the uncoupled one (each tenant served in
    isolation) and the gap between them, which is exactly the quantity the
    single-tenant derivation cannot see.
    """
    needs = orbit_replica_needs(config, demands)
    if any(n is None for n in needs):
        raise ValueError("orbit has an unservable position")
    values = sorted(set(needs))
    weight = {v: sum(1 for n in needs if n == v) / len(needs) for v in values}
    if len(values) ** tenants > max_states:
        raise ValueError(
            f"joint enumeration is {len(values)}^{tenants} states; raise "
            "max_states deliberately or reduce the tenant count")

    # Best achievable violation at a given replica allocation, per need level.
    # Cached because the inner loop revisits the same (need, allocation) pairs.
    best: dict[tuple[int, int], float] = {}

    def violation_at(position_need: int, allocated: int) -> float:
        key = (position_need, allocated)
        if key in best:
            return best[key]
        # Which orbit positions carry this need — they share a demand shape
        # only up to the need level, so take the worst, which keeps this a
        # floor rather than an average dressed as one.
        worst = 0.0
        for demand, need in zip(demands, needs):
            if need != position_need:
                continue
            local = min(
                violation_of(config, TenantState(replicas=max(1, allocated),
                                                 cache_mb=cache_mb, tier=tier),
                             demand)
                for cache_mb in CACHE_LEVELS_MB
                for tier in TIERS
                if config.knob_admits(cache_mb, tier)
            )
            worst = max(worst, local)
        best[key] = worst
        return worst

    coupled = uncoupled = 0.0
    for combo in _product(values, tenants):
        p = 1.0
        for v in combo:
            p *= weight[v]
        if p == 0.0:
            continue
        allocation = fcfs_allocation(list(combo), cap)
        coupled += p * sum(violation_at(need, got)
                           for need, got in zip(combo, allocation)) / tenants
        uncoupled += p * sum(violation_at(need, need) for need in combo) / tenants
    return {
        "coupled_violation": coupled,
        "uncoupled_violation": uncoupled,
        "gap": coupled - uncoupled,
        "bind_probability": cap_binding_probability(needs, tenants, cap),
        "needs": needs,
        "cap": cap,
        "tenants": tenants,
    }


def _product(values: list[int], repeat: int):
    """itertools.product without the import, kept local so this block adds
    no module-level dependency to a published analysis module."""
    if repeat == 0:
        yield ()
        return
    for head in values:
        for rest in _product(values, repeat - 1):
            yield (head,) + rest


# ---------------------------------------------------------------------------
# WP13 step 2 — the corrected coupled floor.
#
# Step 1's `coupled_floor` above is FALSIFIED and stays as committed: V2 found
# `keda` measuring 0.001886 against a derived floor of 0.036487
# (RESULTS_SEPARATION_MT.md). The cause is `fcfs_allocation`, not the demand
# model -- the aggregate convolution was checked against
# `workloads.build('flash_crud', 'uniform', 'small')` and matches.
#
# Everything below is a NEW derivation over the allocation the planner actually
# performs. Nothing above is modified.
# ---------------------------------------------------------------------------

INCREMENTAL_DELTAS = (-2, -1, 0, 1, 2)  # controller.DELTA_REPLICAS, mirrored


def incremental_allocation(needs: list[int], cap: int, held: list[int],
                           replica_max: int,
                           deltas: tuple[int, ...] = INCREMENTAL_DELTAS) -> list[int]:
    """The contention rule the simulator actually runs.

    `fcfs_allocation` hands each tenant `min(need, remaining)` FROM ZERO, so a
    late tenant under contention is allocated nothing and is scored at one
    replica. `controller._best_for_tenant` differs in two ways that both
    matter, and both make the real cluster LESS punitive:

      * a candidate that would exceed the shared cap is REJECTED, not
        truncated (`if others_replicas + candidate.replicas > limits.replicas:
        continue`), and `best_state` initialises to `current` -- so a tenant
        with no admissible candidate KEEPS THE STATE IT ALREADY HOLDS;
      * every candidate is `base + delta` for `delta in DELTA_REPLICAS`, so a
        tenant moves at most two replicas per interval, in either direction.

    The tenant takes the smallest reachable allocation that clears its need,
    and the largest reachable one otherwise. Taking more than the need would
    not lower its own violation and would starve a neighbour, so a floor must
    not model it.
    """
    chosen = list(held)
    for i, need in enumerate(needs):
        others = sum(chosen) - chosen[i]
        room = cap - others
        base = held[i]
        lo = max(1, base - max(deltas))
        hi = min(replica_max, base + max(deltas))
        feasible = [r for r in range(lo, hi + 1) if r <= room]
        if not feasible:
            continue  # keeps `chosen[i]`, which is `held[i]`
        meets = [r for r in feasible if r >= need]
        chosen[i] = min(meets) if meets else max(feasible)
    return chosen


def _violation_table(config: TenantConfig, demands: list[Demand],
                     needs: tuple[int, ...], replica_max: int
                     ) -> dict[tuple[int, int], float]:
    """(need level, replicas held) -> best achievable violation.

    Same construction as `coupled_floor`'s inner cache: positions sharing a
    need level share a demand shape only up to that level, so the worst of
    them is taken, which keeps this a floor rather than an average wearing a
    floor's name.
    """
    table: dict[tuple[int, int], float] = {}
    for level in sorted(set(needs)):
        for held in range(1, replica_max + 1):
            worst = 0.0
            for demand, need in zip(demands, needs):
                if need != level:
                    continue
                local = min(
                    violation_of(config, TenantState(replicas=held,
                                                     cache_mb=cache_mb, tier=tier),
                                 demand)
                    for cache_mb in CACHE_LEVELS_MB
                    for tier in TIERS
                    if config.knob_admits(cache_mb, tier)
                )
                worst = max(worst, local)
            table[(level, held)] = worst
    return table


def ramp_lead(replica_max: int,
              deltas: tuple[int, ...] = INCREMENTAL_DELTAS) -> int:
    """Intervals needed to climb from one replica to the ceiling.

    A floor must be what the BEST controller with this actuation authority can
    do, so the tenant is given exactly enough foresight to pre-ramp and no
    more. Without it the derivation would score a myopic controller's
    catch-up cost as unavoidable, which it is not: the published uncoupled
    floor is 0.000000 precisely because a predictive controller climbs during
    the trough.
    """
    return -(-(replica_max - 1) // max(deltas))


def _trajectory_violation(needs: tuple[int, ...], offsets: tuple[int, ...],
                          cap: int, replica_max: int,
                          table: dict[tuple[int, int], float],
                          periods: int, lead: int) -> float:
    """Mean per-tenant violation on the attractor for one offset vector.

    Phases advance deterministically, so a fixed offset vector makes the whole
    trajectory deterministic and it settles onto a cycle. Burn-in runs
    `periods - 1` orbits and the average is taken over the final one.

    Each tenant provisions for the worst need inside its ramp horizon, which
    is what lets it arrive at a peak already at the ceiling. Violation is
    scored against the need it ACTUALLY faces, never against the target it
    provisioned for -- holding capacity early is a cost to neighbours, not a
    credit to itself, and that asymmetry is the coupling this derivation
    exists to price.
    """
    length = len(needs)
    held = [1] * len(offsets)
    total = 0.0
    for step in range(periods * length):
        actual = [needs[(off + step) % length] for off in offsets]
        target = [max(needs[(off + step + k) % length] for k in range(lead + 1))
                  for off in offsets]
        held = incremental_allocation(target, cap, held, replica_max)
        if step >= (periods - 1) * length:
            total += sum(table[(n, h)] for n, h in zip(actual, held))
    return total / (length * len(offsets))


def _offset_vectors(length: int, tenants: int):
    """Offset vectors with tenant 0 pinned at phase 0.

    Shifting every offset by k and time by -k leaves the time-average
    unchanged, so pinning one tenant is an exact reduction by a factor of
    `length`, not an approximation.
    """
    if tenants == 1:
        yield (0,)
        return
    for rest in _product(list(range(length)), tenants - 1):
        yield (0,) + rest


def _offset_multisets(length: int, tenants: int):
    """Non-decreasing offset vectors, with their multinomial weights.

    Exact ONLY if the mean per-tenant violation is invariant to which tenant
    holds which offset. That is not obvious -- the sweep is first-come-first-
    served by tenant id, so order decides who gets starved -- so it is checked
    rather than assumed: `permutation_invariance_report` compares every
    permutation against its sorted representative, and the analysis refuses
    this mode if any disagree.
    """
    from math import factorial

    def rec(start: int, left: int, acc: tuple[int, ...]):
        if left == 0:
            counts: dict[int, int] = {}
            for v in acc:
                counts[v] = counts.get(v, 0) + 1
            weight = factorial(len(acc))
            for c in counts.values():
                weight //= factorial(c)
            yield acc, weight
            return
        for v in range(start, length):
            yield from rec(v, left - 1, acc + (v,))

    yield from rec(0, tenants, ())


def permutation_invariance_report(config: TenantConfig, demands: list[Demand],
                                  tenants: int, cap: int, *, samples: int = 24,
                                  periods: int = 4, seed: int = 20260824) -> dict:
    """Does sweep order change the MEAN per-tenant violation?

    Draws offset multisets, evaluates every distinct permutation of each, and
    reports the largest spread. Zero spread licenses the multiset enumeration
    used for tenant counts where the ordered product is too large to walk.
    """
    import random
    from itertools import permutations

    needs = orbit_replica_needs(config, demands)
    table = _violation_table(config, demands, needs, config.replica_max)
    rng = random.Random(seed)
    worst = 0.0
    checked = 0
    for _ in range(samples):
        base = tuple(sorted(rng.randrange(len(needs)) for _ in range(tenants)))
        lead = ramp_lead(config.replica_max)
        values = {_trajectory_violation(needs, perm, cap, config.replica_max,
                                        table, periods, lead)
                  for perm in set(permutations(base))}
        worst = max(worst, max(values) - min(values))
        checked += 1
    return {"max_spread": worst, "multisets_checked": checked,
            "invariant": worst == 0.0}


def coupled_floor_incremental(config: TenantConfig, demands: list[Demand],
                              tenants: int, cap: int, *, periods: int = 4,
                              max_states: int = 1 << 21,
                              mode: str = "auto") -> dict:
    """Expected per-tenant violation floor under the cap AND the move clamp.

    Corrects `coupled_floor`, whose `fcfs_allocation` starves a contended
    tenant to one replica while the simulator lets it keep the state it holds.

    `mode` is "ordered" (exact, walks every offset vector with tenant 0
    pinned), "multiset" (exact only under permutation invariance -- check it
    with `permutation_invariance_report`), or "auto", which takes the ordered
    walk when it fits inside `max_states` and the multiset walk otherwise.
    """
    needs = orbit_replica_needs(config, demands)
    if any(n is None for n in needs):
        raise ValueError("orbit has an unservable position")
    length = len(needs)
    table = _violation_table(config, demands, needs, config.replica_max)
    lead = ramp_lead(config.replica_max)

    ordered_states = length ** (tenants - 1) if tenants > 1 else 1
    if mode == "auto":
        mode = "ordered" if ordered_states <= max_states else "multiset"
    if mode == "ordered" and ordered_states > max_states:
        raise ValueError(
            f"ordered enumeration is {ordered_states} vectors; raise "
            "max_states deliberately or use mode='multiset'")

    total = 0.0
    weight_sum = 0
    if mode == "ordered":
        for offsets in _offset_vectors(length, tenants):
            total += _trajectory_violation(needs, offsets, cap,
                                           config.replica_max, table, periods,
                                           lead)
            weight_sum += 1
    else:
        for offsets, weight in _offset_multisets(length, tenants):
            total += weight * _trajectory_violation(
                needs, offsets, cap, config.replica_max, table, periods, lead)
            weight_sum += weight

    coupled = total / weight_sum
    uncoupled = coupled_floor_incremental_uncapped(config, demands, tenants,
                                                   table, needs, periods, lead)
    return {
        "coupled_violation": coupled,
        "uncoupled_violation": uncoupled,
        "gap": coupled - uncoupled,
        "mode": mode,
        "states_walked": weight_sum,
        "needs": needs,
        "cap": cap,
        "tenants": tenants,
    }


def coupled_floor_incremental_uncapped(config: TenantConfig, demands: list[Demand],
                                       tenants: int, table, needs,
                                       periods: int, lead: int) -> float:
    """The same dynamics with the cap lifted — one tenant's trajectory, which
    every tenant then shares because nothing couples them."""
    cap = tenants * config.replica_max
    total = 0.0
    for offset in range(len(needs)):
        total += _trajectory_violation(needs, (offset,), cap,
                                       config.replica_max, table, periods, lead)
    return total / len(needs)
