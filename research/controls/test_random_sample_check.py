"""The random-half check's bootstrap intervals (WP22, exploratory)."""

import numpy as np

import random_sample_check as rsc


def test_an_interval_is_given_only_where_every_draw_defines_the_correlation():
    """A predictor non-zero in 2 of 100 regimes is constant in every draw that misses both:
    its rank correlation is then undefined there, and dropping those draws would leave an
    'interval' conditional on drawing one of the two (it excluded 0 here)."""
    rng = np.random.default_rng(1)
    g = rng.normal(size=100)
    sparse = np.zeros(100)
    sparse[np.argsort(g)[:2]] = 1.0          # the two lowest gains: rho clearly negative
    idx = rng.integers(0, 100, size=(2000, 100))
    lo, hi, undefined = rsc.interval(sparse, g, idx)
    assert undefined == sum(np.ptp(sparse[i]) == 0 for i in idx) > 0
    assert lo is None and hi is None
    smooth = g + rng.normal(size=100)
    lo, hi, undefined = rsc.interval(smooth, g, idx)
    assert undefined == 0 and lo < rsc.spearman(smooth, g) < hi


def test_the_row_says_how_often_the_correlation_is_undefined():
    rng = np.random.default_rng(2)
    g = rng.normal(size=100)
    sparse = np.zeros(100)
    sparse[:2] = 1.0
    idx = rng.integers(0, 100, size=(2000, 100))
    row = rsc.row("share of something", sparse, g, idx)
    _, _, undefined = rsc.interval(sparse, g, idx)
    assert row.startswith("| share of something | 2 | ")
    assert f"not defined: constant in {undefined:,} of 2,000 draws" in row
