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
   Git Bash + PowerShell, Python 3.13, Go per go.mod. **Docker IS available
   locally since session 36** (Desktop 4.85.0 + WSL2; the daemon is often
   not running — start Docker Desktop and if it fails check
   `com.docker.service` is Automatic+Started). kind/helm/k6/kubectl on
   PATH. Sizing: 9.7 GB via `~/.wslconfig`; `small`/`medium` kind clusters
   fit, `large` does not.
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

**Docker-dependent gates (available since session 36 — run these too when
touching storage or the HTTP surface):**

```bash
# PostgreSQL RLS / tenant isolation. These SKIP silently without the env,
# and a skip prints `ok` — always set it explicitly before claiming RLS.
./scripts/pg-test-up.sh                      # starts PG, prints the exports
eval "$(./scripts/pg-test-up.sh --env)"
go test ./internal/storage/postgres/ -count=1   # expect 3/3 PASS
./scripts/pg-test-up.sh --down

# OWASP ZAP against the real API surface. Use --api; the plain baseline
# spiders a JSON API with no root route and "passes" against 2x 404.
docker compose up -d control-plane postgres redis
./scripts/zap-baseline.sh --api              # expect 118 PASS / 0 FAIL
docker compose down
```

PATH note: `kind`/`helm` live under
`%LOCALAPPDATA%\Microsoft\WinGet\Packages\...`. A shell opened before they
were installed has a stale PATH and `preflight()` will report them missing —
re-resolve from Machine+User PATH or open a new shell.

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
- **12 commits local-only on `v-series-validity-remediation`, NOT pushed.**
- Raw traces ARE on disk: `research/traces/data/BurstGPT_{1,2,3}.csv`,
  `research/traces/data/azure-llm/`, `lmsys-chat-1m/`.

### Session 36 addendum (2026-08-09) — Docker is available locally

`ec7a230..9f66192` (V-series) plus `992f8d7` (this roadmap), `cfeca12`
(gateway security hardening), `849c478` (security residuals), `2db9c3e`
(Docker enablement).

- **Docker Desktop 4.85.0 / engine 29.6.2 + WSL2 work on this machine.** The
  blocker was `com.docker.service` shipping as `DEMAND_START` while running
  as LocalSystem — it never started, so the engine could not provision its
  WSL distro. Set to Automatic. **Check that service first** if Docker ever
  fails to start again.
- `kind`, `helm`, `k6` installed (**`GrafanaLabs.k6`**, not `k6.k6`);
  `kubectl` ships with Docker Desktop. `cluster_backend.preflight()` reports
  **no missing tools**. A kind cluster was created, scheduled a pod and torn
  down cleanly. → **WP8a is now desk-doable** (see the split below).
- `~/.wslconfig`: Docker 7.6 GB → **9.7 GB**, 8 CPU, 4 GB swap, mirrored
  networking. `large`/6-node clusters stay tight locally.
- **PostgreSQL RLS verified for the first time — 3/3 PASS** against real
  PostgreSQL 18. These tests skip silently without
  `POLYFORGE_TEST_POSTGRES_{ADMIN,APP}_URL`, so the deepest claim in
  SECURITY.md had never executed here. Use **`./scripts/pg-test-up.sh`**.
- **OWASP ZAP executed for the first time**, findings fixed, re-scan
  **118 PASS / 0 FAIL**. Caveat that matters: the *default* baseline scan
  reaches 2 URLs (both 404) because the control plane is a JSON API with no
  root route — its "66 PASS" is a scan of nothing. Use `--api`. Scope is the
  **unauthenticated** surface; authenticated scanning is WP12.
- Security posture: the session-35/36 audit closed 6 code findings plus 2
  residuals (telemetry bounds, SSRF redirect guard). Remaining are
  deployment-layer only (NetworkPolicy needs an enforcing CNI; Linkerd mTLS
  and Vault are cluster properties).

## §2 Dependency graph and venue map

```
WP1 trace parity ──┐
WP2 MASTER fix ────┤
WP3 layered fix ───┤──► FGCS (Q1) evidence-complete ─┐
                   │                                  ├─(+ WP9 reframe)─► submit
WP7 push (done) ───┤                                  │
WP8a dry-run (desk)┤                                  │
WP8b B1 scored ────┼──► TCC / TSC (Transactions) ─────┘
WP13 MT separation ┤      ▲ the three session-37 Transactions
WP14 live soak ────┘      │ strengtheners: theory weight (WP13),
                          │ live duration (WP14), and the venue
                          │ decision rule in WP9 (WP1's outcome
                          │ picks the pitch BEFORE writing)
WP4 cells + WP5 O(N²) + WP6 mismatch ──► TPDS additionally
WP12 authenticated ZAP ──► security-section completeness (small)
```

**Why WP13/WP14 exist (session 37).** The original Transactions-tier
strengthener — M3's recursive-feasibility theorem — proved vacuous, and its
replacement (the cost separation, `bec0f62`) is honest but explicitly
"suggestive corroboration at one operating point". A Transactions submission
resting on one confirmatory B1 sitting plus a single-operating-point theorem
is thin. WP13 restores theory weight (the multi-tenant extension the M3
close-out itself names as the open item); WP14 turns "we ran it live once"
into "we ran it live for a day with faults, and here is how it behaved" —
at $0, on the Docker that now works locally. Neither blocks FGCS; both are
specifically for the TCC/TSC bar.

**Execute order, reprioritized session 37 for a Transactions-tier target
(TCC/TSC, not just FGCS/Q1).** The user's actual target is Transactions,
not a Q1-safe fallback, so B1 — the only live evidence for the joint
controller and the single highest-uncertainty item on the whole path — is
pulled forward instead of trailing behind the desk-only polish work that a
Transactions reviewer weighs less. WP4–WP6/WP12 are independent of B1 and
of each other; nothing is lost by running them after, and everything is
gained by learning B1's outcome (and whether the live plane needs a
follow-up rep — see the §8b note) before investing further desk time:

**WP7 (done) → WP1 (done) → WP2 (done) → WP15 (budget parity, TOP) → WP13 (theory, parallel-safe) →
WP3 → WP8a (desk-doable since session 36, no GPU — start Docker Desktop
first) → WP14 (live soak, desk, needs Docker + the laptop kept awake) →
WP8b (user opens the GPU gate here, NOT last) → WP4 → WP5 → WP6 → WP12 →
WP9–11.**

WP13 touches only `research/jcac_sim/` and blocks nothing — run it in any
gap (e.g. while a campaign is in flight). WP14 must come AFTER WP8a: the
dry-run proves the actuation plumbing the soak depends on. The venue
decision (TCC/TSC vs FGCS-first) is made when WP1's record lands — see the
WP9 note in §5; do not start the manuscript carve before then.

Note for WP8b specifically: `eval/experiments/wave4_live_plane.yaml` is
frozen at `reps: 1` (the prereg's stopping rule) — a single confirmatory
sitting, which is thin evidence by Transactions standards even though it is
honestly pre-registered. Do not edit that frozen design. If the first B1
result lands clean and the user wants stronger-than-single-sitting
evidence, the correct move is a NEW small follow-up prereg (one changed
factor: more reps or longer duration), mirroring the RB-H1 follow-up
pattern (session 30) — frozen and pushed before it runs, decided only
*after* seeing the first result, not pre-committed now.

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

### WP15 — Budget parity: make the arms solve the same problem (session 38, TOP PRIORITY)

**Why.** WP1's comparison has a third confound, structural and larger than
the eviction one. `controller.py:647` rejects any candidate whose projected
spend exceeds the per-tenant Budget CRD (`hourly_budget_usd`, default $5/hr)
**before the objective is evaluated**; `baselines.py` has **no affordability
check anywhere** in its scaling path (`hourly_budget_usd` appears there only
as a fairness weight). So jcac is the only arm subject to the budget
constraint it was built to honour. Measured on BurstGPT window 57:
`hpa_fair` spends **574.14** with 0% shed; jcac spends **11.93** with 93.6%
shed. jcac looks cheap partly because it is *forbidden* to spend, and
overshoots because shedding is the budget-respecting response to that same
prohibition. **Cost and severity are not two axes here — they are one
constraint seen twice.**

This also explains why the pre-registered β sweep is inert (`f045e68`): β
prices a tradeoff, but a hard filter is binding. **Do not run
`PREREG_VIOLATION_PARITY`'s ladder** — it is measured inert and would burn
hours restating the probe. That prereg stays frozen and pushed with its
premise falsified, per R1.

Prior art in-repo, never connected to the baseline comparison: session 29's
risk-MPC null ("the knob was fighting the Budget CRD, not the demand") and
`guarantee.py`'s header ("what does bind is the per-tenant budget against AI
tier spend").

**Steps.**
1. VERIFY FIRST: confirm the asymmetry by code reading, not inference —
   `budget_per_step` in `controller.py`, and its absence in every
   `baselines.py` scaling path. Record file:line for both.
2. Freeze `PREREG_BUDGET_PARITY.md` (§0.2 template), push before any run.
   One factor: **which arms the budget filter applies to.** Two directions,
   both frozen in the same prereg because they bracket the truth:
   - `hpa_budget` / `keda_budget`: the fair comparators **subject to the
     same per-tenant filter** (the honest like-for-like — what a reactive
     autoscaler under a real Budget CRD would do).
   - `jcac_nobudget`: the proposal with the filter **lifted** (what the
     controller does when allowed to spend like the baselines).
   Published arms untouched; new arms default-off (R4).
   Hypotheses: BP-H1 against budget-capped comparators, jcac's cost
   advantage survives at violation parity. BP-H2 with the filter lifted,
   jcac reaches the comparators' `mean_excess`. BP-H3 (descriptive) how much
   of WP1's cost gap and of its severity gap each direction explains.
3. Run once over both traces (WP1's protocols), analysis →
   `RESULTS_BUDGET_PARITY.md` via `record_path()`, register, checklist.

**Contingency.** If BP-H2 shows jcac reaches parity once unconstrained, then
WP1's severity FAIL is an artefact of the constraint asymmetry and must be
reported as such — without claiming the constrained arm's cost number, which
was earned under the constraint. If BP-H1 fails, the cost contribution does
not survive a like-for-like comparison and is withdrawn per
`PREREG_VIOLATION_PARITY`'s pre-committed response. **Effort:** ~half day +
campaign hours (the ladder's runtime, roughly 2–3 h for both traces).

### WP13 — Multi-tenant extension of the cost separation (the Transactions theory strengthener)

**Why.** M3's replacement theorem (`bec0f62`, `research/jcac_sim/guarantee.py`,
756 lines) derives the reactive-vs-predictive cost separation for a **single
tenant** — the per-tenant frontier enumeration is *exact* only because
tenants are independent. The M3 close-out itself records the gap
(`docs/MAIN_WORKING_PATH.md` §3 M3): **the multi-tenant coupling is
load-bearing, not optional**. Ignoring cluster caps gives `flash_crud` the
*wrong sign* (−19.5% derived vs +11.5% measured); equal-share makes the cell
*infeasible*; the truth sits between because per-tenant phases are drawn
independently, so bursts rarely coincide and a tenant borrows capacity while
neighbours sit in troughs. Today only 2 of 5 matched parity pairs agree in
sign, and the record honestly says "suggestive corroboration at one
operating point". A bracket that contains the measured value on the coupled
cells upgrades that to a validated correspondence — the single biggest
theory upgrade available for TCC/TSC. Tools: Python 3.13 + pytest only; no
Docker, no campaign, no GPU.

**Preconditions.** None on other WPs. Read BEFORE designing anything:
(1) `research/jcac_sim/guarantee.py` end to end — the machinery to extend is
`reactive_cost_floor` (line ~336), `predictive_cycle_cost` (~372),
`price_of_reaction` (~442), `cost_at_violation_parity` (~674), and the
docstring at line ~74 listing the three couplings deliberately left out;
(2) `docs/MAIN_WORKING_PATH.md` §3 M3 in full, including the RETRACTED
sign-agreement claim and the do-not-say list in `DEFENSE_QA` #28 — do not
resurrect the retracted claim in any form; (3) `test_guarantee.py` for the
test conventions.

**BLOCKER FOUND SESSION 37 — read this before planning the extension.**
`guarantee.py` is imported by **`test_guarantee.py` and nothing else**
(verified: `grep -rl guarantee --include=*.py` returns only the module, its
test, and two unrelated files matching the word). There is **no
`RESULTS_SEPARATION.md`, no analysis script, no `reproduce.py`
registration**, and `test_guarantee.py`'s 27 tests do **not** pin any
published number (no 49.0, no 0.012800, no 43.4, no per-cell table). **The
entire M3 result — the paper's only formal contribution — exists solely as
prose in `docs/MAIN_WORKING_PATH.md` §3 M3.** It cannot be regenerated by
any committed command. For a Transactions submission this is worse than
the trace-record gap (§Session 37 of `REMAINING_WORK.md`): a reviewer
asking "regenerate Table N" has no answer, and nobody can tell whether the
prose numbers still match the code.

So **WP13 step 0 is to make the EXISTING result reproducible**, and that is
independently valuable even if the multi-tenant extension later fails.

**PARTIAL VERIFICATION ALREADY DONE (session 37) — start from here, do not
redo it.** The orbit constructors are in `test_guarantee.py`
(`crud_demand`, `flash_orbit`, `gentle_orbit`, `standard_config`); the
published percentage is `gap / reactive_cost_floor`. Results:

| published row | reproduces? | how |
|---|---|---|
| `flash` $0.012800 / $0.006533 / **+49.0%** | **EXACT** | `price_of_reaction(standard_config(), flash_orbit())` |
| `ramp_gentle` $0.009733 / $0.009467 / **+2.7%** | **EXACT** | `gentle_orbit(period=20)` — **NOT** the test file's default 40, which gives 0.019333 / 0.018800 / +2.8% |
| all-distinct $0.004533 / $0.004800 / **−5.9%** | **NO — could not be reproduced** | ~20 constructions tried, none matches |

**The mechanism row does not reproduce, and appears structurally
impossible as described.** De-aliasing a *smooth* orbit provably gives
gap **+0.0%** (floor == cycle): with singleton successor sets the relaxed
reactive optimum is achievable, and the reach clamp never binds on a gentle
slope. Confirmed empirically at periods 10/12/20, perturbing either
`crud_base_ms` or `rps`, at five magnitudes each — every variant returned
floor == cycle exactly. So a **negative** gap cannot arise in the family
the prose names. The row's cycle $0.004800 does equal `gentle_orbit(10)`'s
cycle exactly, so it is gentle(10)-family; but that family de-aliases to a
floor of $0.004800, not $0.004533. Negative gaps DO arise for orbits that
are distinct **and** sharp (monotone ramps over the same 0.5–6.0×
envelope: n=10 → −8.3%, n=16 → −7.1%, n=20 → −5.6%), none hitting −5.9%.

**Why this matters more than the arithmetic:** row 3 is the *mechanism
test* — its job is to show the +49.0% gap is caused by observational
aliasing rather than by the arithmetic. Rows 1–2 stand exactly; the row
that carries the causal argument has no runnable derivation today.

**What step 0 must do about it** (do NOT quietly substitute a number that
reproduces): find the original construction, or, failing that, report the
row as unreproducible in `RESULTS_SEPARATION.md` and derive a *new*
mechanism test that is runnable — the natural one is the ramp family
above, which is genuinely aliasing-free and does show the sign inversion.
State plainly in the record that the published −5.9% could not be
regenerated and what replaced it. Do not edit the M3 prose to match;
supersede it in the open, per R1.

**Loose thread for whoever picks this up:** `flash_orbit` de-aliased gives
exactly **−19.5%**, which is also the number `MAIN_WORKING_PATH` §3 M3
reports for the *no-cap `flash_crud`* case. That may be coincidence or may
indicate the prose numbers came from overlapping throwaway scripts. Worth
checking; not asserted.

**Steps.**
0. **Make M3 reproducible (do this first, ~half day).** Write
   `research/analysis/analysis_separation.py` that regenerates, from
   `guarantee.py` alone with no hand-entered numbers: the three-row orbit
   table (`flash` floor $0.012800 / cycle $0.006533 / **+49.0%**;
   `ramp_gentle` +2.7%; all-distinct-demands **−5.9%** mechanism test), and
   the 5-pair derived-vs-measured table (medium/`spike_agentic` +43.4 vs
   +53.1; large/`flash_ai` +42.5 vs +79.1; small/`flash_crud` **not
   computable**; both `ramp_gentle` pairs out of scope), with the measured
   side read from the committed campaign export rather than typed in.
   Output `RESULTS_SEPARATION.md` via `stats.record_path()`; register in
   `reproduce.py` `CAMPAIGN_RECORDS`. **If any regenerated number differs
   from the prose, STOP** — that is a finding about the published claim,
   report it, do not silently adopt the new value. Add tests pinning the
   three headline orbit numbers so future drift is caught.
1. VERIFY FIRST (continues from step 0): confirm the two broken coupling
   models reproduce — no-cap gives `flash_crud` **−19.5%** (opposite sign
   to the measured +11.5%) and the equal share (`replicas//8`,
   `cache_mb//8`) makes the cell **infeasible** — before deriving anything
   new. These two are the bracket WP13 must narrow.
2. Design — two candidate routes, try in this order, keep whichever yields
   a bracket that *bites* (is narrower than the no-cap/equal-share gap):
   - **Route A (exact, preferred): aggregate-demand enumeration.** Phases
     are independent uniform draws over deterministic orbits, so the joint
     phase space is the finite product of orbit positions and every
     coincidence probability is *exactly countable* — no concentration
     inequality needed. The cluster cap binds on aggregate work units, so
     convolve the per-tenant orbit work-unit distributions (a DP over
     tenants × orbit length × aggregate WU, not the L^N product) to get the
     exact distribution of aggregate demand per step; the reactive floor
     under coupling becomes the expected cost of clearing every aliased
     aggregate successor, allocated by the same rule the simulator uses.
   - **Route B (bracket): refine the two broken bounds.** Lower: no-cap
     corrected by the exactly-computed probability mass of coincident
     bursts. Upper: equal-share relaxed by the borrowable headroom
     (neighbours' trough capacity × probability both are in trough).
     Cruder, but still an honest interval.
3. Implement in `guarantee.py` (new functions; existing functions
   UNTOUCHED — the single-tenant results are published), tests in
   `test_guarantee.py`: degenerate case (1 tenant) must reduce exactly to
   the existing floor; a hand-computable 2-tenant × 2-position case checked
   against brute-force enumeration of the full product space.
4. **State the validation reading in writing BEFORE computing it** (in the
   analysis script's docstring, committed first — this is a derivation over
   already-committed campaign data, so no prereg/R2 applies, mirroring how
   `bec0f62` itself landed; the pre-stated reading is what keeps it
   honest): the derived bracket contains the measured cost delta on ≥4 of
   the 5 matched pairs INCLUDING `flash_crud`'s sign, or the extension is
   reported as failed.
5. `research/analysis/analysis_separation_mt.py` →
   `RESULTS_SEPARATION_MT.md` via `stats.record_path()`, register in
   `reproduce.py` `CAMPAIGN_RECORDS`, full §0.3 checklist, update
   `MAIN_WORKING_PATH.md` §3 M3 (append, dated — the closed M3 record is
   not edited).

**Contingencies.** If neither route produces a bracket narrower than the
no-cap/equal-share gap, that adjudication IS the deliverable: the record
states the coupling is analytically intractable at this generality, with
the enumeration evidence, and the paper cites the theorem as single-tenant
scope only. Do NOT widen the acceptance rule after seeing numbers, and do
NOT tune constants until a pair agrees. **Effort:** ~half day for step 0
(reproducibility, valuable on its own) + 1–2 days for the extension.

**Step 0 is separable and should ship on its own commit** even if the
extension is abandoned: it converts the paper's formal contribution from
unverifiable prose into a gated, regenerable record.

### WP14 — Live CRUD-plane soak with faults (the Transactions duration strengthener)

**Why.** Everything live on record is short: B2/B3 was one Codespace
sitting; WP8b is frozen at `reps: 1`. Transactions reviewers distinguish
"ran live once" from "ran live for a day and here is how it behaved through
faults". The GPU is NOT needed for this: `POLYFORGE_EVAL_LIVE_AI` is opt-in
(`eval/harness/cluster_backend.py`) — unset, the harness deploys no AI
gateway and exercises the operator, planner, replica + cache knobs, chaos
injection and the full audit path on CRUD load alone. Local Docker
(session 36) makes a 12–24 h soak $0.

**Preconditions.** WP8a dry-run PASSED (the soak stands on that plumbing).
Docker Desktop running (`docker version` answers; if not, check
`Get-Service com.docker.service` is Automatic+Started — the session-36
fault). Tools already on PATH: `kind`, `helm`, `kubectl`, `k6`
(**GrafanaLabs.k6**, not k6.k6; note winget tools live under
`%LOCALAPPDATA%\Microsoft\WinGet\Packages\`, so a stale shell may need PATH
re-resolved). Cluster size `small` or `medium` ONLY — `~/.wslconfig` gives
Docker 9.7 GB and `large` does not fit. **Keep the laptop awake for the
duration**: `powercfg /change standby-timeout-ac 0` before, restore after
(record the prior value first: `powercfg /query SCHEME_CURRENT SUB_SLEEP`).
Free disk ≥ 10 GB for metrics + PG.

**Steps.**
1. Freeze `PREREG_LIVE_SOAK.md` (§0.2 template), commit AND PUSH before the
   run (R1/R2 — this scores new live measurement). Design shape (exact
   margins frozen by the implementer FROM committed B2/B3 values in
   `eval/results/live_chaos_p99_runs.csv` and `RESULTS_LIVE_CHAOS_P99.md` —
   never invented): duration 12 h minimum / 24 h target on one kind
   cluster, CRUD-only load via the session-19/23 harness
   (`docs/WAVE3_LIVE_RUNBOOK.md` is the proven pattern), `medium` cell,
   `POLYFORGE_EVAL_SHARED_PG=1` (R5), fault schedule injected at frozen
   hours reusing B2's two injectors (planner crash + apiserver throttle),
   ≥2 occurrences each. Hypotheses to freeze: SK-H1 every fault recovers
   without operator intervention (binary); SK-H2 audit-record continuity —
   every degraded cycle audited across the whole soak (the D11 fix under
   sustained load); SK-H3 hour-bucketed crud p95 stays within a band around
   the B2 committed values; SK-H4 zero invalid-run signatures
   (`check_metrics` gates: nonzero p95, nonzero `n_events`, sampler
   coverage ≥90%, k6 delivery thresholds).
2. Write the soak driver as a thin spec over the existing harness — a new
   `eval/experiments/live_soak.yaml` + whatever minimal runner glue the
   chaos campaign scripts don't already provide. Do NOT fork the harness;
   the B2 fault injectors and the k6/`check_k6_delivery` path are the
   proven components. Run `kubectl get events -w` logging to a file for the
   post-mortem trail.
3. Run once. If the machine sleeps, the run is VOID — report, do not
   splice two half-runs (the prereg forbids it).
4. Export csv.gz, `analysis_live_soak.py` → `RESULTS_LIVE_SOAK.md` via
   `record_path()`, register in `reproduce.py`, §0.3 checklist.

**Contingencies.** Instability surfacing mid-soak (planner leak, operator
crash-loop, PG exhaustion) **is the finding, not a nuisance**: report it,
fix forward, and re-sit under a NEW small prereg (one changed factor),
mirroring the RB-H1 follow-up pattern. Never truncate or splice the record.
If Docker cannot hold `medium` for 24 h, drop to `small` and disclose —
duration outranks width for this WP's purpose. **Effort:** ~half day desk +
12–24 h unattended wall-clock.

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

**SPLIT INTO WP8a / WP8b (session 36).** Docker is now installed locally and
`cluster_backend.preflight()` reports no missing tools, so the two halves no
longer share a gate. The GPU is only needed for **real model tiers**:
`POLYFORGE_EVAL_LIVE_AI` is opt-in (`cluster_backend.py`), and with it unset
the harness deploys no AI gateway and needs no `POLYFORGE_EVAL_TIER_BACKENDS`.
Kubernetes actuation — which is what the dry-run checks — needs a cluster,
not a GPU.

#### WP8a — live actuation dry-run (**NOW DESK-DOABLE, agent-executable, $0**)

This was deferred out of M1 for want of Docker and is the last item standing
between the repo and a scored B1. Non-scored; it proves the plumbing.

1. `kind` is verified working locally (session 36: cluster up in 16 s,
   scheduled a pod, torn down clean). Tools: all five on PATH — note they
   live under `%LOCALAPPDATA%\Microsoft\WinGet\Packages\...`, so a shell that
   predates the install has a stale PATH; re-resolve from Machine+User PATH.
2. Render and apply all four frozen arms (`jcac`, `replica-only`,
   `cache-only`, `tier-only`) from `eval/experiments/wave4_live_plane.yaml`
   with `POLYFORGE_EVAL_LIVE_AI` **unset**.
3. Assert, per arm: the CRs admit against the committed CRDs (the CEL bound
   rules are live), the operator actuates, and a **pinned** knob does not
   move while the free knob does. That is the ablation's whole meaning.
4. Record the outcome in `REMAINING_WORK.md`. If a pin leaks, that is a
   finding, not a nuisance — an unpinned arm is an unlabelled copy of the
   full controller.

Constraint: `~/.wslconfig` gives Docker 9.7 GB, so `small` (2 nodes) and
`medium` (4) are comfortable and `large` (6) is tight. The dry-run does not
need `large`.

#### WP8b — the scored matrix (**still user-gated on a GPU**)

Substrate (all free): `docs/WAVE4_FREE_ROUTE.md` — Kaggle P100 (30 GPU-h/wk,
tier bench already proven there) runs `kaggle_tier_server.py` → tunnel URL →
`POLYFORGE_EVAL_TIER_BACKENDS`; Codespaces (Education: 180 core-h/mo) runs
the kind cluster + harness. (Local Docker can host the cluster half now, but
the tiers still need the GPU.)

Order INSIDE the sitting (from `PREREG_WAVE4_LIVE_PLANE.md` §Status):
1. WP8a's dry-run, if it has not already been done at the desk.
2. `knob_preflight.py` WL-H2 gate — now runs automatically inside
   `execute()`; an inert knob RAISES and voids the run. **If it raises:
   report SUBSTRATE INADEQUATE; WL-H1 is void; do NOT fake or lower
   margins.**
3. The frozen 4 arms × 4 cells matrix, `--workers 1` (enforced), once.
4. Export csv.gz, write the RESULTS record against the frozen prereg,
   register in `reproduce.py`, full checklist.

### WP12 — authenticated ZAP scan (small, desk-doable)

Session 36 ran ZAP for the first time and fixed the three findings it
surfaced (see `docs/SECURITY.md` §Penetration test status). The scan covers
the **unauthenticated** surface only: every real endpoint answers 401, so the
rules never exercise authenticated behaviour. Build a ZAP context carrying a
tenant API key (provision via `/v1/tenants` + `/v1/tenants/{id}/api-keys`,
as `cluster_backend.provision_tenants` does) and re-run
`./scripts/zap-baseline.sh --api`. Triage into the same findings table. Until
then, the honest claim is "unauthenticated surface, 118 rules, 0 FAIL" —
never "the API passed a pen test".

---

## §5 Track 3 — user-owned (writing + accounts; agent does NOT do these)

- **WP9 Reframe** thesis + `research/paper/main.tex`: the claim is now
  *"joint control at cost parity with a competently-configured reactive
  autoscaler, with ~half the SLO overshoot; cheaper than event-driven
  scaling; advantage concentrated where cache/tier are load-bearing"* +
  the validity-methodology story (self-audit found and published its own
  confound: EVICTION_PARITY + ORDER_PERMUTATION + [WP1/WP3/WP6 records]).
  Sources: MAIN_WORKING_PATH §0b, the V-series records, WP2's MASTER.

  **Venue decision rule (session 37 — decide BEFORE writing a word).**
  The pitch is selected by `RESULTS_TRACE_PARITY.md` (WP1), not by
  ambition:
  - **TP-H1 survives on ≥1 trace** (a real cost advantage against fair
    comparators on real demand): primary target **TCC or TSC**
    (Transactions), with WP13's theory bracket and WP14's soak as the
    supporting weight; FGCS is the resubmission fallback, not the first
    shot.
  - **TP-H1 fails on both traces** (parity, as the synthetic EP-H1a
    result suggests it may): primary target **FGCS (Q1)** with the
    parity-plus-half-the-overshoot framing and the methodology story as
    the lead; a Transactions attempt then waits on B1 landing clean AND
    the WP14 soak, and is a second paper cycle, not this one.
  - Either way: the arXiv preprint (WP11) goes up when the manuscript is
    done and is NOT delayed by venue strategy; the security paper split
    (Computers & Security / PETS) is unaffected by this rule.
- **WP10** RB-H1 family decision (p=0.0073: survives Holm in its own
  6-hypothesis family at 0.05/6=0.00833; fails vs all 48 at 0.00104).
  Decide which family it belongs to; state it in the paper's stats section.
- **WP11** Bucket C mechanics: OSF submit (paste frozen preregs, record
  DOIs), Zenodo deposit + DOI, title-page macros (real name/roll/supervisor
  — never fabricated), human PDF proofread, arXiv + venue submission.

## §6 Progress table (update as WPs land)

| WP | Status | Evidence |
|---|---|---|
| WP1 trace parity | **DONE (session 37, `5c75e8a`)** — split verdict, see below | `RESULTS_TRACE_PARITY.md`; replication PASS bit-for-bit both traces; gate 21/21 |
| WP13 step 0 | **DONE (session 37, `67a32a5`)** | `RESULTS_SEPARATION.md` registered; 2 of 3 published rows EXACT, mechanism row not reproducible and replaced by a runnable test |
| WP2 MASTER reconcile | **DONE (session 37)** | Cost/SLO/Fairness scoreboard rows + ablation annotation + 3 disambiguation-table rows updated with the EP/WP1 adjudications; hand-curated only, no generated record touched |
| **WP15 budget parity** | **DONE (session 38, `c6a021f` Azure + `b3cd766` BurstGPT)** — BP-H1 FAIL both traces (cost claim withdrawn), BP-H2 PASS both (typical-window only on BurstGPT) | `RESULTS_BUDGET_PARITY.md`; replication EXACT both traces (504 + 672 rows, 0.00e+00); gate 22/22; 14 pinning tests |
| WP3 layered fix | **DONE (session 38)** — LF-H1 PASS, LF-H2 PASS; +2884% withdrawn, **+318.5%** is the honest number | `RESULTS_LAYERED_FIX.md`; prereg `bedd6ff` pushed before the arms existed; 500/500 valid runs; latch measured 850/960 tenant-steps in `large`, gone after the fix (952/960 in `mid`); gate 23/23 byte-identical |
| WP4 cells verify/fix | **DONE (session 38)** — audit claim **FALSE**, closed as an adjudication, no code change | `REMAINING_WORK.md` §WP4/C6: `planner.py:245-256` keeps the tenant set out of the rebuild signature by design, `:266-289` carries survivor history across both paths, `planner_cells.py:81-88,126-144` partitions by index with cores built once; 3 new `PlanningCellTests` demonstrate partitioned == monolithic history |
| WP5 O(N²) memoize | **DONE (session 38)** — shipped default-on; R4 gate PASSED | profile before: `evaluate_step` 5,859,776 calls, 35.6% tottime, 84% cumtime on a 64-tenant × 120-step run; after: **54,074 calls (108× fewer), 132.75 s → 9.66 s (13.7×)**; `reproduce.py` 22/22 byte-identical; 3 `ProjectionMemoTests` pin per-cycle clearing + linear scaling |
| WP6 model mismatch | NOT STARTED | — |
| WP7 push | **DONE (session 37, 2026-08-11)** | 26 commits pushed to `origin/v-series-validity-remediation`; permanent anchor disclosure for the two session-35 preregs recorded in `REMAINING_WORK.md` §Session 37 |
| **WP8a dry-run** | **DONE (session 38)** — ran against a real apiserver; found `replica-only` rendering CRs **byte-identical to `jcac`'s** | `eval/scripts/live_actuation_dryrun.py`; 4/4 arms admit, both CEL bound rules fire on negative tests, pin now declared for all four arms; no measurement affected (WP8b never ran, and the pin was already enforced by `planner.enabled=false`); 2 `TestWave4ArmPins` tests; gate 23/23 |
| WP8b B1 scored | WAITING ON USER (GPU gate) | prereqs landed session 35; cluster half now runnable locally |
| WP12 authenticated ZAP | NOT STARTED (small) | unauth surface done: 118 PASS / 0 FAIL |
| **WP13 MT separation** | NOT STARTED (new, session 37 — Transactions theory strengthener) | machinery: `guarantee.py`; open item named in M3 close-out |
| **WP14 live CRUD soak** | NOT STARTED (new, session 37 — needs WP8a first, Docker started, laptop kept awake 12–24 h) | harness proven in B2/B3; GPU not required |
| WP9–11 | user-owned; **WP9 waits on WP1's record (venue decision rule, §5)** | — |

### WP1's verdict and what the venue rule now says (session 37)

| trace | published vs `hpa` | fair vs `hpa_fair` | cost TP-H1 | severity TP-H3 |
|---|---:|---:|---|---|
| BurstGPT (n=96) | −70.4% | **−52.2%** | **FAIL** | **FAIL** |
| Azure (n=72) | −42.5% | **−7.1%** | **PASS** | **PASS** |

**The venue rule's cost condition is met** (TP-H1 survives on Azure), which
reads as "TCC/TSC primary". **Do not apply it mechanically — two findings
the rule did not contemplate cut against that**, and WP9 must weigh them:

1. **The BurstGPT cost advantage is not typical, it is concentrated.** Mean
   window diff −35.586 but **median +2.232**, with only **42.7%** of windows
   favouring jcac: PolyForge is *more expensive* than a fairly-configured HPA
   in the majority of 6-hour windows. The paired t (p=0.000205, mean-driven)
   would have passed at the published α=0.01; the pre-registered Wilcoxon
   gate (rank-driven) does not. The FAIL stands — the prereg named the gate
   in advance precisely so this could not be chosen afterwards — but the
   *shape* of the advantage is the real finding. Azure is genuinely
   pervasive by contrast: median −0.666, 69.4% of windows favour jcac.
2. **The severity claim reverses on real demand.** `RESULTS_EVICTION_PARITY`
   found jcac at ~half `hpa_fair`'s unbounded overshoot (0.2143 vs 0.4288).
   On BurstGPT it is **0.5134 vs 0.008818 — 58× worse**, above the 0.05
   margin in 41.7% of windows, with AI shed on 9.3% of tenant-steps against
   0.0% for every reactive arm. **This damages the "parity at half the
   overshoot" fallback framing WP9 was going to lean on**: that claim holds
   on the synthetic matrix and reverses on the larger real trace.

Practical reading for WP9: the defensible claim is now *trace-dependent* —
a pervasive but modest (~7%) advantage on Azure with severity within
margin, against a concentrated-in-a-minority advantage on BurstGPT bought
with materially worse overshoot. That is publishable and honest, but it is
an FGCS-shaped story unless B1 (WP8b) and the WP14 soak add live weight.
**Decide the venue after B1, not now**; nothing here is a reason to soften
the record.

### Session 38 supersedes the cost condition above (WP15)

**The venue rule's cost condition is no longer met.** It was keyed on TP-H1
surviving on Azure, but WP15 showed that comparison still carried a third
confound: only jcac was subject to the per-tenant budget filter. Against
comparators carrying the controller's own budget rule, **BP-H1 FAILS on
both traces** (Azure −3.3%/−1.3%, p=0.379/0.91; BurstGPT −50.2%/−49.8%,
p=0.112/0.191). The comparative cost claim is withdrawn at every level:
−70%/−42% → −52.2%/−7.1% → **withdrawn**.

What replaces it is stronger against the "you tuned the baseline badly"
objection and weaker as a headline: a **feasibility** result. At the
per-tenant cap, tier spend dominates infra spend by three orders of
magnitude; capping a replica-only arm moves infra −45.7%/−57.4% and tier by
**exactly 0.00%**. No replica-only reactive controller can satisfy the
budget under AI load by any scaling decision available to it, so no
budget-respecting comparator exists in that class. That is an argument from
the price table, not a benchmark outcome, and it cannot be answered by
retuning a baseline.

Consequence for the venue rule: the FAIL branch of §5's rule now fires on
the cost condition. **This does not by itself select FGCS** — the joint
control feasibility argument plus fairness, forecasting, security and the
validity methodology are the Transactions case now, and B1 (WP8b) + WP14
remain the deciding live evidence. Re-read §5's rule with the cost
condition scored FAIL, and still decide after B1.

**Infrastructure status (session 36):** Docker + WSL2 + kind/helm/k6 all
working locally; PostgreSQL RLS 3/3 PASS via `./scripts/pg-test-up.sh`; ZAP
executable via `./scripts/zap-baseline.sh --api`. Nothing on Track 1 or WP8a
is blocked on tooling any more — only WP8b (GPU) and WP7/WP9–11 (user).
**Session 37: Docker Desktop is installed but the daemon is not running by
default** — start it before WP8a/WP8b/WP12; if it refuses, check
`com.docker.service` is Automatic + Started (the session-36 fault). WP1–WP6
need no Docker at all — pure `research/jcac_sim`/`research/analysis` work.

**Target is Transactions tier (TCC/TSC), not FGCS/Q1 as a ceiling** — see
the reprioritized execute order in §2. WP8b is pulled forward, ahead of
WP4–WP6/WP12, so the live-evidence outcome is known before further desk
polish is invested.

## §7 Risk register

| Risk | Response (decided NOW, not after seeing data) |
|---|---|
| WP1: trace headlines reverse vs fair arms | Headline of the record; scoreboard restated (WP2); paper leans severity+keda+fairness (WP9). Not softened. |
| WP3: jcac loses to `gptcache_v2` on J | Reported as-is; the joint-ablation claim narrows to the honest delta. |
| WP4: audit claim false | Adjudicate + close in writing; no code churn. |
| WP5: memoization drifts one byte | Do not ship default-on. Fix key or ship default-off arm. |
| WP6: advantage collapses at ±25% mismatch | Publish the boundary; it becomes the "when does this controller apply" section — a contribution, not an embarrassment. |
| WP8: WL-H2 inert knob | SUBSTRATE INADEQUATE, WL-H1 void, report honestly, fix substrate, re-sit. |
| WP13: no bracket narrower than the no-cap/equal-share gap | The adjudication IS the deliverable: theorem cited as single-tenant scope; acceptance rule never widened after seeing numbers. |
| WP13: bracket misses `flash_crud`'s sign | Reported as measured; the retracted sign-agreement claim stays retracted; no constant tuning until a pair agrees. |
| WP14: instability surfaces mid-soak | That IS the finding. Report, fix forward, re-sit under a NEW one-factor prereg. Never truncate or splice a record. |
| WP14: machine sleeps mid-soak | Run VOID. Disclose, re-sit. Two half-runs are never joined. |
| WP1 fails on both traces | Venue rule in §5 fires: FGCS-first pitch, Transactions deferred to a second cycle behind B1 + WP14. Not a crisis — pre-decided. |
| Any record drifts under reproduce.py | Stop. Find cause. Never re-freeze a prereg to match an outcome. |
