# Pre-registration: live CRUD-plane soak, attempt 8 — measuring the stall

Drafted 2026-08-28 (session 40). **Committed and pushed before the run starts**;
the push event is the timestamp anchor. This scores new live measurement, so
R1/R2 apply in full.

## Why there is an eighth sitting, when V5 said there would not be

`PREREG_LIVE_SOAK_V5` closed with: *"In every branch, this is the last sitting on
this machine."* `RESULTS_LIVE_SOAK_V5.md` stands exactly as committed, including
its SK-H4 FAIL and the 11 h 47 m duration it actually reached.

**That rule is not being quietly set aside, and this prereg exists to say so
before any data is seen.** V5's rule was written against a specific thing:
trying yet another PostgreSQL setting after `fsync=off` had exhausted the
storage-durability levers. That reasoning was correct and it still holds — no
database setting changes here.

But V5 also wrote this, in its own words:

> **What this does not fix, stated before the run.** The stall's cause is still
> not *proven* — it is inferred from: telemetry ingestion collapsing before each
> excursion, checkpoints spanning the stall windows, zero pod restarts, node
> memory never above 22%, and a server-side service time that never moved while
> clients waited seconds.

Attempt 7 then produced a **51.4 s stall**, 1,369 dropped iterations, and an
SK-H4 FAIL — and the corroborating evidence for the host-freeze explanation was
a Windows **Volsnap event 36** found in the host event log *after* the run.
Every instrument the harness carried was pointed at the cluster. **Not one of
them could observe the host.** The conclusion "different hardware" is therefore
being drawn from an inference that this apparatus has never been able to test.

Running an experiment that can measure the thing it has only inferred is not
repetition. It is the step that was missing.

## The changed factor: the harness can now observe the host

**One factor, and it changes the instruments only — not the system under test.**
`scripts/soak_observer.sh` gains two columns, appended to `observer.csv`:

| column | what it measures | why it settles the question |
|---|---|---|
| `sample_gap_s` | wall-clock seconds since the previous sample | the loop sleeps a fixed interval, so a gap far above it means **this process was not scheduled**. A 51 s host freeze appears as a ~51 s excess. |
| `host_write_ms` | latency of one small write to the Windows volume | a VSS/Volsnap freeze blocks exactly this; CPU contention does not. |

Both are host-side and independent of the cluster, so they still record a freeze
in the case that has defeated every previous sitting: when `kubectl` is itself
the thing that is blocked.

**Nothing else changes.** Duration, cell, fault schedule and offsets,
hypotheses, margins, thresholds, the VU pool and every PostgreSQL setting carry
over from V5 unchanged. In particular:

**`dropped_iterations` remains absolute, cumulative and abort-on-fail. The
threshold is NOT relaxed.** It has held since V2 and it holds here. A 51 s
freeze will fail SK-H4 again, and that is the intended behaviour — the purpose
of this sitting is to learn *what* caused the failure, not to make the gate
survivable. Relaxing SK-H4 now, having seen attempt 7 fail it, is precisely the
move these pre-registrations exist to prevent.

## What this does not fix, stated before the run

**This does not make a host freeze less likely.** No mitigation is applied. VSS
and System Restore cannot be reconfigured from this harness — `vssadmin`
requires elevation and this environment cannot answer an interactive UAC prompt
— and no mitigation was substituted in its place, because an unmeasured
mitigation layered on an unproven cause is how attempts 5 through 7 each
consumed a day.

So attempt 8 is expected to fail SK-H4 if the host freezes again. What it will
no longer do is fail without evidence.

## Hypotheses, design and stopping rule

Inherited verbatim from V5, which inherited them from V4 and V3: SK-H1
(recovery on 60 s buckets), SK-H2 (audit continuity as a healthy-vs-degraded
difference), SK-H3 (hourly `crud_p95` at or below 8.0072 ms), SK-H4 (validity,
absolute), SK-H6 (load distribution), SK-H7 (client latency, descriptive). Cell
`crud_bursty` / `uniform` / `medium`, CRUD-only, shared PostgreSQL, dual export
at 3600 s and 60 s. Eight faults at T+2/5/8/11/14/17/20/23 h, alternating
planner scale-to-zero and CPU starvation.

The V5 disclosures carry unchanged, including that `crud_p95`/`crud_p99` measure
CPU service time and not service latency, that SK-H3's margin is 0.003 ms with
no headroom, and that `fsync=off` on the eval store is correct for a disposable
measurement database and would be indefensible in a deployment.

**Duration reading.** The design is "12 h minimum, 24 h target". A run that
clears 12 h but not 24 h is reported at the length it reached and is **not**
described as a completed sitting. Attempt 7 reached 11 h 47 m and therefore
missed even the minimum, by 13 minutes; that is recorded as a miss, not rounded
up.

## Outcome handling — exhaustive, and binding

The soak runs **once** under this prereg. No hypothesis, threshold, fault time,
duration or margin is altered after the first result is seen.

- **A stall recurs and `sample_gap_s` / `host_write_ms` show the host froze** →
  the host-freeze hypothesis is **CONFIRMED by measurement** rather than
  inferred. SK-H4 fails, that failure is reported as the honest outcome, and the
  limitation becomes a measured property of the machine. This apparatus is then
  genuinely finished and the V4/V5 conclusion — different hardware — is at last
  supported by direct evidence instead of a circumstantial event-log entry.
- **A stall recurs and the host probes stay flat** → the host-freeze hypothesis
  is **FALSIFIED**. That is a real and useful finding: it would mean six
  sittings were attributed to the wrong cause, and the record must say exactly
  that rather than reaching for the next explanation.
- **The run completes 24 h** → the hypotheses are scored as written, and a
  failing hypothesis is the finding rather than a nuisance.

**In every branch this is the last sitting on this machine, and unlike V5 that
statement now has an instrument behind it.** There is no ninth attempt: after
this run the host either froze or it did not, and both answers end the
investigation on this hardware.

`reproduce.py` must re-derive every previously committed record byte-identically
after this work lands (R4). The new record joins the gate and does **not**
replace `RESULTS_LIVE_SOAK_V5.md`, which stands with its FAIL.
