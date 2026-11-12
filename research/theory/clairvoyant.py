"""Reference points (programme 2, WP1): what any controller could achieve.

For one run's world (tenants, their jittered demand, the cluster's caps) and
a per-tenant-step objective

    w_i,t(s) = (cost + refused requests x price_out) / c0 + 2 x violation

of holding state s = (replicas, cache level, tier) against the demand that
arrives after step t (the engine's scoring convention), compute:

    LB0     sum over tenants and steps of the cheapest state, no move
            limits, no caps: a lower bound for EVERY controller
    LB1     per tenant, the best path under the planner's move limits
            (+-2 replicas, +-1 cache level, any tier per step), free start,
            no caps: a lower bound for every controller that obeys the limits
    LB2     LB1 with the cluster caps (replicas and cache summed over tenants
            at every step) dualised by per-step Lagrange multipliers,
            maximised by subgradient ascent: a lower bound on the capped
            clairvoyant optimum
    UB      the best capped-feasible plan the ascent's primal heuristic finds,
            starting within one move of `start` (the simulator's initial
            state: 2 replicas, 128 MB), scored with the FULL objective
            including 0.5 x (1 - mean Jain): an achievable value for a
            controller with the planner's move limits, so the capped
            clairvoyant optimum from that start is <= UB
    STATIC  the best fixed state per tenant inside an equal share of the caps,
            held from the first step (as the simulator's static arm does),
            scored with the full objective

The lower bounds use a free start and no tier restriction, so they hold for
any start state and any per-tenant knob bounds an arm adds.

Every lower bound drops the fairness term, which is non-negative. All values
are per tenant-step: sums are divided by N x T.
"""

from __future__ import annotations

import itertools
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import numpy as np

_SIM = Path(__file__).resolve().parents[1] / "jcac_sim"
if str(_SIM) not in sys.path:
    sys.path.insert(0, str(_SIM))

import model  # noqa: E402
from model import CACHE_LEVELS_MB, TIERS, TenantState, evaluate_step, jain_index  # noqa: E402

C0 = 0.01  # controller.COST_SCALE_USD
VIOLATION_WEIGHT = 2.0
FAIRNESS_WEIGHT = 0.5
R_RADIUS, C_RADIUS = 2, 1  # the planner's per-interval move limits


# --------------------------------------------------------------------------
# Single-tenant path problems on a (replicas, cache, tier) grid
# --------------------------------------------------------------------------

def _shift(a: np.ndarray, d: int, axis: int) -> np.ndarray:
    """b[..., i, ...] = a[..., i + d, ...] along `axis`, +inf outside."""
    out = np.full_like(a, np.inf)
    n = a.shape[axis]
    if abs(d) >= n:
        return out
    src = [slice(None)] * a.ndim
    dst = [slice(None)] * a.ndim
    if d >= 0:
        src[axis], dst[axis] = slice(d, n), slice(0, n - d)
    else:
        src[axis], dst[axis] = slice(0, n + d), slice(-d, n)
    out[tuple(dst)] = a[tuple(src)]
    return out


def window_min(a: np.ndarray, r_axis: int = -2, c_axis: int = -1) -> np.ndarray:
    """min over |dr| <= R_RADIUS, |dc| <= C_RADIUS of a[.., r + dr, c + dc]."""
    tmp = np.full_like(a, np.inf)
    for dr in range(-R_RADIUS, R_RADIUS + 1):
        tmp = np.minimum(tmp, _shift(a, dr, r_axis))
    out = np.full_like(a, np.inf)
    for dc in range(-C_RADIUS, C_RADIUS + 1):
        out = np.minimum(out, _shift(tmp, dc, c_axis))
    return out


def dp_values(w: np.ndarray) -> np.ndarray:
    """Cost-to-go V[..., t, r, c, k] of the best path from step t holding
    state (r, c, k) at t, moves limited between consecutive steps, any tier.
    `w` has shape (..., T, R, C, K) (leading axes: independent problems
    solved together); +inf marks a state that may not be held."""
    T = w.shape[-4]
    V = np.empty_like(w)
    V[..., T - 1, :, :, :] = w[..., T - 1, :, :, :]
    for t in range(T - 2, -1, -1):
        nxt = window_min(V[..., t + 1, :, :, :].min(axis=-1))
        V[..., t, :, :, :] = w[..., t, :, :, :] + nxt[..., None]
    return V


def best_path(V: np.ndarray) -> list[tuple[int, int, int]]:
    """The minimising path (free start), as (r, c, k) indices per step;
    ties keep the first index in C order."""
    T, R, C, K = V.shape
    path = [np.unravel_index(int(np.argmin(V[0])), (R, C, K))]
    for t in range(1, T):
        r, c, _ = path[-1]
        r0, r1 = max(0, r - R_RADIUS), min(R, r + R_RADIUS + 1)
        c0, c1 = max(0, c - C_RADIUS), min(C, c + C_RADIUS + 1)
        block = V[t, r0:r1, c0:c1, :]
        i = np.unravel_index(int(np.argmin(block)), block.shape)
        path.append((r0 + i[0], c0 + i[1], i[2]))
    return [tuple(int(v) for v in p) for p in path]


def path_value(w: np.ndarray, path: Sequence[tuple[int, int, int]]) -> float:
    return float(sum(w[t][p] for t, p in enumerate(path)))


def moves_ok(path: Sequence[tuple[int, int, int]]) -> bool:
    return all(abs(a[0] - b[0]) <= R_RADIUS and abs(a[1] - b[1]) <= C_RADIUS
               for a, b in zip(path, path[1:]))


# --------------------------------------------------------------------------
# A run's world as arrays
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class TenantArrays:
    tenant: str
    replicas: np.ndarray  # (R,) replica count of each r index
    cache_mb: np.ndarray  # (C,) MB of each c index
    cost: np.ndarray  # (T, R, C, K) USD per tenant-step
    shed: np.ndarray  # (T, R, C, K) refused AI requests
    violation: np.ndarray  # (T, R, C, K) capped violation in [0, 1]

    def weight(self, price_out: float) -> np.ndarray:
        """w = (cost + shed x price_out) / c0 + 2 x violation; +inf where the
        tenant's bounds do not admit the state (none in published worlds)."""
        return (self.cost + self.shed * price_out) / C0 + VIOLATION_WEIGHT * self.violation


@dataclass(frozen=True)
class World:
    tenants: list[TenantArrays]
    cap_replicas: int
    cap_cache_mb: int
    steps: int


def tenant_arrays(tid: str, config, demands: Sequence) -> TenantArrays:
    """Evaluate every admitted state of one tenant against every scored
    demand with the simulator's own `evaluate_step`."""
    replicas = np.arange(config.replica_min, config.replica_max + 1)
    cache = np.array(CACHE_LEVELS_MB)
    T, R, C, K = len(demands), len(replicas), len(cache), len(TIERS)
    cost = np.full((T, R, C, K), np.inf)
    shed = np.zeros((T, R, C, K))
    viol = np.zeros((T, R, C, K))
    for ci, c in enumerate(cache):
        for ki, tier in enumerate(TIERS):
            if not config.knob_admits(int(c), tier):
                continue
            for ri, r in enumerate(replicas):
                state = TenantState(replicas=int(r), cache_mb=int(c), tier=tier)
                for t, d in enumerate(demands):
                    m = evaluate_step(config, state, d)
                    cost[t, ri, ci, ki] = m.cost_usd
                    shed[t, ri, ci, ki] = m.ai_shed_requests
                    viol[t, ri, ci, ki] = m.violation
    return TenantArrays(tid, replicas, cache, cost, shed, viol)


def world_from_harness(workload: str, tenant_mix: str, cluster_size: str, seed: int,
                       steps: int, economy: tuple = (), model_form: tuple = (),
                       transition_costs: bool = False, interference: bool = False) -> World:
    """The world `harness.sim_backend.execute` builds for a run, with the same
    Poisson jitter. Scored demand is buckets[1:] (the engine scores a plan
    made on bucket t against bucket t + 1).

    Refuses the engine options under which the served state is not the
    planned one (startup lag, injected interference): the arrays score the
    planned state. The model's economy and form are restored to the
    published ones before returning, whatever happens."""
    if transition_costs or interference:
        raise ValueError("the arrays score the planned state; runs with transition costs "
                         "or interference serve a different one")
    eval_dir = Path(__file__).resolve().parents[2] / "eval"
    if str(eval_dir) not in sys.path:
        sys.path.insert(0, str(eval_dir))
    import simulate
    from harness import sim_backend, workloads

    try:
        sim_backend._apply_economy(economy)
        sim_backend._apply_model_form(model_form)
        tenant_ids, buckets, configs, limits = workloads.build(workload, tenant_mix, cluster_size, seed, steps)
        buckets = simulate.jitter_buckets(buckets, seed ^ sim_backend._JITTER_SALT)
        scored = buckets[1:]
        tenants = [tenant_arrays(tid, configs[tid], [b[tid] for b in scored]) for tid in tenant_ids]
    finally:
        model.set_economy()
        model.set_model_form()
    return World(tenants, limits.replicas, limits.cache_mb, len(scored))


def world_for_run(run) -> World:
    """`world_from_harness` for a harness RunSpec (chaos settings live in the
    arm, not the run; the arrays are arm-independent)."""
    return world_from_harness(run.workload, run.tenant_mix, run.cluster_size, run.seed, run.steps,
                              economy=run.economy, model_form=run.model_form,
                              transition_costs=run.transition_costs, interference=run.interference)


# --------------------------------------------------------------------------
# Plans, scoring, bounds
# --------------------------------------------------------------------------

def plan_objective(world: World, plan: Sequence[Sequence[tuple[int, int, int]]],
                   price_out: float) -> float:
    """The full per-tenant-step objective of a plan (one path per tenant),
    with the fairness term: what the harness would score."""
    n, T = len(world.tenants), world.steps
    total_w, jain_sum = 0.0, 0.0
    for t in range(T):
        sats = []
        for ten, path in zip(world.tenants, plan):
            idx = (t, *path[t])
            total_w += float((ten.cost[idx] + ten.shed[idx] * price_out) / C0
                             + VIOLATION_WEIGHT * ten.violation[idx])
            sats.append(1.0 - float(ten.violation[idx]))
        jain_sum += jain_index(sats)
    return total_w / (n * T) + FAIRNESS_WEIGHT * (1.0 - jain_sum / T)


def usage(world: World, plan) -> tuple[np.ndarray, np.ndarray]:
    reps = np.zeros(world.steps)
    cache = np.zeros(world.steps)
    for ten, path in zip(world.tenants, plan):
        for t, (r, c, _) in enumerate(path):
            reps[t] += ten.replicas[r]
            cache[t] += ten.cache_mb[c]
    return reps, cache


def lb0(world: World, price_out: float) -> float:
    n, T = len(world.tenants), world.steps
    return float(sum(ten.weight(price_out).reshape(T, -1).min(axis=1).sum()
                     for ten in world.tenants)) / (n * T)


def lb1(world: World, price_out: float) -> tuple[float, list]:
    n, T = len(world.tenants), world.steps
    total, plan = 0.0, []
    for ten in world.tenants:
        V = dp_values(ten.weight(price_out))
        total += float(V[0].min())
        plan.append(best_path(V))
    return total / (n * T), plan


def start_mask(ten: TenantArrays, start: tuple[int, int] | None) -> np.ndarray:
    """(R, C) states a controller may hold at the first step when it starts
    at `start` = (replicas, cache MB): one move away. None: every state."""
    R, C = len(ten.replicas), len(ten.cache_mb)
    if start is None:
        return np.ones((R, C), dtype=bool)
    r0 = np.flatnonzero(ten.replicas == start[0])
    c0 = np.flatnonzero(ten.cache_mb == start[1])
    if r0.size == 0 or c0.size == 0:
        raise ValueError(f"start {start} is not on the tenant's grid")
    rr = np.abs(np.arange(R) - r0[0]) <= R_RADIUS
    cc = np.abs(np.arange(C) - c0[0]) <= C_RADIUS
    return rr[:, None] & cc[None, :]


def floor_path(ten: TenantArrays, steps: int, start: tuple[int, int] | None) -> tuple[np.ndarray, np.ndarray]:
    """The smallest replicas and cache a tenant can hold at each step when it
    starts at `start` and descends as fast as the move limits allow (the
    lowest grid point at every step when there is no start)."""
    t = np.arange(steps)
    if start is None:
        return np.full(steps, float(ten.replicas[0])), np.full(steps, float(ten.cache_mb[0]))
    r0 = int(np.flatnonzero(ten.replicas == start[0])[0])
    c0 = int(np.flatnonzero(ten.cache_mb == start[1])[0])
    ri = np.maximum(0, r0 - R_RADIUS * (t + 1))
    ci = np.maximum(0, c0 - C_RADIUS * (t + 1))
    return ten.replicas[ri].astype(float), ten.cache_mb[ci].astype(float)


def _sequential_feasible(world: World, wt: list[np.ndarray], order: Sequence[int],
                         start: tuple[int, int] | None = None) -> list:
    """Primal heuristic: tenants in `order` each take their best path inside
    the capacity the earlier ones left, starting within one move of `start`
    and reserving, at every step, the floor path of every later tenant.
    Always capped-feasible and finite: by induction each tenant's own floor
    path fits what remains."""
    T = world.steps
    rem_r = np.full(T, float(world.cap_replicas))
    rem_c = np.full(T, float(world.cap_cache_mb))
    floors = [floor_path(ten, T, start) for ten in world.tenants]
    plan = [None] * len(world.tenants)
    for pos, i in enumerate(order):
        ten = world.tenants[i]
        later = order[pos + 1:]
        reserve_r = sum((floors[j][0] for j in later), np.zeros(T))
        reserve_c = sum((floors[j][1] for j in later), np.zeros(T))
        allow_r = ten.replicas[None, :] <= (rem_r - reserve_r)[:, None]  # (T, R)
        allow_c = ten.cache_mb[None, :] <= (rem_c - reserve_c)[:, None]  # (T, C)
        mask = allow_r[:, :, None, None] & allow_c[:, None, :, None]
        mask[0] &= start_mask(ten, start)[:, :, None]
        w = np.where(mask, wt[i], np.inf)
        path = best_path(dp_values(w))
        plan[i] = path
        for t, (r, c, _) in enumerate(path):
            rem_r[t] -= ten.replicas[r]
            rem_c[t] -= ten.cache_mb[c]
    return plan


def _improve(world: World, plan: list, w: list[np.ndarray], price_out: float,
             start: tuple[int, int] | None, rounds: int = 2) -> tuple[list, float]:
    """Block-coordinate improvement of a capped-feasible plan: each tenant in
    turn re-solves its path inside the capacity the others leave (its own
    current path is feasible, so the re-solve never worsens its own sum);
    the change is kept only if the full objective, fairness included,
    improves. The plan stays capped-feasible and within the start window."""
    plan = [list(p) for p in plan]
    best = plan_objective(world, plan, price_out)
    T = world.steps
    for _ in range(rounds):
        changed = False
        for i, ten in enumerate(world.tenants):
            use_r, use_c = usage(world, plan)
            own_r = np.array([ten.replicas[p[0]] for p in plan[i]], dtype=float)
            own_c = np.array([ten.cache_mb[p[1]] for p in plan[i]], dtype=float)
            room_r = world.cap_replicas - (use_r - own_r)
            room_c = world.cap_cache_mb - (use_c - own_c)
            mask = ((ten.replicas[None, :] <= room_r[:, None])[:, :, None, None]
                    & (ten.cache_mb[None, :] <= room_c[:, None])[:, None, :, None])
            mask[0] &= start_mask(ten, start)[:, :, None]
            path = best_path(dp_values(np.where(mask, w[i], np.inf)))
            if path == plan[i]:
                continue
            trial = plan[:i] + [path] + plan[i + 1:]
            value = plan_objective(world, trial, price_out)
            if value < best - 1e-15:
                plan, best, changed = trial, value, True
        if not changed:
            break
    return plan, best


@dataclass(frozen=True)
class Bounds:
    lb0: float
    lb1: float
    lb2: float
    ub: float
    ub_nofair: float  # the UB plan's objective without the fairness term (comparable with LB2)
    static: float
    iterations: int


def static_best(world: World, price_out: float) -> tuple[float, list]:
    """The best fixed state per tenant with replicas <= cap / N and cache <=
    cap / N (an equal share), held from the first step."""
    n, T = len(world.tenants), world.steps
    share_r = world.cap_replicas // n
    share_c = world.cap_cache_mb // n
    plan = []
    for ten in world.tenants:
        tot = ten.weight(price_out).sum(axis=0)  # (R, C, K)
        ok_r = ten.replicas <= max(share_r, int(ten.replicas[0]))
        ok_c = ten.cache_mb <= share_c
        tot = np.where(ok_r[:, None, None] & ok_c[None, :, None], tot, np.inf)
        s = np.unravel_index(int(np.argmin(tot)), tot.shape)
        plan.append([tuple(int(v) for v in s)] * T)
    return plan_objective(world, plan, price_out), plan


def bounds(world: World, price_out: float, iterations: int = 120,
           primal_every: int = 5, start: tuple[int, int] | None = (2, 128)) -> Bounds:
    """LB0, LB1, LB2 and UB for one world (see the module docstring).

    The dual is maximised by projected subgradient ascent on per-step
    multipliers for the two caps, with a Polyak step against the best
    capped-feasible plan known (the step size halves after 10 iterations
    without a better dual). Every `primal_every` iterations, and whenever the
    relaxed plan fits the caps, the primal heuristic turns the current
    multipliers into a capped plan that starts within one move of `start`;
    UB is the best such plan, scored with the full objective. `start=None`
    lets UB start anywhere."""
    if iterations < 1:
        raise ValueError("iterations must be at least 1")
    n, T = len(world.tenants), world.steps
    ref = world.tenants[0]
    if not all(np.array_equal(t.replicas, ref.replicas) and np.array_equal(t.cache_mb, ref.cache_mb)
               and t.cost.shape == ref.cost.shape for t in world.tenants):
        raise ValueError("tenants must share one state grid to be solved together")
    W = np.stack([ten.weight(price_out) for ten in world.tenants])  # (N, T, R, C, K)
    reps = ref.replicas.astype(float)
    mbs = ref.cache_mb.astype(float)
    masks = [start_mask(ten, start) for ten in world.tenants]
    static_val, static_plan = static_best(world, price_out)
    best_ub, best_ub_nofair = math.inf, math.inf
    best_feasible_sum = sum(path_value(W[i], p) for i, p in enumerate(static_plan))
    w_true = [W[i] for i in range(n)]
    lam_r = np.zeros(T)
    lam_c = np.zeros(T)
    best_dual, lb1_sum = -math.inf, None
    theta, stall = 1.0, 0
    orders = [list(range(n)), list(reversed(range(n)))]
    it = 0
    for it in range(iterations):
        Wt = (W + lam_r[None, :, None, None, None] * reps[None, None, :, None, None]
              + lam_c[None, :, None, None, None] * mbs[None, None, None, :, None])
        V = dp_values(Wt)
        plan = [best_path(V[i]) for i in range(n)]
        dual = float(V[:, 0].reshape(n, -1).min(axis=1).sum())
        if it == 0:
            lb1_sum = dual  # no multipliers yet: the uncapped optimum, LB1
        dual -= float(lam_r.sum() * world.cap_replicas + lam_c.sum() * world.cap_cache_mb)
        if dual > best_dual + 1e-12:
            best_dual, stall = dual, 0
        else:
            stall += 1
            if stall >= 10:
                theta, stall = theta / 2.0, 0
        use_r, use_c = usage(world, plan)
        g_r, g_c = use_r - world.cap_replicas, use_c - world.cap_cache_mb
        fits = bool((g_r <= 0).all() and (g_c <= 0).all())
        if fits:  # feasible for the free-start capped problem: a Polyak target
            best_feasible_sum = min(best_feasible_sum,
                                    sum(path_value(W[i], p) for i, p in enumerate(plan)))
        candidates = []
        if fits and all(masks[i][p[0][0], p[0][1]] for i, p in enumerate(plan)):
            candidates.append(plan)
        if not candidates and (fits or it % primal_every == 0 or it == iterations - 1):
            wt = [Wt[i] for i in range(n)]
            candidates = [_sequential_feasible(world, wt, order, start) for order in orders]
        for cand in candidates:
            cand, value = _improve(world, cand, w_true, price_out, start)
            nofair = sum(path_value(W[i], p) for i, p in enumerate(cand))
            if value < best_ub:
                best_ub, best_ub_nofair = value, nofair / (n * T)
            best_feasible_sum = min(best_feasible_sum, nofair)
        norm = float((g_r ** 2).sum() + (g_c ** 2).sum())
        slack_value = float((lam_r * -g_r).sum() + (lam_c * -g_c).sum())
        if fits and slack_value <= 1e-9 * max(1.0, abs(dual)):
            break  # complementary slackness: the relaxed plan is optimal for the capped problem
        if norm <= 0 or theta < 1e-4:
            break
        step = theta * max(best_feasible_sum - dual, 1e-9) / norm
        lam_r = np.maximum(0.0, lam_r + step * g_r)
        lam_c = np.maximum(0.0, lam_c + step * g_c)
    if not math.isfinite(best_ub):
        raise RuntimeError("the primal heuristic produced no plan")
    return Bounds(lb0=lb0(world, price_out), lb1=lb1_sum / (n * T),
                  lb2=best_dual / (n * T), ub=best_ub, ub_nofair=best_ub_nofair,
                  static=static_val, iterations=it + 1)
