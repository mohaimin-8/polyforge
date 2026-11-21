"""The WP11 scorer's agreement statistics and its valid-run filter."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import score  # noqa: E402


def test_agreement_of_a_perfect_prediction():
    a = score.agreement([(1.0, 1.0), (2.0, 2.0), (3.0, 3.0)])
    assert a["mape_median"] == 0.0 and a["spearman"] == pytest.approx(1.0) and a["mean_diff"] == 0.0


def test_agreement_skips_missing_values_and_reads_a_bias():
    a = score.agreement([(1.2, 1.0), (2.4, 2.0), (math.nan, 3.0), (3.6, 3.0)])
    assert a["cells"] == 3 and a["mape_median"] == pytest.approx(0.2) and a["mean_diff"] == pytest.approx(0.4)


def test_only_valid_complete_runs_are_scored(tmp_path):
    for name, valid in (("run0", True), ("run1", False), ("run2", True)):
        d = tmp_path / name
        d.mkdir()
        (d / "run.json").write_text(json.dumps({"valid": valid, "complete": True}))
        if name != "run2":
            (d / "k6-samples.csv.gz").write_bytes(b"")
    assert [p.parent.name for p in score.valid_runs(tmp_path)] == ["run0"]


def test_a_run_off_the_mains_at_its_start_or_end_is_not_scored(tmp_path):
    for name, start, end in (("run0", True, True), ("run1", True, False), ("run2", False, True), ("run3", None, None)):
        d = tmp_path / name
        d.mkdir()
        (d / "run.json").write_text(json.dumps({"valid": True, "complete": True, "ac_power": start,
                                                "ac_power_end": end}))
        (d / "k6-samples.csv.gz").write_bytes(b"")
    assert [p.parent.name for p in score.valid_runs(tmp_path)] == ["run0", "run3"]   # unknown counts as the mains


def test_the_report_names_the_replica_scaling_the_primary_prediction_used():
    linear = {"rules": {"slo_factor": 3.0}, "calibration": {"small": {}, "mid": {}}}
    assert score.scaling_note(linear) == "The primary prediction keeps the simulator's capacity, linear in replicas"
    fitted = {"rules": {"replica_scaling": "fitted"},
              "calibration": {"small": {"replica_scaling_exponent": 0.5975}, "mid": {"replica_scaling_exponent": 0.359}}}
    assert score.scaling_note(fitted) == ("The primary prediction scales each tier's capacity with replicas as "
                                          "n ** beta, beta fitted to the calibration's curves (small 0.598, mid 0.359)")


def test_spearman_gives_tied_values_their_average_rank_whatever_the_order():
    """A bootstrap resample repeats cells, and cells can share a hit rate: ties take the mean of
    their ranks (the old argsort-of-argsort ranks gave 1.0 or 0.8 here, by sort order)."""
    a, b = np.array([1.0, 2.0, 2.0, 3.0]), np.array([1.0, 2.0, 3.0, 4.0])
    assert score.spearman(a, b) == pytest.approx(3 / math.sqrt(10))
    assert score.spearman(a[::-1], b[::-1]) == pytest.approx(3 / math.sqrt(10))
    assert list(score.average_ranks([5.0, 1.0, 5.0, 5.0])) == [3.0, 1.0, 3.0, 3.0]
    repeated = np.array([0.2, 0.2, 0.5, 0.9]), np.array([0.3, 0.3, 0.4, 0.8])
    assert score.spearman(*repeated) == pytest.approx(1.0)
