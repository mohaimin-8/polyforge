# Pre-registration: live CRUD-plane soak, attempt 10 — the first valid V7 sitting

Drafted 2026-08-29 (session 41). **Committed and pushed before the run starts**;
the push event is the timestamp anchor. This scores new live measurement, so
R1/R2 apply in full.

## Why there is a tenth sitting when V7 said there would not be

`PREREG_LIVE_SOAK_V7` closed with: *"In every branch this is the last sitting on
this machine … A tenth sitting would require a changed host, and that is a
different experiment with a different prereg."*

**That clause is not being set aside, because attempt 9 was never a sitting.**

Attempt 9 ran with an **unregistered second fault injector** on the same
cluster. A launcher for the abandoned attempt 8b (`launch_soak8.sh`, retained
as `.DISABLED`) was executed at 2026-08-28T17:59:03Z, 2 h 13 m into the run. It
began with `rm -f .observer.lock .controls.lock`, which deleted the
single-instance guard, then backgrounded a second `soak_observer.sh` and a
second `stage_b_controls.sh` carrying the full 24 h schedule, then started a
harness runner that died six seconds later on a DuckDB file lock held by the
live run. **The runner died; its two backgrounded children did not.** They ran
for 13 h 42 m.

The cluster under measurement therefore received **nine** fault injections, not
five:

| time (UTC) | fault | source |
|---|---|---|
| 17:51:07 | planner scale-to-zero | registered |
| 20:00:40 | planner scale-to-zero | **unregistered** |
| 20:49:36 | CPU starvation | registered |
| 22:59:07 | CPU starvation | **unregistered** |
| 23:51:07 | planner scale-to-zero | registered |
| 02:00:39 | planner scale-to-zero | **unregistered** |
| 02:49:35 | CPU starvation | registered |
| 04:59:06 | CPU starvation | **unregistered** |
| 05:51:07 | planner scale-to-zero | registered |

No two injections overlapped, and instrumentation load was doubled throughout
by the second observer and its second host-event tail.

**A run whose system under test was altered by an unregistered process is VOID,
not failed.** The roadmap's own contingency says so: *"Run VOID. Disclose,
re-sit."* `RESULTS_LIVE_SOAK_V7.md` will record attempt 9 in exactly those
terms — its 1,081 dropped iterations are **not** attributed to the system under
test, its evidence is retained, and it is never spliced into anything.

V7's design was therefore never given a valid trial. Attempt 10 is not a tenth
attempt at a question already answered nine times; it is **the first sitting
under V7's apparatus that the apparatus actually governs**.

## The changed factor: a guard a launcher cannot delete

**One factor, and it is the harness's own integrity, not the system under
test.**

`scripts/soak_observer.sh` and `scripts/stage_b_controls.sh` each now maintain
a **liveness heartbeat** — `.observer.heartbeat` / `.controls.heartbeat` — that
is refreshed every sampling loop and, in the injector, throughout the multi-hour
waits between faults. A starting instance refuses (exit 3) if the heartbeat is
under 120 s old. The lock files remain, but they are no longer the only
defence: the heartbeat is a file the offending launcher does not know about and
does not delete.

Verified against the exact failure before this run: with `.controls.lock`
deleted and a fresh heartbeat present, a second injector **refuses to start**;
with a stale heartbeat, a legitimate restart proceeds.

The launcher is also repaired. It no longer `exec`s the runner, so its trap can
fire, and on any exit it kills the observer, the injector, and the observer's
PowerShell event-tail child. **The instruments must not survive the run they
instrument.**

**Nothing about the system under test changes.** Duration, cell, fault schedule
and offsets, hypotheses, margins, thresholds, the VU pool and every PostgreSQL
setting carry over from V7 — which carried them from V6, V5, V4 and V3 —
unchanged. `dropped_iterations` remains absolute, cumulative and abort-on-fail.
**The threshold is NOT relaxed.**

SK-H8, V7's descriptive host-event hypothesis, carries over verbatim, as does
the `host_event_tail.ps1` instrument that serves it.

## What this does not fix, stated before the run

**The host is unchanged and no mitigation is applied.** Attempt 9 recorded five
`Kernel-Power` "Power source change" events in 14 h 33 m on a laptop plugged in
at full charge, two of them under load, and its collapse began six seconds
after the last one. Nothing about that has been altered — the charger, dock,
battery thresholds and power profile are as they were.

**Attempt 10 is therefore expected to fail SK-H4 if the host stalls again, and
that outcome stands as the finding.** This sitting exists to obtain a *valid*
trial of V7's design, not to obtain a pass.

**A second disclosure, recorded before the data.** Attempt 9's evidence is
compromised as a measurement of the system under test but its *host* record is
not: the host-event tail and the observer's own probes were unaffected by the
rogue injector. Where attempt 10 corroborates or contradicts attempt 9's
power-transition association, both readings will be reported.

## Hypotheses, design and stopping rule

SK-H1 through SK-H8 are inherited **verbatim** from V7: SK-H1 (recovery on 60 s
buckets), SK-H2 (audit continuity as a healthy-vs-degraded difference), SK-H3
(hourly `crud_p95` at or below 8.0072 ms), SK-H4 (validity, absolute), SK-H6
(load distribution), SK-H7 (client latency, descriptive), SK-H8 (named host
events, descriptive and non-gating). Cell `crud_bursty` / `uniform` / `medium`,
CRUD-only, shared PostgreSQL, dual export at 3600 s and 60 s. Eight faults at
T+2/5/8/11/14/17/20/23 h, alternating planner scale-to-zero and CPU starvation.

The V5/V6/V7 disclosures carry unchanged, including that `crud_p95`/`crud_p99`
measure **CPU service time and not service latency**, that SK-H3's margin is
0.003 ms with no headroom, and that `fsync=off` on the eval store is correct for
a disposable measurement database and indefensible in a deployment.

**Duration reading.** "12 h minimum, 24 h target." A run that clears 12 h but
not 24 h is reported at the length it reached and is **not** described as a
completed sitting.

**Two further readings are registered here because attempt 9 raised them.**

- V7's record must state that `sample_gap_s` is **blind to a guest-only stall**:
  attempt 9's fatal window showed the observer scheduled normally while the
  cluster stopped answering. No new instrument is added for it here — adding one
  would be a second changed factor — and the limitation is carried openly.
- The audit-window deviation seen once in attempt 9 (healthy +72 / degraded
  +80, i.e. ten control cycles against nine) is **not** treated as a failure
  signature in advance. If it recurs it is reported as a property of the
  measurement window, not of audit continuity, unless the degraded window comes
  in **below** the healthy one — which is what a continuity failure looks like.

## Outcome handling — exhaustive, and binding

The soak runs **once** under this prereg. No hypothesis, threshold, fault time,
duration or margin is altered after the first result is seen.

- **The run completes 24 h** → the hypotheses are scored as written, and a
  failing hypothesis is the finding rather than a nuisance.
- **A stall recurs and `host_events.csv` names an event in its window** → the
  attempt-9 association is corroborated on a clean apparatus, which is what it
  has never had. SK-H4 fails and that failure is the honest outcome.
- **A stall recurs and the host log is silent** → attempt 9's association is
  weakened, and the record says so rather than preferring the earlier reading.
- **The apparatus is compromised again in any way** → VOID again, disclosed
  again, and the sitting is not reported as a result.

**This is the last sitting on this machine in every branch**, and unlike V7 that
statement now rests on a guard that has been tested against the failure that
broke it. A further sitting would require a *changed host* — and the change now
has an address.

`reproduce.py` must re-derive every previously committed record byte-identically
after this work lands (R4). The new record joins the gate and does **not**
replace `RESULTS_LIVE_SOAK_V5.md` or `RESULTS_LIVE_SOAK_V7.md`, both of which
stand with their outcomes.
