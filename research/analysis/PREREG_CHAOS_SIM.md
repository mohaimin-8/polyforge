# Pre-registration: simulated chaos campaign (Wave 2, first half of DEFENSE_QA #19)

Registered 2026-07-16 (session 20). Committed and pushed **before** any run;
push event is the timestamp anchor; to be mirrored to OSF prospectively per
DEFENSE_QA #17. The second half of #19 (an over-the-wire attack and live
chaos on the kind cluster) is Wave 3 and gets its own pre-registration.

## Question

PolyForge's planner-outage fallback (hold last-known-good) is designed and
unit-tested but was never exercised under load, and no failure-injection
campaign exists. Two failure classes are injected into the scoring engine,
controllers blind to both:

1. **Planner outage** (`chaos_planner_outage=(start, n)`): for planning
   steps in [start, start+n) the controller neither plans nor observes;
   the cluster serves on its last configuration. Applied to jcac it is the
   central-planner crash; applied to hpa it is the apiserver-throttling
   analog (a frozen reactive loop) — the same hook models both, which is
   exactly the point of comparing them.
2. **Replica kill** (`chaos_replica_kill=(start, fraction, n)`): for scored
   steps in [start, start+n) every tenant serves on
   max(1, floor(replicas·(1−fraction))) while billing stays nominal.
   System-agnostic infrastructure failure.

## Design (frozen)

Experiment `eval/experiments/chaos_sim.yaml`, sim backend, steps=120,
reps=15, `timeseries_reps: 15` (recovery curves need every rep's rows):

- Systems (9): jcac, hpa, keda (controls); jcac_outage_1m (40,6),
  jcac_outage_3m (40,18), hpa_outage_1m (40,6); jcac_kill50,
  hpa_kill50, keda_kill50 (all (60, 0.5, 3)).
- Workloads (4): crud_bursty, ai_cacheable, flash_crud, spike_agentic.
  Window geometry is analytic, not tuned: shapes are periodic with random
  per-tenant phase, so the 18-step outage covers ≥ 1 full burst cycle for
  every tenant of flash (period 16) and spike (period 12); the 6-step
  outage gives partial (phase-dependent) burst exposure; start=40 is
  post-warm-up, mid-run, leaving ≥ 60 recovery steps.
- Mixes/sizes: uniform × medium (the chaos factor is the failure, not the
  population). 9 × 4 × 15 = 540 runs, `chaos_sim.duckdb`.
- Chaos params are engine-level SystemSpec entries popped by sim_backend
  before the controller is constructed (unit-tested: they cannot leak into
  a controller; default-off path replays committed runs bit-identically).

Power note: primary tests pair 4 workloads × 15 reps = 60 pairs; a paired
t at alpha 0.01 has ≈ 0.9 power at dz ≈ 0.5 — the scale of effect the
campaign is designed to detect.

## Hypotheses (frozen; paired t on (workload, rep) cells, 60 pairs, alpha 0.01)

- **CH-H1 (primary, fallback value):** jcac with its planner dead for
  1 minute mid-run still beats a *healthy* HPA on composite J:
  J(jcac_outage_1m) < J(hpa), p < 0.01.
- **CH-H2 (primary, symmetric failure):** under the identical 50% kill,
  jcac keeps its J win: J(jcac_kill50) < J(hpa_kill50), p < 0.01.
- **CH-H3 (secondary, dose-response):** J(jcac_outage_3m) − J(jcac) > 0
  quantified with a 95% CI; expectation (directional): degradation grows
  with outage duration. jcac_outage_3m vs healthy hpa is reported as
  measured with **no** hypothesis — a 3-minute dead planner across a full
  flash cycle losing to a live reactive loop would be an honest boundary,
  and we decline to bet against it in advance.
- **CH-H4 (secondary, centralization tax):** the paired degradation deltas
  [J(jcac_outage_1m) − J(jcac)] vs [J(hpa_outage_1m) − J(hpa)] are compared
  descriptively: who suffers more when its control loop freezes for the
  same 1 minute.

Descriptive recovery metric (frozen): per (system, workload), mean
per-step violation across reps; recovery time of a chaos arm = number of
scored steps after its window ends until its 3-step rolling mean violation
first comes within 0.02 of its own control arm's value at the same steps.
Reported per workload; no hypothesis attached.

## Outcome handling and stopping rule

Results to `RESULTS_CHAOS_SIM.md` as measured, failures verbatim (a CH-H1
failure means the fallback story must weaken from "still wins" to its
measured shape — that sentence would go into the thesis, not be softened).
One execution of the 540-run matrix, valid only at 540/540; crash-resume
allowed; no widening; no added arms after this push.
