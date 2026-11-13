"""Unit tests of the WP10 schedule generator. Synthetic days and prompts only:
no trace, no LMSYS text."""

from __future__ import annotations

import argparse
import csv
import json
from types import SimpleNamespace

import numpy as np
import pytest

import make_schedule as mk

NATIVE = mk.research_module("native")  # the real construction, fed synthetic days


def synthetic_days(n_days: int = 4, rate: float = 0.5, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.poisson(rate * mk.BIN_S, size=(n_days, NATIVE.DAY_BINS)).astype(np.int64)


TENANTS = [mk.Tenant("t00", "s/a", 4.0, "standard", 11), mk.Tenant("t01", "s/b", 2.0, "premium", 12)]
DAYS = {"s/a": synthetic_days(seed=1), "s/b": synthetic_days(seed=2)}


def test_first_user_turn_follows_the_wp6_rule():
    assert mk.first_user_turn([{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]) == "hi"
    assert mk.first_user_turn([{"role": "user", "content": ""}, {"role": "user", "content": "later"}]) is None
    assert mk.first_user_turn([{"role": "assistant", "content": "a"}]) is None
    assert mk.first_user_turn(None) is None


def test_take_prompts_skips_long_ones_and_offsets_admissible_turns():
    turns = ["a", "x" * 50, "b", "c", "d"]
    prompts, stats = mk.take_prompts(iter(turns), offset=1, count=2, max_bytes=10)
    assert prompts == ["b", "c"] and stats.skipped_long == 1 and stats.used == 2
    with pytest.raises(ValueError, match="admissible"):
        mk.take_prompts(iter(turns), offset=0, count=10, max_bytes=10)
    # bytes, not characters: three-byte characters count three times
    assert mk.take_prompts(iter(["中" * 4, "ok"]), 0, 1, 10)[0] == ["ok"]


def test_assign_prompts_gives_each_tenant_a_contiguous_run():
    arrivals = [mk.Arrival(0.1, "t01"), mk.Arrival(0.2, "t00"), mk.Arrival(0.3, "t01"), mk.Arrival(0.4, "t00")]
    reqs = mk.assign_prompts(arrivals, ["t00", "t01"], ["p0", "p1", "p2", "p3"])
    assert [(r["tenant"], r["prompt"]) for r in reqs] == [("t01", "p2"), ("t00", "p0"), ("t01", "p3"), ("t00", "p1")]
    assert [r["seq"] for r in reqs] == [0, 1, 2, 3]
    with pytest.raises(ValueError):
        mk.assign_prompts(arrivals, ["t00", "t01"], ["p0"])


def test_place_in_bins_keeps_counts_inside_their_bins():
    counts = {"t00": np.array([2, 0, 3]), "t01": np.array([1, 1, 0])}
    a = mk.place_in_bins(counts, np.random.default_rng(5))
    b = mk.place_in_bins(counts, np.random.default_rng(5))
    assert a == b and len(a) == 7 and [x.t for x in a] == sorted(x.t for x in a)
    per_bin = {(int(x.t // mk.BIN_S), x.tenant) for x in a}
    assert (1, "t00") not in per_bin and (2, "t01") not in per_bin


def test_utilisation_arithmetic():
    times = [1.0] * 30 + [61.0] * 60 + [125.0]  # last one in a partial window: left out
    w = mk.window_utilisation(times, 125.0, capacity_rps=2.0, share=0.5, hit_rate=0.0)
    assert list(w) == pytest.approx([30 * 0.5 / 120, 60 * 0.5 / 120])
    assert mk.mean_utilisation(120, 100.0, 2.0, 1.0, 0.25) == pytest.approx(0.45)


@pytest.mark.parametrize("load", [
    mk.Load(0.95, 0.99, 1.0), mk.Load(0.2, 0.9, 1.0), mk.Load(0.6, 0.5, 1.0),
    mk.Load(0.6, 0.9, 0.0), mk.Load(0.6, 0.9, 1.0, small_share=0.0), mk.Load(0.6, 0.9, 1.0, expected_hit_rate=1.0),
])
def test_load_validation(load):
    with pytest.raises(ValueError):
        load.validate()


def test_native_arrivals_hit_the_target_and_respect_the_window_cap():
    load = mk.Load(target_util=0.6, max_window_util=0.9, capacity_rps=1.5)
    scale, arrivals, counts = mk.native_arrivals(TENANTS, DAYS, run_seed=3, steps=60, load=load,
                                                 series=NATIVE.series)
    duration = 60 * mk.BIN_S
    assert sum(int(c.sum()) for c in counts.values()) == len(arrivals)
    assert load.util(len(arrivals), duration) == pytest.approx(0.6, abs=0.05)
    assert load.windows([a.t for a in arrivals], duration).max() <= 0.9 + 1e-9
    assert 0 < scale < 1
    # same seeds, same schedule
    again = mk.native_arrivals(TENANTS, DAYS, run_seed=3, steps=60, load=load, series=NATIVE.series)
    assert again[1] == arrivals


def test_native_counts_match_the_harness_construction():
    """steps + 1 buckets with rng [tenant seed, run seed], first `steps` kept."""
    counts = mk.native_counts(TENANTS[:1], DAYS, run_seed=9, steps=20, scale=0.1, series=NATIVE.series)
    rates, _ = NATIVE.series(DAYS["s/a"], 0.1 * 4.0, 21, np.random.default_rng([11, 9]))
    assert list(counts["t00"]) == list(np.rint(rates[:20] * mk.BIN_S).astype(int))


def test_fit_scale_refuses_a_window_too_bursty_for_the_band():
    load = mk.Load(target_util=0.6, max_window_util=0.65, capacity_rps=1.0)

    def bursty(scale):  # every request in the first minute of ten
        n = int(round(360 * scale))
        return [mk.Arrival(i * 60.0 / max(n, 1), "t00") for i in range(n)]

    with pytest.raises(ValueError, match="too bursty"):
        mk.fit_scale(bursty, 1.0, 600.0, load)


def test_trace_arrivals_thin_exactly_and_jitter_whole_seconds():
    raw = np.repeat(np.arange(0, 600, dtype=float), 3)  # 3 arrivals per whole second
    load = mk.Load(target_util=0.5, max_window_util=0.9, capacity_rps=2.0)
    frac, arrivals = mk.trace_arrivals(raw, 600.0, 3, run_seed=1, load=load, whole_seconds=True)
    assert len(arrivals) == 600 and frac == pytest.approx(600 / 1800)
    assert {a.tenant for a in arrivals} == {"t00", "t01", "t02"}
    assert any(a.t % 1 > 0 for a in arrivals)
    with pytest.raises(ValueError, match="busier window"):
        mk.trace_arrivals(raw[:100], 600.0, 1, 1, load, True)


def test_capacity_from_prefers_the_trace_rate(tmp_path):
    meta = tmp_path / "m.json"
    meta.write_text(json.dumps({"mu_pre_rps": 1.0, "mu_post_rps": 2.0}))
    assert mk.capacity_from(str(meta)) == 1.5
    meta.write_text(json.dumps({"mu_pre_rps": 1.0, "mu_post_rps": 2.0, "mu_trace_rps": 0.7}))
    assert mk.capacity_from(str(meta)) == 0.7


def test_run_native_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(mk, "load_population", lambda workload: TENANTS)
    fake_native = SimpleNamespace(load_days=lambda: DAYS, series=NATIVE.series)
    monkeypatch.setattr(mk, "research_module", lambda name: fake_native)
    prompts = (f"synthetic prompt {i}" for i in range(100000))
    args = mk.parse_args(["native", "--workload", "pop000", "--steps", "30", "--capacity-rps", "2.0",
                          "--target-util", "0.5", "--warmup-s", "60", "--out", str(tmp_path / "s")])
    meta = mk.run(args, prompts_source=lambda: prompts)
    out = tmp_path / "s"
    sched = json.loads((out / "schedule.json").read_text())
    reqs = sched["requests"]
    assert [r["seq"] for r in reqs] == list(range(len(reqs))) and len(reqs) == meta["requests"]
    assert [r["t"] for r in reqs] == sorted(r["t"] for r in reqs)
    assert all(r["warmup"] == (r["t"] < 60) for r in reqs)
    side = (out / "schedule.meta.json").read_text()
    assert "synthetic prompt" not in side
    rows = list(csv.DictReader((out / "demand_10s.csv").open()))
    assert sum(int(r["count"]) for r in rows) == len(reqs) and len(rows) == 30 * 2
    assert meta["utilisation"]["mean"] == pytest.approx(0.5, abs=0.1)
    assert {t["id"]: t["requests"] for t in meta["tenants"]} == {
        t: sum(1 for r in reqs if r["tenant"] == t) for t in ("t00", "t01")}


def test_main_reports_errors_without_a_traceback(monkeypatch, capsys):
    def boom(args, prompts_source=None):
        raise ValueError("bad window")
    monkeypatch.setattr(mk, "run", boom)
    assert mk.main(["native", "--workload", "pop000", "--capacity-rps", "1", "--out", "x"]) == 2
    assert "bad window" in capsys.readouterr().err


def test_load_population_reads_the_real_file_shape(tmp_path):
    pop = tmp_path / "population.json"
    pop.write_text(json.dumps({"workloads": [{"workload": "pop001", "tenants": [
        {"tenant": "t00", "stream": "s", "rate_rps": 1.5, "slo_class": "premium", "seed": 3}]}]}))
    assert mk.load_population("pop001", pop) == [mk.Tenant("t00", "s", 1.5, "premium", 3)]
    with pytest.raises(ValueError):
        mk.load_population("pop999", pop)
