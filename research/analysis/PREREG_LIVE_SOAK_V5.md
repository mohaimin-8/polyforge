# Pre-registration: live CRUD-plane soak, attempt 7 — one changed factor

Drafted 2026-08-26 (session 39). **Committed and pushed before the run
starts**; the push event is the timestamp anchor. This scores new live
measurement, so R1/R2 apply in full.

## Why a seventh sitting, when V4 said not to

`PREREG_LIVE_SOAK_V4` ended with: *"the next step is different hardware — not a
seventh sitting on this one."* `RESULTS_LIVE_SOAK_V4.md` stands exactly as
committed, including that sentence and its INVALID verdict.

**That instruction was right about the mechanism and wrong about the options.**
It assumed the only remaining lever was hardware. It was not: the eval
PostgreSQL was still running with `fsync` enabled. `synchronous_commit=off`
stops *commits* waiting on the WAL, but **checkpoints still fsync the data
files**, and a checkpoint fsync against an `emptyDir` on a VHDX under WSL2 is
exactly the stall that has ended every recent sitting. Concluding "different
hardware" while an untried setting sat in the manifest was premature, and this
prereg says so rather than quietly re-running.

Two other options were checked first and rejected on evidence, not taste:

- **The registered `small`-cell fallback does not apply.** The original prereg
  allows dropping the cell if the machine cannot hold `medium` — "duration
  outranks width". Measured: `small` and `medium` produce **identical** demand
  (299.8 mean / 496.9 peak req/s); cluster size changes replica limits, not
  request rate. A smaller cell would not reduce telemetry write volume by one
  row, so it cannot address a write stall. It would only tighten the replica
  ceiling and make violation worse.
- **Reducing the request rate or sampling telemetry** would change the measured
  object. Not available without changing what the sitting claims.

## The changed factor: the eval database stops calling fsync

**One factor.** `fsync=off` and `full_page_writes=off` on the eval PostgreSQL.
Nothing else changes — duration, cell, fault schedule, hypotheses, margins,
thresholds and the VU pool all carry over from V4 unchanged.

`fsync=off` removes every fsync in the engine, including the checkpoint fsyncs
`synchronous_commit=off` does not touch. `full_page_writes=off` cuts WAL volume
for the same reason.

**This is correct here and would be indefensible in a deployment.** The eval
store exists to be measured and is deleted with the cluster; the cost of
`fsync=off` is that an unclean stop corrupts the database, and a sitting whose
PostgreSQL died is already void, so nothing measurable is at risk. It is
disclosed here and in the record rather than buried in a manifest.

**`dropped_iterations` remains absolute, cumulative and abort-on-fail. The
threshold is NOT relaxed.** That has held since V2 and holds here. The entire
point of attacking the stall is that the gate can stay where it is.

## What this does not fix, stated before the run

The stall's cause is still not *proven* — it is inferred from: telemetry
ingestion collapsing before each excursion, checkpoints spanning the stall
windows, zero pod restarts, node memory never above 22%, and a server-side
service time that never moved while clients waited seconds. `fsync=off` tests
that inference directly. **If a multi-second stall recurs with fsync disabled,
the storage hypothesis is wrong** and the record must say so plainly rather
than reaching for the next setting.

## Ladder precondition

Stage B is re-run on the changed commit before the sitting. It is cheap
insurance against a specific failure: a malformed `-c` argument makes
PostgreSQL refuse to start, which would waste a day rather than an hour.

Stage C is carried forward from the V4 ladder with its reason stated: the
change is to database durability only, it can only reduce I/O latency, and
Stage C recorded zero stalls in four hours regardless.

## Hypotheses, design and stopping rule

Inherited verbatim from V4, which inherited them from V3: SK-H1 (recovery on
60 s buckets), SK-H2 (audit continuity as a healthy-vs-degraded difference),
SK-H3 (hourly `crud_p95` at or below 8.0072 ms), SK-H4 (validity, absolute),
SK-H6 (load distribution), SK-H7 (client latency, descriptive). Cell
`crud_bursty` / `uniform` / `medium`, CRUD-only, shared PostgreSQL, dual export
at 3600 s and 60 s. Eight faults at T+2/5/8/11/14/17/20/23 h, alternating
planner scale-to-zero and CPU starvation.

The V4 disclosures carry unchanged, including that `crud_p95`/`crud_p99`
measure CPU service time and not service latency, and that SK-H3's margin
against its threshold is 0.003 ms with no headroom.

**Duration reading, stated because attempt 6 came close.** The design is "12 h
minimum, 24 h target". Attempt 6 reached **10 h 44 m** and therefore missed
even the minimum, by 76 minutes. A run that clears 12 h but not 24 h is
reported at the length it reached and is **not** described as a completed
sitting.

## Outcome handling — the stopping rule, and this time it is exhaustive

The soak runs **once** under this prereg. No hypothesis, threshold, fault time,
duration or margin is altered after the first result is seen.

- **A multi-second stall recurs with fsync disabled** → the storage hypothesis
  is FALSIFIED. That is a real finding, it is reported as one, and the cause
  returns to unknown rather than being reassigned to whatever is convenient.
- **SK-H4 fails for any other reason** → reported, and this apparatus is done.
- **The run completes** → hypotheses are scored as written, and a failing
  hypothesis is the finding rather than a nuisance.

**In every branch, this is the last sitting on this machine.** `fsync=off` is
the final lever that targets the measured mechanism; there is no further
setting to try, and an eighth attempt would be repetition rather than
investigation. If this one does not complete, the honest conclusion is the one
V4 reached — different hardware — and it will then be supported by having
eliminated the storage hypothesis too.

`reproduce.py` must re-derive every previously committed record
byte-identically after this work lands (R4). The new record joins the gate and
does **not** replace `RESULTS_LIVE_SOAK_V4.md`.
