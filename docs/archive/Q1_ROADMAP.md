# Roadmap to a Q1 journal acceptance

Created 2026-07-25 (session 31); **revised same day (v2)** after a second
review pass. What changed and why:

1. **B1 moved ahead of cell sharding.** Cell sharding modifies the exact
   operator→planner call path that B1's frozen preflight (WL-H2, session
   27) was desk-verified against — running B1 first means the frozen
   harness meets the build that passed its preflight, with no
   re-verification cycle on a gated experiment. B1 is also the cheapest,
   highest-information experiment on the board, and its outcome steers the
   manuscript, so it should exist before writing starts.
2. **Venue quartiles corrected.** v1 called FGCS/TCC "Q1-borderline
   fallbacks" — by JCR/SJR both are formally Q1, which splits the plan
   into two tracks with different minimum paths (below). ACM TOCS was
   dropped from the target list: prestigious, but low-volume and by impact
   factor often *not* Q1 — the wrong target for this specific goal.
3. **Cell-sharding scope trimmed** to the smallest implementation that
   makes the shipped claim honest (Phase 3.3).

**Terse machine-followable runbook: `docs/archive/Q1_EXECUTION_MAP.md`** — tasks
T0–T16 with per-task DO/VERIFY/DONE-WHEN and the safety rules R1–R8; use it
to execute, use this file for the why.

This is the **journal** roadmap. `docs/REMAINING_WORK.md` stays the thesis
owner-split ledger and the source of truth for defence readiness. Where the
two disagree about a shared item, REMAINING_WORK wins for the thesis and
this file wins for the paper.

---

## Venue math — what "Q1" actually requires

"Q1" is a journal-ranking quartile, not a prestige tier, and the
distinction changes the plan:

- **Track A — formal Q1: FGCS, IEEE TCC, IEEE TSC.** All three are JCR/SJR
  Q1. A submission built on Phases 0–2 plus precise claim-scoping is
  credible here. **This is the shortest path to the stated goal.**
- **Track B — elite systems: IEEE TPDS (and TSC at its most competitive).**
  Reviewers here run clusters and weigh multi-node live evidence — Phase 4
  is effectively required. JPDC sits on the Q1/Q2 border year to year;
  treat it as a fallback, not a target.

Pick the track **after** Phase 2 (B1) has run: a clean WL-H1 PASS is the
strongest argument for attempting Track B at all, and a wounded result
makes Track A with honest framing the right play.

---

## The honest starting position

The research is done and it is good. Twenty campaign records, 26
pre-registrations pushed before their runs, an eight-entry published-nulls
ledger, two real demand traces, a 2026-stack reactive baseline, an
offline-trained RL controller beaten at zero training cost, and a security
result confirmed over the wire. That body of work is already **comfortably
Q2**, and most of it is Q1-grade.

What separates it from Q1 is not more measurement. It is three things, in
descending order of how much a reviewer will care:

1. **The central novelty has never run live.** The joint three-knob
   controller — the contribution — is proven in simulation and on the
   replica axis only. Every headline number is decision quality under
   `research/jcac_sim/model.py`.
2. **The one sim-vs-reality comparison that ran, disagreed**
   (`PHASE7_ORDINAL.md`, DISAGREE in both cells). The explanation is
   structurally sound — kind's data plane makes the cache and tier levers
   inert, so the separating mechanism was absent by construction — but it
   is currently **unfalsified**. Nothing yet distinguishes "the mechanism
   was absent" from "the model is wrong."
3. **The shipped artifact does not implement the paper's scalability
   answer.** Planning cells exist in the research simulator only; the
   deployed planner is a single replica behind a process-global lock.

Items 1 and 2 are the *same* experiment (B1). That coupling is why B1 is
not "confirmatory" in the way `REMAINING_WORK.md` currently frames it — it
is the load-bearing item for the paper, and it runs **second**, right
after the desk-only Phase 1.

---

## Phase 0 — Security hardening ✅ DONE (session 31)

Completed and verified before this roadmap was written.

| Issue | Severity | Disposition |
|---|---|---|
| `golang.org/x/text` v0.38.0 — GO-2026-5970 infinite-loop DoS, reachable from `internal/auth/oidc.go:216` (OIDC token exchange) and `internal/storage/postgres/migrate.go` | High (reachable, pre-auth path) | **Fixed** — bumped to v0.39.0; `govulncheck` now reports zero reachable vulnerabilities |
| Planner `/v1/plan` + `/v1/state` unauthenticated; sole control was a NetworkPolicy that is **inert on non-enforcing CNIs** — fails open silently | Medium–High (cross-tenant demand disclosure + cluster-wide capacity steering) | **Fixed** — constant-time bearer token on every `/v1/*` route and verb, Helm generates and pins it across upgrades, operator wired to the same Secret, `/healthz` left open for probes |
| No CVE gate in CI | Medium (process) | **Fixed** — reachability-aware `govulncheck` step added to `ci.yml` |
| Planner surface absent from the threat model | Medium (process) | **Fixed** — `docs/SECURITY.md` now documents it, with two new honest gaps (plain-HTTP hop, no per-caller attribution) |
| Live Kaggle API key at `Thesis Project/kaggle.json`, pasted in chat | Low (verified **not** in git history or tree) | **User action** — rotate at kaggle.com/settings; nothing to fix in-repo |

No hardcoded secrets anywhere in the tree. gitleaks runs over full history.
Per-tenant cache partitioning is the unconditional default and the insecure
shared posture requires an explicit opt-in no production path makes. The
project's security posture was already strong; these were the real gaps.

---

## Phase 1 — Artifact integrity (desk; ~3 sessions; no gate)

Journals award reproducibility badges rather than run conference-style
artifact tracks, but the real consumers — referees who clone the repo, and
the thesis examiners — check exactly this. Cheap, zero-risk, no
dependencies: do it first.

| # | Item | Why it matters | Effort |
|---|---|---|---|
| 1.1 | **CSV fallback loaders** for the six duckdb-only analysis scripts (`analysis_chaos`, `analysis_concurrency`, `analysis_econ`, `analysis_learned`, `analysis_risk`, `analysis_tenant_scale`) | The `.duckdb` files are gitignored (Zenodo-archived), so a clean clone currently re-derives **5 of 20** campaign records. The run-level `.csv.gz` exports *are* committed — only the loader path is missing. Affects exactly the newest and most defensively important campaigns | ~100 lines across 6 files |
| 1.2 | Add fig18/fig19 to the `reproduce.py` set once 1.1 lands | Closes the figure tier at 19/19 | small |
| 1.3 | **Controller invariant property tests** — assert no emitted plan exceeds its per-interval actuation clamps, respects replica/cache bounds, and is deterministic under seed | The Wave 5 audit caught the published controller violating its own clamps *after* its numbers were published. A property test would have caught it at commit. This is the single highest-leverage hardening item | ~1 session |
| 1.4 | Extend `reproduce.py` to cover all 20 records | Turns "one-command reproduction" from a partial claim into a complete one | ~1 session |

**Exit criterion:** `python scripts/reproduce.py` on a fresh clone re-derives
20/20 records and 19/19 figures with zero external downloads.

---

## Phase 2 — B1, the live three-knob plane (AUTHOR-GATED; open the gate early; ~1 GPU day + 2 desk sessions)

The single highest-value item in the entire project, **moved ahead of cell
sharding in v2** for three reasons:

1. **Dependency safety.** The WL-H2 preflight (session 27) was
   desk-verified against the *current* operator→planner→gateway build.
   Cell sharding (Phase 3) modifies exactly that call path; running B1
   first means the frozen harness runs against the build that passed its
   preflight.
2. **Information value.** B1 carries the pre-registered falsifier for the
   central claim. Its outcome steers the manuscript's framing and the
   track choice, so it should exist before writing effort is sunk.
3. **It is the cheapest gate on the board** — roughly **$20–60** of GPU
   for a few hours — and GPU access has scheduling friction, so open the
   gate as early as convenient.

Mechanics (unchanged from v1): harness prep is desk-complete and WL-H2
preflight passes against the real gateway binary; the protocol is frozen in
`PREREG_WAVE4_LIVE_PLANE.md`. The falsifier is stated in advance — if the
joint arm does not beat the best single-knob arm on cost-at-fixed-fairness,
the central claim is wounded; that pre-commitment is what makes the result
credible. Drop the 7B tier if VRAM is short (the prereg's amendment rule
permits it).

**Session-31 interaction (deployment flag, not a protocol change):** the
operator chart now defaults `planner.auth.enabled=true`. For the live run,
either set it `false` — reproducing the harness posture the preflight was
verified against — or export the token into the harness environment. The
prereg is untouched either way.

**This resolves weaknesses 1 and 2 simultaneously.** It is the difference
between "we simulate a joint controller" and "we built one and it works."

---

## Phase 3 — Ship planning cells (desk; ~4–5 sessions; no gate; required for Track B, strengthening for Track A)

The paper will claim planning cells scale the controller to 1024 tenants.
The shipped operator has no cell logic at all. A reviewer who runs the
artifact and reads the paper will find this divergence.

| # | Item | Detail |
|---|---|---|
| 3.1 | **Cell assignment in the operator** | Hash-based tenant→cell mapping (the measured-correct scheme; round-robin's ΔJain −0.089 is the aliasing artifact) |
| 3.2 | **Per-cell planner routing** | Operator fans out one plan call per cell. Each measured cell solve is ~195 ms (`PLANNER_CELLS.md`), far under the 3 s per-call timeout; a 10 s cadence fits 32 cells even sequentially |
| 3.3 | **Smallest honest implementation first** | Per-cell solves and per-cell locks inside the existing single planner service — the measured 1024-tenant win comes from partitioning the *solve*, not from horizontal replicas (it was measured single-process on one core). A multi-replica StatefulSet with per-cell state files is the escape hatch if the 256-tenant measurement demands it, **not** the default plan |
| 3.4 | **Scale test on the shipped path** | Drive 128 and 256 tenants through operator→planner and confirm the per-cell p95 stays flat, matching the simulated ~195 ms |
| 3.5 | Document the fairness scoping honestly | Cell partitioning makes fairness **local to a cell**, not global. ΔJain −0.0053 is small but the claim must change from "globally fair" to "approximately fair within cells, measured cost −0.0053" |

**Track A note:** if the manuscript scopes the claim precisely ("planning
cells are evaluated in the simulator on the deployed solver code; the
shipped operator runs the single-cell configuration"), this phase
*strengthens* rather than gates a Track A submission. For Track B it is
required.

---

## Phase 4 — Multi-node live validation (AUTHOR-GATED; Track B's gate; ~2 weeks + €50–150)

Phases 0–3 produce a strong, defensible **Track A** submission. Phase 4 is
what makes **Track B** genuinely likely rather than a coin flip — "we ran
on one host for a few hours" is thin for reviewers who run clusters.

| # | Item | Detail |
|---|---|---|
| 4.1 | Multi-node cluster (3–5 nodes, Hetzner terraform already written) | `eval/infra/terraform` exists; the first cloud smoke is already a Bucket-C item at ~€10 |
| 4.2 | Run the joint controller under real multi-tenant load at 32+ tenants | Converts the tenant-scale slice from simulated to live |
| 4.3 | Live chaos at cluster scale | B2/B3 already executed on a shared-PG Codespace; redo on real nodes |
| 4.4 | Report live p99 alongside the sim's p95 across the matrix | Ground rule 5 currently confines p99 to a single live campaign |

Skip this phase entirely if Track A is the decision — put the time into the
manuscript instead.

---

## Phase 5 — Manuscript and submission mechanics (AUTHOR-OWNED)

Writing is explicitly outside this plan's scope. Listed for completeness and
sequencing only.

| Item | Owner | Notes |
|---|---|---|
| Carve the manuscript from the thesis | User | `research/paper/main.tex` is a compiling FGCS scaffold awaiting the carve |
| **Split the security paper** | User | The cache side-channel (AUC 0.88 → 0.50 at 24% latency, 76% hits kept, confirmed over the wire) is a clean, self-contained contribution. It is *stronger* as its own venue submission than as a section, and splitting shortens the systems paper |
| OSF registration submit | User | Preregs are frozen and git-anchored; paste and record DOIs |
| Zenodo deposit + DOI | User | `cd eval && python scripts/archive_zenodo.py` |
| GHCR images, Artifact Hub, Pages supplement | User | Badge/repro prerequisites |
| arXiv preprint → venue submission | User | |
| **Rotate the Kaggle + HF tokens** | User | Phase 0 finding; not in git, but exposed in chat |

---

## Time to Q1 — an honest estimate

Two clocks run here, and conflating them is how people end up disappointed.

**Clock 1 — work you control (in execution order):**

| Phase | Owner | Elapsed |
|---|---|---|
| 0. Security | ✅ done | — |
| 1. Artifact integrity | desk | ~1 week |
| 2. B1 live plane | author gate + desk | ~1 week once the gate opens (overlaps Phase 1) |
| 3. Planning cells shipped | desk | ~2 weeks |
| 4. Multi-node validation | author gate + desk | ~2 weeks |
| 5. Manuscript + mechanics | user | ~4–6 weeks |

- **Track A minimum path (FGCS/TCC/TSC):** Phases 0–2 + 5, with Phase 3 if
  time allows → **~6–8 weeks to submission.**
- **Track B (TPDS-tier):** all phases → **~10–12 weeks**, compressible to
  ~8 if the manuscript is drafted during Phases 3–4.

**Clock 2 — the review cycle you do not control:** FGCS first decisions
commonly land in ~2–4 months; IEEE Transactions run ~3–6 months per round.
One major revision is normal and is not a failure signal.

**Realistic acceptance: Track A ~Q1–Q2 2027; Track B ~mid–late 2027.**

**Cost:** ~$20–60 (B1 GPU) + ~€50–150 (multi-node, Track B only) + optional
APC. Under $250 of compute for the whole plan.

---

## Sequencing against the thesis defence

The defence comes first and nothing here blocks it — `REMAINING_WORK.md`
confirms the thesis compiles at 45 pages with every claim traced. Phases 1
and 3 *strengthen* the defence (a reviewer-proof artifact and a shipped
scalability story), so they are safe to do beforehand. Phase 2 (B1)
materially improves the defence if the gate opens in time. Phases 4–5 are
strictly post-defence.

**Recommended next slice:** Phase 1.1 — the CSV fallback loaders. Bounded,
no gate, no dependency on anything above it, and it takes the reproduction
claim from 5/20 to 20/20 records. Phase 1.3 (controller invariant tests) is
the natural follow-on and the highest-leverage hardening in the project.
**And open the B1 gate whenever convenient** — it is $20–60, everything
downstream reads better once it exists, and its result should be known
before a word of the manuscript is written.
