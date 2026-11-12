"""The MMPP2 fit recovers the moments it matches."""

import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from generator import MMPP2, fit  # noqa: E402


@pytest.mark.parametrize("truth", [MMPP2(2.0, 12.0, 0.02, 0.10), MMPP2(20.0, 45.0, 0.05, 0.05),
                                   MMPP2(0.5, 6.0, 0.01, 0.2)])
def test_fit_recovers_mean_dispersion_and_decay(truth):
    acf = {k: truth.acf(k) for k in (1, 6, 30)}
    got = fit(truth.mean(), truth.dispersion(), acf, truth.p99_ratio())
    assert got.mean() == pytest.approx(truth.mean(), rel=1e-9)
    assert got.dispersion() == pytest.approx(truth.dispersion(), rel=1e-9)
    assert 1 - got.p_up - got.p_down == pytest.approx(1 - truth.p_up - truth.p_down, rel=1e-6)
    assert got.p99_ratio() == pytest.approx(truth.p99_ratio(), rel=0.15)


def test_poisson_window_has_no_modulation():
    got = fit(3.0, 0.9, {1: 0.0}, 2.0)
    assert got.lam_l == got.lam_h == 3.0


def test_simulated_counts_match_the_moments():
    truth = MMPP2(2.0, 12.0, 0.02, 0.10)
    rng = np.random.default_rng(7)
    rates = truth.rates(200_000, rng) * 10.0
    counts = rng.poisson(rates)
    assert counts.mean() == pytest.approx(truth.mean(), rel=0.03)
    assert counts.var() / counts.mean() == pytest.approx(truth.dispersion(), rel=0.08)
