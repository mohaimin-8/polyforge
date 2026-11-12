"""Fits, bytes per entry and the plant-2 block on synthetic replay tables."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import fit


def synthetic_rows(hmax=0.3, k_half=500.0, tenants=8, noise=0.004, seed=3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for t in range(tenants):
        for cap in (32, 64, 128, 256, 512, 1024, 2048, 0):
            entries = cap if cap else 3000
            h = hmax * entries / (entries + k_half) + rng.normal(0, noise)
            rows.append({"tenant": t, "capacity": cap, "warm_hits": round(h * 10000), "warm_lookups": 10000,
                         "warm_mean_resident": float(entries), "warm_mean_completion_bytes": 800.0,
                         "warm_mean_key_bytes": 300.0, "filled": bool(cap)})
    return pd.DataFrame(rows)


def test_fit_curve_recovers_parameters_and_refuses_too_few_points():
    x = np.array([10, 30, 100, 300, 1000, 3000, 10000.0])
    hmax, half = fit.fit_curve(x, fit.saturating(x, 0.42, 750.0))
    assert hmax == pytest.approx(0.42, rel=1e-3) and half == pytest.approx(750.0, rel=1e-3)
    assert fit.fit_curve(x[:2], np.array([0.1, 0.2])) is None
    assert fit.fit_curve(x, np.zeros_like(x)) is None


def test_bytes_model_overhead_is_heap_minus_key_and_completion():
    gomem = {"dims": 384, "go_version": "go1.x", "runs": [
        {"lengths": "minimal", "n": 10, "heap_bytes_per_entry": 2300.0, "mean_key_bytes": 15, "mean_completion_bytes": 0},
        {"lengths": "sample", "n": 10, "heap_bytes_per_entry": 3500.0, "mean_key_bytes": 300, "mean_completion_bytes": 800},
        {"lengths": "sample", "n": 20, "heap_bytes_per_entry": 3480.0, "mean_key_bytes": 300, "mean_completion_bytes": 780}]}
    m = fit.bytes_model(gomem)
    assert m["overhead_bytes"] == pytest.approx(2400.0) and m["overhead_range"] == [2400.0, 2400.0]
    assert m["vector_bytes"] == 1536 and m["minimal_entry_bytes"] == 2300.0


def test_fit_group_recovers_the_curve_in_entries_and_mb_with_intervals():
    rows = synthetic_rows()
    out = fit.fit_group(rows, overhead=2000.0, rng=np.random.default_rng(0), reps=100)
    ent, mb, knob = out["entries"], out["mb"], out["knob_mb"]
    assert ent["cache_hit_max"] == pytest.approx(0.3, abs=0.02) and ent["half"] == pytest.approx(500, rel=0.15)
    assert ent["cache_hit_max_ci95"][0] <= ent["cache_hit_max"] <= ent["cache_hit_max_ci95"][1]
    per_entry = 2000.0 + 300 + 800
    assert mb["half"] == pytest.approx(ent["half"] * per_entry / fit.MIB, rel=1e-3)
    assert knob["half"] == pytest.approx(ent["half"] * 800 / fit.MIB, rel=1e-3)
    assert {p["capacity"] for p in out["points"]} == {32, 64, 128, 256, 512, 1024, 2048}


def test_pooled_points_resample_and_plant2_block():
    a = fit.group_arrays(synthetic_rows(tenants=3))
    whole = fit.pooled_points(a, 2000.0)
    same = fit.pooled_points(a, 2000.0, np.array([0, 1, 2]))
    np.testing.assert_allclose(whole["hit_rate"], same["hit_rate"])
    curves = {"all": {"costaware": {"0.85": {"mb": {"cache_hit_max": 0.2, "half": 3.0},
                                             "knob_mb": {"cache_hit_max": 0.2, "half": 1.0}}}}}
    block = fit.plant2_block(curves)
    assert block["cache_hit_max"] == 0.2 and block["cache_half_mb"] == 3.0 and block["cache_half_mb_knob_unit"] == 1.0
    assert "note" in fit.plant2_block({})
