"""Kubernetes' HorizontalPodAutoscaler with its default scaling behaviour, on
each tenant's pool (programme 2, WP9).

Defaults, from the official documentation
https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/
(retrieved 2026-11-12; the page reads "Last modified August 03, 2026"):

- algorithm: desiredReplicas = ceil[currentReplicas * (currentMetricValue /
  desiredMetricValue)]; "The control plane skips any scaling action if the
  ratio is sufficiently close to 1.0 (within a configurable tolerance, 0.1
  by default)."
- sync: "the default interval is 15 seconds"
  (--horizontal-pod-autoscaler-sync-period).
- "Default behavior":
      scaleDown: stabilizationWindowSeconds 300
                 policies: [Percent 100 per 15 s]
      scaleUp:   stabilizationWindowSeconds 0
                 policies: [Percent 100 per 15 s, Pods 4 per 15 s]
                 selectPolicy: Max
  "The controller considers all recommendations within a configurable window
  choosing the highest recommendation from within that window"; "4 pods or a
  100% of the currently running replicas may at most be added every 15
  seconds".

The arithmetic follows the controller source, kubernetes/kubernetes
pkg/controller/podautoscaler/horizontal.go (master, read 2026-11-12):
`stabilizeRecommendationWithBehaviors` (a recommendation counts while its
timestamp is After now - window; the result is the current count pulled up to
the minimum over the scale-up window and down to the maximum over the
scale-down window; every unstabilised recommendation is recorded),
`calculateScaleUpLimitWithScalingRules` / `calculateScaleDownLimitWithBehaviors`
(both directions: period start = current - replicas added + replicas deleted
within the period, from the scale-up AND the scale-down events; Pods: start
+/- value; Percent: ceil(start * (1 + v/100)) up, trunc(start * (1 - v/100))
down; selectPolicy Max = the largest change) and `storeScaleEvent`. With
syncs exactly 15 s apart and the controller's strict "After" cut, the previous
sync's event is never inside the next 15 s period, so here the default
policies act as per-sync caps: up to max(2 x current, current + 4), and down
to the stabilised recommendation in one sync.

Two readings of "default" exist, and both are here:
- `behavior="default"` (used by the arm): the documented default block above,
  what the API fills in once a `behavior` field is present.
- `behavior="unset"`: an HPA created with no `behavior` field. The v2
  defaulting (pkg/apis/autoscaling/v2/defaults.go) leaves it nil, and the
  controller then runs its legacy path (`normalizeDesiredReplicas`): the
  stabilised value is the MAXIMUM recommendation over the last 300 s in either
  direction, and one sync may raise the count to at most max(2 x current, 4)
  replicas (`calculateScaleUpLimit`), with no scale-down rate limit. That
  path marks a sample old when it is Before the cut, so a sample exactly
  300 s old still counts (unlike the behavior path's strict After).

Compared with `baselines.HPAController(policy="k8s_default")`, which this
module leaves untouched: that arm has no tolerance band, acts every 10 s
control interval (the default syncs every 15 s), treats one 10 s interval as
one 15 s policy period and keeps 30 intervals of recommendations.

Simulator approximations, stated:
- The simulator decides once per 10 s interval. A sync tick at time t acts on
  the decision of the interval containing t (ticks at 0, 15, 30, 45 s fall in
  the intervals starting at 0, 10, 30, 40 s): on average one sync per 15 s,
  and a new count takes effect at the start of an interval.
- The metric is read without delay and every pod is ready: the documented
  --horizontal-pod-autoscaler-initial-readiness-delay (30 s) and
  --horizontal-pod-autoscaler-cpu-initialization-period (5 min) never bind.
- Utilisation is offered work over the pool's capacity, so it can exceed 1
  in overload; a real pod's CPU usage saturates at its limit.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, replace
from pathlib import Path

_SIM = Path(__file__).resolve().parents[1] / "jcac_sim"
if str(_SIM) not in sys.path:
    sys.path.insert(0, str(_SIM))

import model  # noqa: E402
from model import Demand, TenantConfig, TenantState  # noqa: E402

DOC_URL = "https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/"
RETRIEVED = "2026-11-12"

SYNC_PERIOD_S = 15
TOLERANCE = 0.1
SCALE_UP_WINDOW_S = 0
SCALE_DOWN_WINDOW_S = 300
# (type, value, periodSeconds); selectPolicy Max in both directions
SCALE_UP_POLICIES = (("Percent", 100, 15), ("Pods", 4, 15))
SCALE_DOWN_POLICIES = (("Percent", 100, 15),)
# Legacy path (no `behavior` field): scaleUpLimitFactor, scaleUpLimitMinimum
LEGACY_UP_FACTOR = 2.0
LEGACY_UP_MINIMUM = 4.0

METRICS = ("utilisation", "queue")
BEHAVIORS = ("default", "unset")


@dataclass(frozen=True)
class PoolHistory:
    """One pool's HPA memory: time-stamped unstabilised recommendations and
    scale events, as (time_s, replicas) pairs."""

    recommendations: tuple = ()
    up_events: tuple = ()
    down_events: tuple = ()


def metric_recommendation(current: int, ratio: float, tolerance: float = TOLERANCE) -> int:
    """The replica calculator: hold inside the tolerance band, else
    ceil(ratio x current). May be 0 or above the maximum; the bounds are
    applied after stabilisation, as in the controller."""
    if abs(1.0 - ratio) <= tolerance:
        return current
    return math.ceil(ratio * current)


def stabilize(history: PoolHistory, now: float, current: int, desired: int,
              up_window: float = SCALE_UP_WINDOW_S,
              down_window: float = SCALE_DOWN_WINDOW_S) -> tuple[int, PoolHistory]:
    """`stabilizeRecommendationWithBehaviors`: the current count, raised to the
    minimum recommendation of the scale-up window and lowered to the maximum
    of the scale-down window (both include `desired`). Records `desired` and
    drops samples older than both windows (the controller reuses their slot)."""
    up_cut, down_cut = now - up_window, now - down_window
    up_rec = down_rec = desired
    for t, rec in history.recommendations:
        if t > up_cut:
            up_rec = min(up_rec, rec)
        if t > down_cut:
            down_rec = max(down_rec, rec)
    out = current
    if out < up_rec:
        out = up_rec
    if out > down_rec:
        out = down_rec
    keep = tuple((t, r) for t, r in history.recommendations if t > min(up_cut, down_cut))
    return out, replace(history, recommendations=keep + ((now, desired),))


def _period_start(history: PoolHistory, now: float, current: int, period: float) -> int:
    cut = now - period
    added = sum(n for t, n in history.up_events if t > cut)
    deleted = sum(n for t, n in history.down_events if t > cut)
    return current - added + deleted


def scale_up_limit(history: PoolHistory, now: float, current: int,
                   policies: tuple = SCALE_UP_POLICIES) -> int:
    """Highest count one sync may reach (selectPolicy Max), never below current."""
    proposals = []
    for kind, value, period in policies:
        start = _period_start(history, now, current, period)
        proposals.append(start + value if kind == "Pods" else math.ceil(start * (1.0 + value / 100.0)))
    return max(max(proposals), current)


def scale_down_limit(history: PoolHistory, now: float, current: int,
                     policies: tuple = SCALE_DOWN_POLICIES) -> int:
    """Lowest count one sync may reach (selectPolicy Max), never above current."""
    proposals = []
    for kind, value, period in policies:
        start = _period_start(history, now, current, period)
        proposals.append(start - value if kind == "Pods" else int(start * (1.0 - value / 100.0)))
    return min(min(proposals), current)


def rate_limited(history: PoolHistory, now: float, current: int, desired: int,
                 rmin: int, rmax: int) -> int:
    """`convertDesiredReplicasWithBehaviorRate`."""
    if desired > current:
        return min(desired, min(rmax, scale_up_limit(history, now, current)))
    if desired < current:
        return max(desired, max(rmin, scale_down_limit(history, now, current)))
    return desired


def record_event(history: PoolHistory, now: float, current: int, new: int) -> PoolHistory:
    """`storeScaleEvent`, dropping events older than the longest policy period."""
    longest = max(p for _, _, p in SCALE_UP_POLICIES + SCALE_DOWN_POLICIES)
    up = tuple(e for e in history.up_events if e[0] > now - longest)
    down = tuple(e for e in history.down_events if e[0] > now - longest)
    if new > current:
        up += ((now, new - current),)
    elif new < current:
        down += ((now, current - new),)
    return replace(history, up_events=up, down_events=down)


def reconcile(history: PoolHistory, now: float, current: int, ratio: float, rmin: int, rmax: int,
              behavior: str = "default", tolerance: float = TOLERANCE) -> tuple[int, PoolHistory]:
    """One sync of one pool: (new replica count, new history)."""
    desired = metric_recommendation(current, ratio, tolerance)
    if behavior == "unset":
        return _reconcile_legacy(history, now, current, desired, rmin, rmax)
    stable, history = stabilize(history, now, current, desired)
    new = rate_limited(history, now, current, stable, rmin, rmax)
    return new, record_event(history, now, current, new)


def _reconcile_legacy(history: PoolHistory, now: float, current: int, desired: int,
                      rmin: int, rmax: int) -> tuple[int, PoolHistory]:
    """`normalizeDesiredReplicas`: window maximum in either direction, then
    [minReplicas, min(maxReplicas, max(2 x current, 4))]."""
    cut = now - SCALE_DOWN_WINDOW_S
    stable = max([desired] + [r for t, r in history.recommendations if t >= cut])
    keep = tuple((t, r) for t, r in history.recommendations if t >= cut)
    history = replace(history, recommendations=keep + ((now, desired),))
    up_cap = int(max(LEGACY_UP_FACTOR * current, LEGACY_UP_MINIMUM))
    return max(rmin, min(stable, min(rmax, up_cap))), history


def usage_ratio(state: TenantState, demand: Demand, metric: str, target: float) -> float:
    """Current metric over its target. `utilisation`: offered work over the
    pool's capacity (a Resource metric, target averageUtilization);
    `queue`: work in flight per replica by Little's law under the plant's
    congestion law (a Pods metric, target averageValue), as
    baselines.ConcurrencyController computes it."""
    wu = demand.work_units(state.cache_mb, state.tier)
    rho = wu / (max(1, state.replicas) * model.REPLICA_CAPACITY_WU)
    if metric == "utilisation":
        return rho / target
    return rho * model.congestion(wu, max(1, state.replicas)) / target


class K8sReplicaLayer:
    """One default-behaviour HPA per tenant pool, synced on the 15 s grid.
    Time is counted in `replicas` calls (one per control interval), so a run
    whose engine skips planning (simulate's chaos_planner_outage) would shift
    the grid; WP9 runs never skip."""

    def __init__(self, configs: dict[str, TenantConfig], metric: str = "utilisation",
                 target_utilisation: float = 0.6, target_queue: float = 1.5,
                 behavior: str = "default"):
        if metric not in METRICS:
            raise ValueError(f"metric must be one of {METRICS}, not {metric!r}")
        if behavior not in BEHAVIORS:
            raise ValueError(f"behavior must be one of {BEHAVIORS}, not {behavior!r}")
        if target_utilisation <= 0.0 or target_queue <= 0.0:
            raise ValueError("HPA targets must be positive")
        if SYNC_PERIOD_S < model.CONTROL_INTERVAL_S:
            raise ValueError("at most one sync per control interval is modelled")
        self.configs = configs
        self.metric = metric
        self.target = target_utilisation if metric == "utilisation" else target_queue
        self.behavior = behavior
        self.histories = {tid: PoolHistory() for tid in configs}
        self._cycle = 0
        self._next_tick_s = 0.0
        self.syncs = 0  # reporting: sync ticks acted on

    def replicas(self, states: dict[str, TenantState], demands: dict[str, Demand]) -> dict[str, int]:
        """Each pool's count for this interval: a reconcile when a sync tick
        falls inside it, the current count otherwise."""
        now = self._cycle * model.CONTROL_INTERVAL_S
        self._cycle += 1
        if self._next_tick_s >= now + model.CONTROL_INTERVAL_S:
            return {tid: s.replicas for tid, s in states.items()}
        tick = self._next_tick_s
        self._next_tick_s += SYNC_PERIOD_S
        self.syncs += 1
        out = {}
        for tid, state in states.items():
            config = self.configs[tid]
            ratio = usage_ratio(state, demands.get(tid, Demand()), self.metric, self.target)
            new, self.histories[tid] = reconcile(self.histories[tid], tick, state.replicas, ratio,
                                                 config.replica_min, config.replica_max, self.behavior)
            out[tid] = new
        return out
