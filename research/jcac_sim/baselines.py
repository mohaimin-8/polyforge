"""Baseline controllers JCAC is compared against (W30).

Each baseline controls a strict subset of the knobs, or controls them all
but independently — the thesis claim is that per-layer local optima do not
compose into a joint optimum, so the interesting baseline is `layered`.

All baselines share the system model in model.py and the same clamped
transition (`apply_action`), so no baseline can cheat the guardrails.
"""

from __future__ import annotations

import json
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
    congestion,
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
            rho = demand.work_units(state.cache_mb, state.tier) / max(1, state.replicas * REPLICA_CAPACITY_WU)
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

            rho = demand.work_units(state.cache_mb, state.tier) / max(1, state.replicas * REPLICA_CAPACITY_WU)
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
        rho = demand.work_units(state.cache_mb, state.tier) / max(1, state.replicas * REPLICA_CAPACITY_WU)
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

            rho = demand.work_units(state.cache_mb, state.tier) / max(1, state.replicas * REPLICA_CAPACITY_WU)
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


class ConcurrencyController:
    """Concurrency/queue-depth autoscaler — the 2026 serving-stack shape.

    The production LLM-serving stack (Knative KPA's concurrency target;
    AIBrix/llm-d-class pod autoscalers; the K8s Gateway API Inference
    Extension's queue-depth signals) scales decode pools on **in-flight
    requests**, not CPU utilization. DEFENSE_QA #22 asked whether the
    joint controller's win survives against that shape; this arm makes it
    a measured baseline instead of an argument.

    Signal: in-system work by Little's law under the model's own
    congestion curve — L_total = λ·W ∝ ρ·g(ρ)·replicas — so the signal
    grows superlinearly as the pool saturates (queues blow up), unlike
    HPA's linear ρ. The KPA rule: desired = ceil(L_total / c_target).

    Two vendor-shaped behaviors, both tuned in the W34 grid:
    - `target_concurrency` c_t: per-replica in-flight work target (the
      container-concurrency analog; c_t = ρ·g(ρ) at the steady operating
      point, so c_t 0.5..4.0 spans ρ* 0.33..0.80 under the published
      a = 1 congestion form).
    - `stable_intervals`: scale-down stabilization — shrink only after
      this many consecutive below-target readings (KPA's stable window;
      scale-UP is immediate, the panic behavior).

    Cache and tier stay fixed: like every reactive baseline, this is a
    replica autoscaler with no concept of a semantic cache or model tier.
    """

    name = "concurrency"

    def __init__(self, configs: dict[str, TenantConfig],
                 target_concurrency: float = 1.5, stable_intervals: int = 3):
        self.configs = configs
        self.c_t = target_concurrency
        self.stable_intervals = max(1, int(stable_intervals))
        self._below = {tid: 0 for tid in configs}

    def plan(self, states: dict[str, TenantState], demands: dict[str, Demand]):
        out = {}
        for tid, state in states.items():
            config = self.configs[tid]
            demand = demands.get(tid, Demand())
            lam = demand.work_units(state.cache_mb, state.tier)
            rho = lam / max(1, state.replicas * REPLICA_CAPACITY_WU)
            in_flight = rho * congestion(lam, state.replicas) * state.replicas
            desired = (math.ceil(in_flight / self.c_t) if in_flight > 0
                       else config.replica_min)
            desired = max(config.replica_min, desired)

            if desired < state.replicas:
                # Stable window: a transient drain must persist before the
                # pool shrinks; burst valleys do not flap the deployment.
                self._below[tid] = self._below.get(tid, 0) + 1
                if self._below[tid] < self.stable_intervals:
                    desired = state.replicas
            else:
                self._below[tid] = 0

            delta = max(-2, min(2, desired - state.replicas))
            out[tid] = apply_action(config, state, delta, state.cache_mb, state.tier)
        return out


class VTCReplicaController:
    """Re-implementation of VTC's fair scheduler (Sheng et al., OSDI '24)
    restricted to the replica knob — hence "VTC-replica", the same
    reduction shape as FIRM-replica.

    VTC serves the client with the smallest *virtual token counter*
    (accumulated weighted service) first, dividing a fixed serving
    capacity fairly. At simulator scale the fixed capacity is the shared
    cluster replica pool, service is executed work (the same observable
    `interference_scores` uses), and weights are the tenants' budgets —
    the pricing analog of VTC's client weights:

    1. On every plan, each tenant's counter accrues the work it is about
       to receive under the standing allocation:
       min(offered work, replicas x capacity) / budget.
    2. Replicas are then re-divided: everyone gets replica_min, and the
       remaining pool is granted in ascending-counter order (least served
       first, VTC's rule) up to each tenant's utilization-target need.
    3. Moves pass the same +-2-per-interval clamp and `apply_action`
       guardrails every other controller obeys.

    Cache and tier stay fixed: like FIRM, VTC allocates serving capacity
    and has no concept of a semantic cache or a model tier. `target_rho`
    is the one tunable (W34 grid). The cluster pool arrives via
    `set_limits` (the engine calls it when the controller exposes it);
    without limits the pool is the sum of per-tenant maxima, i.e. the
    constraint simply never binds.
    """

    name = "vtc_replica"

    def __init__(self, configs: dict[str, TenantConfig], target_rho: float = 0.6):
        self.configs = configs
        self.target_rho = target_rho
        self.counters = {tid: 0.0 for tid in configs}
        self._pool: int | None = None

    def set_limits(self, limits) -> None:
        self._pool = int(limits.replicas)

    def plan(self, states: dict[str, TenantState], demands: dict[str, Demand]):
        pool = self._pool if self._pool is not None else sum(
            c.replica_max for c in self.configs.values())

        needs = {}
        for tid, state in states.items():
            config = self.configs[tid]
            offered = demands.get(tid, Demand()).work_units(state.cache_mb, state.tier)
            served = min(offered, state.replicas * REPLICA_CAPACITY_WU)
            weight = max(config.hourly_budget_usd, 1e-9)
            self.counters[tid] += served / weight
            need = math.ceil(offered / (REPLICA_CAPACITY_WU * self.target_rho)) if offered > 0 else 0
            needs[tid] = max(config.replica_min, min(config.replica_max, need))

        # Everyone keeps its floor; the rest of the pool goes least-served
        # first (ascending virtual counter), VTC's service order.
        grant = {tid: self.configs[tid].replica_min for tid in states}
        remaining = pool - sum(grant.values())
        for tid in sorted(states, key=lambda t: (self.counters[t], t)):
            if remaining <= 0:
                break
            extra = min(needs[tid] - grant[tid], remaining)
            if extra > 0:
                grant[tid] += extra
                remaining -= extra

        out = {}
        for tid, state in states.items():
            delta = max(-2, min(2, grant[tid] - state.replicas))
            out[tid] = apply_action(self.configs[tid], state, delta, state.cache_mb, state.tier)
        return out


# The learned joint controller optimizes the same cost/violation surface the
# MPC does; these are controller.COST_SCALE_USD (the shared cost normalizer)
# and the serving tiers a learner is allowed to route to ("none" is a
# designed AI outage the MPC only reaches as an infeasibility fallback, so
# the learned arm is not given it — an exclusion that favors the baseline).
_COST_SCALE_USD = 0.01
_SERVING_TIERS = ("small", "mid", "large")
_SLO_CLASSES = ("premium", "standard", "best-effort")  # matches SLO_CLASS_CYCLE

# Joint action lattice for the learned controller, ordered so index 0 is the
# no-op (hold) — the default an untrained state falls back to. Δreplicas is
# the ±2/interval clamp every controller obeys; Δcache/Δtier move one level.
_LEARNED_DR = (0, -1, 1, -2, 2)
_LEARNED_DC = (0, -1, 1)
_LEARNED_DT = (0, -1, 1)
_LEARNED_ACTIONS = tuple(
    (dr, dc, dt) for dr in _LEARNED_DR for dc in _LEARNED_DC for dt in _LEARNED_DT
)


class LearnedJointController:
    """Model-free RL over the FULL joint action space — the learned analog of
    PolyForge's MPC, and the direct joint-knob generalization of
    `FIRMReplicaController`.

    FIRM-replica (above) showed that tabular Q-learning can drive **one**
    knob (replicas). PolyForge's thesis is that the interesting problem is
    *joint* cross-layer coordination — replicas, semantic cache, and model
    tier moved together against one objective — which it solves by MPC
    (`controller.py`). The obvious 2026 reviewer question is whether the
    hand-designed optimizer is necessary at all: could a **learned** policy
    discover the joint coordination on its own? This controller is that
    question made measurable. It uses exactly FIRM's machinery — per-tenant
    tabular Q-learning, ε-greedy, seeded, learning online during the run —
    but over the same ≤60-candidate joint lattice the MPC enumerates:

        action u = (Δreplicas ∈ {-2,-1,0,+1,+2},
                    Δcache_level ∈ {-1,0,+1}   (one level per interval),
                    Δtier ∈ {-1,0,+1}          over small/mid/large)

    ordered so index 0 is *hold* — an untrained state defaults to holding
    its configuration rather than exploring into an outage, a fair prior.

    Reward mirrors the objective the MPC minimizes and every baseline is
    tuned on — cost and SLO overshoot on the same scales
    (`_COST_SCALE_USD`, `log1p(excess)` exactly as `JCACController._project`
    uses) — so the learner is optimizing the paper's surface, not a proxy:

        r = -(reward_alpha · cost_norm + reward_beta · log1p(excess))

    The fairness (γ·(1−Jain)) term is cross-tenant and, like FIRM, is not in
    the per-tenant reward; the resulting Jain is measured, not assumed.

    To make this a *strong* learned arm rather than a straw man, it gets
    experience replay (the standard sample-efficiency tool for model-free
    RL): each step replays `replay_batch` past transitions from a bounded
    buffer, so a short deployment episode yields many more Q-updates than
    online SARSA alone. Its exploration/learning knobs are swept in the same
    W34 grid as every other baseline. What the experiment then measures is
    whether that machinery, given the joint action space, recovers the
    cross-layer coordination the MPC computes with **zero** learning — the
    scientific point is the comparison, whichever way it falls.

    Cache/tier bounds and the ±1-cache-level thrash rule are enforced by
    construction; replica bounds by the shared `apply_action`. Like every
    reactive baseline it evicts LRU (no W28 cost-aware eviction) and, like
    the FIRM/HPA/KEDA family, does not itself honor cluster caps (the
    v3-disclosed asymmetry that is strict against PolyForge). Seeded, so the
    harness's per-rep seed makes runs reproducible.

    Two deployment shapes (PREREG_LEARNED_CONTROL):
    - `shared=True` (default): one **tenant-agnostic** Q-table keyed by the
      discretized state — which *includes the SLO class* — so every tenant's
      transitions pool into one policy that transfers across episodes and
      tenant counts. This is the table an offline trainer fills and freezes;
      `save_q`/`load_q` persist it as a committed artifact (the RL analog of
      `cmd/classifier-train`'s model). Deploy it with `epsilon=0` for a
      frozen greedy policy.
    - `shared=False`: FIRM-style per-tenant tables learned online within the
      run — the pure-online ablation, which is data-starved over the joint
      space by design (that contrast is part of what the campaign measures).
    """

    name = "learned"
    _actions = _LEARNED_ACTIONS  # index 0 == (0,0,0) == hold

    def __init__(
        self,
        configs: dict[str, TenantConfig],
        learning_rate: float = 0.3,
        discount: float = 0.7,
        epsilon: float = 0.1,
        reward_alpha: float = 1.0,
        reward_beta: float = 2.0,
        replay_batch: int = 8,
        replay_size: int = 2000,
        shared: bool = True,
        train: bool = True,
        qtable_path: str | None = None,
        seed: int = 0,
    ):
        self.configs = configs
        self.lr = learning_rate
        self.discount = discount
        self.epsilon = epsilon
        self.reward_alpha = reward_alpha
        self.reward_beta = reward_beta
        self.replay_batch = max(0, int(replay_batch))
        self.replay_size = max(1, int(replay_size))
        self.shared = shared
        # train=False is the frozen deployment: pure greedy inference from the
        # loaded table, no online updates — the committed artifact fully
        # determines behavior (and the run is reproducible from it alone).
        self.train = train
        self.rng = random.Random(seed)
        # Shared: one state-keyed table for all tenants. Per-tenant: q[tid].
        self._shared_q: dict[tuple, list[float]] = {}
        self._tenant_q: dict[str, dict[tuple, list[float]]] = {tid: {} for tid in configs}
        self._last: dict[str, tuple[tuple, int]] = {}
        self._buffer: list[tuple[str, tuple, int, float, tuple]] = []
        if qtable_path:
            self.load_q(qtable_path)

    def _table(self, tid: str) -> dict[tuple, list[float]]:
        return self._shared_q if self.shared else self._tenant_q[tid]

    def _row(self, tid: str, s: tuple) -> list[float]:
        return self._table(tid).setdefault(s, [0.0] * len(self._actions))

    def save_q(self, path: str) -> None:
        """Persist the shared policy as JSON: a list of [state_list, q_row]
        pairs (tuple keys are not JSON-native). Only the shared table is a
        deployable artifact; per-tenant tables are run-local."""
        rows = [[list(s), row] for s, row in sorted(self._shared_q.items())]
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"actions": len(self._actions), "q": rows}, f)

    def load_q(self, path: str) -> None:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if int(data.get("actions", len(self._actions))) != len(self._actions):
            raise ValueError("qtable action count does not match this controller")
        self._shared_q = {tuple(s): [float(v) for v in row] for s, row in data["q"]}

    def _observe(self, config: TenantConfig, state: TenantState, demand: Demand):
        """Discretized joint state + the immediate reward of the transition
        that produced `state`. The state carries what each knob needs:
        the SLO class (so a shared policy conditions on the target),
        utilization (replicas), AI tail pressure (tier), cacheable intensity
        (cache), plus the current cache/tier index because moves are
        relative."""
        slo_b = _SLO_CLASSES.index(config.slo_class) if config.slo_class in _SLO_CLASSES else 1
        lam = demand.work_units(state.cache_mb, state.tier)
        rho = lam / max(1, state.replicas * REPLICA_CAPACITY_WU)
        rho_b = min(9, int(rho * 5.0))  # 0..9 in ρ steps of 0.2 (FIRM's bucketing)
        m = evaluate_step(config, state, demand)
        viol_b = 0 if m.violation <= 0.0 else (1 if m.violation < 0.5 else 2)

        ai_rps = sum(demand.rps.get(k, 0.0) for k in AI_KINDS)
        if ai_rps > 0.0:
            target = SLO_BASE_MS["ai"] * SLO_CLASS_FACTOR[config.slo_class]
            ratio = m.ai_p95_ms / target if target > 0 else 0.0
            ai_b = 0 if ratio < 0.5 else (1 if ratio <= 1.0 else 2)
        else:
            ai_b = 0
        cacheable = sum(demand.rps.get(k, 0.0) * CACHEABLE_FRACTION[k] for k in AI_KINDS)
        cache_load_b = 0 if cacheable < 0.05 else (1 if cacheable < 2.0 else 2)

        cache_idx = CACHE_LEVELS_MB.index(state.cache_mb) if state.cache_mb in CACHE_LEVELS_MB else 0
        tier_idx = _SERVING_TIERS.index(state.tier) if state.tier in _SERVING_TIERS else 0

        s = (slo_b, rho_b, viol_b, ai_b, cache_load_b, cache_idx, tier_idx)
        reward = -(self.reward_alpha * m.cost_usd / _COST_SCALE_USD
                   + self.reward_beta * math.log1p(m.excess))
        return s, reward

    def _learn(self, tid: str, prev_s: tuple, prev_a: int, reward: float, s: tuple) -> None:
        table = self._table(tid)
        prev_row = table.setdefault(prev_s, [0.0] * len(self._actions))
        row = table.setdefault(s, [0.0] * len(self._actions))
        prev_row[prev_a] += self.lr * (reward + self.discount * max(row) - prev_row[prev_a])

    def _replay(self) -> None:
        if self.replay_batch == 0 or not self._buffer:
            return
        for _ in range(self.replay_batch):
            tid, prev_s, prev_a, reward, s = self._buffer[self.rng.randrange(len(self._buffer))]
            self._learn(tid, prev_s, prev_a, reward, s)

    def _apply(self, config: TenantConfig, state: TenantState, action: tuple) -> TenantState:
        dr, dc, dt = action
        ci = CACHE_LEVELS_MB.index(state.cache_mb) if state.cache_mb in CACHE_LEVELS_MB else 0
        ci = max(0, min(len(CACHE_LEVELS_MB) - 1, ci + dc))
        ti = _SERVING_TIERS.index(state.tier) if state.tier in _SERVING_TIERS else 0
        ti = max(0, min(len(_SERVING_TIERS) - 1, ti + dt))
        return apply_action(config, state, dr, CACHE_LEVELS_MB[ci], _SERVING_TIERS[ti])

    def plan(self, states: dict[str, TenantState], demands: dict[str, Demand]):
        out = {}
        for tid, state in states.items():
            config = self.configs[tid]
            demand = demands.get(tid, Demand())
            s, reward = self._observe(config, state, demand)
            row = self._row(tid, s)

            if self.train:
                # Learn from the previous transition (Q-learning bootstrap on
                # the greedy value), then remember it for replay.
                if tid in self._last:
                    prev_s, prev_a = self._last[tid]
                    self._learn(tid, prev_s, prev_a, reward, s)
                    self._buffer.append((tid, prev_s, prev_a, reward, s))
                    if len(self._buffer) > self.replay_size:
                        self._buffer.pop(0)

            if self.train and self.rng.random() < self.epsilon:
                a = self.rng.randrange(len(self._actions))
            else:
                a = row.index(max(row))  # ties → index 0 == hold
            self._last[tid] = (s, a)
            out[tid] = self._apply(config, state, self._actions[a])
        if self.train:
            self._replay()
        return out


BASELINES = (
    StaticController,
    HPAController,
    KEDAController,
    LayeredController,
    FIRMReplicaController,
    GPTCacheLRUController,
    VTCReplicaController,
    ConcurrencyController,
    LearnedJointController,
)


def make_baseline(name: str, configs: dict[str, TenantConfig], **params):
    """Instantiate a baseline by name. `params` are the controller's tuning
    knobs (W34 grid search sweeps them); unknown names fail loudly."""
    for cls in BASELINES:
        if cls.name == name:
            return cls(configs, **params)
    raise ValueError(f"unknown baseline {name!r}")
