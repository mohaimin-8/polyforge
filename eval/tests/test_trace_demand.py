"""L5's demand projection: the live plane driven by the trace the sim replayed.

These pin the properties a future refactor could silently break. The
*equivalence* to the sim's projection is not tested, because it is true by
construction -- `trace_demand` calls `trace_matrix.window_buckets` rather than
reimplementing it, so there is one projection and not two that agree today.
What is tested is that the call still returns what the live harness needs, and
that the trace's own character (chat-only, low arrival rate) is visible rather
than assumed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness import trace_demand as td  # noqa: E402


@pytest.fixture(scope="module")
def window():
    """One window, loaded once: the trace is a 384 MB tree and reading it per
    test would make this file the slowest in the suite for no benefit."""
    return td.trace_window(0)


def test_window_count_is_positive():
    assert td.window_count() > 0


def test_bucket_count_matches_the_sim_constants(window):
    """WINDOW_H x 3600 / CONTROL_INTERVAL_S. If either constant moves, a live
    sitting driven by this no longer lines up with the replayed windows, which
    is the entire point of reusing the projection."""
    _, buckets, meta = window
    assert len(buckets) == td.BUCKETS_PER_WINDOW
    assert meta["buckets"] == td.BUCKETS_PER_WINDOW


def test_demand_is_chat_only(window):
    """A property of BurstGPT, not an omission: it records LLM arrivals. A live
    cell built on this exercises the AI path and NOT the CRUD path, which is
    the opposite bias to every synthetic cell so far and must be disclosed
    rather than papered over by mixing in synthetic CRUD."""
    _, buckets, meta = window
    assert meta["kinds"] == ["chat"]
    for bucket in buckets:
        for demand in bucket.values():
            assert set(demand.rps) <= {"chat"}


def test_every_bucket_covers_every_tenant(window):
    """k6 drives one arrival stream per tenant; a missing tenant in some
    bucket would silently drop that tenant's load mid-window."""
    tenant_ids, buckets, _ = window
    assert len(tenant_ids) == 8
    for bucket in buckets:
        assert set(bucket) == set(tenant_ids)


def test_projection_is_deterministic(window):
    """Same window index, same rates. The live and sim sides must be able to
    quote the same demand without coordinating a seed."""
    tenant_ids, buckets, meta = window
    again_ids, again_buckets, again_meta = td.trace_window(0)
    assert again_ids == tenant_ids
    assert again_meta == meta
    assert [{t: d.total_rps() for t, d in b.items()} for b in again_buckets] \
        == [{t: d.total_rps() for t, d in b.items()} for b in buckets]


def test_peak_total_rps_is_the_max_aggregate(window):
    _, buckets, _ = window
    totals = [sum(d.total_rps() for d in b.values()) for b in buckets]
    assert td.peak_total_rps(buckets) == pytest.approx(max(totals))


def test_the_windows_arrival_rate_is_modest(window):
    """Guards a real planning conclusion, so it fails loudly if the projection
    ever changes scale.

    scale_factor targets WORK UNITS -- mean per-tenant demand equals one
    replica's capacity -- not requests/second, and chat is expensive per
    request. The result is a peak around 1.4 rps aggregate, ~80x below the
    synthetic joint_stress cell's ~110 AI rps. That is why a trace-driven live
    sitting does NOT need the GPU throughput B1 is blocked on. If this starts
    failing high, that conclusion is void and L5's substrate has to be
    re-planned.
    """
    _, buckets, _ = window
    assert td.peak_total_rps(buckets) < 10.0


def test_out_of_range_window_raises(window):
    with pytest.raises(IndexError):
        td.trace_window(td.window_count())
    with pytest.raises(IndexError):
        td.trace_window(-1)


def test_meta_carries_the_provenance_a_record_must_quote(window):
    """A live record has to say which window of which trace it replayed, at
    what scale. Without that the sitting is unreproducible even with the
    trace in hand."""
    _, _, meta = window
    for key in ("trace", "window_index", "window_start_s", "scale_factor",
                "rate_bucket_s", "control_interval_s", "tenants"):
        assert key in meta, f"meta is missing {key}"
    assert meta["scale_factor"] > 0
