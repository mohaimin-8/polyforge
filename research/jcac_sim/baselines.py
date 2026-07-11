"""Baseline controllers JCAC is compared against (W30).

Each baseline controls a strict subset of the knobs, or controls them all
but independently — the thesis claim is that per-layer local optima do not
compose into a joint optimum, so the interesting baseline is `layered`.

All baselines share the system model in model.py and the same clamped
transition (`apply_action`), so no baseline can cheat the guardrails.
"""

from __future__ import annotations

import math
import random

from model import (
    AI_KINDS,
    CACHE_LEVELS_MB,
    CACHEABLE_FRACTION,
    REPLICA_CAPACITY_WU,
    SLO_BASE_MS,
    SLO_CLASS_FACTOR,
    Demand,
    TenantConfig,
    TenantState,
    apply_action,
    evaluate_step,
    hit_rate,
)


class StaticController:
    """Fixed configuration. Default: the do-nothing floor (initial states).
    `overprovisioned=True` is the W34 baseline sense — every tenant pinned
    at replica_max with a large cache and the mid tier, i.e. provisioned to
    peak so it never violates but always pays.
    `fixed_replicas=n` is the v2 iso-cost sense (PREREG_V2 §5): the same
    posture but pinned at n replicas per tenant, where n is derived from
    PolyForge's realized spend — same never-adapts behavior, matched budget."""

    name = "static"

    def __init__(self, configs: dict[str, TenantConfig], overprovisioned: bool = False,
                 fixed_replicas: int | None = None):
        self.configs = configs
        self.overprovisioned = overprovisioned
        self.fixed_replicas = fixed_replicas

    def plan(self, states: dict[str, TenantState], demands: dict[str, Demand]):
        if not self.overprovisioned and self.fixed_replicas is None:
            return dict(states)
        return {
            tid: TenantState(
                replicas=(self.configs[tid].replica_max if self.fixed_replicas is None
                          else max(1, min(self.configs[tid].replica_max, self.fixed_replicas))),
                cache_mb=CACHE_LEVELS_MB[-1],
                tier="mid",
            )
            for tid in states
        }


class HPAController:
    """Kubernetes HPA semantics on utilization only: desired = ceil(current ·
    ρ/ρ_target), clamped to ±2 per interval. Cache and tier stay fixed."""

    name = "hpa"

    def __init__(self, configs: dict[str, TenantConfig], target_rho: float = 0.6):
        self.configs = configs
        self.target_rho = target_rho

    def plan(self, states: dict[str, TenantState], demands: dict[str, Demand]):
        out = {}
        for tid, state in states.items():
            config = self.configs[tid]
            demand = demands.get(tid, Demand())
            rho = demand.work_units(state.cache_mb) / max(1, state.replicas * REPLICA_CAPACITY_WU)
            desired = math.ceil(state.replicas * rho / self.target_rho) if rho > 0 else config.replica_min
            delta = max(-2, min(2, desired - state.replicas))
            out[tid] = apply_action(config, state, delta, state.cache_mb, state.tier)
        return out


class KEDAController:
    """Event-driven scaling on raw request rate (KEDA's rps scaler):
    replicas = ceil(rps / per-replica target). Cache and tier stay fixed."""

    name = "keda"

    def __init__(self, configs: dict[str, TenantConfig], rps_per_replica: float = 8.0):
        self.configs = configs
        self.rps_per_replica = rps_per_replica

    def plan(self, states: dict[str, TenantState], demands: dict[str, Demand]):
        out = {}
        for tid, state in states.items():
            config = self.configs[tid]
            rps = demands.get(tid, Demand()).total_rps()
            desired = max(config.replica_min, math.ceil(rps / self.rps_per_replica))
            delta = max(-2, min(2, desired - state.replicas))
            out[tid] = apply_action(config, state, delta, state.cache_mb, state.tier)
        return out


class LayeredController:
    """Every knob has its own well-tuned local controller, none of them
    talk to each other, and none of them see cost or fairness:

    - replicas: HPA rule (utilization target)
    - cache: grow a level when there is meaningful cacheable traffic and
      headroom in the hit-rate curve, shrink when the cache is idle
    - tier: reactive rule on last observed AI p95 vs the tenant's target

    This is the strongest realistic non-joint baseline — it is what a
    competent team gets from stock components without a joint optimizer.
    """

    name = "layered"

    def __init__(self, configs: dict[str, TenantConfig], target_rho: float = 0.6):
        self.configs = configs
        self.target_rho = target_rho

    def plan(self, states: dict[str, TenantState], demands: dict[str, Demand]):
        out = {}
        for tid, state in states.items():
            config = self.configs[tid]
            demand = demands.get(tid, Demand())

            rho = demand.work_units(state.cache_mb) / max(1, state.replicas * REPLICA_CAPACITY_WU)
            desired = math.ceil(state.replicas * rho / self.target_rho) if rho > 0 else config.replica_min
            delta = max(-2, min(2, desired - state.replicas))

            cache_mb = self._cache_rule(state, demand)
            tier = self._tier_rule(config, state, demand)
            out[tid] = apply_action(config, state, delta, cache_mb, tier)
        return out

    def _cache_rule(self, state: TenantState, demand: Demand) -> int:
        cacheable_rps = sum(
            demand.rps.get(k, 0.0) * CACHEABLE_FRACTION[k] for k in AI_KINDS
        )
        i = CACHE_LEVELS_MB.index(state.cache_mb) if state.cache_mb in CACHE_LEVELS_MB else 0
        if cacheable_rps > 0.5 and hit_rate(state.cache_mb) < 0.7 and i < len(CACHE_LEVELS_MB) - 1:
            return CACHE_LEVELS_MB[i + 1]
        if cacheable_rps < 0.05 and i > 0:
            return CACHE_LEVELS_MB[i - 1]
        return state.cache_mb

    def _tier_rule(self, config: TenantConfig, state: TenantState, demand: Demand) -> str:
        ai_rps = sum(demand.rps.get(k, 0.0) for k in AI_KINDS)
        if ai_rps <= 0.0:
            return state.tier
        target = SLO_BASE_MS["ai"] * SLO_CLASS_FACTOR[config.slo_class]
        metrics = evaluate_step(config, state, demand)
        order = ["small", "mid", "large"]
        current = state.tier if state.tier in order else "small"
        i = order.index(current)
        if metrics.ai_p95_ms > target and i < len(order) - 1:
            return order[i + 1]
        if metrics.ai_p95_ms < 0.3 * target and i > 0:
            return order[i - 1]
        return current


class FIRMReplicaController:
    """Re-implementation of FIRM's RL resource controller (Qiu et al.,
    OSDI '20) restricted to the replica knob — hence "FIRM-replica".

    FIRM detects SLO violations and lets a per-service RL agent choose a
    scaling action from telemetry; we keep the same shape at simulator
    scale: per-tenant tabular Q-learning over a discretized
    (utilization-bucket, violating?) state, actions Δreplicas ∈ [-2, 2],
    ε-greedy online learning. The reward mirrors FIRM's objective — meet
    the SLO first, then hand resources back:

        r = −w_slo · violation − w_util · |ρ − ρ_target|

    Cache and tier stay fixed: FIRM manages compute allocation, it has no
    concept of a semantic cache or a model tier. Seeded, so runs are
    reproducible; the harness varies the seed per repetition.
    """

    name = "firm"
    _actions = (-2, -1, 0, 1, 2)

    def __init__(
        self,
        configs: dict[str, TenantConfig],
        learning_rate: float = 0.3,
        discount: float = 0.7,
        epsilon: float = 0.1,
        w_slo: float = 2.0,
        w_util: float = 0.5,
        target_rho: float = 0.6,
        seed: int = 0,
    ):
        self.configs = configs
        self.lr = learning_rate
        self.discount = discount
        self.epsilon = epsilon
        self.w_slo = w_slo
        self.w_util = w_util
        self.target_rho = target_rho
        self.rng = random.Random(seed)
        # q[tenant][(rho_bucket, violating)][action_index]
        self.q: dict[str, dict[tuple[int, bool], list[float]]] = {
            tid: {} for tid in configs
        }
        self._last: dict[str, tuple[tuple[int, bool], int]] = {}

    def _observe(self, config: TenantConfig, state: TenantState, demand: Demand):
        rho = demand.work_units(state.cache_mb) / max(1, state.replicas * REPLICA_CAPACITY_WU)
        m = evaluate_step(config, state, demand)
        bucket = min(9, int(rho * 5.0))  # 0..9 in ρ steps of 0.2
        s = (bucket, m.violation > 0.0)
        reward = -self.w_slo * m.violation - self.w_util * abs(rho - self.target_rho)
        return s, reward

    def plan(self, states: dict[str, TenantState], demands: dict[str, Demand]):
        out = {}
        for tid, state in states.items():
            config = self.configs[tid]
            demand = demands.get(tid, Demand())
            s, reward = self._observe(config, state, demand)
            table = self.q[tid]
            row = table.setdefault(s, [0.0] * len(self._actions))

            # Learn from the previous transition (SARSA-style bootstrap on
            # the greedy value, i.e. Q-learning).
            if tid in self._last:
                prev_s, prev_a = self._last[tid]
                prev_row = table.setdefault(prev_s, [0.0] * len(self._actions))
                prev_row[prev_a] += self.lr * (
                    reward + self.discount * max(row) - prev_row[prev_a]
                )

            if self.rng.random() < self.epsilon:
                a = self.rng.randrange(len(self._actions))
            else:
                best = max(row)
                a = row.index(best)
            self._last[tid] = (s, a)
            out[tid] = apply_action(config, state, self._actions[a], state.cache_mb, state.tier)
        return out


class GPTCacheLRUController:
    """GPTCache in front of a stock autoscaled deployment: the strongest
    cache-centric baseline. GPTCache's default posture is "cache
    everything, evict LRU", so the cache grows toward the largest level
    whenever cacheable AI traffic exists and only shrinks when idle.
    Replicas follow the HPA rule; the tier follows the same reactive rule
    as `layered` (a fixed tier would straw-man it). What it lacks is
    cost-aware eviction (W28) and any joint reasoning — the harness scores
    its inference spend with the measured LRU miss-cost factor.
    """

    name = "gptcache"

    def __init__(self, configs: dict[str, TenantConfig], target_rho: float = 0.6,
                 fixed_cache_mb: int | None = None):
        self.configs = configs
        self.target_rho = target_rho
        # v2 iso-cost sense (PREREG_V2 §5): same posture, but the cache is
        # pinned at a level derived from PolyForge's realized cache spend
        # instead of growing toward the maximum.
        self.fixed_cache_mb = fixed_cache_mb

    def plan(self, states: dict[str, TenantState], demands: dict[str, Demand]):
        out = {}
        for tid, state in states.items():
            config = self.configs[tid]
            demand = demands.get(tid, Demand())

            rho = demand.work_units(state.cache_mb) / max(1, state.replicas * REPLICA_CAPACITY_WU)
            desired = math.ceil(state.replicas * rho / self.target_rho) if rho > 0 else config.replica_min
            delta = max(-2, min(2, desired - state.replicas))

            if self.fixed_cache_mb is not None:
                cache_mb = self.fixed_cache_mb
            else:
                cacheable_rps = sum(
                    demand.rps.get(k, 0.0) * CACHEABLE_FRACTION[k] for k in AI_KINDS
                )
                i = CACHE_LEVELS_MB.index(state.cache_mb) if state.cache_mb in CACHE_LEVELS_MB else 0
                if cacheable_rps > 0.0 and i < len(CACHE_LEVELS_MB) - 1:
                    cache_mb = CACHE_LEVELS_MB[i + 1]  # cache everything
                elif cacheable_rps <= 0.0 and i > 0:
                    cache_mb = CACHE_LEVELS_MB[i - 1]
                else:
                    cache_mb = state.cache_mb

            tier = self._tier_rule(config, state, demand)
            out[tid] = apply_action(config, state, delta, cache_mb, tier)
        return out

    def _tier_rule(self, config: TenantConfig, state: TenantState, demand: Demand) -> str:
        ai_rps = sum(demand.rps.get(k, 0.0) for k in AI_KINDS)
        if ai_rps <= 0.0:
            return state.tier
        target = SLO_BASE_MS["ai"] * SLO_CLASS_FACTOR[config.slo_class]
        metrics = evaluate_step(config, state, demand)
        order = ["small", "mid", "large"]
        current = state.tier if state.tier in order else "small"
        i = order.index(current)
        if metrics.ai_p95_ms > target and i < len(order) - 1:
            return order[i + 1]
        if metrics.ai_p95_ms < 0.3 * target and i > 0:
            return order[i - 1]
        return current


BASELINES = (
    StaticController,
    HPAController,
    KEDAController,
    LayeredController,
    FIRMReplicaController,
    GPTCacheLRUController,
)


def make_baseline(name: str, configs: dict[str, TenantConfig], **params):
    """Instantiate a baseline by name. `params` are the controller's tuning
    knobs (W34 grid search sweeps them); unknown names fail loudly."""
    for cls in BASELINES:
        if cls.name == name:
            return cls(configs, **params)
    raise ValueError(f"unknown baseline {name!r}")
