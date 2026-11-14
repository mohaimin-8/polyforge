"""WP17 twin workloads: four shapes (plan WP17) at three volatilities (programme 2).

Rates are per 10 s step, relative to BASE_RPS, the rate the initial allocation
(2 pods x 0.5 core of plant.Plant) serves at 60 % utilisation:
  step_up      BASE for the first third, then 3 x BASE
  step_down    3 x BASE for the first third, then BASE
  ramp         BASE rising linearly to 4 x BASE
  burst_train  BASE with 2-minute bursts of 3 x BASE every 10 minutes
Volatility multiplies a shape by exp(x) with x an AR(1) (coefficient 0.7) of
stationary standard deviation sigma, mean-corrected so the mean stays the
shape's: sigma 0.05, 0.2, 0.4. The plan's two Azure Functions per-minute
workloads join when that trace is downloaded (research/traces/
etl_azure_functions.py reads it); they are not part of the a-priori run.
"""

from __future__ import annotations

import numpy as np

STEP_S = 10.0
DURATION_S = 3600.0
STEPS = int(DURATION_S / STEP_S)
SHAPES = ("step_up", "step_down", "ramp", "burst_train")
VOLATILITIES = (0.05, 0.2, 0.4)
AR = 0.7


def base_rps(plant) -> float:
    return 0.6 * 2 * plant.capacity_rps(0.5)


def shape(name: str, base: float, steps: int = STEPS) -> np.ndarray:
    t = np.arange(steps) * STEP_S
    if name == "step_up":
        return np.where(t < DURATION_S / 3, base, 3 * base)
    if name == "step_down":
        return np.where(t < DURATION_S / 3, 3 * base, base)
    if name == "ramp":
        return base * (1 + 3 * t / t[-1])
    if name == "burst_train":
        return np.where((t % 600) < 120, 3 * base, base)
    raise ValueError(f"unknown shape {name!r}")


def noisy(rates: np.ndarray, sigma: float, rng: np.random.Generator) -> np.ndarray:
    innov = rng.standard_normal(len(rates)) * sigma * np.sqrt(1 - AR * AR)
    x = np.empty(len(rates))
    x[0] = rng.standard_normal() * sigma
    for i in range(1, len(rates)):
        x[i] = AR * x[i - 1] + innov[i]
    return rates * np.exp(x - 0.5 * sigma * sigma)
