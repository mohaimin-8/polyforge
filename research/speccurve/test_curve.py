"""The specification curve and its sign-flip test on data with a known answer."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from curve import (curve_from_matrix, enumerate_specs, paired_matrix, plot,  # noqa: E402
                   report, sign_flip_test)


def _runs(effect_by_spec, n_workloads=30, noise=0.05, seed=1):
    rng = np.random.default_rng(seed)
    specs = enumerate_specs({"budget": ["symmetric", "asymmetric"], "granularity": ["overall", "per_class"]})
    rows = []
    base = rng.normal(0.5, 0.1, n_workloads)
    for s in specs.itertuples():
        for w in range(n_workloads):
            a = base[w] + rng.normal(0, noise)
            b = a + effect_by_spec(s) + rng.normal(0, noise)
            rows += [{"budget": s.budget, "granularity": s.granularity, "workload": w, "arm": "joint", "score": a},
                     {"budget": s.budget, "granularity": s.granularity, "workload": w, "arm": "layer", "score": b}]
    return pd.DataFrame(rows)


def test_enumeration_is_the_full_factorial():
    specs = enumerate_specs({"a": [1, 2, 3], "b": ["x", "y"]})
    assert len(specs) == 6 and list(specs.spec) == list(range(6))


def test_a_real_effect_in_every_spec_is_detected():
    runs = _runs(lambda s: 0.05)
    specs, d = paired_matrix(runs, ["budget", "granularity"], "joint", "layer")
    assert d.shape == (4, 30) and (d.mean(axis=1) > 0).all()
    t = sign_flip_test(d, draws=500)
    assert t["p_median_positive"] < 0.01 and t["share_significant_positive"] == 1.0


def test_no_effect_is_not_detected():
    runs = _runs(lambda s: 0.0, seed=3)
    _, d = paired_matrix(runs, ["budget", "granularity"], "joint", "layer")
    t = sign_flip_test(d, draws=500)
    assert t["p_median_positive"] > 0.05 and t["p_median_negative"] > 0.05


def test_an_effect_that_depends_on_one_choice_shows_in_the_sorted_curve(tmp_path):
    runs = _runs(lambda s: 0.08 if s.budget == "asymmetric" else -0.08)
    specs, d = paired_matrix(runs, ["budget", "granularity"], "joint", "layer")
    c = curve_from_matrix(specs, d)
    assert list(c.specs.budget[:2]) == ["symmetric"] * 2 and list(c.specs.budget[2:]) == ["asymmetric"] * 2
    assert c.share_significant(+1) == pytest.approx(0.5) and c.share_significant(-1) == pytest.approx(0.5)
    text = report(c, sign_flip_test(d, draws=200), "joint", "layer", ["budget", "granularity"])
    assert "Whole-curve test" in text
    plot(c, ["budget", "granularity"], tmp_path / "curve.png", "test")
    assert (tmp_path / "curve.png").stat().st_size > 0


def test_missing_workloads_are_refused():
    runs = _runs(lambda s: 0.0)
    runs = runs[~((runs.workload == 0) & (runs.arm == "layer") & (runs.budget == "symmetric"))]
    with pytest.raises(ValueError):
        paired_matrix(runs, ["budget", "granularity"], "joint", "layer")
