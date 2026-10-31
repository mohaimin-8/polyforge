"""Real-trace workloads (PREREG_TRACE_AUDITED): BurstGPT windows as harness
workloads.

A trace window must behave like any other workload in `workloads.build` and
`config.load`, must not change anything about the synthetic classes, and its
committed projection must match the raw trace wherever the trace is present.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from harness import config, trace_workloads as tw, workloads

TRACE = Path(__file__).resolve().parents[2] / "research" / "traces" / "out" / "burstgpt_real.csv.gz"


def test_names_cover_the_96_frozen_windows():
    names = tw.names()
    assert len(names) == tw.WINDOWS == 96
    assert names[0] == "burstgpt96_w00" and names[-1] == "burstgpt96_w95"
    assert all(tw.window_index(n) == i for i, n in enumerate(names))


@pytest.mark.parametrize("bad", ["burstgpt96_w96", "burstgpt96_w7", "burstgpt96_wxx",
                                 "burstgpt96_w001", "ai_cacheable"])
def test_malformed_window_names_are_rejected(bad):
    with pytest.raises(ValueError):
        tw.window_index(bad)


def test_every_window_is_committed():
    assert all(tw.cache_path(n).exists() for n in tw.names())


def test_a_window_is_six_hours_of_chat_for_eight_tenants():
    ids, buckets = tw.window("burstgpt96_w00", 2159)
    assert ids == [f"t{i:02d}" for i in range(8)]
    assert len(buckets) == 2160
    kinds = {k for b in buckets for d in b.values() for k in d.rps}
    assert kinds == {"chat"}
    assert tw.meta("burstgpt96_w00")["scale_factor"] == pytest.approx(75.1, abs=0.05)


def test_a_run_longer_than_the_window_is_refused():
    with pytest.raises(ValueError):
        tw.window("burstgpt96_w00", 2160)


def test_every_call_builds_fresh_demand_objects():
    _, first = tw.window("burstgpt96_w03", 10)
    _, second = tw.window("burstgpt96_w03", 10)
    assert first == second
    assert first[0]["t00"] is not second[0]["t00"]
    assert first[0]["t00"].rps is not second[0]["t00"].rps


def test_build_takes_slo_budget_and_caps_from_mix_and_size():
    ids, buckets, configs, limits = workloads.build("burstgpt96_w10", "premium_heavy", "medium", 7, 119)
    assert len(buckets) == 120
    size = workloads.CLUSTER_SIZES["medium"]
    slots = workloads.TENANT_MIXES["premium_heavy"]
    assert [configs[t].slo_class for t in ids] == [s.slo_class for s in slots]
    assert [configs[t].hourly_budget_usd for t in ids] == [s.budget_usd for s in slots]
    assert {configs[t].replica_max for t in ids} == {size.replica_max}
    assert (limits.cache_mb, limits.replicas) == (size.limits_cache_mb, size.limits_replicas)


def test_build_refuses_a_mix_of_the_wrong_size():
    with pytest.raises(ValueError):
        workloads.build("burstgpt96_w10", "uniform32", "medium", 7, 119)


def test_trace_windows_count_as_ai_workloads():
    assert workloads.workload_has_ai("burstgpt96_w42")


def test_the_default_workload_list_is_unchanged():
    assert not any(tw.is_trace(w) for w in config.ExperimentSpec(name="x").workloads)


def test_config_accepts_trace_windows_and_rejects_bad_ones(tmp_path):
    good = tmp_path / "good.yaml"
    good.write_text("name: t\nsystems: [static_share]\nworkloads: [burstgpt96_w00, ai_cacheable]\n"
                    "tenant_mixes: [uniform]\ncluster_sizes: [medium]\nreps: 1\nsteps: 10\n")
    assert config.load(good).workloads == ["burstgpt96_w00", "ai_cacheable"]
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: t\nsystems: [static_share]\nworkloads: [burstgpt96_w99]\n"
                   "tenant_mixes: [uniform]\ncluster_sizes: [medium]\nreps: 1\nsteps: 10\n")
    with pytest.raises(ValueError):
        config.load(bad)


@pytest.mark.skipif(not TRACE.exists(), reason="raw BurstGPT trace not fetched")
def test_the_committed_projections_match_the_raw_trace():
    fresh = tw.project_all()
    for name, blob in fresh.items():
        assert gzip.decompress(tw.cache_path(name).read_bytes()) == blob, name
