# OSF prospective registration — mirror index (closes DEFENSE_QA #17)

DEFENSE_QA #17 concedes that git-push forensics is a weaker pre-registration
anchor than an external registry, and commits: "New experiments will mirror
their pre-registrations to an external registry (OSF) prospectively." This
file is the registry-ready mirror. Every protocol below was **committed and
pushed before its first run** (or, for the deferred live protocols, before
any live number exists); the git push is the primary timestamp anchor and
the OSF registration is the independent secondary witness.

## AUTHOR ACTION REQUIRED (the one manual step)

OSF registration needs an account and a browser; it cannot be scripted from
here. To register:

1. Create/sign in at https://osf.io, make a project "PolyForge — joint
   cross-layer adaptive control for multi-tenant LLM serving".
2. For each row in the table below, add a **Registration** (OSF supports the
   "OSF Preregistration" template): paste the protocol file's text, and in
   the "existing data" field state honestly that the analysis code was
   written and pushed before the run but the simulation had not yet executed
   (git commit + ISO timestamp given per row are the evidence).
3. Registrations are frozen and timestamped by OSF on submission; paste each
   resulting OSF DOI back into the "OSF DOI" column here and into the thesis
   reproducibility section. Until then the git anchor stands.

This is the only item in Wave 1–4 that cannot be completed from the desk; it is
listed for the author exactly as the Zenodo/GHCR/video items are in
`docs/RELEASE_CHECKLIST.md`.

## Registry rows (git anchor is authoritative until an OSF DOI exists)

| protocol file | closes | git commit | pushed (ISO 8601) | executed | OSF DOI |
|---|---|---|---|---|---|
| `PREREG_TIER_RATIO.md` | DEFENSE_QA #15 | `7deb6a3` | 2026-07-16T12:47:08+06:00 | yes (session 20, RESULTS_TIER_RATIO.md) | _pending_ |
| `PREREG_HK_ADOPTION.md` | DEFENSE_QA #16 | `7deb6a3` | 2026-07-16T12:47:08+06:00 | session 20 | _pending_ |
| `PREREG_CHAOS_SIM.md` | DEFENSE_QA #19 (sim) | `7deb6a3` | 2026-07-16T12:47:08+06:00 | session 20 | _pending_ |
| `PREREG_PSEUDO_TENANT.md` | DEFENSE_QA #18 | `7deb6a3` | 2026-07-16T12:47:08+06:00 | yes (session 20, PSEUDO_TENANT.md) | _pending_ |
| `PREREG_WIRE_ATTACK.md` | DEFENSE_QA #19 (security, live) | `c38bce6` | 2026-07-16T13:24:16+06:00 | deferred (Codespace) | _pending_ |
| `PREREG_LIVE_CHAOS_P99.md` | DEFENSE_QA #19 (chaos, live) + #11 (p99) | `c38bce6` | 2026-07-16T13:24:16+06:00 | deferred (Codespace) | _pending_ |
| `PREREG_LM_ADOPTION.md` | DEFENSE_QA #16 (latency-model half) + #24 | `7519958` | 2026-07-18T23:57:47+06:00 | yes (session 24, RESULTS_LM_ADOPTION.md) | _pending_ |
| `PREREG_MIXTURE_P95.md` | DEFENSE_QA #24 (mixture percentile) | `7519958` | 2026-07-18T23:57:47+06:00 | session 24 | _pending_ |
| `PREREG_TIER_WU.md` | DEFENSE_QA #24 (tier capacity coherence) | `7519958` | 2026-07-18T23:57:47+06:00 | session 24 | _pending_ |
| `PREREG_COORD_GAP.md` | DEFENSE_QA #25 (solver exactness) | `fc7e72c` | 2026-07-19T00:02:30+06:00 | yes (session 24, COORD_GAP.md) | _pending_ |
| `PREREG_MOVE_CLAMP.md` | DEFENSE_QA #25 (actuation-clamp violation) | `cfab691` | 2026-07-19T00:11:08+06:00 | session 24 | _pending_ |
| `PREREG_PLANNER_CELLS.md` | DEFENSE_QA #13 (scale) | `264356a` | 2026-07-16 (session 21) | yes (session 21, PLANNER_CELLS.md) | _pending_ |
| `PREREG_PLANNER_CELLS_DEALIAS.md` | DEFENSE_QA #13 (scale, confound follow-up) | `0a61c6a` | 2026-07-16 (session 21) | yes (session 21, PLANNER_CELLS_DEALIAS.md) | _pending_ |
| `PREREG_DEGRADE.md` | gap 4.4 (graceful-degradation policy) | `3e66253` | 2026-07-19T03:27:44+06:00 | yes (session 25, DEGRADE_PROBE.md) | _pending_ |
| `PREREG_WAVE4_LIVE_PLANE.md` | DEFENSE_QA #21 (three-knob live plane) | `821274d` | 2026-07-17T21:40:46+06:00 | yes (session 48, RESULTS_WAVE4_LIVE_PLANE.md: WL-H1 FAIL as scored, WL-H2 PASS x16) | _pending_ |
| `PREREG_WAVE4_CALIBRATED.md` | DEFENSE_QA #21 (follow-up: one changed factor, the corrected controller; 4 dated amendments after the registered text) | `6b2de57` | 2026-09-15T15:43:55+06:00 | yes (session 48, RESULTS_WAVE4_CALIBRATED.md: WL-H1' PASS, WL-H4 PASS x4) | _pending_ |
| `PREREG_WAVE4_SIM_TRANSFER.md` | DEFENSE_QA #21 (does the simulator transfer once its plant is fitted to the live evidence; frozen fit rule) | `10febd3` | 2026-09-16T04:49:30+06:00 | yes (session 48, RESULTS_WAVE4_SIM_TRANSFER.md: WL-H3' PASS 3 of 4, control 1 of 4) | _pending_ |
| `PREREG_WAVE4_DWELL.md` | DEFENSE_QA #21 (damping the corrected controller's replica oscillation) | `34ea69d` | 2026-09-16T11:05:03+06:00 | yes (session 48, RESULTS_WAVE4_DWELL.md: WL-H6 FAIL, WL-H7 FAIL as scored) | _pending_ |
| `PREREG_TRACE_AZURE.md` | DEFENSE_QA #12 (second real trace) | `527a1b0` | 2026-07-19T16:11:38+06:00 | yes (session 27, RESULTS_TRACE_AZURE.md) | _pending_ |
| `PREREG_CONCURRENCY.md` | DEFENSE_QA #22 (2026-stack reactive arm) | `ff84846` | 2026-07-19T16:19:52+06:00 | yes (session 27, RESULTS_CONCURRENCY.md) | _pending_ |
| `PREREG_TENANT_SCALE.md` | DEFENSE_QA #13 (end-to-end width) | `37d36ae` | 2026-07-19T16:24:11+06:00 | yes (session 27, RESULTS_TENANT_SCALE.md) | _pending_ |
| `PREREG_LEARNED_CONTROL.md` | DEFENSE_QA #26 (why not RL?) | `896c896` | 2026-07-22T00:19:46+06:00 | yes (session 29, RESULTS_LEARNED.md) | _pending_ |
| `PREREG_RISK_MPC.md` | DEFENSE_QA #27 (SLO-tolerance dial) | `b2900b2` | 2026-07-22T00:53:16+06:00 | yes (session 29, RESULTS_RISK.md — both gates FAIL, published null) | _pending_ |
| `PREREG_RISK_BUDGET.md` | DEFENSE_QA #27 (one-changed-factor follow-up) | `701d29b` | 2026-07-22T02:28:18+06:00 | yes (session 30, RESULTS_RISK_BUDGET.md — RB-H1 PASS, RB-H2/H3 FAIL) | _pending_ |

Prior-campaign preregs (PREREG_V2/V3/VTC/TRACE/TRACE2) predate this OSF
commitment and remain git-anchored only, as disclosed in DEFENSE_QA #17;
they are listed here for completeness and may be registered retroactively as
"registration of existing analysis" if the venue prefers, clearly labeled as
such — never as prospective.

| prior protocol | git commit (pushed pre-run) |
|---|---|
| `PREREG_V2.md` | f8aa8e6 |
| `PREREG_V3.md` | 6229b6a |
| `PREREG_VTC.md` | bfb70fc |
| `PREREG_TRACE.md` | 717f8bd |
| `PREREG_TRACE2.md` | b09a492 |

Reproduce the anchors: `git log --format="%h %cI" -1 -- research/analysis/<file>`.
