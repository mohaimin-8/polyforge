# L6: the `/metrics` scrape, verified against a live server

**This is an instrument check, not a campaign.** No pre-registration, no
hypothesis, no arm comparison — and the numbers below must not be quoted as a
result. What it establishes is that `scripts/soak_observer.sh`'s end-to-end
latency scrape works against a real server's real exposition output, which
session 42 could only check offline against a synthesized payload.

Run 2026-09-05 (session 43), `verify_scrape.sh` in this directory.

## Method

`cmd/control-plane` built and run on this host (SQLite store, no cluster, no
Postgres, no concurrency — one request at a time from `curl`). 180 POSTs to
`/v1/tenants/l6/workloads/replay`, 60 before the observer started and 120
during it, with `METRICS_URL` pointed at the server and the observer sampling
every 2 s.

## What it establishes

**The scrape works.** `observer.csv` reaches `http_dur_sum_s=0.239796`,
`http_dur_count=180` — exactly the number of requests sent — and
`metrics_http_duration.csv` holds the full bucket rows a percentile actually
needs. Both were empty in every sitting through attempt 10 because nothing
ever scraped `/metrics`.

**The W5 gap has a measurement now.** For the 172 requests that were served
(the other 8 were rate-limited; see below), the two clocks disagree:

| metric | mean | p95 |
|---|---:|---:|
| CPU service time — what every live record's `crud_p95` measures | 1.1496 ms | 1.4105 ms |
| end-to-end, telemetry write included — what the name implies | 1.3911 ms | 4.7778 ms |
| **gap** | **+21.0%** | **+238.7%** |

`internal/platform/replay.go` stops its clock at line 76; the telemetry write
happens at line 94. The mean gap is the write; the p95 gap is the write's own
tail, and it more than triples the percentile. Both counts are 172, so this
compares the same requests, not two different populations.

**It found a security defect.** 8 of the 180 requests were rate-limited, and
those 429s were recorded under `route="/v1/tenants/l6/workloads/replay"` — the
raw path — beside the 172 under `route="/v1/tenants/{tenant_id}/workloads/
replay"`. The route label keys two unbounded maps in the metrics registry, one
allocating a histogram per key, so any caller could mint permanent series by
tripping the rate limiter or requesting a 404. Fixed in `server.go`
(`routePattern` now resolves the pattern through the mux and falls back to the
constant `unmatched`) with a cap in `metrics.go` behind it. See
`docs/SECURITY.md`.

## What it does NOT establish

- **Nothing about the published sittings.** The histogram was never scraped
  through attempt 10, the pods are gone, and V8 pre-committed attempt 10 as the
  last on this machine. SK-H3's FAIL stands as scored on the CPU-time metric.
- **Nothing transferable about the magnitude.** 180 requests, one host, SQLite,
  no concurrency, no cluster. A 24 h sitting on PostgreSQL under 300 rps has a
  different write path and a different tail. The *sign* is structural — the
  end-to-end clock cannot be smaller — but the size is not this number.
- **Nothing about the NodePort path.** The scrape ran against `localhost:8080`
  directly. Against a kind cluster it goes over the NodePort, which is
  unexercised here.
- The p95 is bucket-interpolated (`histogram_quantile`'s method), so it carries
  half a bucket of quantisation.

## Files

| file | what it is |
|---|---|
| `verify_scrape.sh` | the run: build, load, observe, stop |
| `observer.csv` | 54 samples; the last two columns are the scrape |
| `metrics_http_duration.csv` | every bucket row per sample, both route labels |

Both CSVs are trimmed to the ~5 minutes around the load. The observer was left
running afterwards and idled for hours against a stopped server, which is
itself a demonstration of the "fails to empty rather than stopping" rule — but
those rows carry no information and are not kept.
