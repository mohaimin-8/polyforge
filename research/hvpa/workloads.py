"""WP17 twin workloads: four shapes (plan WP17) at three volatilities (programme 2).

Rates are per 10 s step, relative to BASE_RPS, the rate the initial allocation
(2 pods x 0.5 core of service_plant.Plant) serves at 60 % utilisation:
  step_up      BASE for the first third, then 3 x BASE
  step_down    3 x BASE for the first third, then BASE
  ramp         BASE rising linearly to 4 x BASE
  burst_train  BASE with 2-minute bursts of 3 x BASE every 10 minutes
  azure_app1   the two Azure Functions 2019 peak hours azure_workloads.py
  azure_app2   extracts (data/azure_functions_2019.csv), each minute's count
               spread evenly over its six steps, scaled to a mean of 2 x BASE
Volatility multiplies a shape by exp(x) with x an AR(1) (coefficient 0.7) of
stationary standard deviation sigma, mean-corrected so the mean stays the
shape's: sigma 0.05, 0.2, 0.4. The Azure shapes join `all_shapes()` once
their file exists, after the four synthetic ones, so earlier draws keep their
seeds.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

STEP_S = 10.0
DURATION_S = 3600.0
STEPS = int(DURATION_S / STEP_S)
SHAPES = ("step_up", "step_down", "ramp", "burst_train")
AZURE_SHAPES = ("azure_app1", "azure_app2")
AZURE_FILE = Path(__file__).resolve().parent / "data" / "azure_functions_2019.csv"
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
    if name in AZURE_SHAPES:
        import pandas as pd

        per_minute = pd.read_csv(AZURE_FILE)[name.removeprefix("azure_")].to_numpy(dtype=float)
        per_step = np.repeat(per_minute, int(60 / STEP_S))[:steps]
        return per_step / per_step.mean() * 2 * base
    raise ValueError(f"unknown shape {name!r}")


def all_shapes() -> tuple[str, ...]:
    return SHAPES + (AZURE_SHAPES if AZURE_FILE.exists() else ())


def noisy(rates: np.ndarray, sigma: float, rng: np.random.Generator) -> np.ndarray:
    innov = rng.standard_normal(len(rates)) * sigma * np.sqrt(1 - AR * AR)
    x = np.empty(len(rates))
    x[0] = rng.standard_normal() * sigma
    for i in range(1, len(rates)):
        x[i] = AR * x[i - 1] + innov[i]
    return rates * np.exp(x - 0.5 * sigma * sigma)
