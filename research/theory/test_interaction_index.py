"""Property tests for INDEX_THEORY.md's statements (seeded, no extra
dependency). Each test states the claim it checks."""

import itertools
import math
import random

import pytest

from interaction_index import (
    TAIL,
    analyse_box,
    coordinatewise_minimum,
    lower_upper_tail,
    remainder_table,
    sample_range,
    sampling_confidence,
    theorem2_terms,
    tight_instance,
)

EPS = 1e-9


def random_box(rng: random.Random, max_points: int = 240):
    """A random box with an objective = additive part + interaction part of
    random size + random infeasible points, and a random anchor."""
    while True:
        k = rng.randint(2, 4)
        sizes = [rng.randint(1, 6) for _ in range(k)]
        if math.prod(sizes) <= max_points:
            break
    values = [list(range(s)) for s in sizes]
    main = [[rng.gauss(0.0, 1.0) for _ in range(s)] for s in sizes]
    scale = rng.choice([0.0, 0.01, 0.1, 1.0, 10.0])
    p_infeasible = rng.choice([0.0, 0.0, 0.1, 0.3])
    inter = {x: scale * rng.gauss(0.0, 1.0) for x in itertools.product(*values)}
    infeasible = {x for x in inter if rng.random() < p_infeasible}

    def phi(x):
        if x in infeasible:
            return None
        return sum(main[i][v] for i, v in enumerate(x)) + inter[x]

    anchor = tuple(rng.choice(v) for v in values)
    return values, phi, anchor


def test_theorem1_holds_on_random_boxes():
    """0 <= phi(x^D) - phi(x^J) <= R(x^D) - R(x^J) <= max R - min R."""
    rng = random.Random(20261112)
    applied = 0
    for _ in range(20_000):
        values, phi, anchor = random_box(rng)
        a = analyse_box(values, phi, anchor)
        if a.loss is not None:
            assert a.loss >= -EPS
        if a.theorem1_applies:
            applied += 1
            assert a.loss <= a.bound + EPS
            assert a.bound <= a.oscillation + EPS
    assert applied > 10_000  # the test exercised the theorem, not only its guards


def test_no_interaction_means_zero_index_and_zero_loss():
    """An additive objective has R = 0 everywhere: the index is 0 and
    per-knob search attains the joint optimum."""
    rng = random.Random(7)
    for _ in range(2_000):
        k = rng.randint(2, 4)
        values = [list(range(rng.randint(1, 5))) for _ in range(k)]
        main = [[rng.gauss(0, 1) for _ in v] for v in values]
        anchor = tuple(rng.choice(v) for v in values)
        a = analyse_box(values, lambda x: sum(main[i][v] for i, v in enumerate(x)), anchor)
        assert a.oscillation == pytest.approx(0.0, abs=1e-12)
        assert a.loss == pytest.approx(0.0, abs=1e-12)


def test_remainder_is_zero_on_every_axis_through_the_anchor():
    rng = random.Random(11)
    for _ in range(500):
        values, phi, anchor = random_box(rng)
        table = remainder_table(values, phi, anchor)
        for x, r in table.remainder.items():
            if sum(xi != ai for xi, ai in zip(x, anchor)) <= 1:
                assert r == pytest.approx(0.0, abs=1e-12)


def test_tight_instance_reaches_the_bound():
    """T3: loss rho - eta with index rho, so no bound below the index holds
    for every objective."""
    for rho, eta in [(1.0, 0.1), (5.0, 1e-3), (0.2, 0.19)]:
        values, phi, anchor = tight_instance(rho, eta)
        a = analyse_box(values, phi, anchor)
        assert a.per_knob == (1, 1)
        assert a.loss == pytest.approx(rho - eta)
        assert a.oscillation == pytest.approx(rho)
        assert a.bound == pytest.approx(rho)
    with pytest.raises(ValueError):
        tight_instance(1.0, 1.0)


def test_theorem2_holds_on_random_boxes():
    """phi(x^C) - phi(x^J) <= -R^C(x^J) <= max R^C - min R^C for a
    coordinate-wise minimum x^C."""
    rng = random.Random(3)
    applied = 0
    for _ in range(5_000):
        values, phi, anchor = random_box(rng, max_points=120)
        terms = theorem2_terms(values, phi, anchor)
        if terms is None:
            continue
        applied += 1
        assert terms["loss"] >= -EPS
        assert terms["loss"] <= terms["bound"] + EPS
        assert terms["bound"] <= terms["oscillation"] + EPS
    assert applied > 2_500


def test_coordinatewise_minimum_admits_no_single_knob_improvement():
    rng = random.Random(5)
    for _ in range(1_000):
        values, phi, anchor = random_box(rng, max_points=120)
        x = coordinatewise_minimum(values, phi, anchor)
        fx = phi(x)
        if fx is None:
            continue
        for k, vk in enumerate(values):
            for v in vk:
                fy = phi(x[:k] + (v,) + x[k + 1:])
                assert fy is None or fy >= fx - 1e-12


def test_per_knob_search_holds_a_knob_with_no_feasible_axis_value():
    values = [[0, 1, 2], [0, 1]]
    phi = lambda x: None if x[0] != 0 else float(x[1])  # noqa: E731
    a = analyse_box(values, phi, (0, 1))
    assert a.per_knob == (0, 0)


def test_tail_quantiles_meet_their_definition():
    """At least a TAIL share of the values lie at or below the lower one and
    at or above the upper one (what the sampling guarantee needs)."""
    rng = random.Random(9)
    for _ in range(2_000):
        n = rng.randint(1, 400)
        vals = sorted(rng.gauss(0, 1) for _ in range(n))
        lo, hi = lower_upper_tail(vals)
        assert sum(v <= lo for v in vals) >= TAIL * n
        assert sum(v >= hi for v in vals) >= TAIL * n
        assert lo <= hi or n == 1


def test_sampling_guarantee_t5():
    """P(range of m samples >= quantile index) >= 1 - 2 (1 - TAIL)^m;
    with m = 59 the bound is 0.903. Checked on the hardest table (exactly a
    TAIL share in each tail) and on random tables."""
    m = 59
    bound = sampling_confidence(m)
    assert bound == pytest.approx(1 - 2 * 0.95 ** 59)
    assert bound > 0.90
    rng = random.Random(13)
    trials = 4_000

    # Hardest case: 100 values, 5 at the top, 5 at the bottom.
    vals = [10.0] * 5 + [0.0] * 90 + [-10.0] * 5
    table = type("T", (), {"remainder": {i: v for i, v in enumerate(vals)}})()
    lo, hi = lower_upper_tail(sorted(vals))
    hits = sum(sample_range(table, m, rng) >= hi - lo for _ in range(trials))
    assert hits / trials >= bound - 0.02

    # Random boxes: coverage never falls below the bound beyond sampling error.
    for _ in range(40):
        values, phi, anchor = random_box(rng)
        t = remainder_table(values, phi, anchor)
        if len(t.remainder) < 2:
            continue
        lo, hi = lower_upper_tail(sorted(t.remainder.values()))
        hits = sum(sample_range(t, m, rng) >= hi - lo - 1e-12 for _ in range(500))
        assert hits / 500 >= bound - 0.05


def test_box_validation():
    with pytest.raises(ValueError):
        analyse_box([[0, 1]], lambda x: 0.0, (2,))
    with pytest.raises(ValueError):
        analyse_box([[0, 0, 1]], lambda x: 0.0, (0,))
    with pytest.raises(ValueError):
        analyse_box([], lambda x: 0.0, ())
    with pytest.raises(ValueError):
        sampling_confidence(0)
