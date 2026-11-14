"""The WP11 error breakdown on a made-up cell table."""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import diagnose  # noqa: E402


def test_errors_are_signed_and_absolute_per_level():
    df = pd.DataFrame({"small_pods": [1, 1, 3], "att_obs": [0.5, 0.6, 0.4], "att_primary": [0.6, 0.5, 0.9],
                       "att_measured_capacity": [0.5, 0.6, 0.45], "att_steady_cache": [0.6, 0.5, 0.9]})
    lines = diagnose.by_factor(df, "small_pods")
    one = next(l for l in lines if l.startswith("| 1 |"))
    three = next(l for l in lines if l.startswith("| 3 |"))
    assert "+0.000 / 0.100" in one and "+0.500 / 0.500" in three and "+0.050 / 0.050" in three
