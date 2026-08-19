# Incident: the observer degraded the run it was watching (T+17 h 39 m)

Written 2026-08-19 09:35 local, while the soak was still running, by the
agent that caused it. Recorded now rather than reconstructed later because
the evidence is timestamped and the conclusion is unflattering — exactly the
combination that decays if left to a write-up.

## What happened

The port-forward supervisor restarted the forward **20 times**. All of them
fall inside two windows, and both windows are periods when this session was
running heavy local work on the same laptop.

| window (local) | restarts | what the observer was doing |
|---|---:|---|
| 15:50:27 → 07:23:23 | **0** | nothing; the run was untouched for 15 h 33 m |
| 07:23 – 07:24 | 3 | `export_soak_buckets.sh` and its test queries — full scans of a 3 GB, 17 M-row table |
| 09:17 → 09:31 | 17 | `go test ./internal/operator/controllers/` — compiling the operator package and its Kubernetes dependencies |

Fifteen and a half hours of zero restarts, then two clusters landing on two
bursts of observer CPU and I/O, with nothing else changed on the machine.
Correlational, not proven — but there is no competing explanation on offer.

Measured consequences:

* delivery probe fell to 7/10, with two `DELIVERY-FAILING` alerts (3
  consecutive failures at worst) against 1 failure in the preceding 199
  probes;
* telemetry ingestion dropped to **157 events/s against a nominal 298.7/s**,
  52.6% of steady state, still depressed after the local work stopped;
* container memory rose 5.24 → 5.96 GiB across the Go compile.

Postgres was checked and found **idle** — no long-running query, no
autovacuum in flight (last autovacuum 02:51 UTC, last autoanalyze 03:02 UTC).
So the sustained degradation is not the database still digesting the scans.

## Why this was a mistake, specifically

The justification for doing the RBAC work mid-run was that editing a Helm
chart cannot affect a cluster that is already deployed. That is true and
irrelevant. The file edits were harmless; **compiling and running the Go test
was not**. The soak has no CPU, memory or I/O isolation from the session
observing it — one laptop, one kernel, one disk.

The reasoning error was treating "does not touch Kubernetes objects" as
equivalent to "does not affect the run". Those are different claims, and only
the first one was checked. The same error, in milder form, produced the first
three restarts: the mid-run Postgres extraction was disclosed as adding *a
scan to the measured window*, which framed the cost as measurement noise when
it was actually resource contention.

## What was done about it

Nothing to the run. `PREREG_LIVE_SOAK_V2.md` §Outcome handling authorises an
early stop only when *"the delivery probe shows sustained failure in the
first hour"*; this is hour 17.7, and the same section states that instability
surfacing mid-soak is *"a result, not a nuisance: reported, fixed forward"*.
The run therefore continues to its 24 h target, and this document is the
report.

All observer compute stopped at 09:31. Monitoring for the remainder is
single `curl` calls and metadata reads only: no builds, no test runs, no
table scans.

## How this must be reported

**SK-H5 is now contaminated.** It was registered as descriptive — "how many
times did the supervisor restart the forward, when, and why" — and until
09:17 its answer was zero, which the prereg had already fixed as meaning the
attempt-2 port-forward diagnosis stays *unconfirmed*. The answer is now 20,
and **that number describes observer interference, not system behaviour**.
It is not evidence that the supervisor mechanism proved its worth, and the
results record must not read as though it were.

**If SK-H4 fails**, the prereg says a third delivery failure would make "the
local harness cannot sustain a 24 h live run on this machine" the finding
worth reporting. That framing would now be incomplete to the point of being
misleading. The harness sustained 15.5 hours untouched and degraded only
under load the *observer* added. The honest finding is narrower and more
useful: a single-laptop soak has no isolation from the people watching it,
and the experimental protocol needs a rule — no local builds or heavy queries
for the duration of a run — that it did not have.

That rule belongs in the next pre-registration.
