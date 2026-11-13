"""Unit tests of the mock's input builder."""

from __future__ import annotations

import json
import math

import pytest

import build_latency as bl


def test_lognormal_recovers_mean_and_p95():
    mu, sigma = bl.lognormal_from_mean_p95(1.5513, 1.5951)
    assert math.exp(mu + sigma * sigma / 2) == pytest.approx(1.5513, rel=1e-9)
    assert math.exp(mu + bl.Z95 * sigma) == pytest.approx(1.5951, rel=1e-9)


@pytest.mark.parametrize("mean, p95", [(2.0, 1.0), (1.0, 1.0), (1.0, 4.0), (0.0, 1.0)])
def test_lognormal_refuses_impossible_pairs(mean, p95):
    with pytest.raises(ValueError):
        bl.lognormal_from_mean_p95(mean, p95)


def test_even_quantiles_and_filler():
    assert bl.even_quantiles([0, 10], 2) == [2.5, 7.5]
    assert bl.even_quantiles([5], 3) == [5, 5, 5]
    with pytest.raises(ValueError):
        bl.even_quantiles([1], 0)
    assert len(bl.filler(0)) == 1 and len(bl.filler(1000).encode()) == 1000
    assert len(bl.filler(10**9)) == bl.MAX_ANSWER_BYTES


def test_read_column_filters_and_skips_blanks(tmp_path):
    path = tmp_path / "r.csv"
    path.write_text("phase,latency_s\nbaseline,1.0\nload,9.0\nbaseline,\nbaseline,2.0\n")
    assert bl.read_column(str(path), "latency_s", ["phase=baseline"]) == [1.0, 2.0]
    with pytest.raises(ValueError):
        bl.read_column(str(path), "missing")
    with pytest.raises(ValueError):
        bl.read_column(str(path), "latency_s", ["phase"])
    with pytest.raises(ValueError):
        bl.read_column(str(path), "latency_s", ["phase=none"])


def test_cli_writes_each_kind(tmp_path):
    csv_path = tmp_path / "r.csv"
    csv_path.write_text("latency_ms,completion_bytes\n100,10\n200,30\n")
    out = tmp_path / "o.json"
    assert bl.main(["samples", "--csv", str(csv_path), "--column", "latency_ms", "--unit", "ms",
                    "--source", "t", "--out", str(out)]) == 0
    assert json.loads(out.read_text())["values_s"] == [0.1, 0.2]
    assert bl.main(["lognormal", "--mean-ms", "100", "--p95-ms", "150", "--source", "t", "--out", str(out)]) == 0
    assert json.loads(out.read_text())["kind"] == "lognormal"
    assert bl.main(["answers", "--csv", str(csv_path), "--column", "completion_bytes", "--count", "2",
                    "--source", "t", "--out", str(out)]) == 0
    assert [len(a) for a in json.loads(out.read_text())["answers"]] == [15, 25]
