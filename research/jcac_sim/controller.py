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
    # Risk-aware planning state (PREREG_RISK_MPC): the controller's own
    # one-step forecast errors, kept per request kind. `_last_pred` is the
    # prediction the previous horizon() made for the bucket that observe() is
    # now being handed, so residual = actual - predicted. Recorded always
    # (cheap, side-effect only); *used* only when a risk quantile is asked
    # for, so the default planning path stays bit-identical.
    residuals: dict = field(default_factory=dict)  # kind -> list[float]
    _last_pred: dict = field(default_factory=dict)  # kind -> float

    _KEEP = {"persistence": 1, "trend": 3, "holt": 48, "seasonal": 96,
             "seasonal_mr": 96}
    COARSE_AGG = 60  # observations per coarse bucket (10 min at 10 s)
    COARSE_KEEP = 432  # coarse buckets kept (3 days at 10 min)
    RESIDUAL_KEEP = 48  # rolling window of forecast errors (8 min at 10 s)
    RESIDUAL_MIN = 8  # below this the quantile is noise: no inflation

    def snapshot(self) -> dict:
        """JSON-serializable forecast state for planner failover (W31).

        Captures everything horizon() reads — the fine window, the coarse
        buckets, and the forming bucket — so a replacement replica resumes
        the demand history instead of cold-starting (a seasonal method needs
        minutes of observations to re-warm; seasonal_mr's coarse buckets are
        worth days)."""
        return {
            "method": self.method,
            "window": self.window,
            "history": [{"rps": dict(d.rps), "crud_base_ms": d.crud_base_ms}
                        for d in self.history],
            "coarse": [dict(c) for c in self.coarse],
            "accum": dict(self._accum),
            "accum_n": self._accum_n,
            # Risk-aware state: a replacement replica that lost its residual
            # window would plan at the point forecast until it re-warms.
            "residuals": {k: list(v) for k, v in self.residuals.items()},
            "last_pred": dict(self._last_pred),
        }

    @classmethod
    def from_snapshot(cls, data: dict) -> "Forecast":
        f = cls(method=str(data.get("method", "trend")),
                window=int(data.get("window", 3)))
        f.history = [
            Demand(rps={str(k): float(v) for k, v in entry.get("rps", {}).items()},
                   crud_base_ms=float(entry.get("crud_base_ms", 50.0)))
            for entry in data.get("history", [])
        ]
        f.coarse = [{str(k): float(v) for k, v in bucket.items()}
                    for bucket in data.get("coarse", [])]
        f._accum = {str(k): float(v) for k, v in data.get("accum", {}).items()}
        f._accum_n = int(data.get("accum_n", 0))
        f.residuals = {str(k): [float(x) for x in v]
                       for k, v in (data.get("residuals") or {}).items()}
        f._last_pred = {str(k): float(v)
                        for k, v in (data.get("last_pred") or {}).items()}
        return f

    def observe(self, demand: Demand) -> None:
        # Score the previous horizon's one-step prediction against what
        # actually arrived. Side-effect only: never changes what horizon()
        # returns unless a risk quantile is requested.
        if self._last_pred:
            for kind, pred in self._last_pred.items():
                r = self.residuals.setdefault(kind, [])
                r.append(demand.rps.get(kind, 0.0) - pred)
                if len(r) > self.RESIDUAL_KEEP:
                    r.pop(0)
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

    def _residual_quantile(self, kind: str, q: float) -> float:
        """Empirical q-quantile of this kind's forecast errors, floored at 0.

        Deliberately distribution-free — no normality assumption, just the
        controller's own realized errors. Floored at zero because the risk
        knob may only *add* headroom: planning below the point forecast
        because errors happened to skew negative would be reckless.
        """
        r = self.residuals.get(kind)
        if not r or len(r) < self.RESIDUAL_MIN:
            return 0.0
        s = sorted(r)
        idx = min(len(s) - 1, int(q * (len(s) - 1) + 0.5))
        return max(0.0, s[idx])

    def horizon(self, risk_quantile: float | None = None) -> list[Demand]:
        """Per-step demand forecast. With `risk_quantile` in (0.5, 1), each
        kind is inflated by the empirical quantile of its own recent forecast
        errors — plan for the demand you'd exceed only (1-q) of the time
        instead of for the mean (PREREG_RISK_MPC). None/0 = the published
        point-forecast path, arithmetically unchanged."""
        if not self.history:
            return [Demand()] * HORIZON_STEPS
        last = self.history[-1]
        if self.method == "persistence" or len(self.history) < 2:
            self._last_pred = dict(last.rps)
            if not risk_quantile:
                return [last] * HORIZON_STEPS
            pad = {k: self._residual_quantile(k, risk_quantile) for k in last.rps}
            return [
                Demand(
                    rps={k: max(0.0, v + pad.get(k, 0.0)) for k, v in last.rps.items()},
                    crud_base_ms=last.crud_base_ms,
                )
            ] * HORIZON_STEPS

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

        # Residuals score the *base* forecaster, so record the un-inflated
        # one-step prediction; otherwise the padding would feed itself.
        self._last_pred = {k: per_kind[k][0] for k in kinds}
        pad = ({k: self._residual_quantile(k, risk_quantile) for k in kinds}
               if risk_quantile else {})

        return [
            Demand(
                rps={k: max(0.0, per_kind[k][step] + pad.get(k, 0.0)) for k in kinds},
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
        degrade_gracefully: bool = False,
        risk_quantile: float | None = None,
        risk_cost_at_point: bool = False,
    ):
        self.configs = configs
        # PREREG_RISK_BUDGET: the one changed factor the RESULTS_RISK null
        # identified. Inflating demand for the SLO term also inflated
        # *projected spend* (tier cost scales with demand), so candidates
        # failed the per-tenant budget filter and the controller shed to
        # tier="none" — cheap, and a total SLO miss. The correction: size
        # capacity against the risk-inflated demand, but project cost and
        # check the budget against the POINT forecast, because you are billed
        # for the demand that *arrives*, not the demand you provisioned
        # against. False = the published (null) behavior, kept so
        # matrix_risk replays bit-identically.
        self.risk_cost_at_point = risk_cost_at_point
        # PREREG_RISK_MPC: plan against a demand *quantile* instead of the
        # point forecast. The published controller optimizes cost at the
        # expected demand, so demand lands above plan roughly half the time —
        # the mechanism behind the disclosed violation trade. `risk_quantile`
        # inflates each kind by the empirical quantile of the controller's own
        # forecast errors, making SLO-tolerance an explicit, tunable knob
        # rather than an implicit consequence of planning at the mean.
        # Orthogonal to `adaptive_capacity`, which corrects model optimism
        # (realized-vs-projected capacity), not demand variance.
        # None = the published behavior, bit-identical.
        self.risk_quantile = risk_quantile
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
        # PREREG_DEGRADE (gap 4.4): when the lattice is entirely infeasible
        # (budget + cluster caps admit nothing), the published fallback sheds
        # to tier="none" — a designed AI outage. `degrade_gracefully=True`
        # instead serves on the cheapest AFFORDABLE tier at the replica floor.
        # MEASURED no-op at realistic tier costs (DEGRADE_PROBE.md): the
        # fallback binds only when even the cheapest tier's per-request price
        # exceeds budget, and the cache_mb=0 floor maximises misses, so it
        # cannot fit a budget the cached candidates could not — shed-to-none
        # is the correct budget-respecting response. Retained default-off as
        # a documented, budget-safe option (committed campaigns replay
        # bit-identically); DG-H2 (never serves outside budget) holds.
        self.degrade_gracefully = degrade_gracefully
        self.capacity_scale = {tid: 1.0 for tid in configs}
        self._projected: dict[str, float] = {}

    def snapshot(self) -> dict:
        """Serializable planner state for failover (W31): per-tenant forecast
        history and learned capacity corrections. Weights/limits are the
        operator's to supply on the next request, so they are deliberately
        not part of the snapshot — only the state that cannot be
        reconstructed from a single request."""
        return {
            "forecasts": {tid: f.snapshot() for tid, f in self.forecasts.items()},
            "capacity_scale": dict(self.capacity_scale),
        }

    def restore(self, data: dict) -> None:
        """Load a snapshot into the tenants this controller already knows.
        Tenants absent from the current config are ignored (they may have
        departed); tenants present but absent from the snapshot keep their
        fresh state (they are new)."""
        for tid, fsnap in (data.get("forecasts") or {}).items():
            if tid in self.forecasts:
                self.forecasts[tid] = Forecast.from_snapshot(fsnap)
        for tid, scale in (data.get("capacity_scale") or {}).items():
            if tid in self.capacity_scale:
                self.capacity_scale[tid] = float(scale)

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
        horizons = {tid: self.forecasts[tid].horizon(self.risk_quantile)
                    for tid in self.configs}
        # Split projection: SLO is judged on the risk-inflated demand, money on
        # the point forecast. None keeps the single-horizon path exactly.
        cost_horizons = None
        if self.risk_quantile and self.risk_cost_at_point:
            cost_horizons = {tid: self.forecasts[tid].horizon()
                             for tid in self.configs}

        chosen = dict(states)
        # Two sweeps of coordinate descent: tenant order is fixed, each
        # tenant optimizes against the others' current choices; the second
        # sweep lets early tenants react to late ones. With anchor_moves,
        # both sweeps draw candidates from the interval-start lattice so
        # coordination cannot compound the per-interval actuation clamps.
        origin = dict(states) if self.anchor_moves else None
        for _ in range(2):
            for tid in sorted(self.configs):
                chosen[tid] = self._best_for_tenant(tid, chosen, horizons, origin,
                                                    cost_horizons)

        plans = {}
        for tid, state in chosen.items():
            cost, violation, _ = self._project(tid, state, horizons[tid])
            if cost_horizons is not None:
                # Report the spend you will actually be billed for.
                cost = self._project(tid, state, cost_horizons[tid])[0]
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
        cost_horizons: dict[str, list[Demand]] | None = None,
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
            if cost_horizons is not None:
                cost = self._project(oid, ostate, cost_horizons[oid])[0]
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
                    if cost_horizons is not None:
                        # Budget is a *money* guardrail: test it against the
                        # spend the arriving demand will bill, not against the
                        # headroom we provisioned for.
                        cost = self._project(tid, candidate, cost_horizons[tid])[0]
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
            shed = TenantState(replicas=config.replica_min, cache_mb=0, tier="none")
            if not self.degrade_gracefully:
                return shed
            # Graceful degradation (PREREG_DEGRADE): prefer serving on the
            # cheapest affordable tier at the replica floor over a designed
            # outage. Never violates budget or the cluster replica cap.
            if others_replicas + config.replica_min <= self.limits.replicas:
                for tier in ("small", "mid", "large"):
                    cand = TenantState(replicas=config.replica_min, cache_mb=0, tier=tier)
                    cost, _, _ = self._project(tid, cand, horizons[tid])
                    if cost <= budget_per_step:
                        return cand
            return shed
        return best_state
