"""Predicted semantic-cache hits of a WP11 cell (programme 2).

The cell's exact prompt sequence goes through the gateway's cache logic, the
Go-verified Python mirror in research/cachereplay (engine.py, cachesim.py):

  * one partition per tenant (cache.go isolates tenants by default);
  * a request looks up at its arrival time; a miss is stored when its answer
    returns, at arrival + `delay_s(tenant, step)` (the prediction passes its
    tier's predicted mean miss latency in that step), so a near-duplicate
    that arrives while the first is still being answered misses, as in the
    gateway, whose chat handler stores after the provider call;
  * threshold 0.95, cache.go's DefaultCacheThreshold, which the testbed's
    gateway does not override; the cost-aware policy; at most 10,000
    entries per tenant (DefaultCacheCapacityPerTenant) and a byte budget of
    knob MB x 2^20 completion bytes, each answer taken as COMPLETION_BYTES
    long (48 tokens at about 4 bytes). The result counts evictions: with
    none, neither bound nor the length assumption moved a hit.

Vectors: WP6's cache of MiniLM-L6-v2 vectors of the same first turns in the
gateway's key format ("user: <prompt>\\n"), the model revision the testbed's
embedder serves (testbed/k8s/base/models.lock), so scores agree to float
rounding; a score within rounding of 0.95 could fall either way.
"""

from __future__ import annotations

import heapq
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CACHEREPLAY = REPO / "research" / "cachereplay"
if str(CACHEREPLAY) not in sys.path:
    sys.path.insert(0, str(CACHEREPLAY))

from cachesim import NeighborIndex, TenantCache  # noqa: E402
from engine import DEFAULT_CAPACITY, DEFAULT_THRESHOLD, store_cost_usd, tie_key  # noqa: E402

BIN_S = 10
COMPLETION_BYTES = 192
NEIGHBOURS = 64
STORE, LOOKUP = 0, 1   # a store due at the same instant as a lookup lands first


@dataclass(frozen=True)
class Replay:
    hits: list[bool]          # per request of the table, in table order
    evictions: int
    max_used_bytes: int       # largest completion-byte total any partition reached
    max_entries: int


def replay_cell(table: Sequence[Mapping], vectors: np.ndarray, docs: Sequence[str], key_bytes: np.ndarray,
                knob_mb: Mapping[str, int], delay_s: Callable[[str, int], float]) -> Replay:
    """Hit or miss of every request of a cell's table (seq, t, tenant, vector_row)."""
    hits = [False] * len(table)
    by_tenant: dict[str, list[int]] = {}
    for i, r in enumerate(table):
        by_tenant.setdefault(r["tenant"], []).append(i)
    evictions = max_bytes = max_entries = 0
    for tenant, idx in by_tenant.items():
        idx = sorted(idx, key=lambda i: (table[i]["t"], table[i]["seq"]))
        budget = int(knob_mb[tenant]) * 2 ** 20
        if budget == 0:
            continue   # a zero budget admits nothing: every lookup misses
        rows = np.array([table[i]["vector_row"] for i in idx], dtype=np.int64)
        vec = np.ascontiguousarray(vectors[rows], dtype=np.float32)
        tdocs = [docs[r] for r in rows]
        cache = TenantCache(vec, threshold=DEFAULT_THRESHOLD, capacity=DEFAULT_CAPACITY, policy="costaware",
                            docs=tdocs, tiekeys=np.array([tie_key(d) for d in tdocs], dtype=np.uint64),
                            neighbors=NeighborIndex(vec, k=NEIGHBOURS), budget_bytes=budget)
        events = [(float(table[i]["t"]), LOOKUP, j) for j, i in enumerate(idx)]
        heapq.heapify(events)
        while events:
            now, kind, j = heapq.heappop(events)
            if kind == LOOKUP:
                hit, _best, _score = cache.lookup(j, now)
                hits[idx[j]] = hit
                if not hit:
                    step = int(table[idx[j]]["t"] // BIN_S)
                    heapq.heappush(events, (now + float(delay_s(tenant, step)), STORE, j))
            else:
                cache.store(j, now, store_cost_usd(COMPLETION_BYTES), COMPLETION_BYTES, int(key_bytes[rows[j]]))
                max_bytes = max(max_bytes, cache.used_bytes)
                max_entries = max(max_entries, len(cache.pos_of_doc))
        evictions += cache.evictions
    return Replay(hits=hits, evictions=evictions, max_used_bytes=max_bytes, max_entries=max_entries)


def hits_per_step(table: Sequence[Mapping], hits: Sequence[bool], steps: int) -> dict[tuple[int, str], int]:
    out: dict[tuple[int, str], int] = {}
    for r, h in zip(table, hits):
        key = (min(int(r["t"] // BIN_S), steps - 1), r["tenant"])
        out[key] = out.get(key, 0) + int(h)
    return out
