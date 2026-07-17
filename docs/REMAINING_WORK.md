# Remaining work — the honest ledger of what is left and who owns it

Last updated 2026-07-17 (session 23). This is the single place that answers
"what is left and who does it." It reconciles `docs/RELEASE_CHECKLIST.md`
(human-action items), the deferred live campaigns, and the thesis fill-ins into
one owner-split view. When it disagrees with a campaign file, the campaign file
wins.

## Where the project stands

The research is effectively complete and honestly reported. All five contested
segments are resolved (cost WON, SLO closed-honest, fairness WON, forecasting
WON, cache CLOSED) plus the security/isolation segment WON and confirmed over
the wire; the Wave 1–4 leak-fill closed the six examiner-audit gaps; the thesis
compiles (44-page PDF) and the discussion chapter is now reconciled with Waves
2–4. What remains is **not new measurement** — it is one gated confirmatory
experiment, plus submission mechanics and thesis polish.

The three buckets below are ordered by owner, not by priority. The single
highest-*value* remaining item is in bucket B: the three-knob live plane, which
is the only thing that would add live evidence for the joint controller — the
project's central novelty and its sharpest open weakness.

---

## Bucket A — closeable now, at the desk (agent-doable, no gate)

These need no account, host, or payment. They are the natural next agent tasks.

| Item | What it is | Where |
|---|---|---|
| Bibliography author fields | arXiv-only sources still carry placeholder `{{X authors}}` author fields; fill from the real papers (never fabricate) | `thesis/report/references.bib` |
| Title-page macros | placeholder author/roll/supervisor/date macros on the title page | `thesis/report/main.tex` |
| Harness prep for the Wave 4 live plane | wire the tier-routing data plane + real cache hit/miss latency gap so the knobs are live levers (buildable before the GPU host exists) | `PREREG_WAVE4_LIVE_PLANE.md` §Substrate 2–3 |
| Slides ↔ thesis consistency pass | ensure the deck's numbers match the reconciled discussion chapter (−70% not −76%; eight nulls; over-the-wire done) | `thesis/slides/`, `PolyForge_Pre-defence_Presentation.pptx` |

## Bucket B — user-gated live-cluster experiments (agent prepares, user opens the gate)

All are blocked on a Docker/GPU host this machine does not have. The protocols
are frozen and pushed; the runbooks are push-button. Each is **confirmatory** —
the mechanism is already closed in simulation — so none is load-bearing for the
thesis, but B1 is the highest-value because it is the only live evidence for
the joint controller.

| Item | Status | Gate | Spec |
|---|---|---|---|
| **B1. Three-knob live plane** (joint controller, all knobs live) | prereg written this session; harness prep is a Bucket-A task | GPU-capable host (paid GPU or GPU Codespace) | `PREREG_WAVE4_LIVE_PLANE.md` |
| **B2. Live chaos campaign** (planner crash + apiserver throttle) | prereg + runbook ready | Docker host (kind on a Codespace) | `PREREG_LIVE_CHAOS_P99.md`, `docs/WAVE3_LIVE_RUNBOOK.md` §2 |
| **B3. Live p99 number** | export landed + unit-tested; needs a live run to populate | rides on B2's cluster | `PREREG_LIVE_CHAOS_P99.md` Part B |

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

1. **Bucket A now** (this session and next): bib + title page finish the
   thesis's own debts; the Wave 4 harness prep makes B1 turnkey.
2. **Bucket B1 next time a GPU host is opened** — the one experiment that
   materially strengthens the thesis by adding live joint-controller evidence.
   B2/B3 can share a cheaper CPU Codespace sitting whenever convenient.
3. **Bucket C on your own schedule** — submission mechanics; none blocks the
   defense, but OSF submit and the Zenodo DOI are worth doing before the paper
   goes out.
