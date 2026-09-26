"""One non-inferiority rule, applied to every registered NI hypothesis.

Audit 2026-09-26: EP-H3 compared the point estimate with the margin (no test),
while TP-H3, BP-H2 and MM-H2 ran a Wilcoxon on shifted differences -- a rank
test that can pass when the MEAN difference exceeds the margin (Azure TP-H3a:
+0.0666 against 0.05; BurstGPT BP-H2: mean 0.104, median 0). The unified rule
is the standard one: non-inferior iff the one-sided upper confidence bound of
the mean difference lies below the margin.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent


def _mod(monkeypatch, tmp_path):
    monkeypatch.setenv("POLYFORGE_ANALYSIS_OUT", str(tmp_path))
    spec = importlib.util.spec_from_file_location("rni", HERE / "reanalysis_noninferiority.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_mean_above_the_margin_is_never_non_inferior(monkeypatch, tmp_path):
    mod = _mod(monkeypatch, tmp_path)
    # Mostly zeros with a few large excesses: the median is 0, the mean is
    # 0.1 -- exactly the shape a shifted rank test lets through.
    d = np.array([0.0] * 45 + [1.0] * 5)
    res = mod.unified(d, block=1)
    assert res["mean"] > mod.MARGIN and not res["ni"]


def test_a_mean_well_inside_the_margin_is_non_inferior(monkeypatch, tmp_path):
    mod = _mod(monkeypatch, tmp_path)
    res = mod.unified(np.full(40, -0.1) + np.linspace(-0.01, 0.01, 40), block=1)
    assert res["ni"] and res["ucb"] < mod.MARGIN


def test_registered_verdicts_are_read_from_the_records(monkeypatch, tmp_path):
    mod = _mod(monkeypatch, tmp_path)
    assert mod.registered("RESULTS_TRACE_PARITY.md", "TP-H3a", "BurstGPT") == "FAIL"
    assert mod.registered("RESULTS_TRACE_PARITY.md", "TP-H3a", "Azure") == "PASS"
    assert mod.registered("RESULTS_EVICTION_PARITY.md", "EP-H3", None) == "PASS"
    assert mod.registered("RESULTS_MODEL_MISMATCH.md", "MM-H2[cap0.75]", None) == "PASS"


def test_record_builds_and_names_every_registered_hypothesis(monkeypatch, tmp_path):
    mod = _mod(monkeypatch, tmp_path)
    assert mod.main() == 0
    text = (tmp_path / mod.RECORD).read_text(encoding="utf-8")
    for hid in ("EP-H3", "TP-H3a", "TP-H3b", "BP-H2a", "BP-H2b", "MM-H2[cap0.75]", "MM-H2[cache1.25]"):
        assert hid in text, hid
    assert "exploratory" in text.lower()


def test_registered_refuses_an_ambiguous_or_missing_row(monkeypatch, tmp_path):
    # Review of bca5acd: a hypothesis id found in two places, or in none, must
    # raise rather than return whichever row a regex reached first.
    mod = _mod(monkeypatch, tmp_path)
    twice = ("## A\n| id | p | verdict |\n|---|---:|---|\n| X-H1 | 0.01 | PASS |\n"
             "## B\n| id | p | verdict |\n|---|---:|---|\n| X-H1 | 0.5 | FAIL |\n")
    import pytest
    with pytest.raises(LookupError):
        mod.registered_in(twice, "X-H1", None)
    assert mod.registered_in(twice, "X-H1", "B") == "FAIL"
    with pytest.raises(LookupError):
        mod.registered_in(twice, "X-H9", None)
    with pytest.raises(LookupError):
        mod.registered_in(twice, "X-H1", "C")
