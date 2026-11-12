"""Per-layer comparators (programme 2, WP9): no shared objective.

- `RulesStackController`: the per-layer stack of rules. Pool: Kubernetes'
  default-behaviour HPA on each tenant's pool (k8s_hpa.py). Cache: a sizing
  rule on marginal hits per MB. Router: a preferred tier with a latency
  fallback (baselines.cheapest_meeting_tier).
- `IndependentLayersController`: each layer its own small planner with its
  own signal and local objective, over the same forecast machinery the joint
  planner uses. Pool: utilisation or queue against a target, priced against
  replica cost. Cache: expected hits, valued at a fixed price per hit, against
  memory cost. Router: per-request price, AI p95 overshoot and (when priced)
  quality loss, at the pool and cache the other layers left.
- `BudgetGate`: the gateway budget gate both per-layer arms run behind.

All three layers of a controller read the same interval-start state and the
same demand, and none sees another's move: what the joint planner adds is
exactly the coordination. The cluster caps (`baselines.CapEnforced`) apply
first and the budget gate last (arms.py builds BudgetGate(CapEnforced(...))):
the gate only lowers replicas or refuses billed calls, so the final state
keeps both the caps and the budget. (Programme 1's `*_gated_capped` arms used
the other order, in which the cap's cache step-down can push a bill back over
budget on a plant without the admission gate.)
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

_SIM = Path(__file__).resolve().parents[1] / "jcac_sim"
if str(_SIM) not in sys.path:
    sys.path.insert(0, str(_SIM))

import model  # noqa: E402
from baselines import TIER_PRICE_ORDER, cheapest_meeting_tier  # noqa: E402
from controller import COST_SCALE_USD, Forecast  # noqa: E402
from model import (  # noqa: E402
    AI_KINDS,
    CACHE_LEVELS_MB,
    CACHEABLE_FRACTION,
    SLO_BASE_MS,
    SLO_CLASS_FACTOR,
    Demand,
    TenantConfig,
    TenantState,
    apply_action,
    evaluate_step,
    hit_rate,
)

from k8s_hpa import METRICS, K8sReplicaLayer  # noqa: E402
from tunable_planner import TruncatedForecast, check_horizon  # noqa: E402

REPLICA_DELTAS = (-2, -1, 0, 1, 2)  # the joint planner's per-interval clamp
FORECASTS = ("persistence", "trend", "holt", "seasonal")


def cacheable_rps(demand: Demand) -> float:
    """AI requests per second the semantic cache can answer, under the active
    plant (every AI kind is cacheable when CACHEABLE_UNIFORM is set)."""
    return sum(demand.rps.get(k, 0.0) * (1.0 if model.CACHEABLE_UNIFORM else CACHEABLE_FRACTION[k])
               for k in AI_KINDS)


def ai_target_ms(config: TenantConfig) -> float:
    return SLO_BASE_MS["ai"] * SLO_CLASS_FACTOR[config.slo_class]


def _admitted_levels(config: TenantConfig, tier: str, levels) -> list[int]:
    return [c for c in levels if config.knob_admits(c, tier)]


def _combine(config: TenantConfig, state: TenantState, replicas: int, cache_mb: int,
             tier: str) -> TenantState:
    """The layers' choices as one state. A (cache, tier) pair the tenant's
    bounds refuse keeps the cache choice with the current tier if that pair
    is admitted, else the tier choice with the current cache, else the
    current pair (population tenants admit every pair)."""
    if not config.knob_admits(cache_mb, tier):
        if config.knob_admits(cache_mb, state.tier):
            tier = state.tier
        elif config.knob_admits(state.cache_mb, tier):
            cache_mb = state.cache_mb
        else:
            cache_mb, tier = state.cache_mb, state.tier
    return apply_action(config, state, replicas - state.replicas, cache_mb, tier)


# --------------------------------------------------------------------------
# Stack of rules
# --------------------------------------------------------------------------

def cache_sizing_rule(config: TenantConfig, state: TenantState, demand: Demand,
                      hits_per_mb_s: float) -> int:
    """Grow one level while the next level buys at least `hits_per_mb_s`
    extra hits per second per MB; shrink one level when the current level no
    longer earns that over the level below (idle traffic earns nothing)."""
    levels = _admitted_levels(config, state.tier, CACHE_LEVELS_MB)
    if not levels:
        return state.cache_mb
    rate = cacheable_rps(demand)
    current = state.cache_mb if state.cache_mb in levels else levels[0]
    i = levels.index(current)

    def earns(lo: int, hi: int) -> bool:
        return rate * (hit_rate(hi) - hit_rate(lo)) / (hi - lo) >= hits_per_mb_s

    if i + 1 < len(levels) and earns(levels[i], levels[i + 1]):
        return levels[i + 1]
    if i > 0 and not earns(levels[i - 1], levels[i]):
        return levels[i - 1]
    return current


def router_rule(config: TenantConfig, state: TenantState, demand: Demand, preferred: str) -> str:
    """Serve on the preferred tier while its projected AI p95 meets the
    tenant's target; otherwise the cheapest tier that meets it, else the
    cheapest (baselines.cheapest_meeting_tier)."""
    if sum(demand.rps.get(k, 0.0) for k in AI_KINDS) <= 0.0:
        return state.tier
    if config.knob_admits(state.cache_mb, preferred):
        probe = replace(state, tier=preferred)
        if evaluate_step(config, probe, demand).ai_p95_ms <= ai_target_ms(config):
            return preferred
    return cheapest_meeting_tier(config, state, demand)


class RulesStackController:
    """Per-layer stack of rules: default-behaviour HPA, cache-sizing rule,
    router rule (the budget gate and cluster caps wrap it, see arms.py)."""

    name = "wp9_rules"

    def __init__(self, configs: dict[str, TenantConfig], hpa_metric: str = "utilisation",
                 hpa_target_utilisation: float = 0.6, hpa_target_queue: float = 1.5,
                 hpa_behavior: str = "default", cache_hits_per_mb_s: float = 1e-3,
                 router_tier: str = "small"):
        if router_tier not in TIER_PRICE_ORDER:
            raise ValueError(f"router_tier must be one of {TIER_PRICE_ORDER}, not {router_tier!r}")
        if cache_hits_per_mb_s <= 0.0:
            raise ValueError("cache_hits_per_mb_s must be positive")
        self.configs = configs
        self.pool = K8sReplicaLayer(configs, metric=hpa_metric, target_utilisation=hpa_target_utilisation,
                                    target_queue=hpa_target_queue, behavior=hpa_behavior)
        self.hits_per_mb_s = cache_hits_per_mb_s
        self.router_tier = router_tier

    def plan(self, states: dict[str, TenantState], demands: dict[str, Demand]) -> dict[str, TenantState]:
        replicas = self.pool.replicas(states, demands)
        out = {}
        for tid, state in states.items():
            config, demand = self.configs[tid], demands.get(tid, Demand())
            cache_mb = cache_sizing_rule(config, state, demand, self.hits_per_mb_s)
            tier = router_rule(config, state, demand, self.router_tier)
            out[tid] = _combine(config, state, replicas[tid], cache_mb, tier)
        return out


# --------------------------------------------------------------------------
# Independent per-layer planners
# --------------------------------------------------------------------------

def _argmin(candidates: list, score, current) -> object:
    """The candidate with the lowest score; ties keep `current`, then the
    earlier candidate (callers list cheaper candidates first)."""
    scored = [(score(c), c) for c in candidates]
    best = min(s for s, _ in scored)
    tied = [c for s, c in scored if s == best]
    return current if current in tied else tied[0]


class IndependentLayersController:
    """Three planners, one per layer, each minimising its own local objective
    averaged over the first `horizon_steps` steps of its tenant's forecast.

    pool    cost of r replicas + pool_weight x max(0, s(r) / target - 1), s =
            utilisation (offered work / capacity) or queue (work in flight per
            replica), at the interval-start cache and tier; r within +/-2
    cache   memory cost of c - hit_value_usd x expected hits h(c) x cacheable
            requests; c within one level
    router  per-request spend + router_latency_weight x max(0, AI p95 /
            target - 1) + quality_weight x quality loss, at the interval-start
            replicas and cache; any serving tier
    All terms in COST_SCALE_USD units per step, so a weight of 1 trades one
    unit of signal overshoot against one cent.
    """

    name = "wp9_layers"

    def __init__(self, configs: dict[str, TenantConfig], forecast_method: str = "trend",
                 horizon_steps: int = model.HORIZON_STEPS, pool_signal: str = "utilisation",
                 pool_target_utilisation: float = 0.6, pool_target_queue: float = 1.5,
                 pool_weight: float = 1.0, hit_value_usd: float = 1e-4,
                 router_latency_weight: float = 2.0, quality_weight: float = 0.0):
        if forecast_method not in FORECASTS:
            raise ValueError(f"forecast_method must be one of {FORECASTS}, not {forecast_method!r}")
        if pool_signal not in METRICS:
            raise ValueError(f"pool_signal must be one of {METRICS}, not {pool_signal!r}")
        if min(pool_target_utilisation, pool_target_queue) <= 0.0:
            raise ValueError("pool targets must be positive")
        if min(pool_weight, hit_value_usd, router_latency_weight, quality_weight) < 0.0:
            raise ValueError("layer weights must be non-negative")
        self.configs = configs
        self.forecasts: dict[str, Forecast] = {
            tid: TruncatedForecast(method=forecast_method, steps=check_horizon(horizon_steps))
            for tid in configs}
        self.pool_signal = pool_signal
        self.pool_target = pool_target_utilisation if pool_signal == "utilisation" else pool_target_queue
        self.pool_weight = pool_weight
        self.hit_value_usd = hit_value_usd
        self.router_latency_weight = router_latency_weight
        self.quality_weight = quality_weight

    def plan(self, states: dict[str, TenantState], demands: dict[str, Demand]) -> dict[str, TenantState]:
        out = {}
        for tid, state in states.items():
            config = self.configs[tid]
            forecast = self.forecasts[tid]
            forecast.observe(demands.get(tid, Demand()))
            horizon = forecast.horizon()
            replicas = self._pool(config, state, horizon)
            cache_mb = self._cache(config, state, horizon)
            tier = self._router(config, state, horizon)
            out[tid] = _combine(config, state, replicas, cache_mb, tier)
        return out

    def _pool(self, config: TenantConfig, state: TenantState, horizon: list[Demand]) -> int:
        step_price = model.REPLICA_COST_USD_HR * model.CONTROL_INTERVAL_S / 3600.0
        loads = [d.work_units(state.cache_mb, state.tier) for d in horizon]
        options = sorted({apply_action(config, state, d, state.cache_mb, state.tier).replicas
                          for d in REPLICA_DELTAS})

        def signal(wu: float, r: int) -> float:
            rho = wu / (r * model.REPLICA_CAPACITY_WU)
            return rho if self.pool_signal == "utilisation" else rho * model.congestion(wu, r)

        def score(r: int) -> float:
            over = sum(max(0.0, signal(wu, r) / self.pool_target - 1.0) for wu in loads) / len(loads)
            return r * step_price / COST_SCALE_USD + self.pool_weight * over

        return _argmin(options, score, state.replicas)

    def _cache(self, config: TenantConfig, state: TenantState, horizon: list[Demand]) -> int:
        options = _admitted_levels(config, state.tier, model.neighbor_cache_levels(state.cache_mb))
        if not options:
            return state.cache_mb
        rates = [cacheable_rps(d) for d in horizon]
        mean_rate = sum(rates) / len(rates)

        def score(c: int) -> float:
            memory = (c / 1024.0) * model.MEM_COST_USD_GB_HR * model.CONTROL_INTERVAL_S / 3600.0
            hits = hit_rate(c) * mean_rate * model.CONTROL_INTERVAL_S
            return (memory - self.hit_value_usd * hits) / COST_SCALE_USD

        return _argmin(options, score, state.cache_mb)

    def _router(self, config: TenantConfig, state: TenantState, horizon: list[Demand]) -> str:
        if all(sum(d.rps.get(k, 0.0) for k in AI_KINDS) <= 0.0 for d in horizon):
            return state.tier
        options = [t for t in TIER_PRICE_ORDER if config.knob_admits(state.cache_mb, t)]
        if not options:
            return state.tier
        target = ai_target_ms(config)

        def score(tier: str) -> float:
            total = 0.0
            for d in horizon:
                m = evaluate_step(config, replace(state, tier=tier), d)
                total += (m.cost_tier_usd / COST_SCALE_USD
                          + self.router_latency_weight * max(0.0, m.ai_p95_ms / target - 1.0)
                          + self.quality_weight * m.quality_loss)
            return total / len(horizon)

        return _argmin(options, score, state.tier)


# --------------------------------------------------------------------------
# Budget gate
# --------------------------------------------------------------------------

class BudgetGate:
    """The gateway budget gate of a per-layer stack, on the joint planner's
    own per-step cap (hourly budget x interval). When the step the inner
    controller wants would bill more than the cap on the observed demand:

    1. if its model calls are billed per request and the tenant's bounds
       admit tier "none", refuse them (cache hits still answered) when that
       alone fits the budget;
    2. otherwise trim replicas from the wanted count down, keeping the
       largest count that fits, else the tenant's floor.

    Step 1 is baselines.BudgetGated's lever. Unlike BudgetGated it is never
    taken when refusing saves nothing -- in a self-hosted plant (mode B) the
    bill is replica-hours, and BudgetGated would shed every AI request AND
    trim -- and its trim is a guardrail on the wanted count, not a +/-2
    move, because the default-behaviour HPA may legitimately move further.
    In plant 2 the plant's own admission gate already caps per-request
    spend, so this gate binds only when infrastructure alone is over budget.
    """

    def __init__(self, inner):
        self.inner = inner
        self.configs = inner.configs
        self.name = f"{inner.name}_gate"
        self.gated_steps = 0  # reporting: tenant-steps the gate changed

    def set_limits(self, limits) -> None:
        if hasattr(self.inner, "set_limits"):
            self.inner.set_limits(limits)

    def observe_feedback(self, realized_violation: dict[str, float]) -> None:
        observe = getattr(self.inner, "observe_feedback", None)
        if observe is not None:
            observe(realized_violation)

    def plan(self, states: dict[str, TenantState], demands: dict[str, Demand]) -> dict[str, TenantState]:
        proposed = self.inner.plan(states, demands)
        out = {}
        for tid, want in proposed.items():
            gated = gate_state(self.configs[tid], want, demands.get(tid, Demand()))
            self.gated_steps += gated != want
            out[tid] = gated
        return out


def gate_state(config: TenantConfig, want: TenantState, demand: Demand) -> TenantState:
    """BudgetGate's rule for one tenant-step (see the class)."""
    cap = config.hourly_budget_usd * model.CONTROL_INTERVAL_S / 3600.0

    def billed(s: TenantState) -> float:
        return evaluate_step(config, s, demand).cost_usd

    bill = billed(want)
    if bill <= cap:
        return want
    base = want
    if want.tier != "none" and config.knob_admits(want.cache_mb, "none"):
        refused = replace(want, tier="none")
        refused_bill = billed(refused)
        if refused_bill < bill:
            if refused_bill <= cap:
                return refused
            base = refused
    for r in range(base.replicas, config.replica_min - 1, -1):
        trimmed = replace(base, replicas=r)
        if billed(trimmed) <= cap:
            return trimmed
    return replace(base, replicas=config.replica_min)
