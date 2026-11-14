"""WP12: the joint and per-knob planners on the WP10 testbed, in the simulator
calibrated to it (programme 2).

The live pilot of the main contrast (plan WP12) compares the joint planner
with the per-knob planner on the testbed. Neither runs live: each arm's plan
is computed here, before any live run, on the exact demand of a run's
schedule, and the actuator replays it on the schedule's clock (actuator/
actuate.py replay). A pair therefore compares live what the two arms decide,
made by the arms' own code (research/comparators: TunablePlanner, knob_search
"joint" or "decoupled", anchored and convergent) on the plant WP11 validated.

The testbed as the planners see it:
  tenants   t00 pinned to the small tier, t01 to mid, as in WP11. The tier knob
            is fixed, so each tier pool serves one tenant and a tenant's
            replica knob IS its pool's size;
  replicas  counted in cores: a small pod is 1 core, a mid pod 2
            (actuator/bounds.json). The planner's one price per replica-hour
            is then the price per core-hour and its cluster replica cap the
            testbed's core budget, both exact; candidates that are not whole
            pods are never generated (TestbedPlanner);
  form      each tenant projected under its own tier's calibrated form
            (testbed/validation/predict.calibrate: L0, congestion exponent,
            tail, capacity per pod; replica scaling linear or fitted per tier),
            with the SLO at 3 x the small tier's L0, as WP11 predicted;
  cache     the gateway's per-tenant knob in whole MB on CACHE_LEVELS_MB (one
            level per interval, the published rule), believed through the
            model's curve hmax c / (c + half) fitted at those levels to WP6's
            steady-state curve at the testbed's threshold 0.95;
  money     no per-request price and no budget: the core budget binds.

A plan is the state of every step: step 0 is the initial posture (every tenant
at its pod floor, cache off); the state of step k + 1 is the planner's
decision after it has seen step k's demand, as in simulate.run.
"""

from __future__ import annotations

import contextlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

HERE = Path(__file__).resolve().parent
TESTBED = HERE.parent
REPO = TESTBED.parent
for _p in (REPO / "research" / "jcac_sim", REPO / "research" / "comparators", REPO / "research" / "theory",
           TESTBED / "validation"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import numpy as np  # noqa: E402

import arms  # noqa: E402  (research/comparators)
import model  # noqa: E402
import predict  # noqa: E402  (testbed/validation)
import protocol  # noqa: E402  (research/comparators)
from controller import ClusterLimits  # noqa: E402
from controller_index import analyse_plan  # noqa: E402
from model import Demand, TenantConfig, TenantState, apply_action  # noqa: E402
from tunable_planner import TunablePlanner  # noqa: E402

BIN_S = predict.BIN_S
WARMUP_STEPS = predict.WARMUP_STEPS
PINS = dict(predict.cs.PINS)
CACHE_LEVELS_MB = (0, 1, 2, 4, 8, 16)
PLANNER_ARMS = ("joint", "per_knob")
NO_CACHE_CAP_MB = 10**6
TENANTS_J = 2


class PlanError(ValueError):
    """A plan the testbed cannot apply exactly."""


@dataclass(frozen=True)
class TierForm:
    cores_per_pod: int
    pod_min: int
    pod_max: int
    a: float
    b: float
    f0: float
    l0_s: float
    capacity_per_pod_rps: float
    scaling: float | None = None  # capacity ~ pods ** scaling; None: linear


@dataclass(frozen=True)
class Testbed:
    tiers: Mapping[str, TierForm]
    pins: Mapping[str, str]
    slo_ms: float
    price_core_hr: float
    core_budget: int
    hit_max: float
    hit_half_mb: float
    cache_levels_mb: tuple = CACHE_LEVELS_MB

    def cores(self, tenant: str) -> int:
        return self.tiers[self.pins[tenant]].cores_per_pod

    def as_dict(self) -> dict:
        return {"tiers": {k: vars(v) for k, v in self.tiers.items()}, "pins": dict(self.pins),
                "slo_ms": self.slo_ms, "price_core_hr": self.price_core_hr, "core_budget": self.core_budget,
                "hit_max": self.hit_max, "hit_half_mb": self.hit_half_mb,
                "cache_levels_mb": list(self.cache_levels_mb)}


def fit_hit_curve(levels: tuple, rate: Callable[[float], float]) -> tuple[float, float]:
    """(hmax, half) of hmax c / (c + half), least squares at the positive
    levels; half on a log grid, hmax in closed form for each half."""
    c = np.array([x for x in levels if x > 0], dtype=float)
    y = np.array([rate(x) for x in c])
    best = (math.inf, 0.0, 1.0)
    for half in np.logspace(-3, 3, 1201):
        g = c / (c + half)
        hmax = min(1.0, max(0.0, float(g @ y / (g @ g))))
        sse = float(((hmax * g - y) ** 2).sum())
        if sse < best[0]:
            best = (sse, hmax, float(half))
    return best[1], best[2]


def testbed_from(calibration: Path, bounds: Path, scaling: str, price_core_hr: float,
                 wp6: Path = predict.WP6_CACHE) -> Testbed:
    """The testbed's form from the WP11 calibration curves (small_r1, mid_r1,
    and for fitted scaling every small_rN, mid_rN) and the actuator's bounds."""
    if scaling not in ("linear", "fitted"):
        raise ValueError(f"replica scaling must be linear or fitted, not {scaling!r}")
    raw = json.loads(Path(bounds).read_text(encoding="utf-8"))
    tiers = {}
    for tier in ("small", "mid"):
        cal = predict.calibrate(Path(calibration) / f"{tier}_r1")
        beta = predict.fitted_scaling(Path(calibration), tier) if scaling == "fitted" else None
        tb = raw["tiers"][tier]
        tiers[tier] = TierForm(int(tb["cores_per_pod"]), int(tb["min"]), int(tb["max"]), float(cal["a"]),
                               float(cal["b"]), float(cal["f0"]), float(cal["l0_s"]),
                               float(cal["capacity_per_replica_rps"]), beta)
    hmax, half = fit_hit_curve(CACHE_LEVELS_MB, lambda mb: predict.steady_hit_rate(mb, wp6))
    return Testbed(tiers, dict(PINS), predict.SLO_FACTOR * tiers["small"].l0_s * 1000, float(price_core_hr),
                   int(raw["core_budget"]), hmax, half)


def use_tier(tb: Testbed, tier: str) -> None:
    """The model's form and economy for one tier's pool: predict.tier_form's,
    with the cache knob live and replicas counted in cores."""
    t = tb.tiers[tier]
    beta = 1.0 if t.scaling is None else t.scaling
    model.set_model_form(
        congestion_exponent=min(2.0, max(1e-3, t.a)), p95_tail=(t.f0, t.b), mixture_p95=True, attainment_slo=True,
        replica_capacity_wu=t.capacity_per_pod_rps / t.cores_per_pod ** beta,
        wu_ai_scale=1.0 / model.WORK_UNITS["chat"], wu_crud_scale=0.0, cacheable_uniform=True,
        tier_latency_ms={x: t.l0_s * 1000 for x in ("small", "mid", "large")},
        replica_scaling_exponent=t.scaling)
    model.set_economy(tier_cost_usd_per_req={"small": 0.0, "mid": 0.0, "large": 0.0},
                      cache_hit_max=tb.hit_max, cache_half_mb=tb.hit_half_mb, replica_cost_usd_hr=tb.price_core_hr)


@contextlib.contextmanager
def testbed_world(tb: Testbed):
    """The testbed's SLO and cache lattice; the published world back on exit."""
    saved_slo, saved_levels = dict(model.SLO_BASE_MS), model.CACHE_LEVELS_MB
    model.SLO_BASE_MS["ai"] = tb.slo_ms
    model.CACHE_LEVELS_MB = tuple(tb.cache_levels_mb)
    try:
        yield
    finally:
        model.SLO_BASE_MS.clear()
        model.SLO_BASE_MS.update(saved_slo)
        model.CACHE_LEVELS_MB = saved_levels
        model.set_model_form()
        model.set_economy()


def tenant_configs(tb: Testbed) -> dict[str, TenantConfig]:
    out = {}
    for tid, tier in tb.pins.items():
        t = tb.tiers[tier]
        out[tid] = TenantConfig(tenant_id=tid, slo_class="premium", hourly_budget_usd=1e9,
                                replica_min=t.pod_min * t.cores_per_pod, replica_max=t.pod_max * t.cores_per_pod,
                                cache_min=0, cache_max=max(tb.cache_levels_mb), tier_min=tier, tier_max=tier)
    return out


def initial_states(tb: Testbed) -> dict[str, TenantState]:
    return {tid: TenantState(replicas=tb.tiers[tier].pod_min * tb.tiers[tier].cores_per_pod, cache_mb=0, tier=tier)
            for tid, tier in tb.pins.items()}


class TestbedPlanner(TunablePlanner):
    """TunablePlanner on the testbed: whole pods only, every tenant projected
    under its own tier's form. Everything else is the arm's own code."""

    name = "wp12_testbed"

    def __init__(self, configs: dict, testbed: Testbed, **kwargs):
        super().__init__(configs, **kwargs)
        self.testbed = testbed

    def whole_pods(self, tid: str, state: TenantState) -> bool:
        return state.replicas % self.testbed.cores(tid) == 0

    def _lattice(self, tid, base):
        return (c for c in super()._lattice(tid, base) if self.whole_pods(tid, c))

    def _knob_candidates(self, tid, base, held, knob):
        return [c for c in super()._knob_candidates(tid, base, held, knob) if self.whole_pods(tid, c)]

    def _project(self, tid, state, horizon, tag=0):
        use_tier(self.testbed, self.testbed.pins[tid])
        return super()._project(tid, state, horizon, tag)


def testbed_box(tb: Testbed):
    """The box a testbed planner searches around `base` (for the index)."""
    def box(ctl, tid, base):
        config = ctl.configs[tid]
        reach = {apply_action(config, base, d, base.cache_mb, base.tier).replicas
                 for d in ctl._delta_replicas if not (d and ctl._dwell_blocks(tid, d))}
        return [sorted(r for r in reach if r % tb.cores(tid) == 0), list(ctl._cache_candidates(base.cache_mb)),
                [tb.pins[tid]]]
    return box


def check_states(tb: Testbed, states: Mapping[str, TenantState]) -> None:
    cores = 0
    for tid, s in states.items():
        if s.tier != tb.pins[tid]:
            raise PlanError(f"{tid}: tier {s.tier!r}, pinned to {tb.pins[tid]!r}")
        if s.replicas % tb.cores(tid):
            raise PlanError(f"{tid}: {s.replicas} cores is not a whole number of pods")
        if s.cache_mb not in tb.cache_levels_mb:
            raise PlanError(f"{tid}: cache {s.cache_mb} MB is not a testbed level")
        cores += s.replicas
    if cores > tb.core_budget:
        raise PlanError(f"{cores} cores over the budget of {tb.core_budget}")


def step_demands(demand: Mapping, step: int, tenants) -> dict[str, Demand]:
    return {t: Demand(rps={"chat": demand.get((step, t), 0) / BIN_S}) for t in tenants}


def plan_run(arm: str, params: dict, demand: Mapping[tuple[int, str], int], tb: Testbed, steps: int,
             with_index: bool = False) -> dict:
    """One arm's plan on one schedule's demand {(step, tenant): requests},
    the planners' own predicted outcome, and (with_index) the interaction
    index of every decision."""
    if arm not in PLANNER_ARMS:
        raise ValueError(f"WP12 compares {PLANNER_ARMS}, not {arm!r}")
    _, full = arms.controller_params(arm, params, "enforced")
    configs = tenant_configs(tb)
    states = initial_states(tb)
    path, rows = [dict(states)], []
    with testbed_world(tb):
        ctl = TestbedPlanner(configs, tb, limits=ClusterLimits(cache_mb=NO_CACHE_CAP_MB, replicas=tb.core_budget),
                             **full)
        for k in range(steps - 1):
            plans = ctl.plan(states, step_demands(demand, k, configs))
            states = {t: p.state for t, p in plans.items()}
            check_states(tb, states)
            path.append(dict(states))
            if with_index:
                rows += [{"step": k, **r} for r in analyse_plan(ctl, box_of=testbed_box(tb))]
        outcome = predicted_outcome(path, demand, tb, configs)
    return {"path": path, "outcome": outcome, "index_rows": rows}


def j_of(total_cost: float, steps: int, violations: np.ndarray) -> dict:
    """WP9's objective (protocol.objective) for TENANTS_J tenants:
    `violations` is steps x tenants (each tenant-step's late share), the
    fairness term the simulator's (model.jain_index of the satisfactions)."""
    jain = [model.jain_index([1.0 - float(v) for v in row]) for row in violations]
    metrics = {"total_cost_usd": total_cost, "steps": steps, "mean_violation": float(violations.mean()),
               "mean_jain": float(np.mean(jain)), "mean_quality_loss": 0.0}
    return {**metrics, **protocol.objective(metrics, TENANTS_J)}


def predicted_outcome(path: list, demand: Mapping, tb: Testbed, configs: Mapping[str, TenantConfig]) -> dict:
    """Cost, attainment and J of a plan over the scored steps, by the model
    the planners plan with (inside testbed_world)."""
    tenants = sorted(configs)
    scored = range(WARMUP_STEPS, len(path))
    viol = np.zeros((len(scored), len(tenants)))
    cost = late = requests = 0.0
    for i, k in enumerate(scored):
        for j, tid in enumerate(tenants):
            state = path[k][tid]
            cost += state.replicas * tb.price_core_hr * BIN_S / 3600.0
            n = float(demand.get((k, tid), 0))
            if n <= 0:
                continue
            use_tier(tb, tb.pins[tid])
            m = model.evaluate_step(configs[tid], state, Demand(rps={"chat": n / BIN_S}))
            viol[i, j] = m.violation
            late += n * m.violation
            requests += n
    out = j_of(cost, len(scored), viol)
    out.update(requests=requests, attainment=1.0 - late / requests if requests else math.nan,
               cost_per_1k=1000.0 * cost / requests if requests else math.nan)
    return out


def decisions(path: list, tb: Testbed) -> list[dict]:
    """The plan as actuator decisions (actuate.parse_decision's shape): step
    0's full posture, then only what changes, at t = step x BIN_S."""
    out, prev = [], None
    for k, states in enumerate(path):
        replicas = {tb.pins[t]: s.replicas // tb.cores(t) for t, s in sorted(states.items())}
        tenants = {t: {"model_tier": s.tier, "cache_size_mb": int(s.cache_mb)} for t, s in sorted(states.items())}
        if prev is None:
            out.append({"step": k, "t_s": 0.0, "replicas": replicas, "tenants": tenants})
        else:
            d = {"step": k, "t_s": float(k * BIN_S)}
            moved = {tier: n for tier, n in replicas.items() if prev[0][tier] != n}
            knobs = {t: {"cache_size_mb": v["cache_size_mb"]} for t, v in tenants.items()
                     if prev[1][t]["cache_size_mb"] != v["cache_size_mb"]}
            if moved:
                d["replicas"] = moved
            if knobs:
                d["tenants"] = knobs
            if moved or knobs:
                out.append(d)
        prev = (replicas, tenants)
    return out


def index_summary(rows: list[dict]) -> dict:
    """Mean interaction index, disagreement share and mean loss over decisions."""
    if not rows:
        return {"decisions": 0}
    idx = [r["index"] for r in rows if r["index"] is not None]
    loss = [r["loss"] for r in rows if r["loss"] is not None]
    return {"decisions": len(rows), "mean_index": float(np.mean(idx)) if idx else math.nan,
            "disagree_share": float(np.mean([r["disagree"] for r in rows])),
            "costly_disagree_share": float(np.mean([r["disagree_with_loss"] for r in rows])),
            "mean_loss": float(np.mean(loss)) if loss else math.nan,
            "anchor_infeasible_share": float(np.mean([not r["anchor_feasible"] for r in rows]))}
