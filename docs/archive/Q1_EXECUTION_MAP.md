# Q1 execution map — terse runbook

> **Read `docs/MAIN_WORKING_PATH.md` first** — it is the top-level ordered
> route (milestones M1–M6) that this runbook's tasks slot into. When the two
> disagree on **order**, the main path wins; this file remains authoritative on
> each task's **steps**. Session 2026-08-05 decisions folded in below: goal
> raised to **Transactions-level**; **T17 formal SLO guarantee** added (TPDS
> strengthener); **T18 energy/admission** added (optional); the **leakage-budget
> controller is spun off to a separate project** (TDSC) and is NOT a task here.

Purpose: every remaining weakness, as executable steps. Written so anyone
can follow it without re-deriving context. Rationale lives in
`docs/archive/Q1_ROADMAP.md` (v2); this file is the *what to do*. No submission is
guaranteed acceptance; completing ALL tasks (incl. Phase 4) before
submitting to FGCS/TCC/TSC (formal Q1 / Transactions) maximizes the probability.

Execute tasks in ID order. `[GATE:author]` tasks wait for the author; skip and
continue. Each task: DO → VERIFY → DONE-WHEN. **If VERIFY fails: stop that
task, report, do not improvise.**

## Rules (read before ANY task — violating these invalidates the record)

- **R1** `research/analysis/PREREG_*.md` and every generated `RESULTS_*` /
  campaign record are **immutable** once their campaign closed. Post-run
  notes go in a new file or `RESULTS_MASTER.md`, never in the closed file.
- **R2** Any new *scored* measurement needs its own PREREG committed and
  pushed **before** the first run (`RESULTS_MASTER.md` §ground rules).
- **R3** Universal regression gate before any commit: `gofmt -l .` empty,
  `go test ./...` green, `python -m pytest` green,
  `python scripts/reproduce.py` reports all records MATCH.
- **R4** Any change under `research/jcac_sim/` or `eval/harness/` must
  default OFF/no-op so existing run identities and records stay
  bit-identical (R3 proves it).
- **R5** Any live cluster metrics run: `export POLYFORGE_EVAL_SHARED_PG=1`
  (default per-pod SQLite reads ~empty under scaling → false 0-latency).
- **R6** Any live run with the operator chart: `--set
  planner.auth.enabled=false` (the frozen-harness posture) OR export the
  chart's generated token as `POLYFORGE_PLANNER_TOKEN` in the harness env.
- **R7** Cite numbers only per `RESULTS_MASTER.md` §"Which number to cite";
  never mix substrates or pool samples.
- **R8** Thesis/manuscript prose is author-owned. The desk does engineering only.

## T0 — commit session-31 security work ✅ DONE (9431553, pushed)

## Phase 1 — artifact integrity ✅ DONE (session 32)

### T1 — CSV fallback loaders ✅ DONE (0d012d4)
Delivered wider than specified. Findings that changed the plan:

* **The map said 7 scripts; the real split was different.** Five share one
  run-level query (`stats.load_campaign_runs`); `analysis_econ` already used
  `stats.load_runs` and only needed its DBs registered in `CSV_EXPORTS`;
  `analysis_chaos` and `analysis_econ.tier_posture` read the **timeseries**
  table, which no run-level export covers.
* **Timeseries solved by committed aggregates.** Each such consumer uses a
  *grouped* result, so `eval/scripts/export_timeseries_agg.py` commits an
  aggregate of exactly the query that consumer runs (~35 KB total): chaos
  violation traces, per-(system, tier) posture, fairness worst-tenant, and
  the two runs fig09 plots. Scope stated in `docs/REPRODUCE.md`; a slice
  outside the committed one raises rather than silently returning nothing.
* **Row order is part of the contract.** Bootstrap CIs resample positionally
  under a fixed seed, so the exports' `ORDER BY` shifted published CI digits
  when rebuilt from CSV. Exporter now writes the DuckDB's own row order;
  `load_runs` (whose SQL *has* an `ORDER BY`) re-sorts in its CSV branch.
  Six exports rewritten in place — verified same rows, reordered only.
* **Four `.exists()` guards** (v2, v3, fairness_v2, iso-cost) asked whether
  the DuckDB was present when the question is whether the data is loadable;
  now `runs_available()`.

### T2 — reproduce.py covers everything ✅ DONE (0d012d4)
**16/16 records byte-identical, 19/19 figures, on a tree with zero DuckDBs**
(the map's "20/20" counted campaigns, not records; 16 is the true corpus).
Two defects found while verifying:

* reproduce.py never cleared its output dir, so anything that failed to
  rebuild was still counted from the previous run's leftovers — it reported
  19/19 figures on a tree where fig09 had not rebuilt at all. Now clears its
  own artifacts by pattern first.
* Campaign scripts wrote to a hardcoded path, ignoring
  `POLYFORGE_ANALYSIS_OUT` — running one during a reproduction would have
  overwritten the frozen record it was meant to be diffed against. All now
  go through `stats.record_path()`.

### T3 — controller invariant property tests ✅ DONE (608eb5b)
`research/jcac_sim/test_invariants.py`, 9 tests, 192 total pass.
**Lesson worth carrying:** the first draft derived `MAX_REPLICA_STEP` from
`DELTA_REPLICAS`, which made the tests tautological — mutating the lattice
to ±4 left all six passing. Limits are now literals (the frozen spec), with
`ActuationContractTests` pinning the lattice against them.
**Always mutation-test a property test.** Verified: widening the lattice
fails 4 tests; reintroducing the audited defect itself (`origin = None`)
fails exactly the two clamp tests.

## Phase 2 — B1 live three-knob plane `[GATE:user, $0 route available]`
**Runs BEFORE Phase 3** (Phase 3 modifies the code path B1's preflight
verified).

**The money gate is gone.** `docs/WAVE4_FREE_ROUTE.md` runs B1 at $0 by
splitting it: GPU tiers on a free Kaggle kernel (30 GPU-h/week; ran the tier
bench twice), cluster on a free Codespace (120 core-h/month; ran Phase 7 and
the live chaos sitting), joined by a free cloudflared tunnel. No code change
was needed — `POLYFORGE_EVAL_TIER_BACKENDS` already takes a URL. Prefer a
single GPU VM on GCP's $300 trial / Azure-for-Students $100 **if GPU quota is
granted** (avoids the round-trip entirely); otherwise the split route.

### T4a — free-route preflight (do this FIRST; protects GPU quota)
DO: start `research/calibration/kaggle_tier_server.py` on Kaggle (GPU +
internet on); it prints the public URL and the exact
`POLYFORGE_EVAL_TIER_BACKENDS` export line. Then, **before** standing up any
cluster: `python eval/scripts/tunnel_preflight.py`.
VERIFY: exit 0. Read `slo_headroom_ms` — **the binding constraint is the
premium AI SLO (2500 ms), not WL-H2.** The tier gap survives ~1.3 s of
round-trip, but ~600 ms already puts the mid tier (1883 ms) over target,
after which premium tenants violate regardless of the controller and the SLO
term goes flat. The probe warns; heed it.
DONE-WHEN: exit 0 **and** the slowest tier is inside the premium SLO. Exit 1
or an over-target warning → do not start the run; reduce round-trip or use a
credit-funded single host.

### T4-BLOCKED — B1 cannot run as frozen yet (found session 33)

**Stop. The ledger's "B1 now needs only the GPU host" is wrong**, and this was
found by trying to run it, not by reading. The GPU half is solved and T4a
passes on real hardware; the *cluster* half cannot express the protocol:

* `eval/harness/cluster_backend.py`: `OPERATOR_SYSTEMS = {"jcac"}`, with the
  comment "the jcac_* ablations remain sim-only". Only the full jcac arm has
  live wiring.
* The prereg's arms are **jcac, replica-only, cache-only, tier-only**, and it
  calls the two-knob ablations "the sharpest test of the central claim".
  WL-H1 is scored against them, so without them WL-H1 is not evaluable.
* Freezing a knob is not configuration: the Policy CRD has `replicaMin` /
  `replicaMax` but **no bounds for `cacheSizeMB` or `modelTier`**, so
  cache-only and tier-only need planner- or operator-side support for pinned
  knobs.
* No B1 experiment spec exists (`eval/experiments/` has no wave4 file); the
  four frozen cell classes *do* exist in `workloads.py`.

**T4 prerequisite (new task, desk-doable, no GPU):** implement the three
missing live arms faithfully to the prereg's definitions, add the wave4
experiment spec mirroring the frozen arms/cells, and dry-run the whole
integration non-scored. Only then spend the single scored shot. Do NOT
improvise substitute arms — arms are frozen by the prereg push and "may not
change after it".

**RESOLVED at the desk, session 34 (2026-08-06)** — every bullet above is
closed: `OPERATOR_SYSTEMS = {"jcac", "cache-only", "tier-only"}` (replica-only
is reactive HPA with cache/tier held by `push_default_knobs`); the Policy CRD
carries `cacheSizeMBMin/Max` + `modelTierMin/Max`, min==max pinning a knob,
clamped at the actuation point and forwarded to the planner; the sim mirrors
the same freeze via `SystemSpec.knob_freeze`; and
`eval/experiments/wave4_live_plane.yaml` holds the frozen 4 arms × 4 cells.
Arms were implemented to the prereg's §Arms text, not substituted. R4 holds
(`reproduce.py` 16/16 byte-identical), so the open-bounds default is a no-op.
**One piece of this task is still owed:** the *live* non-scored dry-run — this
machine has no Docker, so it moves to the front of T4's own sitting, before
the WL-H2 gate and before the scored matrix.

### T4 — execute B1 (after the prerequisite above)
DO: follow `research/analysis/PREREG_WAVE4_LIVE_PLANE.md` §Substrate +
§Status update exactly; apply R5 + R6 (on the free route, `docs/
WAVE4_FREE_ROUTE.md` §3 lists both). Run the **WL-H2 preflight
knob-liveness gate first** (`eval/scripts/knob_preflight.py` — the real gate;
T4a only predicts it) — if any knob is inert live, STOP (an inert-knob run
VOIDS WL-H1; do not fake it, report). VRAM short → drop the 7B tier
(prereg's amendment rule; the free route is a two-tier run by construction).
On the free route, carry `WAVE4_FREE_ROUTE.md` §4 verbatim into the RESULTS
file — tunnel round-trip changes absolute latency, so live absolutes are
never quotable as tier latencies. Run the frozen matrix once (stopping rule).
Publish outcome **as measured, PASS or FAIL**, via a new analysis script →
new RESULTS file; reconcile `RESULTS_MASTER.md`, `DEFENSE_QA.md`,
`REMAINING_WORK.md`. Never edit the prereg (R1).
DONE-WHEN: RESULTS committed + ledgers reconciled.

## Phase 3 — ship planning cells ✅ DONE (session 33, 81dd3d5)

Done ahead of T4 (user opened the gate). **T5/T6/T7 all complete.** Findings:

* **The map's partition rule was wrong.** It specified `md5(tenant_id) % K`
  (fixed cell *count*); the measured rule is fixed cell *size* — order by
  md5 and chunk into 32 (`planner_cells_dealias.hash_cells`,
  `planner_cells.CELL_SIZE`). Different memberships. Shipped the measured one;
  a mutation test now fails if modulo is reintroduced.
* **The wall reproduces on the shipped path at the same width.** Monolithic
  p95 3180 ms at 128 tenants (first over the 3 s timeout) and 10171 ms at 256
  — past the whole 10 s control period. Per-cell p95 flat at 272–378 ms.
  `research/analysis/CELLS_SHIPPED.md`, engineering note, no prereg (nothing
  thesis-scored changed).
* **Off by default**, and the chart omits the env var rather than setting 0,
  because `envInt32` exits on non-positive so "disabled" must be absence.
* Ceilings apportioned by largest remainder so per-cell limits sum to exactly
  the cluster ceiling (over-sum would let cells jointly overcommit).

### Superseded plan (kept for context)

### T5 — operator-side cell fan-out (smallest honest implementation)
DO: in `internal/operator/controllers` PlanRunner: new env
`POLYFORGE_PLAN_CELLS` (default **1** = exactly today's single call —
bit-compatible). When K>1: partition tenants by `md5(tenant_id) % K`
(**hash, not round-robin** — round-robin's ΔJain −0.089 is a measured
aliasing artifact), issue one `/v1/plan` call per non-empty cell, merge the
plan maps. For per-cell `Limits`, read how `PLANNER_CELLS.md`'s measured
protocol split cluster ceilings and mirror it exactly. Planner service
unchanged (each cell call ~195 ms; 32 sequential calls fit the 10 s
cadence; per-cell locks in the planner only if T6 measurement demands).
Helm: `planner.cells` value → the env. Tests: K=1 ≡ current behavior,
partition determinism, merge correctness, limits split.
VERIFY: R3 green; new Go tests green.

### T6 — scale verification on the shipped path
DO: local test driving 128 and 256 synthetic tenants through
PlanRunner→real planner process at K per `PLANNER_CELLS.md`'s cell sizing;
record per-call p95 and total cycle time in a new engineering note
`research/analysis/CELLS_SHIPPED.md` (note, not campaign — no prereg
needed per R2 since nothing thesis-scored changes).
DONE-WHEN: per-cell p95 in the ~195 ms class, cycle < 10 s at 256T,
numbers committed.

### T7 — honest scoping docs
DO: one paragraph each in `values.yaml` comment + `ARCHITECTURE.md`:
fairness under cells is **per-cell, not global** (measured cost ΔJain
−0.0053, `PLANNER_CELLS_DEALIAS.md`). Flag for the author's manuscript text.

## Phase 3.5 — Transactions strengtheners (desk, parallel; added 2026-08-05)

Desk-doable, no gate. Touch a disjoint part of the tree from B1's live path, so
they run in parallel with Phase 2. Detail + rationale in
`docs/MAIN_WORKING_PATH.md` §3.

### T17 — formal SLO guarantee (the TPDS strengthener) — main path M3
DO: over the MPC (`research/jcac_sim/controller.py`, actuation clamps already
pinned by `test_invariants.py`): (1) formalize the bounded per-interval demand
disturbance from the workload model; (2) construct a terminal invariant set as
an executable predicate; (3) prove recursive feasibility as a checkable
condition and derive the SLO-violation bound; (4) ship an invariant/feasibility
checker + a validation script that replays every closed campaign and asserts
measured violation ≤ the derived bound.
VERIFY: checker + validation green on all campaigns; **mutation-test the
checker** (widen the disturbance bound ⇒ recursive-feasibility must fail, per
the T3 lesson); R3/R4 green; default OFF/no-op (bit-identical).
DONE-WHEN: bound derived, checker committed, validation green, `DEFENSE_QA`
entry recorded. Theorem/proof PROSE is author-owned (R8).
RISK: must be a real proof, not a heuristic — the accept-vs-major-revision
line. If the terminal set can't be built cleanly over the actual lattice,
report the obstruction; do not paper over it.

### T18 — energy/carbon + admission control (optional strengthener) — main path M4
DO: add a carbon/energy term and/or a bursty-load admission-control arm as an
additional constraint/dimension, **off by default** (R4). Citable against the
IEEE survey's stated open list. Needs its own prereg only if scored (R2).
VERIFY: R3/R4 green, bit-identical with the feature off.
DONE-WHEN: feature committed off-by-default with tests. Low priority.

> **Out of scope here:** the leakage-budget controller (TDSC) is spun off to a
> separate project by author decision — see `docs/MAIN_WORKING_PATH.md` §SIDE
> TRACK and `PolyForge_Research_Gap_Analysis.docx`.

## Phase 4 — multi-node live `[GATE:user, ~€50–150]` (max-probability tier; skippable only if accepting lower odds)

### T8 — cluster up: `terraform apply` in `eval/infra/terraform` (Hetzner).
### T9 — run: 32-tenant live slice + live chaos + matrix p99, per
`docs/WAVE3_LIVE_RUNBOOK.md` patterns; R2 preregs before any scored run;
R5 + R6 mandatory.
### T10 — `terraform destroy` (always, same sitting).

## Phase 5 — user-only mechanics (parallel track, any time)

- T11 Rotate Kaggle + HF tokens (kaggle.com/settings; HF settings).
- T12 OSF: submit frozen preregs, record DOIs in `OSF_REGISTRATION.md`.
- T13 Zenodo: `cd eval && python scripts/archive_zenodo.py` → upload →
  publish → DOI.
- T14 GHCR images + Artifact Hub + Pages supplement
  (`docs/RELEASE_CHECKLIST.md`).
- T15 Manuscript: carve into `research/paper/main.tex` (compiling FGCS
  scaffold); split the security result into its own paper.
- T16 Submit: arXiv preprint → FGCS or IEEE TCC (Track A). Cover letter
  cites the artifact (Zenodo DOI), 26 preregs (OSF DOIs), and the
  one-command reproduction.

## Order summary

**Done:** T0–T3 (Phase 0 security + Phase 1 artifact integrity), pushed
through 608eb5b.

**Next:** T4 (B1). The $20-60 gate is gone -- `docs/WAVE4_FREE_ROUTE.md`
runs it at $0 on Kaggle + Codespaces, and T4a checks the path is good enough
before any GPU hour is spent. Run it the moment the author has an hour free — it preempts everything,
because T5 modifies the operator→planner path B1's frozen WL-H2 preflight
was verified against. If the gate stays shut, T5–T7 can proceed first, but
then re-run the WL-H2 preflight before T4.

Then T8–T10 if maximum acceptance probability is wanted (Track B).
T11–T16 run on the author's clock in parallel and none of them block T4–T10.
**T17 (formal guarantee) runs in parallel with everything** — it is the primary
Transactions/TPDS strengthener and touches only `research/jcac_sim/`. T18
optional.
Submission-ready = T0–T7 + T11–T16; **Transactions-strength = add T17**;
maximum-probability = add T8–T10.
