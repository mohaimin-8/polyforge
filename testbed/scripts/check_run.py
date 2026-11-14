"""Check and summarise one WP10 load run (programme 2).

From k6's sample CSV (replay.js's wp10_latency_from_due, one row per request,
tagged with tenant, tier, cache outcome, status, warm-up flag): requests,
failures, hit rate, latency per tier and cache outcome, and SLO attainment
with failures counted as misses (the plan's primary metric).

From Prometheus, when given (--prometheus and the run's --start/--end):
  load distribution  each model server's share of its tier's generated tokens;
                     a replica far below its fair share means requests are not
                     being spread (the port-forward defect class, WP14), and the
                     run's replica knob was not what the plant assumes;
  CPU throttling     the share of CFS periods each model server was throttled.

    python check_run.py --k6-csv ../out/runs/r1/k6-samples.csv.gz --slo-ms 8000 \
        --prometheus http://127.0.0.1:30090 --start 1794550000 --end 1794551800 \
        --out ../out/runs/r1/check.json
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import math
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

METRIC = "wp10_latency_from_due"
MIN_FAIR_SHARE = 0.5  # a replica below half its fair share of tokens is flagged


def open_text(path: Path) -> io.TextIOBase:
    if str(path).endswith(".gz"):
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", newline="")
    return open(path, encoding="utf-8", newline="")


def read_k6_requests(path: Path) -> list[dict]:
    """One dict per request from k6's CSV output (extra tags URL-encoded)."""
    out = []
    with open_text(path) as handle:
        for row in csv.DictReader(handle):
            if row["metric_name"] != METRIC:
                continue
            tags = {k: v[0] for k, v in urllib.parse.parse_qs(row.get("extra_tags") or "").items()}
            out.append({"seq": int(tags.get("seq", -1)), "tenant": tags.get("tenant", ""),
                        "tier": tags.get("tier", "none"), "cache": tags.get("cache", "none"),
                        "warmup": tags.get("warmup") == "true", "status": row.get("status") or tags.get("status", ""),
                        "latency_ms": float(row["metric_value"])})
    return out


def quantile(values: Sequence[float], q: float) -> float:
    if not values:
        return math.nan
    ordered = sorted(values)
    pos = q * (len(ordered) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def summarise(requests: Iterable[dict], slo_ms: float) -> dict:
    """Scored requests only (warm-up excluded). Attainment: answered with 200
    within the SLO, over all scored requests (a failure is a miss)."""
    scored = [r for r in requests if not r["warmup"]]
    ok = [r for r in scored if r["status"] == "200"]
    groups: dict[str, list[float]] = {}
    for r in ok:
        groups.setdefault(f"{r['tier']}/{r['cache']}", []).append(r["latency_ms"])
    return {
        "requests": len(scored),
        "failed": len(scored) - len(ok),
        "failed_rate": (len(scored) - len(ok)) / len(scored) if scored else math.nan,
        "hit_rate": sum(1 for r in ok if r["cache"] == "hit") / len(ok) if ok else math.nan,
        "slo_ms": slo_ms,
        "attainment": sum(1 for r in ok if r["latency_ms"] <= slo_ms) / len(scored) if scored else math.nan,
        "latency_ms": {k: {"n": len(v), "mean": sum(v) / len(v), "p50": quantile(v, 0.5),
                           "p95": quantile(v, 0.95), "p99": quantile(v, 0.99)} for k, v in sorted(groups.items())},
        "by_tenant": {t: sum(1 for r in scored if r["tenant"] == t) for t in sorted({r["tenant"] for r in scored})},
    }


Query = Callable[[str, float], list[dict]]


def prometheus_query(base: str) -> Query:
    def query(promql: str, at: float) -> list[dict]:
        url = f"{base.rstrip('/')}/api/v1/query?" + urllib.parse.urlencode({"query": promql, "time": at})
        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 (operator-given URL)
            body = json.load(resp)
        if body.get("status") != "success":
            raise RuntimeError(f"Prometheus query failed: {body}")
        return body["data"]["result"]
    return query


def by_labels(result: Iterable[dict], *labels: str) -> dict[tuple, float]:
    return {tuple(r["metric"].get(l, "") for l in labels): float(r["value"][1]) for r in result}


def pod_shares(tokens: Mapping[tuple, float]) -> dict[str, dict[str, float]]:
    """tier -> pod -> share of the tier's generated tokens."""
    tiers: dict[str, dict[str, float]] = {}
    for (tier, pod), value in tokens.items():
        tiers.setdefault(tier, {})[pod] = value
    return {tier: {pod: (v / sum(pods.values()) if sum(pods.values()) > 0 else 0.0) for pod, v in pods.items()}
            for tier, pods in tiers.items()}


def distribution_findings(shares: Mapping[str, Mapping[str, float]], min_fair: float = MIN_FAIR_SHARE) -> list[str]:
    findings = []
    for tier, pods in shares.items():
        if len(pods) < 2:
            continue
        fair = 1.0 / len(pods)
        for pod, share in sorted(pods.items()):
            if share < min_fair * fair:
                findings.append(f"{tier}: {pod} served {share:.1%} of the tokens, fair share {fair:.1%}")
    return findings


MIN_TOKENS_PER_POD = 20 * 48   # about 20 answers of 48 tokens per pod: below that a split is noise


def live_pods(query: Query, end: float) -> set[tuple]:
    """(tier, pod) of the model servers scraped in the window's last 30 s. A pod
    scaled down as the run starts keeps a series inside the window but serves
    nothing; it is not a starved replica (WP11, 2026-11-14: three runs flagged
    so, with the live pods splitting the load 0.496 / 0.504)."""
    return set(by_labels(query("count by (tier, pod) (last_over_time(llamacpp:tokens_predicted_total[30s]))", end),
                         "tier", "pod"))


def judged(tokens: Mapping[tuple, float], alive: set) -> tuple[dict, dict]:
    """The live pods whose spread can be judged, and each skipped tier with why:
    a tier that got too little work (one request on two pods) has no fair share."""
    live = {k: v for k, v in tokens.items() if k in alive}
    totals: dict[str, list[float]] = {}
    for (tier, _pod), v in live.items():
        totals.setdefault(tier, []).append(v)
    kept, skipped = {}, {}
    for (tier, pod), v in live.items():
        total, n = sum(totals[tier]), len(totals[tier])
        if total < MIN_TOKENS_PER_POD * n:
            skipped[tier] = f"{total:.0f} tokens over {n} pods: too few to judge the spread"
        else:
            kept[(tier, pod)] = v
    return kept, skipped


def prometheus_checks(query: Query, start: float, end: float) -> dict:
    window = f"{max(1, int(end - start))}s"
    all_tokens = by_labels(query(f"sum by (tier, pod) (increase(llamacpp:tokens_predicted_total[{window}]))", end),
                           "tier", "pod")
    alive = live_pods(query, end)
    tokens, skipped = judged(all_tokens, alive)
    throttled = by_labels(query(
        f'sum by (pod) (increase(container_cpu_cfs_throttled_periods_total{{container="llama-server"}}[{window}]))'
        f' / sum by (pod) (increase(container_cpu_cfs_periods_total{{container="llama-server"}}[{window}]))', end), "pod")
    cpu = by_labels(query(f'sum by (pod) (rate(container_cpu_usage_seconds_total{{container="llama-server"}}[{window}]))',
                          end), "pod")
    shares = pod_shares(tokens)
    return {"window": window, "token_shares": shares, "distribution_findings": distribution_findings(shares),
            "token_shares_all_series": pod_shares(all_tokens), "distribution_skipped": skipped,
            "pods_not_live": sorted("/".join(k) for k in all_tokens if k not in alive),
            "throttled_period_share": {k[0]: v for k, v in throttled.items()},
            "cpu_cores_used": {k[0]: v for k, v in cpu.items()}}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--k6-csv", required=True)
    ap.add_argument("--slo-ms", type=float, required=True, help="latency target for attainment")
    ap.add_argument("--prometheus", default=None, help="e.g. http://127.0.0.1:30090")
    ap.add_argument("--start", type=float, default=None, help="unix time the scored window opened")
    ap.add_argument("--end", type=float, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    report = {"k6": summarise(read_k6_requests(Path(args.k6_csv)), args.slo_ms)}
    if args.prometheus:
        if args.start is None or args.end is None or args.end <= args.start:
            ap.error("--prometheus needs --start < --end")
        report["prometheus"] = prometheus_checks(prometheus_query(args.prometheus), args.start, args.end)
    text = json.dumps(report, indent=1, sort_keys=True)
    print(text)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    findings = report.get("prometheus", {}).get("distribution_findings", [])
    if findings:
        print("load distribution: " + "; ".join(findings), file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
