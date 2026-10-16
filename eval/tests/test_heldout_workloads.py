"""The held-out `mmpp` workloads (PREREG_HELDOUT): seeded, not periodic, at
the designed level, and unseen by every earlier experiment and tuning slice."""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

import yaml

EVAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL_DIR))

from harness import workloads  # noqa: E402

HELD_OUT = ("mmpp_crud", "mmpp_mixed")


def test_mmpp_is_deterministic_in_the_seed_and_tenant():
    assert workloads.mmpp_factors(7, "t00", 50) == workloads.mmpp_factors(7, "t00", 50)
    assert workloads.mmpp_factors(7, "t00", 50) != workloads.mmpp_factors(8, "t00", 50)
    assert workloads.mmpp_factors(7, "t00", 50) != workloads.mmpp_factors(7, "t01", 50)


def test_mmpp_runs_at_its_designed_level_and_burst_share():
    f = [x for s in range(300) for x in workloads.mmpp_factors(s, "t00", 121)]
    stationary_high = workloads.MMPP_P_UP / (workloads.MMPP_P_UP + workloads.MMPP_P_DOWN)
    designed = (1 - stationary_high) * workloads.MMPP_LOW + stationary_high * workloads.MMPP_HIGH
    assert abs(statistics.fmean(f) - designed) < 0.03
    assert abs(sum(x > 1.2 for x in f) / len(f) - stationary_high) < 0.02


def test_mmpp_bursts_are_not_periodic():
    """A burst indicator's autocorrelation at every lag the seasonal
    forecaster scans (8-48) stays near zero: there is no cycle to lock onto."""
    f = workloads.mmpp_factors(11, "t00", 4000)
    b = [1.0 if x > 1.2 else 0.0 for x in f]
    mean = statistics.fmean(b)
    var = statistics.fmean((x - mean) ** 2 for x in b)
    for lag in range(8, 49):
        cov = statistics.fmean((b[i] - mean) * (b[i + lag] - mean) for i in range(len(b) - lag))
        assert abs(cov / var) < 0.1, lag


def test_held_out_classes_are_unseen_by_every_other_experiment():
    for path in (EVAL_DIR / "experiments").glob("*.yaml"):
        if path.name == "matrix_heldout.yaml":
            continue
        spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        assert not set(spec.get("workloads") or []) & set(HELD_OUT), path.name
    sys.path.insert(0, str(EVAL_DIR / "baselines"))
    import tune

    assert not {wl for wl, _ in tune.TUNING_CELLS} & set(HELD_OUT)
