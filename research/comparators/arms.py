"""The programme-2 arm set (WP9) as in-process harness registrations.

Every tuned arm declares its tunable parameters (name, type, range or
choices, default); `sample_configs` draws the 64 random configurations of the
tuning protocol from them. Arms reach the harness the way the pilots' arms do
(research/theory/dose_response_pilot.py, research/sensitivity/morris_pilot.py):
a `SystemSpec` added to `harness.systems.SYSTEMS` at run time, never written
to eval/harness/systems.py. The new controllers are built through
`baselines.make_baseline`, swapped for a dispatcher only inside `installed()`;
`arm_system()` undoes both and restores the published economy and model form
when a job ends, so a pooled worker carries nothing from one job to the next.

| arm | what it is | existing code it maps to |
|---|---|---|
| joint | joint planner | controller.JCACController, knob_search "joint" (registered arm `jcac_converged`) |
| per_knob | per-knob planner | knob_search "decoupled" (registered arm `jcac_decoupled`) |
| one_knob | iterated one-knob planner | knob_search "knobwise" (registered arm `jcac_knobwise`) |
| layers | independent per-layer planners | new: layer_control.IndependentLayersController |
| rules | per-layer stack of rules | new: layer_control.RulesStackController with k8s_hpa |
| static_best | reference line | research/theory/clairvoyant.py `static_best` |
| clairvoyant | reference lines | research/theory/clairvoyant.py `bounds` (LB0, LB1, LB2, UB) |

The three planner arms run `tunable_planner.TunablePlanner` (JCACController
plus a settable horizon and weights; identical to it at the defaults), anchored
and convergent like every programme-2 pilot. The two per-layer arms run inside
the cluster caps (`baselines.CapEnforced`) and, when the budget is enforced,
behind `layer_control.BudgetGate` applied last (BudgetGate(CapEnforced(...)):
the gate only lowers usage, so both constraints hold); the planners enforce
both themselves.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for _p in (HERE, REPO / "research" / "jcac_sim", REPO / "eval"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import numpy as np  # noqa: E402

import baselines  # noqa: E402
import model  # noqa: E402
from layer_control import FORECASTS, BudgetGate, IndependentLayersController, RulesStackController  # noqa: E402
from tunable_planner import TunablePlanner  # noqa: E402

PREFIX = "wp9_"
BUDGETS = ("enforced", "plant")
TUNED = ("joint", "per_knob", "one_knob", "layers", "rules")
REFERENCES = ("static_best", "clairvoyant")
SIGNALS = ("utilisation", "queue")
TIERS = ("small", "mid", "large")


@dataclass(frozen=True)
class Param:
    """One tunable parameter. `float`: uniform on [low, high], or log-uniform
    with `log`; `int`: uniform on low..high inclusive; `choice`: uniform over
    `choices`. Sampled floats keep six significant digits."""

    name: str
    kind: str
    default: object
    low: float | None = None
    high: float | None = None
    choices: tuple = ()
    log: bool = False
    doc: str = ""

    def __post_init__(self) -> None:
        if self.kind == "choice":
            if not self.choices or self.default not in self.choices:
                raise ValueError(f"{self.name}: a choice needs choices that include the default")
            return
        if self.kind not in ("float", "int"):
            raise ValueError(f"{self.name}: unknown kind {self.kind!r}")
        if self.low is None or self.high is None or not self.low < self.high:
            raise ValueError(f"{self.name}: needs low < high")
        if self.log and self.low <= 0.0:
            raise ValueError(f"{self.name}: a log range needs low > 0")
        if not self.contains(self.default):
            raise ValueError(f"{self.name}: default {self.default!r} outside its range")

    def contains(self, value: object) -> bool:
        if self.kind == "choice":
            return value in self.choices
        if self.kind == "int" and (not isinstance(value, int) or isinstance(value, bool)):
            return False
        return isinstance(value, (int, float)) and self.low <= value <= self.high

    def sample(self, rng: np.random.Generator) -> object:
        if self.kind == "choice":
            return self.choices[int(rng.integers(len(self.choices)))]
        if self.kind == "int":
            return int(rng.integers(int(self.low), int(self.high) + 1))
        u = rng.random()
        x = (math.exp(math.log(self.low) + u * (math.log(self.high) - math.log(self.low))) if self.log
             else self.low + u * (self.high - self.low))
        return min(self.high, max(self.low, float(f"{x:.6g}")))

    def describe(self) -> str:
        if self.kind == "choice":
            return "{" + ", ".join(str(c) for c in self.choices) + "}"
        if self.kind == "int":
            return f"integer {self.low:g}..{self.high:g}"
        return f"{'log-uniform' if self.log else 'uniform'} [{self.low:g}, {self.high:g}]"


PLANNER_SPACE = (
    Param("switch_penalty", "float", 0.05, 0.001, 0.2, log=True,
          doc="objective units charged per changed knob (hysteresis); published 0.05"),
    Param("horizon_steps", "int", 6, 1, 6, doc="forecast steps of 10 s the plan is judged on"),
    Param("forecast_method", "choice", "trend", choices=FORECASTS, doc="demand forecaster"),
    Param("weight_beta", "float", 2.0, 0.25, 32.0, log=True,
          doc="SLO weight beta; the cost weight alpha is fixed at 1 (the objective's scale)"),
    Param("weight_gamma", "float", 0.5, 0.0, 2.0, doc="fairness weight gamma"),
)
LAYERS_SPACE = (
    Param("forecast_method", "choice", "trend", choices=FORECASTS, doc="demand forecaster, every layer"),
    Param("horizon_steps", "int", 6, 1, 6, doc="forecast steps each layer averages over"),
    Param("pool_signal", "choice", "utilisation", choices=SIGNALS, doc="the pool planner's signal"),
    Param("pool_target_utilisation", "float", 0.6, 0.05, 1.0, log=True, doc="target when the signal is utilisation"),
    Param("pool_target_queue", "float", 1.5, 0.25, 8.0, log=True,
          doc="target work in flight per replica when the signal is queue"),
    Param("pool_weight", "float", 1.0, 0.01, 100.0, log=True, doc="price of signal overshoot against replica cost"),
    Param("hit_value_usd", "float", 1e-4, 1e-8, 1e-2, log=True, doc="the cache planner's value of one hit, USD"),
    Param("router_latency_weight", "float", 2.0, 0.1, 100.0, log=True,
          doc="price of AI p95 overshoot against per-request spend"),
)
RULES_SPACE = (
    Param("hpa_metric", "choice", "utilisation", choices=SIGNALS,
          doc="HPA metric: utilisation (Resource) or queue (Pods, work in flight per replica)"),
    Param("hpa_target_utilisation", "float", 0.6, 0.05, 1.0, log=True, doc="averageUtilization target"),
    Param("hpa_target_queue", "float", 1.5, 0.25, 8.0, log=True, doc="averageValue target, work in flight per pod"),
    Param("cache_hits_per_mb_s", "float", 1e-3, 1e-5, 1e-1, log=True,
          doc="cache-sizing rule: grow while a level buys this many hits/s per MB"),
    Param("router_tier", "choice", "small", choices=TIERS, doc="router rule: preferred tier (latency fallback)"),
)


def quality_param(score_weight: float) -> Param:
    """The planner-side price of answer-quality loss, offered only when the
    scoring objective prices it: default the score's own weight."""
    return Param("quality_weight", "float", float(score_weight), 0.0, 2.0 * float(score_weight),
                 doc="price of answer-quality loss in the arm's own objective")


@dataclass(frozen=True)
class Arm:
    name: str
    role: str  # "tuned" or "reference"
    controller: str | None  # a wp9_* dispatcher name; None for a reference
    fixed: tuple  # (name, value) pairs every configuration carries
    space: tuple
    maps_to: str
    description: str
    planner: bool = False  # enforce_budget follows the budget mode
    gated: bool = False  # behind BudgetGate when the budget is enforced
    capped: bool = False  # inside baselines.CapEnforced
    quality: bool = False  # offered quality_param when quality is priced


_PLANNER_FIXED = (("anchor_moves", True), ("converge_sweeps", True))

ARMS: dict[str, Arm] = {
    "joint": Arm(
        "joint", "tuned", "wp9_planner", _PLANNER_FIXED + (("knob_search", "joint"),), PLANNER_SPACE,
        "controller.JCACController, knob_search 'joint' (registered arm jcac_converged), via "
        "tunable_planner.TunablePlanner",
        "the joint planner: one tenant's three knobs searched together on the move lattice, "
        "tenants coordinated by convergent sweeps on one shared objective", planner=True, quality=True),
    "per_knob": Arm(
        "per_knob", "tuned", "wp9_planner", _PLANNER_FIXED + (("knob_search", "decoupled"),), PLANNER_SPACE,
        "knob_search 'decoupled' (registered arm jcac_decoupled)",
        "the per-knob planner: each knob best-responded alone on the shared objective, winners combined",
        planner=True, quality=True),
    "one_knob": Arm(
        "one_knob", "tuned", "wp9_planner", _PLANNER_FIXED + (("knob_search", "knobwise"),), PLANNER_SPACE,
        "knob_search 'knobwise' (registered arm jcac_knobwise)",
        "the iterated one-knob planner: one knob at a time to a fixed point, never a two-knob move",
        planner=True, quality=True),
    "layers": Arm(
        "layers", "tuned", "wp9_layers", (), LAYERS_SPACE,
        "new: layer_control.IndependentLayersController (router objective has the form of "
        "baselines.quality_aware_tier, with its own weights); caps baselines.CapEnforced",
        "independent per-layer planners: pool on utilisation or queue, cache on hit rate, router on "
        "latency and price; each its own local objective, no shared one",
        gated=True, capped=True, quality=True),
    "rules": Arm(
        "rules", "tuned", "wp9_rules", (), RULES_SPACE,
        "new: layer_control.RulesStackController; pool k8s_hpa.K8sReplicaLayer (in place of "
        "baselines.HPAController(policy='k8s_default'), see k8s_hpa.py); router fallback "
        "baselines.cheapest_meeting_tier; caps baselines.CapEnforced",
        "per-layer stack of rules: Kubernetes default-behaviour HPA on each pool, a cache-sizing rule, "
        "a router rule, the budget gate", gated=True, capped=True),
    "static_best": Arm(
        "static_best", "reference", None, (), (),
        "research/theory/clairvoyant.py static_best",
        "hindsight-best fixed state per tenant inside an equal share of the caps (WP1)"),
    "clairvoyant": Arm(
        "clairvoyant", "reference", None, (), (),
        "research/theory/clairvoyant.py bounds",
        "clairvoyant bounds LB0 (any controller), LB1/LB2 (controllers inside the move limits) and an "
        "achievable plan UB (WP1)"),
}


def arm(name: str) -> Arm:
    if name not in ARMS:
        raise ValueError(f"unknown arm {name!r} (known: {sorted(ARMS)})")
    return ARMS[name]


def space(name: str, quality_weight: float = 0.0) -> tuple:
    """The arm's tunable parameters under a scoring objective that prices
    quality loss at `quality_weight` (0 = not priced)."""
    a = arm(name)
    if a.role != "tuned":
        raise ValueError(f"{name} is a reference line, not a tuned arm")
    return a.space + ((quality_param(quality_weight),) if quality_weight > 0.0 and a.quality else ())


def defaults(params: tuple) -> dict:
    return {p.name: p.default for p in params}


def sample_configs(name: str, n: int, seed: int, quality_weight: float = 0.0,
                   include_default: bool = True) -> list[dict]:
    """`n` configurations of arm `name`: the declared defaults first (when
    `include_default`), then random draws. Each arm has its own stream,
    seeded by (seed, arm name), so a draw does not depend on which other
    arms are tuned, and growing `n` only appends."""
    if n < 1:
        raise ValueError("need at least one configuration")
    params = space(name, quality_weight)
    stream = int(hashlib.sha256(f"wp9|{name}".encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng([int(seed), stream])
    out = [defaults(params)] if include_default else []
    while len(out) < n:
        out.append({p.name: p.sample(rng) for p in params})
    return out


def controller_params(name: str, params: dict, budget: str = "enforced") -> tuple[str, dict]:
    """(dispatcher controller name, full constructor parameters) of one
    configuration of a tuned arm under a budget mode: `enforced` = every arm
    keeps each tenant inside its per-step budget (planners filter, per-layer
    arms are gated); `plant` = only the plant's own admission gate."""
    if budget not in BUDGETS:
        raise ValueError(f"budget must be one of {BUDGETS}, not {budget!r}")
    a = arm(name)
    if a.role != "tuned":
        raise ValueError(f"{name} is a reference line, not a runnable arm")
    allowed = {p.name for p in space(name, 1.0)}
    unknown = set(params) - allowed
    if unknown:
        raise ValueError(f"{name}: unknown parameters {sorted(unknown)}")
    full = {**dict(a.fixed), **params}
    controller = a.controller
    if a.planner:
        full["enforce_budget"] = budget == "enforced"
    if a.capped:
        controller += "_capped"
    if a.gated and budget == "enforced":
        controller += "_gate"
    return controller, full


FACTORIES = {"wp9_planner": TunablePlanner, "wp9_layers": IndependentLayersController,
             "wp9_rules": RulesStackController}


def build(name: str, configs: dict, **params):
    """`make_baseline` for wp9_* names: a base name with wrapper suffixes,
    the last suffix outermost (`wp9_rules_capped_gate` is
    BudgetGate(CapEnforced(RulesStackController)))."""
    if name.endswith("_gate"):
        return BudgetGate(build(name[: -len("_gate")], configs, **params))
    if name.endswith("_capped"):
        return baselines.CapEnforced(build(name[: -len("_capped")], configs, **params))
    if name in FACTORIES:
        return FACTORIES[name](configs, **params)
    raise ValueError(f"unknown WP9 controller {name!r}")


@contextlib.contextmanager
def installed():
    """Route wp9_* controller names through `build`; every other name goes
    to the published make_baseline unchanged. Restored on exit."""
    original = baselines.make_baseline
    if getattr(original, "wp9_dispatch", False):
        yield
        return

    def make_baseline(name: str, configs: dict, **params):
        if name.startswith(PREFIX):
            return build(name, configs, **params)
        return original(name, configs, **params)

    make_baseline.wp9_dispatch = True
    baselines.make_baseline = make_baseline
    try:
        yield
    finally:
        baselines.make_baseline = original


def system_name(name: str, params: dict, budget: str) -> str:
    digest = hashlib.sha256(json.dumps([name, budget, params], sort_keys=True).encode()).hexdigest()
    return f"{PREFIX}{name}_{digest[:12]}"


@contextlib.contextmanager
def arm_system(name: str, params: dict, budget: str = "enforced"):
    """Yield the registry name of one configuration of a tuned arm, with the
    dispatcher installed. On exit: the registration is removed, make_baseline
    is restored and the model's economy and form are reset to the published
    ones (sim_backend sets both at the start of every run anyway)."""
    from harness.systems import SYSTEMS, SystemSpec

    controller, full = controller_params(name, params, budget)
    key = system_name(name, full, budget)
    with installed():
        SYSTEMS[key] = SystemSpec(controller, params=full,
                                  description=f"WP9 arm {name} ({budget} budget): {arm(name).description}")
        try:
            yield key
        finally:
            SYSTEMS.pop(key, None)
            model.set_economy()
            model.set_model_form()


def execute(name: str, params: dict, run, budget: str = "enforced") -> dict:
    """`harness.sim_backend.execute` of one configuration of a tuned arm on
    `run` (a harness RunSpec; its system field is replaced)."""
    import dataclasses

    from harness import sim_backend

    with arm_system(name, params, budget) as key:
        return sim_backend.execute(dataclasses.replace(run, system=key))
