"""A two-state Markov-modulated Poisson process per window (WP8 step 2).

Discrete time, one step per 10 s bin. States low and high with Poisson means
lam_l < lam_h per bin; the chain moves low -> high with probability p_up and
high -> low with p_down. With pi_h = p_up / (p_up + p_down) and
r = 1 - p_up - p_down:

    mean            m   = pi_l lam_l + pi_h lam_h
    variance        var = m + pi_l pi_h (lam_h - lam_l)^2
    autocorrelation acf(k) = (var - m) / var * r^k          (k >= 1 bins)

Fit by moment matching: m and the dispersion D = var / m fix the modulation
variance V = (D - 1) m; the measured autocorrelations fix r (least squares on
log acf(k) - log((D - 1) / D) = k log r); and pi_h is chosen so the mixture's
99th percentile over m matches the measured p99 ratio, subject to lam_l >= 0.
A window with D <= 1 is plain Poisson.

The simulator draws its own Poisson noise around each step's rate
(`simulate.jitter_buckets`), so `rates()` returns the modulating chain's
per-step rate (requests per second), not counts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.stats import poisson

BIN_S = 10


@dataclass(frozen=True)
class MMPP2:
    lam_l: float    # requests per bin, low state
    lam_h: float    # requests per bin, high state
    p_up: float     # P(low -> high) per bin
    p_down: float   # P(high -> low) per bin

    @property
    def pi_h(self) -> float:
        s = self.p_up + self.p_down
        return self.p_up / s if s > 0 else 0.0

    def mean(self) -> float:
        return (1 - self.pi_h) * self.lam_l + self.pi_h * self.lam_h

    def dispersion(self) -> float:
        m = self.mean()
        return (m + (1 - self.pi_h) * self.pi_h * (self.lam_h - self.lam_l) ** 2) / m if m > 0 else math.nan

    def acf(self, k: int) -> float:
        d = self.dispersion()
        return (d - 1) / d * (1 - self.p_up - self.p_down) ** k

    def p99_ratio(self) -> float:
        return _mixture_quantile(self.lam_l, self.lam_h, self.pi_h, 0.99) / self.mean()

    def rates(self, steps: int, rng: np.random.Generator, scale: float = 1.0) -> np.ndarray:
        """Per-step rate (requests per second) of the modulating chain,
        started from its stationary distribution."""
        high = rng.random() < self.pi_h
        out = np.empty(steps)
        for i in range(steps):
            out[i] = (self.lam_h if high else self.lam_l) / BIN_S * scale
            u = rng.random()
            high = (u >= self.p_down) if high else (u < self.p_up)
        return out


def _mixture_quantile(lam_l: float, lam_h: float, pi_h: float, q: float) -> float:
    hi = int(poisson.ppf(q, max(lam_h, 1e-12))) + 1
    k = np.arange(0, hi + 1)
    cdf = (1 - pi_h) * poisson.cdf(k, lam_l) + pi_h * poisson.cdf(k, lam_h)
    return float(k[np.searchsorted(cdf, q)])


def fit(m: float, dispersion: float, acf: dict[int, float], p99_ratio: float) -> MMPP2:
    """Moment-matched MMPP2 for one window: m = mean count per bin,
    acf = {lag in bins: autocorrelation}."""
    if not m > 0:
        raise ValueError("mean count must be positive")
    if not dispersion > 1.0 + 1e-9:
        return MMPP2(m, m, 0.0, 1.0)
    v = (dispersion - 1.0) * m
    c = (dispersion - 1.0) / dispersion
    # One estimate of log r per short lag, equally weighted: (log acf(k) -
    # log c) / k. Lags beyond 6 bins are left out (a 60-bin window measures
    # them too noisily), and an autocorrelation at or above c is capped just
    # below it rather than dropped, so the strongest windows still count.
    est = [(math.log(min(a, c * (1 - 1e-6))) - math.log(c)) / k
           for k, a in acf.items() if k <= 6 and a is not None and a > 0]
    r = min(0.999, max(0.0, math.exp(sum(est) / len(est)))) if est else 0.0
    pi_max = min(0.5, m * m / (v + m * m))  # lam_l >= 0; high is the rarer state

    def build(pi_h: float) -> MMPP2:
        delta = math.sqrt(v / ((1 - pi_h) * pi_h))
        lam_l = max(0.0, m - pi_h * delta)
        return MMPP2(lam_l, lam_l + delta, pi_h * (1 - r), (1 - pi_h) * (1 - r))

    # Identifiable branch: the high state must hold more than 1% of bins to
    # move the 99th percentile at all. On that branch the count quantile is
    # discrete and not monotone in pi_h (just above 1% it sits at the high
    # state's lower tail), so search a grid: the pi_h whose p99 ratio is
    # closest to the target, the middle of the best plateau on ties.
    lo = min(0.0101, pi_max)
    grid = np.geomspace(lo, pi_max, 400) if pi_max > lo else np.array([pi_max])
    def miss(p: float) -> float:
        got = build(p).p99_ratio()
        return abs(math.log(got / p99_ratio)) if got > 0 and p99_ratio > 0 else math.inf

    err = np.array([miss(p) for p in grid])
    best = np.flatnonzero(err <= err.min() + 1e-12)
    return build(float(grid[best[len(best) // 2]]))
