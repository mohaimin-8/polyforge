"""T6 (knobs as players of a game) and T4 (a run-level bound), WP18.

T6, draft: with one shared objective phi, phi is an exact potential of the
game whose players are the knobs, and its pure equilibria are the
coordinate-wise minima of Theorem 2. These tests also pin down what does NOT
follow when each knob's controller minimises its own local objective.

T4: the conjectured run-level bound fails under the plan's assumptions
(explicit family below; 100,000-instance record in PROPERTY_CHECKS.md); the
two-step proposition T4a and the within-reach version T4' are checked on
random instances. Statements: INDEX_THEORY.md (drafts)."""

import itertools
import random

import numpy as np
import pytest

from interaction_index import analyse_box, coordinatewise_minimum, remainder_table
from property_cases import coordinatewise_minimal_mask
from t4_check import draw_instance, drift_instance, evaluate, grid_coords, index_all, step_maps


# ---------------------------------------------------------------- T6 helpers

def pure_equilibria(values, costs):
    """Points where no player k lowers its own cost costs[k] by changing x_k."""
    out = []
    for x in itertools.product(*values):
        if all(costs[k](x[:k] + (v,) + x[k + 1:]) >= costs[k](x)
               for k in range(len(values)) for v in values[k]):
            out.append(x)
    return out


def best_response_dynamics(values, costs, start, max_rounds=100):
    """Round-robin best responses (strict improvements only): returns the
    final point and 'equilibrium', or the point where a state repeats and 'cycle'."""
    x, seen = tuple(start), set()
    for _ in range(max_rounds):
        changed = False
        for k, vals in enumerate(values):
            best_v, best = x[k], costs[k](x)
            for v in vals:
                c = costs[k](x[:k] + (v,) + x[k + 1:])
                if c < best:
                    best_v, best = v, c
            if best_v != x[k]:
                x, changed = x[:k] + (best_v,) + x[k + 1:], True
        if not changed:
            return x, "equilibrium"
        if x in seen:
            return x, "cycle"
        seen.add(x)
    return x, "no_settle"


def four_cycle_sums(values, costs):
    """Sum of the movers' cost changes around every closed path of four
    unilateral moves (players k, j alternate). Monderer and Shapley (1996):
    a finite game has an exact potential iff every such sum is zero."""
    sums = []
    for x in itertools.product(*values):
        for k, j in itertools.permutations(range(len(values)), 2):
            for a in values[k]:
                for b in values[j]:
                    y1 = x[:k] + (a,) + x[k + 1:]
                    y2 = y1[:j] + (b,) + y1[j + 1:]
                    y3 = y2[:k] + (x[k],) + y2[k + 1:]
                    sums.append(costs[k](y1) - costs[k](x) + costs[j](y2) - costs[j](y1)
                                + costs[k](y3) - costs[k](y2) + costs[j](x) - costs[j](y3))
    return sums


def _random_game(rng):
    n_knobs = rng.randint(2, 3)
    values = [list(range(rng.randint(2, 4))) for _ in range(n_knobs)]
    table = {x: float(rng.randint(-5, 5)) for x in itertools.product(*values)}
    return values, table


# ---------------------------------------------------------------- T6

def test_shared_objective_is_an_exact_potential_and_equilibria_are_coordinatewise_minima():
    """Identical interest: every player's cost is phi. Then phi is an exact
    potential (all four-cycle sums vanish), the pure equilibria are exactly
    the coordinate-wise minima, and iterated one-knob search ends in one."""
    rng = random.Random(61)
    for _ in range(300):
        values, table = _random_game(rng)
        phi = table.__getitem__
        costs = [phi] * len(values)
        assert all(abs(s) < 1e-12 for s in four_cycle_sums(values, costs))
        arr = np.array([table[x] for x in itertools.product(*values)]).reshape([len(v) for v in values])
        cw = {tuple(int(i) for i in x) for x in np.argwhere(coordinatewise_minimal_mask(arr))}
        assert set(pure_equilibria(values, costs)) == cw
        start = tuple(rng.choice(v) for v in values)
        assert coordinatewise_minimum(values, phi, start) in cw
        joint = min(table, key=table.get)
        assert joint in cw  # the optimum is an equilibrium: price of stability 0


def test_local_objectives_aligned_up_to_terms_a_player_cannot_move_keep_the_equilibria():
    """psi_k(x) = phi(x) + D_k(x without knob k): still an exact potential
    game with potential phi, same equilibria, so Theorem 2 applies."""
    rng = random.Random(62)
    for _ in range(200):
        values, table = _random_game(rng)
        dummies = [{x[:k] + x[k + 1:]: rng.uniform(-9, 9) for x in itertools.product(*values)}
                   for k in range(len(values))]
        costs = [(lambda x, k=k: table[x] + dummies[k][x[:k] + x[k + 1:]]) for k in range(len(values))]
        assert all(abs(s) < 1e-9 for s in four_cycle_sums(values, costs))
        assert set(pure_equilibria(values, costs)) == set(pure_equilibria(values, [table.__getitem__] * len(values)))


def test_misaligned_local_objectives_can_cycle_without_an_equilibrium():
    """Replicas match the cache, the cache mismatches the replicas (matching
    pennies): no pure equilibrium, best responses cycle, no exact potential."""
    values = [[0, 1], [0, 1]]
    costs = [lambda x: float(x[0] != x[1]), lambda x: float(x[0] == x[1])]
    assert pure_equilibria(values, costs) == []
    assert best_response_dynamics(values, costs, (0, 0))[1] == "cycle"
    assert min(four_cycle_sums(values, costs)) == -4.0


def test_an_equilibrium_of_misaligned_controllers_escapes_theorem2_even_with_zero_index():
    """phi(x) = -(x_1 + x_2) is additive (R = 0, index 0), so Theorem 2 gives
    0 loss at any coordinate-wise minimum of phi. Controllers that each
    minimise their own resource x_k form a potential game (potential
    x_1 + x_2) whose equilibrium (0, 0) loses 2 on phi."""
    values = [[0, 1], [0, 1]]
    phi = lambda x: -float(x[0] + x[1])  # noqa: E731
    costs = [lambda x: float(x[0]), lambda x: float(x[1])]
    assert all(abs(s) < 1e-12 for s in four_cycle_sums(values, costs))  # a potential game...
    (eq,) = pure_equilibria(values, costs)
    rem = remainder_table(values, phi, eq).remainder
    assert max(rem.values()) - min(rem.values()) == 0.0  # ...with no interaction in phi
    assert phi(eq) - phi((1, 1)) == 2.0 > -rem[(1, 1)]  # the bound of Theorem 2 fails


# ---------------------------------------------------------------- T4

def test_drift_family_meets_the_assumptions_and_breaks_the_conjectured_constant():
    ev8, ev16 = evaluate(drift_instance(8)), evaluate(drift_instance(16))
    for ev in (ev8, ev16):
        assert ev["unique_minimisers"] and ev["a"] and ev["c"] == {"linf": True, "l1": True}
        assert ev["target"]["linf"]["C"] == pytest.approx(1 + 4.02 / 1.0)
    assert ev8["target"]["linf"]["D"]["violated"] and ev8["target"]["l1"]["D"]["violated"]
    ratio = [ev["target"]["linf"]["D"]["E"] / ev["target"]["linf"]["D"]["S"] for ev in (ev8, ev16)]
    assert ratio[1] > 1.9 * ratio[0]  # E / sum I grows linearly in T; the constant does not


def test_drift_family_step_by_step_with_the_library():
    """The same runs, recomputed with analyse_box only (no step maps)."""
    t_max = 8
    inst = drift_instance(t_max)
    n = inst.phi.shape[1]
    x_d = x_j = inst.x0
    total_e = total_i = 0.0
    for t in range(t_max):
        f = lambda p, t=t: float(inst.phi[t][p])  # noqa: E731

        def box(x):
            return [list(range(max(0, x[k] - 1), min(n - 1, x[k] + 1) + 1)) for k in range(2)]

        a_d, a_j = analyse_box(box(x_d), f, x_d), analyse_box(box(x_j), f, x_j)
        assert a_j.joint == tuple(inst.minimisers[t])  # the joint run sits on the minimiser
        total_e += f(a_d.per_knob) - f(a_j.joint)
        total_i += a_d.oscillation
        x_d, x_j = a_d.per_knob, a_j.joint
    assert total_e == pytest.approx(2.0 * t_max) and total_i == pytest.approx(2.02)
    assert total_e > (1 + 4.02) * total_i


def test_step_maps_and_index_agree_with_the_library():
    rng = random.Random(5)
    for i in range(25):
        inst = draw_instance(500_000 + i, "target")
        t_max, n_knobs, n = inst.shape
        joint, pk = step_maps(inst.phi, inst.r)
        index = index_all(inst.phi, inst.r)
        coords = grid_coords(n_knobs, n)
        for _ in range(12):
            t, s = rng.randrange(t_max), rng.randrange(n ** n_knobs)
            x = tuple(int(v) for v in coords[s])
            box = [list(range(max(0, x[k] - inst.r), min(n - 1, x[k] + inst.r) + 1)) for k in range(n_knobs)]
            a = analyse_box(box, lambda p, t=t: float(inst.phi[t][p]), x)
            assert tuple(coords[joint[t, s]]) == a.joint
            assert tuple(coords[pk[t, s]]) == a.per_knob
            assert index[t, s] == pytest.approx(a.oscillation, abs=1e-9)


def test_two_step_proposition_t4a():
    """From a start within reach of m_0, under (a) and (b) only:
    E_1 <= I_0 and E_2 <= (1 + beta/alpha) I_0 + I_1."""
    checked = 0
    for i in range(600_000, 600_300):
        ev = evaluate(draw_instance(i, "target"))
        for ok in ev["t4a"].values():
            assert ok
            checked += 1
    assert checked > 200


def test_within_reach_version_t4prime():
    """If the start is within reach, the minimiser moves at most rho <= r and
    I_t(x) <= alpha (r - rho) within reach, both runs stay within reach, the
    joint run lands on m_t, and 0 <= E_T' <= sum I_t along the per-knob run."""
    accepted = 0
    for i in range(700_000, 700_250):
        ev = evaluate(draw_instance(i, "within"))
        if not ev["within_assumptions"]:
            continue
        accepted += 1
        assert all(ev["within"].values()), ev["within"]
    assert accepted > 100
