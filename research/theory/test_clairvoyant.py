"""Reference points: the path DP is exact, the bounds bracket the capped
clairvoyant optimum, and the arrays reproduce the engine's own scoring."""

import dataclasses
import itertools
import random
from pathlib import Path

import numpy as np
import pytest

from clairvoyant import (
    C_RADIUS,
    R_RADIUS,
    TenantArrays,
    World,
    best_path,
    bounds,
    dp_values,
    lb0,
    lb1,
    moves_ok,
    path_value,
    plan_objective,
)

REPO = Path(__file__).resolve().parents[2]


def _random_w(rng: np.random.Generator, T=4, R=3, C=3, K=2, p_inf=0.1):
    w = rng.normal(size=(T, R, C, K))
    w[rng.random(w.shape) < p_inf] = np.inf
    w[:, 0, 0, 0] = rng.normal()  # one state always holdable: a finite path exists
    return w


def _brute_force(w):
    T, R, C, K = w.shape
    states = list(itertools.product(range(R), range(C), range(K)))
    best = np.inf
    for path in itertools.product(states, repeat=T):
        if not moves_ok(path):
            continue
        best = min(best, path_value(w, path))
    return best


@pytest.mark.parametrize("shape", [(4, 3, 3, 2), (3, 7, 4, 1)])
def test_dp_is_the_brute_force_optimum(shape):
    """Including a grid wider than the move window (7 replica values, 4 cache
    levels), so a wrong radius or shift would change the optimum."""
    T, R, C, K = shape
    rng = np.random.default_rng(1)
    for _ in range(15):
        w = _random_w(rng, T=T, R=R, C=C, K=K)
        V = dp_values(w)
        assert V[0].min() == pytest.approx(_brute_force(w))
        path = best_path(V)
        assert moves_ok(path)
        assert path_value(w, path) == pytest.approx(V[0].min())


def test_moves_respect_the_planner_limits():
    assert (R_RADIUS, C_RADIUS) == (2, 1)
    assert moves_ok([(0, 0, 0), (2, 1, 3)])
    assert not moves_ok([(0, 0, 0), (3, 0, 0)])
    assert not moves_ok([(0, 0, 0), (0, 2, 0)])


def test_stacked_dp_equals_separate_dps():
    rng = np.random.default_rng(2)
    ws = np.stack([_random_w(rng, T=6, R=5, C=4, K=3) for _ in range(4)])
    V = dp_values(ws)
    for i in range(4):
        np.testing.assert_allclose(V[i], dp_values(ws[i]))


def _tiny_world(rng: random.Random, replicas=(1, 2), cache=(0, 64), K=2, T=3,
                cap_replicas=3, cap_cache_mb=64) -> World:
    R, C = len(replicas), len(cache)
    tenants = []
    for i in range(2):
        shape = (T, R, C, K)
        cost = np.array([rng.uniform(0, 0.02) for _ in range(np.prod(shape))]).reshape(shape)
        shed = np.zeros(shape)
        shed[..., 0] = np.array([rng.uniform(0, 50) for _ in range(T * R * C)]).reshape(T, R, C)
        viol = np.array([rng.uniform(0, 1) for _ in range(np.prod(shape))]).reshape(shape)
        tenants.append(TenantArrays(f"t{i}", np.array(replicas), np.array(cache), cost, shed, viol))
    return World(tenants, cap_replicas=cap_replicas, cap_cache_mb=cap_cache_mb, steps=T)


def _capped_optimum(world: World, price_out: float, start=None) -> tuple[float, float]:
    """Brute force over every pair of paths: the capped optimum without the
    fairness term from a free start (what LB2 bounds), and with it from
    `start` (what UB bounds; None = free start)."""
    T = world.steps
    ten0 = world.tenants[0]
    states = list(itertools.product(range(len(ten0.replicas)), range(len(ten0.cache_mb)),
                                    range(ten0.cost.shape[-1])))
    paths = [p for p in itertools.product(states, repeat=T) if moves_ok(p)]

    def starts_ok(p):
        if start is None:
            return True
        r0 = list(ten0.replicas).index(start[0])
        c0 = list(ten0.cache_mb).index(start[1])
        return abs(p[0][0] - r0) <= R_RADIUS and abs(p[0][1] - c0) <= C_RADIUS

    best_nofair, best_full = np.inf, np.inf
    for pa, pb in itertools.product(paths, repeat=2):
        ok = all(world.tenants[0].replicas[pa[t][0]] + world.tenants[1].replicas[pb[t][0]] <= world.cap_replicas
                 and world.tenants[0].cache_mb[pa[t][1]] + world.tenants[1].cache_mb[pb[t][1]] <= world.cap_cache_mb
                 for t in range(T))
        if not ok:
            continue
        nofair = sum(path_value(ten.weight(price_out), p) for ten, p in zip(world.tenants, (pa, pb))) / (2 * T)
        best_nofair = min(best_nofair, nofair)
        if starts_ok(pa) and starts_ok(pb):
            best_full = min(best_full, plan_objective(world, (pa, pb), price_out))
    return best_nofair, best_full


@pytest.mark.parametrize("seed", range(6))
def test_bounds_bracket_the_capped_optimum(seed):
    world = _tiny_world(random.Random(seed))
    for price in (0.0, 1e-4):
        b = bounds(world, price, iterations=200, start=None)
        opt_nofair, opt_full = _capped_optimum(world, price)
        assert b.lb0 <= b.lb1 + 1e-12
        assert b.lb1 <= b.lb2 + 1e-12
        assert b.lb2 <= opt_nofair + 1e-9
        assert opt_full <= b.ub + 1e-9
        assert opt_full <= b.static + 1e-9


@pytest.mark.parametrize("seed", range(3))
def test_bounds_with_binding_moves_caps_and_a_start(seed):
    """Four replica values (moves of +-2 cannot cross the grid), caps that
    bind, and UB restricted to start within one move of (4 replicas, 64 MB)."""
    world = _tiny_world(random.Random(100 + seed), replicas=(1, 2, 3, 4), cache=(0, 64), K=1,
                        cap_replicas=5, cap_cache_mb=64)
    for price in (0.0, 1e-4):
        b = bounds(world, price, iterations=200, start=(4, 64))
        opt_nofair, opt_from_start = _capped_optimum(world, price, start=(4, 64))
        assert b.lb2 <= opt_nofair + 1e-9
        assert opt_from_start <= b.ub + 1e-9


def test_bounds_refuse_bad_input():
    world = _tiny_world(random.Random(0))
    with pytest.raises(ValueError):
        bounds(world, 0.0, iterations=0)
    with pytest.raises(ValueError):
        bounds(world, 0.0, start=(2, 128))  # not on the tiny world's grid


def test_a_plan_made_on_the_noise_free_curve_scores_in_the_realised_world():
    """The curve world differs from the realised one only by the jitter; a
    plan made on the curve is feasible in the realised world and scores at
    or above its lower bounds there."""
    import sys
    sys.path.insert(0, str(REPO / "eval"))
    from harness import config as hconfig

    from clairvoyant import world_for_run

    spec = hconfig.load(REPO / "eval" / "experiments" / "matrix_level2.yaml")
    # An AI workload: its model spend follows demand (CRUD cost does not).
    run = next(r for r in hconfig.expand(spec) if r.workload == "ai_uncacheable" and r.cluster_size == "small")
    run = dataclasses.replace(run, steps=8)
    noisy, curve = world_for_run(run), world_for_run(run, jitter=False)
    assert noisy.steps == curve.steps == 8
    assert not np.allclose(noisy.tenants[0].cost, curve.tenants[0].cost)
    assert not np.allclose(noisy.tenants[0].violation, curve.tenants[0].violation)
    b = bounds(curve, 1e-4, iterations=10)
    value = plan_objective(noisy, b.ub_plan, 1e-4)
    assert lb1(noisy, 1e-4)[0] <= value


REAL_CELLS = [("ai_cacheable", "whale", "small"), ("agentic", "uniform", "small"),
              ("crud_bursty", "premium_heavy", "large")]
REAL_ARMS = ["jcac_level_ratchet", "hpa_level_capped", "static_share"]


@pytest.mark.parametrize("cell", REAL_CELLS)
def test_arrays_reproduce_the_engines_scoring_of_real_runs(cell):
    """Score each arm's realised states with the arrays: cost, mean violation
    and mean Jain equal the engine's. LB0 sits below every arm; LB1 below the
    joint controller whenever it never used its (move-breaking) fallback."""
    import sys
    sys.path.insert(0, str(REPO / "eval"))
    from harness import config as hconfig
    from harness import sim_backend

    from clairvoyant import world_for_run

    spec = hconfig.load(REPO / "eval" / "experiments" / "matrix_level2.yaml")
    world = None
    for arm in REAL_ARMS:
        run = next(r for r in hconfig.expand(spec)
                   if (r.system, r.workload, r.tenant_mix, r.cluster_size, r.rep) == (arm, *cell, 0))
        run = dataclasses.replace(run, steps=12, store_timeseries=True)
        result = sim_backend.execute(run)
        if world is None:  # shared seeds: one world for every arm of the cell
            world = world_for_run(run)
        assert world.steps == result["metrics"]["steps"] == 12

        plan = []
        for ten in world.tenants:
            rows = sorted((r for r in result["timeseries"] if r["tenant"] == ten.tenant),
                          key=lambda r: r["step"])
            plan.append([(int(np.flatnonzero(ten.replicas == r["replicas"])[0]),
                          int(np.flatnonzero(ten.cache_mb == r["cache_mb"])[0]),
                          ("none", "small", "mid", "large").index(r["tier"])) for r in rows])
        n, T = len(world.tenants), world.steps
        cost = sum(ten.cost[(t, *p[t])] for ten, p in zip(world.tenants, plan) for t in range(T))
        viol = sum(ten.violation[(t, *p[t])] for ten, p in zip(world.tenants, plan) for t in range(T)) / (n * T)
        assert cost == pytest.approx(result["metrics"]["total_cost_usd"], rel=1e-12)
        assert viol == pytest.approx(result["metrics"]["mean_violation"], rel=1e-12)
        j_real = plan_objective(world, plan, 1e-4)
        jain = 1.0 - (j_real - (cost + sum(ten.shed[(t, *p[t])] for ten, p in zip(world.tenants, plan)
                                           for t in range(T)) * 1e-4) / (n * T * 0.01) - 2 * viol) / 0.5
        assert jain == pytest.approx(result["metrics"]["mean_jain"], rel=1e-9)
        assert lb0(world, 1e-4) <= j_real
        if arm == "jcac_level_ratchet" and not result["metrics"]["plan_fallback_share"]:
            assert lb1(world, 1e-4)[0] <= j_real
