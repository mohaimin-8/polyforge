"""The WP11 cell schedules on synthetic days, windows and prompts (no trace, no LMSYS text)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cell_schedules as cs  # noqa: E402

DAY_BINS = 8640


def test_anchored_counts_start_with_the_anchor_window_and_hit_the_target_exactly():
    days = np.zeros((5, DAY_BINS), dtype=np.int64)
    days[2, 600:660] = 3          # the anchor window: 180 arrivals
    days[[0, 1, 3, 4], 600:660] = 1
    counts, passes = cs.anchored_counts(days, day=2, start=600, steps=60, target=100, rng=np.random.default_rng(0))
    assert counts.sum() == 100 and passes == 1 and counts.max() <= 3   # a thinned anchor window
    counts, passes = cs.anchored_counts(days, day=2, start=600, steps=60, target=300, rng=np.random.default_rng(0))
    assert counts.sum() == 300 and passes == 1 and (counts >= 3).all()  # anchor whole, then other days


def test_a_thin_stream_takes_further_passes_and_a_dead_one_is_refused():
    days = np.zeros((3, DAY_BINS), dtype=np.int64)
    days[:, ::100] = 1             # one arrival every 1,000 s
    counts, passes = cs.anchored_counts(days, day=0, start=0, steps=60, target=8, rng=np.random.default_rng(1))
    assert counts.sum() == 8 and passes > 1
    with pytest.raises(ValueError, match="too thin"):
        cs.anchored_counts(np.zeros((2, DAY_BINS), dtype=np.int64), 0, 0, 60, 5, np.random.default_rng(1))


def test_anchor_is_an_evaluation_window_inside_a_whole_day():
    population = {"workloads": [
        {"split": "evaluation", "tenants": [{"stream": "s", "window": 150}, {"stream": "x", "window": 7}]},
        {"split": "tuning", "tenants": [{"stream": "s", "window": 3}]},
        {"split": "evaluation", "tenants": [{"stream": "s", "window": 999}]}]}
    windows = cs.evaluation_windows(population, "s")
    assert windows == [150, 999]
    for seed in range(5):
        w, day, start = cs.choose_anchor(windows, n_days=2, day_bins=DAY_BINS, rng=np.random.default_rng(seed))
        assert w == 150 and (day, start) == divmod(150 * 60, DAY_BINS)  # 999 lies past day 2
    with pytest.raises(ValueError):
        cs.choose_anchor([999], n_days=2, day_bins=DAY_BINS, rng=np.random.default_rng(0))


def test_split_keeps_every_arrival_and_the_share():
    counts = np.full(60, 50, dtype=np.int64)
    split = cs.split_counts(counts, 0.7, np.random.default_rng(3))
    assert (split["t00"] + split["t01"] == counts).all()
    assert split["t00"].sum() / counts.sum() == pytest.approx(0.7, abs=0.03)
    assert cs.split_counts(counts, 1.0, np.random.default_rng(3))["t01"].sum() == 0


def test_prompt_indices_are_make_schedules_assignment():
    arrivals = [cs.mk.Arrival(float(i), "t01" if i % 3 else "t00") for i in range(10)]
    prompts = [f"p{i}" for i in range(10)]
    requests = cs.mk.assign_prompts(arrivals, cs.TENANTS, prompts)
    assert [prompts[k] for k in cs.prompt_indices(requests, cs.TENANTS)] == [r["prompt"] for r in requests]


def test_vector_rows_apply_take_prompts_rule_and_the_table_checks_lengths():
    cache_bytes = np.array([5, 9000, 3, 4, 6, 2])
    rows = cs.vector_rows(cache_bytes, offset=1, count=3, max_bytes=2400)
    assert rows.tolist() == [2, 3, 4]   # row 1 is too long; offset counts admissible turns
    requests = [{"seq": 0, "t": 0.5, "tenant": "t00", "prompt": "abc", "warmup": True},
                {"seq": 1, "t": 1.5, "tenant": "t01", "prompt": "abcd", "warmup": False},
                {"seq": 2, "t": 2.5, "tenant": "t00", "prompt": "abcd", "warmup": False}]
    table = cs.request_table(requests, np.array([2, 3, 3]), np.array([5, 9000, 3, 4, 4]))
    assert [r["vector_row"] for r in table] == [2, 3, 3] and table[0]["warmup"] == 1
    with pytest.raises(ValueError, match="bytes"):
        cs.request_table(requests, np.array([2, 3, 4]), np.array([5, 9000, 3, 4, 6]))


def test_table_round_trips_and_its_bytes_depend_on_content_only(tmp_path):
    table = [{"seq": 0, "t": 0.25, "tenant": "t00", "warmup": 1, "vector_row": 7, "prompt_bytes": 12}]
    cs.write_table(tmp_path / "a.csv.gz", table)
    cs.write_table(tmp_path / "b.csv.gz", table)
    assert (tmp_path / "a.csv.gz").read_bytes() == (tmp_path / "b.csv.gz").read_bytes()
    back = cs.read_table(tmp_path / "a.csv.gz")
    assert back == [{"seq": 0, "t": 0.25, "tenant": "t00", "warmup": True, "vector_row": 7, "prompt_bytes": 12}]


def _curves(tmp_path: Path, mu: float) -> Path:
    for n in (1, 2, 3):
        d = tmp_path / "curves" / f"small_r{n}"
        d.mkdir(parents=True)
        (d / "load_curve_meta.json").write_text(json.dumps({"mu_pre_rps": 9.0, "mu_post_rps": 9.0,
                                                            "mu_trace_rps": mu * n}))
    return tmp_path / "curves"


def test_build_cell_meets_the_target_on_the_cells_small_pool(tmp_path):
    curves = _curves(tmp_path, mu=0.5)
    days = np.random.default_rng(4).poisson(2.0, size=(4 * DAY_BINS,)).astype(np.int64)
    population = {"workloads": [{"split": "evaluation", "tenants": [{"stream": "s/x", "window": 30}]}]}
    prompts = [f"prompt number {i}" for i in range(5000)]
    cache_bytes = np.array([len(p.encode()) for p in prompts])
    cell = {"cell": 3, "target_util": 0.6, "small_share": 0.75, "stream": "s/x", "pools": [2, 1], "cache": "small"}
    built = cs.build_cell(cell, curves, population, {"s/x": days}, DAY_BINS, lambda: iter(prompts), cache_bytes)
    meta, requests = built["meta"], built["requests"]
    target = round(0.6 * (0.5 * 2) * 600 / 0.75)
    assert meta["requests"] == target == len(requests) == len(built["table"])
    assert meta["anchor"]["window"] == 30 and meta["anchor"]["passes"] == 1
    assert meta["realised_small_share"] == pytest.approx(0.75, abs=0.05)
    assert all(r["warmup"] == (r["t"] < cs.WARMUP_S) for r in requests)
    assert built["pins"]["cache_knob_mb"] == {"t00": 1, "t01": 1} and built["pins"]["pins"] == cs.PINS
    assert sum(c for _, _, c in built["demand"]) == target
    rebuilt = cs.build_cell(cell, curves, population, {"s/x": days}, DAY_BINS, lambda: iter(prompts), cache_bytes)
    assert rebuilt["table"] == built["table"]   # seeded: the same cell twice is the same schedule


def test_capacity_needs_the_measured_curve(tmp_path):
    with pytest.raises(FileNotFoundError):
        cs.capacity(tmp_path, "small", 2)
