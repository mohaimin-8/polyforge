# PolyForge Service-Level Objectives

Roadmap W12 deliverable. Objectives are stated against the metrics the
control plane actually exports today (`/metrics`, Prometheus text format);
the AI-gateway SLOs are pre-registered so the service is built against a
target rather than assigned one retroactively.

## Objectives

| Service | SLI | Objective | Window |
|---|---|---|---|
| control-plane | availability (non-5xx / total) | ≥ 99.9% | 30 days rolling |
| control-plane | p99 request latency | < 200 ms | 30 days rolling |
| ai-gateway (future, M5) | availability | ≥ 99.5% | 30 days rolling |
| ai-gateway (future, M5) | p95 time-to-first-token | < 800 ms | 30 days rolling |

429 responses are *not* SLO violations: rate limiting a misbehaving tenant is
the platform working as designed. Only 5xx counts against availability.

## SLI expressions

Availability (control-plane):

```promql
1 - (
  sum(rate(polyforge_http_requests_total{status=~"5.."}[5m]))
  /
  sum(rate(polyforge_http_requests_total[5m]))
)
```

p99 latency:

```promql
histogram_quantile(0.99,
  sum by (le) (rate(polyforge_http_request_duration_seconds_bucket[5m])))
```

Saturation (context for incident triage, no SLO of its own):

```promql
polyforge_http_in_flight_requests
```

## Error budget

At 99.9% over 30 days the control plane may serve **0.1%** of requests as
5xx — 43.2 minutes of full outage, or proportionally longer partial
degradation. The budget is a spending account: planned risky work (schema
migrations, dependency bumps) should be scheduled while budget remains, and
frozen when it is exhausted.

## Burn-rate alerts (Google SRE workbook formula)

A burn rate of 1 consumes exactly the budget over the window. Alert on fast
and slow burns, both requiring a short and a long window to avoid flapping:

| Alert | Burn rate | Windows | Budget consumed at trigger |
|---|---|---|---|
| page | ≥ 14.4 | 5 min AND 1 h | 2% of 30-day budget in 1 h |
| ticket | ≥ 6 | 30 min AND 6 h | 5% of 30-day budget in 6 h |

```promql
# page: fast burn
(
  sum(rate(polyforge_http_requests_total{status=~"5.."}[5m]))
    / sum(rate(polyforge_http_requests_total[5m])) > 14.4 * 0.001
) and (
  sum(rate(polyforge_http_requests_total{status=~"5.."}[1h]))
    / sum(rate(polyforge_http_requests_total[1h])) > 14.4 * 0.001
)
```

The ticket alert is the same expression with `6 * 0.001` and 30 m/6 h windows.

## Status of verification

Prometheus + Grafana run locally via `docker compose --profile observability
up`; alert rules are **not yet loaded** into Prometheus, and the fake-outage
drill (kill a pod, alert within 5 minutes) requires the kind cluster, which
is blocked on Docker availability on the dev machine. Until that drill runs,
these SLOs are definitions, not demonstrated capabilities.
