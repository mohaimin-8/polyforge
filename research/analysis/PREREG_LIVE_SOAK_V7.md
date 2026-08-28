# Pre-registration: live CRUD-plane soak, attempt 9 — naming the host stall

Drafted 2026-08-28 (session 41). **Committed and pushed before the run starts**;
the push event is the timestamp anchor. This scores new live measurement, so
R1/R2 apply in full.

## What attempt 8 actually delivered

`PREREG_LIVE_SOAK_V6` asked one question: does the host freeze, or was six
sittings' worth of blame misattributed? Attempt 8 answered it **by
measurement**, which is what V6 was built to do and what no earlier apparatus
could:

| instrument | attempt 8's worst reading | sampling interval |
|---|---:|---:|
| `sample_gap_s` | **54 s** at 2026-08-28T13:28:13Z | 20 s |
| `host_write_ms` | **1412 ms** at 2026-08-28T15:17:18Z | — |

The run aborted at **8 h 41 m 43 s** on **68 dropped iterations** — k6's
`abortOnFail` threshold firing, SK-H4 FAIL, the fifth in a row — one minute
after that 1412 ms host write. 9,330,574 requests were delivered at
**0.0018% failed** (168 of 9.33 M) before it stopped.

So V6's host-freeze branch is the one that fired: **the host stopped scheduling
the observer for 54 seconds, and a small write to the Windows volume took
1.4 seconds.** That is no longer inferred from checkpoint timing and pod
restarts. It is measured, host-side, by an instrument that does not depend on
`kubectl` being able to answer.

## Why there is a ninth sitting, when V6 said there would not be

V6 closed with: *"There is no ninth attempt: after this run the host either
froze or it did not, and both answers end the investigation on this hardware."*

**That clause is not being quietly set aside, and this prereg exists to say so
before any data is seen.** It was written against a real failure mode — six
sittings that each ended by reaching for the next explanation — and the
discipline behind it is correct.

But V6's clause rests on a premise that attempt 8 has now shown to be
incomplete. It assumed "the host froze" is a single answer. It is not. A 54 s
scheduling gap and a 1.4 s volume write are the *signature* of a stall, and at
least four different host subsystems produce that same signature:

- Volume Shadow Copy freezing the volume for a snapshot,
- the storage stack resetting or retrying under the VHDX,
- a processor power-state or power-source transition,
- Modern Standby entry.

**V6's instruments cannot tell these apart, and the conclusion the thesis wants
to draw from them — "this machine cannot host the sitting; use different
hardware" — is a different claim for each one.** Two of the four are properties
of *this box's configuration* and would follow the work to any Windows laptop;
the other two are properties of the machine.

There is direct evidence that this ambiguity is not hypothetical. The Windows
System log for attempt 8's window contains **Volsnap event 36 at
2026-08-28T13:42:25Z** — *"The shadow copies of volume C: were aborted because
the shadow copy storage could not grow due to a user imposed limit"* — sitting
**inside attempt 8's worst gap cluster** (54 s at 13:28:13Z, 46 s at 13:40:10Z,
47 s at 13:44:45Z). The same log holds **27 `Kernel-Power` power-source-change
events in twelve hours**, one pair at 13:27:15Z, roughly sixty seconds before
that 54 s gap.

**This is exactly the weakness V6 was written to remove, reappearing one level
up.** Attempt 7's Volsnap 36 was found in the host log after the run and V6
called that circumstantial. Attempt 8's Volsnap 36 was *also* found in the host
log after the run. The apparatus still cannot see the host's own account of
itself while running — it can only see that something stopped it.

## The changed factor: the observer names the event, live

**One factor, and it changes the instruments only — not the system under
test.** This is the same class of change V6 made, for the same reason.

`scripts/soak_observer.sh` now starts `scripts/host_event_tail.ps1` as a child
for the life of the sitting. It polls the Windows System log every 60 s for the
providers that can produce a stall signature — `volsnap`, `VSS`, `disk`,
`Ntfs`, `volmgr`, `storahci`, `stornvme`, `Kernel-Power`, `Kernel-Boot`,
`EventLog` — and appends each new event to `host_events.csv` in the evidence
directory as it lands: UTC timestamp, epoch, record id, provider, event id,
level, message.

A stall row in `observer.csv` can then be joined to a **named** host event by
timestamp, during the run, instead of being reconstructed from a post-hoc trawl
that the reviewer has to take on trust.

**Cost discipline, stated because the instrument measures a load-sensitive
property.** One long-lived process for the whole sitting, not one launch per
sample; a 60 s poll against the event log's own index; no cluster contact of
any kind. The observer's 20 s sampling loop is untouched.

**Nothing else changes.** Duration, cell, fault schedule and offsets,
hypotheses, margins, thresholds, the VU pool and every PostgreSQL setting carry
over from V6 — which carried them from V5, V4 and V3 — unchanged.

**`dropped_iterations` remains absolute, cumulative and abort-on-fail. The
threshold is NOT relaxed.** It has held since V2 and it holds here. It has now
failed five sittings and it is still not being moved; a gate that is widened
after it fails is not a gate.

## What this does not fix, stated before the run

**This does not make a host stall less likely, and it is not a mitigation
test.** No mitigation is applied and no host setting is changed.

The operator confirms, and it is recorded here before the run rather than
reconstructed afterwards, that **nothing on the host was altered between
attempt 8 and attempt 9.** Docker Desktop was deliberately shut down by the
operator and restarted; that is the only intervening event. VSS shadow-copy
storage, System Restore, the power profile, the antivirus configuration and the
startup set are all as they were during attempt 8.

Attempt 9 is therefore expected to fail SK-H4 again if the host stalls again.
**Attempt 9 is not an attempt to pass. It is an attempt to name.**

## Disclosure: attempt 8b is VOID and is not a sitting

A run was started at 2026-08-28T15:22:13Z under V6 and ended at approximately
15:27Z — about five minutes — when the operator **deliberately shut down Docker
Desktop**. It produced 2.1 KB of observer samples, no k6 summary and no export.

Per the roadmap's own rule for an interrupted sitting, it is **VOID and
disclosed, never spliced**. It is not scored, not reported as an attempt
outcome, and its evidence directory is retained only as the record that it
happened. Attempt 9 is a fresh sitting from T0, on a cluster the harness
destroys and rebuilds (`kind delete` precedes `kind create` in the plan), so no
state carries across.

## Hypotheses, design and stopping rule

SK-H1 through SK-H7 are inherited **verbatim** from V6, which inherited them
from V5: SK-H1 (recovery on 60 s buckets), SK-H2 (audit continuity as a
healthy-vs-degraded difference), SK-H3 (hourly `crud_p95` at or below
8.0072 ms), SK-H4 (validity, absolute), SK-H6 (load distribution), SK-H7
(client latency, descriptive). Cell `crud_bursty` / `uniform` / `medium`,
CRUD-only, shared PostgreSQL, dual export at 3600 s and 60 s. Eight faults at
T+2/5/8/11/14/17/20/23 h, alternating planner scale-to-zero and CPU starvation.

**One hypothesis is added, and it is descriptive, not gating** — declared here
before any data, in the same register as SK-H7:

> **SK-H8 (descriptive).** Every `sample_gap_s` excursion above 2× the
> sampling interval (i.e. > 40 s) coincides, within ±120 s, with a named host
> event in `host_events.csv`. Reported as a count of matched and unmatched
> excursions with the providers involved. **It gates nothing** — it cannot
> pass or fail the sitting, and no other hypothesis's outcome depends on it.

The V5/V6 disclosures carry unchanged, including that `crud_p95`/`crud_p99`
measure **CPU service time and not service latency** (`internal/platform/replay.go`
stops its clock before the telemetry write, pinned by
`replay_latency_semantics_test.go`), that SK-H3's margin is 0.003 ms with no
headroom, and that `fsync=off` on the eval store is correct for a disposable
measurement database and would be indefensible in a deployment.

**Duration reading.** The design is "12 h minimum, 24 h target". A run that
clears 12 h but not 24 h is reported at the length it reached and is **not**
described as a completed sitting. Attempt 8 reached 8 h 41 m and therefore
missed the minimum by over three hours; that is recorded as a miss, not
softened.

## Outcome handling — exhaustive, and binding

The soak runs **once** under this prereg. No hypothesis, threshold, fault time,
duration or margin is altered after the first result is seen.

- **A stall recurs and `host_events.csv` names an event in its window** → the
  cause is **identified**, not merely measured. SK-H4 fails, that failure is
  reported as the honest outcome, and the limitation becomes a named,
  attributable property — "VSS snapshot activity on C: stalls the volume", or
  "the power source transitions", or whichever provider the log actually shows.
  This is the branch that lets the thesis state a *reason* rather than a
  symptom, and it is the branch that makes the "different hardware" conclusion
  checkable by a reviewer instead of trusted.
- **A stall recurs and `host_events.csv` is silent across its window** → none
  of the ten instrumented subsystems is responsible. That is a real and useful
  finding and it must be reported as one: it would mean the stall originates
  somewhere this apparatus still cannot see, and the honest conclusion is that
  the cause remains **unidentified** — not that it is Volsnap because Volsnap
  is what previous sittings happened to notice.
- **The run completes 24 h** → the hypotheses are scored as written, and a
  failing hypothesis is the finding rather than a nuisance.

**In every branch this is the last sitting on this machine.** The distinction
that justified attempt 9 — measured versus named — does not admit a tenth:
after this run the stall is either attributed to a named host subsystem or it
is attributed to nothing, and neither answer is improved by sitting again on
unchanged hardware. A tenth sitting would require a *changed host*, and that is
a different experiment with a different prereg.

`reproduce.py` must re-derive every previously committed record byte-identically
after this work lands (R4). The new record joins the gate and does **not**
replace `RESULTS_LIVE_SOAK_V5.md`, nor the attempt-8 record written under V6,
both of which stand with their FAILs.
