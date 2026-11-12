"""One tenant partition of the gateway's semantic cache over a prompt stream
whose vectors are known in advance, and the two replay protocols (WP6 step 3).

The mirrored Go behaviour is listed in engine.py. Here a stored prompt is
identified by its stream position; `docs` (the gateway doc IDs) decide when
two positions are the same document (Upsert replaces by ID). With docs=None
every position is its own document, which is the old protocol's array
semantics (research/analysis/semantic_cache_eval.py).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from engine import NEAR, effective_threshold, make_policy


class NeighborIndex:
    """For each query row, its k most similar earlier rows (exact products in
    the vectors' dtype, sorted by score). Earlier means a row index below
    history_end[row]; by default the row itself, so strictly earlier prompts.
    A lookup scans these k candidates first and falls back to an exact scan of
    the resident set when none of them is resident, so results stay exact."""

    def __init__(self, vectors: np.ndarray, k: int = 64, history_end=None, rows=None,
                 max_block_cells: int = 1 << 25) -> None:
        n = len(vectors)
        self.k = k
        self.history_end = (np.arange(n, dtype=np.int64) if history_end is None
                            else np.asarray(history_end, dtype=np.int64))
        self.ids = np.full((n, k), -1, dtype=np.int32)
        self.scores = np.full((n, k), -np.inf, dtype=vectors.dtype)
        self.length = np.zeros(n, dtype=np.int64)
        rows = np.arange(n) if rows is None else np.asarray(rows, dtype=np.int64)
        if len(rows):
            self._build(vectors, rows, max_block_cells)

    def _build(self, vectors: np.ndarray, rows: np.ndarray, max_cells: int) -> None:
        hmax = int(self.history_end[rows].max())
        block = max(16, min(1024, max_cells // max(1, hmax)))
        for start in range(0, len(rows), block):
            blk = rows[start:start + block]
            hist = self.history_end[blk]
            h = int(hist.max())
            if h <= 0:
                continue
            sims = vectors[blk] @ vectors[:h].T
            if (hist < h).any():
                sims[np.arange(h)[None, :] >= hist[:, None]] = -np.inf
            kk = min(self.k, h)
            part = (np.argpartition(sims, h - kk, axis=1)[:, h - kk:] if h > kk
                    else np.tile(np.arange(h), (len(blk), 1)))
            top = np.take_along_axis(sims, part, axis=1)
            order = np.argsort(-top, axis=1, kind="stable")
            ids = np.take_along_axis(part, order, axis=1)
            scores = np.take_along_axis(top, order, axis=1)
            finite = np.isfinite(scores)
            self.ids[blk, :kk] = np.where(finite, ids, -1)
            self.scores[blk, :kk] = scores
            self.length[blk] = finite.sum(axis=1)

    def candidates(self, row: int) -> tuple[np.ndarray, np.ndarray]:
        m = self.length[row]
        return self.ids[row, :m], self.scores[row, :m]


class TenantCache:
    """SemanticCache for one tenant partition (cache.go), over `vectors`."""

    def __init__(self, vectors: np.ndarray, *, threshold: float, capacity: int | None,
                 policy, docs=None, tiekeys=None, neighbors: NeighborIndex | None = None,
                 budget_bytes: int | None = None) -> None:
        self.vectors = vectors
        self._v64 = np.asarray(vectors, dtype=np.float64)  # float64 copy for exact scores
        self.docs = docs
        self.tiekeys = (np.arange(len(vectors), dtype=np.uint64) if tiekeys is None
                        else np.asarray(tiekeys, dtype=np.uint64))
        self.threshold = effective_threshold(threshold)
        # cache.go overBoundLocked: `capacityPerTenant > 0 && count > capacity`.
        self.capacity = int(capacity) if capacity is not None and capacity > 0 else None
        self.budget = budget_bytes
        self.policy = make_policy(policy) if isinstance(policy, str) else policy
        self.neighbors = neighbors
        self.resident = np.zeros(len(vectors), dtype=bool)
        self.pos_of_doc: dict = {}
        self._row_of_pos: dict = {}
        self._matrix = np.empty((64, vectors.shape[1]), dtype=vectors.dtype)
        self._pos_at_row = np.empty(64, dtype=np.int64)
        self.n = 0
        self.entry_bytes: dict = {}
        self.used_bytes = 0   # cache.go usedBytes: completion bytes of resident entries
        self.key_bytes = 0    # key-text bytes of resident entries (not accounted by Go)
        self.hits = self.misses = self.inserts = self.evictions = self.fallbacks = 0

    # ---- vector/index.go -------------------------------------------------
    def _doc(self, pos: int):
        return pos if self.docs is None else self.docs[pos]

    def _add_row(self, pos: int) -> None:
        if self.n == len(self._matrix):
            self._matrix = np.concatenate([self._matrix, np.empty_like(self._matrix)])
            self._pos_at_row = np.concatenate([self._pos_at_row, np.empty_like(self._pos_at_row)])
        self._matrix[self.n] = self.vectors[pos]
        self._pos_at_row[self.n] = pos
        self._row_of_pos[pos] = self.n
        self.n += 1
        self.resident[pos] = True

    def _drop_row(self, pos: int) -> None:
        row, last = self._row_of_pos.pop(pos), self.n - 1
        if row != last:
            moved = int(self._pos_at_row[last])
            self._matrix[row] = self._matrix[last]
            self._pos_at_row[row] = moved
            self._row_of_pos[moved] = row
        self.n = last
        self.resident[pos] = False

    def _refine(self, pos: int, cand: np.ndarray) -> tuple[int, float]:
        """Exact float64 scores (embed.go Cosine) of near-best candidates; the
        best wins, an exact tie goes to the smaller doc ID (index.go Search)."""
        if len(cand) == 1:
            return int(cand[0]), float(self._v64[cand[0]] @ self._v64[pos])
        exact = self._v64[cand] @ self._v64[pos]
        best = exact.max()
        tied = cand[exact == best]
        winner = tied[0] if len(tied) == 1 else tied[np.argmin(self.tiekeys[tied])]
        return int(winner), float(best)

    def search(self, pos: int) -> tuple[int, float]:
        """index.go Search(part, q, 1): the best resident position and its
        score, or (-1, -inf) for an empty partition."""
        if self.n == 0:
            return -1, -math.inf
        if self.neighbors is not None:
            ids, scores = self.neighbors.candidates(pos)
            mask = self.resident[ids]
            j = int(mask.argmax()) if len(ids) else 0
            if len(ids) and mask[j]:
                floor = scores[j] - NEAR       # scores are sorted, so near-best ones follow j
                if j + 1 == len(ids) or scores[j + 1] < floor:
                    return self._refine(pos, ids[j:j + 1])
                near = np.flatnonzero(mask[j:] & (scores[j:] >= floor)) + j
                return self._refine(pos, ids[near])
            self.fallbacks += 1
        sims = self._matrix[:self.n] @ self.vectors[pos]
        rows = np.flatnonzero(sims >= sims.max() - NEAR)
        return self._refine(pos, self._pos_at_row[rows])

    # ---- cache.go ---------------------------------------------------------
    def lookup(self, pos: int, now: float) -> tuple[bool, int, float]:
        best, score = self.search(pos)
        if best < 0 or score < self.threshold:
            self.misses += 1
            return False, best, score
        self.hits += 1
        self.policy.on_hit(self._doc(best), now)
        return True, best, score

    def store(self, pos: int, now: float, cost: float, completion_bytes: int, key_bytes: int,
              searched: tuple[int, float] | None = None) -> bool:
        if self.budget is not None and self.budget == 0:
            return False  # a zero budget admits nothing
        density = 0.0
        if self.policy.needs_density:
            best, score = searched if searched is not None else self.search(pos)
            density = score if best >= 0 and score > 0 else 0.0
        doc = self._doc(pos)
        old = self.pos_of_doc.get(doc)
        if old is not None:
            self._drop_row(old)  # Upsert replaces the document with this ID
        self.pos_of_doc[doc] = pos
        self._add_row(pos)
        self.policy.on_store(doc, cost, density, int(self.tiekeys[pos]), now)
        self._account(doc, completion_bytes, key_bytes)
        self.inserts += 1
        self._evict_while_over(now)
        return True

    def _account(self, doc, completion_bytes: int, key_bytes: int) -> None:
        old = self.entry_bytes.get(doc)
        if old is not None:
            self.used_bytes -= old[0]
            self.key_bytes -= old[1]
        self.entry_bytes[doc] = (completion_bytes, key_bytes)
        self.used_bytes += completion_bytes
        self.key_bytes += key_bytes

    def _over_bound(self) -> bool:
        if self.capacity is not None and len(self.pos_of_doc) > self.capacity:
            return True
        return self.budget is not None and self.used_bytes > self.budget

    def _evict_while_over(self, now: float) -> None:
        while self._over_bound():
            victim = self.policy.victim(now)
            if victim is None:
                break
            self._drop_row(self.pos_of_doc.pop(victim))
            self.policy.on_remove(victim)
            sizes = self.entry_bytes.pop(victim, (0, 0))
            self.used_bytes -= sizes[0]
            self.key_bytes -= sizes[1]
            self.evictions += 1

    def set_budget_bytes(self, budget: int, now: float) -> None:
        """cache.go SetTenantBudgetBytes: negative removes the bound."""
        if budget < 0:
            self.budget = None
            return
        self.budget = int(budget)
        self._evict_while_over(now)


@dataclass(frozen=True)
class StreamResult:
    lookups: int
    hits: int
    warm_lookups: int
    warm_hits: int
    inserts: int
    evictions: int
    fallbacks: int
    resident_end: int
    resident_at_warm_start: int
    warm_mean_resident: float
    warm_mean_completion_bytes: float   # per resident entry, warm half
    warm_mean_key_bytes: float          # per resident entry, warm half
    warm_completion_bytes: int          # completion bytes of all warm-half requests
    warm_hit_completion_bytes: int      # ... of those served from the cache (Store's cost basis)


def replay_stream(vectors: np.ndarray, costs, completion_bytes, key_bytes, *, threshold: float,
                  capacity: int | None, policy: str, dt: float, warm_start: int, docs=None,
                  tiekeys=None, neighbors: NeighborIndex | None = None) -> StreamResult:
    """The gateway's request path, request by request: Lookup; on a miss the
    completion is stored (server.go chat: Store after the provider call)."""
    cache = TenantCache(vectors, threshold=threshold, capacity=capacity, policy=policy,
                        docs=docs, tiekeys=tiekeys, neighbors=neighbors)
    warm_hits, at_warm, res_sum, cb_sum, kb_sum, wbytes, whit_bytes = 0, 0, 0, 0, 0, 0, 0
    for j in range(len(vectors)):
        now = j * dt
        if j == warm_start:
            at_warm = len(cache.pos_of_doc)
        hit, best, score = cache.lookup(j, now)
        if j >= warm_start:
            warm_hits += hit
            res_sum += len(cache.pos_of_doc)
            cb_sum += cache.used_bytes
            kb_sum += cache.key_bytes
            wbytes += int(completion_bytes[j])
            whit_bytes += int(completion_bytes[j]) if hit else 0
        if not hit:
            cache.store(j, now, float(costs[j]), int(completion_bytes[j]), int(key_bytes[j]),
                        searched=(best, score))
    warm = len(vectors) - warm_start
    return StreamResult(
        lookups=len(vectors), hits=cache.hits, warm_lookups=warm, warm_hits=warm_hits,
        inserts=cache.inserts, evictions=cache.evictions, fallbacks=cache.fallbacks,
        resident_end=len(cache.pos_of_doc), resident_at_warm_start=at_warm,
        warm_mean_resident=res_sum / warm if warm else 0.0,
        warm_mean_completion_bytes=cb_sum / res_sum if res_sum else 0.0,
        warm_mean_key_bytes=kb_sum / res_sum if res_sum else 0.0,
        warm_completion_bytes=wbytes, warm_hit_completion_bytes=whit_bytes)


def replay_insert_then_query(vectors: np.ndarray, capacities, *,
                             neighbors: NeighborIndex | None = None) -> dict[int, np.ndarray]:
    """The old protocol (semantic_cache_eval.py): insert the first half, then
    query the second half read-only; a cache of size K holds the last K
    inserted (FIFO). Returns, per K (capped at the half, as the old code
    does), the best resident score of every query (-inf if none)."""
    n = len(vectors)
    half = n // 2
    out: dict[int, np.ndarray] = {}
    for k in sorted({min(int(c), half) for c in capacities}):
        cache = TenantCache(vectors, threshold=1.0, capacity=k, policy="fifo",
                            neighbors=neighbors)
        for j in range(half):
            cache.store(j, 0.0, 0.0, 0, 0)
        out[k] = np.array([cache.search(j)[1] for j in range(half, n)])
    return out
