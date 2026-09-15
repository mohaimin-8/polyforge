"""The corrected latency measurand, scraped inside the run.

PREREG_WAVE4_LIVE_PLANE.md's session-44 amendment clause 4 makes the
middleware histogram `polyforge_http_request_duration_seconds` the primary
latency measure and carries the replay-clock export alongside. The B1
sitting of 2026-09-15 did not capture it: the scrape lived in a separate
script the runbook listed under "export", and per-run clusters are gone
once the run ends. This module puts the scrape where it cannot be skipped:
`execute()` calls `scrape()` when the scored window opens and again when
the load step returns, and `window()` differences the two.

Two facts shape what the histogram can and cannot say, both stated in the
record rather than hidden:
- it is labelled by method and route, not by tenant, so it yields route-level
  p95/p99 and no per-tenant fairness term;
- every control-plane pod keeps its own in-process histogram, so a NodePort
  scrape samples one pod. `scrape()` reads every pod through the API-server
  proxy (`kubectl get --raw`, no curl in a distroless image) and sums.

Quantiles follow Prometheus's histogram_quantile: linear interpolation
inside the bucket that crosses the rank, the top bucket's lower bound when
the rank falls in +Inf.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
from dataclasses import dataclass, field

METRIC = "polyforge_http_request_duration_seconds"
NAMESPACE = "polyforge"
POD_SELECTOR = "app.kubernetes.io/name=polyforge-control-plane"
POD_PORT = 8080

# Label values may contain braces (route="/v1/tenants/{id}/..."), so the
# label block is matched quote-aware: unquoted chars other than " and }, or
# whole quoted strings, until the closing brace.
_LINE = re.compile(
    rf'^{METRIC}_(?P<kind>bucket|sum|count)'
    r'\{(?P<labels>(?:[^"}]|"(?:[^"\\]|\\.)*")*)\}'
    r'\s+(?P<value>[-+0-9.eE]+|\+Inf)\s*$'
)
_LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


@dataclass
class Series:
    """One (method, route): cumulative bucket counts keyed by upper bound."""

    buckets: dict[float, float] = field(default_factory=dict)
    sum: float = 0.0
    count: float = 0.0

    def add(self, other: "Series", sign: float = 1.0) -> None:
        for le, c in other.buckets.items():
            self.buckets[le] = self.buckets.get(le, 0.0) + sign * c
        self.sum += sign * other.sum
        self.count += sign * other.count


def parse(text: str) -> dict[str, Series]:
    """Series keyed 'METHOD route' from one pod's exposition text."""
    out: dict[str, Series] = {}
    for line in text.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        labels = dict(_LABEL.findall(m.group("labels")))
        key = f"{labels.get('method', '?')} {labels.get('route', '?')}"
        s = out.setdefault(key, Series())
        kind, value = m.group("kind"), m.group("value")
        if kind == "bucket":
            le = math.inf if value == "+Inf" or labels.get("le") == "+Inf" else float(labels["le"])
            s.buckets[le] = float(value)
        elif kind == "sum":
            s.sum = float(value)
        else:
            s.count = float(value)
    return out


def merge(per_pod: list[dict[str, Series]]) -> dict[str, Series]:
    total: dict[str, Series] = {}
    for series in per_pod:
        for key, s in series.items():
            total.setdefault(key, Series()).add(s)
    return total


def window(end: dict[str, dict[str, Series]],
           start: dict[str, dict[str, Series]]) -> tuple[dict[str, Series], list[str]]:
    """The scored window's own counts per route: end minus start, POD BY POD,
    then summed. A pod present only at the end was born inside the window and
    counts whole; a pod present only at the start was scaled away before the
    end and its window counts are lost -- returned as the second value so the
    record can say so rather than subtract them from other pods."""
    total: dict[str, Series] = {}
    for pod, series in end.items():
        base = start.get(pod, {})
        for key, s in series.items():
            w = Series(); w.add(s)
            if key in base:
                w.add(base[key], -1.0)
            total.setdefault(key, Series()).add(w)
    lost = sorted(p for p in start if p not in end)
    return total, lost


def quantile(s: Series, q: float) -> float | None:
    """histogram_quantile(q, ...) in seconds; None when the series is empty."""
    if s.count <= 0 or not s.buckets:
        return None
    rank = q * s.count
    bounds = sorted(s.buckets)
    prev_le, prev_c = 0.0, 0.0
    for le in bounds:
        c = s.buckets[le]
        if c >= rank:
            if math.isinf(le):
                return prev_le  # Prometheus: the lower bound of +Inf
            if c == prev_c:
                return le
            return prev_le + (le - prev_le) * (rank - prev_c) / (c - prev_c)
        prev_le, prev_c = le, c
    return bounds[-1] if not math.isinf(bounds[-1]) else prev_le


def _kubectl(*args: str) -> str:
    return subprocess.run(["kubectl", *args], capture_output=True, text=True,
                          check=True, timeout=60).stdout


def scrape(namespace: str = NAMESPACE) -> dict[str, dict[str, Series]]:
    """Every control-plane pod's histogram, keyed by pod. Raises on a kubectl
    failure; the caller decides whether a missing scrape voids the run."""
    pods = _kubectl("--namespace", namespace, "get", "pods", "-l", POD_SELECTOR,
                    "-o", "jsonpath={.items[*].metadata.name}").split()
    return {pod: parse(_kubectl(
        "get", "--raw", f"/api/v1/namespaces/{namespace}/pods/{pod}:{POD_PORT}/proxy/metrics"))
        for pod in pods}


def summarize(win: dict[str, Series], pods_at_start: int, pods_at_end: int,
              lost_pods: list[str] | None = None) -> dict:
    """The evidence file: per route, the window's buckets, count, mean and
    interpolated p50/p95/p99 in milliseconds."""
    routes = {}
    for key, s in sorted(win.items()):
        if s.count <= 0:
            continue
        routes[key] = {
            "count": s.count,
            "mean_ms": 1000.0 * s.sum / s.count,
            "p50_ms": 1000.0 * (quantile(s, 0.50) or 0.0),
            "p95_ms": 1000.0 * (quantile(s, 0.95) or 0.0),
            "p99_ms": 1000.0 * (quantile(s, 0.99) or 0.0),
            "buckets": {("+Inf" if math.isinf(le) else str(le)): c for le, c in sorted(s.buckets.items())},
        }
    return {
        "metric": METRIC,
        "labels": ["method", "route"],
        "note": "route-level; no tenant label, so no per-tenant fairness from this source. "
                "Buckets are the scored window (end minus start), summed over every "
                "control-plane pod present at each scrape.",
        "pods_at_start": pods_at_start,
        "pods_at_end": pods_at_end,
        "pods_lost_in_window": list(lost_pods or []),
        "routes": routes,
    }


def dumps(doc: dict) -> str:
    return json.dumps(doc, indent=2, sort_keys=True)
