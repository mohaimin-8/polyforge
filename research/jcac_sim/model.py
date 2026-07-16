"""JCAC system model (W30).

The Joint Cross-layer Adaptive Controller reasons about three knobs per
tenant — replicas, semantic-cache size, model tier — against demand
expressed in *work units*. Everything here is the offline model of how the
PolyForge data plane responds to a configuration; the controller and every
baseline share it, so comparisons measure decision quality, not modeling
luck.

State  x_i = (replicas, cache_mb, tier)          per tenant i
Action u_i = (Δreplicas ∈ [-2,2], cache level ±1, tier ∈ TIERS)
Cost   J   = α·cost_norm + β·slo_violation + γ·(1 − Jain)

All money is USD, all latencies milliseconds, all rates per second.
The model is deliberately simple and stated; its role is ranking
controllers under identical assumptions, not predicting absolute
production numbers (those come from the W33 harness on a live cluster).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

CONTROL_INTERVAL_S = 10
HORIZON_STEPS = 6  # 60 s lookahead, re-planned every interval

# Demand weight of one request, in work units (wu). A replica serves
# REPLICA_CAPACITY_WU wu/s; congestion is queueing on this budget.
WORK_UNITS = {
    "crud_read": 1.0,
    "crud_write": 2.0,
    "batch": 8.0,
    "embed": 5.0,
    "chat": 20.0,
    "agent": 40.0,
}
REPLICA_CAPACITY_WU = 100.0

CRUD_KINDS = ("crud_read", "crud_write", "batch")
AI_KINDS = ("chat", "embed", "agent")

# Fraction of each AI kind that is semantically repeatable (W28 cache study).
CACHEABLE_FRACTION = {"chat": 0.40, "embed": 0.70, "agent": 0.10}
CACHE_HIT_LATENCY_MS = 20.0
CACHE_HALF_MB = 256.0  # hit-rate saturates: h(c) = HMAX * c / (c + half)
CACHE_HIT_MAX = 0.85

TIERS = ("none", "small", "mid", "large")
TIER_COST_USD_PER_REQ = {"none": 0.0, "small": 0.0001, "mid": 0.001, "large": 0.01}
# Base model latency; agent traffic needs mid+ capacity or it degrades
# (tool-loop retries), which is what makes tier choice non-trivial.
TIER_BASE_LATENCY_MS = {
    "chat": {"small": 300.0, "mid": 800.0, "large": 2000.0},
    "embed": {"small": 80.0, "mid": 150.0, "large": 400.0},
    "agent": {"small": 6000.0, "mid": 2500.0, "large": 3500.0},
}
NO_TIER_LATENCY_MS = 30000.0  # AI request with tier=none: effectively an outage

REPLICA_COST_USD_HR = 0.048
MEM_COST_USD_GB_HR = 0.005

CACHE_LEVELS_MB = (0, 64, 128, 256, 512, 1024)

# The constants above are the *published* economy: every committed campaign
# ran on them and stays bit-reproducible because they are the defaults.
# Pre-registered sensitivity reruns (PREREG_TIER_RATIO.md,
# PREREG_HK_ADOPTION.md) replay the same matrices under a different economy
# via set_economy(); hit_rate/evaluate_step read the module globals at call
# time, and the controllers plan through those same functions, so one
# override moves the *world* and every controller's *beliefs* together —
# the shared-model contract this file's docstring promises is preserved.
_DEFAULT_TIER_COST_USD_PER_REQ = dict(TIER_COST_USD_PER_REQ)
_DEFAULT_CACHE_HIT_MAX = CACHE_HIT_MAX
_DEFAULT_CACHE_HALF_MB = CACHE_HALF_MB


def set_economy(
    tier_cost_usd_per_req: dict | None = None,
    cache_hit_max: float | None = None,
    cache_half_mb: float | None = None,
) -> None:
    """Reset the economy to the published defaults, then apply overrides.

    Always resets first so a worker process is stateless across runs: calling
    with no arguments restores the frozen constants exactly. The price table
    is mutated in place (never rebound) so `from model import` aliases keep
    seeing the active economy.
    """
    global CACHE_HIT_MAX, CACHE_HALF_MB
    TIER_COST_USD_PER_REQ.clear()
    TIER_COST_USD_PER_REQ.update(_DEFAULT_TIER_COST_USD_PER_REQ)
    if tier_cost_usd_per_req:
        unknown = set(tier_cost_usd_per_req) - set(TIERS)
        if unknown:
            raise ValueError(f"unknown tiers in economy override: {sorted(unknown)}")
        if any(v < 0.0 for v in tier_cost_usd_per_req.values()):
            raise ValueError("tier prices must be non-negative")
        TIER_COST_USD_PER_REQ.update(tier_cost_usd_per_req)
    CACHE_HIT_MAX = _DEFAULT_CACHE_HIT_MAX if cache_hit_max is None else float(cache_hit_max)
    CACHE_HALF_MB = _DEFAULT_CACHE_HALF_MB if cache_half_mb is None else float(cache_half_mb)
    if not 0.0 <= CACHE_HIT_MAX <= 1.0 or CACHE_HALF_MB <= 0.0:
        raise ValueError("cache_hit_max must be in [0,1] and cache_half_mb positive")

# SLO targets on estimated p95, per traffic family, scaled by class.
SLO_BASE_MS = {"crud": 150.0, "ai": 2500.0}
SLO_CLASS_FACTOR = {"premium": 1.0, "standard": 2.5, "best-effort": 8.0}

P95_FACTOR = 1.4  # p95 ≈ mean · 1.4 under the assumed latency distribution
MAX_CONGESTION = 50.0
SATURATION_RHO = 0.95


@dataclass(frozen=True)
class TenantConfig:
    """Static per-tenant facts the controller may not change."""

    tenant_id: str
    slo_class: str = "standard"
    hourly_budget_usd: float = 5.0
    replica_min: int = 1
    replica_max: int = 10
    fairness_weight: float = 0.5


@dataclass(frozen=True)
class TenantState:
    """One tenant's controllable configuration."""

    replicas: int = 2
    cache_mb: int = 128
    tier: str = "small"


@dataclass(frozen=True)
class Demand:
    """Aggregated demand for one tenant over one control interval."""

    rps: dict = field(default_factory=dict)  # kind -> requests/second
    crud_base_ms: float = 50.0  # mean expected latency of CRUD requests

    def work_units(self, cache_mb: int) -> float:
        """Effective wu/s after the semantic cache absorbs its hits."""
        total = 0.0
        hit = hit_rate(cache_mb)
        for kind, rate in self.rps.items():
            if rate <= 0.0:
                continue
            served = rate
            if kind in AI_KINDS:
                served = rate * (1.0 - hit * CACHEABLE_FRACTION[kind])
            total += served * WORK_UNITS[kind]
        return total

    def total_rps(self) -> float:
        return sum(self.rps.values())


def hit_rate(cache_mb: int) -> float:
    if cache_mb <= 0:
        return 0.0
    return CACHE_HIT_MAX * cache_mb / (cache_mb + CACHE_HALF_MB)


def congestion(demand_wu: float, replicas: int) -> float:
    """Latency inflation from utilization: 1/(1-ρ) below saturation, then
    quadratic growth in overload (capped). Overload must stay *graded* —
    if every saturated state mapped to one flat cap, an optimizer in deep
    overload would see no reason to add the first replicas."""
    if replicas <= 0:
        return MAX_CONGESTION
    rho = demand_wu / (replicas * REPLICA_CAPACITY_WU)
    if rho < SATURATION_RHO:
        return 1.0 / (1.0 - rho)
    at_saturation = 1.0 / (1.0 - SATURATION_RHO)
    return min(MAX_CONGESTION, at_saturation * (rho / SATURATION_RHO) ** 2)


@dataclass(frozen=True)
class StepMetrics:
    """Everything the objective needs about one tenant-step.

    `violation` is bounded to [0,1] for reporting and fairness;
    `excess` is the same traffic-weighted SLO overshoot left unbounded,
    because an optimizer needs to tell a 4× overload from a 20× one.
    """

    cost_usd: float
    violation: float  # 0..1, traffic-weighted, saturating
    excess: float  # >= 0, traffic-weighted, unbounded
    crud_p95_ms: float
    ai_p95_ms: float
    cache_hit_rate: float
    # Cost decomposition (cost_usd = cost_infra_usd + cost_tier_usd). The
    # W33 harness scales the tier component for systems that evict with
    # plain LRU instead of the W28 cost-aware policy — eviction changes
    # *which* requests miss, so it moves inference spend, not infra spend.
    cost_infra_usd: float = 0.0
    cost_tier_usd: float = 0.0


def evaluate_step(config: TenantConfig, state: TenantState, demand: Demand) -> StepMetrics:
    """Predicted cost and SLO outcome of holding `state` for one interval."""
    factor = SLO_CLASS_FACTOR[config.slo_class]
    inflate = congestion(demand.work_units(state.cache_mb), state.replicas)
    hit = hit_rate(state.cache_mb)

    crud_rps = sum(demand.rps.get(k, 0.0) for k in CRUD_KINDS)
    ai_rps = sum(demand.rps.get(k, 0.0) for k in AI_KINDS)

    crud_p95 = demand.crud_base_ms * inflate * P95_FACTOR

    # AI latency: cache hits are flat-fast, misses pay the tier price.
    ai_p95 = 0.0
    miss_rps = 0.0
    if ai_rps > 0.0:
        weighted_ms = 0.0
        for kind in AI_KINDS:
            rate = demand.rps.get(kind, 0.0)
            if rate <= 0.0:
                continue
            hit_share = hit * CACHEABLE_FRACTION[kind]
            if state.tier == "none":
                miss_ms = NO_TIER_LATENCY_MS
            else:
                miss_ms = TIER_BASE_LATENCY_MS[kind][state.tier] * inflate
            weighted_ms += rate * (hit_share * CACHE_HIT_LATENCY_MS + (1.0 - hit_share) * miss_ms)
            miss_rps += rate * (1.0 - hit_share)
        ai_p95 = weighted_ms / ai_rps * P95_FACTOR

    # Violation: how far each family's p95 overshoots its target, weighted
    # by that family's share of traffic, saturating at 1.
    total_rps = crud_rps + ai_rps
    violation = 0.0
    excess = 0.0
    if total_rps > 0.0:
        crud_target = SLO_BASE_MS["crud"] * factor
        ai_target = SLO_BASE_MS["ai"] * factor
        if crud_rps > 0.0:
            over = max(0.0, crud_p95 / crud_target - 1.0)
            violation += (crud_rps / total_rps) * min(1.0, over)
            excess += (crud_rps / total_rps) * over
        if ai_rps > 0.0:
            over = max(0.0, ai_p95 / ai_target - 1.0)
            violation += (ai_rps / total_rps) * min(1.0, over)
            excess += (ai_rps / total_rps) * over

    interval_hr = CONTROL_INTERVAL_S / 3600.0
    cost_infra = state.replicas * REPLICA_COST_USD_HR * interval_hr
    cost_infra += (state.cache_mb / 1024.0) * MEM_COST_USD_GB_HR * interval_hr
    cost_tier = miss_rps * CONTROL_INTERVAL_S * TIER_COST_USD_PER_REQ[state.tier]

    return StepMetrics(
        cost_usd=cost_infra + cost_tier,
        violation=violation,
        excess=excess,
        crud_p95_ms=crud_p95,
        ai_p95_ms=ai_p95,
        cache_hit_rate=hit,
        cost_infra_usd=cost_infra,
        cost_tier_usd=cost_tier,
    )


def jain_index(values: list[float]) -> float:
    """Jain's fairness index over non-negative values; 1.0 when all equal."""
    if not values:
        return 1.0
    total = sum(values)
    squares = sum(v * v for v in values)
    if squares == 0.0:
        return 1.0
    return (total * total) / (len(values) * squares)


def apply_action(
    config: TenantConfig, state: TenantState, delta_replicas: int, cache_mb: int, tier: str
) -> TenantState:
    """Clamped state transition — the same guardrails the operator enforces:
    replica bounds always win over whatever the optimizer wanted."""
    replicas = max(config.replica_min, min(config.replica_max, state.replicas + delta_replicas))
    return replace(state, replicas=replicas, cache_mb=cache_mb, tier=tier)


def neighbor_cache_levels(cache_mb: int) -> tuple[int, ...]:
    """Cache may move at most one level per interval (thrash prevention)."""
    if cache_mb not in CACHE_LEVELS_MB:
        return (CACHE_LEVELS_MB[0],)
    i = CACHE_LEVELS_MB.index(cache_mb)
    lo, hi = max(0, i - 1), min(len(CACHE_LEVELS_MB) - 1, i + 1)
    return tuple(CACHE_LEVELS_MB[lo : hi + 1])
