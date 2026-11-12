"""Replay semantics on hand-made vectors, and parity with the Go code
(testdata/go_parity.json is written by gocheck/main.go from the real
eviction policies and the real SemanticCache)."""

from __future__ import annotations

import hashlib
import json
import random
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest

from cachesim import NeighborIndex, TenantCache, replay_insert_then_query, replay_stream
from engine import (DEFAULT_THRESHOLD, LRU, CostAware, CostAwareReference, cache_key_text, doc_id,
                    store_cost_usd, tie_key)
from streams import doc_tiekeys

HERE = Path(__file__).resolve().parent
GO_TRACE = HERE / "testdata" / "go_parity.json"


def f32(rows) -> np.ndarray:
    return np.asarray(rows, dtype=np.float32)


class Spy(LRU):
    """LRU that records every call, to check the order of policy calls."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = []

    def on_store(self, doc, cost, density, tiekey, now) -> None:
        self.calls.append(("store", doc, round(density, 6)))
        super().on_store(doc, cost, density, tiekey, now)

    def on_hit(self, doc, now) -> None:
        self.calls.append(("hit", doc))
        super().on_hit(doc, now)

    def on_remove(self, doc) -> None:
        self.calls.append(("remove", doc))
        super().on_remove(doc)


def gateway_step(cache: TenantCache, pos: int, now: float = 0.0) -> bool:
    """server.go chat: Lookup, and Store on a miss."""
    hit, best, score = cache.lookup(pos, now)
    if not hit:
        cache.store(pos, now, 1e-4, 10, 10, searched=(best, score))
    return hit


def test_hit_iff_score_at_or_above_threshold():
    vecs = f32([[1, 0], [0.75, 0.6614378], [0.75, 0.6614378]])
    at = TenantCache(vecs, threshold=0.75, capacity=10, policy="lru")
    at.store(0, 0.0, 1e-4, 1, 1)
    assert at.lookup(1, 1.0)[:1] == (True,)          # cosine exactly 0.75 >= 0.75
    above = TenantCache(vecs, threshold=0.7500001, capacity=10, policy="lru")
    above.store(0, 0.0, 1e-4, 1, 1)
    hit, best, score = above.lookup(1, 1.0)
    assert (hit, best, score) == (False, 0, 0.75)


def test_default_threshold_and_unbounded_capacity():
    vecs = f32(np.eye(4))
    assert TenantCache(vecs, threshold=0, capacity=0, policy="lru").threshold == DEFAULT_THRESHOLD
    assert TenantCache(vecs, threshold=1.5, capacity=0, policy="lru").threshold == DEFAULT_THRESHOLD
    cache = TenantCache(vecs, threshold=0.9, capacity=0, policy="lru")
    for p in range(4):
        assert not gateway_step(cache, p)
    assert cache.evictions == 0 and len(cache.pos_of_doc) == 4


def test_hit_miss_insert_evict_order_lru():
    # a, b, a' (near a), c, b: capacity 2
    vecs = f32([[1, 0, 0], [0, 1, 0], [0.99, 0.141, 0], [0, 0, 1], [0, 1, 0]])
    docs = ["a", "b", "a2", "c", "b"]
    spy = Spy()
    cache = TenantCache(vecs, threshold=0.9, capacity=2, policy=spy, docs=docs)
    hits = [gateway_step(cache, p, float(p)) for p in range(5)]
    assert hits == [False, False, True, False, False]
    assert spy.calls == [("store", "a", 0.0), ("store", "b", 0.0), ("hit", "a"),
                         ("store", "c", 0.0), ("remove", "b"), ("store", "b", 0.0), ("remove", "a")]
    assert sorted(cache.pos_of_doc) == ["b", "c"]


def test_density_is_best_score_before_insert_and_zero_when_not_positive():
    vecs = f32([[1, 0], [0.6, 0.8], [-1, 0]])
    spy = Spy()
    spy.needs_density = True
    cache = TenantCache(vecs, threshold=0.99, capacity=10, policy=spy)
    for p in range(3):
        gateway_step(cache, p)
    # empty partition -> 0; b's best is a at 0.6; c's best is b at -0.6, not > 0 -> 0
    assert [c[2] for c in spy.calls] == [0.0, 0.6, 0.0]


def test_tie_goes_to_smaller_doc_id_and_upsert_replaces():
    vecs = f32([[1, 0], [1, 0], [1, 0]])
    spy = Spy()
    docs = ["ffff0000", "0000ffff", "x"]
    cache = TenantCache(vecs, threshold=0.5, capacity=10, policy=spy, docs=docs,
                        tiekeys=[0xFFFF0000, 0x0000FFFF, 0])
    cache.store(0, 0.0, 1e-4, 5, 5)
    cache.store(1, 0.0, 1e-4, 7, 5)
    assert cache.lookup(2, 1.0)[:2] == (True, 1)
    assert spy.calls[-1] == ("hit", "0000ffff")
    up = TenantCache(vecs, threshold=0.5, capacity=10, policy="lru", docs=["d", "d", "e"])
    up.store(0, 0.0, 1e-4, 5, 3)
    up.store(1, 0.0, 1e-4, 9, 3)                      # same doc ID: replaced, not added
    assert len(up.pos_of_doc) == 1 and up.used_bytes == 9 and up.resident.tolist() == [False, True, False]


def test_byte_budget_zero_admits_nothing_and_budget_evicts():
    vecs = f32(np.eye(3))
    cache = TenantCache(vecs, threshold=0.9, capacity=0, policy="lru", budget_bytes=0)
    assert cache.store(0, 0.0, 1e-4, 60, 5) is False and cache.n == 0
    cache = TenantCache(vecs, threshold=0.9, capacity=0, policy="lru", budget_bytes=100)
    cache.store(0, 0.0, 1e-4, 60, 5)
    cache.store(1, 1.0, 1e-4, 60, 5)                  # 120 > 100: the LRU entry goes
    assert sorted(cache.pos_of_doc) == [1] and cache.used_bytes == 60
    cache.set_budget_bytes(-1, 2.0)
    cache.store(2, 2.0, 1e-4, 60, 5)
    assert cache.used_bytes == 120


def test_store_cost_and_doc_id_follow_cache_go():
    assert store_cost_usd(1000) == 1000 / 4 / 1000 * 0.0004
    key = cache_key_text([("user", "hello")])
    assert key == "user: hello\n"
    assert doc_id(key) == hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]  # hex(sum[:16])
    assert tie_key("00ff" + "0" * 28) < tie_key("0100" + "0" * 28)


@pytest.mark.parametrize("seed,gap", [(1, 1.0), (2, 60.0), (3, 1800.0), (4, 36000.0)])
def test_fast_costaware_matches_reference_order(seed, gap):
    rng = random.Random(seed)
    fast, ref = CostAware(size=4), CostAwareReference()
    t, resident, nxt = 0.0, [], 0
    for _ in range(3000):
        t += rng.expovariate(1 / gap)
        r = rng.random()
        if len(resident) < 3 or r < 0.4:
            doc, nxt = f"e{nxt:05d}", nxt + 1
            cost, dens = 10 ** rng.uniform(-6, -3), rng.random()
            for p in (fast, ref):
                p.on_store(doc, cost, dens, nxt, t)
            resident.append(doc)
        elif r < 0.75:
            doc = rng.choice(resident)
            fast.on_hit(doc, t)
            ref.on_hit(doc, t)
        else:
            v1, v2 = fast.victim(t), ref.victim(t)
            assert v1 == v2
            assert fast.l == pytest.approx(ref.l, rel=1e-9, abs=1e-15)
            fast.on_remove(v1)
            ref.on_remove(v2)
            resident.remove(v1)


@pytest.fixture(scope="module")
def go_trace():
    if not GO_TRACE.exists():
        pytest.skip("testdata/go_parity.json missing (run gocheck parity)")
    return json.loads(GO_TRACE.read_text(encoding="utf-8"))


def replay_policy_ops(script: dict, policy) -> tuple[list, int]:
    """Replays Go's calls; returns the victims that differ from Go's choice
    without being an exact tie, and the number of exact ties. Go breaks an
    exact tie in w by map order (random), so any tied entry is a correct
    victim; the replay then follows Go's choice to stay aligned."""
    ref = CostAwareReference()
    wrong, ties = [], 0
    for i, op in enumerate(script["ops"]):
        kind, t = op["op"], op["t"]
        for p in (policy, ref):
            if kind == "store":
                p.on_store(op["id"], op.get("cost", 0.0), op.get("density", 0.0), 0, t)
            elif kind == "hit":
                p.on_hit(op["id"], t)
            elif kind == "remove":
                p.on_remove(op["id"])
        if kind != "evict":
            continue
        mine, go = policy.victim(t), op["victim"]
        ref.victim(t)
        if mine != go:
            tie = (script["policy"] == "costaware" and
                   ref.weight(ref.entries[mine], t) == ref.weight(ref.entries[go], t))
            ties += tie
            if not tie:
                wrong.append((i, mine, go))
        policy.on_remove(go)
        ref.on_remove(go)
    return wrong, ties


def test_policies_match_go_victims(go_trace):
    evictions = 0
    for script in go_trace["policies"]:
        makers = [LRU] if script["policy"] == "lru" else [CostAware, CostAwareReference]
        evictions += sum(op["op"] == "evict" for op in script["ops"])
        for make in makers:
            wrong, ties = replay_policy_ops(script, make())
            assert not wrong, (script["policy"], script["mean_gap_s"], make.__name__, wrong[:3])
            assert ties <= 5, ties   # ties only where w has decayed to the inflation term
    assert evictions > 1500


def replay_go_cache(script: dict) -> list[tuple]:
    reqs = script["requests"]
    caches, where = {}, {}
    for tenant in sorted({r["tenant"] for r in reqs}):
        idx = [i for i, r in enumerate(reqs) if r["tenant"] == tenant]
        docs = [doc_id(cache_key_text([("user", reqs[i]["text"])])) for i in idx]
        vecs = f32([reqs[i]["vector"] for i in idx])
        caches[tenant] = TenantCache(vecs, threshold=script["threshold"], capacity=script["capacity"],
                                     policy="costaware", docs=docs, tiekeys=doc_tiekeys(docs))
        where.update({i: p for p, i in enumerate(idx)})
    out = []
    for i, r in enumerate(reqs):
        cache = caches[r["tenant"]]
        if r.get("budget") is not None:
            cache.set_budget_bytes(r["budget"], 0.0)
        hit, best, score = cache.lookup(where[i], 0.0)
        if not hit:
            key = cache_key_text([("user", r["text"])])
            cache.store(where[i], 0.0, store_cost_usd(r["completion_bytes"]), r["completion_bytes"],
                        len(key.encode("utf-8")), searched=(best, score))
        out.append((hit, round(score, 9) if hit else 0.0, cache.used_bytes))
    return out


def test_cache_matches_go_request_by_request(go_trace):
    for script in go_trace["caches"]:
        mine = replay_go_cache(script)
        go = [(r["hit"], round(r["score"], 9), r["used_bytes"]) for r in script["requests"]]
        first_diff = next((i for i, (a, b) in enumerate(zip(mine, go)) if a != b), None)
        assert first_diff is None, (script["seed"], first_diff, mine[first_diff], go[first_diff])
        assert sum(h for h, _, _ in go) > 20      # the script exercises hits, not only misses


def clustered(n: int, dims: int, clusters: int, noise: float, seed: int, dtype=np.float32) -> np.ndarray:
    rng = np.random.default_rng(seed)
    centers = rng.normal(size=(clusters, dims))
    v = centers[rng.integers(clusters, size=n)] + noise * rng.normal(size=(n, dims))
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    return v.astype(dtype)


@pytest.mark.parametrize("policy", ["costaware", "lru"])
def test_neighbor_index_gives_the_exact_replay(policy):
    vecs = clustered(400, 16, 40, 0.35, seed=5)
    n = len(vecs)
    comp = np.random.default_rng(6).integers(1, 3000, size=n)
    costs = np.array([store_cost_usd(int(c)) for c in comp])
    nb = NeighborIndex(vecs, k=4)                      # small k forces exact fallbacks too
    for cap in (3, 17, 60, 0):
        kw = dict(threshold=0.85, capacity=cap, policy=policy, dt=1.2, warm_start=n // 2)
        exact = asdict(replay_stream(vecs, costs, comp, comp, **kw))
        fast = asdict(replay_stream(vecs, costs, comp, comp, neighbors=nb, **kw))
        assert fast.pop("fallbacks") >= 0 and exact.pop("fallbacks") == 0
        assert fast == exact, cap


def test_old_protocol_reproduced_by_the_engine():
    sys.path.insert(0, str(HERE.parents[0] / "analysis"))
    old = pytest.importorskip("semantic_cache_eval")
    vecs = clustered(600, 24, 60, 0.45, seed=9, dtype=np.float64)
    half = len(vecs) // 2
    ladder = [5, 30, 100, half]
    want = old.nearest_at_sizes(vecs[half:], vecs[:half], ladder)
    nb = NeighborIndex(vecs, k=8, history_end=np.full(len(vecs), half), rows=np.arange(half, len(vecs)))
    for neighbors in (None, nb):
        got = replay_insert_then_query(vecs, ladder, neighbors=neighbors)
        assert sorted(got) == sorted(want)
        for size in want:
            np.testing.assert_allclose(got[size], want[size], rtol=0, atol=1e-12)
            for th in old.THRESHOLDS:
                assert (got[size] >= th).sum() == (want[size] >= th).sum()
