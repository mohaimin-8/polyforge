"""The joint planner with a tunable horizon and tunable objective weights
(programme 2, WP9).

`TunablePlanner` is `controller.JCACController` plus three things the
harness cannot set through a SystemSpec:

- `horizon_steps` H in 1..HORIZON_STEPS: the planner judges a candidate on
  the first H steps of its forecast instead of all six (10 s each). The
  forecasters extrapolate step by step, so the first H steps of the six-step
  forecast ARE the H-step forecast;
- `weight_alpha`, `weight_beta`, `weight_gamma`: the objective's weights
  (controller.Weights). sim_backend forwards only an arm's beta and gamma;
- `set_limits`, so the planner can be built through baselines.make_baseline
  (simulate.run hands the cluster limits to any controller with the hook).

With none of them set, it is JCACController exactly (test_comparators.py
checks a run bit for bit). A subclass in a new module, not a flag on
JCACController (rule R4).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

_SIM = Path(__file__).resolve().parents[1] / "jcac_sim"
if str(_SIM) not in sys.path:
    sys.path.insert(0, str(_SIM))

import model  # noqa: E402
from controller import ClusterLimits, Forecast, JCACController, Weights  # noqa: E402


@dataclass
class TruncatedForecast(Forecast):
    """A Forecast whose horizon is its first `steps` steps. The residual
    record (`_last_pred`) keeps step one, as before. `Forecast.from_snapshot`
    rebuilds a plain six-step Forecast; the simulator never restores one."""

    steps: int = model.HORIZON_STEPS

    def horizon(self, risk_quantile: float | None = None) -> list:
        return super().horizon(risk_quantile)[: self.steps]


def check_horizon(steps: int) -> int:
    if not 1 <= int(steps) <= model.HORIZON_STEPS:
        raise ValueError(f"horizon_steps must be in 1..{model.HORIZON_STEPS}, not {steps!r}")
    return int(steps)


class TunablePlanner(JCACController):
    """JCACController with a settable horizon and objective weights."""

    name = "wp9_planner"

    def __init__(self, configs: dict, weights: Weights | None = None,
                 limits: ClusterLimits | None = None, *, horizon_steps: int | None = None,
                 weight_alpha: float | None = None, weight_beta: float | None = None,
                 weight_gamma: float | None = None, **kwargs):
        if any(w is not None for w in (weight_alpha, weight_beta, weight_gamma)):
            base = weights or Weights()
            weights = Weights(
                alpha=base.alpha if weight_alpha is None else float(weight_alpha),
                beta=base.beta if weight_beta is None else float(weight_beta),
                gamma=base.gamma if weight_gamma is None else float(weight_gamma),
            )
            if min(weights.alpha, weights.beta, weights.gamma) < 0.0:
                raise ValueError("objective weights must be non-negative")
        super().__init__(configs, weights=weights, limits=limits, **kwargs)
        self.horizon_steps = None if horizon_steps is None else check_horizon(horizon_steps)
        if self.horizon_steps is not None:
            method = kwargs.get("forecast_method", "trend")
            self.forecasts = {tid: TruncatedForecast(method=method, steps=self.horizon_steps)
                              for tid in configs}

    def set_limits(self, limits: ClusterLimits) -> None:
        self.limits = limits
