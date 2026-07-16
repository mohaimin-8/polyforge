# Session Log

`docs/EXECUTION_PLAN.md` requires that every work session produce runnable code,
passing tests, measured data, thesis text, or a decision record, and that the
session close by recording milestone status, what changed, how it was verified,
and what to study next. This file is that record. Newest entry first.

---

## 2026-07-16 (session 22) — the over-the-wire cache attack executed on the real gateway (WA-H1 PASS)

Milestone status: **the security half of Wave 3 ran for real.** User granted
broad autonomy ("do everything by yourself") and a new Kaggle token. Honest
outcome up front: the novel Wave-3 deliverable — the over-the-wire cache
side-channel attack — was executed against the live gateway and passed; the
live chaos *campaign*, a live p99 *number*, and the Kaggle GPU run are
genuinely blocked/deferred for reasons stated below (not skipped quietly).

### Over-the-wire cache side-channel — EXECUTED (RESULTS_WIRE_ATTACK.md)

Realisation that unblocked it: the attack needs a real gateway *process*, not
the kind cluster — so it runs anywhere with Go+Python, no Docker. Built and
ran the whole harness against a live `cmd/ai-gateway` (production
`SemanticCache` over real HTTP, deployed local n-gram embedder, `mock_llm.py`
backend for miss latency, `wireseed.go` to seed victim/attacker tenants+keys
straight into the gateway sqlite), both cache postures.

- New code (committed, tested): `SemanticCache.WithShared` / `Config.CacheShared`
  / `POLYFORGE_CACHE_SHARED` — the INSECURE single-partition posture, off by
  default, `partition()` threaded through Lookup/Store;
  `TestSharedCachePostureLeaksCrossTenant` asserts it leaks AND that the
  default does not. Gateway tests green.
- **WA-H1 (defense, primary) PASS on the wire:** per-tenant AUC **0.502
  (95% CI [0.384, 0.612]), 0 cross-tenant hits** — chance, on the real
  cache/HTTP path. Shared posture leaks perfectly on loopback (AUC 1.000,
  exactly the 50 victim-warmed secrets). Hit/miss gap 15.6/98.7 ms.
- Honest scope: gateway process over loopback (no WAN jitter — would only
  weaken the shared number, never the defense); exact-prompt membership
  threat (embedder-agnostic). Declared in a pre-run PREREG_WIRE_ATTACK
  amendment.
- Two real bugs found by running: gateway auth header is `X-PolyForge-API-Key`
  not Bearer; and the probe fixture must use high-entropy prompts — natural
  prompts sharing a sentence frame collide above 0.95 under the lexical n-gram
  embedder (40 spurious per-tenant hits before the fix). Both fixed; the
  clean run shows 0 / 50 hits exactly.

### Genuinely blocked / deferred (stated, not skipped)

- **Kaggle GPU (Wave 4) — later UNBLOCKED and run:** the first token was the
  new `KGAT_` OAuth format (CLI 2.2.3 browser-only, API probes 400); the user
  then provided a classic 32-hex `kaggle.json` key, which authenticated
  immediately. Pushed `kaggle_tier_bench.py` (kernel v7, GPU+internet) and
  polled to completion. **Independent replication: all three tiers reproduce
  session 16d within ~3%** (small 1205 vs 1242 ms, mid 1887 vs 1883, large
  20595 vs 20665; tokens/s identical) → the tier-latency ordering is
  reproducible across GPU sessions. The 7B large-tier CPU-offload caveat
  persists (pool granted a single ~16 GB card again; T4 ×2 is not selectable
  from the `kaggle kernels push` script API, only the notebook UI). Recorded
  as a replication section in `TIER_BENCH.md` + `tier_bench_replication.csv`;
  no sim constants change.
- **Live chaos campaign + live p99 number:** both need the kind cluster
  (Codespace, no local Docker) AND new harness work — fault injection wired
  into the live run, and p99 persisted through the results schema (the export
  emits it, the schema stores only p95). Substantial; the sim chaos (Wave 2,
  CH-H1/H2 PASS) already closed the mechanism, so these are confirmatory.
  Codespace was started, driven, synced, and stopped again (quota); runbook
  ready.

### What changed / verified

Go: cache.go/server.go/ai-gateway main + cache_test (green). Python:
wire_attack.py (exact-membership, REPS=1, high-entropy fixture, per-posture
JSON + --compose; --selftest green), mock_llm.py, wireseed.go. Results:
RESULTS_WIRE_ATTACK.md + two JSONs. Integrated into RESULTS_MASTER (scoreboard
security row + ledger entry 12) and DEFENSE_QA §19. All pushed
(d73d99a + this commit). The wire attack ran end-to-end locally; every number
is the harness's own committed output.

### What remains

Wave 3 live chaos + live p99 (Codespace + harness plumbing); Wave 4 GPU
(classic Kaggle key) + live three-knob plane; RELEASE_CHECKLIST + thesis
fill-ins (user-owned); OSF submit (user action).

---

## 2026-07-16 (session 21) — Waves 3–4 opened: planning cells to 1024 measured (local), live campaigns made turnkey (deferred)

Milestone status: **Wave 4's local half is done and Wave 3 is turnkey.** User:
"go on next two waves." The honest split, stated up front: Wave 3 is
fundamentally a *live-cluster* wave and this machine has **no Docker/kind**
(confirmed: `docker`/`kind` absent), so its runs are deferred to a Codespace;
Wave 4's planner-scaling half is pure local CPU and was executed. No thesis
`.tex`, no controller logic touched.

### Wave 4 — planning-cell partitioning to 1024 (local, executed)

Two pre-registered campaigns on the deployed `PlannerCore.plan` path,
single laptop core.

- **`PLANNER_CELLS.md`** (PREREG_PLANNER_CELLS at 264356a, pushed pre-run):
  partition the portfolio into fixed K=32 planning cells. **PS-H1 PASS** —
  partitioned per-cell p95 flat at ~195 ms through 1024 tenants (fitted
  exponent 0.27) where the monolithic joint plan reaches **71 s at 1024**
  (exponent 1.59) and crosses the 3 s operator timeout at 128. 1024 tenants
  become deadline-feasible. **PS-H2 FAIL** (worst ΔJain −0.089): diagnosed as
  a whale-period/cell-count aliasing artifact — round-robin on index (cell =
  i mod C) puts every whale (i mod 8 == 0) into a handful of cells whenever C
  is a multiple of 8 (N ∈ {256,512,1024}). Published as measured; a post-run
  **erratum** (prose only, data immutable) flags the script's pre-written
  optimistic "Reading" as falsified by the FAIL, and the script's prose
  generation was made outcome-aware so it cannot self-contradict again.
- **`PLANNER_CELLS_DEALIAS.md`** (PREREG_PLANNER_CELLS_DEALIAS at 0a61c6a,
  pushed pre-run) — the disciplined follow-up, one changed factor: hash-order
  cells by md5(tenant_id). **PF-H1 PASS**, worst ΔJain **−0.0053** through 1024
  (vs round-robin's −0.089). The fairness cost was the aliasing, not
  partitioning; hash-based cell assignment is the measured fix, and reporting
  both rules is the contribution.

### Wave 3 — live wave, made turnkey (execution deferred)

- **`research/security/wire_attack.py`** — stdlib-only over-the-wire attack
  client for PREREG_WIRE_ATTACK; the analysis (Mann-Whitney AUC reused from
  `cache_side_channel._auc` + bootstrap CI + WA-H1/H2/H3 verdicts) and
  orchestration are **offline-verified** via `--selftest` against a
  fixture-aware mock gateway (shared posture separates; per-tenant CI contains
  chance 0.50). The self-test caught a mock bug before it could mislead. The
  live transport is a thin adapter; deterministic 50-secret probe fixture
  committed.
- **`docs/WAVE3_LIVE_RUNBOOK.md`** — push-button Codespace steps for both
  deferred live campaigns (wire attack shared/per-tenant posture toggles; live
  chaos via kubectl planner-crash + apiserver-throttle; the new live p99
  export), reusing the session-19 Phase 7 harness with its gotchas pre-solved.

### What changed (docs/analysis/security; no thesis .tex, no controller logic)

Two preregs + two analysis scripts + two result records (planner cells); wire
attack client + fixture; live runbook. `RESULTS_MASTER.md` (ledger entry 11,
new which-number row, prereg count 11→13, consolidated-files list);
`DEFENSE_QA.md` §13 rewritten (scale lever executed to 1024, both assignment
rules measured); `OSF_REGISTRATION.md` (two new rows). p99 export from Wave 3
prep landed and unit-tested last session.

### How it was verified

Both planner campaigns ran to N=1024; PS-H1/PS-H3 and PF-H1 are the scripts'
own committed output. Wire-attack `--selftest` passes (analysis pipeline
verified offline). Environment absence of Docker/kind confirmed by command
probe. Every prereg pushed before its run (264356a, 0a61c6a); the de-alias is
a new prereg, not a re-run of the frozen campaign.

### What remains

Wave 3 live execution (Codespace: wire attack, live chaos, live p99 — runbook
ready) and Wave 4's live-plane + GPU-rental halves (three-knob real data plane,
paid host) — all user-gated. RELEASE_CHECKLIST + thesis fill-ins user-owned;
OSF submission the one flagged user action.

---

## 2026-07-16 (session 20) — Waves 1–2 leak-fill: six DEFENSE_QA audit gaps turned into pre-registered measurements, all as measured

Milestone status: **the user-gated Waves 1–4 leak-fill plan is opened and
Waves 1–2 are complete; every closed result got a robustness measurement,
and none reversed.** No thesis prose touched (user: "no thesis writing —
make it a strong publication").

### Engine (defaults bit-identical; 86/86 py tests, 8/8 spot-check replays 0.00e+00)

- `model.set_economy()` — per-run tier-price / cache-curve override, reset-
  first so a pooled worker is stateless; the module constants both
  `evaluate_step` (world) and every controller (beliefs) read move together,
  preserving the shared-model contract.
- `simulate.run` chaos hooks: `chaos_planner_outage` (last-known-good hold,
  no plan/observe) and `chaos_replica_kill` (serving-only degrade, billing
  nominal), controllers blind, default-off.
- Harness: `ExperimentSpec.economy` (sim-only, validated, appended to run
  identity only when set → every existing run_id unchanged); chaos SystemSpec
  arms routed to the engine, never the controller. `TestEconomyOverride` +
  `TestChaosArms` added; the KNOWN_FULL_RUN identity test pins the v1 headline
  run_id so resume/replay of every closed campaign can't silently break.
- `evalexport.go`: `evalPercentile` generalizes the exact p95 path; live
  `crud_p99_ms`/`ai_p99_ms` added (Go test green). p99 stays LIVE-ONLY.

### Wave 1 (re-reads immutable DBs only, exploratory, no new runs)

- `BREAKEVEN_TIER.md` — exact re-pricing algebra over rep-0 rows (identity
  check vs recorded cost ≤ 4e-4 USD); no aggregate cost win reverses across
  60 frozen price vectors in any campaign.
- `OBJECTIVE_FORM.md` — 69/85 form×baseline combinations preserve the
  published direction; all 16 non-wins are the disclosed +0.07 violation
  trade under violation-first / log-cost orderings.

### Wave 2 (four pre-registered campaigns, preregs pushed at 7deb6a3 pre-run)

- **Tier-ratio (§15)** `RESULTS_TIER_RATIO.md`, 1,800 runs at GPU corner
  1:1.516:16.64 — TR-H1 cost PASS 5/5 (p ≤ 6.8e-47), TR-H2 J PASS 5/5;
  decision response = jcac re-plans into now-cheap mid tier (1.7% → 13.7%
  steps), still wins.
- **Cache curve (§16)** `RESULTS_HK_ADOPTION.md`, 1,800 runs at measured
  hmax 0.285 / half 19 MB — HK-H1 J PASS 5/5, HK-H2 cost PASS 5/5; hit rate
  halves, gptcache devalued most, jcac drops cache spend, margin narrows
  (−27.5% → −19.4% vs HPA) but never closes.
- **Sim chaos (§19a)** `RESULTS_CHAOS_SIM.md`, 540 runs — CH-H1 (dead planner
  1 min beats healthy HPA, dz −1.69) + CH-H2 (survives 50% kill, dz −1.79)
  PASS; centralization-tax finding: freeze costs jcac ΔJ +0.0063 < HPA
  +0.0109 (p 3.8e-7) — proactive held config ages better than a frozen
  reactive one. Recovery 2–5 steps.
- **Pseudo-tenant (§18)** `PSEUDO_TENANT.md`, 19 sub-streams — PT-H1 PASS 0
  counterexamples, PT-H2 all 6 periodic streams, PT-H3 three distinct
  forecasters win → per-tenant pluggability measured one level down.

### Wave 3 prepared (deferred to a live Codespace session; pushed at c38bce6)

- `PREREG_WIRE_ATTACK.md` (over-the-wire membership attack, WA-H1 defense =
  load-bearing, no attack magnitude pre-committed) and
  `PREREG_LIVE_CHAOS_P99.md` (live planner-crash / apiserver-throttle + live
  p99). p99 export desk half landed and unit-tested now.
- `OSF_REGISTRATION.md` (§17): registry-ready mirror index, git push anchor
  per protocol; OSF submit is the one flagged user action.

### What changed (docs/code; no thesis .tex, no Go/Python controller logic)

`model.py`, `simulate.py`, harness `config.py`/`sim_backend.py`/`systems.py`,
`evalexport.go`(+test), `test_harness.py`; eight new analysis scripts/records
+ six preregs; `RESULTS_MASTER.md` (ledger entry 10, four new which-number
rows, prereg count 7→11, p95/p99 ground rule) and `DEFENSE_QA.md` §15–19
rewritten from open→measured/closed. Run-level CSVs committed
(`metrics_matrix_gpu_econ`, `metrics_matrix_hk`, `metrics_chaos_sim`); raw
duckdbs gitignored per policy.

### How it was verified

Each economy rerun spot-checked bit-identical on its default path (drift
0.00e+00); every campaign validated at full N (1800/1800, 1800/1800,
540/540) with runner `ok: true`; 86/86 harness + 43/43 sim py tests; Go
evalexport test green; gofmt clean. Every hypothesis outcome is the analysis
script's own output, committed verbatim.

### What remains

Wave 3 live execution (Codespace: wire attack, live chaos, live p99); Wave 4
(three-knob live plane, planner→1024 cells, GPU rental) — both user-gated.
RELEASE_CHECKLIST human items; thesis fill-ins (user-owned). OSF submission
(user action).

---

## 2026-07-16 (session 19) — the jcac live campaign executed end-to-end; ordinal verdict: DISAGREE, published as measured

Milestone status: **the project's final open measurement is done.** The
Phase 7 jcac live arm ran on the reusable 16d Codespace, driven remotely
from this machine over `gh codespace ssh` (no Docker locally). Smoke passed
on the FIRST attempt — 1/1 valid, actuation gate passed before load, zero
bug tail; the two session-17 desk fixes (admin-key demand auth, summed
shared-target actuation) held exactly as designed. Full mirrored slice:
12/12 valid ({jcac, hpa} × {crud_bursty, ai_cacheable} × 3 reps, ~22.5 min
wall each, `ok: true`).

### The measured result (frozen protocol, one run)

`phase7_ordinal.py` (frozen pre-run at 650ce29) against the committed sim
reference: **primary reading DISAGREE — the J winner flips in both cells.**
Live, the arms are at parity on every metric (J deltas 0.002, overlapping
rep ranges, violations 0 for both arms in both cells); the sim separates
them decisively. Mechanisms recorded in `PHASE7_ORDINAL.md` + thesis §6.10:
(1) ai_cacheable's sim separation flows through the cache economy — the
live cache knob is inert by construction (the frozen caption's disclosure
made concrete); (2) crud_bursty: sim HPA concedes 0.0417 violation where
real HPA at this amplitude never violates — the sim overestimates reactive
lateness there, a direction that had favored jcac in-sim, now on the
record. No thesis claim rested on live ranking reproduction; the claims
stand on the sim + replay substrates, scoped as such throughout.

### Operational record (for the next live session's benefit)

- Codespace fantastic-waffle had lost its `.git` (stale tree preserved at
  `/workspaces/polyforge.stale.16d`); fresh clone via
  `x-access-token:$GITHUB_TOKEN`. `gh codespace cp` broke on Windows —
  files streamed down via `base64` over ssh instead. Nested quoting
  through `gh codespace ssh` from PowerShell strips inner quotes — all
  remote commands shipped as base64-decoded scripts run under `bash -l`
  (GITHUB_TOKEN only exists in login shells).
- One hpa attempt died to a pod-level memcg OOM (22:29:43, control-plane
  pod at its limit); the harness retried per design and the clean second
  attempt validated (`attempts=2` in the run table). One jcac run
  similarly at attempts=2. The runner itself was killed AFTER run 12
  validated (23:35:56), during teardown — lost only the buffered summary,
  one leftover kind cluster (deleted), and the DAILY tick (recovered via
  no-op resume, which re-validated 12/12 and printed the official
  `ok: true`).
- Run-level table exported and committed:
  `eval/results/phase7_live_runs.csv` (runs ⋈ metrics); the duckdb stays
  gitignored per policy.

### What changed (docs + thesis; no Go/Python source)

- `research/analysis/PHASE7_ORDINAL.md` — the frozen script's output,
  committed verbatim; `RESULTS_MASTER.md` gains campaign entry 9a, a
  which-number row ("live validation — cite the end-to-end verification +
  parity DISAGREE; never 'sim ranking confirmed live'"), and an
  honest-nulls ledger entry.
- `docs/DEFENSE_QA.md` §14–19: the live-disagreement answer plus the five
  audit gaps named this session (tier-ratio sensitivity absent;
  calibration-asymmetry — congestion errs against us but the assumed cache
  curve erred FOR the matrix; prereg anchored in git forensics not a
  registry; per-stream→per-tenant extrapolation; no chaos campaign +
  security attack is model-level).
- Thesis: §6.10 rewritten around the measured table + frozen caption;
  abstract/intro/limitations/future-work updated (live = published
  disagreement, three-knob live substrate promoted to top future-work
  item); limitations gains six new bullets mirroring DEFENSE_QA §15–19;
  fairness section states the emergent-not-mechanism framing. Recompiled
  clean: 44 pp, 0 undefined refs.
- `PHASE7_JCAC_PLAN.md` marked EXECUTED; `DAILY.md` gains the smoke + live
  ticks.

### How it was verified

Runner validation JSON `ok: true` (12/12, 0 failed, 0 orphans) via no-op
resume; run table cross-read from the duckdb (read-only) before analysis;
pdflatex ×2 clean. The OOM-retry forensics come from `dmesg` timestamps
matched against `runs.recorded_at`. Codespace stopped after data pull to
conserve quota.

### What remains

RELEASE_CHECKLIST human items; thesis fill-ins (bib authors, title page,
proofread; slides to be re-cut into the user's own template); and the
user-gated Waves 1–4 leak-fill plan (tier-ratio prereg + h(K)-adoption
prereg + chaos + over-the-wire side channel + three-knob live substrate),
recorded in this session's conversation and DEFENSE_QA §15–19.

---

## 2026-07-15 (session 18) — Milestone 9 opened: thesis report and defense slides, compiled from the evidence base

Milestone status: M9 (Thesis and Publication Package) moves from empty to
substantially drafted. `thesis/report/` (43-page LaTeX report, compiles
clean) and `thesis/slides/` (16-frame beamer deck, compiles clean) now
exist; both are generated strictly from the committed measurement records,
with every number following RESULTS_MASTER.md's which-number-to-cite
discipline. Related-work matrix, viva Q&A, and evaluation figures (the
other M9 deliverables) already existed; the remaining M9 gap is the live
jcac ordinal figure (blocked on a Docker box) and human submission items.

### Audit findings

Clean tree except one uncommitted DAILY.md progress tick from the 7/13
phase7_sim_ref slice (12/12 valid) — committed as f3317a8. No regressions;
CI green through 650ce29.

### What changed (docs-only; no Go/Python source touched)

- `thesis/report/`: `main.tex` (report class, natbib, TikZ architecture
  figure), 8 chapters + claims-traceability appendix (`chapters/`),
  `references.bib` (31 entries from RELATED_WORK.md's verified URL list),
  `build.ps1`. Figures included directly from `eval/results/figures/*.pdf`
  (8 used). Structure: intro/thesis statement → related work (shape-vs-shape
  rule stated) → design + both THEORY_V2 propositions with scope →
  implementation incl. the two desk-found live-arm defects → pre-registered
  methodology → evaluation (all campaigns, wins AND nulls, scoreboard +
  which-number-to-cite tables) → discussion mirroring DEFENSE_QA → 
  conclusion → Appendix A mapping every claim to its evidence file,
  protocol commit, and regeneration script.
- `thesis/slides/`: 16-frame Madrid-theme deck along the same skeleton,
  honest-nulls framing kept (H1/H1′ FAIL slides included).
- `thesis/README.md`: build instructions + the four human fill-ins.
- `.gitignore`: LaTeX build artifacts; the two PDFs are committed
  deliverables.

### Honesty notes for the record

- A numeric discrepancy surfaced while sourcing the FIRM Jain claim:
  RESULTS_MASTER says d_z=1.0/p=4.5e-47, RELATED_WORK says 0.95/7e-44.
  Resolved by the campaign files per the master's own rule: both are real —
  v1 (RESULTS.md: 0.9689 vs 0.9294, dz=1.001, p=4.536e-47) and v2
  (RESULTS_V2.md: dz=0.9513, p=7.479e-44). The thesis cites v1 as headline
  with the v2 replication beside it. RESULTS_MASTER is NOT edited (its
  number is v1's and is correct).
- `references.bib` uses placeholder author fields ("Chiron authors") for
  arXiv-only sources whose author lists were not verified — flagged in
  thesis/README.md as a pre-submission fill-in. No fabricated authors.
- Title-page macros (university, supervisor, degree, full name) are
  placeholders in `main.tex`.
- The live section (§6.10) states the true Phase 7 status: hpa arm
  verified, jcac wired+gated, live slice pending; frozen caption referenced,
  not pre-claimed.

### How it was verified

pdflatex (MiKTeX 25.12) ×3 + bibtex: **0 undefined references, 0 undefined
citations**, 43-page main.pdf, all 8 figure PDFs embedded (checked in
main.log); one residual 9.6pt overfull hbox (cosmetic). Slides: 2 passes,
16-page slides.pdf, no errors (metropolis theme failed on missing
mathkerncmssi fonts → switched to stock Madrid + lmodern). Visual
proofread NOT done on this machine (no PDF renderer available to the
agent); the compile log is the verification. Go/Python gates not re-run:
no source files changed this session.

### What remains (unchanged, all outside this machine)

The jcac live session (`--jcac-smoke` then `--full` on a Docker box) and
its ordinal figure into §6.10; RELEASE_CHECKLIST account items (Zenodo DOI,
Artifact Hub, GHCR images, demo video, pages site, cloud smoke, arXiv/FGCS);
the four thesis fill-ins in thesis/README.md; a human proofread of both
PDFs.

---

## 2026-07-13 (session 17, round 2) — the leak-fill round: every field- and reviewer-perspective gap closable from this machine, closed

Milestone status: after the morning's jcac wiring, the session pivoted
(user directive: "fill all the leakings") to the weakness list from the
field/reviewer audit. Everything that could be closed honestly without a
Docker box or an account action is now measured or documented, committed,
and pushed. Two new real-data measurements ran under frozen protocols.

### New measurements (both protocols frozen + pushed pre-run)

1. **Cache hit precision on real LMSYS** (`CACHE_PRECISION.md`,
   protocol f1b2988): the correctness denominator the hit-rate segment
   lacked. 100k answerable (prompt, response) pairs, MiniLM both sides,
   argmax-neighbor serving. At τ=0.85: hit rate 24.1%, response-agreement
   precision 0.313 overall / 0.443 same-model / 0.353 first-turn (ρ=0.70
   declared pre-run), 165.5 incorrect hits per 1k queries. The τ≥0.95
   near-duplicate row (0.338) doubles as the proxy's stochasticity
   ceiling: 25 models + sampling temperature dominate the absolute level,
   so the citable claims are the relative readings (precision rises as τ
   tightens; same-model ≈ 2× cross-model). Entered into the scoreboard,
   which-number-to-cite, and the honest-nulls ledger.
2. **Second real LLM trace** (`FORECAST_AZURE.md`, protocol 573c197 +
   declared pre-run amendment 399f86a — the 2023 in-repo files hold ~1 h;
   the 2024 release assets are the one-week data): Azure LLM inference
   2024 (44.1M requests, two production workloads) through the identical
   BurstGPT decomposition. **The v3 H2′ mechanism boundary reproduces on
   independent data**: seasonal wins −15.6% RMSE exactly on the stream
   with a real daily cycle (code, autocorr 0.57 @ lag 24), loses +48.5%
   on the weakly-periodic conv stream (persistence wins), and pooling
   destroys forecastability (seasonal +104%) — direct external support
   for per-tenant forecasting and for the pluggable-forecaster design;
   across three real streams no fixed method dominates.

### New engineering measurements

- **Planner scaling** (`PLANNER_SCALING.md`): the deployed
  `PlannerCore.plan` path from 8→256 tenants; fitted growth exponent
  1.45 (jointness has a measured super-linear price — the first draft
  template assumed linear and the data corrected it); p95 crosses the
  3 s operator timeout at 128 tenants, the 10 s period at 256; past the
  timeout the failure mode is the designed fallback. DEFENSE_QA §13
  upgraded from scoping note to measured bound.
- **Effect sizes** (`EFFECT_SIZES.md`): both real-trace campaigns
  restated as paired d_z with 95% bootstrap CIs from the committed run
  tables — the citable unit, displacing astronomical p-values; all n=96
  confirmatory intervals exclude 0; the +0.07 violation trade carries
  its own CI.

### New documentation (the reviewer-perspective fills)

- `docs/DEFENSE_QA.md`: 13 hard examiner questions pre-answered with
  evidence pointers (substrate, one-axis live figure, overload-cell
  construct validity, p-values, goalposts, fairness altitude, cache
  quality, cost economies, 2026-stack positioning, baseline strength,
  p95, trace scope, tenant scale).
- `RELATED_WORK.md` §4: substrate mapping — each knob onto the
  llm-d/AIBrix/Dynamo actuation layer; llm-d/AIBrix/Dynamo/Gateway-API
  sources added.
- README/eval-README: limitations gain token-level-latency, fairness
  altitude, cost-economies, cache-quality, and live one-axis
  disclosures; the ordinal-figure caption is frozen verbatim in
  `PHASE7_JCAC_PLAN.md`; new scripts in the reproduction table;
  `HELM_VALUES_BY_SYSTEM` scoped (only hpa/jcac rows are live-wired).

### Verification

Full gates after every chunk: gofmt/vet clean, go test 26 packages pass,
41 Python tests pass, redocly valid, CI green through `6e97b26`. Bug
tail on the Azure path, each fixed+committed: 2023-vs-2024 release
mix-up (amended pre-run), tz-aware timestamp mix (naive/aware sort
crash), curl exit-18 resume.

### What remains (unchanged, all outside this machine)

The jcac live session (`--jcac-smoke` then `--full` on a Docker box),
the RELEASE_CHECKLIST account items, and the thesis document itself.

---

## 2026-07-13 (session 17) — jcac live arm fully wired in code; two desk-found defects that would have burned the cluster session

Milestone status: PHASE7_JCAC_PLAN.md steps 1–5 are done and locally
tested. The only measurement left in the project is now literally one
command on a Docker-capable machine:
`bash scripts/phase7_kind_run.sh --jcac-smoke`, then `--full`.

### Two defects found at the desk, not in the Codespace

1. **The demand plumbing could never have authenticated.**
   `FeatureDemandSource` sent its token as `Authorization: Bearer`, but
   `authorizeTenant` treats any bearer strictly as a JWT — the operator
   would have received 401 for every tenant, every cycle, and the JCAC
   loop degrades by design into silent fallback: the arm would have run
   "live" while planning nothing. Fix: the features endpoint (that
   endpoint only) accepts the platform admin key — the same trust
   argument as admin-minted first API keys (16d) — and the demand source
   gained an `AdminKey` header mode. Unit tests cover: admin key reads
   any tenant's features, wrong key is 401, and the admin key still
   cannot touch other tenant routes.
2. **Actuation was last-writer-wins on a shared target.** All 8 eval
   tenants' Policies name `polyforge/polyforge-control-plane`; each
   reconcile wrote its own tenant's replica count over everyone else's.
   The shared Deployment would have sat at ~2 replicas against 8 tenants
   of demand — jcac crippled by wiring, not by its planner, which is a
   mislabeling exactly as bad as the fixed-replica one the honesty gate
   exists to prevent. Fix: Policies resolving to the same target now sum
   their clamped contributions (pool-tenant semantics); deletion
   re-enqueues siblings so the sum shrinks. Status.AppliedReplicas stays
   the tenant's own share.

### What else changed

- `cmd/operator`: `POLYFORGE_FEATURES_ADMIN_KEY`, and planner ceilings
  via `POLYFORGE_PLAN_LIMIT_{REPLICAS,CACHE_MB}` (invalid values exit
  loudly rather than silently becoming defaults).
- Operator chart: `features.{url,token,adminKeySecret}` values feed the
  operator env; `planner.limits.{replicas,cacheMB}` render as env.
- Harness: `operator_install_plan()` (secret → chart install → CR apply →
  `kubectl wait --for=condition=Applied`, the honesty gate made
  executable), `operator_crs()` (Policy/Budget **before** Tenant to win
  the ensureDefaults race; every number mirrored from the sim world),
  operator/planner image side-loads for the jcac arm only.
- Capacity parity became a cell property: every arm starts at
  `replicaCount = tenants × 2` and is ceilinged by
  `CLUSTER_SIZES[size].limits_replicas` (hpa `maxReplicas`, jcac planner
  limits) — committed under test so no arm gets more cluster than another.
- `eval/experiments/phase7_jcac_smoke.yaml` + `--jcac-smoke` runbook mode;
  the runbook now builds all three images.
- OpenAPI: features path documents the AdminKey scheme; lint green.

### How it was verified

```text
gofmt -l .                              -> clean
go vet ./...                            -> pass
go test ./... -count=1                  -> pass (new: aggregation x2,
                                           admin-key features, AdminKey header)
python -m pytest eval/tests -q          -> 34 passed (4 new)
python -m pytest services/planner -q    -> 7 passed
npx @redocly/cli lint api/openapi.yaml  -> valid
```

Not verified locally: anything requiring Docker/kind/helm binaries — the
chart renders are covered by CI's `helm lint`/`helm template` job, and the
jcac arm itself remains **not live-verified** (that is the next session,
by design, behind the now-executable Applied gate).

### Immediate next tasks

1. Push; confirm CI green (helm job validates the chart changes).
2. The live session: `--jcac-smoke`, fix the bug tail, then `--full`
   ordinal slice + the paired figure (PHASE7_JCAC_PLAN.md).
3. Thesis document and slides; RELEASE_CHECKLIST human items.

---

## 2026-07-13 (session 16e) — Phase 9 hardening done; jcac live arm staged and sized

Milestone status: "complete everything except the thesis document."
Everything automatable is now done; the one measurement left in the whole
project is Phase 7's jcac-vs-hpa ordinal figure, and it is deliberately
gated, not forgotten.

### Phase 9 — done (the automatable half)

- **CI `trace-smoke` job**: BurstGPT ETL (synthetic, seeded) and the
  semantic-cache protocol (synthetic + hash fallback — dependency-free by
  design) now run on every push; a broken trace pipeline fails CI instead
  of a future measurement session. Both steps verified locally first.
- **README**: "Reproducing the research results" quickstart (every claim →
  committed script) and a "Limitations (honest boundaries)" section — the
  substrate framing, both SLO nulls, p95 deviation, cache scope, the
  gated jcac live arm, and the γ null, stated in the project's own words.
- **Zenodo bundle refreshed**: `polyforge-eval-artifact-2026-07-13.zip`
  (21 files) + SHA-256 manifest + deposit.json regenerated. Upload/DOI is
  account-bound (RELEASE_CHECKLIST §1), as are Artifact Hub, GHCR pushes,
  the demo video, and the paid cloud smoke — human-action items by design.

### Phase 7 jcac arm — staged, sized, honestly gated

`Dockerfile.operator` and `services/planner/Dockerfile` now exist (neither
did; GHCR has no published images) — the planner image preserves the repo
layout because planner.py resolves `research/jcac_sim` relative to its own
path. `docs/PHASE7_JCAC_PLAN.md` is the exact one-session remainder:
operator-chart install in the harness, per-tenant Tenant/Policy CRs,
demand plumbing (POLYFORGE_PLANNER_URL + features API), the
Policy→Deployment actuation check (the only potentially non-trivial code),
then the first jcac live smoke and the ordinal slice. The honesty gate
stands: jcac does not run live until actuation is verified.

### State of the whole project after this session

Measured and closed: all five contested segments + security, ADR 0016,
J-sensitivity, Phase 6 (both halves), Phase 7 hpa-arm live verification,
Phase 9 automatable hardening. Remaining, in total: (1) the jcac live
session per PHASE7_JCAC_PLAN.md; (2) RELEASE_CHECKLIST human items
(Zenodo upload, Artifact Hub, GHCR, video, €10 cloud smoke); (3) the
thesis document and slides — explicitly excluded from this push.

## 2026-07-12 (session 16d) — Phase 6 complete (GPU tier table measured); Phase 7's pipeline verified on a real cluster

Milestone status: "do phase 6 and 7 properly." Phase 6 is now fully done —
both measurement halves are committed with raw data. Phase 7 crossed from
"never executed" to "verified live" for the hpa arm; the ordinal figure
awaits only the operator/planner chart wiring.

### Phase 6 GPU half — measured on a free Kaggle GPU kernel

Six kernel versions to a clean run, each failure a committed fix: no phone
verification (no GPU/internet — user verified), P100 pool vs modern torch
(no sm_60 kernels → pinned cu118 fallback), transformers/torch ABI skew
(pinned contemporaries), broken torchvision import (removed — absent is
fine, broken is fatal). `TIER_BENCH.md` + `tier_bench.csv`: small
1242 ms / 38.7 tok/s, mid 1883 ms / 25.5 tok/s, large 20 665 ms / 2.3 tok/s
(disclosed CPU-offload upper bound). Tier ordering confirmed; the 1:10:100
price table is market pricing, not GPU-seconds — framed, constants
unchanged. Engine deviation (transformers, not vLLM) documented in-file.

### Phase 7 — the first cluster-backend execution in the project's history

GitHub Codespace (`.devcontainer/` + sshd), driven end-to-end over `gh`.
**Five blockers found and fixed, every one committed the moment it was
understood:**
1. helm chart path was cwd-relative → parsed as a repo name (8d77770);
2. the chart was *undeployable since W10*: runAsNonRoot cannot verify
   distroless's named user → CreateContainerConfigError (17db05b);
3. the SQLite emptyDir was root-owned for a 65532 pod → fsGroup (0ae10a3);
4. every harness step addressed deployment/service `polyforge` while the
   chart names them `polyforge-control-plane` (0ae10a3);
5. no HTTP path could mint a fresh tenant's first API key — bootstrap gap
   closed: the admin key (which already gates tenant creation) may create
   keys (25d3bfd, unit-tested).

**Attempt 5: `ok: true` — a valid live run with sane physics.** hpa arm,
ai_cacheable, 30 steps: ai p95 20.008 ms against the replay endpoint's
20 ms chat burn, crud p95 1.009 ms against 1 ms, zero violations against
standard-class targets, infra cost injected from live replica metering.
The eval-export numbers are exactly what the load's construction predicts,
which is the strongest validity check a smoke can give.

Honesty gates kept: the jcac arm was never run live — the operator/planner
is not in the chart yet, and a fixed-replica pod must not be recorded
under PolyForge's name. That wiring plus the PolyForge-vs-HPA ordinal
slice is what remains of Phase 7's acceptance figure.

### Verification

Local gates green throughout (gofmt/vet/full go test incl. the new
bootstrap test, pytest 31/31); every fix pushed with green CI. The live
verification itself ran on kind v0.29 / metrics-server / autoscaling/v2
inside the Codespace; the smoke DuckDB stays in the Codespace (results are
environment-bound smoke evidence, not campaign data).

## 2026-07-12 (session 16c) — Phase B: the cache headline on real LMSYS-Chat-1M; the last open segment closes

Milestone status: user provided the HF token; the LMSYS-Chat-1M gate was
already accepted on their account. The five contested segments now all have
real-data resolutions.

### The measurement

The committed protocol needed a feasibility amendment before it could touch
the real dataset: 2M+ user turns make its dense query×inserted similarity
matrix ~40 TB. The amendment (seeded reservoir sample n=200,000; block-wise
exact NN over recency-ordered vectors — verified bit-identical on the
synthetic set; a supplementary fixed baseline at 10% of inserted so the
committed 200-entry point isn't a strawman at scale; the Phase 3b h(K)
ladder) was **committed and pushed (31c73c7) before a byte of the gated
data was downloaded** — the pre-registration chain holds.

As measured (`SEMANTIC_CACHE.md`, 200,000 of 2,015,645 user turns, MiniLM,
exact cosine NN):

- **Adaptive hit rate 29.6% @ cosine 0.85, 48.8% @ 0.70** — the same order
  as InstCache's 51.34% on this dataset, under a different mechanism
  (they pre-populate predicted instructions; we cache observed prompts).
  Beside the anchor, never head-to-head (ground rule 4).
- **Adaptive +93% vs the 10%-fixed baseline** (29.6 vs 15.3). The committed
  200-entry point reads +594% but is a strawman at 100k inserted — the
  amendment exists precisely so we don't have to cite it.
- **$0.296 saved per 1k queries** at mid-tier pricing — the dollar axis no
  cache-only paper reports.
- **h(K) fit: hmax=0.285, K_half=6,569 entries (~19 MB at ~3 KB/entry).**
  The sim's assumed curve (max 0.85, half-point 256 MB) is *optimistic* on
  real traffic — real prompts saturate earlier and lower. Documented next
  to the assumption; `model.py` constants unchanged (bit-reproducibility;
  adopting empirical h(c) = new prereg, session 16b precedent). This is a
  deliberate deviation from the plan's "feed into model.py" wording,
  surfaced here rather than silently applied.

### Environment note

pyarrow was absent (known from the BurstGPT session) — installed for
parquet reads. The HF token lives in the standard local credential cache
(`~/.cache/huggingface/token`), never in the repo.

### Scoreboard after this session

Cost WON (synthetic + real, confirmatory) · SLO closed-honest (iso-attainment
+ mechanism + spike-class exploratory) · Fairness WON (FIRM + VTC-replica) ·
Forecasting WON (Holt on real trace) · **Cache CLOSED (real-LMSYS headline +
adaptive win + $-axis)** · Security WON (frontier). Remaining work is
environment/user-bound (Phase 6 GPU half on a T4, Phase 7 execution in a
Codespace, Phase 9 hardening) — and the thesis document itself.

## 2026-07-12 (session 16b) — Phase 6 CPU calibration measured; Phase 7 reduced to one command

Milestone status: user asked for Phases 6 and 7. Phase 6's CPU path — the
half designed for this machine — is **measured and done**; its GPU path is
scripted for a user-run free T4. Phase 7's code blockers are gone and the
whole live run is now one command in a Docker-capable environment; the run
itself still needs that environment (this laptop has none).

### Phase 6 — congestion model calibrated against a real inference server

`research/calibration/measure_congestion.py`: llama.cpp llama-server (CPU),
Qwen2.5-0.5B Q4_K_M, 4 decode slots, fixed 48-token completions, prompt
caching defeated, open-loop Poisson arrivals so ρ is a controlled factor.
**As measured (`CALIBRATION.md`): the sim's g(ρ)=1/(1−ρ) holds to first
order — fitted exponent a=0.86 vs the asserted a=1, R² of the a=1 model
0.857.** The deviation is *conservative against the lean system*: at
ρ=0.84 the model predicts 6.4× inflation, the batching server delivers
3.7× — so the sim overtaxes exactly the high-utilization regime JCAC's
cost-lean postures live in, while over-provisioned baselines sit at low ρ
where the model is accurate. The cost wins were not bought with a rosy
congestion model. Measured p95/mean is 1.59–2.41 vs the sim's flat 1.4 —
an underestimate of tails that applies to every system through the same
estimator, noted for the limitations chapter. ρ≥0.95 (the capped quadratic
overload branch) is deliberately uncalibrated: no finite-time steady state
exists there; it is an optimizer-gradient device and documented as such.

Measurement integrity trail: the first full sweep was **discarded** — the
Go/pytest suites ran concurrently on the same cores and pass-1/pass-2
disagreed up to 3.5× at ρ=0.92. The protocol gained shuffled two-pass
levels and pre/post capacity measurement, and the committed numbers come
from a rerun on an idle machine (pre/post μ within 1.6%). `model.py`
constants are unchanged; committed matrices stay bit-reproducible.

GPU path: `kaggle_tier_bench.py` (vLLM, Qwen 0.5B/3B/7B-AWQ on a free T4)
is ready; it is user-run because it needs a Kaggle/Colab login.

### Phase 7 — from "environment-blocked" to "one command"

- `control-plane eval-export` (integration point 1) implemented and
  unit-tested: harness-schema metrics from the pod's telemetry store, SLO
  targets and tier prices mirroring `model.py`; infra cost injected via
  `--infra-cost-usd` (replica-hours are invisible in-pod), exported as a
  separate component.
- Real orchestration bug fixed: `command_plan` never side-loaded the
  locally built image into kind — every pod would have hit
  ImagePullBackOff on the first live run. `kind load docker-image` step
  added and pinned by test.
- `.devcontainer/` + `scripts/phase7_bootstrap.sh` give GitHub Codespaces
  every preflight tool; `scripts/phase7_kind_run.sh` is the runbook;
  `eval/experiments/phase7_live.yaml` is the 12-run ordinal slice
  (dry-run verified). Integration points 2 (chart toggles) and 3
  (`/v1/workloads/replay` + k6 tenant tokens) stay open, documented in
  eval/README.md — they are the first live session's work, as the roadmap
  always said.

### How it was verified

All local gates green (gofmt, vet, full go test, harness pytest 31/31,
phase7 dry-run). **CI run 29194999487 on 839decc: success on all five
jobs — and its PostgreSQL 18 service executed the session-16 Postgres
truncation and RLS tests for the first time. The Postgres path of ADR 0016
is now proven, not assumed.**

### Still blocked, and by what

- **Phase B / cache headline:** the LMSYS-Chat-1M gate — user HF token.
- **Phase 6 GPU half:** user-run Kaggle/Colab session (script committed).
- **Phase 7 execution:** a Docker-capable machine; a free GitHub Codespace
  on this repo now satisfies preflight out of the box.

## 2026-07-12 (session 16) — gap-closing: truncation disclosure (ADR 0016), a latent time-encoding bug, and the J weight-sensitivity sweep

Milestone status: user override ("do the remaining work that fully solves").
Sessions 14–15 recorded themselves in their plan/prereg/results commits rather
than here; this entry resumes the log. Everything below was done in one
session; no simulator campaign was run and no closed campaign file was touched.

### What was built

**1. `MaxFeatureEvents` truncation is now disclosed, not silent (ADR 0016).**
The session-13-era open decision is resolved: `FeatureSet` gains a required
`truncated` boolean, surfaced verbatim over HTTP and required by the OpenAPI
schema. Detection is exact — each store selects `MaxFeatureEvents+1` rows and
the overflow row proves truncation before being discarded — and lives in one
shared helper (`telemetry.ClampFeatureEvents`), so the three stores cannot
drift on which prefix survives. The in-memory store now also selects in
timestamp order, matching the SQL stores' `ORDER BY timestamp` exactly (the
new unit test inserts the newest event first to catch insertion-order drift).

**2. A latent window-correctness bug, found and fixed.** Writing the
truncation test with sub-second timestamps exposed that `encodeTime` in the
SQLite store used `RFC3339Nano`, which trims trailing zeros — and TEXT-encoded
time columns compare lexicographically in every window bound and `ORDER BY`.
`"…43.001Z" < "…43Z"` as strings, so a `since` at a whole second silently
excluded same-second fractional events from the analytical path. No prior
test used fractional timestamps, which is why five milestones of green suites
never caught it. Fix: `encodeTime` now emits a fixed 9-digit fraction (string
order ≡ chronological order), and `migrate()` pads legacy rows in place
(idempotent, length-guarded, all nine time columns). The regression test
covers the nastiest edge: a legacy whole-second row exactly at `until` must
stay excluded from the half-open window after a mixed-format reopen.

**3. Composite-J weight-sensitivity sweep — declared exploratory, run once.**
`sensitivity_j.py` freezes its grid in the docstring before execution
(w_v ∈ {0, 0.5, 1, 2, 4} × w_f ∈ {0, 0.25, 0.5, 1, 2}, cost ≡ 1) and re-reads
the five closed campaign sources read-only; cost_norm is recovered from each
campaign's own J by exact algebra, never re-derived. Validation: the
pre-registered cells reproduce the published numbers exactly (v1 gptcache
p=4.6e-25, v2 p=6.7e-25, VTC p=3.3e-19, replay p=5.5e-05). Result
(`SENSITIVITY_J.md`): **the paired ΔJ direction never flips in any of the 425
cells; 416/425 keep p<0.01.** All 9 exceptions sit at w_v=4 — double the
pre-registered violation weight — and lose only significance, exactly where
the disclosed +0.07 violation trade predicts fragility. This is
threats-to-validity context, not a new claim; the (2, 0.5) numbers remain the
only citable ones.

**4. Scope hygiene.** `docs/RELATED_WORK.md` gap table gains two rows: the
explicit descope of `etl_azure_functions.py`/`etl_alibaba_v2018.py` (FaaS/VM
traces, not LLM-serving demand; kept as tooling, any future use needs a new
prereg — `etl_lmsys_chat1m.py` is *not* descoped, it is Phase B) and the
weight-sensitivity mitigation. `V3_SEGMENT_WIN_PLAN.md` brought current:
Phase D marked DONE (VTC HV1+HV2 PASS), the powered n=96 replay recorded with
the −70%-is-citable pointer, the stale unpushed-commits warning resolved.

### How it was verified

```text
gofmt -l .                              -> clean
go vet ./...                            -> pass
go test ./... -count=1                  -> pass (all packages)
npx @redocly/cli lint api/openapi.yaml  -> valid
python sensitivity_j.py                 -> wrote SENSITIVITY_J.md (416/425)
```

Not verified locally: `TestPostgresFeaturesReportTruncationAtEventCap` (and
the pre-existing RLS tests) **skip** without Docker. They compile and vet;
CI's PostgreSQL service is the first environment that will execute them.
Treat the Postgres truncation path as unproven until a CI run is green.

### Still blocked, unchanged

- **Phase B (cache headline, the last open segment):** hard-blocked on the
  user — HF account, LMSYS-Chat-1M gate acceptance, token, then
  `pip install sentence-transformers`. No token exists on this machine
  (checked env vars and HF cache paths); nothing was faked.
- **Phase 6 (GPU calibration) / Phase 7 (live kind run):** environment-blocked
  (no GPU, no Docker). These are the only levers left against the sim-substrate
  gap and the p95→p99 deviation.

### Immediate next tasks

1. Commit and push; confirm green CI (proves the Postgres truncation test).
2. User unblocks Phase B → run the frozen `semantic_cache_eval.py` protocol.
3. Session-19 slice from the plan: README v2/v3/replay numbers + Phase 9
   hardening; then thesis writing (thesis/report and thesis/slides are empty).

## 2026-07-11 (session 13) — v2 Phases 1–5 + 8: the full segment-win push, executed under pre-registration

Milestone status: on the user's explicit "full power, beat all 5 segments"
override of the one-slice cadence. Phases 1, 2, 4, 5, 8 and the local half of
Phase 3 complete; all engineering built and locally verified. **2,900 new runs
(2,100 + 600 + 200), 100% valid, zero failures.** Nothing here touches the
immutable v1 headline; every result is additive and each "win" is reported as
measured, including the two honest nulls.

### What was built and found, by segment

**Phase 1 — v2 headline matrix (2,100 runs, `matrix_v2.yaml`).** `jcac_v2`
(Holt + adaptive_capacity, the two mechanisms session 11 showed each help)
run over the same 300 blocked cells as the v1 matrix. `analysis_v2.py` →
`RESULTS_V2.md` executes the pre-registered H1/H2. **H1 FAILs as measured**:
v2's SLO violation is at *parity* with HPA/KEDA, not significantly below —
though at −43%/−48% cost — so per prereg §6 the null is published and v1 stays
the headline system. **H2 PASSes**: v2 is −11% cost vs v1 (p=4e-36) with no
large-effect regression. v2 does beat FIRM on SLO (p=2e-19). The honest read:
combining the two forecast/calibration mechanisms buys cost, not the SLO
segment — which the pre-registration was written precisely to be able to say.

**Phase 2 — iso-cost baselines + gate v2 (600 runs, `isocost.yaml`).**
`static_isocost` / `cache_isocost` pinned at budgets derived from v2's own
realized per-cell spend (`eval/baselines/isocost.py`, ceiling rounding strict
against PolyForge; resolved per run by `eval/harness/isocost.py`). Making
`static` iso-cost **flips its gate row from a 1/5 loss to a 3/5 PASS** — its
v1 SLO/Jain "wins" were bought with 12× overspend. The overall gate stays FAIL
because `cache_isocost` still outspends ~36× (36/60 cells are budget-infeasible
even at 1 replica — the honest quantification of the structural confound).

**Phase 4 — fairness γ-term made testable (200 runs, `fairness_v2.yaml`).**
Added noisy-neighbor interference injection to `simulate.run`: a tenant serving
>2× its fair share steals up to 50% of co-tenants' capacity (billing nominal),
and JCAC sees the observable detector score. This is the W32 signal v1
documented as absent. Result is the **negative branch, robustly**: even in the
whale-only subgroup where injection fires, γ-removal shows no significant
degradation (all p>0.1) and slightly *helps* SLO — the joint cost/SLO optimizer
already absorbs interference. The term is dropped from claims; the segment is
resolved either way. `fairness_v2.py` → `FAIRNESS_V2.md`, worst-tenant p95 added.

**Phase 5 — security defense frontier (fig. 17).** Extended
`cache_side_channel.py` with response-time padding and TTL jitter, swept on
fixed grids against the same attack. **Per-tenant partitioning dominates the
frontier**: chance-level AUC (0.50) at 24% latency cost retaining 76% of hits —
strictly lower-left of both mitigations (padding never below AUC 0.73; TTL
needs to discard ~90% of hits to reach chance). Claim upgrades from "we have a
defense" to "partitioning dominates the known frontier."

**Phase 3a — BurstGPT pipeline + forecast win.** `research/traces/etl_burstgpt.py`
(real BurstGPT schema + `--synthetic` reproducing daily periodicity and
heavy-tailed tokens, same pattern as the Azure ETL). `forecast_trace.py` buckets
the trace hourly and re-runs the four forecasters: the seasonal detector locks a
**24-bucket daily period (autocorr 0.85)** and seasonal — neutral on synthetic
noise in session 11 — now **cuts one-step RMSE 44.8% vs trend** (Holt is +35%
*worse*, chasing the diurnal ramp). Exactly the V2_README prediction, and the
strongest clean segment result of the session.

**Phase 3b — semantic-cache protocol.** `semantic_cache_eval.py`: insert-first-
half / query-second-half / threshold-sweep with a pluggable embedder
(all-MiniLM-L6-v2 when installed, dependency-free char-n-gram fallback
otherwise) and exact cosine NN. Reports fixed-vs-adaptive hit-rate curves and
dollar-weighted savings (the metric cache-only papers can't report). Runs
offline on synthetic conversations; pointable at real LMSYS + MiniLM with an HF
token.

**Phase 8 — theory.** `THEORY_V2.md`: Prop 1 (two-sweep coordinate descent over
the finite lattice → block-coordinate/unilateral-move optimum, with an explicit
note it is *not* the global optimum under the non-separable Jain coupling) and
Prop 2 (calibration scale forward-invariant in [0.5,1], humble-only, contracts
to 1 in ≤25 agreeing steps). Both proved by construction, line-referenced to
`controller.py`.

### How it was verified

- **68/68** Python tests pass (`research/jcac_sim`, `eval/tests`,
  `research/security`), including 8 new v2 tests: interference scoring/gate,
  capacity theft through `evaluate_step`, injection no-op without a dominant
  tenant, and the two iso-cost baseline controllers.
- Every new analysis module was run to completion and its report inspected;
  fig. 17 was eyeballed for layout, not just generated. The affine-cost identity
  behind `static_isocost` was checked against the simulator to the cent.
- No local Docker/GPU/HF-gated work was claimed: Phase 3 real-dataset replays,
  Phase 6 calibration, and Phase 7 live run remain, and are marked `[~]` in
  V2_README with the exact blocker.

### Deliberate deviations (surface, don't "fix")

- Two pre-registered/accepted **nulls** are headline outcomes, not failures: H1
  (SLO parity) and the γ-term (dropped). This is the pre-registration working.
- The Phase 3b fallback embedder yields *lexical*, not semantic, hit rates —
  labeled inline; real numbers need the gated dataset + deps.
- Synthetic traces (`out/*.csv.gz`) stay gitignored like the existing ones; the
  ETL script is the committed artifact.

### Immediate next tasks

1. Push (prereg timestamp + this work). Phase 0 commit f8aa8e6 is still local.
2. Real-dataset halves of Phase 3 (BurstGPT 10M download; LMSYS HF token + install
   sentence-transformers/faiss) — pipelines are committed and waiting.
3. Phase 6 calibration (CPU llama.cpp / free-GPU vLLM) and Phase 7 live kind run
   (Docker VM) — the only remaining phases, both needing environments this laptop
   lacks. Phase 9 hardening (CI smoke jobs, `run_v2.py` dispatcher, RELATED_WORK.md).

## 2026-07-11 (session 12) — v2 Phase 0: pre-registration committed before any v2 run

Milestone status: v2 phase per `docs/V2_README.md` begun. Phase 0 complete;
Phases 1–9 untouched. No v2 matrix cell has been run — that ordering is the
point of this session.

### What was done

**`research/analysis/PREREG_V2.md`** — dated configuration freeze, written
before any v2 run per V2_README ground rule 1. Pins: the exact `jcac_v2`
parameters (`forecast_method: "holt"`, `adaptive_capacity: true` — the two
Tier-2 mechanisms session 11 showed independently helped, now combined) and
the fact that these two keys are the *complete* effective params (tuned.yaml
has no `jcac` entry to merge in); the v2 headline matrix design (same 300
blocked cells as `full.yaml`, 7 systems, 2,100 runs, new output DB
`raw_sim_v2.duckdb` so the v1 DB is never reopened); the confirmatory
hypotheses — H1: SLO violation below HPA and KEDA at paired p<0.01 with the
cost point estimate ≤ 0, no significance escape hatch on cost; H2: any
large-effect regression vs v1 `jcac` blocks promotion; gate v2 (same ≥3/5,
p<0.01, |d_z|≥0.5 thresholds, baselines iso-cost, budgets matched per cell
with ceiling rounding — strict against PolyForge); and the stopping rule
(one matrix run, analysis run once, negative results published, any
parameter change requires a new dated pre-registration).

**`eval/harness/systems.py::SYSTEMS["jcac_v2"]`** — the pre-registered
SystemSpec, one registry entry, no change to any existing system.

### How it was verified

- Smoke through the real matrix code path (`harness.sim_backend.execute`,
  scratchpad script): merged params assert-equal to exactly the two
  pre-registered keys; a 30-step `ai_cacheable/uniform/small` cell runs
  valid; the same cell at the same seed under v1 `jcac` produces different
  cost/latency — the combination is demonstrably active, not silently
  ignored.
- `python -m pytest eval/tests/ -q`: 28 passed. Existing systems untouched.

### Next tasks

1. Phase 1: `eval/experiments/matrix_v2.yaml`, run the 2,100-run matrix
   locally, verify 100% valid.
2. Extend `run_analysis.py` for the v1-vs-v2 paired comparison →
   `RESULTS_V2.md` (RESULTS.md immutable).
3. Then Phase 2 iso-cost baselines per the pre-registered definitions.

## 2026-07-11 (session 11) — Advanced work: forecasting, self-calibration, reconfiguration realism, and a cache side-channel security contribution

Milestone status: post-roadmap depth work, on the user's demand to make
the project "more advanced" and add a novel contribution. All engineering
built and locally verified; two new experiments (200 + 175 runs) ran
100% valid. Nothing here changes the committed 1,800-run headline — the
new work stacks on top and is reported as such.

### What was built

**Tier 2A — pluggable forecasters (`research/jcac_sim/controller.py`).**
`Forecast` gained four methods behind one interface: persistence (the
no-lookahead floor), trend (the W30 original), damped Holt smoothing, and
online-seasonal (autocorrelation period detection on a *detrended*
series, seasonal-naive forecast, falling back to trend below correlation
0.4). Exposed as JCAC system variants and swept in `forecasters.yaml`.
**The forecast ablation is an ablation that improved our own system:**
the linear-trend default *overreacts* to single-bucket noise — Holt beats
it by 16% on SLO violation (p=0.0027, d_z=−0.45) at statistically equal
cost; persistence does about as well; seasonal is neutral where no period
exists. Holt is now the documented recommended default, but trend stays
the code default so the committed headline stays reproducible (the gain
stacks, it isn't folded in). This is the honest inversion of my first
auto-generated narrative, which had wrongly called lookahead
"load-bearing" — corrected against the numbers.

**Tier 2C — self-calibrating capacity (`JCACController`,
`adaptive_capacity`).** The controller compares each step's realized
violation to what it projected and learns a per-tenant capacity
correction bounded to [0.5, 1.0] — it may become humbler, never
optimistic. Applied as demand inflation in `_project`; fed by a new
`observe_feedback` hook the replay engine calls each step.

**Tier 2B — reconfiguration realism (`simulate.run(transition_costs=)`).**
Scale-ups take one interval to serve (replica startup lag) and grown
caches start half-warm, while billing follows the nominal config
immediately — you pay when you ask, benefit one step later; scale-downs
instant. Controllers aren't told, so thrashing is punished and hysteresis
finally earns its keep. `effective_state()` computes what actually
serves. Under this model (`realism.yaml`, 175 runs) self-calibration
beats the base controller (d_z=+0.46, p=0.029 on violation) — it earns
its keep specifically where the world underperforms the model.

**Novel contribution — cross-tenant cache timing side channel
(`research/security/cache_side_channel.py`).** A shared semantic cache
(GPTCache's default posture) is a covert channel: a hit means *some other
tenant* recently asked a similar prompt, so response time alone leaks
prompt membership across tenants. The study quantifies it end to end on
PolyForge's own latency/hit-rate constants: a membership-inference
threshold classifier reaches **AUC 0.88** against a shared cache and
**0.50 (chance)** against PolyForge's per-tenant bounded cache (W28) — the
isolation eliminates essentially all exploitable signal. It also measures
the cost: naive equal-split isolation loses 29% of the aggregate hit
rate, the joint planner's demand-proportional sizing cuts that to 24%.
Novel framing: prior semantic-cache work optimizes hit rate; treating the
shared cache as a covert channel and quantifying the isolation/efficiency
trade-off is new, and PolyForge's existing per-tenant design is the
defense. Backed by a new Go invariant test
`TestCacheGivesNoCrossTenantHit` (internal/ai/gateway) — a probe by one
tenant must miss another tenant's identical prompt.

**Analysis + delivery.** `research/analysis/advanced.py` produces figures
13–16 (forecast ablation, realism Pareto, adaptive-under-realism, the
side channel) and `ADVANCED.md`; wired into `run_analysis.py` (skips
cleanly without the DBs). CSV exports committed; pitch page and README
updated with the security result (stat strip now leads with 0.88→0.50).
CI gained the security-study run.

**Hallucination fixes.** The Helm chart pointed at a fictional
`github.com/polyforge/polyforge` org and `ghcr.io/polyforge/*` images —
corrected to the real `mohaimin-8` repo. Also removed a compiled
`control-plane.exe` that had been committed at the repo root and added a
`pyrightconfig.json` so the IDE resolves the `sys.path`-injected sim
imports (was a persistent false-positive diagnostic).

### How it was verified

```
research/jcac_sim: python -m unittest   -> 33 tests OK (was 25)
services/planner:  python -m pytest     -> 7 passed
eval:              python -m pytest tests-> 28 passed (was 22)
internal/ai/gateway: go test            -> ok (incl. new side-channel invariant)
forecasters.yaml   -> 200/200 valid, 0 flakes
realism.yaml       -> 175/175 valid, 0 flakes
cache_side_channel.py -> AUC 0.88 (shared) vs 0.50 (per-tenant)
```

### What to be able to explain next

- Why the trend forecaster overreacts and Holt fixes it — and why we
  report the gain separately instead of swapping the default.
- Why self-calibration is invisible in the ideal model but real under
  reconfiguration realism (it corrects a model/world gap that only exists
  when capacity lags).
- The threat model of the cache side channel: what the attacker observes,
  what "membership inference" means for prompts, and why per-tenant
  isolation is a complete rather than partial mitigation.

### Immediate next tasks

1. Push; confirm CI (python job now also runs the security study) green.
2. Optional: promote Holt to the default in a fresh experiment name
   (`full_holt`) so a second headline exists without touching the first.
3. Still the human items in RELEASE_CHECKLIST.md (cloud runs, Zenodo,
   Artifact Hub, video) and M10 paper — §security now has its own
   subsection ready in ADVANCED.md.

---

## 2026-07-10 (session 10) — W33-W36 + W39: Month 9 complete — harness, baselines, 2,300 runs, statistics, OSS packaging

Milestone status: closes **all of M9 (Evaluation)** on the sim backend plus
the automatable half of **W39 (open-source polish)**. The user explicitly
asked to complete the whole remaining project scope (override of the ~5%
norm), excluding paper writing. What remains after this session is
exactly the set of things that need a human: paid cloud runs (~€10 on
Hetzner via the committed Terraform), Zenodo publish, Artifact Hub
publish, the demo video, and M10's paper/defense work —
`docs/RELEASE_CHECKLIST.md` enumerates each with exact steps. Honest
position: **all engineering 100% built and locally verified; the
cluster-backend path and cloud items are code-complete but unexecuted.**

### What was built

**W33 — harness (`eval/harness`).** One YAML describes an experiment
(systems × 5 taxonomy workload classes × 4 tenant mixes × 3 cluster
sizes × reps); the harness expands it into runs with sha256 run IDs,
executes on a backend, and lands everything in one DuckDB file (`runs` /
`metrics` / `timeseries`, `status` as the integrity contract). Resume
skips valid run_ids; failures retry then record as failed/invalid — never
dropped. Two backends: `sim` (drives `research/jcac_sim`; the verified
path) and `cluster` (kind + Helm + generated k6 replay + teardown;
code-complete, needs Docker and a `control-plane eval-export` CLI that
does not exist yet — contract documented in `eval/README.md`).
Repetitions are seeded Poisson resamples of the demand trace
(`simulate.jitter_buckets`), so reps are independent draws, not identical
replays. Metric note: latency is **p95** (the model's estimator), not the
roadmap's p99 — documented deviation.

**W34 — baselines.** `research/jcac_sim/baselines.py` gained
**FIRM-replica** (OSDI '20 RL controller re-implemented as per-tenant
tabular Q-learning over (ρ-bucket, violating), replica knob only, seeded)
and **GPTCache-LRU** (cache-everything posture + HPA replicas + reactive
tier). `static` gained the over-provisioned-to-peak mode. All controllers
parameterized; `tune.py` grid-searched each on a tuning slice disjoint
from evaluation cells, scored by the paper's own objective
(J = cost + 2·viol + 0.5·(1−Jain)). Best-of-grid committed
(`tuned.yaml`, sweeps in `grids/*.csv`): hpa ρ=0.3, keda 2 rps/replica,
gptcache ρ=0.3, firm lr=0.1/ε=0.1/w_slo=4. Grids are bounded at the
vendor-sane envelope because J improves monotonically toward
over-provisioning — the degenerate end is already represented by
`static` (TUNING.md documents this; it is a finding, not a dodge).
Eviction accounting: baselines evict LRU, so their inference spend is
scaled by the W28-measured LRU/cost-aware ratio (geometric mean 1.458,
loaded from `eviction_comparison.csv`).

**W35a — smoke + IaC.** 50-run smoke: 50/50 valid, 0 flakes, 34 s with 4
workers. Three real bugs found and fixed before the full matrix
(`eval/SMOKE_BUGS.md`): (1) `hash()`-based tuning seeds are randomized
per process — replaced with sha256 everywhere; (2) unconstrained tuning
degenerates utilization baselines into static — bounded grids; (3) the
KS reproducibility gate false-alarmed under multiple comparisons (4
tests at α=0.1 ≈ 34% familywise false-positive; an empirical 6-group
bias test showed the harness itself unbiased) — Holm-Bonferroni fixed
the gate, and the same correction discipline applies to paper claims.
Terraform for a 4-node Hetzner k3s cluster committed
(`eval/infra/terraform`, ~€7.60 for the 7-day W35b window); **not
applied** — cloud spend is a user decision.

**W35b/c — the runs.** Full matrix: **1,800/1,800 valid, zero flakes,
~31 min wall at 6 workers** (the roadmap budgeted 7 days on cloud
hardware; the sim backend is why). Ablations: 500/500 valid. Validation:
exact expected run_id sets, zero duplicates, zero NULLs, every valid run
has metrics. Spot-check: 10 (full) + 5 (ablations) random runs replayed
**bit-identically** (tolerance was ±3%). Run-level CSVs are committed
(`eval/results/metrics_*.csv.gz`); raw DuckDBs are gitignored and go in
the Zenodo bundle (`scripts/archive_zenodo.py` → 2.6 MB zip + sha256
manifest + deposit.json, ready for upload).

**W36 — statistics (`research/analysis/`).** Scripted analysis instead
of the roadmap's notebook (deliberate deviation: scripts diff and re-run
deterministically). Primary claim, paired by matrix cell (the matrix is
a blocked factorial design, so per-cell differences are the correct
test): **PolyForge beats every tuned baseline on the composite objective
with p ≤ 4.6e-25 and |d_z| 0.66–1.44** (J = 0.402 vs 0.555–7.54). It
also wins cost against every baseline (|d_z| 0.66–1.12) and is the only
Pareto-undominated system (fig. 3). The roadmap's raw per-metric gate
("≥3 of 5 metrics vs every baseline") **fails as measured and is
reported as FAIL**: `static` wins SLO-shaped metrics by construction at
~12× cost, `gptcache` wins hit rate at ~30× — you cannot out-violate a
baseline that never violates, only match it at radically lower cost.
RESULTS.md carries that framing verbatim for the paper. Ablations
(paired): removing the joint controller costs +2884% (p<0.01), removing
cost-aware eviction +35.7% (p<0.01), removing the classifier +2.1% cost
and −45% cache hit rate (p<0.01); the fairness ablation is **untested,
not refuted** — the sim never injects the W32 interference signal γ
responds to (limitation for §9). Two-way ANOVA (system × workload) and
12 publication figures (600-DPI PNG + vector PDF, validated
color-blind-safe palette, CIs on everything) in
`eval/results/figures/`, each caption answering "what does this prove".

**W39 — OSS packaging.** `polyforge-operator` Helm chart
(`deploy/helm/polyforge-operator`: CRDs, RBAC, operator + optional
planner with weight values; planner gained `--alpha/--beta/--gamma`
default-override flags — request weights still win). ARCHITECTURE.md
(the whole loop, one diagram), CONTRIBUTING.md, CODE_OF_CONDUCT.md,
issue/PR templates, README overhaul, RELEASE_CHECKLIST.md. CI gained a
`python` job (simulator, planner, harness tests + a 4-run end-to-end
smoke) and a `helm` job (lint + render both charts) — before this,
nothing protected the Python half of the repo.

### How it was verified

```
research/jcac_sim: python -m unittest        -> 25 tests OK (was 16)
services/planner:  python -m pytest          -> 7 passed
eval:              python -m pytest tests    -> 22 passed
gofmt -l . / go vet / go test ./... -count=1 -> clean / pass / pass
full matrix validation                        -> 1800 valid, 0 dup, 0 NULL, all green
ablations validation                          -> 500 valid, all green
spot-check replays                            -> 15/15 bit-identical
KS reproducibility (Holm at family α=0.1)     -> green on all 4 gated metrics
```

Not verified locally (no Docker): the cluster backend end-to-end, helm
lint/template (CI will run it), `terraform apply`. None are claimed.

### What to be able to explain next

- Why the per-metric roadmap gate *cannot* pass against a tuned baseline
  set that includes static-overprovisioned, and why the paired
  composite-objective sweep is the defensible headline instead.
- Why paired-by-cell d_z is the right effect size for a blocked
  factorial design, and what the pooled d answers instead.
- Why the tuning objective drives utilization targets toward
  over-provisioning, and what that says about single-knob controllers.
- Why the fairness ablation is silent on the sim backend and what
  cluster evidence would activate it.

### Immediate next tasks

1. Push; confirm the new CI jobs (python, helm) are green.
2. Human items in order of leverage: Zenodo publish (10 min), first
   cloud smoke on Hetzner (needs `eval-export` CLI, ~1 day + ~€2),
   Artifact Hub + demo video, then M10 paper writing from
   RESULTS.md + FIGURES.md.

---

## 2026-07-10 (session 9) — W29-W32: Month 8 complete — operator, JCAC, planner loop, fairness

Milestone status: closes **all of M8 (Research II)** — W29 Kubernetes
operator + CRDs, W30 JCAC formulation + offline simulator, W31 live
planner loop, W32 noisy-neighbor fairness. The user asked for "the next
10%" (explicit override of the ~5% norm); the roadmap places end-of-W32
at 90%, and the honest position is **~88-89%** — the deltas are the
live-cluster items (kubectl-against-a-real-API-server verification,
Pixie/eBPF collection, the 24-hour soak), each designed behind an
interface, unit-verified, and documented as deferred to the W33 harness
environment rather than claimed. Four commits, one per week.

### What was built

**W29 (ADR 0013).** `internal/operator` on controller-runtime v0.22:
four cluster-scoped CRDs (Tenant, WorkloadProfile, Policy, Budget) with
status subresources and printcolumns, deepcopy + CRD manifests generated
by controller-gen via `go run` (no kubebuilder scaffold — single-module
repo). TenantReconciler ensures namespace (silo tenants own one;
pool/bridge share `polyforge-tenants` per ADR 0008), read-only tenant
RBAC, and default Policy/Budget/WorkloadProfile it never overwrites
after creation; a finalizer deletes dependents in order. PolicyReconciler
clamps replicas to `[replicaMin, replicaMax]` and patches the target
Deployment + per-tenant ConfigMap. Money is integer milli-USD and
confidence is permille because CRD schemas reject floats.

**W30 (ADR 0014).** JCAC as receding-horizon MPC over a discrete action
lattice (`research/jcac_sim`): α·cost + β·log1p(SLO excess) + γ·(1−Jain)
over a 60s horizon, re-planned every 10s; budget, cluster-cache, and
cluster-CPU constraints; switch-penalty hysteresis. CVXPY was deliberately
dropped: the problem is mixed-integer (integer replicas, categorical
tier), which CVXPY's free solvers cannot express — the ≤60-candidate
move-blocked lattice is solved *exactly* by enumeration with two rounds
of coordinate descent across tenants. Two modeling lessons worth
remembering: saturating penalties kill optimizers (violation capped at 1
and congestion capped flat left deep overload gradient-free — the fix is
graded overload and log1p shaping), and per-layer baselines need to be
strong to be meaningful (`layered` runs HPA + a cache autoscaler + a
latency-reactive tier rule, all locally sensible).

**W31 (ADR 0014).** The planner is a stdlib-only Python HTTP service
(`services/planner`) importing the simulator's controller verbatim —
offline figures and online decisions come from provably the same solver.
The Go `PlanRunner` (a manager Runnable, not a per-policy reconcile)
gathers all tenants + feature-API demand, requests **one joint plan**
every 10s, re-clamps to Policy bounds, writes specs, and publishes every
action to the existing NATS audit stream. Planner failure = specs
untouched (last good plan keeps running) + one status flip to
`fallback`. JSON-over-HTTP instead of gRPC (no protoc locally) is
isolated behind the `planner.Client` interface.

**W32 (ADR 0015).** Noisy-neighbor detector scoring each tenant's
eBPF-shaped signals (PSI CPU stall, syscall rate, memory pressure)
against the *cluster median* — a uniformly busy cluster flags nobody.
3 noisy windows flag (30s), 6 clean windows clear. The score reaches the
planner in the plan request; expansion of a flagged tenant pays
γ·score·resource_share, so mitigation flows through the same guarded
plan/apply/audit path as every action. Jain's index is exported per
cycle as `polyforge_operator_fairness_jain`.

### Key numbers (azure_synth, seed 42, first 20 min, defaults α=1 β=2 γ=0.5)

| controller | cost | mean violation | Jain |
|---|---|---|---|
| static | $0.538 | 0.099 | 0.940 |
| hpa | $0.348 | 0.102 | 0.938 |
| keda | $0.348 | 0.102 | 0.938 |
| layered | $11.549 | 0.085 | 0.941 |
| **jcac** | **$1.598** | **0.054** | **0.977** |

The 16-point α/β sweep traces a 12-point Pareto front that touches HPA's
operating point (α=10, β=2: $0.372 at violation 0.101) and extends to
violations no single-layer scaler reaches (0.044) — HPA/KEDA's residual
violations come from the model tier, a knob they cannot see. That, not
any absolute dollar figure, is the paper's claim.

### How it was verified

```text
gofmt -l .                              -> clean
go vet ./...                            -> pass
go test ./... -count=1                  -> pass (operator: 21 Go tests)
python -m unittest (research/jcac_sim)  -> 16 tests pass
python -m unittest (services/planner)   -> 7 tests pass
sweep + pareto.png                      -> regenerated deterministically
```

Cross-language contract: `TestLivePlannerContract` was executed against
the real Python planner (surge scales replicas within 3 cycles). It
caught a genuine bug: Go marshals zero-value `hourly_budget_usd: 0`,
which the planner correctly reads as "spend nothing" and sheds the
tenant to its floor — callers must always send a real budget.

Not verified locally (no Docker/kind): operator against a live API
server, planner pod deployment, Pixie/eBPF collection, the 24-hour soak.
All deferred to the W33 harness environment and marked in ADRs 0013-0015.

### What to be able to explain next

- Why the guardrail clamp lives in the operator when the planner already
  respects bounds (no plan source is trusted, including humans).
- Why detection is peer-relative (median-normalized) rather than
  threshold-absolute, and what Jain's index does and does not measure.
- Move blocking: what it costs (within-horizon action sequences) and why
  the 10s re-plan makes that acceptable.

### Immediate next tasks

1. Push; confirm CI green (first execution of operator tests in CI).
2. W33: benchmark harness — kind provisioning, `experiment.yaml`-driven
   runs, DuckDB result store; this is also where the deferred
   live-cluster verifications land.
3. Decide the Pixie-vs-Hubble question with cluster access in hand.

---

## 2026-07-10 (session 8) — W25a-W28: Month 7 complete — analytics pipeline, traces, classifier, cost-aware eviction

Milestone status: closes **all of M7 (Research I)** — W25a analytical
telemetry, W25b trace acquisition + replay, W26 taxonomy + feature
pipeline + model, W27 online classification + drift, W28 cost-aware
eviction. The user asked for "the next 10%" (explicit override of the
~5% norm); the roadmap places end-of-W28 at 80%, and the honest position
is **~78-79%** — the deltas are the real-dataset items (downloads gated
or multi-GB) and live-cluster runs (no Docker here), each implemented,
unit-verified, and documented as pending rather than claimed. Session
started by pushing the previously-unpushed W20-W24 backlog; CI went
green on it (run 29054784590) before new work landed on top.

What changed, per week:

- **W25a** (ADR 0011, `docs/CLICKHOUSE_DESIGN.md`): ClickHouse chosen
  over Postgres/Timescale for the analytical store. DDL with justified
  `PARTITION BY toYYYYMM` / `ORDER BY (tenant_id, timestamp)`, an
  AggregatingMergeTree minute rollup for the W27 poll loop, OTel
  collector pipeline with file-backed queue (the no-loss path), and an
  in-process best-effort mirror (bounded batcher, JSONEachRow over HTTP,
  drop accounting) that never blocks or fails ingest.
- **W25b**: three ETL pipelines to the normalized
  `trace_event(timestamp_ms, tenant_id, request_kind, payload_bytes,
  expected_latency_ms)` schema, each with a `--synthetic` mode that
  exercises the identical normalize path. Go replay driver (`cmd/replay`,
  in-repo deviation from k6 like `cmd/loadgen`) with weighted tenant-mix
  mapping and seeded determinism; the Go `trace_hash` reproduces the
  Python `stream_hash` byte-for-byte (cross-language fixture is a test).
- **W26** (ADR 0012, `research/TAXONOMY.md`): 12-feature vector as the
  single implementation both the API and the trainer read; pure-Go
  softmax LR with deterministic training and a drift-guarded JSON
  artifact (feature/class-name mismatch refuses to load). ONNX/XGBoost
  deliberately deferred to real-trace data — comparing model families on
  synthetic windows would rank noise. Rules baseline drops to 0.885
  macro-F1 under 30%-contaminated windows; the LR holds 1.0 (pipeline
  validation, not a research claim — labeled as such everywhere).
- **W27**: online classification loop inside the control plane
  (documented deviation from a separate microservice), 10s default
  interval, label-change events to NATS, PSI drift detector (decile
  bins, 0.25 threshold; the 3σ-shift scenario is a test), bounded label
  history, admin API + zero-dependency dashboard at `/admin/workloads`.
  OpenAPI updated and lint-clean. Live-verified end to end: booted the
  server, ingested 45 AI-shaped events, watched `/v1/admin/workloads`
  report AI-cacheable at 0.9999 confidence.
- **W28** (`research/paper/sec-eviction.tex`): eviction weight
  `w = L + cost × smoothed-hit-rate × staleness × 1/(ε+density)` with
  GreedyDual inflation aging; five baselines (LRU, LFU, GDSF, ARC,
  GPTCache threshold+LRU) behind one Policy interface; benchmark over a
  seeded LMSYS-shaped stream (Zipf, topic drift, paraphrase clusters,
  70/25/5 tier mix) in two capacity regimes. Honest result, stated as
  such in the paper section: 24-38% cheaper per request than every
  deployed-practice policy, statistical tie with GDSF on synthetic
  exact-match traffic (the density term needs real substitute traffic to
  differentiate — the real-LMSYS replay is the deciding experiment).
  The live `SemanticCache` gained an optional per-tenant capacity bound
  using the cost-aware policy; capacity is per tenant because a global
  budget would be a cross-tenant interference channel (tested).

### How it was verified

- `gofmt`/`go vet`/`go test ./... -count=1` clean at every commit; new
  packages (`analytics`, `replay`, `classifier/online`, `eviction`) also
  run under `-race`.
- Redocly lint on the extended OpenAPI spec: valid.
- Determinism proofs actually executed: repeated ETL runs → identical
  hashes; repeated `cmd/replay -dry-run` → identical schedule hashes;
  repeated `cmd/classifier-train` → identical printed metrics.
- W27 verified against the running binary, not just httptest (see above).
- **Not verified here**: PostgreSQL RLS suites (skip without Docker),
  live ClickHouse (stress plan documented in `docs/CLICKHOUSE_DESIGN.md`),
  real trace downloads, Ollama throughput numbers. None are claimed.

### Immediate next tasks

1. W29+ (M8): kubebuilder operator scaffold — Tenant/WorkloadProfile/
   Policy/Budget CRDs and reconcile loops; manifests are writable here,
   cluster verification is not.
2. First Docker-capable machine: ClickHouse stress run, Postgres RLS
   suites, real-trace ETL + 5k RPS replay gate.
3. Consider surfacing the analytics batcher's drop counter on /metrics.

---

## 2026-07-09 (session 7) — W20-W24: LLM routing, tenant isolation ladder, canary, Vault/OIDC, security hardening

Milestone status: closes the *implementable-on-this-machine* core of W20
(local LLM serving + routing policy), W21 (multi-tenant isolation modes),
W22 (mesh + canary), W23 (Vault + OIDC + secret scanning), and W24
(network policy + OWASP + supply chain). The user explicitly asked for the
W20-24 block in one session (overriding the usual ~5% slice), with license
to deviate from the HTML where a better path exists. Honest roadmap
position after this session: **~70-71%** against the roadmap's 72% marker
for end-of-W24 — the gap is exactly the cluster-execution items this
machine cannot run (live Ollama benchmark numbers, Linkerd mTLS tap,
live Keycloak login, ZAP scan, compromised-pod simulation), all of which
now have runnable harnesses/runbooks and pending-status documentation.

### What changed — W20 local LLM serving + routing (ADR 0007 extended)

- **Native Ollama adapter** (`gateway.Ollama`, `/api/chat` NDJSON): the
  native API reports `prompt_eval_count`/`eval_count`, which the benchmark
  needs for honest tokens/sec. Tool calls map both directions.
- **Policy router** (`gateway.Router` + `deploy/routing.json`): routes
  /chat per request by tenant plan, prompt length, and a per-tenant daily
  budget (UTC-day ledger; exhaustion degrades to the cheapest backend
  rather than failing). Decision exposed via `X-PolyForge-Backend` /
  `X-PolyForge-Route-Reason` headers; usage charged post-response from
  provider-reported tokens. Routing happens only on cache miss. This
  static rule table is the baseline JCAC (M8) will replace.
- **Benchmark harness** (`internal/ai/bench`, `cmd/llmbench`): identical
  streaming instrumentation for every backend; TTFT = first delta,
  throughput = tokens/(total−TTFT); per-request CSV to `benchmarks/`,
  aggregate markdown for `docs/INFERENCE_BENCH.md`. **No live numbers
  published** — no Ollama on this machine; the doc says so explicitly.
- Compose gains an `ai` profile (ollama + one-shot model puller).

### What changed — W21 isolation ladder (ADR 0008)

- `isolation_mode` was already a column; it is now a *state machine*:
  `POST /v1/tenants/{id}/isolation` (admin-only) promotes pool → bridge →
  silo, upward-only (skip allowed, downgrade 409). Implemented in all
  three stores; Postgres takes `FOR UPDATE` on the tenant row; every
  promotion writes a `tenant.promoted` outbox event in-transaction, so
  namespace provisioning can be event-driven later.
- `deploy/k8s/tenant-silo-template.yaml`: Namespace + ResourceQuota +
  LimitRange per silo tenant (envsubst-instantiated).
- Deliberate deviation, documented in the ADR: bridge's schema-per-tenant
  data migration is specified but **not implemented** — zero-downtime
  copy/cutover needs a real Postgres to prove and this machine has none.

### What changed — W22 mesh + canary (ADR 0009)

- **Two canary layers.** Mesh: `deploy/linkerd/` (install runbook, mTLS
  verification commands, SMI TrafficSplit 95/5 over new
  `deploy/k8s/ai-gateway.yaml` v1/v2 deployments; inject annotations added
  to workloads). App: `gateway.CanaryProvider` — deterministic
  position-mod-100 split between model hosts with a sliding-window breaker
  that auto-rolls-back on error spike. The roadmap's rollback gate is a
  unit test (`TestCanaryErrorSpikeAutoRollsBack`), not a manual step.

### What changed — W23 secrets + OIDC (ADR 0010)

- `internal/secrets`: `Dir` (Vault-Agent files; re-read per call, so
  rotation is instant — beats the 60s gate) → `Vault` (KV v2, Kubernetes
  auth with lease-aware token renewal) → `Env` chain. Control plane admin
  key and gateway LLM key now resolve through it.
- `internal/auth/oidc.go`: stdlib OIDC relying party — discovery, JWKS
  with rotation-tolerant refetch, S256 PKCE, code exchange, ID-token
  validation with alg pinned to RS256. Tested end-to-end against a fake
  IdP that *enforces* PKCE server-side; negative tests cover wrong aud,
  expired, wrong issuer, tampered payload.
- CI gains a `secret-scan` job (gitleaks, full history) and an `sbom` job
  (syft CycloneDX artifact). `deploy/vault/`, `deploy/keycloak/` dev
  manifests + runbooks.

### What changed — W24 hardening

- `deploy/k8s/networkpolicies.yaml`: default-deny both directions, then
  DNS + ingress→services + control-plane→{postgres,redis} +
  ai-gateway→ollama; postgres/redis also pin inbound to the control plane.
- `docs/SECURITY.md`: OWASP API Top 10 (2023) line-by-line with code
  citations, threat model, supply-chain section, and an explicit
  known-gaps list (API7 egress allowlist is network-level only; ZAP and
  pod-pivot simulation pending Docker).
- `release.yml`: tag-triggered image push to GHCR + cosign keyless sign +
  CycloneDX SBOM attestation. `scripts/zap-baseline.sh` for the pen-test.
- OpenAPI spec documents the new isolation endpoint (redocly-valid).

### How it was verified

```text
gofmt -l .                              -> clean
go vet ./...                            -> pass
go test ./... -count=1 -cover           -> all pass (bench 89%, gateway 74%,
                                           auth 81%, secrets 70%, tenant 83%)
npx @redocly/cli lint api/openapi.yaml  -> valid
```

Not verified locally: `-race` (no gcc on this machine — CI runs it),
PostgreSQL suite incl. the new `PromoteTenantIsolation` (skips without
Docker; CI executes it), golangci-lint (not installed; CI runs v2.6.2),
gitleaks/sbom/release workflows (first execution on push), and everything
listed above as needing a cluster. Treat all of those as unproven until
the next green CI run.

### What to be able to explain next

- Why the router charges budget from *reported* usage post-response
  rather than estimating pre-route, and what a provider that reports no
  usage does to the ledger (answer: nothing — cost 0; the bench estimates
  at 4 chars/token but the ledger never guesses).
- Why isolation demotion is a 409 and not a feature.
- Why the canary splits deterministically (position mod 100) instead of
  randomly, and what that does to test reproducibility and low-traffic
  rollouts.
- Why the ID-token verifier pins RS256 (alg=none / RS-HS confusion).

### Immediate next tasks

1. Push; confirm CI green including the two new jobs — that run is the
   proof for -race, Postgres promotion test, gitleaks, and SBOM.
2. First live run of `cmd/llmbench` on a machine with Ollama; paste the
   table into docs/INFERENCE_BENCH.md and commit `benchmarks/inference.csv`.
3. W25 (M7 research phase) starts the telemetry-pipeline/classifier work —
   the routing policy and isolation ladder built this session are its
   actuators.

---

## 2026-07-09 (session 6) — ADR 0006 events cluster + M5 AI cluster (W14/W15, W17-W19)

Milestone status: closes the W14/W15 events cluster whole (outbox, NATS
relay, audit events, onboarding saga, CQRS projection — the slice ADR 0006
specified) and lands the M5 AI-workload MVP: W17 embeddings + vector
search, W18 LLM gateway with semantic cache and streaming, W19 tool-use
agent loop. Two commits, each cluster landing with its tests, plus one
CI-fix commit. CI is green (65.4% coverage). Honest roadmap position after
this session: ~59-60% (W19 is the roadmap's own 59% marker; pgvector and
local model serving are the noted deferrals).

### What changed — events cluster (commit 1)

- **Transactional outbox in all three stores.** Every write path (tenant
  provision/create/delete, project create/update/delete, key
  create/revoke/rotate) inserts the domain row and an audit event in the
  same transaction — memory store under the same mutex, SQLite and
  PostgreSQL in the same tx (`003_outbox.sql`; app role gets INSERT only,
  RLS scopes it to its tenant; the outbox has deliberately no FK to
  tenants so the stream outlives its subjects). Shared constructors in
  `internal/tenant/audit.go` keep payloads byte-identical across backends
  and never include secrets or hashes.
- **Relay + JetStream backbone (`internal/events`).** The relay drains
  unpublished rows in insertion order and publishes with
  `Nats-Msg-Id = outbox.id`. Crash between publish and mark-published is
  the tested case: the retry republishes and JetStream's duplicate window
  drops it — `TestRelayRetryAfterCrashDoesNotDuplicate` proves stream
  count stays 1 against an embedded `nats-server` (no Docker, per ADR
  0006's verification stance).
- **Onboarding saga (`internal/saga`).** create tenant → bootstrap key →
  default-policy event → welcome event; every step idempotent, every step
  with an explicit compensation (event-only steps compensate by emitting a
  `saga.compensated` fact — appended events cannot be unappended). State
  persists in the DB (`sagas` table in SQLite/Postgres, memory for tests).
  Tests cover the happy path, mid-saga failure with full compensation
  (tenant gone, created+deleted both in the stream), crash-resume from a
  persisted cursor without minting duplicate keys or events (deterministic
  saga event IDs + `INSERT OR IGNORE`/`ON CONFLICT DO NOTHING`), and
  terminal-state stickiness.
- **CQRS projection.** `tenant_summary` folds the stream into per-tenant
  counters; the regeneration test replays the stream twice and asserts
  byte-identical JSON, then cross-checks counts against the write model.
- `cmd/control-plane` starts the relay when `POLYFORGE_NATS_URL` is set;
  without it events accumulate in the outbox and publish on the first
  start that has a backbone — which is the outbox's whole point.

### What changed — AI cluster (commit 2, ADR 0007)

- **W17 (`internal/ai/embed`, `internal/ai/vector`).** One `Embedder`
  interface; `OpenAI` speaks any OpenAI-compatible `/embeddings` endpoint,
  `Local` is a deterministic hashed n-gram embedder that makes every
  downstream assertion exact and offline. The vector index is exact
  per-tenant brute-force cosine — the recall=1.0 baseline pgvector/HNSW
  will be benchmarked against (`BenchmarkSearch10k`). Tenant isolation is
  structural (one map per tenant), tested by asserting another tenant sees
  zero results.
- **W18 (`internal/ai/gateway`).** Chat proxy over the OpenAI wire
  protocol with SSE streaming both up and down; per-tenant semantic cache
  at 0.95 cosine over the full conversation (fails open on embedder
  errors, `X-PolyForge-Cache: hit/miss` headers, hit/miss counters on
  `/metrics`); tenant-scoped semantic search (`/ai/index`, `/ai/search`).
  Auth reuses the control plane's API keys and scopes. Every AI request
  records a telemetry event, so the classifier sees AI workload classes.
- **W19 (`internal/ai/agent`).** OpenAI tool-calling loop with three
  tools: `polyforge_query` (tenant's projects via the same repository as
  the HTTP API — isolation inherited, not reimplemented), `polyforge_calc`
  (recursive-descent arithmetic parser, no eval), `polyforge_search`
  (injected engine; DuckDuckGo scrape in the binary, fake in tests). Tool
  failures go back to the model as tool output so it can recover; the run
  errors after 8 steps. `agent.run` → `llm.call`/`tool.call` spans give
  the multi-span causal chain, and `child_spans = llm_calls + tool_calls`
  lands in telemetry — the agentic-multistep feature the W26 classifier
  reads.
- **`cmd/ai-gateway`** (`:8081`): second service binary sharing the
  control plane's SQLite database; defaults to Ollama's local endpoint and
  the local embedder, so it runs with zero external dependencies.

### How it was verified

```text
gofmt -l .                                   -> clean
go vet ./...                                 -> pass
staticcheck ./...                            -> clean
go test ./... -count=1 -covermode=atomic     -> pass, total 60.5% local
```

Local coverage of 60.5% is with the PostgreSQL suite skipped (no Docker on
this machine). CI (race detector + PostgreSQL 18) is GREEN on `be57419`
with **65.4% total coverage** — after one CI-only failure that proves why
the CI-first stance matters: the first run of `003_outbox.sql` failed
`TestPostgresRowLevelIsolation` with `permission denied for table outbox`,
because `INSERT ... ON CONFLICT (id)` needs SELECT privilege on the
arbiter column and the app role is deliberately INSERT-only on the outbox.
Fix: the tenant-transaction path (always fresh random IDs) drops the
conflict clause; deterministic-ID idempotent appends stay on the
admin-role `AppendEvent`. The green run is the first execution of the
PostgreSQL outbox, its RLS policy, and the INSERT-only grant.

### Deliberate deviations from the roadmap's W17-W19 letter

- pgvector + HNSW deferred (needs a CI Postgres image with the extension);
  the exact in-memory index is the measured baseline it must beat.
- No real model host in CI: scripted providers + the local embedder keep
  the gate deterministic. The wire protocol is tested against fakes that
  speak real OpenAI JSON/SSE.
- Ollama/local serving (W20) untouched — next cluster.

### Immediate next tasks

1. W20 local model serving: point `cmd/ai-gateway` at Ollama and demo the
   agent end-to-end against a real model.
2. Choose the pgvector CI image and land the Postgres-backed vector index
   against the exact baseline (ADR 0007 consequence).
3. Add the gateway's AI endpoints to `api/openapi.yaml` (currently the
   spec covers only the control plane).

---

## 2026-07-09 (session 5) — W11 OTel migration, W13 Redis limiter, W14 idempotency, W16 5k-RPS gate

Milestone status: closes W11's SDK migration (traces now come from the OTel
SDK, not the hand-rolled traceparent parser), W13's sliding-window limiter
with per-tenant budgets, and W14's idempotency half; passes the W16
performance gate after finding and fixing a real bottleneck. The W14/W15
events cluster (NATS, outbox, saga, CQRS) is deferred whole — one backbone
decision, not four half-features (ADR 0005).

### What changed

**W11 — OTel SDK tracing (`internal/platform`, `cmd/control-plane`).**

- `trace.go` (hand-rolled W3C parser/generator) deleted; the request
  middleware now extracts context with the OTel propagator, opens a real
  SDK server span, renames it to the low-cardinality route pattern after
  routing, and stamps method/route/status attributes. 5xx sets span error
  status and `writeError` records the error on the span.
- Logs carry `trace_id`/`span_id` from the span context — correlation
  survives the migration. Response `traceparent` is injected by the
  propagator; the old propagation tests were ported to parse with the
  propagator and still pass.
- `TestRequestsEmitOTelServerSpans` proves a request joins a remote trace,
  is a server span named `GET /v1/tenants/{tenant_id}/projects`, and
  carries the golden attributes.
- `main.go` builds the TracerProvider; spans export via OTLP/HTTP only when
  `POLYFORGE_OTLP_ENDPOINT` is set. Collector config at
  `deploy/observability/otel-collector/config.yaml` (+ compose service).

**W13 — Redis sliding-window rate limiter (`internal/limit`).**

- One Lua script over a sorted set: prune, count, admit, expire — atomic
  server-side. Proven exact under 200 concurrent goroutines (admits exactly
  the limit) and resident in the script cache (SCRIPT EXISTS), both against
  miniredis, so the logic is CI-verifiable without Docker.
- Platform accepts any `RequestLimiter`; the token bucket is the no-Redis
  fallback. Credentialed traffic on `/v1/tenants/{id}/...` shares one
  budget per tenant (two keys of one tenant proven to spend one budget);
  anonymous traffic stays host-keyed so strangers cannot drain a tenant.
- Redis unavailable ⇒ fail open with a warning (availability over
  strictness; rationale in ADR 0005). Proven by killing miniredis mid-test.

**W14 (half) — Idempotency-Key replay (`internal/idempotency`).**

- Claim (SETNX) → execute → cache response → replay on retry with
  `Idempotency-Replayed: true`; concurrent duplicate ⇒ 409; 5xx never
  cached. Key scoped to credential + method + path + key + body digest, so
  cross-tenant replay is impossible and a reused key with a different body
  executes as a distinct operation. Redis and in-memory stores share one
  contract test; the HTTP gate proves the write executed exactly once.

**W16 — performance gate, measured, with a real fix.**

- `loadgen` gained `-rate` (shared token dispatcher minting from measured
  elapsed time — immune to Windows timer granularity).
- First run: **~830 RPS ceiling, p99 216 ms** on the authenticated projects
  list. Root cause: `SetMaxOpenConns(1)` + default journal in the SQLite
  store — every request serialized on one connection.
- Fix: WAL + per-connection DSN pragmas (busy_timeout, foreign_keys,
  synchronous=NORMAL) + NumCPU connection pool. Also replaced the
  random-ID ordering tiebreak with `rowid` after the pool exposed a
  key-listing order flake (timestamps collide at Windows clock granularity).
- Gate run (5 minutes sustained, authenticated endpoint, SQLite):
  **5,266 RPS, p50 2.87 ms, p99 22.6 ms, max 160 ms, zero non-2xx, zero
  transport errors over 1,579,872 requests** — artifact at
  `artifacts/m4-projects-5krps.json`. Gate: ≥5,000 RPS, p99 < 50 ms.
  (A first 5-minute run at `-rate 5000` delivered 4,917 RPS — 98.3% —
  because the generator sheds tokens during transient scheduler stalls;
  the committed run targets 5,300 so delivered throughput clears the gate
  with margin. Server headroom, not saturation: p50 under 3 ms.)
- `docs/SERVICE_TEMPLATE.md` records the 14-point cross-cutting checklist
  every future service is reviewed against, each row tied to its proving
  test.

**Plumbing.** Redis (AOF) + otel-collector services in compose;
`POLYFORGE_REDIS_URL` switches limiter + idempotency to Redis. OpenAPI
0.6.0: `Idempotency-Key` parameter on the Projects mutating operations.
New deps: OTel SDK + OTLP/HTTP exporter, go-redis v9, miniredis (test).

### How it was verified

```
gofmt -l .                              -> clean
go vet ./...                            -> pass
go test ./... -count=1                  -> pass (10 packages, incl. 5x sqlite for the ex-flake)
npx @redocly/cli lint api/openapi.yaml  -> valid, 0 warnings
loadgen -rate 5000 -duration 5m         -> gate PASSED (artifact committed)
```

Not verified locally (Docker-blocked, unchanged): real Redis persistence/
failover, compose bring-up, kind/Helm, collector end-to-end, RLS tests.
`-race` still needs cgo ⇒ CI-only.

### What to be able to explain next

- Why the sliding window must be one Lua script (what breaks with
  ZREMRANGEBYSCORE + ZCARD + ZADD as three client calls).
- Why the idempotency key includes the body digest, and what Stripe does
  differently with the same collision.
- Why `SetMaxOpenConns(1)` was a 6x throughput ceiling and what WAL changes
  about reader/writer interaction.
- Why fail-open is right for rate limits and wrong for billing quotas.

### Addendum (same day): first push and first green CI

Repo published to `github.com/mohaimin-8/polyforge` (private). The first CI
run earned its keep immediately:

1. Lint gate caught 3 staticcheck QF1008 findings in `internal/auth/jwt.go`
   (explicit selectors through the embedded `rsa.PublicKey`) — fixed.
2. The RLS integration tests failed their first-ever execution with
   `role "polyforge_admin" does not exist`: the CI role-creation step used
   `docker exec` without `-i`, so psql received no stdin and exited 0
   having executed nothing. A green checkmark on a step that did nothing —
   fixed with `-i` and a warning comment.

Run 3 is fully green: gofmt, vet, golangci-lint, **race detector across all
packages** (first execution ever — no cgo locally), **both PostgreSQL RLS
tests against real Postgres 18** (multi-tenant isolation now proven, not
just designed), coverage 66.5% (gate ratcheted 55% → 60%), OpenAPI lint.
ADR 0006 (NATS JetStream backbone: subjects, outbox schema, relay dedupe
semantics, embedded-server test strategy) also landed, unblocking W14/W15.

### Immediate next tasks

1. Implement ADR 0006: outbox schema + relay → audit events → onboarding
   saga → CQRS projection (W14 remainder + W15), each landing whole with
   embedded-NATS tests.
2. Docker-capable host: compose up, Redis failover drill, kind/Helm, image
   size gate, SLO outage drill. Consider a CI job for the Docker build +
   Trivy to close the W9 gates without local Docker.

---

## 2026-07-09 (session 4) — W7 JWT layer closed; W9/W10/W12 deployment + SLO artifacts written (Docker-blocked, unverified)

Milestone status: closes the W7 remainder (RS256 JWT issuer, refresh
rotation, JWKS, key rotation without restart — the last open W7 item after
OAuth2/Hydra was deferred whole, see ADR 0004). Completes the four golden
signals at `/metrics` (W11's metrics deliverable; the OTel SDK migration
itself remains open). Writes the W9 Docker, W10 kind/Helm, and W12 SLO
artifacts, none of which can be executed on this machine.

### What changed

**`internal/auth` — stdlib RS256 JWT issuer (W7).**

- JWS compact serialization on `crypto/rsa` — zero new dependencies
  (ADR 0004). Verifier pins `alg` to RS256 before key lookup; tests attack
  it with `alg=none`, a foreign key, and a tampered payload.
- Refresh tokens: opaque, single-use, stored as SHA-256 digests in memory.
  Replay of a consumed token is 401. In-memory means a restart drops
  refresh sessions — accepted and documented in ADR 0004.
- Key rotation keeps the previous key verifiable: `TestKeyRotationWithoutRestart`
  proves pre-rotation tokens verify, new tokens carry a new kid, and the
  JWKS material itself verifies real signatures (reconstructed `rsa.PublicKey`
  from n/e, the way an external verifier would).

**Platform wiring.**

- `POST /v1/auth/token` (API key → token pair; JWT inherits the key's
  scope), `POST /v1/auth/token/refresh`, `GET /.well-known/jwks.json`
  (public), `POST /v1/auth/keys/rotate` (admin).
- `authorizeTenant` accepts `Authorization: Bearer` as a peer of the API-key
  header, with the same 401/403 contract; wrong-tenant tokens are 403.
  Bearer credentials get their own rate-limit buckets.
- The W7 gate restated for JWTs is automated: read-scope bearer token → 200
  on GET, 403 on POST.
- `/metrics` gains `polyforge_http_in_flight_requests` (saturation), so all
  four golden signals are now derivable from the endpoint.
- `control-plane -healthcheck` subcommand probes `/healthz` and exits 0/1,
  because the distroless image has no shell for container health checks.

**Deployment artifacts — written, NOT verified locally (no Docker).**

- `Dockerfile`: multi-stage, CGO off (modernc SQLite is pure Go),
  distroless/static-debian12 nonroot. The <20MB gate is unproven.
- `compose.yaml`: control-plane service added (postgres-healthy dependency,
  self-probe healthcheck). `docker compose up` has never been run here.
- `deploy/k8s/`: namespace, postgres (Recreate strategy, RLS roles via init
  ConfigMap), control-plane (2 replicas, probes, read-only rootfs), NGINX
  ingress `/api`. `deploy/helm/polyforge/`: chart templating image tag,
  replicas, resources, admin-key Secret, rate limits. Schema-reviewed only;
  `helm install` and the rollout-restart gate need a kind cluster.
- `docs/SLO.md`: 99.9% availability / p99 < 200ms objectives against the
  real metric names, error budget (43.2 min/30d), and the 14.4×/6× burn-rate
  alert expressions. Alerts are not loaded into Prometheus yet.
- OpenAPI 0.5.0: four auth paths, `BearerToken` scheme offered on all
  tenant-scoped operations, `TokenPair`/`JWKS` schemas. Redocly-clean.

### How it was verified

```
gofmt -l .                              -> clean
go vet ./...                            -> pass
go test ./... -count=1                  -> pass (auth: 7 tests; platform: +3 endpoint suites)
npx @redocly/cli lint api/openapi.yaml  -> valid, 0 warnings
```

Not verified: everything under "Deployment artifacts" above, plus the two
standing PostgreSQL RLS integration tests (still Docker-blocked). CI on
first push is the earliest environment that can prove any of it.

### What to be able to explain next

- Why pinning `alg` before key lookup kills both `alg=none` and RS256→HS256
  confusion, and why that ordering (not the library choice) is the security
  property.
- Why refresh-token rotation must consume the token even on the success
  path, and what replay window exists if it doesn't.
- Why 429 is excluded from the availability SLI in `docs/SLO.md`.

### Immediate next tasks

1. First `git push`: proves CI (race/lint/coverage), Postgres migrations,
   and can add a Docker build + Trivy job to exercise the W9 artifacts.
2. W11 proper: migrate hand-rolled traceparent/metrics to the OTel SDK
   (whole, not half — same rule as Hydra).
3. On any Docker-capable host: `docker compose up`, image-size gate,
   `helm install` on kind, and the SLO fake-outage drill.

---

## 2026-07-09 (session 3) — API-key scopes with RBAC, and the 30k-RPS gate beaten 2.4×

Milestone status: closes the roadmap's W7 deliverable "API keys with scoped
permissions" including its verification gate ("read-only key returns 403 on
POST"), and the W4 throughput gate ("≥ 30,000 RPS for a simple GET handler").
Commits: `a06f329` (scopes + RBAC), plus this session's benchmark commit.

### What changed

**Read/full API-key scopes, enforced per route (`a06f329`).**

- `tenant` package: `ScopeRead`/`ScopeFull` constants, `NormalizeScope`,
  `ValidScope`, and `ScopeAllows` (fails closed on unknown scopes and unknown
  requirements). `APIKey.Scope` is immutable for the key's lifetime and
  preserved by rotation — widening access requires a new credential.
- All three repositories persist scope. SQLite backfills legacy rows to
  `full` during migration (asserted by the legacy-migration test); PostgreSQL
  migration `002_api_key_scope.sql` adds the column with `DEFAULT 'full'` and
  a CHECK constraint. Backfilling to full rather than least-privilege was
  deliberate: least-privilege would silently break every existing
  deployment's credentials.
- `authorizeTenant` takes the route's required scope: GETs require `read`,
  mutations and telemetry ingest require `full`. 401 (bad credential) vs 403
  (valid credential, missing authority) is deliberate, with a structured
  `forbidden` error code.
- `TestReadOnlyScopeIsEnforced` automates the roadmap gate: a read key gets
  403 with code `forbidden` on every mutating route, 200 on every read route;
  a full key mutates; invalid scopes are 400.
- OpenAPI 0.4.0: `scope` on `APIKey`/`CreateAPIKey`, `Forbidden` response,
  403 documented on all 13 tenant-scoped operations. Redocly-clean.

**HTTP throughput baseline (W4 gate).**

- `cmd/loadgen`: Go load generator — fixed worker pool, per-worker latency
  slices (no locks in the hot path), context cancellation, JSON artifact with
  RPS/percentiles/runtime metadata. Doubles as the W3 concurrency reference
  implementation.
- `scripts/bench-http.ps1`: builds server + loadgen, runs 15 s against
  `/healthz` with the limiter off and logs at warn, writes
  `artifacts/m1-http-baseline.json`.
- `POLYFORGE_LOG_LEVEL` env var (default `info`): per-request access logs are
  info-level, so benchmarks set `warn` to measure the handler path rather
  than the logger. A production ops knob the service needed anyway.

### Measured result

**71,893 RPS, p99 1 ms, 0 transport errors, 0 non-2xx over 1,078,405
requests** in 15 s at concurrency 16 on this 8-CPU Windows laptop — the
roadmap gate is ≥ 30,000. Caveat recorded honestly: Windows' ~0.5 ms
interrupt-timer granularity quantizes sub-millisecond samples, so p50 reads
0 and is not meaningful; throughput is computed over the full wall-clock run
and is unaffected. The middleware chain (request ID, trace context, metrics,
recoverer) was active during the run.

### How it was verified

gofmt/vet/golangci-lint clean, `go test ./...` 7/7 packages, Redocly lint
clean, smoke script green end-to-end, benchmark run twice (warmup discarded).
PostgreSQL migration 002 and the integration-test scope assertions compile
locally but execute only in CI (no local Docker) — not claimed as locally
verified.

### Immediate next tasks

1. Push to a remote; watch race + coverage + lint + migration 002 go green.
2. W7 remainder: JWT RS256 + JWKS (deferred — half-landing auth is worse
   than deferring it).
3. W5 evidence artifacts (EXPLAIN ANALYZE) and W9 Dockerfiles — both need
   Docker; consider installing Docker Desktop before next session.

---

## 2026-07-09 (session 2) — Testing gates, auth coverage, and roadmap alignment

Milestone status: closes the roadmap's W8 testing gates (race in CI, coverage
gate, auth-package coverage) and the W4 recoverer gap. The internal
EXECUTION_PLAN.md milestones remain the day-to-day tracker; `docs/THESIS_DETAILS.html`
is the source roadmap they compress.

### Verification of the previous session

Commit `2de9a04` intact, clean tree, `gofmt`/`go vet`/`go build`/`go test ./...`
all green. The end-to-end feature endpoint behavior was re-proven in session 1;
nothing regressed.

### Audit findings (against the roadmap's own verification gates)

The roadmap requires `go test -race` on every test from W3 onward, and W8 sets
"coverage ≥ 80% on control-plane, 100% on auth; CI: lint → test → race → coverage
gate". Reality found:

1. **The race detector had never run.** Not locally (Windows `-race` requires
   CGO and this machine has no C compiler) and not in CI, which ran plain
   `go test`. All the concurrent code — in-memory stores, rate limiter, metrics
   registry — was unproven against races.
2. **`internal/tenant` had 0% coverage.** That package holds API-key generation,
   hashing, authentication, revocation, and rotation — the auth layer where the
   roadmap demands 100%.
3. **API-key IDs leaked secret entropy.** `NewRandomAPIKey` derived the key ID
   from the same random bytes as the secret (`ID = hex(raw[:8])`, secret =
   `hex(raw)`), so every key ID — visible in URLs, listings, and logs — exposed
   64 of the secret's 192 entropy bits. Not practically exploitable (128 bits
   remain), but a defense examiner would ask, and the fix costs one extra
   `rand.Read`.
4. **No panic-recovery middleware** (W4 expects requestID, logger, recoverer,
   timeout, CORS). A panicking handler dropped the connection with no
   structured error and no request ID for correlation.
5. `internal/controller` at 28% coverage; total 45.1%.

### What changed

- `internal/tenant/store_test.go` (new): provisioning, normalization, project
  CRUD + keyset pagination boundaries, API-key lifecycle including cross-tenant
  authentication/revocation boundaries and rotation atomicity, key-shape
  assertions (including "ID must not be derivable from the secret"), and a
  concurrent-access test that gives CI's race detector real contention.
  Package coverage 0% → 94.9%.
- `internal/tenant/store.go`: key IDs now drawn independently of the secret.
- `internal/controller/controller_test.go`: full action-vector table for all
  six labels plus policy invariants (bursty > steady replicas, cacheable >
  uncacheable cache budget, CRUD never routes to a model, agentic triggers
  guarded fairness). 28% → 100%.
- `internal/platform/server.go`: `recoverPanics` middleware inside the
  `requestLog` chain — structured 500 envelope with request ID, stack logged
  server-side, panic details never sent to the client,
  `http.ErrAbortHandler` re-panicked per net/http convention. Two tests.
- `.github/workflows/ci.yml`: tests now run `-race -covermode=atomic`, and a
  coverage gate fails CI under 55% total (ratchet toward 80% as packages gain
  tests; local-without-Docker total is 57.5%, CI adds the PostgreSQL suite).
- golangci-lint v2.6.2 run locally first (never gate CI on an unverified tool):
  it reported 16 `errcheck` findings — unchecked `rows.Close`, `tx.Rollback`,
  `resp.Body.Close`, and one deferred cleanup `Exec`. All were the intentional
  ignore-the-error idiom; each is now an explicit `_ =` so intent is visible.
  With the tree clean, the linter was added to CI as a gate.

### Roadmap alignment notes (THESIS_DETAILS.html vs. repository)

Tool-level deviations that are deliberate, with matching outcomes:

- Roadmap says chi router; repo uses stdlib `http.ServeMux` (Go 1.22+ method
  patterns cover the need). Outcome equivalent; revisit only if middleware
  ergonomics demand it.
- Roadmap says sqlc + golang-migrate; repo uses hand-written SQL behind
  repository interfaces and an idempotent migrator. Outcomes (type-safe access,
  versioned schema, RLS proven by test) hold. An ADR should record this choice.
- Roadmap says argon2id for API keys; repo hashes with SHA-256. This is sound:
  argon2id exists to slow brute force on low-entropy passwords, while PolyForge
  secrets are 192-bit random strings where brute force is infeasible and fast
  hashing is O(1) per request. Worth an ADR — this exact question is
  interview/defense bait.
- Roadmap's W7 JWT/OAuth2/RBAC scopes and W9 Dockerfile/W10 Helm remain open
  and are the natural next milestones on the main track.

### How it was verified

`gofmt` clean, `go vet` pass, `go build` pass, `go test ./... -count=1` — 7/7
packages ok. Coverage gate logic exercised against the real profile in both
directions (passes at 55, fails at 90). **The race detector still cannot run on
this machine** (no C compiler for CGO); the CI `-race` job is the first
execution and is unproven until a green run. Mutex placement in the tenant
store, telemetry store, metrics registry, and rate limiter was manually
reviewed before gating CI on it.

### Immediate next tasks

1. Push; confirm the new race + coverage gates and the PostgreSQL RLS tests go
   green in CI.
2. ADRs: SHA-256-for-high-entropy-keys, and stdlib-SQL-vs-sqlc.
3. Next roadmap slice: W9 Dockerfile (multi-stage distroless) + compose for the
   full local stack — requires Docker, so plan it for a machine/CI that has it.
   Alternatively W7 auth scopes (read-only vs full keys), which is fully
   testable locally.

---

## 2026-07-09 — Repository audit, build repair, and the M2 feature-query API

Milestone status: **M2 in progress.** The feature-query API exit item is now closed.
Trace exporter, analytical store, and batch export remain open.

### Audit findings

The working tree was audited before any code was written. Four defects were found.

1. **The build was broken.** `internal/storage/postgres` did not compile:
   `*Store` did not satisfy `telemetry.Repository` because `Features` was never
   implemented. A previous session added `Features` to the interface, to the
   in-memory store, and to SQLite, but stopped before PostgreSQL. The SQLite
   development path masked this, because `go run ./cmd/control-plane` with no
   `POLYFORGE_POSTGRES_ADMIN_URL` never compiles against the broken package in a
   way the developer would notice. Only the production path was dead.
2. **CI would have failed on formatting.** `internal/telemetry/store.go` had
   misaligned struct fields and const block. The CI `Verify formatting` step runs
   `test -z "$(gofmt -l .)"`, which fails on any unformatted file.
3. **The repository had zero commits.** Every one of the 52 source files was
   untracked. Roughly two milestones of work existed only as uncommitted working-tree
   state, with no history, no diff review, and no recovery point.
4. **Structure defects.** An empty `.git` directory sat one level above the real
   repository, which made tooling report the parent folder as a Git repository while
   every Git command inside it failed. `THESIS_DETAILS.html` — the 10-month thesis and
   career roadmap, and the source document behind `EXECUTION_PLAN.md` — lived outside
   version control entirely.

### What changed

- `internal/telemetry/store.go` reformatted with `gofmt`.
- `internal/storage/postgres/store.go` gained `Features`, restoring the build. It runs
  inside `withTenantTx`, so the row-level-security tenant context is set before the
  query, and it calls `requireTenant` so an unknown tenant yields `ErrTenantNotFound`
  exactly as SQLite does.
- `internal/platform/server.go` gained `GET /v1/tenants/{tenant_id}/telemetry/features`,
  exposing the aggregation that already existed in the repository layer but was
  unreachable over HTTP.
- `api/openapi.yaml` documents the path, its three query parameters, and the
  `TelemetryFeatureSet` / `TelemetryFeatureRow` schemas.
- `internal/telemetry/store_test.go` is new. The package previously had no tests at all,
  despite holding the classifier's feature math.
- Feature tests added to the SQLite, PostgreSQL, and HTTP suites.
- The empty stray `.git` was removed; `THESIS_DETAILS.html` moved to `docs/`.
- The repository received its first commits.

### Design decisions

**An invalid time window is a `400`, not a repair.** `NormalizeFeatureQuery` silently
rewrites an inverted or zero window to the default 15-minute lookback, which is correct
as a repository-layer guarantee: no store can be handed a range it cannot execute. But
if the HTTP layer inherited that behavior, an experiment could request
`since=T, until=T-1h`, receive `200 OK`, and record features for a window it never
asked for. Reproducible evaluation requires that a malformed request fail loudly. The
handler therefore validates bounds before normalization.

**The window is half-open, `[since, until)`.** Adjacent windows tile without
double-counting an event that falls exactly on a boundary. This matters once the
controller consumes one feature window per control interval; a closed upper bound
would count boundary events twice across consecutive intervals.

**Aggregation lives in one place.** `BuildFeatureSet` is the only implementation of the
feature math. Each repository selects events and delegates. Three stores cannot drift
into three different definitions of `p95_latency_ms`, which would silently invalidate
any cross-backend comparison in the evaluation matrix.

**Tenant isolation is proven, not assumed.** The PostgreSQL feature query filters on
`tenant_id`, so a passing test proves only that the `WHERE` clause works.
`TestPostgresFeaturesAreTenantScopedByRowLevelSecurity` therefore also queries
`telemetry_events` with no `tenant_id` predicate at all, inside a tenant transaction
context, and asserts the foreign tenant's rows are invisible. That is a test of RLS.
The pre-existing isolation test only covered `projects`; the analytical table that the
entire research contribution reads from was never checked.

### How it was verified

Every CI gate was run locally:

```text
gofmt -l .                              -> clean
go vet ./...                            -> pass
go test ./... -count=1                  -> pass
npx @redocly/cli lint api/openapi.yaml  -> valid
```

Not verified locally: `TestPostgresFeaturesAreTenantScopedByRowLevelSecurity` and
`TestPostgresRowLevelIsolation`. Docker is not installed on this machine, so both
**skipped** rather than passed. They compile and vet. CI runs PostgreSQL 18 as a service
and will be the first environment to actually execute the new RLS assertion. Treat it as
unproven until a CI run is green.

### What to be able to explain next

- Why row-level security is enforced at the database rather than in the query builder,
  and what class of bug that defends against. See `docs/adr/0003`.
- Why the `/metrics` endpoint deliberately omits tenant IDs as labels while the feature
  API is tenant-scoped by design. The distinction is cardinality and blast radius.
- What `p95_latency_ms` computed over a 15-minute window of at most 10,000 events can
  and cannot support as a claim. `MaxFeatureEvents` is a silent truncation: a tenant
  above that rate yields a percentile over a prefix of the window, not the window.

### Immediate next tasks

1. Push and confirm a green CI run, which is the only proof the PostgreSQL RLS test passes.
2. Decide whether `MaxFeatureEvents` truncation should be surfaced in the response
   (for example a `truncated: true` field) rather than silently changing what a
   percentile means. This affects evaluation validity and should be an ADR.
3. Continue M2: trace exporter integration, then the analytical store decision
   (ClickHouse vs. PostgreSQL), which also wants an ADR.
