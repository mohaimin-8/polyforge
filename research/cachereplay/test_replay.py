"""The replay driver: tasks, the reuse rule for capacities without eviction,
pooling with bootstrap intervals, presets."""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

import replay
from streams import Stream


def toy_stream(n=240, seed=4) -> Stream:
    rng = np.random.default_rng(seed)
    centers = rng.normal(size=(30, 16))
    v = centers[rng.integers(30, size=n)] + 0.3 * rng.normal(size=(n, 16))
    v = (v / np.linalg.norm(v, axis=1, keepdims=True)).astype(np.float32)
    meta = pd.DataFrame({"row": np.arange(n), "language": ["English"] * n, "prompt_bytes": 50,
                         "key_bytes": 57, "completion_bytes": rng.integers(1, 2000, size=n),
                         "code": np.arange(n) % 4 == 0, "doc": [f"{i:032x}" for i in range(n)]})
    return Stream(meta=meta, vectors=v, embed_seconds=0.0, embedded=0)


def test_tasks_rows_and_reuse_rule():
    grid = replay.Grid(("costaware", "lru"), (0.8, 0.9), (4, 16, 1000, 0), dt=1.2, k=8)
    stream = toy_stream()
    tasks = replay.build_tasks(stream, ("all", "code"), 60, grid)
    assert [t.cls for t in tasks].count("all") == 4 and [t.cls for t in tasks].count("code") == 1
    rows = pd.DataFrame(replay.run_tasks(tasks, stream.vectors, workers=1, run_dir=None))
    assert len(rows) == len(tasks) * 2 * 2 * 4
    for _, g in rows.groupby(["class", "tenant", "policy", "tau"]):
        g = g.set_index("capacity")
        assert g.loc[1000, "evictions"] == 0 and bool(g.loc[0, "reused"])
        assert g.loc[1000, "warm_hits"] == g.loc[0, "warm_hits"]
        assert g.loc[4, "resident_end"] == 4 and bool(g.loc[4, "filled"])
    summary = replay.pool(rows)
    assert {"warm_hit_rate", "warm_ci_lo", "warm_ci_hi", "warm_saved_share"} <= set(summary.columns)
    allc = summary[(summary["class"] == "all")]
    assert (allc.warm_ci_lo <= allc.warm_hit_rate + 1e-12).all() and (allc.warm_hit_rate <= allc.warm_ci_hi + 1e-12).all()
    assert summary[summary["class"] == "code"].warm_ci_lo.isna().all()   # one tenant: no interval


def test_resolve_presets_and_overrides():
    ns = argparse.Namespace(preset="smoke", limit=None, stream_length=None, workers=None, threads=None,
                            capacities="8,0", classes="all", taus="0.85", policies="lru",
                            key_format="raw", dt=2.0, k=16, out=None)
    cfg = replay.resolve(ns)
    assert cfg["limit"] == 2000 and cfg["capacities"] == (8, 0) and cfg["classes"] == ("all",)
    assert cfg["taus"] == (0.85,) and cfg["policies"] == ("lru",) and cfg["key_format"] == "raw"
    full = replay.PRESETS["full"]
    assert full["stream_length"] == 32768 and full["capacities"][-2:] == (16384, 0)
