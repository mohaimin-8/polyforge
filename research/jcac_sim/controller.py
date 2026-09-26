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

import contextlib
import math
import random
from dataclasses import dataclass, field, replace

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
# Normaliser for the optional carbon term (M4 / T18), chosen so a typical
# tenant-step's grams land in the same 0.1-1 band COST_SCALE_USD puts dollars
# in — that way `carbon_weight` is on a comparable footing to `alpha` instead
# of needing a hidden order-of-magnitude correction.
CARBON_SCALE_G = 10.0

SWITCH_PENALTY = 0.05  # objective units per changed knob (hysteresis)
# Half of one replica-step in objective units: the hysteresis a corrected
# controller charges per changed knob (see JCACController.__init__).
SWITCH_PENALTY_HALF_REPLICA = 0.5 * (
    model.REPLICA_COST_USD_HR * model.CONTROL_INTERVAL_S / 3600.0) / COST_SCALE_USD
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


@dataclass(frozen=True)
class RealizedStep:
    """What one tenant's plant served last interval: the state that served,
    the demand that arrived, and the realized p95 per traffic family. Fed to
    JCACController.observe_realized for headroom calibration."""

    state: TenantState
    demand: Demand
    crud_p95_ms: float
    ai_p95_ms: float


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

    MAX_SWEEPS = 50   # convergent solver's safety cap; `last_converged` reports hitting it
    CAP_LEARN = 0.15  # multiplicative step when projection was optimistic
    CAP_RECOVER = 0.02  # additive drift back toward trusting the model
    CAP_TOLERANCE = 0.02  # |realized - projected| below this is agreement
    HEADROOM_FLOOR = 0.5   # never believe a replica delivers less than half
    HEADROOM_LEARN_DOWN = 0.5  # toward a smaller scale: half the gap per step
    HEADROOM_LEARN_UP = 0.1    # toward a larger scale: a tenth of the gap
    HEADROOM_BISECT = 24       # bisection steps; 2^-24 of the bracket
    AI_OFFSET_LEARN_UP = 0.5   # plant slower than the tier table: half the gap
    AI_OFFSET_LEARN_DOWN = 0.1 # plant faster: a tenth of the gap
    AI_OFFSET_FLOOR_MS = -500.0  # never believe a tier is >0.5 s faster than its table
    AI_OFFSET_CAP_MS = 30000.0

    def __init__(
        self,
        configs: dict[str, TenantConfig],
        weights: Weights | None = None,
        limits: ClusterLimits | None = None,
        forecast_method: str = "trend",
        adaptive_capacity: bool = False,
        anchor_moves: bool = False,
        degrade_gracefully: bool = False,
        clamped_fallback: bool = False,
        risk_quantile: float | None = None,
        risk_cost_at_point: bool = False,
        carbon_weight: float = 0.0,
        tenant_order_seed: int | None = None,
        enforce_budget: bool = True,
        belief_scale: dict | None = None,
        headroom_calibration: bool = False,
        headroom_cap: float = 4.0,
        switch_penalty: float | None = None,
        replica_dwell_steps: int = 0,
        converge_sweeps: bool = False,
        belief_form: str | None = None,
    ):
        self.configs = configs
        # Audit 2026-09-26 (simulator CRITICAL): `_project` calls the very
        # evaluate_step the engine scores with, so the planner sees 100% of
        # the plant's structural form -- a model-match advantage no reactive
        # baseline has. belief_form="published" makes the planner believe
        # the published FORMS while the plant runs whatever form the
        # experiment set (model.use_form around every projection). None =
        # the planner sees the plant: every registered arm, bit-identical.
        if belief_form not in (None, "published"):
            raise ValueError(f"belief_form must be None or 'published', not {belief_form!r}")
        self._belief_form = model.published_form() if belief_form == "published" else None
        # B1' follow-up (session 48): the corrected controller oscillated its
        # replica count on the live plane -- 8 to 24 replicas in tier_mixed
        # with single-step jumps of 9-11, changes at 21-24 of 30 steps
        # (CHURN_WAVE4_CALIBRATED.md). The half-replica switching penalty
        # holds no plan still once the plant estimate moves every step. A
        # dwell time is the damping a control engineer reaches for first: a
        # tenant that changed its replica count may not REVERSE that change
        # for `replica_dwell_steps` control cycles (same-direction moves and
        # the other knobs stay free). 0 = off = every registered arm,
        # bit-identical. Scoring it needs its own pre-registration.
        self.replica_dwell_steps = max(0, int(replica_dwell_steps))
        self._cycle = 0
        self._replica_move: dict[str, tuple[int, int]] = {}  # tid -> (cycle, direction)
        # B1 follow-up (session 48), the second defect: SWITCH_PENALTY is a
        # flat 0.05 objective units per changed knob, while one replica costs
        # 0.048 $/h = 0.0133 units per 10 s step. Dropping the maximum two
        # replicas saves 0.027, less than the penalty, so the published
        # controller scales replicas UP on a projected violation and never
        # back DOWN on cost alone -- a ratchet, visible as `4455555...` in the
        # sim trace and as replicas nobody needed on the live bill. Hysteresis
        # exists to damp chatter between near-equal plans, not to block
        # convergence, so the corrected arm sets the penalty to HALF the cost
        # of the smallest economic move (SWITCH_PENALTY_HALF_REPLICA): a
        # one-replica scale-down always clears it, a coin-flip never does.
        # (A first attempt charged only move REVERSALS; on the live plant that
        # let a wrong shrink of the cache go free and penalised the correcting
        # grow -- a one-way trap. Rejected on measurement.) None = published.
        self.switch_penalty = SWITCH_PENALTY if switch_penalty is None else float(switch_penalty)
        # B1 follow-up (session 48): learn the plant's capacity from realized
        # latency HEADROOM, not only from realized violation. The published
        # `adaptive_capacity` can make the model humbler (capacity_scale <= 1)
        # and never bolder, and it observes only the plan it executed: at the
        # replicas the model demanded it projects ~0 violation, reality gives
        # 0, and "agreement" teaches nothing about the replicas it did not
        # try. On the live plane the model predicted 3,500 ms CRUD p95 at two
        # replicas where the cluster served 1 ms, so the joint controller
        # bought replicas that bought nothing and lost WL-H1 on cost to its own
        # tier-only ablation, whose replica knob was pinned.
        #
        # With `headroom_calibration` on, `observe_realized` receives each
        # tenant's executed state, arrived demand and realized p95s, solves
        # for the load-scale at which the model reproduces the realized p95 at
        # that very operating point (the model is monotone in load, so one
        # bisection), and moves `capacity_scale` toward it -- fast downward,
        # slow upward, bounded to [HEADROOM_FLOOR, headroom_cap]. The cap is
        # the safety margin: however much headroom is observed, the model
        # keeps predicting a violation for a demand jump beyond cap x plan.
        # Composes with `belief_scale["replica_capacity"]` by multiplication
        # (see _planning_demand), so a belief of b is corrected to b * s = 1.
        # Default off: the published campaigns replay bit-identically (R4).
        self.headroom_calibration = headroom_calibration
        self.headroom_cap = float(headroom_cap)
        # PREREG_MODEL_MISMATCH (WP6): let the controller believe *wrong*
        # constants while the world keeps the published ones.
        #
        # `_project` calls the same `evaluate_step` the engine scores with, so
        # with realism flags off the controller's model is bit-identical to
        # the plant and every sensitivity campaign moves world and beliefs
        # together (`model.set_economy` says so in as many words). For an MPC
        # paper "what happens when the model is wrong?" is the first
        # structural question a reviewer asks, and no experiment answered it.
        #
        # Keys, each a multiplier on what the controller BELIEVES:
        #   replica_capacity  0.8 = "a replica delivers 80% of true capacity"
        #   tier_cost         1.5 = "inference costs 50% more than it does"
        #   cache_half        0.5 = "the cache saturates twice as fast"
        #
        # None (the default) takes the published code path verbatim -- not an
        # equivalent one -- so every committed campaign replays bit-for-bit
        # (R4). The scales are applied ONLY inside `_project`; the scoring
        # path never sees them, which is the whole point of the experiment.
        self.belief_scale = dict(belief_scale) if belief_scale else None
        if self.belief_scale:
            unknown = set(self.belief_scale) - {"replica_capacity", "tier_cost",
                                                "cache_half"}
            if unknown:
                raise ValueError(f"unknown belief_scale keys: {sorted(unknown)}")
            if any(v <= 0.0 for v in self.belief_scale.values()):
                raise ValueError("belief_scale values must be positive")
        # M4 / T18: price the plan's carbon alongside its dollars. 0.0 (the
        # default) leaves the objective untouched — the term is guarded, not
        # multiplied by zero, so the published campaigns replay bit-for-bit.
        # Scope it honestly (see model.py's energy block for the measurement):
        # under a CONSTANT grid intensity carbon is near-collinear with cost on
        # these constants, so this knob buys almost nothing there. It becomes a
        # real dimension when the grid's intensity varies over time, because
        # price does not follow the grid — the same plan is clean at 03:00 and
        # dirty at 18:00 for identical money. Expect a sharp response: low
        # weights move nothing, high weights shed AI traffic outright.
        self.carbon_weight = carbon_weight
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
        # Audit 2026-09-26: the infeasible-lattice shed jumps to (replica_min,
        # 0 MB, none) in one step, outside the move clamps the lattice obeys
        # and through any pinned knob (a tenant at 9 replicas became 1).
        # `clamped_fallback=True` sheds inside those guardrails instead
        # (_clamped_shed). Default False = the published shed (R4).
        self.clamped_fallback = clamped_fallback
        # PREREG_ORDER_PERMUTATION: the coordinate-descent sweep order is
        # first-come-first-served on the shared cluster caps, and the default
        # sorted() order is confounded with priority class because tenant ids
        # are assigned by slot. None = sorted(), the published behavior.
        self.tenant_order_seed = tenant_order_seed
        # See the filter site in `plan()` for why this exists. Default True =
        # published behaviour.
        self.enforce_budget = enforce_budget
        self._order_cache: list[str] | None = None
        self.capacity_scale = {tid: 1.0 for tid in configs}
        # Headroom calibration's second dial. Capacity (replicas) is learned
        # from the CRUD family only: on the live plane AI latency is served by
        # the gateway and the tier, not by replicas, and a first attempt that
        # folded the AI family into the capacity target read a slow tier as a
        # weak replica, halved the believed capacity and bought replicas and
        # mid-tier routing that cost 2.7x (measured on the mock probe, session
        # 48). The AI family instead calibrates an ADDITIVE per-tenant latency
        # offset at the executed tier -- what the model's tier table misses on
        # this plant -- applied through evaluate_step's extra_ai_latency_ms in
        # every projection. Fast upward (the plant is slower), slow downward,
        # never below AI_OFFSET_FLOOR_MS.
        self.ai_latency_offset_ms = {tid: 0.0 for tid in configs}
        self._projected: dict[str, float] = {}
        # WP5: within-cycle projection memo. `_project` is a pure function of
        # (tenant, state, horizon) for the duration of one `plan()` -- the
        # config, the capacity correction and both horizons are all fixed
        # before the sweeps start and none is mutated during them -- so
        # caching it cannot change a single result. It is cleared at the top
        # of every cycle, so no state crosses cycles and R4 holds by
        # construction rather than by luck. The waste it removes is the
        # O(N^2) wall the audit named: each tenant's `_best_for_tenant`
        # re-projects every *other* tenant at its unchanged state, so an
        # N-tenant sweep recomputes the same (tenant, state) projection up to
        # N-1 times.
        self._project_memo: dict = {}
        # Audit 2026-09-26 (THEORY_V2 Proposition 1): two FIXED sweeps do not
        # end at a block-coordinate minimum -- a tenant visited before the last
        # mover best-responded to that mover's OLD value. Measured on the
        # published unanchored solver: 23 of 1,830 control cycles left a
        # tenant a strictly better unilateral move. `converge_sweeps=True`
        # sweeps until a full sweep changes no tenant and keeps the incumbent
        # on ties, so each accepted move strictly lowers the shared potential
        # (the objective is an exact potential when fairness weights are
        # uniform, as in every campaign). A converged plan is then a best
        # response for every tenant by construction: in the last sweep each
        # tenant was re-optimised against others that did not move after it.
        # It requires anchored moves: unanchored, each extra sweep re-centres
        # the +/-2 clamp and convergence would compound the clamp defect
        # (PREREG_MOVE_CLAMP) without bound. False = the published two sweeps.
        if converge_sweeps and not anchor_moves:
            raise ValueError("converge_sweeps requires anchor_moves=True")
        self.converge_sweeps = converge_sweeps
        self.last_sweeps = 0
        self.last_converged: bool | None = None
        self._last_plan_ctx: tuple | None = None

    def snapshot(self) -> dict:
        """Serializable planner state for failover (W31): per-tenant forecast
        history and learned capacity corrections. Weights/limits are the
        operator's to supply on the next request, so they are deliberately
        not part of the snapshot — only the state that cannot be
        reconstructed from a single request."""
        return {
            "forecasts": {tid: f.snapshot() for tid, f in self.forecasts.items()},
            "capacity_scale": dict(self.capacity_scale),
            "ai_latency_offset_ms": dict(self.ai_latency_offset_ms),
            "cycle": self._cycle,
            "replica_move": {tid: list(mv) for tid, mv in self._replica_move.items()},
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
        for tid, off in (data.get("ai_latency_offset_ms") or {}).items():
            if tid in self.ai_latency_offset_ms:
                self.ai_latency_offset_ms[tid] = float(off)
        self._cycle = int(data.get("cycle", self._cycle) or 0)
        for tid, mv in (data.get("replica_move") or {}).items():
            if tid in self.configs and len(mv) == 2:
                self._replica_move[tid] = (int(mv[0]), int(mv[1]))

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

    def observe_realized(self, realized: dict[str, "RealizedStep"]) -> None:
        """Headroom calibration, projected under the believed form (see
        `belief_form`); the realized values come from the plant."""
        with self._believed():
            self._observe_realized(realized)

    def _observe_realized(self, realized: dict[str, "RealizedStep"]) -> None:
        """Headroom calibration hook: what each tenant's plant actually served
        last interval, at which state, under which demand. No-op unless
        `headroom_calibration`; see __init__ for the mechanism."""
        if not self.headroom_calibration:
            return
        for tid, obs in realized.items():
            if tid not in self.configs:
                continue
            target = self._headroom_target(tid, obs)
            if target is not None:
                scale = self.capacity_scale[tid]
                rate = self.HEADROOM_LEARN_DOWN if target < scale else self.HEADROOM_LEARN_UP
                scale = scale + rate * (target - scale)
                self.capacity_scale[tid] = min(self.headroom_cap, max(self.HEADROOM_FLOOR, scale))
            gap = self._ai_offset_target(tid, obs)
            if gap is not None:
                cur = self.ai_latency_offset_ms[tid]
                rate = self.AI_OFFSET_LEARN_UP if gap > cur else self.AI_OFFSET_LEARN_DOWN
                cur = cur + rate * (gap - cur)
                self.ai_latency_offset_ms[tid] = min(self.AI_OFFSET_CAP_MS, max(self.AI_OFFSET_FLOOR_MS, cur))

    def _ai_offset_target(self, tid: str, obs: "RealizedStep") -> float | None:
        """realized AI p95 minus the model's projection of the executed point
        (with the current capacity scale, without any offset): the additive
        correction that would have made the projection right. None without
        AI traffic or with a zero realized p95 (cache-served, unobserved)."""
        ai_rps = sum(obs.demand.rps.get(k, 0.0) for k in model.AI_KINDS)
        if ai_rps <= 0.0 or obs.ai_p95_ms <= 0.0:
            return None
        planned = self._planning_demand(tid, obs.demand)
        believed = self._believed_state(obs.state) if self.belief_scale else obs.state
        predicted = evaluate_step(self.configs[tid], believed, planned).ai_p95_ms
        return obs.ai_p95_ms - predicted

    def _headroom_target(self, tid: str, obs: "RealizedStep") -> float | None:
        """The capacity scale at which the controller's own projection of the
        executed point reproduces the realized CRUD p95. None when nothing
        was observed (no CRUD traffic, or a realized p95 of zero)."""
        config = self.configs[tid]
        belief = (self.belief_scale or {}).get("replica_capacity", 1.0)
        believed = self._believed_state(obs.state) if self.belief_scale else obs.state

        def predicted(scale: float):
            eff = scale * belief
            d = Demand(rps={k: v / eff for k, v in obs.demand.rps.items()},
                       crud_base_ms=obs.demand.crud_base_ms)
            return evaluate_step(config, believed, d)

        # CRUD only: replicas serve CRUD; AI latency is the tier's and is
        # calibrated separately (see _ai_offset_target).
        families = []
        crud_rps = sum(obs.demand.rps.get(k, 0.0) for k in model.CRUD_KINDS)
        if crud_rps > 0.0 and obs.crud_p95_ms > 0.0:
            families.append(("crud_p95_ms", obs.crud_p95_ms))
        if not families:
            return None

        targets = []
        for attr, realized_ms in families:
            lo, hi = self.HEADROOM_FLOOR, self.headroom_cap
            # predicted p95 is non-increasing in scale: bracket, then bisect
            if getattr(predicted(hi), attr) >= realized_ms:
                targets.append(hi)
                continue
            if getattr(predicted(lo), attr) <= realized_ms:
                targets.append(lo)
                continue
            for _ in range(self.HEADROOM_BISECT):
                mid = 0.5 * (lo + hi)
                if getattr(predicted(mid), attr) >= realized_ms:
                    lo = mid
                else:
                    hi = mid
            targets.append(lo)
        return min(targets)

    def _planning_demand(self, tid: str, demand: Demand) -> Demand:
        """Capacity correction applied as demand inflation: planning for
        rps/scale on nominal capacity equals planning for rps on
        scale-degraded capacity."""
        scale = self.capacity_scale.get(tid, 1.0)
        if self.belief_scale is None:
            # Published guard, unchanged: `capacity_scale` is a degradation
            # correction and is never above 1.0, so >= 1.0 means "nothing to
            # correct". A belief CAN exceed 1.0 (an over-optimistic
            # controller), which is why the belief branch tests != instead.
            # Under headroom calibration the scale may legitimately exceed
            # 1.0 (the plant out-performs the model) and is applied.
            if scale == 1.0 or (scale > 1.0 and not self.headroom_calibration):
                return demand
        else:
            # PREREG_MODEL_MISMATCH: a `replica_capacity` belief of b says
            # "a replica delivers b times the capacity it really does".
            # Planning for demand d against capacity b*C is identical to
            # planning for d/b against C, which is the transform this method
            # already performs — so the two compose by multiplication rather
            # than needing a second mechanism.
            scale *= self.belief_scale.get("replica_capacity", 1.0)
            if scale == 1.0:
                return demand
        return Demand(
            rps={k: v / scale for k, v in demand.rps.items()},
            crud_base_ms=demand.crud_base_ms,
        )

    def _believed(self):
        """The context every projection runs in: the believed structural
        form when `belief_form` is set, else the plant's own (a no-op)."""
        return model.use_form(self._belief_form) if self._belief_form else contextlib.nullcontext()

    def plan(self, states: dict[str, TenantState], demands: dict[str, Demand],
             interference: dict[str, float] | None = None) -> dict[str, PlanEntry]:
        """One control cycle (see `_plan_cycle`), under the believed form."""
        with self._believed():
            return self._plan_cycle(states, demands, interference)

    def _plan_cycle(
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
        self._cycle += 1
        # WP5: the memo is per cycle. Clearing here (not in __init__)
        # is what makes cross-cycle state impossible.
        self._project_memo.clear()
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
        sweeps, changed = 0, True
        while changed if self.converge_sweeps else sweeps < 2:
            changed = False
            for tid in self._sweep_order():
                new = self._best_for_tenant(tid, chosen, horizons, origin, cost_horizons)
                changed = changed or new != chosen[tid]
                chosen[tid] = new
            sweeps += 1
            if sweeps >= self.MAX_SWEEPS:
                break
        self.last_sweeps = sweeps
        self.last_converged = (not changed) if self.converge_sweeps else None
        self._last_plan_ctx = (dict(chosen), horizons, origin, cost_horizons)

        if self.replica_dwell_steps:
            for tid, state in chosen.items():
                delta = state.replicas - states[tid].replicas
                if delta:
                    self._replica_move[tid] = (self._cycle, 1 if delta > 0 else -1)

        plans = {}
        for tid, state in chosen.items():
            cost, violation, _ = self._project(tid, state, horizons[tid])
            if cost_horizons is not None:
                # Report the spend you will actually be billed for.
                cost = self._project(tid, state, cost_horizons[tid], 1)[0]
            self._projected[tid] = violation  # feedback baseline (adaptive)
            plans[tid] = PlanEntry(state=state, projected_cost_usd=cost, projected_violation=violation)
        return plans

    def _dwell_blocks(self, tid: str, delta: int) -> bool:
        """True when `delta` would reverse this tenant's last replica move
        inside the dwell window. Off (0) blocks nothing."""
        if not self.replica_dwell_steps:
            return False
        last = self._replica_move.get(tid)
        if last is None:
            return False
        cycle, direction = last
        return (delta > 0) != (direction > 0) and (self._cycle - cycle) < self.replica_dwell_steps

    def _project(self, tid: str, state: TenantState, horizon: list[Demand],
                 tag: int = 0) -> tuple[float, float, float]:
        """Horizon-mean (cost, bounded violation, objective violation).

        The objective term is log1p(excess): unbounded so deep overload
        still has a gradient, compressive so the controller doesn't
        sacrifice everything else to a single hopeless tenant-step.

        `tag` names *which* horizon was passed (0 = the risk-inflated one
        the SLO is judged on, 1 = the point forecast money is judged on).
        It exists only to key the within-cycle memo: the two horizons differ
        for the same tenant whenever `risk_cost_at_point` is on, so the
        tenant and state alone do not identify a projection. Passing an
        explicit tag rather than hashing the horizon keeps the key exact and
        the fast path free of list comparisons.
        """
        key = (tid, state, tag)
        hit = self._project_memo.get(key)
        if hit is not None:
            return hit
        cost = violation = obj = 0.0
        if self.belief_scale is None:
            # The published path, verbatim. Deliberately not folded into the
            # belief branch with neutral multipliers: `cost_usd` and
            # `cost_infra + cost_tier` are equal in real arithmetic but need
            # not be equal in floating point, and R4 is a byte-identity gate.
            # extra_ai_latency_ms is the learned tier-latency correction;
            # 0.0 (the default and the published path) leaves evaluate_step's
            # arithmetic untouched, so R4 holds.
            offset = self.ai_latency_offset_ms.get(tid, 0.0) if self.headroom_calibration else 0.0
            # The published call is made verbatim when there is no offset:
            # tests stub evaluate_step with the published signature, and
            # R4 is a byte-identity gate on the published path.
            extra = {"extra_ai_latency_ms": offset} if offset else {}
            for demand in horizon:
                m = evaluate_step(self.configs[tid], state,
                                  self._planning_demand(tid, demand), **extra)
                cost += m.cost_usd
                violation += m.violation
                obj += math.log1p(m.excess)
        else:
            believed = self._believed_state(state)
            tier_belief = self.belief_scale.get("tier_cost", 1.0)
            offset = self.ai_latency_offset_ms.get(tid, 0.0) if self.headroom_calibration else 0.0
            extra = {"extra_ai_latency_ms": offset} if offset else {}
            for demand in horizon:
                m = evaluate_step(self.configs[tid], believed,
                                  self._planning_demand(tid, demand), **extra)
                cost += m.cost_infra_usd + tier_belief * m.cost_tier_usd
                violation += m.violation
                obj += math.log1p(m.excess)
        n = max(1, len(horizon))
        out = (cost / n, violation / n, obj / n)
        self._project_memo[key] = out
        return out

    def _believed_state(self, state: TenantState) -> TenantState:
        """The configuration the projection *thinks* it is evaluating.

        `cache_half` is a belief about how fast the hit-rate curve saturates:
        `hit_rate(c) = HMAX * c / (c + CACHE_HALF_MB)`, so believing a half
        of `b * CACHE_HALF_MB` is exactly evaluating the true curve at `c / b`.
        Re-scaling the believed cache size rather than the module constant
        keeps the world untouched — mutating `model.CACHE_HALF_MB` would move
        the plant too, which is the confound this experiment exists to break.

        The one impurity, stated rather than hidden: a scaled cache size also
        scales the *memory* cost the projection believes it pays. That term is
        `MEM_COST_USD_GB_HR = 0.005`, i.e. $1.8e-6 per step at 128 MB against
        a $0.0139 per-step budget and ~$0.031 of tier spend — five orders of
        magnitude below the quantities any hypothesis reads.
        """
        half = self.belief_scale.get("cache_half", 1.0)
        if half == 1.0:
            return state
        return replace(state, cache_mb=max(0, int(round(state.cache_mb / half))))

    def _project_carbon(self, tid: str, state: TenantState, horizon: list[Demand]) -> float:
        """Horizon-mean carbon in grams. Kept separate from `_project` so the
        default path's arithmetic and return shape are untouched; only called
        when `carbon_weight` is set (M4 / T18)."""
        total = 0.0
        for demand in horizon:
            total += evaluate_step(
                self.configs[tid], state, self._planning_demand(tid, demand)).carbon_g
        return total / max(1, len(horizon))

    def _sweep_order(self) -> list[str]:
        """Tenant order for the coordinate-descent sweeps.

        Default is `sorted()`, which is what every published campaign ran and
        what R4 requires this to keep returning. The order is not neutral:
        the sweep is first-come-first-served on the shared cluster caps, so an
        earlier tenant claims contended capacity first. Tenant ids are
        assigned by slot (`t00`, `t01`, ...) and the mixes put premium and
        whale tenants in the low slots, so sorted order is confounded with
        priority class — in a controller that also reports a fairness index.

        `tenant_order_seed` draws a deterministic permutation instead, which
        is how PREREG_ORDER_PERMUTATION measures whether the published Jain
        survives re-ordering. It is a per-run constant, not per-cycle: the
        question is whether *an* order biases the outcome, and reshuffling
        every cycle would average the bias away and answer a different one.
        """
        if self.tenant_order_seed is None:
            return sorted(self.configs)
        if self._order_cache is None:
            order = sorted(self.configs)
            random.Random(self.tenant_order_seed).shuffle(order)
            self._order_cache = order
        return self._order_cache

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
        others = self._others(tid, chosen, horizons, cost_horizons)
        others_replicas = others[5]

        best_state, best_score = current, float("inf")
        incumbent_score = None
        for candidate in self._lattice(tid, base):
            score = self._candidate_score(tid, candidate, base, others, horizons,
                                          cost_horizons, budget_per_step)
            if score is None:
                continue
            if candidate == current:
                incumbent_score = score
            if score < best_score:
                best_score, best_state = score, candidate
        # Convergent solver only: a move must STRICTLY improve on the
        # incumbent, or sweeping could cycle between equal-score plans. The
        # published path keeps its enumeration-order tie-break (R4).
        if (self.converge_sweeps and incumbent_score is not None
                and not best_score < incumbent_score):
            return current
        # An entirely infeasible lattice (tight budget + tight cluster)
        # falls back to shedding cost: floor replicas, no cache, no model.
        if best_score == float("inf"):
            shed = (self._clamped_shed(tid, base) if self.clamped_fallback
                    else TenantState(replicas=config.replica_min, cache_mb=0, tier="none"))
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

    def _clamped_shed(self, tid: str, base: TenantState) -> TenantState:
        """The shed move inside the lattice's own guardrails: the largest
        replica step down (floored by apply_action), the lowest neighbouring
        cache level the tenant's bounds admit, and tier none unless the tier
        is pinned -- then the cheapest admitted tier (TIERS is cheapest
        first)."""
        config = self.configs[tid]
        caches = [c for c in neighbor_cache_levels(base.cache_mb)
                  if any(config.knob_admits(c, t) for t in TIERS)]
        cache_mb = min(caches) if caches else base.cache_mb
        tiers = [t for t in TIERS if config.knob_admits(cache_mb, t)]
        tier = tiers[0] if tiers else base.tier
        return apply_action(config, base, min(DELTA_REPLICAS), cache_mb, tier)

    def _others(self, tid: str, chosen: dict[str, TenantState],
                horizons: dict[str, list[Demand]],
                cost_horizons: dict[str, list[Demand]] | None) -> tuple:
        """Every other tenant's fixed contribution to `tid`'s objective:
        (cost, obj, carbon, satisfaction list, cache MB, replicas)."""
        other_cost = other_obj = other_carbon = 0.0
        other_satisfaction = []
        others_cache = others_replicas = 0
        for oid, ostate in chosen.items():
            if oid == tid:
                continue
            cost, viol, obj = self._project(oid, ostate, horizons[oid])
            if self.carbon_weight:
                other_carbon += self._project_carbon(oid, ostate, horizons[oid])
            if cost_horizons is not None:
                cost = self._project(oid, ostate, cost_horizons[oid], 1)[0]
            other_cost += cost
            other_obj += obj
            other_satisfaction.append(1.0 - viol)
            others_cache += ostate.cache_mb
            others_replicas += ostate.replicas
        return (other_cost, other_obj, other_carbon, other_satisfaction,
                others_cache, others_replicas)

    def _lattice(self, tid: str, base: TenantState):
        """`tid`'s move-blocked candidates around `base`, in the published
        enumeration order (replica delta, cache level, tier)."""
        config = self.configs[tid]
        for delta in DELTA_REPLICAS:
            if delta and self._dwell_blocks(tid, delta):
                continue
            for cache_mb in neighbor_cache_levels(base.cache_mb):
                for tier in TIERS:
                    # Per-tenant knob bounds: a pinned (min==max) cache or tier
                    # removes every off-value candidate, so a cache-only /
                    # tier-only ablation truly re-optimizes over the free knob
                    # alone instead of planning jointly and being clamped at
                    # actuation. Full-envelope bounds admit everything, so the
                    # published campaigns enumerate the identical lattice (R4).
                    if not config.knob_admits(cache_mb, tier):
                        continue
                    yield apply_action(config, base, delta, cache_mb, tier)

    def _candidate_score(self, tid: str, candidate: TenantState, base: TenantState,
                         others: tuple, horizons: dict[str, list[Demand]],
                         cost_horizons: dict[str, list[Demand]] | None,
                         budget_per_step: float) -> float | None:
        """The objective `tid` minimises for one candidate, others fixed; None
        when the candidate breaks a cluster cap or the tenant's budget."""
        config = self.configs[tid]
        (other_cost, other_obj, other_carbon, other_satisfaction,
         others_cache, others_replicas) = others
        if others_cache + candidate.cache_mb > self.limits.cache_mb:
            return None
        if others_replicas + candidate.replicas > self.limits.replicas:
            return None
        cost, viol, obj = self._project(tid, candidate, horizons[tid])
        if cost_horizons is not None:
            # Budget is a *money* guardrail: test it against the
            # spend the arriving demand will bill, not against the
            # headroom we provisioned for.
            cost = self._project(tid, candidate, cost_horizons[tid], 1)[0]
        # PREREG_BUDGET_PARITY: the per-tenant Budget CRD is a hard
        # filter applied BEFORE the objective, and no baseline in
        # baselines.py has an equivalent -- so the proposal is the
        # only arm solving "best SLO within budget". `jcac_nobudget`
        # lifts it to measure how much of the WP1 severity gap the
        # constraint accounts for. True (the default) is the
        # published behaviour, so every committed arm replays
        # bit-identically (R4).
        if self.enforce_budget and cost > budget_per_step:
            return None
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
            + self.switch_penalty * switches
        )
        # M4 / T18: guarded rather than multiplied by zero, so with
        # the knob off the score is the published expression to the
        # last bit.
        if self.carbon_weight:
            score += self.carbon_weight * (
                other_carbon + self._project_carbon(tid, candidate, horizons[tid])
            ) / CARBON_SCALE_G
        return score

    def best_response_gaps(self) -> dict[str, float]:
        """See `_best_response_gaps`; scored under the believed form, as the
        plan it checks was."""
        with self._believed():
            return self._best_response_gaps()

    def _best_response_gaps(self) -> dict[str, float]:
        """For the last `plan()`: how much each tenant could lower its own
        objective by a unilateral move with every other tenant held at its
        plan. 0.0 = a best response (Proposition 1's claim); inf = the planned
        state is infeasible while a feasible candidate exists. Uses the same
        lattice and score as the solver. With `replica_dwell_steps` on, the
        dwell state has already advanced past the plan, so read it with care."""
        if self._last_plan_ctx is None:
            return {}
        chosen, horizons, origin, cost_horizons = self._last_plan_ctx
        gaps = {}
        for tid in chosen:
            config = self.configs[tid]
            current = chosen[tid]
            base = current if origin is None else origin[tid]
            budget = config.hourly_budget_usd * model.CONTROL_INTERVAL_S / 3600.0
            others = self._others(tid, chosen, horizons, cost_horizons)
            planned = self._candidate_score(tid, current, base, others, horizons,
                                            cost_horizons, budget)
            scores = [s for s in (self._candidate_score(tid, c, base, others, horizons,
                                                        cost_horizons, budget)
                                  for c in self._lattice(tid, base)) if s is not None]
            if not scores:
                gaps[tid] = 0.0
            elif planned is None:
                gaps[tid] = float("inf")
            else:
                gaps[tid] = max(0.0, planned - min(scores))
        return gaps
