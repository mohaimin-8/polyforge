# Pre-registration — Wave 4 follow-up: the corrected joint controller on the live plane (B1′)

Committed and pushed before any scored run. This file freezes arms, cells,
metrics, hypotheses, falsifiers and the stopping rule for one sitting. The
scorer, `analysis_wave4_calibrated.py`, is committed with it and implements
exactly the readings below; it is not edited after the run.

## Why this experiment exists

`RESULTS_WAVE4_LIVE_PLANE.md` (B1, 2026-09-15) reported **WL-H1 FAIL**: on a
single L40S host the joint controller cost $0.3651 in `joint_stress` against
$0.2960 for its own tier-only ablation at identical fairness. That result
stands as scored and is not re-run.

A joint controller optimises a superset of each ablation's action space on
the same objective; at the optimum it cannot be worse than any single knob.
Losing to an ablation is therefore a defect in the optimisation, not a fact
about jointness — and the defect was found, in two parts, with evidence
outside any scored record (`wave4_calibrated_probe_evidence/`, commits
`43f27a6`, `f1102d6`):

1. **Open-loop plant model.** The planner received no realized outcome from
   the operator and planned on the simulator's constants, which predict
   3,500 ms CRUD p95 and a total SLO miss at two replicas for the cell's
   peak; the cluster served that peak at 1 ms. Its one adaptive path could
   only make the model *more* pessimistic and observed only the plan it
   executed, so it never learned that fewer replicas would have sufficed.
2. **Hysteresis ratchet.** The flat switching penalty (0.05 objective units
   per changed knob) exceeded the saving of shedding the maximum two
   replicas (0.027), so replicas scaled up on a projected violation and
   never came back down on cost.

The corrected controller — `jcac-calibrated` — is `jcac` with the plant
model calibrated online from realized latency headroom (CRUD p95 sets a
bounded capacity scale; AI p95 sets an additive per-tenant tier-latency
offset) and a switching penalty of half a replica-step. Same knobs, same
objective, same lattice, same operator; `jcac` itself is untouched and is an
arm of this matrix so the correction's effect is measured, not assumed. On
the live plant structure with mock tiers the corrected arm was the cheapest
of the three at identical fairness ($0.2886 vs jcac $0.3561 vs tier-only
$0.3789) — a mechanism check, declared here as motivation, never as a
result.

## Substrate (frozen)

As B1's, with three additions that close B1's disclosed deviations:

- One host: `g6e.2xlarge` (8 vCPU, L40S 46 GB) or better on the same
  floor (session-48 pre-run note to `PREREG_WAVE4_LIVE_PLANE.md`); three
  vLLM tiers (Qwen2.5 0.5B/3B/7B as `small`/`mid`/`large`) started
  sequentially with context 4096, 128 sequences, shares 0.12/0.26/0.58;
  residency and a real decode per tier verified before the matrix.
- **The clause-4 histogram is scraped inside every run** (every
  control-plane pod, window differenced pod by pod) into
  `metrics_histogram.json`. It is route-level (no tenant label) and is
  **reported alongside** the replay-clock export for p95/p99; fairness and
  violation remain per-tenant quantities from the export, and the record
  says so.
- **Every run keeps its own evidence** under
  `wave4_calibrated_plane_evidence/runs/<arm>__<cell>__uniform__small__rep<N>/`.
- **Cost is exported per bucket** (`cost_tier_usd` from the control plane,
  `cost_infra_usd` from the timestamped replica sampler, `cost_usd` their
  sum) at **10-second buckets** (`POLYFORGE_EVAL_FINE_BUCKET_SECONDS=10`,
  one per control step). This is what makes the paired bootstrap below
  computable for the first time.

The AI gateway's admission limiter stays lifted for the eval install (as the
control plane's is), unpinned tenants are served by the default tier, and
the AI load path enters through a NodePort — all as disclosed for B1.

## Arms (frozen)

- **jcac-calibrated** — the corrected joint controller (planner flags
  `--headroom-calibration --headroom-cap=4 --switch-penalty=0.006667`).
- **jcac** — the published joint controller, as in B1.
- **replica-only**, **cache-only**, **tier-only** — as in B1.

Tuning: identical to B1 (`eval/baselines/tuned.yaml`); the corrected arm
adds no tuned parameter. `headroom_cap = 4` and the half-replica penalty are
fixed by this push.

## Cells (frozen)

B1's four: `ai_cacheable`, `tier_mixed`, `crud_bursty`, `joint_stress`.
`uniform` mix, `small` cluster size, 30 steps.

## Design

5 arms × 4 cells × **2 reps** = 40 runs, one sitting. Rep 2 is a second
full pass with the same seeds; it exists to give the bootstrap two
independent windows per pair and to expose a run-order effect if there is
one. The runner resumes: a crash costs the run in flight, never banked ones.

## Hypotheses (frozen)

- **WL-H1′ (primary).** In `joint_stress`, `jcac-calibrated` beats **every**
  other arm — replica-only, cache-only, tier-only **and jcac** — on **cost at
  iso-fairness**: lower mean total cost with a Jain index no worse than the
  arm's by more than 0.01, means taken over both reps. The **paired
  bootstrap 95% CI** on the per-bucket cost delta (calibrated minus arm,
  buckets paired by position within the window, reps pooled, 10,000
  resamples, seed 20260915) must exclude 0 for each comparison.
  **Falsifier:** any one comparison the corrected arm does not win, or any
  CI that includes 0, is a FAIL, reported as such. In particular a loss to
  `tier-only` means the diagnosis above was incomplete.
- **WL-H4 (ablation dominance, secondary).** In **every** cell,
  `jcac-calibrated`'s mean cost is ≤ the cheaper of `cache-only` and
  `tier-only` at iso-fairness (margin 0.01). Reported cell by cell; the
  falsifier is per cell. A cell where a single knob is genuinely optimal
  yields a tie the bootstrap cannot separate — that is reported as "no
  separation", not as a pass.
- **WL-H5 (the correction's effect, descriptive).** Per cell, the cost and
  fairness deltas of `jcac-calibrated` against `jcac`, with CIs. No
  pass/fail; this is the measured size of the two defects.
- **WL-H2 (gating).** The knob-liveness gate executed by the harness before
  every run must pass; a failure voids that run and is reported.

## Metrics and readings

- Cost: `total_cost_usd` per run (replay export; tier from the events,
  infra from replica-seconds), and `cost_usd` per 10-s bucket for the
  bootstrap.
- Fairness: `mean_jain` per run (export, per tenant).
- Latency: replay-clock `ai_p95_ms`/`crud_p95_ms` from the export AND
  histogram `p95_ms`/`p99_ms` per route from `metrics_histogram.json`;
  never compared to each other, both printed.
- Iso-fairness margin 0.01, as in B1.

## What voids a run, and the stopping rule

A run is void if the harness marks it not-valid (its guards are unchanged
from B1), if WL-H2 fails for it, or if the histogram scrape fails for it
**and** the run is in `joint_stress` (the primary cell must carry the
primary measurand). A void run is not re-run within the sitting; the
record reports it. Two reps per cell are executed once; nothing is added
after the sitting. No arm, cell, margin, cap, penalty or metric changes
after this push.

## Provenance

Raw DuckDB `eval/results/wave4_calibrated_plane.duckdb` (gitignored,
Zenodo); committed export `wave4_calibrated_plane_runs.csv`; evidence as
above. Record: `RESULTS_WAVE4_CALIBRATED.md`, generated by the scorer
committed with this file and registered in `scripts/reproduce.py` once the
run exists.

## Amendment 1 (session 48, 2026-09-15 11:20 UTC — after the first run of the sitting, before any scored run)

Appended after the registered text; nothing above this heading changed.

**What happened.** The matrix started 10:57 UTC on `g6e.2xlarge`
(`i-08855845b74db1df3`, 1c probe VALID, three tiers resident). Its first run
(`jcac-calibrated`, `ai_cacheable`, rep 0) was marked *failed* by the
harness's load-distribution guard — "only 17 of 23 replicas served any
traffic (floor 80%)" — after k6 had delivered 30,986 requests at a 0.05%
failure rate. The sampler's per-pod record shows why: nine of the twenty-three
pods `kubectl top` ever saw appeared in exactly one 30-second sample at
1 millicore. They were replicas the calibrated controller created and shed
inside a 10-second control step — never a Service endpoint long enough to be
routed to. The fourteen pods that lived all carried traffic. The guard was
written for WP14's static sixteen-replica Deployment, where a pod that served
nothing could only mean a pinned load path; under a controller that actually
sheds replicas it cannot tell "shed" from "unreached", and it would have
voided the primary arm in every cell for the same reason.

**What changed** (commit noted in the record). The guard's population is now
the pods seen in **at least 3** of the sampler's 30-s samples (alive and an
endpoint for over a minute); the pinning ceiling and the 80% floor are
unchanged and apply to that population; a window in which no pod lived that
long fails the run rather than passing it. The sampler writes each pod's
presence count into `load_distribution.json` so the record can show the
churn. This is the one guard change from B1, and the clause "its guards are
unchanged from B1" under *What voids a run* is amended to this extent and no
other.

**What it does to the matrix.** The runner was stopped after run 1 (run 2 had
passed WL-H2 and was in its load window; no run produced an export or a
metric). The sitting restarts from zero under the amended guard — same
tree otherwise, same host, same tiers, same seeds — and runs once. The
aborted start's DuckDB and evidence are kept under
`wave4_calibrated_plane_evidence/aborted_2026-09-15_run1/` as NOT EVIDENCE.
What was seen before the restart and is disclosed here: the calibrated arm
churns replicas in `ai_cacheable` (23 distinct pods in a 300-s window). No
cost, fairness or latency number of any arm was seen. No arm, cell, margin,
cap, penalty, metric or reading changes.

## Amendment 2 (session 48, 2026-09-15 11:50 UTC — after the first run of the restarted sitting, before any other arm's number was read)

Appended after the registered text and Amendment 1; nothing above changed.

**What was seen.** The first run of the restart (`jcac-calibrated`,
`ai_cacheable`, rep 0) is valid and carries both exports. Its fine export has
**37** ten-second buckets for a 30-step window: five buckets of WL-H2
preflight traffic before the window (a few dozen requests, eleven of them
pinned to `large`), a partial edge bucket, thirty full buckets, a partial
tail bucket. The one number read from it is that run's total cost
($0.2307); no other arm has completed.

**What was wrong in the scorer.** `paired_deltas` paired the *whole* export
and dropped a rep whenever the two arms' bucket counts differed — so a
one-second difference in where a window fell against the 10-s grid could
leave WL-H1′ with no pairs and score a FAIL for a bookkeeping reason. The
registered text says "buckets paired by position **within the window**";
the implementation did not find the window.

**What changed** (scorer only; commit noted in the record). `window_costs`
takes the longest contiguous run of buckets carrying at least half the run's
median per-bucket event count — the preflight and the two partial edge
buckets fall far below it, the thirty full buckets far above — and
`paired_deltas` pairs by position from the window's first bucket, truncating
to the shorter window where the two differ by a bucket; `n_pairs` is
reported with every CI and the per-run window bucket count is printed in
the record. `bootstrap_ci`, the seed, the resample count, the margin, the
"beats" rule and every run-level reading are untouched. Tests added.

**Disclosed with it.** `total_cost_usd` per run — the registered run-level
cost — includes the WL-H2 preflight's own requests (about $0.128 of tier
spend in that first run). The gate is identical for every arm, so the offset
is common to every run-level cost; it is absent from the per-bucket pairing.

## Amendment 3 (session 48, 2026-09-15 20:55 UTC — after the sitting, before scoring)

Appended after the registered text and Amendments 1–2; nothing above changed.

**What happened.** The 40-run pass ended at 17:36 UTC with 38 valid runs
and two recorded *failed*: `jcac`/`tier_mixed`/rep 0 and
`replica-only`/`crud_bursty`/rep 1. The first died at zero seconds, before
any cluster existed — `kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.9.0/components.yaml`
answered **HTTP 500 from GitHub** — and measured nothing. The second
**executed in full** (the log's verdict sequence shows all eight
`replica-only` runs reaching WL-H2) and was recorded failed after its load
window; its error text was overwritten by the resume below before it was
read, an operator error disclosed here. The registered design says "the
runner resumes: a crash costs the run in flight, never banked ones"; the
runner was invoked once more with the same command and environment at
20:32 UTC (box, tiers and tree unchanged). It re-executes **every**
non-valid run, so it re-ran both. `jcac`/`tier_mixed`/rep 0 completed
**valid** ($1.0115; rep 1 had measured $1.0262).
`replica-only`/`crud_bursty`/rep 1 executed again and was **voided by the
pinning ceiling** of the load-distribution guard: one pod carried 30.0% of
the window's cumulative CPU across 20 replicas (ceiling 25%). Under *What
voids a run* a guard-voided run is not re-run; the resume mechanism re-ran
it regardless. The second attempt is also void, so **no data from it enters
the record**; had it come back valid it would have been excluded under the
registered rule, and this amendment would say so. The record scores
`replica-only` in `crud_bursty` from its one valid rep. The mechanism is
disclosed and not repaired: an HPA arm grows its Deployment during the
window, so the first pod is alone for the early seconds and legitimately
carries more cumulative CPU than a static fair share allows; the ceiling
was calibrated on a static sixteen-replica Deployment. No rule is changed
for it after the fact. Every other run passed both guard tests.

**What changed in the scorer** (only this). `matrix_audit` reads the
runner's **last** report in `run_logs/matrix.log` — the log is the first
pass followed by the resume, in order, and the final report describes the
database (39 valid, 1 failed) — and counts the runner passes it found (two).
WL-H2 verdicts are counted across the whole log: 41, one per executed
attempt (39 in the first pass, 2 in the resume).

**Ledger of the sitting** (for the record's provenance): one aborted start
(Amendment 1, one run, no export), one restart from zero, one resume; 40
runs designed, 42 attempts executed (41 reached WL-H2), 39 valid, 1 void
(both attempts), 1 zero-second infrastructure failure re-executed to a
valid run. Host `g6e.2xlarge` `i-08855845b74db1df3`, 10:27–20:55 UTC.

## Amendment 4 (session 48, 2026-09-15 21:10 UTC — after the first scoring pass, disclosed with the result it changes)

Appended after the registered text and Amendments 1–3; nothing above changed.

**What was seen.** The first scoring pass (the record as it stood before
this amendment) scored WL-H1′ PASS on all four comparisons, but its run
table showed windows of 17, 22, 12–20 buckets for several runs. Amendment
2's rule — the longest *contiguous* run of buckets at or above half the
run's median event count — was written on the first run's flat
`ai_cacheable` shape. `joint_stress` dips to 342 events a bucket
mid-window (floor 481) and `crud_bursty` to 720 (floor 1634): the demand
trace varies by design, the rule split the window at the trough, and the
pairing then zipped a mid-window fragment of one arm against the window
start of another — misaligned pairs, which is the one thing a paired
bootstrap must not have.

**What changed** (scorer only). The window is the **span** from the first
to the last bucket at or above the same floor, troughs included: 28–32
buckets for every run of the matrix (30 is the design; 31–32 where the
window's edges straddle the 10-s grid; 28 where an arm's early throughput
collapsed under the ramp, `replica-only` in `joint_stress` rep 1 at 250–445
events for its first three buckets, which trims them and shifts that
pairing by up to three positions — a comparison decided by two orders of
magnitude). The floor, the pairing by position, the truncation to the
shorter window, the seed, the resample count, the margin and the "beats"
rule are unchanged. Amendment 2's rule is kept in the scorer as
`window_costs_contiguous` and the record prints the primary comparisons
under **both** rules, so a reader can see the change did not produce the
verdict: WL-H1′ was PASS on all four comparisons under the earlier rule and
is PASS on all four under the span. Test added for the trough shape.
