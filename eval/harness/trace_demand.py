"""L5: drive the LIVE plane from a real trace, not a synthetic workload class.

The sharpest un-conceded objection to this project's evaluation is precise:
traces are replayed in *simulation* (bit-for-bit, `RESULTS_TRACE_PARITY.md`),
and every `backend: cluster` experiment drives SYNTHETIC cells -- `crud_bursty`,
`ai_cacheable`, `tier_mixed`, `joint_stress`. **No real trace has ever driven a
real cluster.** A reviewer who joins "sim-only traces" to "synthetic-only live"
has a real point, and stating the gap is only worth doing once it is closed.

This module is the demand half of closing it.

**It reuses the sim's own projection rather than reimplementing it.**
`research/analysis/trace_matrix.py` is import-safe (constants and defs only,
`main()` guarded), so `window_buckets` is called directly. That makes the claim
"the live plane saw the demand the simulator replayed" true BY CONSTRUCTION
rather than by a test that could drift: there is one projection, not two that
agree today. `trace_matrix.py` is untouched, so `RESULTS_TRACE_PARITY.md` and
`RESULTS_BUDGET_PARITY.md` keep replaying byte-identically.

What the projection is, unchanged from the sim path:

  * real BurstGPT arrivals, grouped per tenant into `RATE_BUCKET_S` (600 s)
    coarse buckets -- the finest resolution the arrival density supports
  * scaled by the committed `scale_factor`, held piecewise-constant across the
    `600 / CONTROL_INTERVAL_S` control intervals inside each coarse bucket
  * eight tenants, `WINDOW_H` = 6 hour windows, window starts from the same
    `window_starts` segmentation

**The demand is chat-only.** That is a property of the trace, not an omission:
BurstGPT records LLM arrivals, so the projection emits `chat` and nothing else.
A live cell built on this therefore exercises the AI path and NOT the CRUD path,
which is the opposite bias to every synthetic cell used so far, and has to be
disclosed as such rather than quietly mixed with synthetic CRUD to look
balanced.

This module does not run anything and does not pick a window. Scoring a
comparison from it needs a NEW pre-registration -- it is a new scored
comparison, and folding it into `PREREG_WAVE4_LIVE_PLANE` would be widening a
frozen prereg after seeing results.
"""

from __future__ import annotations

import dataclasses
import gzip
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "research" / "analysis"))
sys.path.insert(0, str(REPO_ROOT / "research" / "jcac_sim"))

import trace_matrix as tm  # noqa: E402
from model import CONTROL_INTERVAL_S  # noqa: E402

# Buckets per window, fixed by the sim's own constants. Asserted rather than
# assumed: if either constant moves, a live sitting driven by this would no
# longer line up with the replayed windows and the whole point is lost.
BUCKETS_PER_WINDOW = tm.WINDOW_H * 3600 // CONTROL_INTERVAL_S

# A committed projection of the windows a record depends on.
#
# The raw BurstGPT trace is 56 MB of downloaded public dataset, deliberately
# not vendored (`.gitignore`: raw datasets are fetched, never committed). That
# was fine until L5 put `analysis_trace_live.py` in the reproduction gate: on a
# clean clone the trace is absent, the script raised FileNotFoundError, and
# `RESULTS_TRACE_LIVE.md` came out NOT PRODUCED. The gate has been red in CI
# since 2026-08-31 for exactly this, while passing on the author's machine
# where the file happens to exist — the worst shape a reproduction claim can
# take.
#
# The projection of one window is 5 KB gzipped, so it is committed. Same
# pattern as `research/analysis/separation_mt_v3_walk.json`: a derived artifact
# small enough to carry, with a staleness check that re-derives it wherever the
# raw input IS present (`test_trace_cache.py`). A clean clone reproduces the
# record; a machine with the trace proves the cache still matches it.
CACHE_DIR = Path(__file__).resolve().parent / "trace_cache"


def _cache_path(index: int) -> Path:
    return CACHE_DIR / f"window{index}.json.gz"


def _demand_type():
    """`workloads.Demand`, imported lazily so the module keeps its light
    import surface (`trace_matrix` alone pulls in pandas)."""
    from harness import workloads

    return workloads.Demand


def serialise_window(tenant_ids, buckets, meta) -> bytes:
    """The canonical bytes for a cached window. Sorted keys and no spaces so
    two builds of the same window compare byte for byte."""
    payload = {
        "tenant_ids": list(tenant_ids),
        "meta": meta,
        "buckets": [{t: dataclasses.asdict(d) for t, d in bucket.items()}
                    for bucket in buckets],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def load_cached_window(index: int):
    """Read a committed window. Raises if it was never built."""
    path = _cache_path(index)
    if not path.exists():
        raise FileNotFoundError(
            f"window {index} has no committed projection ({path.name}) and the raw "
            f"trace is absent ({tm.TRACE}). Fetch the trace with "
            f"`python research/traces/fetch_burstgpt.py`, or build the cache with "
            f"`python -m harness.trace_demand --build-cache {index}`.")
    payload = json.loads(gzip.decompress(path.read_bytes()).decode())
    demand = _demand_type()
    buckets = [{t: demand(**fields) for t, fields in bucket.items()}
               for bucket in payload["buckets"]]
    return payload["tenant_ids"], buckets, payload["meta"]


def _portable_trace_path() -> str:
    """The trace's path as the record should quote it: relative to the repo and
    POSIX-separated, so it means the same thing on every machine."""
    try:
        return tm.TRACE.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:  # trace configured outside the repo
        return tm.TRACE.name


def window_count() -> int:
    """How many replay windows the committed trace affords.

    Falls back to the number recorded in a cached window's metadata, so a
    clean clone without the raw trace can still answer it."""
    if not tm.TRACE.exists():
        return int(load_cached_window(0)[2]["window_count"])
    df = tm.load_events()
    return len(tm.window_starts(tm.segments_of(df)))


def trace_window(index: int, *, events=None):
    """One replay window as live-plane demand.

    Returns `(tenant_ids, buckets, meta)` where `buckets` is exactly what
    `workloads.build` returns in that position -- a list of
    `{tenant_id: Demand}` -- so a cluster-backend caller can substitute this
    for a synthetic workload class without touching the k6 path.

    `meta` carries the provenance a record has to quote: which window, which
    trace second it starts at, and the scale factor applied.
    """
    if events is None and not tm.TRACE.exists():
        # No raw trace: fall back to the committed projection rather than
        # failing. This is what makes the record rebuildable on a clean clone.
        # The range check comes first so an out-of-range index still raises
        # IndexError here, exactly as it does with the trace present.
        count = window_count()
        if not 0 <= index < count:
            raise IndexError(
                f"window {index} out of range; the trace affords {count}")
        return load_cached_window(index)
    df = tm.load_events() if events is None else events
    segs = tm.segments_of(df)
    starts = tm.window_starts(segs)
    if not 0 <= index < len(starts):
        raise IndexError(
            f"window {index} out of range; the trace affords {len(starts)}")
    k = tm.scale_factor(df, segs)
    start_s = starts[index]
    buckets = tm.window_buckets(df, start_s, k)
    tenant_ids = [f"t{i:02d}" for i in range(tm.TENANTS)]
    meta = {
        # Repo-relative and POSIX, never absolute. The cache is committed and
        # read on other machines, and `analysis_trace_live.py` renders this
        # with `Path(...).name` — which on Linux does not split a Windows path
        # at all, so an absolute `C:\...` from the author's box came out as the
        # whole string and drifted the record in CI.
        "trace": _portable_trace_path(),
        "window_index": index,
        "window_start_s": int(start_s),
        "window_hours": tm.WINDOW_H,
        "scale_factor": float(k),
        "rate_bucket_s": tm.RATE_BUCKET_S,
        "control_interval_s": CONTROL_INTERVAL_S,
        "tenants": tm.TENANTS,
        "buckets": len(buckets),
        # Carried so window_count() has an answer on a clean clone.
        "window_count": len(starts),
        "kinds": sorted({kind for b in buckets for d in b.values() for kind in d.rps}),
    }
    return tenant_ids, buckets, meta


def peak_total_rps(buckets) -> float:
    """Highest aggregate arrival rate in the window.

    The number that decides whether a substrate can serve this window at all.
    `tunnel_preflight.py`'s concurrent stage should be driven at this rate, not
    at a synthetic cell's, or the preflight would certify the wrong load.
    """
    return max(sum(d.total_rps() for d in bucket.values()) for bucket in buckets)


def _build_cache(index: int) -> Path:
    """Regenerate a committed window from the raw trace."""
    if not tm.TRACE.exists():
        raise SystemExit(f"the raw trace is required to build the cache: {tm.TRACE}")
    tenant_ids, buckets, meta = trace_window(index)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(index)
    path.write_bytes(gzip.compress(serialise_window(tenant_ids, buckets, meta), 9))
    return path


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--build-cache":
        written = _build_cache(int(sys.argv[2]))
        print(f"wrote {written} ({written.stat().st_size} bytes)")
    else:
        raise SystemExit("usage: python -m harness.trace_demand --build-cache <window>")
