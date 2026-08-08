# Publication roadmap — the mechanical execution route to Q1/Transactions

Written session 35 (2026-08-08), after the V-series validity remediation
closed all 21 audit defects. **This document is written to be executed by a
future session without re-deriving anything.** Follow it top to bottom. Where
a step depends on a claim that was never independently verified, the work
package says VERIFY FIRST and gives the command.

> Relationship to the other route docs: `docs/MAIN_WORKING_PATH.md` is the
> milestone-level story (M1–M6). `docs/REMAINING_WORK.md` is the owner-split
> ledger. **This file is the execution level**: work packages with exact
> files, commands, frozen designs, and definitions of done. When they
> disagree, the most recently updated wins; update all three when a WP lands.

---

## §0 How to use this document (cold-session bootstrap)

1. `cd "c:/Users/DARKR/Documents/Thesis Project/polyforge"` — Windows box,
   Git Bash + PowerShell, **no Docker locally**, Python 3.13, Go per go.mod.
2. `git log --oneline -8` — you should see the session-35 V-series commits
   (`ec7a230..9f66192`) on branch `v-series-validity-remediation`. If the
   branch was merged/pushed since, fine; the WPs below are independent of
   branch topology.
3. Run the **gate battery** (§0.1) BEFORE touching anything, to prove the
   baseline is green. Run it again AFTER every work package. Never commit on
   a red battery.
4. Execute work packages in numeric order unless their Preconditions say
   otherwise. One WP = one prereg (if it measures anything) = one commit
   (or a small series) = gates green = update the three route docs.
5. **Cardinal rules** (project-wide, non-negotiable):
   - **R1**: pre-registrations are frozen and committed BEFORE the campaign
     runs. Push immediately after committing a prereg — the push event is
     the timestamp anchor. (Session-35 lesson: two preregs ran with the
     commit-only anchor because the branch was never pushed. Do not repeat.)
   - **R4**: committed campaigns replay bit-identically. After ANY change to
     `research/jcac_sim/`, `eval/harness/`, or an analysis script, run
     `python scripts/reproduce.py` — it must report **all records
     byte-identical, exit 0** (19/19 at time of writing; grows as WPs land).
   - **Honest reporting**: every hypothesis is reported PASS or FAIL
     whichever way it lands. A FAIL is a publishable outcome and is written
     up as the headline finding of its record. Never edit a published
     record; supersede it with a new adjudication.
   - New mechanisms default OFF / no-op so published arms replay unchanged.

### §0.1 The gate battery (run before and after every WP)

```bash
cd "c:/Users/DARKR/Documents/Thesis Project/polyforge"
gofmt -l . # must print nothing
go build ./... && go vet ./...
go test ./... -count=1                          # all pass
KUBEBUILDER_ASSETS="C:/Users/DARKR/.local/share/kubebuilder-envtest/k8s/1.31.0-windows-amd64" \
  go test -tags envtest ./internal/operator/controllers/ -run Envtest -count=1   # 3/3
(cd research/jcac_sim && python -m pytest -q)   # 137+ pass
(cd services/planner  && python -m pytest -q)   # 35+ pass
(cd research/analysis && python -m pytest test_stats.py -q)  # 6+ pass
(cd eval && python -m pytest tests -q)          # 99+ pass
python scripts/reproduce.py                     # ALL records byte-identical, exit 0
```

If envtest binaries are missing (fresh machine):
`go run sigs.k8s.io/controller-runtime/tools/setup-envtest@latest use 1.31.0 --bin-dir "$HOME/.local/share/kubebuilder-envtest"`.

### §0.2 The prereg template (mirror `PREREG_EVICTION_PARITY.md` exactly)

Sections, in order: title (`# Pre-registration: <name> (V-series, adjudicates …)`);
registration line with date + "committed and pushed before any run";
`## Question (and provenance of the finding)` — cite file:line;
`## Design (frozen)` — arms table, cells, metrics, the ONE factor that changes;
`## Hypotheses (frozen)` — ids, directions, tests, margins, Holm family
(`stats.holm_bonferroni`); `## Outcome handling and stopping rule` — runs
once, FAIL is publishable, R4 must hold, record joins `reproduce.py`.

### §0.3 Definition of done, per work package

- [ ] Prereg frozen + committed + **pushed** before any run (campaign WPs)
- [ ] Implementation + tests (new behavior default-off where R4 demands)
- [ ] Campaign run once over the frozen matrix; `validate()` report ok
- [ ] `eval/scripts/export_metrics_csv.py <spec>` → committed csv.gz
- [ ] Analysis script writes the record via `stats.record_path()` (NEVER a
      hardcoded path — session-35 lesson: a hardcoded path made the gate
      overwrite the file it was checking)
- [ ] Record registered in `scripts/reproduce.py` `CAMPAIGN_RECORDS`
- [ ] Clean-clone check: hide the DuckDB, rebuild from csv.gz, byte-identical
- [ ] Full gate battery green
- [ ] `MAIN_WORKING_PATH.md` §0b, `REMAINING_WORK.md`, and this file's §6
      progress table updated
- [ ] Committed with a message that states finding + mechanism + gates

---

## §1 State snapshot (2026-08-08, end of session 35)

- All 21 audit defects closed. Gate battery fully green: Go suite, envtest
  3/3 vs real kube-apiserver 1.31.0, 137 sim / 35 planner / 6 stats / 99
  eval tests, `reproduce.py` 19/19 records + 19/19 figures byte-identical.
- **Headline adjudicated**: `RESULTS_EVICTION_PARITY.md` — published −36.0%
  vs `hpa` reproduces, but vs `hpa_fair` (no 1.4581× LRU charge, cache
  pre-sized 512 MB) it is **+0.8% = cost-neutral (EP-H1a FAIL)**. Survives:
  −14.1% vs `keda_fair` (p 3.8e-16); **~half the SLO overshoot** on
  unbounded `mean_excess` (0.2143 vs 0.4288, p 6.4e-12); −14.2% on
  `ai_cacheable`. `crud_bursty` +107.5% (explicit weakness).
- **Fairness confound adjudicated immaterial**: `RESULTS_ORDER_PERMUTATION.md`
  — largest Jain excursion 0.0001 vs pre-registered 0.01; published results
  stand; `tenant_order_seed` is now an explicit parameter.
- **7 commits local-only on `v-series-validity-remediation`, NOT pushed.**
- Raw traces ARE on disk: `research/traces/data/BurstGPT_{1,2,3}.csv`,
  `research/traces/data/azure-llm/`, `lmsys-chat-1m/`.

## §2 Dependency graph and venue map

```
WP1 trace parity ──┐
WP2 MASTER fix ────┤
WP3 layered fix ───┤──► FGCS (Q1) evidence-complete ─┐
                   │                                  ├─(+ WP9 reframe)─► submit
WP7 push ──────────┤                                  │
WP8 B1 live ───────┴──► TCC / TSC (Transactions) ─────┘
WP4 cells + WP5 O(N²) + WP6 mismatch ──► TPDS additionally
```

Execute order: **WP7 (2 min, do it first — it fixes the anchor weakness for
everything after) → WP1 → WP2 → WP3 → WP4 → WP5 → WP6 → WP8 → WP9–11.**

---

## §3 Track 1 — desk work packages (agent-executable, $0)

### WP1 — Trace-replay eviction parity (adjudicates the −70% / −42% headlines)

**Why.** The scoreboard's strongest real-data claims —
`RESULTS_TRACE2.md` (BurstGPT: −70% vs tuned HPA/KEDA/FIRM, n=96 windows)
and `RESULTS_TRACE_AZURE.md` (−42%/window, n=72) — were scored by
`research/analysis/trace_matrix2.py` / `trace_matrix_azure.py`, which apply
`miss_cost_factor=lru_miss_cost_factor()` (=1.4581) to every baseline arm
(see `trace_matrix.py:126` for the shared pattern; verified session 35).
`RESULTS_EVICTION_PARITY.md` proved that charge accounts for ~37 pp of the
synthetic headline. The trace headlines have never been re-scored fairly.

**Preconditions.** WP7 (push) done. Traces on disk (verify:
`ls research/traces/data/BurstGPT_1.csv research/traces/data/azure-llm`).

**Steps.**
1. VERIFY FIRST (30 min): read `trace_matrix2.py` and `trace_matrix_azure.py`
   end to end. Confirm: (a) how arms are declared (`SYSTEMS_UNDER_TEST =
   ["jcac","jcac_v2","jcac_seasonal","hpa","keda","firm"]`), (b) that
   `spec.lru_eviction` drives the factor, (c) whether `simulate.run`'s
   result fields `mean_excess` / `tier_none_step_share` (added session 35)
   are captured in the per-window rows — if not, capture them; they are
   needed for TP-H3. (d) how `hpa_fair` would enter: the scripts build arms
   from `harness.systems.SYSTEMS`, and `hpa_fair`/`keda_fair` already exist
   there with `lru_eviction=False, static_cache_mb=512` — so adding them to
   `SYSTEMS_UNDER_TEST` should be sufficient. Confirm `static_cache_mb`
   reaches `simulate.run(initial_cache_mb=…)` on this path the same way
   `sim_backend.execute` does; if trace_matrix calls `simulate.run` directly
   (it does), thread `initial_cache_mb=spec.static_cache_mb` at its call
   site, mirroring `eval/harness/sim_backend.py`.
2. Freeze `research/analysis/PREREG_TRACE_PARITY.md` (§0.2 template).
   Design (one factor: eviction pricing + cache posture, exactly as
   EVICTION_PARITY): arms = published six UNCHANGED + `hpa_fair` +
   `keda_fair`; same windows, same seeds, same paired-by-window tests as
   PREREG_TRACE2 §3; metrics = published + `mean_excess` +
   `tier_none_step_share`; eviction sensitivity band (none 1.0 / gdsf
   0.9784 / arc 1.3371 / lru 1.4581 from
   `harness.systems.eviction_sensitivity_band()`) reported as a band on the
   headline delta. Hypotheses (frozen, Holm family of 4 via
   `stats.holm_bonferroni`):
   - TP-H1a/b (primary): jcac cost < hpa_fair / keda_fair, one-sided paired
     Wilcoxon over windows, per trace. *Directional prediction: the −70%
     shrinks materially; direction unknown — that is the point.*
   - TP-H2: published-vs-fair swing > 10 pp on each trace (descriptive).
   - TP-H3: `mean_excess` non-inferiority vs `hpa_fair`, margin 0.05.
   Both traces in ONE prereg, scored per-trace (they are replications, not
   a pooled family).
3. Commit + push the prereg. Then implement step 1's wiring (tiny diff),
   run the unit gates, and run both campaigns:
   `cd research/analysis && python trace_matrix2.py` then
   `python trace_matrix_azure.py` — CHECK how they persist runs (they write
   window-level rows; mirror the existing output path convention). Expect
   hours, run in background, workers per script default.
4. Write `analysis_trace_parity.py` mirroring `analysis_eviction_parity.py`
   (loader with DuckDB/csv fallback if applicable — if trace_matrix writes
   plain CSVs, read those; row order is part of the contract), producing
   `RESULTS_TRACE_PARITY.md` via `record_path()`. Generated interpretation
   section computed from the numbers (mirror the EP script's pattern), with
   the prereg's FAIL-is-the-headline discipline.
5. Register in `reproduce.py`, run definition-of-done checklist (§0.3).

**Contingencies.** If TP-H1 FAILS on both traces (jcac not cheaper than a
fairly-configured reactive baseline on real demand): that is the headline of
the record, the scoreboard's Cost row is restated in WP2 as
"parity-at-lower-severity on real traces", and the paper's framing (WP9)
leans on severity + keda + fairness. Do NOT soften the record.
**Effort:** ~half day + campaign hours.

### WP2 — RESULTS_MASTER reconciliation (mechanical part)

**Why.** `research/analysis/RESULTS_MASTER.md` still reads "Cost WON −43…−48%",
"−joint control +2884% cost", "−cost-aware eviction +35.7%" with no mention
of the V-series adjudications. Side by side with `RESULTS_EVICTION_PARITY.md`
this is a self-contradiction a reviewer finds in minutes.

**Steps.** (No prereg — no new measurement. Mechanical cross-referencing.)
1. Scoreboard Cost row: append the adjudication sentence — published deltas
   reproduce vs published comparators; vs `hpa_fair` (fair accounting +
   pre-sized cache) the synthetic headline is +0.8% (EP-H1a FAIL); surviving
   claims: −14.1% vs keda_fair, ~half `mean_excess`, −14.2% ai_cacheable.
   Cite `RESULTS_EVICTION_PARITY.md`. After WP1: same treatment for the
   trace rows, citing `RESULTS_TRACE_PARITY.md`.
2. SLO row: add that `mean_violation` saturates and `mean_excess` shows the
   advantage UNDERSTATED (0.2143 vs 0.4288) — this row gets STRONGER.
3. Fairness row: add the ORDER_PERMUTATION one-liner (immaterial, stands).
4. Ablation section: annotate "+2884%" as resting on the layered baseline's
   absorbing-tier defect, superseded by WP3's re-run when it lands.
5. `tier=none` step-share disclosure line (7.3% jcac / 0.0% reactive).
6. Do NOT touch any generated record. MASTER is hand-curated — that is why
   this is allowed. Keep edits additive + dated ("Adjudication, session 35:").
**Effort:** ~1 hour. **The narrative reframe stays user-owned (WP9).**

### WP3 — Fix the layered/gptcache absorbing-tier baseline; re-run the ablation

**Why.** `research/jcac_sim/baselines.py:162-166` and `:310-314` (verify
lines before editing) escalate tier on `ai_p95 > target` and de-escalate
only below `0.3 × target`. For `agent` traffic `TIER_BASE_LATENCY_MS` is
non-monotonic (small 6000 / mid 2500 / large 3500), so `large` is slower AND
10× dearer than `mid`, and its minimum achievable p95 (≈4900 ms) can never
fall below the de-escalation threshold (0.3 × 6250 = 1875) → absorbing
state. This single latch generates "−joint control = +2884%" and most of
"−94.7% vs gptcache". A baseline with an absorbing state at the 100×-price
tier is not a competent implementation.

**Steps.**
1. VERIFY FIRST: reproduce the audit's tier histogram on one cell
   (`layered` on `agentic`: expect ~{small:3, mid:97, large:860}). One
   throwaway script, do not commit it.
2. Freeze `PREREG_LAYERED_FIX.md`. One factor: the escalation rule. Fixed
   design: rank tiers by measured `TIER_BASE_LATENCY_MS` for the tenant's
   dominant AI kind (not by name), escalate one rank toward faster,
   de-escalate when the projected p95 at the cheaper rank would hold with
   margin (use `evaluate_step` projection — same information HPA-style
   baselines already get). The published `layered`/`gptcache` arms stay
   UNCHANGED; add `layered_v2` / `gptcache_v2` arms (R4).
   Hypotheses: LF-H1 the joint-control ablation delta vs `layered_v2` is
   < +2884% (trivially expected — the honest number is the point); LF-H2
   jcac still beats `gptcache_v2` on J (direction unknown, report as lands).
3. Implement `layered_v2`/`gptcache_v2` in `baselines.py` + registry entries
   in `eval/harness/systems.py` + tests (absorbing state gone: from `large`
   under `agentic`, controller must de-escalate within N steps once load
   drops). Spec `eval/experiments/matrix_layered_fix.yaml` over the
   ablation cells. Run, analyze (`analysis_layered_fix.py` →
   `RESULTS_LAYERED_FIX.md`), register, checklist.
**Effort:** ~half day + campaign hours.

### WP4 — Planning cells vs forecast state (audit C6) — VERIFY, then fix or adjudicate

**Why.** The architect-agent audit claimed: with planning cells enabled
(`PLANNER_CELLS.md` campaign, PS-H1 1024-tenant scaling), each re-partition
wipes per-tenant forecast history, degrading the forecaster to persistence —
which would gut the TPDS scalability story. **This claim was NEVER
independently verified** (session-35 greps found no "cell" in
`services/planner/planner.py` or `controller.py`).

**Steps.**
1. VERIFY FIRST (this IS the work): find the cells implementation.
   `grep -rn "cell" --include="*.py" research/ eval/ services/ | grep -v __pycache__`
   and read `research/analysis/PLANNER_CELLS.md` + its campaign script to
   see what "cells" concretely is (it may live in the scaling campaign
   script, partitioning tenants across JCACController instances).
2. Then ONE of:
   - **Defect confirmed** → fix: forecast state must be keyed by tenant id
     and survive re-partitioning (carry `Forecast` objects across cell
     assignment, or persist/restore via the existing `snapshot()`/restore
     path from W31). Regression test: run N steps with cells on vs off on
     identical demand → per-tenant forecast history identical. Re-run the
     PS-H1 slice; write `RESULTS_CELLS_FIX.md` showing the forecaster
     survives partitioning.
   - **Claim wrong** → write the adjudication into `REMAINING_WORK.md`
     (C6 section): what the audit claimed, what the code actually does,
     evidence (file:line + a demonstration run). Close it. Do not fix
     what is not broken.
**Effort:** verify ~1 h; fix path ~half day + slice re-run.

### WP5 — Remove the O(N²) planner wall (audit H1) — memoize, R4-gated

**Why.** The audit reported the per-cycle lattice search re-evaluates
`evaluate_step` for identical (config, state, demand) tuples across sweeps
and candidates, making cost quadratic-ish in practice and motivating the
cells workaround. Removing the self-inflicted wall makes the 1024-tenant
claim native. **Also unverified in detail — profile first.**

**Steps.**
1. VERIFY FIRST: profile one large cell
   (`python -m cProfile -s cumtime` on a 64-tenant, 120-step sim run);
   confirm `evaluate_step` call count and its share of runtime. Record the
   numbers in the eventual commit message.
2. Implement memoization INSIDE one plan cycle only (dict keyed on the
   projection inputs; cleared per cycle — no cross-cycle state, so no
   behavioral change is possible in principle). No flag needed IF R4 proves
   bit-identity; otherwise ship behind `memoize_projection=False` default.
3. THE GATE IS R4: `python scripts/reproduce.py` must stay all-records
   byte-identical with memoization on the replay path. If it drifts even
   one byte, the cache key is wrong — fix or abandon; do not ship a
   default-on drift.
4. Measure and record the speedup (same profile, after). Add a wall-clock
   line to `PLANNER_SCALING.md`'s successor or the commit message.
**Effort:** ~half day.

### WP6 — Model-mismatch campaign (audit H8): the MPC with a wrong plant

**Why.** `controller.py::_project` calls the same `evaluate_step` the engine
scores with; with realism flags off, the controller's model is bit-identical
to the plant. Every sensitivity campaign moves world and beliefs together.
For an MPC paper this is the first structural question a TPDS/TCC reviewer
asks, and today there is no experiment where the controller believes wrong
constants while the world keeps the published ones.

**Steps.**
1. Design the belief-decoupling mechanism (implementation before prereg is
   fine — the MECHANISM is engineering; the CAMPAIGN is what gets frozen):
   add `belief_scale: dict | None = None` to `JCACController.__init__`
   (e.g. `{"tier_cost": 1.5, "cache_half": 0.5, "replica_capacity": 0.8}`).
   Inside `_project` ONLY, apply the scales to a local view of the
   constants (cleanest: a scaled copy of the projection inputs — scale the
   projected demand's work-units for `replica_capacity`, scale the
   cost terms after `evaluate_step` for `tier_cost`, and for `cache_half`
   pre-scale the cache_mb the projection *believes*). None = published
   behavior bit-identical (R4). Unit tests: belief_scale=None is a no-op;
   each scale moves the projection the expected direction; the SCORING path
   never sees the scales.
2. Freeze `PREREG_MODEL_MISMATCH.md`. Design: jcac with belief_scale swept
   over {0.5, 0.75, 1.0 (control), 1.25, 2.0} on each of the three
   constants separately (one factor at a time), vs `hpa_fair`/`keda_fair`
   (fair accounting — this campaign inherits WP1's standard), headline
   matrix cells at `medium`, 5 reps. Hypotheses:
   - MM-H1: at ±25% mismatch on every constant, jcac retains its keda_fair
     cost win (the surviving headline claim is mismatch-robust).
   - MM-H2: `mean_excess` non-inferiority vs hpa_fair holds at ±25%.
   - MM-H3: degradation is graceful — J(mismatch) monotone in |scale−1|,
     no cliff (descriptive, plotted).
3. Run once, `analysis_model_mismatch.py` → `RESULTS_MODEL_MISMATCH.md`,
   register, checklist. *This record is the direct answer to "why should I
   believe an MPC that was tuned on its own simulator" — quote it in WP9.*
**Effort:** ~1 day + campaign hours.

---

## §4 Track 2 — user-gated (agent prepares, user opens the gate)

### WP7 — PUSH THE BRANCH (do this before anything else, 2 minutes)

```bash
cd "c:/Users/DARKR/Documents/Thesis Project/polyforge"
git push -u origin v-series-validity-remediation
```
Then (agent, after push): append a one-line disclosure to
`RESULTS_EVICTION_PARITY.md` + `RESULTS_ORDER_PERMUTATION.md`… **NO — those
are generated records under the reproduce gate.** The disclosure goes in the
PREREGS' status lines? Also no — preregs are frozen. Correct mechanism: add
the disclosure to `REMAINING_WORK.md` §session-35 and to WP2's MASTER notes:
*"The two session-35 preregs were committed before their runs but pushed
only afterwards (single-machine session); the anchor for both is the commit
hash, not the push event."* Honest, permanent, does not touch frozen files.

### WP8 — B1: the three-knob live plane (the Transactions gate)

Everything is prepared; every session-35 fix below was a prerequisite:
demand signal live (`RPSWindow` + kinds + 10 s window + truncation error),
SLO classes provisioned, WL-H2 preflight EXECUTED in-harness, k6 delivery
thresholds, sampler coverage, substrate-scoped resume, CEL-validated pins.

Substrate (all free): `docs/WAVE4_FREE_ROUTE.md` — Kaggle P100 (30 GPU-h/wk,
tier bench already proven there) runs `kaggle_tier_server.py` → tunnel URL →
`POLYFORGE_EVAL_TIER_BACKENDS`; Codespaces (Education: 180 core-h/mo) runs
the kind cluster + harness.

Order INSIDE the sitting (from `PREREG_WAVE4_LIVE_PLANE.md` §Status):
1. **Live actuation dry-run** of all four arms (deferred from M1): each arm
   renders, CRs admit, operator actuates, knobs visibly move. Non-scored.
2. `knob_preflight.py` WL-H2 gate — now runs automatically inside
   `execute()`; an inert knob RAISES and voids the run. **If it raises:
   report SUBSTRATE INADEQUATE; WL-H1 is void; do NOT fake or lower
   margins.**
3. The frozen 4 arms × 4 cells matrix, `--workers 1` (enforced), once.
4. Export csv.gz, write the RESULTS record against the frozen prereg,
   register in `reproduce.py`, full checklist.

---

## §5 Track 3 — user-owned (writing + accounts; agent does NOT do these)

- **WP9 Reframe** thesis + `research/paper/main.tex`: the claim is now
  *"joint control at cost parity with a competently-configured reactive
  autoscaler, with ~half the SLO overshoot; cheaper than event-driven
  scaling; advantage concentrated where cache/tier are load-bearing"* +
  the validity-methodology story (self-audit found and published its own
  confound: EVICTION_PARITY + ORDER_PERMUTATION + [WP1/WP3/WP6 records]).
  Sources: MAIN_WORKING_PATH §0b, the V-series records, WP2's MASTER.
- **WP10** RB-H1 family decision (p=0.0073: survives Holm in its own
  6-hypothesis family at 0.05/6=0.00833; fails vs all 48 at 0.00104).
  Decide which family it belongs to; state it in the paper's stats section.
- **WP11** Bucket C mechanics: OSF submit (paste frozen preregs, record
  DOIs), Zenodo deposit + DOI, title-page macros (real name/roll/supervisor
  — never fabricated), human PDF proofread, arXiv + venue submission.

## §6 Progress table (update as WPs land)

| WP | Status | Evidence |
|---|---|---|
| WP1 trace parity | NOT STARTED | — |
| WP2 MASTER reconcile | NOT STARTED | — |
| WP3 layered fix | NOT STARTED | — |
| WP4 cells verify/fix | NOT STARTED (claim UNVERIFIED) | — |
| WP5 O(N²) memoize | NOT STARTED (profile first) | — |
| WP6 model mismatch | NOT STARTED | — |
| WP7 push | **WAITING ON USER** | — |
| WP8 B1 live | WAITING ON USER (gate) — prereqs all landed session 35 | — |
| WP9–11 | user-owned | — |

## §7 Risk register

| Risk | Response (decided NOW, not after seeing data) |
|---|---|
| WP1: trace headlines reverse vs fair arms | Headline of the record; scoreboard restated (WP2); paper leans severity+keda+fairness (WP9). Not softened. |
| WP3: jcac loses to `gptcache_v2` on J | Reported as-is; the joint-ablation claim narrows to the honest delta. |
| WP4: audit claim false | Adjudicate + close in writing; no code churn. |
| WP5: memoization drifts one byte | Do not ship default-on. Fix key or ship default-off arm. |
| WP6: advantage collapses at ±25% mismatch | Publish the boundary; it becomes the "when does this controller apply" section — a contribution, not an embarrassment. |
| WP8: WL-H2 inert knob | SUBSTRATE INADEQUATE, WL-H1 void, report honestly, fix substrate, re-sit. |
| Any record drifts under reproduce.py | Stop. Find cause. Never re-freeze a prereg to match an outcome. |
