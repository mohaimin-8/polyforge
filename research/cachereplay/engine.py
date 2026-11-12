"""A Python mirror of the gateway's semantic cache, for replaying real prompts
(programme 2, WP6 step 3).

Mirrored behaviour (Go sources under internal/ai/):

  gateway/cache.go     cacheKeyText, NewSemanticCache (threshold default),
                       SemanticCache.Lookup (hit iff best score >= threshold;
                       a hit calls policy.OnHit), Store (cost estimate from the
                       completion's byte length), StoreWithCost (zero byte budget
                       admits nothing; density = best score before the insert,
                       if > 0; doc ID = hex of sha256(key text)[:16]; Upsert
                       replaces by ID; OnStore; accountStoreLocked), and
                       evictWhileOverLocked / overBoundLocked (evict while
                       count > capacity, capacity 0 = unbounded, or while the
                       completion bytes exceed a set byte budget)
  vector/index.go      Index.Search (exact cosine over the tenant's docs; score
                       descending, then doc ID ascending)
  embed/embed.go       Cosine (float64 sum of float32 products)
  gateway/eviction/costaware.go   costAware: OnStore, OnHit, OnRemove, Victim
                       (argmin of w, then l := w_min), weight, default config
  gateway/eviction/policy.go      lru (the comparison policy)

One TenantCache is one tenant partition (cache.go `partition`; the gateway
isolates tenants by default).

Deliberate differences, all stated:
  * time: the gateway reads time.Now(); the replay passes a virtual clock.
  * ties: costAware.Victim scans a Go map, so an exact tie in w goes to
    whichever entry the map yields first (random); here to the smaller doc ID,
    the convention of eviction/bench.go's clusterRepresentative.
  * arithmetic: the fast CostAware evaluates w in a factored form (rounding
    differs from Go's in the last bits); CostAwareReference evaluates it in
    Go's order and the tests check both choose the same victims.
  * search: vectors are float32 as in the gateway; candidate scores come from
    float32 matrix products and every candidate within NEAR of the best is
    re-scored in float64 before the threshold test and the doc-ID tie-break.
"""

from __future__ import annotations

import hashlib
import math
from collections import OrderedDict

import numpy as np

DEFAULT_THRESHOLD = 0.95            # cache.go DefaultCacheThreshold
DEFAULT_CAPACITY = 10_000           # cache.go DefaultCacheCapacityPerTenant
STALENESS_HALF_LIFE_S = 6 * 3600.0  # costaware.go withDefaults
HIT_RATE_HALF_LIFE_S = 3600.0
DENSITY_EPSILON = 0.5
NEAR = 1e-5          # bound on float32 product error; candidates this close are re-scored
REBASE_HALF_LIVES = 400.0  # CostAware keeps 2**(t/h2) finite by rebasing its clock


def cache_key_text(messages) -> str:
    """cache.go cacheKeyText: "role: content\\n" for every message."""
    return "".join(f"{role}: {content}\n" for role, content in messages)


def doc_id(key_text: str) -> str:
    """cache.go StoreWithCost: hex of the first 16 bytes of sha256(key text)."""
    return hashlib.sha256(key_text.encode("utf-8")).hexdigest()[:32]


def tie_key(doc: str) -> int:
    """Orders like the hex doc ID (its first 64 bits); used to break ties only."""
    return int(doc[:16], 16)


def store_cost_usd(completion_bytes: int) -> float:
    """cache.go Store: len(completion) bytes / 4 chars per token / 1000 x $0.0004."""
    return float(completion_bytes) / 4 / 1000 * 0.0004


def effective_threshold(threshold: float) -> float:
    """NewSemanticCache: a threshold outside (0, 1] falls back to the default."""
    return DEFAULT_THRESHOLD if threshold <= 0 or threshold > 1 else float(threshold)


def go_normalize(matrix: np.ndarray) -> np.ndarray:
    """embed.go Normalize per row: float64 norm, float32 scale; zero rows unchanged."""
    m = np.asarray(matrix, dtype=np.float32)
    sums = np.einsum("ij,ij->i", m.astype(np.float64), m.astype(np.float64))
    scale = np.ones(len(m), dtype=np.float32)
    nz = sums > 0
    scale[nz] = (1.0 / np.sqrt(sums[nz])).astype(np.float32)
    return m * scale[:, None]


# ---- eviction policies (Go: eviction.Policy) ---------------------------------
class LRU:
    """eviction/policy.go lru: front = most recent; Victim = back."""

    name = "lru"
    needs_density = False

    def __init__(self) -> None:
        self._order: OrderedDict = OrderedDict()  # least recent first

    def on_store(self, doc, cost, density, tiekey, now) -> None:
        if doc in self._order:
            self._order.move_to_end(doc)
        else:
            self._order[doc] = None

    def on_hit(self, doc, now) -> None:
        if doc in self._order:
            self._order.move_to_end(doc)

    def on_remove(self, doc) -> None:
        self._order.pop(doc, None)

    def victim(self, now):
        return next(iter(self._order)) if self._order else None


class FIFO(LRU):
    """Not a gateway policy: the old protocol's cache (the last K inserted)."""

    name = "fifo"

    def on_store(self, doc, cost, density, tiekey, now) -> None:
        if doc not in self._order:
            self._order[doc] = None

    def on_hit(self, doc, now) -> None:
        return None


class CostAwareReference:
    """eviction/costaware.go, scalar and in Go's evaluation order (tests only)."""

    name = "costaware"
    needs_density = True

    def __init__(self, h1: float = HIT_RATE_HALF_LIFE_S, h2: float = STALENESS_HALF_LIFE_S,
                 eps: float = DENSITY_EPSILON) -> None:
        self.h1, self.h2, self.eps, self.l = h1, h2, eps, 0.0
        self.entries: dict = {}

    def on_store(self, doc, cost, density, tiekey, now) -> None:
        self.entries[doc] = {"cost": cost, "density": density, "stored": now, "last": now,
                             "hits": 0.0, "inflation": self.l, "tie": tiekey}

    def on_hit(self, doc, now) -> None:
        e = self.entries.get(doc)
        if e is not None:
            e["hits"] += 1
            e["last"] = now
            e["inflation"] = self.l

    def on_remove(self, doc) -> None:
        self.entries.pop(doc, None)

    def weight(self, e: dict, now: float) -> float:
        periods = 1 + (now - e["stored"]) / self.h1
        hit_rate = (e["hits"] + 1) / periods
        staleness = math.pow(2.0, -(now - e["last"]) / self.h2)
        isolation = 1 / (self.eps + e["density"])
        return e["inflation"] + e["cost"] * hit_rate * staleness * isolation

    def victim(self, now):
        best = None
        for doc, e in self.entries.items():
            key = (self.weight(e, now), e["tie"])
            if best is None or key < best[0]:
                best = (key, doc)
        if best is None:
            return None
        self.l = best[0][0]
        return best[1]


class CostAware:
    """eviction/costaware.go with its default config, vectorised.

    w = inflation + cost x hitRate x staleness x isolation, evaluated for all
    entries at once in the factored form
        inflation + 2**(-(now-t0)/h2) x num / (h1 + now - stored),
        num = h1 x cost x (hits+1) / (eps+density) x 2**((last-t0)/h2),
    which equals Go's weight up to rounding (CostAwareReference is Go's order).
    """

    name = "costaware"
    needs_density = True
    _FIELDS = ("infl", "cost", "dens", "stored", "last", "hits", "num")

    def __init__(self, h1: float = HIT_RATE_HALF_LIFE_S, h2: float = STALENESS_HALF_LIFE_S,
                 eps: float = DENSITY_EPSILON, size: int = 1024) -> None:
        self.h1, self.h2, self.eps, self.l = h1, h2, eps, 0.0
        self.t0, self.n = 0.0, 0
        self._pos: dict = {}
        self._docs: list = []
        self._arr = {f: np.zeros(size) for f in self._FIELDS}
        self._tie = np.zeros(size, dtype=np.uint64)

    def _grow(self) -> None:
        size = 2 * len(self._tie)
        for f in self._FIELDS:
            self._arr[f] = np.concatenate([self._arr[f], np.zeros(size - len(self._arr[f]))])
        self._tie = np.concatenate([self._tie, np.zeros(size - len(self._tie), dtype=np.uint64)])

    def _num(self, p: int) -> float:
        a = self._arr
        return (self.h1 * a["cost"][p] * (a["hits"][p] + 1) / (self.eps + a["dens"][p])
                * math.pow(2.0, (a["last"][p] - self.t0) / self.h2))

    def _rebase(self, now: float) -> None:
        if (now - self.t0) / self.h2 < REBASE_HALF_LIVES:
            return
        self.t0 = now
        for p in range(self.n):
            self._arr["num"][p] = self._num(p)

    def on_store(self, doc, cost, density, tiekey, now) -> None:
        self._rebase(now)
        p = self._pos.get(doc)
        if p is None:
            if self.n == len(self._tie):
                self._grow()
            p, self.n = self.n, self.n + 1
            self._pos[doc] = p
            self._docs.append(doc)
        a = self._arr
        a["infl"][p], a["cost"][p], a["dens"][p] = self.l, cost, density
        a["stored"][p], a["last"][p], a["hits"][p] = now, now, 0.0
        self._tie[p] = tiekey
        a["num"][p] = self._num(p)

    def on_hit(self, doc, now) -> None:
        p = self._pos.get(doc)
        if p is None:
            return
        self._rebase(now)
        a = self._arr
        a["hits"][p] += 1
        a["last"][p] = now
        a["infl"][p] = self.l
        a["num"][p] = self._num(p)

    def on_remove(self, doc) -> None:
        p = self._pos.pop(doc, None)
        if p is None:
            return
        last = self.n - 1
        if p != last:
            moved = self._docs[last]
            for f in self._FIELDS:
                self._arr[f][p] = self._arr[f][last]
            self._tie[p] = self._tie[last]
            self._docs[p] = moved
            self._pos[moved] = p
        self._docs.pop()
        self.n = last

    def weights(self, now: float) -> np.ndarray:
        n, a = self.n, self._arr
        scale = math.pow(2.0, -(now - self.t0) / self.h2)
        return a["infl"][:n] + scale * a["num"][:n] / ((self.h1 + now) - a["stored"][:n])

    def victim(self, now):
        if self.n == 0:
            return None
        self._rebase(now)
        w = self.weights(now)
        p = int(np.argmin(w))
        best = w[p]
        if np.count_nonzero(w == best) > 1:
            ties = np.flatnonzero(w == best)
            p = int(ties[np.argmin(self._tie[ties])])
        self.l = float(best)
        return self._docs[p]


POLICIES = {"costaware": CostAware, "lru": LRU, "fifo": FIFO, "costaware_ref": CostAwareReference}


def make_policy(name: str):
    if name not in POLICIES:
        raise ValueError(f"unknown policy {name!r}; have {sorted(POLICIES)}")
    return POLICIES[name]()
