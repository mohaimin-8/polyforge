"""Native replay of real request streams for population tenants (WP8 step 5).

The MMPP2 tenants rescale a window's rate, which keeps the modulation's
relative shape but not its weight against Poisson noise (POPULATION.md). Here
a tenant's demand is built from real 10 s request counts instead:

- superpose whole real days of the tenant's stream at their own time of
  day (the same start bin in every day), in a seeded random order, until
  the window holds the tenant's target number of requests;
- the last day is thinned by keeping exactly the missing number of its
  real arrivals, drawn without replacement, so the total lands on the target;
- if the stream's days run out first, further passes use other windows of
  the same days (a fresh random start per pass), and the series records
  how many passes it needed.

No rate is rescaled: every request in the series is a real arrival (thinned
or not), so burstiness survives at the tenant's rate as far as the data
allow. Superposing many independent days averages their noise, which is
what real aggregation does too.

    python native.py      # writes data/counts_10s.npz from the raw traces (DuckDB)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
COUNTS = HERE / "data" / "counts_10s.npz"
BIN_S = 10
DAY_BINS = 86400 // BIN_S


def save_counts() -> Path:
    """Per stream, 10 s request counts over the whole trace, as int32."""
    sys.path.insert(0, str(HERE))
    from features import bin_counts, connect, streams

    con = connect()
    arrays = {name.replace("/", "__"): bin_counts(con, sql).astype(np.int32)
              for name, sql in streams(con).items()}
    COUNTS.parent.mkdir(exist_ok=True)
    np.savez_compressed(COUNTS, **arrays)
    return COUNTS


def days(counts: np.ndarray) -> np.ndarray:
    """The stream's full days, shape (days, DAY_BINS), keeping days with any request."""
    n = len(counts) // DAY_BINS
    d = counts[: n * DAY_BINS].reshape(n, DAY_BINS)
    return d[d.sum(axis=1) > 0]


def series(day_counts: np.ndarray, rate_rps: float, steps: int, rng: np.random.Generator) -> tuple[np.ndarray, int]:
    """Per-step request rate (requests per second) for `steps` 10 s steps
    at mean `rate_rps`, and the number of passes over the days it needed."""
    if steps > DAY_BINS:
        raise ValueError("a native series spans at most one day")
    target = int(round(rate_rps * BIN_S * steps))  # whole requests, so the loop ends exactly
    acc = np.zeros(steps, dtype=np.int64)
    start = int(rng.integers(0, DAY_BINS - steps + 1))
    passes = 0
    while acc.sum() < target:
        passes += 1
        if passes > 50:
            raise ValueError("stream too thin for the target rate")
        for d in rng.permutation(len(day_counts)):
            w = day_counts[d, start:start + steps]
            have, add = acc.sum(), w.sum()
            if add == 0:
                continue
            if have + add <= target:
                acc += w
            else:
                # keep exactly the missing number of this day's real arrivals
                acc += rng.multivariate_hypergeometric(w, int(target - have))
                break
            if acc.sum() >= target:
                break
        start = int(rng.integers(0, DAY_BINS - steps + 1))  # later passes: other windows of the same days
    return acc / BIN_S, passes


def load_days() -> dict[str, np.ndarray]:
    with np.load(COUNTS) as z:
        return {k.replace("__", "/"): days(z[k]) for k in z.files}


if __name__ == "__main__":
    print(save_counts())
