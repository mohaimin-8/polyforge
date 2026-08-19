# Stage A — 10-minute smoke: PASS (WP14 Phase 6)

Run 2026-08-19, 812 s wall, `ok: true`, `failed_runs: 0`. The first time the
Phase 1-5 changes touched a real cluster.

Stage A does not measure anything. It proves every instrument the 24 h sitting
depends on actually emits — in fifteen minutes rather than at hour 24, which
is the whole reason four previous attempts each cost a day.

| # | criterion | result |
|---|---|---|
| 1 | delivery | **0.0000% failed** (0 of 179,486), **0 dropped** |
| 2 | load distribution | **7.11% hottest** of 16 pods (fair share 6.25%, ceiling 25%) |
| 3 | buckets | **11 emitted**, all non-empty |
| 4 | audit stream | **494 records** |
| 5 | k6 live log | written during the run, not after |
| 6 | OOM kills | **zero** |

## The load distribution is the result that matters

```
7.11% 7.10% 7.02% 6.75% 6.59% 6.44% 6.38% 6.28%
6.27% 6.26% 6.15% 6.10% 5.85% 5.46% 5.25% 5.01%
```

Sixteen pods between 5.01% and 7.11% against a 6.25% fair share. Attempt 4's
pinned pod carried roughly 90% while fifteen replicas sat idle, and nothing in
four attempts ever checked. kube-proxy is balancing in the kernel; the
mechanism that invalidated four sittings is gone.

## Two numbers worth reading closely

**`crud_p99 = 8.0073 ms` against B2's committed `8.0072 ms`.** One
ten-thousandth of a millisecond apart, over a completely rebuilt load path.
Independent confirmation that replacing the load path did not perturb what is
being measured, and that attempt 4's Postgres-rescued figures were sound.

**Client p95 13.5 ms vs server p95 2.05 ms** — a 6.6x gap, which is the
network and NodePort hop. Attempt 4's gap through the dying port-forward was
**464x**.

## What Stage A does NOT establish

`mean_violation` is `0.00e+00` in every bucket. SK-H1's saturation problem is
untouched — a 375 ms target against an 8 ms p99 leaves the metric no range,
which is why Phase 3 re-registered SK-H1 on p95 deviation. Stage B's latency
fault is where that gets proven, by making the hypothesis FAIL on demand.

Nothing here says anything about 24 h behaviour. That is Stage C's job.

`k6-live.log` is trimmed to its first 60 KB and last 20 KB; the middle is
k6's repeating progress output.
