"""The cell cache replay on synthetic vectors: timing of stores, isolation, the zero budget."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cell_hits  # noqa: E402


def _vectors(n: int, seed: int = 0) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal((n, 16)).astype(np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


VEC = _vectors(6)
DOCS = [hashlib.sha256(str(i).encode()).hexdigest() for i in range(6)]
KEYB = np.full(6, 40)


def _row(seq, t, tenant, row):
    return {"seq": seq, "t": t, "tenant": tenant, "vector_row": row}


def test_a_repeat_hits_only_once_the_first_answer_is_stored():
    # rows 0 and 2 are the same prompt (same vector, same doc), asked at t=0 and t=5
    table = [_row(0, 0.0, "t00", 0), _row(1, 1.0, "t00", 1), _row(2, 5.0, "t00", 0)]
    fast = cell_hits.replay_cell(table, VEC, DOCS, KEYB, {"t00": 1}, lambda t, s: 2.0)
    slow = cell_hits.replay_cell(table, VEC, DOCS, KEYB, {"t00": 1}, lambda t, s: 9.0)
    assert fast.hits == [False, False, True]     # stored at t=2, asked again at t=5
    assert slow.hits == [False, False, False]    # still being answered at t=5
    assert fast.evictions == 0 and fast.max_entries == 2


def test_partitions_are_per_tenant_and_a_zero_budget_admits_nothing():
    table = [_row(0, 0.0, "t00", 0), _row(1, 5.0, "t01", 0), _row(2, 9.0, "t01", 0)]
    r = cell_hits.replay_cell(table, VEC, DOCS, KEYB, {"t00": 1, "t01": 1}, lambda t, s: 1.0)
    assert r.hits == [False, False, True]        # t00's answer never serves t01
    off = cell_hits.replay_cell(table, VEC, DOCS, KEYB, {"t00": 0, "t01": 0}, lambda t, s: 1.0)
    assert off.hits == [False, False, False]


def test_a_different_prompt_misses_and_hits_are_counted_per_step():
    table = [_row(0, 0.0, "t00", 0), _row(1, 12.0, "t00", 3), _row(2, 15.0, "t00", 0)]
    r = cell_hits.replay_cell(table, VEC, DOCS, KEYB, {"t00": 16}, lambda t, s: 1.0)
    assert r.hits == [False, False, True]
    assert cell_hits.hits_per_step(table, r.hits, steps=60) == {(0, "t00"): 0, (1, "t00"): 1}
