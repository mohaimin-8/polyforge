"""Precision-recall arithmetic, the published tables' parsers, untrusted-input checks."""

from __future__ import annotations

import io

import numpy as np
import pandas as pd
import pytest

import oldcheck
import precision


def test_wilson_interval_known_values():
    lo, hi = precision.wilson(0, 10)
    assert lo == pytest.approx(0.0, abs=1e-12) and hi == pytest.approx(0.2775, abs=1e-4)
    lo, hi = precision.wilson(5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-4) and hi == pytest.approx(0.7634, abs=1e-4)


def test_pr_at_and_average_precision_on_a_toy_ranking():
    sims = np.array([0.99, 0.9, 0.8, 0.7, 0.6])
    labels = np.array([1, 0, 1, 0, 0])
    m = precision.pr_at(sims, labels, 0.8)
    assert m["pairs_at_or_above"] == 3 and m["precision"] == pytest.approx(2 / 3)
    assert m["recall"] == 1.0 and m["wrong_hit_rate"] == pytest.approx(1 / 3)
    assert precision.average_precision(sims, labels) == pytest.approx((1 + 2 / 3) / 2)
    assert precision.average_precision(np.array([0.9, 0.1]), np.array([1, 0])) == 1.0


def test_lmsys_proxy_reads_the_published_table():
    p = precision.lmsys_proxy()
    assert p["by_tau"]["0.85"]["precision_rho_0.70"] == 0.313 and p["by_tau"]["0.85"]["hit_rate"] == 0.241
    assert p["by_tau"]["0.95"]["wrong_hit_rate"] == pytest.approx(0.662)
    assert p["first_turn_precision_at_0.85"] == 0.353 and set(p["by_tau"]) >= {"0.75", "0.95"}


def test_published_semantic_cache_table_and_old_ladder():
    t = oldcheck.published_table()
    assert t["adaptive"]["0.85"] == 0.296 and t["fixed200"]["0.85"] == 0.043
    assert t["ladder_at_0.85"][100000] == 0.296 and t["ladder_at_0.85"][200] == 0.043
    assert t["supplementary"] == {"entries": 10000, "hit_rate_at_0.85": pytest.approx(0.153)}
    assert t["fit"] == {"hmax": 0.285, "k_half": 6569}
    assert oldcheck.old_ladder(100000) == sorted(t["ladder_at_0.85"])


def test_against_published_compares_at_three_decimals():
    class Old:
        THRESHOLDS = (0.85,)

    half = 100000
    nearest = {half: np.array([0.9, 0.1]), 200: np.array([0.9, 0.1, 0.1, 0.1])}
    pub = {"adaptive": {"0.85": 0.5}, "fixed200": {"0.85": 0.25}, "ladder_at_0.85": {}}
    ok = oldcheck.against_published(Old, nearest, half, pub)
    assert ok["reproduced"] is True and ok["max_abs_diff_at_3dp"] == 0.0
    pub["adaptive"]["0.85"] = 0.6
    bad = oldcheck.against_published(Old, nearest, half, pub)
    assert bad["reproduced"] is False and bad["max_abs_diff_at_3dp"] == pytest.approx(0.1)


def test_load_pairs_rejects_unlabelled_rows(tmp_path):
    path = tmp_path / "qqp.parquet"
    pd.DataFrame({"question1": ["a", "b"], "question2": ["c", "d"], "label": [1, -1]}).to_parquet(path)
    with pytest.raises(SystemExit):
        precision.load_pairs(path, None)
    pd.DataFrame({"question1": ["a"], "question2": ["c"], "label": [0]}).to_parquet(path)
    assert len(precision.load_pairs(path, None)) == 1


def test_download_refuses_a_non_parquet_payload(tmp_path, monkeypatch):
    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(precision.urllib.request, "urlopen", lambda req, timeout: Resp(b"<html>nope</html>"))
    with pytest.raises(SystemExit):
        precision.download("validation", tmp_path)
    assert not (tmp_path / "validation.parquet").exists()
