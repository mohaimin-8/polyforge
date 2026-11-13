"""Unit tests of the run checker: k6 CSV parsing, attainment, distribution."""

from __future__ import annotations

import gzip
import math

import pytest

import check_run as cr

HEADER = ("metric_name,timestamp,metric_value,check,error,error_code,expected_response,group,method,name,"
          "proto,scenario,service,status,subproto,tls_version,url,extra_tags,metadata\n")


def k6_row(value: float, status: str, **tags) -> str:
    extra = "&".join(f"{k}={v}" for k, v in tags.items())
    return f"{cr.METRIC},1794545440,{value:.6f},,,,,,,,,replay,,{status},,,,{extra},\n"


def write_csv(path, rows, gz=True):
    data = HEADER + "".join(rows) + "http_req_duration,1794545440,5.0,,,,true,,POST,chat,,replay,,200,,,,,\n"
    if gz:
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write(data)
    else:
        path.write_text(data, encoding="utf-8")


def test_reads_one_row_per_request_with_tags(tmp_path):
    path = tmp_path / "s.csv.gz"
    write_csv(path, [k6_row(1200.0, "200", tenant="t00", seq=0, tier="small", cache="miss", warmup="false"),
                     k6_row(15.0, "200", tenant="t01", seq=1, tier="small", cache="hit", warmup="true")])
    reqs = cr.read_k6_requests(path)
    assert [r["seq"] for r in reqs] == [0, 1]
    assert reqs[0] == {"seq": 0, "tenant": "t00", "tier": "small", "cache": "miss", "warmup": False,
                       "status": "200", "latency_ms": 1200.0}
    plain = tmp_path / "s.csv"
    write_csv(plain, [k6_row(1.0, "502", tenant="t00", seq=5, tier="none", cache="miss", warmup="false")], gz=False)
    assert cr.read_k6_requests(plain)[0]["status"] == "502"


def test_summary_counts_failures_as_misses_and_drops_warmup():
    reqs = [
        {"seq": 0, "tenant": "t00", "tier": "small", "cache": "miss", "warmup": False, "status": "200", "latency_ms": 900.0},
        {"seq": 1, "tenant": "t00", "tier": "small", "cache": "hit", "warmup": False, "status": "200", "latency_ms": 20.0},
        {"seq": 2, "tenant": "t01", "tier": "mid", "cache": "miss", "warmup": False, "status": "200", "latency_ms": 9000.0},
        {"seq": 3, "tenant": "t01", "tier": "none", "cache": "none", "warmup": False, "status": "502", "latency_ms": 1.0},
        {"seq": 4, "tenant": "t01", "tier": "small", "cache": "miss", "warmup": True, "status": "200", "latency_ms": 1.0},
    ]
    s = cr.summarise(reqs, slo_ms=1000.0)
    assert s["requests"] == 4 and s["failed"] == 1 and s["failed_rate"] == 0.25
    assert s["attainment"] == 0.5  # two of four within 1 s; the 502 counts as a miss
    assert s["hit_rate"] == pytest.approx(1 / 3)
    assert set(s["latency_ms"]) == {"small/miss", "small/hit", "mid/miss"}
    assert s["by_tenant"] == {"t00": 2, "t01": 2}


def test_quantile_interpolates():
    assert cr.quantile([1, 2, 3, 4], 0.5) == 2.5
    assert math.isnan(cr.quantile([], 0.5))


def test_pod_shares_and_distribution_findings():
    tokens = {("small", "a"): 900.0, ("small", "b"): 100.0, ("mid", "c"): 50.0}
    shares = cr.pod_shares(tokens)
    assert shares["small"] == {"a": 0.9, "b": 0.1} and shares["mid"] == {"c": 1.0}
    findings = cr.distribution_findings(shares)
    assert findings == ["small: b served 10.0% of the tokens, fair share 50.0%"]
    assert cr.distribution_findings({"small": {"a": 0.55, "b": 0.45}}) == []


def test_prometheus_checks_build_queries_over_the_run_window():
    seen = []

    def query(promql, at):
        seen.append((promql, at))
        if "tokens_predicted" in promql:
            return [{"metric": {"tier": "small", "pod": "a"}, "value": [at, "10"]},
                    {"metric": {"tier": "small", "pod": "b"}, "value": [at, "10"]}]
        return [{"metric": {"pod": "a"}, "value": [at, "0.05"]}]

    out = cr.prometheus_checks(query, start=1000.0, end=1600.0)
    assert out["window"] == "600s" and out["distribution_findings"] == []
    assert out["throttled_period_share"] == {"a": 0.05}
    assert all(at == 1600.0 and "[600s]" in q for q, at in seen)
