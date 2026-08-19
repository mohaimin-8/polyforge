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

---

# Correction (10:12 local, T+18 h 22 m): the attribution above is wrong

The section above concluded that observer contention caused the restarts and
that the run would recover once local work stopped. **The first half is at
best partial and the second half is false.** Restarts continued after all
observer compute ceased, reaching 40 by 10:10, and the pattern is not what
contention produces.

| # | time | reason | gap |
|---:|---|---|---:|
| 25 | 09:40:51 | process exited | 169 s |
| 26 | 09:41:59 | health probe failed | 68 s |
| 27 | 09:44:41 | health probe failed | 162 s |
| 28 | 09:46:03 | health probe failed | 82 s |
| … | | | |
| 39 | 10:08:54 | process exited | 168 s |
| 40 | 10:10:01 | health probe failed | 67 s |

The intervals alternate with machine regularity — roughly 70–80 s, then
150–175 s, repeating without drift for forty minutes. Resource contention
from a compiler is bursty and stops when the compiler stops. This is a
**stable oscillation**, and it has been running since ~09:32 while the
observer did nothing heavier than two `max(id)` index lookups every half
hour, an activity far too infrequent to explain restarts every two minutes.

What appears to have happened is that the builds *triggered* an initial
failure — the correlation across two separate windows is still strong, and
15 h 33 m of prior silence is still unexplained by anything else — but the
port-forward then entered a self-sustaining failure mode that no longer
depends on the trigger. `kubectl port-forward` is a userspace proxy carrying
~300 req/s from k6 through a single pinned pod; the alternation between
"process exited" and "health probe failed" is consistent with it repeatedly
saturating and being torn down, not with external CPU starvation.

**Being accurate here matters more than accepting blame.** Over-attributing
the failure to the observer is as much an error as missing the observer's
role: it would send the next investigation after a protocol rule about local
builds when the actual defect is a fragile load path that the harness has
never stress-tested at this duration. Both belong in the record. Only one of
them is the thing that will break the next run.

**No intervention.** The supervisor is already doing the only available
remedy — restarting the forward — roughly every two minutes. Manually
touching it would add an undisclosed, unregistered action to a live run
without offering anything the supervisor is not already attempting.

**Expected consequence.** At ~1 restart per 2 minutes with 5.5 h remaining,
another ~165 restarts are likely. Each drops k6 connections in flight, so
SK-H4's `http_req_failed < 1%` gate is now the run's most probable failure
point, and the delivery-path defect the whole of attempt 3 and 4 was meant to
fix is not fixed — it was merely dormant for 15 hours.
