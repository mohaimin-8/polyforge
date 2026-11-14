"""The WP11 predictor on a synthetic calibration and a made-up demand."""

import math
import sys
from pathlib import Path

import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import predict  # noqa: E402

CAL = {"small": {"a": 0.8, "l0_s": 1.2, "f0": 1.8, "b": 0.0, "capacity_per_replica_rps": 0.4,
                 "measured_at_replicas": 1},
       "mid": {"a": 0.8, "l0_s": 2.4, "f0": 1.8, "b": 0.0, "capacity_per_replica_rps": 0.15,
               "measured_at_replicas": 1}}


def _demand(rate_rps: float, steps: int = 12) -> pd.DataFrame:
    return pd.DataFrame({"step": range(steps), "tenant": "t00", "count": [rate_rps * 10] * steps})


def _cell(**kw):
    base = {"cell": 0, "small_share": 1.0, "pools": [1, 1], "cache": "off"}
    return {**base, **kw}


def test_light_load_is_the_unloaded_latency_and_on_time():
    p = predict.predict_cell(_cell(), _demand(0.001), CAL, 0.1)
    assert p["mean_latency_s"] == pytest.approx(1.2, rel=0.02)
    assert p["attainment"] > 0.99 and p["throughput_rps"] == pytest.approx(0.001)


def test_heavier_load_is_slower_and_less_on_time():
    light = predict.predict_cell(_cell(), _demand(0.1), CAL, 0.1)
    heavy = predict.predict_cell(_cell(), _demand(0.35), CAL, 0.1)
    assert heavy["mean_latency_s"] > light["mean_latency_s"] and heavy["attainment"] < light["attainment"]
    assert heavy["p95_latency_s"] > heavy["mean_latency_s"]


def test_more_pods_relieve_the_load_and_cost_more():
    one = predict.predict_cell(_cell(pools=[1, 1]), _demand(0.35), CAL, 0.1)
    three = predict.predict_cell(_cell(pools=[3, 1]), _demand(0.35), CAL, 0.1)
    assert three["mean_latency_s"] < one["mean_latency_s"] and three["cost"] > one["cost"]


def test_cache_hits_answer_fast_and_mixing_tiers_counts_every_request():
    off = predict.predict_cell(_cell(small_share=0.6), _demand(0.2), CAL, 0.1)
    big = predict.predict_cell(_cell(small_share=0.6, cache="large"), _demand(0.2), CAL, 0.1)
    assert big["mean_latency_s"] < off["mean_latency_s"]
    assert off["requests"] == pytest.approx(0.2 * 10 * 12) and not math.isnan(off["p95_latency_s"])


def test_the_published_form_is_restored():
    import model

    predict.predict_cell(_cell(), _demand(0.1), CAL, 0.1)
    assert model.ATTAINMENT_SLO is False and model.SLO_BASE_MS["ai"] == 2500.0
