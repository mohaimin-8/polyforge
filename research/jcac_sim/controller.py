"""JCAC controller: receding-horizon MPC over a discrete action lattice (W30).

Every CONTROL_INTERVAL_S the controller solves

    min_{u}  Σ_{k=1..H} [ α·cost_norm(k) + β·violation(k) ] + γ·(1 − Jain)

subject to  replica_min ≤ r ≤ replica_max        (per tenant)
            cache moves ≤ 1 level per interval    (thrash prevention)
            Σ_i cache_i ≤ cluster cache budget    (shared memory)
            Σ_i replicas_i ≤ cluster replica cap  (shared CPU)
            projected step cost ≤ tenant budget   (Budget CRD)

then applies only the first action and re-plans — standard MPC.

Solver choice (deviation from the week plan, see ADR 0014): the true
problem is mixed-integer — replicas are integers, the model tier is
categorical — which CVXPY's bundled convex solvers cannot express without
a commercial MIP backend. The action space is small (≤ 60 candidates per
tenant), so we solve the move-blocked problem *exactly* by enumeration,
coordinating tenants through two rounds of coordinate descent on the
shared objective. Move blocking (hold the candidate configuration across
the horizon) is the standard MPC simplification that keeps enumeration
linear in candidates instead of exponential in horizon.

Hysteresis: changing configuration pays a small switching cost in the
objective, so the controller only moves when the forecast justifies it —
this is what prevents oscillation between adjacent plans.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import model
from model import (
    HORIZON_STEPS,
    TIERS,
    Demand,
    TenantConfig,
    TenantState,
    apply_action,
    evaluate_step,
    jain_index,
    neighbor_cache_levels,
)

# Normalizer that puts cost on a comparable scale with violation (0..1):
# the per-interval cost of a deliberately over-provisioned tenant.
COST_SCALE_USD = 0.01

SWITCH_PENALTY = 0.05  # objective units per changed knob (hysteresis)
DELTA_REPLICAS = (-2, -1, 0, 1, 2)


@dataclass
class Weights:
    alpha: float = 1.0  # $ cost
    beta: float = 2.0  # SLO violation
    gamma: float = 0.5  # fairness (1 - Jain)


@dataclass
class ClusterLimits:
    cache_mb: int = 4096
    replicas: int = 60


@dataclass
class Forecast:
    """Per-tenant demand forecast over the horizon, from a linear trend fit
    to the recent history. Persistence would collapse the horizon into a
    single step; the trend is what makes lookahead worth having. W31 swaps
    this for live WorkloadProfile-informed forecasts."""

    history: list = field(default_factory=list)  # recent Demand objects
    window: int = 3

    def observe(self, demand: Demand) -> None:
        self.history.append(demand)
        if len(self.history) > self.window:
            self.history.pop(0)

    def horizon(self) -> list[Demand]:
        if not self.history:
            return [Demand()] * HORIZON_STEPS
        last = self.history[-1]
        if len(self.history) < 2:
            return [last] * HORIZON_STEPS
        prev = self.history[0]
        span = max(1, len(self.history) - 1)
        out = []
        kinds = set(last.rps) | set(prev.rps)
        for k in range(1, HORIZON_STEPS + 1):
            rps = {}
            for kind in kinds:
                a, b = prev.rps.get(kind, 0.0), last.rps.get(kind, 0.0)
                rps[kind] = max(0.0, b + (b - a) / span * k)
            out.append(Demand(rps=rps, crud_base_ms=last.crud_base_ms))
        return out


@dataclass
class PlanEntry:
    state: TenantState
    projected_cost_usd: float
    projected_violation: float


class JCACController:
    """Coordinate-descent MPC over all tenants against a shared objective."""

    def __init__(
        self,
        configs: dict[str, TenantConfig],
        weights: Weights | None = None,
        limits: ClusterLimits | None = None,
    ):
        self.configs = configs
        self.weights = weights or Weights()
        self.limits = limits or ClusterLimits()
        self.forecasts = {tid: Forecast() for tid in configs}

    def plan(
        self, states: dict[str, TenantState], demands: dict[str, Demand]
    ) -> dict[str, PlanEntry]:
        """One control cycle: observe demand, optimize, return the first
        action of the best move-blocked plan for every tenant."""
        for tid, demand in demands.items():
            self.forecasts[tid].observe(demand)
        horizons = {tid: self.forecasts[tid].horizon() for tid in self.configs}

        chosen = dict(states)
        # Two sweeps of coordinate descent: tenant order is fixed, each
        # tenant optimizes against the others' current choices; the second
        # sweep lets early tenants react to late ones.
        for _ in range(2):
            for tid in sorted(self.configs):
                chosen[tid] = self._best_for_tenant(tid, chosen, horizons)

        plans = {}
        for tid, state in chosen.items():
            cost, violation, _ = self._project(tid, state, horizons[tid])
            plans[tid] = PlanEntry(state=state, projected_cost_usd=cost, projected_violation=violation)
        return plans

    def _project(self, tid: str, state: TenantState, horizon: list[Demand]) -> tuple[float, float, float]:
        """Horizon-mean (cost, bounded violation, objective violation).

        The objective term is log1p(excess): unbounded so deep overload
        still has a gradient, compressive so the controller doesn't
        sacrifice everything else to a single hopeless tenant-step.
        """
        cost = violation = obj = 0.0
        for demand in horizon:
            m = evaluate_step(self.configs[tid], state, demand)
            cost += m.cost_usd
            violation += m.violation
            obj += math.log1p(m.excess)
        n = max(1, len(horizon))
        return cost / n, violation / n, obj / n

    def _best_for_tenant(
        self,
        tid: str,
        chosen: dict[str, TenantState],
        horizons: dict[str, list[Demand]],
    ) -> TenantState:
        config = self.configs[tid]
        current = chosen[tid]
        budget_per_step = config.hourly_budget_usd * model.CONTROL_INTERVAL_S / 3600.0

        # Others' contributions are fixed during this tenant's move.
        other_cost = other_obj = 0.0
        other_satisfaction = []
        others_cache = others_replicas = 0
        for oid, ostate in chosen.items():
            if oid == tid:
                continue
            cost, viol, obj = self._project(oid, ostate, horizons[oid])
            other_cost += cost
            other_obj += obj
            other_satisfaction.append(1.0 - viol)
            others_cache += ostate.cache_mb
            others_replicas += ostate.replicas

        best_state, best_score = current, float("inf")
        for delta in DELTA_REPLICAS:
            for cache_mb in neighbor_cache_levels(current.cache_mb):
                for tier in TIERS:
                    candidate = apply_action(config, current, delta, cache_mb, tier)
                    if others_cache + candidate.cache_mb > self.limits.cache_mb:
                        continue
                    if others_replicas + candidate.replicas > self.limits.replicas:
                        continue
                    cost, viol, obj = self._project(tid, candidate, horizons[tid])
                    if cost > budget_per_step:
                        continue
                    switches = (
                        (candidate.replicas != current.replicas)
                        + (candidate.cache_mb != current.cache_mb)
                        + (candidate.tier != current.tier)
                    )
                    fairness = 1.0 - jain_index(other_satisfaction + [1.0 - viol])
                    score = (
                        self.weights.alpha * (other_cost + cost) / COST_SCALE_USD
                        + self.weights.beta * (other_obj + obj)
                        + self.weights.gamma * (0.5 + config.fairness_weight) * fairness
                        + SWITCH_PENALTY * switches
                    )
                    if score < best_score:
                        best_score, best_state = score, candidate
        # An entirely infeasible lattice (tight budget + tight cluster)
        # falls back to shedding cost: floor replicas, no cache, no model.
        if best_score == float("inf"):
            return TenantState(replicas=config.replica_min, cache_mb=0, tier="none")
        return best_state
