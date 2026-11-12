"""Native replay hits the target rate with real arrivals only."""

import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from native import DAY_BINS, series  # noqa: E402


def _days(rate_per_bin: float, n: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.poisson(rate_per_bin, size=(n, DAY_BINS))


@pytest.mark.parametrize("rate", [0.5, 3.0, 20.0])
def test_series_hits_the_target_with_integer_arrivals(rate):
    rates, passes = series(_days(1.0, 40), rate, 120, np.random.default_rng(1))
    counts = rates * 10
    assert np.allclose(counts, np.round(counts))
    assert counts.sum() == round(rate * 10 * 120)
    assert passes >= 1


def test_a_busy_stream_is_thinned_not_scaled():
    busy = _days(400.0, 3)
    rates, passes = series(busy, 2.0, 60, np.random.default_rng(2))
    assert passes == 1 and (rates * 10).sum() == 1200


def test_a_thin_stream_takes_more_passes_or_refuses():
    thin = _days(0.3, 5)
    rates, passes = series(thin, 0.5, 60, np.random.default_rng(3))
    assert passes > 1
    with pytest.raises(ValueError):
        series(_days(0.0001, 2, seed=9) + 0, 50.0, 60, np.random.default_rng(4))
