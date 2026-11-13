"""Fast seeded property tests for Theorems 1 and 2, T3 and T5 (WP3, WP18).

The 100,000-case record is PROPERTY_CHECKS.md (`property_check.py`); these
tests run the same checks on fresh case ids (200,000 and up) in seconds.
Seeded numpy, no extra dependency; see property_cases.py for why not
Hypothesis. Each test names the statement it checks; the statements are
drafts (INDEX_THEORY.md)."""

import itertools
import math
import random
from collections import Counter

import numpy as np
import pytest

from interaction_index import analyse_box, lower_upper_tail, remainder_table, sample_range, sampling_confidence
from property_cases import (
    FAMILIES, M_DRAWS, MC_ALPHA, binom_two_sided, check_case, check_t3, draw_rho_eta, exact_coverage,
    hardest_table_coverage, make_case, tie_instance,
)

FRESH = 200_000  # case ids above the recorded run


@pytest.fixture(scope="module")
def sample_counts():
    """40 fresh cases per family through every WP3 check."""
    total, examples = Counter(), []
    for i in range(FRESH, FRESH + 40 * len(FAMILIES)):
        c, ex, _ = check_case(make_case(i), mc_trials=300 if i % 97 == 0 else 0)
        total += c
        examples += ex
    return total, examples


def test_theorems_1_and_2_hold_and_the_library_matches_the_oracle(sample_counts):
    """0 <= loss <= R(x^D) - R(x^J) <= index (T1) and
    0 <= phi(x^C) - phi(x^J) <= -R^C(x^J) <= index at x^C (T2), with the
    library's every quantity equal to an independent numpy computation."""
    total, examples = sample_counts
    assert examples == []
    for key in ("t1_violation", "t2_violation", "t1_oracle_mismatch", "t2_oracle_mismatch",
                "analyse_box_differs", "t2_not_coordinatewise_minimal", "tight_mismatch",
                "t5_below_bound", "t5_sample_above_index", "t5_mc_disagree"):
        assert total[key] == 0, key
    # the sample exercised the theorems, their guards and the tight cases
    assert total["t1_applies"] > 300 and total["t2_applies"] > 300 and total["t2_equilibrium_checks"] > 1000
    assert total["t1_loss_equals_index"] >= 40 and total["tight_t2_cases"] >= 80
    assert total["t1_constraint_coupling"] > 0 and total["t1_anchor_infeasible"] > 0
    assert total["t2_search_ends_infeasible"] > 0


def test_theorem1_needs_its_remainder_hypothesis(sample_counts):
    """Outside the hypotheses the loss can exceed the index: here the axis
    point (0, 1) is infeasible, so R(x^J) is undefined; loss 4, index 0."""
    table = {(0, 0): 0.0, (1, 0): -1.0, (0, 1): None, (1, 1): -5.0}
    a = analyse_box([[0, 1], [0, 1]], table.__getitem__, (0, 0))
    assert a.loss == 4.0 and a.oscillation == 0.0 and not a.theorem1_applies
    assert sample_counts[0]["t1_outside_loss_above_index"] > 0  # it happens in random cases too


def test_theorem1_bound_is_attained_when_ties_break_adversely():
    """eta = 0 with the improving value listed first: loss = bound = index."""
    for rho in (1e-3, 1.0, 7.5, 1e4):
        a = analyse_box(*tie_instance(rho))
        assert a.per_knob == (1, 1) and a.loss == a.bound == a.oscillation == rho


def test_theorem2_bound_is_attained():
    """The tight_t2_ties construction: phi(x^C) - phi(x^J) = -R^C(x^J) = index."""
    from interaction_index import theorem2_terms
    hits = 0
    for i in range(FRESH, FRESH + 20 * len(FAMILIES)):
        case = make_case(i)
        if case.family != "tight_t2_ties":
            continue
        t = theorem2_terms(case.values(), case.objective(), case.point(case.start))
        rho = case.expect["t2"]["index"]
        assert t["loss"] == pytest.approx(rho, rel=1e-12)
        assert t["bound"] == pytest.approx(rho, rel=1e-12)
        assert t["oscillation"] == pytest.approx(rho, rel=1e-12)
        hits += 1
    assert hits == 20


def test_t3_construction_for_random_rho_and_eta():
    """T3: for random rho > 0 and eta in (0, rho), including eta within 1e-12
    of either end, the index is rho and the per-knob loss rho - eta."""
    fails = Counter()
    for i in range(1_000_000, 1_002_000):
        fails.update(check_t3(*draw_rho_eta(i)))
    assert not fails


def test_t3_refuses_parameters_outside_its_range():
    from interaction_index import tight_instance
    for rho, eta in ((1.0, 0.0), (1.0, 1.0), (1.0, 1.5), (1.0, -1.0), (1.0, math.nan), (0.0, 0.0)):
        with pytest.raises(ValueError):
            tight_instance(rho, eta)


def _brute_coverage(vals, d, m):
    hits = sum(max(draw) - min(draw) >= d for draw in itertools.product(vals, repeat=m))
    return hits / len(vals) ** m


def test_exact_coverage_matches_enumeration_of_every_draw():
    """The exact T5 probability against all n^m equally likely draws,
    including ties and values whose differences round (0.1, 0.3, ...)."""
    rng = random.Random(4)
    tables = [[0.1, 0.2, 0.3], [0.0, 0.0, 1.0, 1.0, 2.0], [0.1, 0.3, 0.7, 1.1], [5.0], [1e16, 1e16 + 2.0, 3.0]]
    tables += [[rng.choice([0.1, 0.2, 0.3, -0.4, 1e-17]) for _ in range(rng.randint(1, 5))] for _ in range(30)]
    for vals in tables:
        srt = sorted(vals)
        lo, hi = lower_upper_tail(srt)
        for d in {hi - lo, 0.2, 0.30000000000000004, 0.1 + 0.2 - 0.1}:
            for m in (1, 2, 3, 4):
                assert exact_coverage(srt, d, (m,))[m] == pytest.approx(_brute_coverage(srt, d, m), abs=1e-12)


def test_t5_bound_holds_on_the_hardest_tables():
    """ceil(0.05 n) values in each tail: exact coverage >= 1 - 2 (0.95)^m,
    equal to the closed form, with slack (1 - 2p)^m."""
    for n in range(2, 201):
        for m in M_DRAWS:
            exact, closed = hardest_table_coverage(n, m)
            assert exact == pytest.approx(closed, abs=1e-12)
            assert exact >= sampling_confidence(m) - 1e-12
    exact, _ = hardest_table_coverage(100, 59)
    assert exact - sampling_confidence(59) == pytest.approx(0.9 ** 59, rel=1e-9)


def test_sample_range_never_exceeds_the_index_and_matches_the_exact_coverage():
    """The library's sampler: every sampled range is at most the index, and
    its hit rate agrees with the exact probability (exact binomial test)."""
    rng = random.Random(8)
    for i in range(FRESH, FRESH + 30):
        case = make_case(i)
        table = remainder_table(case.values(), case.objective(), case.point(case.anchor))
        rs = sorted(table.remainder.values())
        if len(rs) < 2:
            continue
        lo, hi = lower_upper_tail(rs)
        p = exact_coverage(rs, hi - lo, (59,))[59]
        draws = [sample_range(table, 59, rng) for _ in range(400)]
        assert max(draws) <= rs[-1] - rs[0]
        hits = sum(r >= hi - lo for r in draws)
        assert binom_two_sided(hits, len(draws), p) >= MC_ALPHA
        assert p >= sampling_confidence(59) - 1e-12


def test_non_finite_values_are_rejected_or_treated_as_infeasible():
    values = [[0, 1, 2], [0, 1, 2, 3]]
    t = [1.0, 0.0, 0.0, 2.0, -4.0, 4.0, math.nan, -4.0, 0.0, -4.0, -2.0, -2.0]
    tab = dict(zip(itertools.product(*values), t))
    clean = analyse_box(values, lambda x: None if math.isnan(tab[x]) else tab[x], (0, 0))
    try:
        got = analyse_box(values, tab.__getitem__, (0, 0))
    except ValueError:
        return  # rejecting non-finite values is a fix
    assert got.oscillation == clean.oscillation == 12.0  # today: 3.0, silently


def test_every_family_is_drawn_with_two_to_seventeen_values_per_knob():
    seen, sizes = set(), Counter()
    for i in range(FRESH, FRESH + 3 * len(FAMILIES)):
        case = make_case(i)
        seen.add(case.family)
        assert 2 <= case.phi.ndim <= 4
        assert all(2 <= s <= 17 for s in case.phi.shape)
        sizes.update(case.phi.shape)
    assert seen == set(FAMILIES)
    assert max(sizes) >= 15


def test_cases_are_reproducible_from_their_id():
    a, b = make_case(123_456), make_case(123_456)
    assert a.family == b.family and a.labels == b.labels and a.anchor == b.anchor
    assert np.array_equal(a.phi, b.phi, equal_nan=True)


def test_exact_binomial_test_against_known_values():
    """The cross-check's test: one miss in 1,000 draws at coverage 0.99997 is
    unremarkable (a z-test would flag it), ten misses are not."""
    assert binom_two_sided(999, 1000, 0.99997) > 0.05
    assert binom_two_sided(990, 1000, 0.99997) < MC_ALPHA
    assert binom_two_sided(5, 10, 0.5) == pytest.approx(1.0)
    assert binom_two_sided(0, 10, 0.5) == pytest.approx(2 * 0.5 ** 10)
