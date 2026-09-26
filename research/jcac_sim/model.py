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

import contextlib
import math
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

# --- energy and carbon (M4 / T18) ------------------------------------------
# Reported on every step, never priced into `cost_usd`, and entering the
# controller's objective only when `carbon_weight` is set (default off, so
# every committed campaign replays bit-for-bit).
#
# WHERE THIS DIMENSION IS REAL, AND WHERE IT IS NOT — measured, not assumed.
# The design intent was that carbon would diverge from dollars because tier
# *price* rises 1 : 10 : 100 across small/mid/large while tier *energy* rises
# about 1 : 4 : 16. Enumerating the lattice shows that intent is NOT met under
# a constant grid: over 240 configurations there are 2 discordant (cost,
# carbon) pairs out of ~57,000 comparisons, and both are near-ties where one
# side is already violating outright. Every knob moves cost and carbon the same
# way — more replicas, heavier tiers and fewer cache hits all cost more and
# emit more — so with a fixed intensity, minimising spend already minimises
# grams and pricing carbon changes essentially nothing. Said plainly: as a
# static per-step term this would be decorative.
#
# What makes it a genuine dimension is the grid, not the lattice.
# `CARBON_INTENSITY_G_PER_KWH` varies over time in the real world and price
# does not follow it, so the same plan is clean at 03:00 and dirty at 18:00
# while costing the identical amount. A carbon-pricing controller therefore
# reacts to something a cost-only controller cannot represent at all, which
# `test_grid_intensity_moves_the_plan_only_when_carbon_is_priced` asserts
# directly. The knob is sharp: at low weights nothing moves, and at high
# weights the controller will shed AI traffic outright to stop emitting.
#
# The figures are order-of-magnitude public estimates for commodity serving
# hardware and an average grid mix. They are NOT measurements of this system,
# and no energy or carbon number produced here is quotable as measured.
REPLICA_POWER_W = 45.0
MEM_POWER_W_PER_GB = 0.4
TIER_ENERGY_WH_PER_REQ = {"none": 0.0, "small": 0.15, "mid": 0.6, "large": 2.4}
CARBON_INTENSITY_G_PER_KWH = 400.0

_DEFAULT_REPLICA_POWER_W = REPLICA_POWER_W
_DEFAULT_MEM_POWER_W_PER_GB = MEM_POWER_W_PER_GB
_DEFAULT_TIER_ENERGY_WH_PER_REQ = dict(TIER_ENERGY_WH_PER_REQ)
_DEFAULT_CARBON_INTENSITY = CARBON_INTENSITY_G_PER_KWH


def set_energy(
    replica_power_w: float | None = None,
    mem_power_w_per_gb: float | None = None,
    tier_energy_wh_per_req: dict | None = None,
    carbon_intensity_g_per_kwh: float | None = None,
) -> None:
    """Reset the energy model to its defaults, then apply overrides.

    Same contract as `set_economy` and `set_model_form`: always resets first so
    a pooled worker process is stateless across runs, and the tier table is
    mutated in place so `from model import` aliases keep seeing the active
    model. Carbon intensity is the natural knob for a grid-mix study — it moves
    carbon without touching price at all.
    """
    global REPLICA_POWER_W, MEM_POWER_W_PER_GB, CARBON_INTENSITY_G_PER_KWH
    REPLICA_POWER_W = (
        _DEFAULT_REPLICA_POWER_W if replica_power_w is None else float(replica_power_w))
    MEM_POWER_W_PER_GB = (
        _DEFAULT_MEM_POWER_W_PER_GB if mem_power_w_per_gb is None
        else float(mem_power_w_per_gb))
    CARBON_INTENSITY_G_PER_KWH = (
        _DEFAULT_CARBON_INTENSITY if carbon_intensity_g_per_kwh is None
        else float(carbon_intensity_g_per_kwh))
    TIER_ENERGY_WH_PER_REQ.clear()
    TIER_ENERGY_WH_PER_REQ.update(_DEFAULT_TIER_ENERGY_WH_PER_REQ)
    if tier_energy_wh_per_req:
        unknown = set(tier_energy_wh_per_req) - set(TIERS)
        if unknown:
            raise ValueError(f"unknown tiers in energy override: {sorted(unknown)}")
        if any(v < 0.0 for v in tier_energy_wh_per_req.values()):
            raise ValueError("tier energies must be non-negative")
        TIER_ENERGY_WH_PER_REQ.update(tier_energy_wh_per_req)
    if REPLICA_POWER_W < 0.0 or MEM_POWER_W_PER_GB < 0.0 or CARBON_INTENSITY_G_PER_KWH < 0.0:
        raise ValueError("energy model constants must be non-negative")

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

# --- structural model-form overrides (Wave 5) -------------------------
# The published model keeps three deliberate simplifications: an M/M/1-
# shaped congestion exponent of exactly 1, a p95/mean factor that is flat
# in utilization, and work units that do not depend on the model tier.
# The Phase 6 calibration measured the first two (a = 0.86; p95/mean
# 1.59..2.41 rising with rho — CALIBRATION.md), and the tier bench
# measured per-tier serving-time ratios (TIER_BENCH.md). Pre-registered
# structural reruns (PREREG_LM_ADOPTION.md, PREREG_MIXTURE_P95.md,
# PREREG_TIER_WU.md) replay the committed matrices under the measured
# forms via set_model_form(); like set_economy, one override moves the
# world and every controller's beliefs together, and the defaults keep
# every committed campaign bit-reproducible.

CONGESTION_EXPONENT = 1.0  # a in g(rho) = (1 - rho)^(-a) below saturation
P95_TAIL: tuple | None = None  # (f0, b): p95/mean = f0*(1-min(rho, sat))^(-b)
MIXTURE_P95 = False  # True: ai_p95 is the 95th percentile of the hit/miss mixture
WU_TIER_FACTOR = {"none": 1.0, "small": 1.0, "mid": 1.0, "large": 1.0}
_DEFAULT_WU_TIER_FACTOR = dict(WU_TIER_FACTOR)

# --- live-plant overrides (session 48, PREREG_WAVE4_SIM_TRANSFER) --------
# B1 measured the simulator's ordinal predictions reproducing live in 0 of
# 4 cells. The live evidence (B1', 40 runs) names three plant facts the
# published constants get wrong: the control-plane replica serves CRUD at
# 1 ms with no congestion from 2 to 35 rps per replica (capacity far above
# 100 wu/s); AI requests never touch the replicas at all (k6 -> gateway ->
# vLLM, while the model charges 20-40 wu each); and the cache hits 0.95-0.97
# at 64 MB in the cacheable cells (the curve gives 0.17). These four knobs
# let a pre-registered rerun put the measured plant under every arm at
# once. Defaults are the published constants, bit-identical.
_DEFAULT_REPLICA_CAPACITY_WU = REPLICA_CAPACITY_WU
WU_AI_SCALE = 1.0          # multiplier on AI kinds' work units (0 = off the replicas)
CRUD_BASE_SCALE = 1.0      # multiplier on demand.crud_base_ms
CACHEABLE_UNIFORM = False  # True: every AI kind is fully cacheable; the ceiling is CACHE_HIT_MAX
# Flat per-tier base latency for every AI kind (ms), None = the published
# per-kind table. The live tiers are three sizes of one chat model: `small`
# is fastest AND cheapest for every kind (257 ms vs 2306 ms for `large` in
# the B1' knob preflight), where the published table makes `agent` degrade
# on `small` (6000 ms) so that tier choice is non-trivial.
TIER_LATENCY_FLAT: dict | None = None

_Z95 = 1.6448536269514722  # standard-normal 95th-percentile z

# The lognormal branch family needs p95/mean < exp(z95^2 / 2).
_MAX_TAIL_FACTOR = math.exp(_Z95 * _Z95 / 2.0)


def set_model_form(
    congestion_exponent: float | None = None,
    p95_tail: tuple | None = None,
    mixture_p95: bool | None = None,
    wu_tier_factor: dict | None = None,
    replica_capacity_wu: float | None = None,
    wu_ai_scale: float | None = None,
    crud_base_scale: float | None = None,
    cacheable_uniform: bool | None = None,
    tier_latency_ms: dict | None = None,
) -> None:
    """Reset the model form to the published defaults, then apply overrides.

    Same contract as set_economy: always resets first so a pooled worker
    process is stateless across runs; calling with no arguments restores
    the published forms exactly. The factor table is mutated in place so
    `from model import` aliases keep seeing the active form.
    """
    global CONGESTION_EXPONENT, P95_TAIL, MIXTURE_P95
    global REPLICA_CAPACITY_WU, WU_AI_SCALE, CRUD_BASE_SCALE, CACHEABLE_UNIFORM, TIER_LATENCY_FLAT
    TIER_LATENCY_FLAT = None
    if tier_latency_ms:
        unknown = set(tier_latency_ms) - {"small", "mid", "large"}
        if unknown or set(tier_latency_ms) != {"small", "mid", "large"}:
            raise ValueError("tier_latency_ms needs exactly small, mid and large")
        if any(float(v) <= 0.0 for v in tier_latency_ms.values()):
            raise ValueError("tier latencies must be positive")
        TIER_LATENCY_FLAT = {k: float(v) for k, v in tier_latency_ms.items()}
    REPLICA_CAPACITY_WU = _DEFAULT_REPLICA_CAPACITY_WU
    if replica_capacity_wu is not None:
        if float(replica_capacity_wu) <= 0.0:
            raise ValueError("replica_capacity_wu must be positive")
        REPLICA_CAPACITY_WU = float(replica_capacity_wu)
    WU_AI_SCALE = 1.0
    if wu_ai_scale is not None:
        if float(wu_ai_scale) < 0.0:
            raise ValueError("wu_ai_scale must be non-negative")
        WU_AI_SCALE = float(wu_ai_scale)
    CRUD_BASE_SCALE = 1.0
    if crud_base_scale is not None:
        if float(crud_base_scale) <= 0.0:
            raise ValueError("crud_base_scale must be positive")
        CRUD_BASE_SCALE = float(crud_base_scale)
    CACHEABLE_UNIFORM = bool(cacheable_uniform) if cacheable_uniform is not None else False
    CONGESTION_EXPONENT = 1.0
    if congestion_exponent is not None:
        a = float(congestion_exponent)
        if not 0.0 < a <= 2.0:
            raise ValueError("congestion_exponent must be in (0, 2]")
        CONGESTION_EXPONENT = a
    P95_TAIL = None
    if p95_tail is not None:
        f0, b = float(p95_tail[0]), float(p95_tail[1])
        if f0 < 1.0 or b < 0.0:
            raise ValueError("p95_tail requires f0 >= 1 and b >= 0")
        if f0 * (1.0 - SATURATION_RHO) ** (-b) >= _MAX_TAIL_FACTOR:
            raise ValueError(
                f"p95_tail factor at the saturation clamp must stay below "
                f"{_MAX_TAIL_FACTOR:.3f} for the mixture family to be well-defined"
            )
        P95_TAIL = (f0, b)
    MIXTURE_P95 = bool(mixture_p95) if mixture_p95 is not None else False
    WU_TIER_FACTOR.clear()
    WU_TIER_FACTOR.update(_DEFAULT_WU_TIER_FACTOR)
    if wu_tier_factor:
        unknown = set(wu_tier_factor) - set(TIERS)
        if unknown:
            raise ValueError(f"unknown tiers in wu_tier_factor: {sorted(unknown)}")
        if any(float(v) <= 0.0 for v in wu_tier_factor.values()):
            raise ValueError("wu_tier_factor values must be positive")
        WU_TIER_FACTOR.update({k: float(v) for k, v in wu_tier_factor.items()})


# Every global set_model_form controls. A snapshot of these is a complete
# description of the structural form evaluate_step computes with.
_FORM_GLOBALS = ("CONGESTION_EXPONENT", "P95_TAIL", "MIXTURE_P95", "REPLICA_CAPACITY_WU",
                 "WU_AI_SCALE", "CRUD_BASE_SCALE", "CACHEABLE_UNIFORM", "TIER_LATENCY_FLAT")


def form_snapshot() -> tuple:
    """The active structural form: the scalar globals plus a copy of the
    tier work-unit table."""
    return tuple(globals()[n] for n in _FORM_GLOBALS), dict(WU_TIER_FACTOR)


def _restore_form(snapshot: tuple) -> None:
    values, wu = snapshot
    for name, value in zip(_FORM_GLOBALS, values):
        globals()[name] = value
    WU_TIER_FACTOR.clear()
    WU_TIER_FACTOR.update(wu)


def published_form() -> tuple:
    """The published forms' snapshot, without disturbing the active form."""
    active = form_snapshot()
    set_model_form()
    published = form_snapshot()
    _restore_form(active)
    return published


@contextlib.contextmanager
def use_form(snapshot: tuple):
    """Evaluate under `snapshot`'s form, then restore the active one, even on
    an exception. Lets a planner BELIEVE one form while the plant it is
    scored on runs another (audit 2026-09-26: the planner otherwise plans
    with the very function that scores it). The engine is single-threaded
    per process, so swapping module globals around a projection is safe."""
    active = form_snapshot()
    _restore_form(snapshot)
    try:
        yield
    finally:
        _restore_form(active)


def p95_factor(rho: float) -> float:
    """Tail factor p95/mean at utilization rho: the published flat constant
    unless a measured tail curve is adopted (clamped at saturation so the
    overload branch stays finite, mirroring the congestion device)."""
    if P95_TAIL is None:
        return P95_FACTOR
    f0, b = P95_TAIL
    return f0 * (1.0 - min(rho, SATURATION_RHO)) ** (-b)


# SLO targets on estimated p95, per traffic family, scaled by class.
SLO_BASE_MS = {"crud": 150.0, "ai": 2500.0}
SLO_CLASS_FACTOR = {"premium": 1.0, "standard": 2.5, "best-effort": 8.0}

P95_FACTOR = 1.4  # p95 ≈ mean · 1.4 under the assumed latency distribution
MAX_CONGESTION = 50.0
SATURATION_RHO = 0.95


@dataclass(frozen=True)
class TenantConfig:
    """Static per-tenant facts the controller may not change.

    The knob bounds mirror the operator's Policy CRD guardrails so the online
    planner and the offline sim enforce the identical envelope. `replica_min`/
    `replica_max` bound the replica knob (already load-bearing since W30);
    `cache_min`/`cache_max` and `tier_min`/`tier_max` bound the cache and tier
    knobs. Setting a knob's min==max *pins* it — the mechanism the live
    cache-only / tier-only ablations use to freeze two of the three knobs while
    the joint controller still optimizes the third (PREREG_WAVE4_LIVE_PLANE.md
    §Arms). The defaults are the full envelope (cache unbounded above, every
    tier admissible), so a config that sets no bound plans over exactly the
    lattice it did before these fields existed — the committed campaigns replay
    bit-for-bit (R4).
    """

    tenant_id: str
    slo_class: str = "standard"
    hourly_budget_usd: float = 5.0
    replica_min: int = 1
    replica_max: int = 10
    fairness_weight: float = 0.5
    cache_min: int = 0
    cache_max: int | None = None  # None = no ceiling (the published default)
    tier_min: str = "none"
    tier_max: str = "large"

    def knob_admits(self, cache_mb: int, tier: str) -> bool:
        """Whether a candidate's cache/tier fall inside this tenant's bounds.

        The replica knob is bounded separately in apply_action (its clamp
        predates these fields); this covers the two knobs the candidate loop
        enumerates freely. With the default full-envelope bounds every
        candidate is admitted, so the planner's search set is unchanged."""
        if cache_mb < self.cache_min:
            return False
        if self.cache_max is not None and cache_mb > self.cache_max:
            return False
        rank = TIERS.index(tier) if tier in TIERS else -1
        lo = TIERS.index(self.tier_min) if self.tier_min in TIERS else 0
        hi = TIERS.index(self.tier_max) if self.tier_max in TIERS else len(TIERS) - 1
        return lo <= rank <= hi


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

    def work_units(self, cache_mb: int, tier: str = "small") -> float:
        """Effective wu/s after the semantic cache absorbs its hits.

        `tier` matters only under the Wave 5 tier-scaled capacity form
        (WU_TIER_FACTOR): a heavier model consumes proportionally more
        serving capacity per AI request. Under the published defaults
        (all factors 1.0) the argument is inert and the arithmetic is
        bit-identical to the original form.
        """
        total = 0.0
        hit = hit_rate(cache_mb)
        ai_factor = WU_TIER_FACTOR[tier]
        for kind, rate in self.rps.items():
            if rate <= 0.0:
                continue
            served = rate
            wu = WORK_UNITS[kind]
            if kind in AI_KINDS:
                cacheable = 1.0 if CACHEABLE_UNIFORM else CACHEABLE_FRACTION[kind]
                served = rate * (1.0 - hit * cacheable)
                if ai_factor != 1.0:
                    wu = wu * ai_factor
                if WU_AI_SCALE != 1.0:
                    wu = wu * WU_AI_SCALE
            total += served * wu
        return total

    def total_rps(self) -> float:
        return sum(self.rps.values())


def tier_base_latency_ms(kind: str, tier: str) -> float:
    """Base latency of an AI `kind` served on `tier`: the published per-kind
    table, or the flat live table when a pre-registered rerun set one."""
    if TIER_LATENCY_FLAT is not None and tier in TIER_LATENCY_FLAT:
        return TIER_LATENCY_FLAT[tier]
    return TIER_BASE_LATENCY_MS[kind][tier]


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
    a = CONGESTION_EXPONENT
    if rho < SATURATION_RHO:
        if a == 1.0:
            return 1.0 / (1.0 - rho)
        return (1.0 - rho) ** (-a)
    at_saturation = 1.0 / (1.0 - SATURATION_RHO) if a == 1.0 else (1.0 - SATURATION_RHO) ** (-a)
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
    # Energy and its carbon (M4 / T18). Always reported, never priced into
    # `cost_usd`, so nothing that reads cost can be perturbed by them.
    energy_kwh: float = 0.0
    carbon_g: float = 0.0


def _mixture_p95_ms(branches: list[tuple[float, float, bool]], tail: float) -> float:
    """95th percentile of a mixture of latency branches.

    `branches` are (weight, mean_ms, deterministic) triples: cache hits are
    a point mass, each miss branch is lognormal with the given mean and a
    p95/mean ratio of `tail` (sigma from z*s - s^2/2 = ln tail, the smaller
    root, which exists whenever tail < exp(z^2/2) — enforced at override
    time). Solved by fixed-count bisection in log space so replays of this
    campaign stay bit-deterministic.
    """
    total = sum(w for w, _, _ in branches if w > 0.0)
    if total <= 0.0:
        return 0.0
    ln_tail = math.log(tail)
    disc = _Z95 * _Z95 - 2.0 * ln_tail
    sigma = _Z95 - math.sqrt(disc) if disc > 0.0 else 0.0
    parts = []  # (weight_share, mu, sigma) in log space; sigma 0 = point mass
    lo = hi = None
    for w, mean_ms, det in branches:
        if w <= 0.0 or mean_ms <= 0.0:
            continue
        s = 0.0 if det else sigma
        mu = math.log(mean_ms) - 0.5 * s * s
        parts.append((w / total, mu, s))
        b_lo, b_hi = mu - 6.0 * s, mu + 6.0 * s
        lo = b_lo if lo is None else min(lo, b_lo)
        hi = b_hi if hi is None else max(hi, b_hi)
    if not parts:
        return 0.0
    inv_sqrt2 = 1.0 / math.sqrt(2.0)

    def cdf(y: float) -> float:
        c = 0.0
        for w, mu, s in parts:
            if s == 0.0:
                if y >= mu:
                    c += w
            else:
                c += w * 0.5 * (1.0 + math.erf((y - mu) / s * inv_sqrt2))
        return c

    lo -= 1e-9
    hi += 1e-9
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if cdf(mid) < 0.95:
            lo = mid
        else:
            hi = mid
    return math.exp(0.5 * (lo + hi))


def evaluate_step(
    config: TenantConfig,
    state: TenantState,
    demand: Demand,
    extra_ai_latency_ms: float = 0.0,
) -> StepMetrics:
    """Predicted cost and SLO outcome of holding `state` for one interval.

    `extra_ai_latency_ms` charges a fixed per-request overhead to AI traffic —
    the cache policy's own bookkeeping cost (PREREG_EVICTION_PARITY: the
    cost-aware policy measures 1104.9 us p99, LRU and ARC measure 0.0, and the
    published model gives it away free). It defaults to 0.0, so every existing
    caller — including the controller's planning projection — is unchanged.
    The scoring path passes it while the planning path does not, deliberately:
    a controller must not be able to plan around its own bookkeeping cost.
    """
    factor = SLO_CLASS_FACTOR[config.slo_class]
    wu = demand.work_units(state.cache_mb, state.tier)
    inflate = congestion(wu, state.replicas)
    hit = hit_rate(state.cache_mb)
    if P95_TAIL is None:
        tail = P95_FACTOR
    else:
        rho = wu / (state.replicas * REPLICA_CAPACITY_WU) if state.replicas > 0 else 1.0
        tail = p95_factor(rho)

    crud_rps = sum(demand.rps.get(k, 0.0) for k in CRUD_KINDS)
    ai_rps = sum(demand.rps.get(k, 0.0) for k in AI_KINDS)

    crud_p95 = demand.crud_base_ms * CRUD_BASE_SCALE * inflate * tail

    # AI latency: cache hits are flat-fast, misses pay the tier price.
    ai_p95 = 0.0
    miss_rps = 0.0
    if ai_rps > 0.0:
        weighted_ms = 0.0
        branches: list[tuple[float, float, bool]] = []
        for kind in AI_KINDS:
            rate = demand.rps.get(kind, 0.0)
            if rate <= 0.0:
                continue
            hit_share = hit * (1.0 if CACHEABLE_UNIFORM else CACHEABLE_FRACTION[kind])
            if state.tier == "none":
                miss_ms = NO_TIER_LATENCY_MS
            else:
                miss_ms = tier_base_latency_ms(kind, state.tier) * inflate
            weighted_ms += rate * (hit_share * CACHE_HIT_LATENCY_MS + (1.0 - hit_share) * miss_ms)
            miss_rps += rate * (1.0 - hit_share)
            if MIXTURE_P95:
                branches.append((rate * hit_share, CACHE_HIT_LATENCY_MS, True))
                branches.append((rate * (1.0 - hit_share), miss_ms, False))
        if MIXTURE_P95:
            # True percentile of the hit/miss mixture: a cache hit only
            # moves p95 once hits cross the quantile — the mean-based form
            # credits the cache with tail improvements a percentile denies.
            ai_p95 = _mixture_p95_ms(branches, tail)
        else:
            ai_p95 = weighted_ms / ai_rps * tail

    if extra_ai_latency_ms and ai_p95 > 0.0:
        ai_p95 += extra_ai_latency_ms

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

    # Energy on the same footing as cost: replicas and memory draw power for
    # the interval, and every request that *misses* the cache pays the tier's
    # inference energy — the identical miss_rps the tier bill uses, so a cache
    # hit avoids the joules exactly as it avoids the dollars.
    energy_kwh = state.replicas * REPLICA_POWER_W / 1000.0 * interval_hr
    energy_kwh += (state.cache_mb / 1024.0) * MEM_POWER_W_PER_GB / 1000.0 * interval_hr
    energy_kwh += miss_rps * CONTROL_INTERVAL_S * TIER_ENERGY_WH_PER_REQ[state.tier] / 1000.0

    return StepMetrics(
        cost_usd=cost_infra + cost_tier,
        violation=violation,
        excess=excess,
        crud_p95_ms=crud_p95,
        ai_p95_ms=ai_p95,
        cache_hit_rate=hit,
        cost_infra_usd=cost_infra,
        cost_tier_usd=cost_tier,
        energy_kwh=energy_kwh,
        carbon_g=energy_kwh * CARBON_INTENSITY_G_PER_KWH,
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
