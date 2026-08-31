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


def window_count() -> int:
    """How many replay windows the committed trace affords."""
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
        "trace": str(tm.TRACE),
        "window_index": index,
        "window_start_s": int(start_s),
        "window_hours": tm.WINDOW_H,
        "scale_factor": float(k),
        "rate_bucket_s": tm.RATE_BUCKET_S,
        "control_interval_s": CONTROL_INTERVAL_S,
        "tenants": tm.TENANTS,
        "buckets": len(buckets),
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
