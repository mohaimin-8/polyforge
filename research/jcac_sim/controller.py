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
    """Per-tenant demand forecast over the horizon.

    Four methods, ordered by sophistication (W36+ forecast ablation):
    - `persistence`: tomorrow equals today — collapses the horizon into a
      single step; the floor every other method must beat.
    - `trend` (default, the W30 original): linear trend over the last 3
      observations. Cheap, reactive, blind to periodicity — and, per the
      W36+ forecast ablation, prone to *overreacting* to single-bucket
      noise. Kept as the default only so the committed 1,800-run headline
      stays reproducible; `holt` is the evidence-based recommendation.
    - `holt`: damped double exponential smoothing (alpha .5, beta .3,
      phi .9) — an adaptive level+trend that shrugs off single-bucket
      noise instead of chasing it.
    - `seasonal`: online period detection (best autocorrelation lag over
      the recent history) + seasonal-naive forecast, falling back to
      `trend` until a period with correlation >= 0.4 emerges. Bursty
      square-wave tenants are periodic; this is the method that can see
      the next burst *before* it lands.
    - `seasonal_mr` (Wave 5): multi-resolution seasonal. The fine window
      above holds ~16 minutes at the 10 s control interval, so the plain
      `seasonal` forecaster is physically blind to the daily cycles the
      trace studies proved matter (FORECAST_TRACE_REAL, FORECAST_AZURE,
      PSEUDO_TENANT). This method additionally aggregates every
      COARSE_AGG observations into a coarse bucket (10 min at 10 s),
      keeps COARSE_KEEP of them (3 days), detects periodicity at both
      resolutions, and forecasts from whichever is stronger — the layer
      that lets the *deployed* planner exploit measured daily
      periodicity, not only the offline studies.

    All methods are per-request-kind and clamp at zero.
    """

    history: list = field(default_factory=list)  # recent Demand objects
    window: int = 3
    method: str = "trend"
    # seasonal_mr state: coarse per-kind means and the forming bucket.
    coarse: list = field(default_factory=list)  # list[dict kind -> mean rps]
    _accum: dict = field(default_factory=dict)  # kind -> running sum
    _accum_n: int = 0

    _KEEP = {"persistence": 1, "trend": 3, "holt": 48, "seasonal": 96,
             "seasonal_mr": 96}
    COARSE_AGG = 60  # observations per coarse bucket (10 min at 10 s)
    COARSE_KEEP = 432  # coarse buckets kept (3 days at 10 min)

    def observe(self, demand: Demand) -> None:
        self.history.append(demand)
        keep = max(self._KEEP.get(self.method, 3), self.window)
        if len(self.history) > keep:
            self.history.pop(0)
        if self.method == "seasonal_mr":
            for kind, rate in demand.rps.items():
                self._accum[kind] = self._accum.get(kind, 0.0) + rate
            self._accum_n += 1
            if self._accum_n >= self.COARSE_AGG:
                self.coarse.append(
                    {k: v / self._accum_n for k, v in self._accum.items()})
                self._accum, self._accum_n = {}, 0
                if len(self.coarse) > self.COARSE_KEEP:
                    self.coarse.pop(0)

    def horizon(self) -> list[Demand]:
        if not self.history:
            return [Demand()] * HORIZON_STEPS
        last = self.history[-1]
        if self.method == "persistence" or len(self.history) < 2:
            return [last] * HORIZON_STEPS

        kinds = sorted(set().union(*(d.rps.keys() for d in self.history)))
        series = {k: [d.rps.get(k, 0.0) for d in self.history] for k in kinds}
        if self.method == "holt":
            per_kind = {k: self._holt(series[k]) for k in kinds}
        elif self.method == "seasonal":
            per_kind = {k: self._seasonal(series[k]) for k in kinds}
        elif self.method == "seasonal_mr":
            per_kind = {k: self._seasonal_mr(series[k], k) for k in kinds}
        else:
            per_kind = {k: self._trend(series[k]) for k in kinds}

        return [
            Demand(
                rps={k: max(0.0, per_kind[k][step]) for k in kinds},
                crud_base_ms=last.crud_base_ms,
            )
            for step in range(HORIZON_STEPS)
        ]

    def _trend(self, y: list[float]) -> list[float]:
        tail = y[-3:]
        span = max(1, len(tail) - 1)
        slope = (tail[-1] - tail[0]) / span
        return [tail[-1] + slope * k for k in range(1, HORIZON_STEPS + 1)]

    def _holt(self, y: list[float], alpha=0.5, beta=0.3, phi=0.9) -> list[float]:
        level, trend = y[0], 0.0
        for value in y[1:]:
            prev_level = level
            level = alpha * value + (1 - alpha) * (level + phi * trend)
            trend = beta * (level - prev_level) + (1 - beta) * phi * trend
        out, damp = [], 0.0
        for k in range(1, HORIZON_STEPS + 1):
            damp += phi**k
            out.append(level + damp * trend)
        return out

    @staticmethod
    def _best_period(y: list[float], hi: int) -> tuple[int, float]:
        """Best autocorrelation lag in [8, hi] on the detrended series.

        Detrend first: raw autocorrelation mistakes any monotone ramp
        for a season (deviations from the mean stay same-signed for
        long stretches). A least-squares line removed, only genuine
        periodicity survives.
        """
        n = len(y)
        xbar = (n - 1) / 2.0
        ybar = sum(y) / n
        sxx = sum((i - xbar) ** 2 for i in range(n)) or 1.0
        slope = sum((i - xbar) * (y[i] - ybar) for i in range(n)) / sxx
        resid = [y[i] - (ybar + slope * (i - xbar)) for i in range(n)]

        best_lag, best_corr = 0, 0.0
        var = sum(v * v for v in resid) or 1.0
        for lag in range(8, hi + 1):
            cov = sum(resid[i] * resid[i - lag] for i in range(lag, n))
            corr = cov / var
            if corr > best_corr:
                best_lag, best_corr = lag, corr
        return best_lag, best_corr

    def _seasonal(self, y: list[float]) -> list[float]:
        n = len(y)
        best_lag, best_corr = self._best_period(y, min(48, n // 2))
        if best_corr < 0.4:  # no credible period yet: behave like trend
            return self._trend(y)
        return [y[n - best_lag + ((k - 1) % best_lag)] for k in range(1, HORIZON_STEPS + 1)]

    def _seasonal_mr(self, y: list[float], kind: str) -> list[float]:
        """Multi-resolution seasonal: prefer the fine-window season when
        one exists (it can phase-align inside the horizon); otherwise use
        the coarse (10-min-bucket) season, which is the only place a
        daily cycle is visible from a 16-minute fine window. The horizon
        (60 s) sits inside one coarse bucket, so the coarse forecast is
        the seasonal-naive value one period back — the level the cycle
        says is coming — held flat across the horizon."""
        n = len(y)
        fine_lag, fine_corr = self._best_period(y, min(48, n // 2))
        if fine_corr >= 0.4:
            return [y[n - fine_lag + ((k - 1) % fine_lag)]
                    for k in range(1, HORIZON_STEPS + 1)]
        series = [c.get(kind, 0.0) for c in self.coarse]
        m = len(series)
        if m >= 16:  # need at least two candidate periods of coarse history
            coarse_lag, coarse_corr = self._best_period(series, m // 2)
            if coarse_corr >= 0.4:
                nxt = series[m - coarse_lag]  # one period back from the forming bucket
                return [nxt] * HORIZON_STEPS
        return self._trend(y)


@dataclass
class PlanEntry:
    state: TenantState
    projected_cost_usd: float
    projected_violation: float


class JCACController:
    """Coordinate-descent MPC over all tenants against a shared objective.

    `forecast_method` selects the demand forecaster (see Forecast).
    `adaptive_capacity` turns on self-calibration: the controller compares
    each step's realized violation to what it projected and learns a
    per-tenant capacity correction — when the world serves less than the
    model promised (replica startup lag, cache warm-up, interference), the
    planner quietly plans as if capacity were smaller until reality and
    projection agree again. The correction is bounded to [0.5, 1.0]:
    self-calibration may make the model humbler, never optimistic.
    """

    CAP_LEARN = 0.15  # multiplicative step when projection was optimistic
    CAP_RECOVER = 0.02  # additive drift back toward trusting the model
    CAP_TOLERANCE = 0.02  # |realized - projected| below this is agreement

    def __init__(
        self,
        configs: dict[str, TenantConfig],
        weights: Weights | None = None,
        limits: ClusterLimits | None = None,
        forecast_method: str = "trend",
        adaptive_capacity: bool = False,
        anchor_moves: bool = False,
    ):
        self.configs = configs
        self.weights = weights or Weights()
        self.limits = limits or ClusterLimits()
        self.forecasts = {tid: Forecast(method=forecast_method) for tid in configs}
        self._interference: dict[str, float] = {}
        self.adaptive_capacity = adaptive_capacity
        # PREREG_MOVE_CLAMP (Wave 5): the coordination-gap audit caught the
        # second coordinate-descent sweep re-anchoring the move clamps at
        # its own sweep-1 choice, so one control interval could move
        # replicas ±4 and cache two levels while every baseline is clamped
        # to ±2 / one level. `anchor_moves=True` anchors the candidate
        # lattice at the interval-start state; the default preserves the
        # published behavior bit-for-bit (committed campaigns replay).
        self.anchor_moves = anchor_moves
        self.capacity_scale = {tid: 1.0 for tid in configs}
        self._projected: dict[str, float] = {}

    def observe_feedback(self, realized_violation: dict[str, float]) -> None:
        """Self-calibration hook the replay engine calls with what each
        tenant actually experienced last step. No-op unless adaptive."""
        if not self.adaptive_capacity:
            return
        for tid, realized in realized_violation.items():
            projected = self._projected.get(tid)
            if projected is None:
                continue
            scale = self.capacity_scale[tid]
            if realized > projected + self.CAP_TOLERANCE:
                scale = max(0.5, scale * (1.0 - self.CAP_LEARN))
            else:
                # The model kept its promise: trust drifts back. (Recovery
                # on agreement, not only on out-performance — a projection
                # of zero can be met but never beaten.)
                scale = min(1.0, scale + self.CAP_RECOVER)
            self.capacity_scale[tid] = scale

    def _planning_demand(self, tid: str, demand: Demand) -> Demand:
        """Capacity correction applied as demand inflation: planning for
        rps/scale on nominal capacity equals planning for rps on
        scale-degraded capacity."""
        scale = self.capacity_scale.get(tid, 1.0)
        if scale >= 1.0:
            return demand
        return Demand(
            rps={k: v / scale for k, v in demand.rps.items()},
            crud_base_ms=demand.crud_base_ms,
        )

    def plan(
        self,
        states: dict[str, TenantState],
        demands: dict[str, Demand],
        interference: dict[str, float] | None = None,
    ) -> dict[str, PlanEntry]:
        """One control cycle: observe demand, optimize, return the first
        action of the best move-blocked plan for every tenant.

        `interference` carries the W32 noisy-neighbor detector's per-tenant
        score (0 = clean). A flagged tenant pays a fairness surcharge on
        resource expansion, so the planner throttles it rather than feeding
        the interference."""
        self._interference = interference or {}
        for tid, demand in demands.items():
            self.forecasts[tid].observe(demand)
        horizons = {tid: self.forecasts[tid].horizon() for tid in self.configs}

        chosen = dict(states)
        # Two sweeps of coordinate descent: tenant order is fixed, each
        # tenant optimizes against the others' current choices; the second
        # sweep lets early tenants react to late ones. With anchor_moves,
        # both sweeps draw candidates from the interval-start lattice so
        # coordination cannot compound the per-interval actuation clamps.
        origin = dict(states) if self.anchor_moves else None
        for _ in range(2):
            for tid in sorted(self.configs):
                chosen[tid] = self._best_for_tenant(tid, chosen, horizons, origin)

        plans = {}
        for tid, state in chosen.items():
            cost, violation, _ = self._project(tid, state, horizons[tid])
            self._projected[tid] = violation  # feedback baseline (adaptive)
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
            m = evaluate_step(self.configs[tid], state, self._planning_demand(tid, demand))
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
        origin: dict[str, TenantState] | None = None,
    ) -> TenantState:
        config = self.configs[tid]
        current = chosen[tid]
        # Candidate moves anchor at `base`: the interval-start state when
        # anchor_moves is on (clamps are per interval), the sweep's current
        # choice otherwise (published behavior, kept bit-identical).
        base = current if origin is None else origin[tid]
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
            for cache_mb in neighbor_cache_levels(base.cache_mb):
                for tier in TIERS:
                    candidate = apply_action(config, base, delta, cache_mb, tier)
                    if others_cache + candidate.cache_mb > self.limits.cache_mb:
                        continue
                    if others_replicas + candidate.replicas > self.limits.replicas:
                        continue
                    cost, viol, obj = self._project(tid, candidate, horizons[tid])
                    if cost > budget_per_step:
                        continue
                    switches = (
                        (candidate.replicas != base.replicas)
                        + (candidate.cache_mb != base.cache_mb)
                        + (candidate.tier != base.tier)
                    )
                    fairness = 1.0 - jain_index(other_satisfaction + [1.0 - viol])
                    # W32: a tenant flagged noisy pays for expansion in
                    # proportion to its interference score — the planner
                    # shrinks it back toward its floor instead of scaling
                    # the interference up.
                    noise = self._interference.get(tid, 0.0)
                    resource_share = 0.0
                    if noise > 0.0:
                        resource_share = 0.5 * (
                            candidate.replicas / max(1, config.replica_max)
                            + candidate.cache_mb / 1024.0
                        )
                    score = (
                        self.weights.alpha * (other_cost + cost) / COST_SCALE_USD
                        + self.weights.beta * (other_obj + obj)
                        + self.weights.gamma * (0.5 + config.fairness_weight) * fairness
                        + self.weights.gamma * noise * resource_share
                        + SWITCH_PENALTY * switches
                    )
                    if score < best_score:
                        best_score, best_state = score, candidate
        # An entirely infeasible lattice (tight budget + tight cluster)
        # falls back to shedding cost: floor replicas, no cache, no model.
        if best_score == float("inf"):
            return TenantState(replicas=config.replica_min, cache_mb=0, tier="none")
        return best_state
