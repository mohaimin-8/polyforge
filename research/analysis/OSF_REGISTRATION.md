# OSF prospective registration — mirror index (closes DEFENSE_QA #17)

DEFENSE_QA #17 concedes that git-push forensics is a weaker pre-registration
anchor than an external registry, and commits: "New experiments will mirror
their pre-registrations to an external registry (OSF) prospectively." This
file is the registry-ready mirror. Every protocol below was **committed and
pushed before its first run** (or, for the deferred live protocols, before
any live number exists); the git push is the primary timestamp anchor and
the OSF registration is the independent secondary witness.

## USER ACTION REQUIRED (the one human step)

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

This is the only item in Wave 1–4 that the agent cannot complete; it is
listed for the user exactly as the Zenodo/GHCR/video items are in
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
| `PREREG_PLANNER_CELLS.md` | DEFENSE_QA #13 (scale) | `264356a` | 2026-07-16 (session 21) | yes (session 21, PLANNER_CELLS.md) | _pending_ |
| `PREREG_PLANNER_CELLS_DEALIAS.md` | DEFENSE_QA #13 (scale, confound follow-up) | `0a61c6a` | 2026-07-16 (session 21) | yes (session 21, PLANNER_CELLS_DEALIAS.md) | _pending_ |

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
