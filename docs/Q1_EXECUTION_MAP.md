# Q1 execution map — terse runbook

Purpose: every remaining weakness, as executable steps. Written so any agent
can follow it without re-deriving context. Rationale lives in
`docs/Q1_ROADMAP.md` (v2); this file is the *what to do*. No submission is
guaranteed acceptance; completing ALL tasks (incl. Phase 4) before
submitting to FGCS/TCC/TSC (formal Q1) maximizes the probability.

Execute tasks in ID order. `[GATE:user]` tasks wait for the user; skip and
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
- **R8** Thesis/manuscript prose is user-owned. Agents do engineering only.

## T0 — commit session-31 security work `[GATE:user approval]`

DO: `git add -A`, commit ("Security hardening: planner bearer auth,
x/text CVE fix, govulncheck CI gate; Q1 roadmap"), push.
DONE-WHEN: `git status` clean, CI green.

## Phase 1 — artifact integrity (desk, no gate, do now)

### T1 — CSV fallback loaders (7 analysis scripts)
Scripts whose `load()` is duckdb-only: `analysis_chaos.py`,
`analysis_concurrency.py`, `analysis_econ.py`, `analysis_learned.py`,
`analysis_risk.py`, `analysis_tenant_scale.py`, plus
`analysis_risk_budget.py` (its main DB is duckdb-only too). All in
`research/analysis/`.
DO: in each, when the `.duckdb` is absent, load the committed export
instead — mirror the existing fallback pattern in `scripts/reproduce.py`
(`load_runs` → `eval/results/metrics_*.csv.gz`). File map: chaos→
`metrics_chaos_sim`, concurrency→`metrics_matrix_concurrency`, econ→
`metrics_matrix_gpu_econ`, learned→`metrics_matrix_learned`, risk→
`metrics_matrix_risk`, risk_budget→`metrics_matrix_risk_budget`,
tenant_scale→`metrics_matrix_scale32`+`scale64`.
VERIFY: per script — temporarily rename its `.duckdb` away, run with
`POLYFORGE_ANALYSIS_OUT=<scratch>`, diff regenerated record vs committed:
**byte-identical required**. If float-precision digits differ: stop,
report (R1 forbids rewriting the committed record). Restore the `.duckdb`.
DONE-WHEN: 7/7 byte-identical from CSV; R3 green.

### T2 — reproduce.py covers everything
DO: add the T1 campaigns' records and `fig18_risk_frontier` +
`fig19_risk_budget_frontier` to `scripts/reproduce.py`'s rebuild set.
VERIFY: `python scripts/reproduce.py` on a tree without any `.duckdb`
reports **20/20 records MATCH, 19/19 figures**; CI `reproduce` job green.

### T3 — controller invariant property tests (tests ONLY, no behavior change)
DO: new `research/jcac_sim/test_invariants.py`. Over a seeded grid of
states/demands, for `JCACController` with `anchor_moves` True AND False,
assert every emitted plan: (a) per-interval move clamps honored **relative
to the prior config** (|Δreplicas| ≤ clamp, ≤1 cache level — the class of
defect the Wave-5 audit caught post-publication); (b) bounds:
`replica_min ≤ r ≤ replica_max`, cache within limits, tier ∈ `TIERS`;
(c) determinism: same seed twice → identical plans.
VERIFY: pytest green; R3 green (proves zero behavior change).

## Phase 2 — B1 live three-knob plane `[GATE:user opens GPU host, $20–60]`
**Runs BEFORE Phase 3** (Phase 3 modifies the code path B1's preflight
verified).

### T4 — execute B1
DO: follow `research/analysis/PREREG_WAVE4_LIVE_PLANE.md` §Substrate +
§Status update exactly; apply R5 + R6. Run the **WL-H2 preflight
knob-liveness gate first** — if any knob is inert live, STOP (an inert-knob
run VOIDS WL-H1; do not fake it, report). VRAM short → drop the 7B tier
(prereg's amendment rule). Run the frozen matrix once (stopping rule).
Publish outcome **as measured, PASS or FAIL**, via a new analysis script →
new RESULTS file; reconcile `RESULTS_MASTER.md`, `DEFENSE_QA.md`,
`REMAINING_WORK.md`. Never edit the prereg (R1).
DONE-WHEN: RESULTS committed + ledgers reconciled.

## Phase 3 — ship planning cells (desk; AFTER T4, or user says go early)

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
−0.0053, `PLANNER_CELLS_DEALIAS.md`). Flag for the user's manuscript text.

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

Now: T0 → T1 → T2 → T3. The moment the GPU gate opens: T4 (preempts
everything). Then T5–T7. Then T8–T10 if maximum probability is wanted.
T11–T16 run on the user's clock in parallel. Submission-ready =
T0–T7 + T11–T16 done; maximum-probability = add T8–T10.
