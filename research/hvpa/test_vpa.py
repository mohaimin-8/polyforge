"""The VPA mirror against hand-computed values of its upstream formulas."""

import math

import pytest

import vpa


def test_buckets_follow_the_exponential_options():
    assert vpa.bucket_start(0) == 0.0 and vpa.bucket_start(1) == pytest.approx(0.01)
    assert vpa.bucket_start(2) == pytest.approx(0.01 + 0.0105)
    assert vpa.find_bucket(0.0) == 0 and vpa.find_bucket(0.0105) == 1
    assert vpa.find_bucket(1e9) == vpa.NUM_BUCKETS - 1
    for v in (0.03, 0.25, 1.7):
        b = vpa.find_bucket(v)
        assert vpa.bucket_start(b) <= v < vpa.bucket_start(b + 1)


def test_percentile_is_the_end_of_the_crossing_bucket():
    h = vpa.DecayingHistogram()
    for _ in range(9):
        h.add(0.5, 0.1, 0.0)
    h.add(1.5, 0.1, 0.0)
    b = vpa.find_bucket(0.5)
    assert h.percentile(0.5) == pytest.approx(vpa.bucket_start(b + 1))
    assert h.percentile(0.95) == pytest.approx(vpa.bucket_start(vpa.find_bucket(1.5) + 1))
    assert vpa.DecayingHistogram().percentile(0.9) == 0.0


def test_later_samples_weigh_more_by_the_half_life():
    h = vpa.DecayingHistogram()
    h.add(0.2, 0.1, 0.0)
    h.add(0.8, 0.1, vpa.HALF_LIFE_S)   # twice the weight of the first
    assert h.total == pytest.approx(0.3)
    assert h.percentile(0.4) == pytest.approx(vpa.bucket_start(vpa.find_bucket(0.8) + 1))


def test_bounds_are_wide_after_one_hour_and_capped_by_policy():
    r = vpa.Recommender()
    for minute in range(61):
        r.add_sample(0.4, minute * 60.0)
    c = r.confidence()
    assert c == pytest.approx(1 / 24, rel=1e-9)   # 60 min of lifespan; 61 samples / 1440 is larger
    rec = r.recommend()
    top = vpa.bucket_start(vpa.find_bucket(0.4) + 1)
    assert rec.target == pytest.approx(top * 1.15)
    assert rec.upper == pytest.approx(top * 1.15 * (1 + 24))
    assert rec.lower == pytest.approx(top * 1.15 * (1 + 0.024) ** -2)
    capped = vpa.Recommender(min_allowed=0.25, max_allowed=2.0)
    for minute in range(61):
        capped.add_sample(0.4, minute * 60.0)
    assert capped.recommend().upper == 2.0


def test_floor_and_a_single_sample():
    r = vpa.Recommender()
    r.add_sample(0.001, 0.0)
    rec = r.recommend()
    assert rec.target == vpa.MIN_CPU and rec.lower == vpa.MIN_CPU and math.isinf(rec.upper)


def test_updater_raises_a_starved_pod_and_leaves_a_generous_one_in_a_short_run():
    rec = vpa.Recommendation(target=0.6, lower=0.5, upper=12.0)
    assert vpa.pods_to_update([0.25, 0.25, 1.0], [600.0] * 3, rec) == [0]   # tolerance: 1 of 3 per loop
    assert vpa.pods_to_update([0.25, 0.25, 0.25, 0.25], [600.0] * 4, rec) == [0, 1]
    assert vpa.pods_to_update([1.0, 1.0], [600.0] * 2, rec) == []           # inside the range: kept
    assert vpa.pods_to_update([1.0, 1.0], [13 * 3600.0] * 2, rec) == [0]     # past 12 h: lowered
    assert vpa.pods_to_update([0.25], [600.0], rec) == []                    # below min-replicas
    assert vpa.pods_to_update([0.25], [600.0], rec, min_replicas=1) == [0]
