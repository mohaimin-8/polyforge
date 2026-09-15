"""The in-run histogram scrape (clause 4's primary measurand), cluster-free."""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness import histogram as h  # noqa: E402

EXPO = """# HELP polyforge_http_request_duration_seconds HTTP request latency in seconds by method and route.
# TYPE polyforge_http_request_duration_seconds histogram
polyforge_http_request_duration_seconds_bucket{method="POST",route="/v1/tenants/{id}/workloads/replay",le="0.005"} 90
polyforge_http_request_duration_seconds_bucket{method="POST",route="/v1/tenants/{id}/workloads/replay",le="0.01"} 95
polyforge_http_request_duration_seconds_bucket{method="POST",route="/v1/tenants/{id}/workloads/replay",le="0.025"} 99
polyforge_http_request_duration_seconds_bucket{method="POST",route="/v1/tenants/{id}/workloads/replay",le="+Inf"} 100
polyforge_http_request_duration_seconds_sum{method="POST",route="/v1/tenants/{id}/workloads/replay"} 0.42
polyforge_http_request_duration_seconds_count{method="POST",route="/v1/tenants/{id}/workloads/replay"} 100
polyforge_http_request_duration_seconds_bucket{method="GET",route="/healthz",le="0.005"} 7
polyforge_http_request_duration_seconds_bucket{method="GET",route="/healthz",le="+Inf"} 7
polyforge_http_request_duration_seconds_sum{method="GET",route="/healthz"} 0.001
polyforge_http_request_duration_seconds_count{method="GET",route="/healthz"} 7
polyforge_other_metric_total{x="y"} 3
"""


def test_parse_reads_buckets_sum_count_per_route():
    s = h.parse(EXPO)
    replay = s["POST /v1/tenants/{id}/workloads/replay"]
    assert replay.count == 100 and abs(replay.sum - 0.42) < 1e-12
    assert replay.buckets[0.005] == 90 and replay.buckets[math.inf] == 100
    assert s["GET /healthz"].count == 7
    assert "polyforge_other_metric_total" not in str(s)


def test_quantile_interpolates_like_prometheus():
    replay = h.parse(EXPO)["POST /v1/tenants/{id}/workloads/replay"]
    # p50: rank 50 lies in the first bucket (0..0.005, count 90): 0.005*50/90
    assert abs(h.quantile(replay, 0.50) - 0.005 * 50 / 90) < 1e-12
    # p95: rank 95 hits the 0.01 bucket exactly -> its upper bound
    assert abs(h.quantile(replay, 0.95) - 0.01) < 1e-12
    # p99: rank 99 hits the 0.025 bucket exactly
    assert abs(h.quantile(replay, 0.99) - 0.025) < 1e-12
    # a rank inside +Inf returns the last finite bound
    assert h.quantile(replay, 0.999) == 0.025
    assert h.quantile(h.Series(), 0.95) is None


def test_window_differences_pod_by_pod_and_reports_lost_pods():
    start = {"a": h.parse(EXPO), "b": h.parse(EXPO), "gone": h.parse(EXPO)}
    end_text = EXPO.replace('le="0.005"} 90', 'le="0.005"} 190').replace('le="+Inf"} 100', 'le="+Inf"} 200') \
                   .replace('replay"} 100', 'replay"} 200').replace('replay"} 0.42', 'replay"} 0.84')
    end = {"a": h.parse(end_text), "b": h.parse(EXPO), "born": h.parse(EXPO)}
    win, lost = h.window(end, start)
    replay = win["POST /v1/tenants/{id}/workloads/replay"]
    # a: +100 requests in the window; b: 0; born: whole life = 100
    assert replay.count == 200
    assert replay.buckets[0.005] == 100 + 0 + 90
    assert lost == ["gone"]
    doc = h.summarize(win, pods_at_start=3, pods_at_end=3, lost_pods=lost)
    r = doc["routes"]["POST /v1/tenants/{id}/workloads/replay"]
    assert r["count"] == 200 and doc["pods_lost_in_window"] == ["gone"]
    assert 0 < r["p95_ms"] < 25
    assert "no tenant label" in doc["note"]
