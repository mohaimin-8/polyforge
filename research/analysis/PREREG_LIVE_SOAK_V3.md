# Pre-registration: live CRUD-plane soak, attempt 5 (WP14, after a stopped retry loop)

Drafted 2026-08-23 (session 39). **Committed and pushed before Stage D
starts**; the push event is the timestamp anchor. This scores new live
measurement, so R1/R2 apply in full.

## This is not a re-sit under one changed factor, and will not be described as one

`PREREG_LIVE_SOAK_V2.md` was a re-sit under a single changed mechanism, and
said so carefully. This is not that. Attempt 4 ran its full 24 hours, fired
8 of 8 faults, and was rejected by the harness's own gate at 4.567% failed
requests — the **third** consecutive SK-H4 failure. V2's stopping rule fired
on exactly that condition:

> A third failure would make "the local harness cannot sustain a 24 h live run
> on this machine" itself the finding worth reporting, rather than something to
> keep retrying past.

That rule is honoured: the retry loop stopped, and `RESULTS_LIVE_SOAK_V2.md`
stands as committed with attempt 4 recorded INVALID. What follows is not
another retry of the same apparatus. It is a **rebuilt measurement apparatus**,
validated on a ladder before the sitting, registered under a new prereg because
the number of changed things makes any "one factor" claim false.

The honest summary of why: across four sittings the recurring failure was never
the controller. It was that **instruments reported answers they had not
measured**. Three of attempt 4's five hypotheses could not fail. Fixing that
required changing the load path, the exporter, two fault injectors, two
hypothesis definitions, and retiring a third fault entirely. Calling that "one
factor" would be the kind of framing this project exists not to do.

## What changed, itemised

Each row is a defect with evidence, not a tuning choice.

| # | Defect | Evidence it was real | Change |
|---|---|---|---|
| 1 | `kubectl port-forward` pinned all load to 1 pod of 16, which OOM-killed on its 256 Mi limit; 282 forward restarts | attempt 4: server-side p95 2.23 ms vs k6 1032 ms client-side, 464x | NodePort via kind `extraPortMappings`; kube-proxy balances in-kernel |
| 2 | Nothing ever checked that load reached more than one pod | attempt 4 ran 17 h pinned, undetected | `check_load_distribution` fails any run where one pod takes >25% or <80% of replicas serve |
| 3 | `eval-export` read every row into Go and OOM-killed the pod (exit 137) at ~952k events | reproduced at Stage B scale; a 24 h run is ~25M events | server-side PostgreSQL aggregation, pinned byte-identical to the old path by a mutation-checked parity test |
| 4 | The export's 5-minute ceiling fit a 25M-event run by ~4% | measured: 11.6 s per million, so ~289 s against 300 s | ceiling 30 min, `--timeout` flag, throughput floor gated at 30 s/million |
| 5 | `eval-export` emitted only whole-run scalars, so four sittings produced one row of numbers between them | attempt 4's buckets had to be hand-rescued from PostgreSQL before `kind delete` destroyed them | `--bucket-seconds`; 60 s for stages, 3600 s for the sitting |
| 6 | The k6 VU pool was smaller than the latency tail it had already measured | Stage B attempt 1: 4 of 8 scenarios pinned at their 150-VU ceiling, t00 delivering 85 of 94 req/s, 26 dropped iterations | `preAllocatedVUs` 400 (4.3 s of headroom at peak rate); cost measured at 0.13 MB/VU |
| 7 | `POLYFORGE_NATS_URL` was set nowhere, so `PlanRunner.audit` no-opped and SK-H2 computed 0 == 0 | four attempts | NATS deployed; audit stream carried 2,888 records in one Stage B hour |
| 8 | The planner fault deleted a pod that kept serving established connections | `fallback()` never executed in 7 planner kills across 4 attempts | `scale --replicas=0`; Stage B observed 7 fallback lines per injection |
| 9 | SK-H1 scored `mean_violation`, pinned at 0 by a 375 ms target against an ~8 ms p99 | 0.00 in 16 of 17 hours | re-registered on p95 deviation (below) |
| 10 | The SK-H1 positive control used `tc netem`, which **cannot reach the metric** | `crud_p95` derives from `latency_ms`, which times `burnCPU` and nothing else; demonstrated at 250 ms injected / 1 ms recorded | fault is CPU starvation, which lands inside the burn's wall-clock deadline; measured +70.4% |
| 11 | SK-H2 compared 14 degraded cycles against 2,888 audit records and passed | the operator audits every cycle for every tenant: 8 x 360 = 2,880 under healthy operation | restated as stream growth across the outage window vs degraded cycles x tenants |
| 12 | The apiserver throttle fault has never perturbed anything | FlowSchema matches (40/40 impersonated requests dispatched) but the level has **never rejected a request**: 3 concurrency seats, `nominalConcurrencyShares` already at the minimum 1, operator never has 3 requests in flight | **fault retired.** It has no reachable mechanism on this apiserver |
| 13 | 16 replicas issuing concurrent `GRANT`s on startup crash-looped on "tuple concurrently updated" | Stage B attempt 1 died 167 s in | grants moved inside the migration transaction, under the advisory lock |
| 14 | One export width cannot serve both hypotheses: SK-H1 scores a 5-minute recovery, SK-H3 hour buckets, and percentiles do not aggregate | caught while writing this prereg, before the sitting rather than after | the run exports twice, coarse then fine (3600 s and 60 s), each hypothesis scored at its registered width |

## Disclosures made before the run, not after

**`crud_p95_ms` and `crud_p99_ms` measure CPU service time, not service
latency.** `internal/platform/replay.go` starts its clock immediately before
`burnCPU` and stops immediately after; request parse, authorisation, queueing
ahead of the handler, the telemetry write and the entire network path are
outside the measured window.
`internal/platform/replay_latency_semantics_test.go` demonstrates it: 250 ms
injected outside the burn, 1 ms recorded.

This is disclosed rather than fixed. Widening the window would change what
every committed live record means and break comparability with the B2 baseline
SK-H3 is frozen against, so it is not being changed inside a prereg whose job
is to score a run. SK-H7 below reports the client-observed number beside it so
no reader can mistake one for the other. Attempt 4's "server-side 2.23 ms vs
k6 1032 ms" gap is partly this definition and partly the pinned load path; the
record should not attribute all of it to the latter.

**An intermittent sub-minute write stall, roughly once per hour, NOT solved.**
Stage B runs record a spontaneous excursion that produces multi-second requests
while p95 sits near 20 ms: attempt 2 at t+52m (max 3,791 ms), attempt 6 at
t+32m (399 VUs on one tenant, max 5,696 ms, 68 dropped iterations, run
aborted). Attempt 5 completed a clean hour and did not hit one, which is why an
earlier draft of this document claimed the warm-up had eliminated it. **That
claim was wrong and is withdrawn**: one clean hour is not evidence of
elimination, and saying so here is cheaper than discovering it at hour nine.

What the observer establishes: it is NOT pod restarts (0), NOT node memory
pressure (9%), NOT autovacuum (never observed running), and NOT the injected
faults — the excursion in attempt 6 arrived 5.5 minutes AFTER the CPU fault
ended, and during the fault itself VU usage never exceeded 7. What it does show
is telemetry ingestion collapsing about sixfold immediately before the
excursion (~8,000 rows per sample to ~1,350) inside a checkpoint window.

Working mechanism, stated as a hypothesis and not as a result: `pgdata` is an
emptyDir on a VHDX under WSL2; the control plane writes each telemetry event
synchronously inside the request handler; a storage stall therefore blocks
handlers. `latency_ms` cannot see it because that clock stops before the write,
which is why server-side `crud_p95` stays at ~2.4 ms while clients wait
seconds — the same shape as attempt 4's 464x server-versus-client gap, and a
second reason not to read `crud_p95` as service latency.

**Acted on, and disclosed:** the eval PostgreSQL now runs with
`synchronous_commit=off`, `max_wal_size=4GB` and `checkpoint_timeout=30min`.
That is appropriate for a store which exists to be measured and discarded — the
only thing at risk is the last few telemetry rows if the pod dies — and it
would not be appropriate in a real deployment. If the stalls persist under this
configuration, the mechanism above is wrong and the record must say so.

**The consequence for SK-H4, registered now rather than argued later.**
`dropped_iterations` is absolute, cumulative and carries `abortOnFail`. If a
stall of this kind recurs roughly hourly and each can drop an iteration, a
24-hour sitting cannot complete, and **the threshold is still not relaxed** —
`PREREG_LIVE_SOAK_V2.md` said "if attempt 3 fails on a handful of drops, that
is the honest finding, and the response is NOT to relax the threshold", and
that holds. The finding in that case is about what this machine can host, which
is exactly the outcome SK-H4's stopping rule below names in advance.

**Stage A and Stage B evidence is a precondition, not a result.** The ladder's
numbers are reported in the record as gate results. They are not hypotheses and
nothing in the sitting is scored against them.

## Design (frozen)

**Duration.** 12 h minimum, **24 h target**, one kind cluster, one
uninterrupted run. Machine sleep VOIDS the run; it is reported void, never
spliced. Power settings recorded before and restored after
(`docs/WP14_SOAK_RESTORE.md`).

**Cell.** `crud_bursty` / `uniform` / `medium`, CRUD-only load
(`POLYFORGE_EVAL_LIVE_AI` unset), `POLYFORGE_EVAL_SHARED_PG=1`,
`POLYFORGE_EVAL_BUCKET_SECONDS=3600`.

**Ladder precondition.** Stage D is entered only when Stage A (10 min), Stage B
(60 min, positive controls) and Stage C (4 h endurance) have all passed **on
the same commit**. A fix after Stage C invalidates Stage C and the ladder
restarts from the changed stage. This is the rule the previous four attempts
lacked: every earlier fix was validated by the next 24-hour run, which is why
four problems were found serially over four days.

**Faults (frozen schedule).** Two injectors, both with mechanisms demonstrated
in Stage B: **planner scale-to-zero** (90 s) and **CPU starvation** (two
spinners on one worker, 120 s). Alternating at **T+2 h, T+5 h, T+8 h, T+11 h,
T+14 h, T+17 h, T+20 h, T+23 h** — 4 of each, with at least 2 of each inside
the 12 h minimum. The apiserver throttle that occupied half of B2's schedule is
retired per row 12; the sitting injects fewer, real faults rather than more,
cosmetic ones.

**Baseline values (frozen FROM the committed record).**
`eval/results/live_chaos_p99_runs.csv`, row `phase7_chaos_p99` /
`crud_bursty`: `crud_p95_ms` 2.9827, `crud_p99_ms` 8.0072, `mean_violation`
0.0, 135 steps.

## Hypotheses

**SK-H1 (recovery, per-occurrence, binary).** Within **5 minutes** of the end
of each injection, `crud_p95_ms` returns to within **20%** of the pre-fault
window's value. Scored on the **60 s** buckets, per occurrence; PASSES only if
every occurrence passes.

The granularity is load-bearing and was nearly got wrong: the sitting exports
hour buckets for SK-H3, and a five-minute recovery cannot be resolved at that
width — percentiles do not aggregate, so an hourly p95 cannot be subdivided
afterwards. The run therefore exports **twice**, coarse then fine, and each
hypothesis is scored at the width it was registered against.

Re-registered from `mean_violation`, which could not fail. The replacement was
**observed failing on demand** in Stage B — CPU starvation drove `crud_p95`
from 2.215 ms to 3.774 ms, +70.4% against the 20% band — which is the evidence
that the metric has range. `mean_violation` is reported alongside as secondary,
explicitly noted as saturated, so no reader mistakes its 0.00 for stability.

**SK-H2 (audit continuity).** For each planner injection, an equal-length
**healthy** window is sampled immediately before the outage. The audit stream
must not grow more slowly while the controller is degraded than it did while
healthy: `degraded_growth >= healthy_growth - tenants`, allowing one control
cycle of boundary jitter, with both growths strictly positive. Scored per
injection; PASSES only if every injection passes.

**This is the THIRD formulation, and the revisions are disclosed rather than
quietly applied.** Both earlier ones failed for reasons unrelated to the claim,
and both faults were properties of the instrument, visible without reference to
any outcome:

1. *"degraded cycles == audit records"* compared **14 against 2,888 and
   passed.* The operator audits every cycle for every tenant, so a 60-minute
   run puts 8 x 360 = 2,880 records on the stream whether or not anything
   degrades. Two sides differing by two orders of magnitude under healthy
   operation is not a continuity test.
2. *"stream growth across the outage == degraded cycles x tenants"* **failed on
   a run where auditing was perfectly continuous.** Stage B attempt 5 grew by
   exactly 88 records in both windows — 11 control cycles x 8 tenants — while
   the rule expected 56, because grepping the operator log for "planner
   unavailable" undercounts cycles, and the window also holds post-recovery
   records that are indistinguishable from fallback ones.

The third needs neither a cycle count nor a way to separate the two record
populations, which is why it is expected to survive contact with the sitting.
**It is not a looser rule.** It fails whenever records are dropped, and that
was verified against synthetic windows rather than asserted: continuous
auditing passes; a 50% shortfall, total silence while degraded, an idle stream
while healthy, an unreadable sample, and one bad window among two all FAIL.

A reviewer is entitled to be suspicious of a hypothesis restated twice during
validation. The defence is the ladder's purpose: Stage B exists to find
instruments that cannot measure what they claim, BEFORE the sitting rather than
during scoring. Both revisions happened there, both are recorded here with the
numbers that motivated them, and the final rule was fixed before any 24 h data
existed.

**SK-H3 (latency stability).** Every **hour-bucketed** `crud_p95_ms` stays at
or below the committed **p99** of **8.0072 ms**. A bucket above it fails SK-H3
and names the hour. Read together with the disclosure above about what this
metric measures.

**SK-H4 (validity, binary).** Zero invalid-run signatures over the whole soak:
nonzero p95, nonzero `n_events`, sampler coverage >= 90%, and k6 delivery
thresholds met — `check_metrics`, `check_sampler_coverage` and
`check_k6_delivery`, unchanged. `dropped_iterations` remains absolute: one drop
in ~26 million iterations fails it. **The threshold is not relaxed if it
fails.**

**SK-H6 (load distribution, binary).** No single control-plane pod serves more
than **25%** of sampled work and at least **80%** of replicas serve some, for
every sample across the run. Replaces the retired SK-H5.

This is the hypothesis attempt 4 needed and did not have: it ran 17 hours with
one pod carrying effectively all traffic and no gate asked.

**SK-H7 (client-observed latency, descriptive, no pass/fail).** Report k6's
whole-run `http_req_duration` p95, p99 and max beside the server-side figures,
and state plainly that they measure different things.

Descriptive on purpose. It exists so the record cannot be read as claiming
end-to-end latency it never measured, and both outcomes are informative: a
small gap would show the burn dominates real service time, a large one would
show queueing the server-side metric cannot see. Making it scored would invite
reading whichever number is convenient as the result.

## Outcome handling and stopping rule

The soak runs **once**. No hypothesis, threshold, fault time, duration or
margin is altered after the first result is seen.

- **Any hypothesis FAILS** — that is the finding and the headline of the
  record. Instability surfacing mid-soak is a result, not a nuisance.
- **SK-H4 FAILS again** — this would be the **fourth** invalid sitting. The
  finding then is not "try a sixth time": it is that a 24 h live run of this
  system is not achievable on this machine, reported as such, with the ladder
  results as evidence that the apparatus was rebuilt and still could not.
  That is a publishable limitation, and it is registered here in advance so
  that reporting it cannot be mistaken for giving up after seeing the data.
- **The record is never truncated or spliced.** A run ending early is reported
  at the length it reached, with the reason.
- **Early stop on a known-invalid delivery path is permitted and disclosed.**
  k6's `abortOnFail` thresholds stop a run that is already unscoreable rather
  than driving load for hours to be rejected anyway; attempt 4 spent 6.5 h in
  that state. Any such stop is disclosed with the evidence.

`reproduce.py` must re-derive every previously committed record
byte-identically after this work lands (R4). The new record joins the gate.
