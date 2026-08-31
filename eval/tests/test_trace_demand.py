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


# --- the k6 encoding, which nearly destroyed the whole idea ------------------

def _stage_targets(script: str, tenant: str) -> list[int]:
    import re
    match = re.search(r'"tenant_%s".*?"stages":\s*\[(.*?)\]' % tenant, script, re.S)
    assert match, f"no stages block for {tenant}"
    return [int(x) for x in re.findall(r'"target":\s*(\d+)', match.group(1))]


def _run_spec():
    from harness.config import RunSpec
    return RunSpec(run_id="l5", experiment="trace_live", system="jcac",
                   workload="ai_cacheable", tenant_mix="uniform",
                   cluster_size="small", rep=0, seed=1, steps=10,
                   backend="cluster", store_timeseries=False)


def test_default_k6_encoding_would_destroy_trace_demand(window):
    """The defect, pinned so nobody 'simplifies' the fix away.

    k6 arrival rates are integers per timeUnit. At the default "1s" every
    trace bucket (0.0-0.501 rps) rounds to 0 and is then floored to 1: each
    tenant becomes a FLAT 1 rps, idle buckets get invented traffic, and the
    run drives ~25x the trace's real demand while looking perfectly healthy.
    """
    from harness.cluster_backend import k6_script
    tenant_ids, buckets, _ = window
    script = k6_script(_run_spec(), buckets_override=buckets,
                       tenant_ids_override=tenant_ids)
    for tid in tenant_ids:
        targets = _stage_targets(script, tid)
        assert len(set(targets)) == 1, (
            f"{tid} is not flat under the default encoding -- if this now "
            "varies, the defect is gone and this test should be retired")
        assert 0 not in targets, "the default floor should have removed every idle bucket"


def test_trace_encoding_preserves_shape_idleness_and_volume(window):
    """The fix: 1/60 rps resolution and no floor."""
    from harness.cluster_backend import k6_script, TRACE_TIME_UNIT
    tenant_ids, buckets, _ = window
    script = k6_script(_run_spec(), buckets_override=buckets,
                       tenant_ids_override=tenant_ids,
                       time_unit=TRACE_TIME_UNIT, floor_rate=False)

    encoded_total = 0.0
    for tid in tenant_ids:
        targets = _stage_targets(script, tid)
        real = {round(b[tid].total_rps(), 4) for b in buckets}
        # Shape: as many distinct rates as the trace actually has.
        assert len(set(targets)) == len(real), (
            f"{tid}: encoded {len(set(targets))} distinct rates, trace has {len(real)}")
        # Idleness: the trace's silent buckets stay silent.
        assert targets.count(0) == sum(
            1 for b in buckets if b[tid].total_rps() == 0.0)
        encoded_total += sum(targets) / 60.0

    actual_total = sum(sum(b[t].total_rps() for t in tenant_ids) for b in buckets)
    # Volume: within a few percent, the residual being 1/60 rps rounding.
    assert encoded_total == pytest.approx(actual_total, rel=0.05)


# --- the load-distribution ceiling, which failed a healthy 2 h run ----------

class _Sampler:
    def __init__(self, shares):
        self._shares, self.samples, self.failures = shares, 10, 0

    def shares(self):
        return self._shares


def test_pinning_is_still_detected_at_sixteen_replicas():
    """The ceiling exists for WP14 attempt 4, where kubectl port-forward put
    every request on one pod of sixteen until it OOMed. That detection must be
    exactly as sensitive as before."""
    from harness.cluster_backend import check_load_distribution
    pinned = {f"p{i}": (1.0 if i == 0 else 0.0) for i in range(16)}
    with pytest.raises(RuntimeError, match="load was pinned"):
        check_load_distribution(_Sampler(pinned))


def test_even_split_at_sixteen_replicas_passes():
    from harness.cluster_backend import check_load_distribution
    check_load_distribution(_Sampler({f"p{i}": 1 / 16 for i in range(16)}))


def test_single_replica_is_not_pinning():
    """The defect this fixes. `max_share` of 0.25 is 'four times the fair
    share' only at sixteen replicas; at one replica the fair share IS 100%, so
    the ceiling could not be satisfied by any run. L5's trace-driven sitting
    peaks at 1.377 rps, HPA correctly held one replica, and a complete two-hour
    run was thrown away for having nowhere to spread load to."""
    from harness.cluster_backend import check_load_distribution
    check_load_distribution(_Sampler({"only-pod": 1.0}))


def test_ceiling_tracks_the_fair_share_between_those_extremes():
    """Four replicas: fair share 25%, so the ceiling is 100% and no split can
    be called pinned. Eight: fair share 12.5%, ceiling 50%, so 60% on one pod
    still is."""
    from harness.cluster_backend import check_load_distribution
    check_load_distribution(_Sampler({f"p{i}": 0.25 for i in range(4)}))
    hot = {"p0": 0.6, **{f"p{i}": 0.4 / 7 for i in range(1, 8)}}
    with pytest.raises(RuntimeError, match="load was pinned"):
        check_load_distribution(_Sampler(hot))
