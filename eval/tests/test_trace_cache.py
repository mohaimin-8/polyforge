"""The committed window projection must stay identical to the raw trace's.

The cache exists because the 56 MB BurstGPT trace is not vendored, so on a
clean clone `analysis_trace_live.py` could not rebuild `RESULTS_TRACE_LIVE.md`
at all — the reproduction gate was red in CI from 2026-08-31 while passing on
the machine that happens to hold the file.

A cached artifact is only worth carrying if a stale one FAILS rather than
replays quietly, which is the rule `separation_mt_v3_walk.json` already
follows. So: wherever the raw trace is present, the cache is re-derived and
compared byte for byte; where it is absent, the round-trip is still checked.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "eval"))

from harness import trace_demand as td  # noqa: E402

WINDOW = 0


def test_the_committed_window_is_present():
    assert td._cache_path(WINDOW).exists(), (
        "window 0's projection is what makes RESULTS_TRACE_LIVE.md rebuildable "
        "on a clean clone; rebuild it with "
        "`python -m harness.trace_demand --build-cache 0`")


def test_the_cache_round_trips_to_the_same_demand():
    # Arrange / Act
    tenant_ids, buckets, meta = td.load_cached_window(WINDOW)

    # Assert - shape first: a truncated artifact must not read as a short window.
    assert len(buckets) == td.BUCKETS_PER_WINDOW
    assert len(tenant_ids) == meta["tenants"]
    assert meta["window_index"] == WINDOW
    assert meta["kinds"] == ["chat"], "BurstGPT records LLM arrivals only"
    # And the bytes are stable across a load/serialise cycle.
    assert td.serialise_window(tenant_ids, buckets, meta) == \
        td.serialise_window(*td.load_cached_window(WINDOW))


@pytest.mark.skipif(not td.tm.TRACE.exists(),
                    reason="raw BurstGPT trace not present (expected on a clean clone/CI)")
def test_the_cache_still_matches_what_the_raw_trace_projects():
    # Arrange: recompute from the raw trace, bypassing the cache path.
    events = td.tm.load_events()

    # Act
    fresh = td.trace_window(WINDOW, events=events)
    cached = td.load_cached_window(WINDOW)

    # Assert: byte-identical, or the artifact is stale and the record built on
    # it is no longer the record the trace supports.
    assert td.serialise_window(*fresh) == td.serialise_window(*cached), (
        "the committed projection no longer matches the raw trace; rebuild it "
        "with `python -m harness.trace_demand --build-cache 0` and re-run the "
        "records that depend on it")


def test_the_peak_rate_the_conclusion_rests_on_survives_the_cache():
    # The whole reason L5 could run without a GPU is that this window's peak
    # is ~1.4 rps. If the cache ever carried a rescaled projection, that
    # conclusion would silently stop holding.
    _, buckets, _ = td.load_cached_window(WINDOW)
    assert td.peak_total_rps(buckets) < 5.0
