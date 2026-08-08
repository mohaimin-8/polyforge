"""Tests for the shared statistics helpers. Run: python -m pytest test_stats.py -q

These decide PASS/FAIL verdicts in published records, so they are tested
against hand-computed expectations rather than against themselves.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from stats import holm_bonferroni  # noqa: E402


def test_holm_thresholds_step_down():
    # n=4, alpha=0.05 -> thresholds 0.0125, 0.01667, 0.025, 0.05 by rank.
    out = holm_bonferroni({"a": 0.001, "b": 0.02, "c": 0.04, "d": 0.9})
    assert out["a"]["rank"] == 1
    assert out["a"]["threshold"] == 0.05 / 4
    assert out["b"]["threshold"] == 0.05 / 3
    assert out["c"]["threshold"] == 0.05 / 2
    assert out["d"]["threshold"] == 0.05


def test_holm_is_step_down_not_per_test():
    # b fails at 0.02 > 0.0167; c must then fail too even though its own
    # threshold (0.025) would admit 0.04... it would not, but the step-down
    # property is what this pins: nothing after the first failure rejects.
    out = holm_bonferroni({"a": 0.001, "b": 0.02, "c": 0.021, "d": 0.9})
    assert out["a"]["reject"] is True
    assert out["b"]["reject"] is False
    assert out["c"]["reject"] is False, "step-down: no rejection after a failure"
    assert out["d"]["reject"] is False


def test_holm_rejects_all_when_all_tiny():
    out = holm_bonferroni({"a": 1e-30, "b": 1e-25, "c": 1e-20})
    assert all(v["reject"] for v in out.values()), \
        "the headline comparisons are far below any correction threshold"


def test_holm_marginal_pass_becomes_a_failure():
    # The audit's concrete example: a lone p=0.0073 is significant at 0.05,
    # but not once it is one of a family — which is the whole point.
    alone = holm_bonferroni({"RB-H1": 0.0073})
    assert alone["RB-H1"]["reject"] is True
    family = holm_bonferroni({f"H{i}": p for i, p in
                              enumerate([0.0073, 0.01, 0.02, 0.03, 0.04, 0.045])})
    assert family["H0"]["reject"] is False, \
        "p=0.0073 must not survive correction inside a six-hypothesis family"


def test_holm_single_hypothesis_is_uncorrected():
    out = holm_bonferroni({"only": 0.04})
    assert out["only"]["threshold"] == 0.05
    assert out["only"]["reject"] is True


def test_holm_preserves_every_input_key():
    pvals = {"x": 0.5, "y": 0.001, "z": 0.2}
    out = holm_bonferroni(pvals)
    assert set(out) == set(pvals)
    for k in pvals:
        assert out[k]["p"] == pvals[k]
