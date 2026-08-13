# Pre-registration: live CRUD-plane soak with faults (WP14)

Registered 2026-08-13 (session 38). Committed and **pushed before the soak
starts**; the push event is the timestamp anchor. This scores new live
measurement, so R1/R2 apply in full.

## Question (and provenance)

Everything live on record is short. B2/B3 was one Codespace sitting of 135
steps (`eval/results/live_chaos_p99_runs.csv`, session 23) and WP8b is frozen
at `reps: 1` by its own stopping rule. A Transactions reviewer distinguishes
"ran live once" from "ran live for a day, through faults, and here is how it
behaved" — and the repository can only claim the former.

The GPU is not needed for this. `POLYFORGE_EVAL_LIVE_AI` is opt-in
(`eval/harness/cluster_backend.py`); unset, the harness deploys no AI gateway
and exercises the operator, the planner, the replica and cache knobs, chaos
injection and the full audit path on CRUD load alone. Local Docker makes the
run $0.

## Design (frozen)

**Duration.** 12 h minimum, 24 h target, **one** kind cluster, one
uninterrupted run. If the machine sleeps the run is **VOID** — reported as
void, never spliced with a second half.

**Cell.** `crud_bursty` / `uniform` / `medium`, CRUD-only load
(`POLYFORGE_EVAL_LIVE_AI` unset), `POLYFORGE_EVAL_SHARED_PG=1` (R5). If
Docker cannot hold `medium` for the duration, drop to `small` and disclose:
**duration outranks width** for this work package's purpose.

**Faults (frozen schedule).** B2's two injectors, unchanged — planner crash
and apiserver throttle — at **T+2 h, T+5 h, T+8 h, T+11 h**, alternating
(crash, throttle, crash, throttle), giving ≥2 occurrences of each within the
12 h minimum. Later faults at the same 3 h spacing if the run reaches 24 h.

**Baseline values (frozen FROM the committed record, not invented).** Every
threshold below is read off `eval/results/live_chaos_p99_runs.csv`, row
`phase7_chaos_p99.duckdb` / `crud_bursty`, which is the only committed live
CRUD row with faults:

| quantity | committed value |
|---|---:|
| `crud_p95_ms` | 2.9827 |
| `crud_p99_ms` | 8.0072 |
| `mean_violation` | 0.0 |
| steps | 135 |

`CHAOS_TOL = 0.05` is inherited unchanged from B2's LC-H1.

## Hypotheses (frozen)

**SK-H1 (recovery, binary).** Every injected fault recovers **without
operator intervention**: within 5 minutes of each injection the run returns
to planning, and post-fault `mean_violation` is within `CHAOS_TOL = 0.05` of
the pre-fault level. Scored per occurrence; SK-H1 PASSES only if **every**
occurrence passes. A single unrecovered fault fails it and is the headline.

**SK-H2 (audit continuity).** Every degraded cycle is audited, across the
whole soak — the D11 fix under sustained load rather than in a 135-step
window. Scored as: the count of degraded cycles equals the count of audit
records for them, exactly. Any gap fails it.

**SK-H3 (latency stability).** Every **hour-bucketed** `crud_p95_ms` stays
at or below the committed **p99** of **8.0072 ms**. The threshold is
deliberately the short run's *tail*, not its p95: a sustained run is allowed
to be worse than a 135-step p95, and using a value from the committed record
rather than a margin invented today is what keeps this honest. A bucket
above it fails SK-H3 and names the hour.

**SK-H4 (validity, binary).** Zero invalid-run signatures over the whole
soak: nonzero p95, nonzero `n_events`, sampler coverage ≥ 90%, and k6
delivery thresholds met — the existing `check_metrics`,
`check_sampler_coverage` and `check_k6_delivery` gates, unchanged.

## Outcome handling and stopping rule

The soak runs **once**. No hypothesis, threshold, fault time or duration is
altered after the first result is seen.

- **Any hypothesis FAILS** — that is the finding and it is the headline of
  the record. Instability surfacing mid-soak (planner leak, operator
  crash-loop, PG exhaustion) is a **result, not a nuisance**: reported, then
  fixed forward, then re-sat under a NEW prereg with one changed factor,
  mirroring the RB-H1 follow-up pattern.
- **The record is never truncated or spliced.** A run that ends early is
  reported at the length it reached, with the reason.
- **A void run is reported as void.** Machine sleep is the named risk; the
  power settings are changed before the run and restored after, and the
  prior value is recorded first.

`reproduce.py` must re-derive every previously committed record
byte-identically after this work lands (R4). The new record joins the gate.
