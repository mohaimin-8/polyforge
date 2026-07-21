# Remaining work — the honest ledger of what is left and who owns it

Last updated 2026-07-19 (session 28). This is the single place that answers
"what is left and who does it." It reconciles `docs/RELEASE_CHECKLIST.md`
(human-action items), the deferred live campaigns, and the thesis fill-ins into
one owner-split view. When it disagrees with a campaign file, the campaign file
wins.

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
(`RESULTS_LEARNED.md`, DEFENSE_QA #26).

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

## Bucket B — user-gated live-cluster experiments (agent prepares, user opens the gate)

All are blocked on a Docker/GPU host this machine does not have. The protocols
are frozen and pushed; the runbooks are push-button. Each is **confirmatory** —
the mechanism is already closed in simulation — so none is load-bearing for the
thesis, but B1 is the highest-value because it is the only live evidence for
the joint controller.

| Item | Status | Gate | Spec |
|---|---|---|---|
| **B1. Three-knob live plane** (joint controller, all knobs live) | **harness prep DONE + desk-verified (session 27):** tier/cache knobs live on the gateway, operator push under the Applied gate, live-AI harness mode, WL-H2 preflight gate PASS at the desk; run is push-button once a host exists | GPU-capable host (paid GPU or GPU Codespace) | `PREREG_WAVE4_LIVE_PLANE.md` §Status update |
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

1. **Bucket A now** (this session and next): the title page finishes the
   thesis's own debts (bib authors were filled and verified in session 23);
   the Wave 4 harness prep makes B1 turnkey.
2. **Bucket B1 next time a GPU host is opened** — the one experiment that
   materially strengthens the thesis by adding live joint-controller evidence.
   B2/B3 can share a cheaper CPU Codespace sitting whenever convenient.
3. **Bucket C on your own schedule** — submission mechanics; none blocks the
   defense, but OSF submit and the Zenodo DOI are worth doing before the paper
   goes out.
