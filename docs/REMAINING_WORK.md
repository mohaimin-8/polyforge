# Remaining work — the honest ledger of what is left and who owns it

> **Top-level route: `docs/MAIN_WORKING_PATH.md`** (added 2026-08-05). That
> file orders every item below into milestones M1–M6. This ledger remains the
> owner-split *view*; the main path is the *order*. Session 2026-08-05: goal
> raised to **Transactions-level**; a **formal SLO guarantee** (M3 / T17) added
> as the primary in-repo strengthener; the **leakage-budget controller spun off
> to a separate project** (TDSC) — it is NOT in these buckets. Full gap audit:
> `PolyForge_Research_Gap_Analysis.docx` (Downloads).

Last updated 2026-08-05 (main-path consolidation; prior body last touched
session 30). This is the single place that answers
"what is left and who does it." It reconciles `docs/RELEASE_CHECKLIST.md`
(human-action items), the deferred live campaigns, and the thesis fill-ins into
one owner-split view. When it disagrees with a campaign file, the campaign file
wins.

## Session 35 (2026-08-08) — V-series validity remediation

A four-perspective audit found eleven defects. Status of each:

| # | defect | status |
|---|---|---|
| D1 | 1.4581× LRU charge on 16 baseline arms, none on `jcac` | **MEASURED** — `RESULTS_EVICTION_PARITY.md`: EP-H1a FAILS, 36.8 pp of the headline was accounting |
| D2 | no fair-cache comparator existed | **DONE** — `hpa_fair`/`keda_fair` (512 MB, no charge) |
| D3 | `mean_violation` saturates at 1.0, hiding shed AI | **DONE** — `mean_excess` + `tier_none_step_share` reported for every arm |
| D4 | `RPSWindow` never populated → live planner saw zero demand and froze | **DONE** — rate tracker on both emitters, `-race` clean; AI kinds no longer folded into `crud_read` |
| D5 | `knob_preflight.py` called from no code path | **DONE** — executed by `cluster_backend.execute()` before load, raises on inert substrate |
| D6 | `check_metrics` could not detect "measured nothing" | **DONE** — rejects zero p95 / zero `n_events`; `n_events` now captured |
| D7 | 48 hypotheses, no multiple-comparison correction | **DONE** — `holm_bonferroni()` in `stats.py` (6 tests); applied in the V-series records |
| D8 | sweep order confounded with priority class | **MEASURED, IMMATERIAL** — `RESULTS_ORDER_PERMUTATION.md` (540/540): OP-H1 fails strictly, but the largest Jain excursion is **0.0001**, an order of magnitude under the pre-registered 0.01 threshold; OP-H2 FAILS (spread < 0.01 on every mix incl. `whale`); OP-H3 PASSES (cost order-independent to 0.91%). The published order is best on one mix and *worst* on another — sensitivity, not bias. **Published fairness results stand**; sweep order is now a seeded parameter (`tenant_order_seed`) with a measured spread on record |
| D9 | no envtest; fake clients hide conflicts | **PARTIAL** — retry-on-conflict landed; envtest still owed |
| D10 | `reproduce.py` returned 0 on total failure | **DONE** — exits nonzero on drift or failed campaign scripts |
| D11 | audit record dropped exactly when a knob moved | **DONE** — audit emitted before the status write; every degraded cycle audited |

**Two committed live rows in `phase7_live.duckdb` are marked `valid` with
`crud_p95_ms = 0.0`** — they measured nothing. `check_metrics` now rejects
that signature; the rows themselves still need invalidating and
`PHASE7_ORDINAL.md` regenerating (open item, listed in Bucket A below).

## Where the project stands

The research is effectively complete and honestly reported. All five contested
segments are resolved (cost WON, SLO closed-honest, fairness WON, forecasting
WON, cache CLOSED) plus the security/isolation segment WON and confirmed over
the wire; the Wave 1–4 leak-fill closed the six examiner-audit gaps; the thesis
compiles (44-page PDF) and the discussion chapter is reconciled with Waves
2–4. **Session 24 added the Wave 5 structural-form program**: the simulator's
functional forms (measured latency model, mixture-percentile p95, tier-scaled
work units) stress-tested by three pre-registered full-matrix reruns — all
PASS with SLO non-inferiority held; a solver audit that measured the
coordination gap at zero (120/120) and *caught the published controller
exceeding its per-interval actuation clamps*, adjudicated by a pre-registered
clamp-fixed rerun (all PASS, slightly stronger — `anchor_moves` is now the
quotable controller); plus live-path engineering (churn-safe/thread-safe
planner, selectable forecasters, multi-resolution `seasonal_mr` for day-scale
periodicity). DEFENSE_QA #24–25 carry the new record. **Session 27 closed four more
fronts in one sitting, every campaign pre-registered and pushed before its
run:** (1) the B2/B3 formal write-up (`RESULTS_LIVE_CHAOS_P99.md` — LC-H1
PASS, first live p99 on record); (2) the **B1 harness prep is
desk-complete and WL-H2-verified** (tier routing + cache byte budgets on
the production gateway, operator knob push under the Applied gate, live-AI
harness mode, preflight gate PASS against the real binary — B1 now needs
only the GPU host); (3) the **second real demand trace replayed**
(`RESULTS_TRACE_AZURE.md` — HT-AZ PASS-with-disclosure, d_z −1.64…−1.85,
n=72); (4) the **2026-stack concurrency baseline** added, tuned into the
strongest reactive arm, and beaten at violation parity
(`RESULTS_CONCURRENCY.md` — CQ-H1/H2 PASS, d_z=−0.96); plus (5) the
**end-to-end 32/64-tenant slice** (`RESULTS_TENANT_SCALE.md` — TS-H1a
PASS, TS-H1b honest FAIL direction-consistent, margin grows with width).
DEFENSE_QA #12/#13/#22 carry the new records. What remains is **not new
desk measurement** — it is one gated confirmatory experiment (B1), plus
submission mechanics and thesis polish. **Session 28 closed the
artifact-evaluation front**: `python scripts/reproduce.py` re-derives every
generated record and figure from the committed data (verified: 5/5 records
byte-identical, 17/17 figures; CI proves the clean-clone tier on every
push — `docs/REPRODUCE.md`), every campaign now has a committed run-level
csv.gz export, and `research/paper/main.tex` is a compiling FGCS scaffold
awaiting the user's manuscript carve. **Session 29 closed the
learned-control front** — the sharpest remaining *mechanism* objection
("why a hand-designed MPC and not a learned policy?"). A strong,
offline-trained RL controller over the *identical* joint action space and
objective was pre-registered (`PREREG_LEARNED_CONTROL.md`, pushed at
896c896 before any run), trained, and beaten: MPC J −0.376, p=2.9e-28,
d_z=−0.708 over 300 matched cells **at zero training cost**, with the
learner's lower violation bought at 2.61× the spend — the same
attainment-for-spend trade the reactive scalers make
(`RESULTS_LEARNED.md`, DEFENSE_QA #26). **Session 30 closed the risk-control
line** with the disciplined follow-up its own published null called for
(`PREREG_RISK_BUDGET.md`, pushed 701d29b before any run; one changed factor,
the null never re-run): **RB-H1 PASS** — the reading the null failed *with
the sign reversed* now lands as designed (−0.00232 violation, p=0.0073),
confirming the published diagnosis was mechanism and not story — while
**RB-H2 and RB-H3 FAIL honestly** (an interior optimum at q=0.90 rather than
a monotone frontier; 3 of 6 domination conjuncts, cost only, reported as
partial). A 24-cell probe bounds the mechanism: the knob buys attainment only
where a capacity lever still has headroom with a real return. Under the
published weights the corrected arm is net worse on J, so the point-forecast
controller **remains** the quotable configuration and the campaign stands as
evidence for that default (`RESULTS_RISK_BUDGET.md`, DEFENSE_QA #27).

The three buckets below are ordered by owner, not by priority. The single
highest-*value* remaining item is in bucket B: the three-knob live plane, which
is the only thing that would add live evidence for the joint controller — the
project's central novelty and its sharpest open weakness.

---

## Bucket A — closeable now, at the desk (agent-doable, no gate)

These need no account, host, or payment. They are the natural next agent tasks.

| Item | What it is | Where |
|---|---|---|
| Title-page macros | placeholder author/roll/supervisor/date macros on the title page | `thesis/report/main.tex` |
| ~~Harness prep for the Wave 4 live plane~~ | **DONE (session 27):** tier routing + cache byte budgets on the gateway, operator knob push under the Applied gate, live-AI harness mode, `tier_mixed`/`joint_stress` cells, and the executable WL-H2 preflight gate — desk-verified end-to-end (WL-H2 PASS against the real gateway binary with mock-latency tier backends). B1 now needs only the GPU host | `PREREG_WAVE4_LIVE_PLANE.md` §Status update |
| Slides ↔ thesis consistency pass | ensure the deck's numbers match the reconciled discussion chapter (−70% not −76%; eight nulls; over-the-wire done) | `thesis/slides/`, `PolyForge_Pre-defence_Presentation.pptx` |
| Thesis ↔ Wave 5 reconciliation | fold DEFENSE_QA #24–25 into the discussion/limitations chapters: structural-form robustness, the clamp disclosure + anchored-controller quotability, the coordination-gap result | `thesis/report/`, sources in `RESULTS_MASTER.md` §13 |
| ~~B2/B3 formal write-up~~ | **DONE (session 27):** `RESULTS_LIVE_CHAOS_P99.md` generated by `live_chaos_p99.py` from the committed CSV — LC-H1 PASS (zero violation through both live faults), P99-H1 recorded (SLO verdict unchanged at p99) | `research/analysis/RESULTS_LIVE_CHAOS_P99.md` |
| **Formal SLO guarantee** (M3 / T17 — REDIRECTED 2026-08-06) | **The specified theorem is vacuous on this plant and that is committed (b55f91b):** memoryless plant + hold-still actuation + cheap replicas make any SLO-clearing configuration trivially control-invariant, so recursive feasibility here is true and empty. Per the user's call the target became the **reactive-vs-predictive cost separation**, and it is **DONE and executable (bec0f62)**: a floor on reactive cost vs a realised predictive cycle gives **+49.0% on the `flash` orbit** (where the onset climb of 5 exceeds the ±2 authority) against **+2.7%** on the `ramp_gentle` control cell and **−5.9%** once the observational aliasing is removed — i.e. 49.0% derived from the plant constants alone, against the campaigns' measured −44…−50% cost at violation parity. **M3 is closed** (`DEFENSE_QA` #28). Campaign replay done per matched cell on the `uniform` mix across hpa/keda/firm (5 parity pairs): medium/`spike_agentic` derived +43.4% vs measured +53.1%, large/`flash_ai` +42.5% vs +79.1%; small/`flash_crud` not computable; both `ramp_gentle` pairs outside the theorem's scope. **An earlier "all three signs agree" claim is RETRACTED** — it came from comparing one derived cell against a twelve-cell measured average, and `flash_crud` flips once the cells are matched. **The multi-tenant coupling is load-bearing, not optional** (no-cap gives the wrong sign; equal-share makes the cell infeasible; independent per-tenant phases mean neither is right). Status: *suggestive structural corroboration at one operating point*, not a validated correspondence — the open item is the multi-tenant extension. Table, soundness rule and detail in `docs/MAIN_WORKING_PATH.md` §3 M3. Original spec, now superseded: the primary Transactions/TPDS strengthener: terminal invariant set + recursive-feasibility condition over the MPC's already-clamped actuation lattice → a bounded-violation guarantee, shipped as an executable checker + a validation script asserting measured violation ≤ bound on every closed campaign. Theorem prose user-owned (R8). Must be a real proof, not a heuristic | `research/jcac_sim/controller.py`, `research/jcac_sim/test_invariants.py`; spec in `docs/MAIN_WORKING_PATH.md` §3 |
| ~~B1 live ablation arms + CRD bounds (M1)~~ | **DESK-COMPLETE (session 34, 2026-08-06):** live `replica-only`/`cache-only`/`tier-only` arms + Policy-CRD `cacheSizeMBMin/Max` and `modelTierMin/Max` (min==max pins a knob), clamped at the actuation point, forwarded to the planner, mirrored in the sim, with the frozen 4×4 matrix in `eval/experiments/wave4_live_plane.yaml`. R4 holds (16/16 records byte-identical). The **live actuation dry-run is deferred into M2** (no Docker here) | `eval/harness/cluster_backend.py`, `internal/operator/**`, `eval/experiments/` |

## Bucket B — user-gated live-cluster experiments (agent prepares, user opens the gate)

All are blocked on a Docker/GPU host this machine does not have. The protocols
are frozen and pushed; the runbooks are push-button. Each is **confirmatory** —
the mechanism is already closed in simulation — so none is load-bearing for the
thesis, but B1 is the highest-value because it is the only live evidence for
the joint controller.

| Item | Status | Gate | Spec |
|---|---|---|---|
| **B1. Three-knob live plane** (joint controller, all knobs live) | **UNBLOCKED at the desk (session 34, 2026-08-06).** Session 33's correction — `OPERATOR_SYSTEMS = {"jcac"}`, no CRD bounds, so the prereg's `cache-only`/`tier-only` ablations ("the sharpest test of the central claim") had no live implementation and WL-H1 was not evaluable — is **resolved**: all four frozen arms are wired, the CRD pins a knob at min==max, and every rendered CR validates against the committed CRDs. The GPU half was already solved and free (`docs/WAVE4_FREE_ROUTE.md`; T4a gate PASSED on a real Kaggle P100, gap 647.7 ms vs the bench's 641 ms). **Still owed before scoring, in this order:** (1) the live actuation dry-run of the four arms (deferred from M1 — needs a cluster), (2) `knob_preflight.py` WL-H2 liveness gate, then (3) the frozen matrix once. An inert knob VOIDS WL-H1 — report, do not fake | GPU-capable host (free Kaggle + Codespace route) | `PREREG_WAVE4_LIVE_PLANE.md` §Status update, `docs/MAIN_WORKING_PATH.md` §3 M1 status |
| **B2. Live chaos campaign** (planner crash + apiserver throttle) | **EXECUTED** (session 23, Codespace, shared-PG data plane): both faults injected live under load, run valid, violations 0 through both — data in `eval/results/live_chaos_p99_runs.csv` (commit 7bdbd4f). Remaining: the formal RESULTS write-up against the prereg's frozen readings | done (write-up = Bucket A) | `PREREG_LIVE_CHAOS_P99.md`, `eval/results/live_chaos_p99_runs.csv` |
| **B3. Live p99 number** | **EXECUTED** (same sitting): first real live p99 — ai 20.043 ms / crud 1.191 ms (ai_cacheable), crud 8.01 ms (crud_bursty) — same CSV; write-up rides with B2's | done (write-up = Bucket A) | `PREREG_LIVE_CHAOS_P99.md` Part B |

Notes: B2/B3 share one Codespace sitting and reuse the proven session-19
harness. B1 additionally needs the Bucket-A harness prep and real model tiers
on the GPU (tier-bench pair; drop the 7B tier if VRAM is short, per the
prereg's amendment rule). When the user opens the gate, the ordered steps are
in `docs/WAVE3_LIVE_RUNBOOK.md` (for B2/B3) and `PREREG_WAVE4_LIVE_PLANE.md`
§Substrate (for B1).

## Bucket C — human-action only (account / camera / payment / browser)

Cannot be automated from this repo. Full detail in `docs/RELEASE_CHECKLIST.md`.

| Item | One-line action |
|---|---|
| OSF registration submit | paste the frozen preregs into OSF, record the DOIs in `OSF_REGISTRATION.md` (the git-push anchor stands until then) |
| Zenodo deposit | `cd eval && python scripts/archive_zenodo.py`, upload, paste `deposit.json`, publish, copy DOI |
| Helm chart → Artifact Hub | `helm package`, host on `gh-pages`, register the repo |
| Container images → GHCR | build + push `polyforge-operator` / `polyforge-planner` (release.yml signs with cosign) |
| Demo video | ~5 min OBS capture: kind up → `helm install` → load → JCAC adapts on Grafana |
| Paper-supplement site | GitHub Pages from `docs/` with the figure gallery + Zenodo/OSF links |
| First cloud smoke (~€10) | `terraform apply` on Hetzner, run `smoke.yaml` with `backend: cluster`, `terraform destroy` |
| Submission | arXiv preprint + venue submission + artifact-track application (W37–W40) |
| Visual PDF proofread | a human read-through of the compiled thesis PDF (no agent renderer) |
| Rotate pasted credentials | rotate the Kaggle + HF tokens pasted in chat during data work |

---

## Suggested order

1. **Bucket A now** (this session and next): the B1 ablation arms + CRD bounds
   landed 2026-08-06, so the next desk item is the **formal SLO guarantee**
   (M3 / T17) — the only remaining agent-doable item on the main path. The
   title page finishes the thesis's own debts (bib authors were filled and
   verified in session 23).
2. **Bucket B1 next time a GPU host is opened** — the one experiment that
   materially strengthens the thesis by adding live joint-controller evidence.
   B2/B3 can share a cheaper CPU Codespace sitting whenever convenient.
3. **Bucket C on your own schedule** — submission mechanics; none blocks the
   defense, but OSF submit and the Zenodo DOI are worth doing before the paper
   goes out.
