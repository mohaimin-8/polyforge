"""The WP11 predictor on a synthetic calibration and made-up demand."""

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import predict  # noqa: E402

CAL = {"small": {"a": 0.8, "b": 0.0, "l0_s": 1.2, "f0": 1.8, "capacity_per_replica_rps": 0.4},
       "mid": {"a": 0.8, "b": 0.0, "l0_s": 2.4, "f0": 1.8, "capacity_per_replica_rps": 0.15}}
PINS = {"t00": "small", "t01": "mid"}


def _demand(rate_rps: float, share: float = 1.0) -> dict:
    out = {}
    for step in range(predict.cs.STEPS):
        out[(step, "t00")] = rate_rps * 10 * share
        out[(step, "t01")] = rate_rps * 10 * (1 - share)
    return out


def _cell(**kw):
    return {"cell": 0, "pools": [1, 1], "cache": "off", **kw}


def test_light_load_is_the_unloaded_latency_and_on_time():
    p = predict.predict_cell(_cell(), _demand(0.001), PINS, None, CAL, 0.1)
    assert p["mean_latency_s"] == pytest.approx(1.2, rel=0.02)
    assert p["attainment"] > 0.99 and p["throughput_rps"] == pytest.approx(0.001)


def test_heavier_load_is_slower_and_less_on_time():
    light = predict.predict_cell(_cell(), _demand(0.1), PINS, None, CAL, 0.1)
    heavy = predict.predict_cell(_cell(), _demand(0.35), PINS, None, CAL, 0.1)
    assert heavy["mean_latency_s"] > light["mean_latency_s"] and heavy["attainment"] < light["attainment"]
    assert heavy["p95_latency_s"] > heavy["mean_latency_s"]


def test_more_pods_relieve_the_load_and_cost_more():
    one = predict.predict_cell(_cell(pools=[1, 1]), _demand(0.35), PINS, None, CAL, 0.1)
    three = predict.predict_cell(_cell(pools=[3, 1]), _demand(0.35), PINS, None, CAL, 0.1)
    assert three["mean_latency_s"] < one["mean_latency_s"] and three["cost"] > one["cost"]
    assert three["cost"] == pytest.approx((3 * 1 + 1 * 2) * 0.1 * (60 - 12) * 10 / 3600)


def test_hits_answer_fast_and_both_pools_count():
    demand = _demand(0.2, share=0.6)
    hits = {k: 0.3 * n for k, n in demand.items()}
    off = predict.predict_cell(_cell(), demand, PINS, None, CAL, 0.1)
    on = predict.predict_cell(_cell(), demand, PINS, hits, CAL, 0.1)
    assert on["mean_latency_s"] < off["mean_latency_s"] and on["attainment"] > off["attainment"]
    assert on["hit_rate"] == pytest.approx(0.3) and off["hit_rate"] == 0.0
    assert off["requests"] == pytest.approx(0.2 * 10 * 48) and not math.isnan(off["p95_latency_s"])


def test_warm_up_steps_are_not_scored():
    demand = {(s, "t00"): 5.0 for s in range(predict.WARMUP_STEPS)}
    demand[(predict.WARMUP_STEPS, "t00")] = 1.0
    p = predict.predict_cell(_cell(), demand, PINS, None, CAL, 0.1)
    assert p["requests"] == 1.0 and p["scored_s"] == (predict.cs.STEPS - predict.WARMUP_STEPS) * 10


@pytest.mark.parametrize("hit", [0.0, 0.1, 0.45])
def test_hits_as_data_equal_the_simulators_cache_arithmetic(hit):
    """A hit share fed as data gives the simulator's own result for that share."""
    import model

    n, pods = 3.0, 1
    slo_ms = predict.SLO_FACTOR * CAL["small"]["l0_s"] * 1000
    with predict.tier_form(CAL["small"], slo_ms) as cfg:
        decomposed = predict.step_outcome(cfg, pods, "small", n, n * hit)
        # the simulator's cache, its curve set so that a 1 MB cache hits `hit`
        model.set_economy(tier_cost_usd_per_req={"small": 0.0, "mid": 0.0, "large": 0.0},
                          cache_hit_max=2 * hit if hit else 0.0, cache_half_mb=1.0)
        assert model.hit_rate(1) == pytest.approx(hit)
        m = model.evaluate_step(cfg, model.TenantState(replicas=pods, cache_mb=1, tier="small"),
                                model.Demand(rps={"chat": n / 10}))
    assert decomposed["within"] / n == pytest.approx(1.0 - m.violation, abs=1e-12)


def test_the_published_form_is_restored():
    import model

    predict.predict_cell(_cell(), _demand(0.1), PINS, None, CAL, 0.1)
    assert model.ATTAINMENT_SLO is False and model.SLO_BASE_MS["ai"] == 2500.0


def test_miss_latency_is_per_tenant_and_grows_with_load():
    lat = predict.miss_latency_s(_cell(), _demand(0.3, share=0.5), PINS, CAL)
    assert lat[("t01", 0)] > lat[("t00", 0)] > CAL["small"]["l0_s"]


def test_steady_hit_rate_follows_the_wp6_curve():
    assert predict.steady_hit_rate(0) == 0.0
    small, large = predict.steady_hit_rate(1), predict.steady_hit_rate(16)
    assert 0.0 < small < large < 0.25


def _curve_dir(tmp_path: Path, l0=1.0, a=0.7, mu=2.0, l0_trace=1.5, mu_trace=1.6) -> Path:
    rows = [{"phase": "baseline", "level": "", "pass_no": "", "seq": i, "scheduled_s": "", "latency_s": l0}
            for i in range(30)]
    rows += [{"phase": "baseline_trace", "level": "", "pass_no": "", "seq": i, "scheduled_s": "",
              "latency_s": l0_trace * (1.0 if i < 28 else 2.0)} for i in range(30)]
    for rho in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9):
        for i in range(20):
            rows.append({"phase": "load", "level": rho, "pass_no": i % 2, "seq": i, "scheduled_s": i,
                         "latency_s": l0 * (1 - rho) ** (-a)})
    d = tmp_path / "small_r1"
    d.mkdir()
    pd.DataFrame(rows).to_csv(d / "load_curve_requests.csv", index=False)
    (d / "load_curve_meta.json").write_text(json.dumps(
        {"l0_s": l0, "l0_trace_s": l0_trace, "mu_pre_rps": mu, "mu_post_rps": mu, "mu_trace_rps": mu_trace,
         "replicas": 1}))
    return d


def test_calibration_takes_shape_from_the_protocol_and_level_from_real_prompts(tmp_path):
    cal = predict.calibrate(_curve_dir(tmp_path))
    assert cal["a"] == pytest.approx(0.7, abs=1e-9) and cal["b"] == pytest.approx(0.0, abs=1e-9)
    assert cal["l0_s"] == pytest.approx(1.5) and cal["capacity_per_replica_rps"] == 1.6
    assert cal["f0"] == pytest.approx(2.0 * 1.5 / np.mean([1.5] * 28 + [3.0] * 2))


def test_calibration_without_the_real_prompt_phases_is_refused(tmp_path):
    d = _curve_dir(tmp_path)
    meta = json.loads((d / "load_curve_meta.json").read_text())
    del meta["l0_trace_s"]
    (d / "load_curve_meta.json").write_text(json.dumps(meta))
    with pytest.raises(ValueError, match="l0_trace_s"):
        predict.calibrate(d)
