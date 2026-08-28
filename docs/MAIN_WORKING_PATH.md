# Main working path — the single ordered route to a Transactions-level submission

Last updated 2026-08-05. **Start here.** This is the top-level route that
reconciles every open work item into one dependency-ordered path. It sits
*above* two existing files and does not replace them:

- `docs/archive/Q1_EXECUTION_MAP.md` — the terse per-task runbook (T0–T18, DO / VERIFY
  / DONE-WHEN). When this file and the map disagree on **order**, this file
  wins; when they disagree on a task's **steps**, the map wins.
- `docs/REMAINING_WORK.md` — the owner-split ledger (buckets A/B/C).
- `research/analysis/PREREG_*` and `RESULTS_*` — win on any **scored
  definition** (rules R1/R2 below). Never edited to fit a plan.

## 0. What changed this session (decision log)

1. **Goal raised: "formal Q1" → "Transactions-level."** Primary venues are
   unchanged (FGCS / IEEE TCC / IEEE TSC). Two stretch venues are now in view
   and each has a dedicated strengthener:
   - **IEEE TPDS** ← a **formal SLO guarantee** for the controller (new work,
     milestone **M3** below). This is the single biggest in-repo addition.
   - **IEEE TDSC** ← the cache side-channel + a **leakage-budget controller**.
     **Decision: this is spun off to a SEPARATE project** (author's call). It
     is documented as a side track, **not** on PolyForge's critical path.
2. **The journal-gap sweep confirmed the joint predictive controller lands in
   open journal water** — the multi-knob/predictive frontier is almost entirely
   conference/pre-print; Transactions journals hold only narrow slices
   (`PolyForge_Research_Gap_Analysis.docx` in Downloads has the full audit +
   competitor delta table). So the path is *finish + harden what exists*, not
   *add a new problem*.
3. **Survey-blessed optional strengtheners identified** (energy/carbon,
   admission control) — milestone **M4**, low priority.

## 0a-pre. Session 36 (2026-08-09): Docker works locally — two gates closed

`com.docker.service` shipped as `DEMAND_START` and never started, which is
why Docker appeared broken; set to Automatic. Engine 29.6.2 + WSL2 verified,
`kind`/`helm`/`k6` installed, `cluster_backend.preflight()` reports **no
missing tools**, and a kind cluster came up, scheduled a pod and tore down.

Two claims that could never be tested here are now tested:
**PostgreSQL RLS 3/3 PASS** (real PG 18; `./scripts/pg-test-up.sh`) and
**OWASP ZAP executed** (118 PASS / 0 FAIL after fixing the three header
findings it surfaced). Caveat on record: the *default* ZAP baseline reaches
2 URLs, both 404, so its "66 PASS" is a scan of nothing — use `--api`, and
the scope is the unauthenticated surface.

**Roadmap consequence: WP8 split.** The GPU is only needed for real model
tiers (`POLYFORGE_EVAL_LIVE_AI` is opt-in), so **WP8a — the live actuation
dry-run — is now desk-doable and is the next agent-executable item after
Track 1.** WP8b (the scored matrix) stays user-gated on the GPU.

## 0a. EXECUTION ROUTE: `docs/PUBLICATION_ROADMAP.md` — start there

Added end of session 35. That file is the mechanical, work-package-level
route to Q1/Transactions (WP1–WP11): exact files, commands, frozen designs,
verify-first steps, gates, and a progress table. A future session should
execute it top to bottom without re-deriving anything. This file remains the
milestone-level story; `REMAINING_WORK.md` remains the owner-split ledger.

## 0a. Session 38 (2026-08-13): what changed at the top — READ THIS FIRST

Eight work packages landed in one session. Two results move the headline and
one closes the sharpest open attack on it. Detail lives in the records; this
is the orientation.

| WP | Outcome |
|---|---|
| **WP15** budget parity | The comparative **cost claim is withdrawn on both real traces.** BP-H1 FAILS against a budget-capped comparator (Azure −3.3%/−1.3%, BurstGPT −50.2%/−49.8%, none surviving the rank gate). What replaces it is a **feasibility** result: tier spend dominates infra spend by three orders of magnitude, so **no replica-only reactive controller can meet the per-tenant budget under AI load** — an argument from the price table that no amount of baseline retuning answers. |
| **WP6** model mismatch | **The controller survives being wrong.** MM-H1 and MM-H2 both PASS, all twelve sub-tests under Holm: ±25% mismatch on replica capacity, tier price or cache saturation leaves the `keda_fair` cost win and `hpa_fair` severity non-inferiority intact, with no cliff across 0.5–2.0. This is the direct answer to "it was tuned on its own simulator". |
| **WP3** layered fix | The **+2884% −joint-control ablation is withdrawn**: it measured a baseline latched in its most expensive tier. Honest number **+318.5%**. jcac still beats a competently-tiered GPTCache posture on J. |
| **WP13** MT separation | Coupling is **exactly computable** (cap binds 21.7% of steps) but the derivation's own pre-stated V2 check **falsified it**. Reported, not patched. |
| **WP5** | O(N²) planner wall removed: 108× fewer projections, 13.7× faster, byte-identical. |
| **WP4** | Audit C6 adjudicated **FALSE** — planning cells never wipe forecast history. No code change. |
| **WP8a / WP12** | Live dry-run found a pin that never reached the CRs; the authenticated ZAP scan found it was measuring the rate limiter, then found a **Medium** NUL-byte→500 defect. Both fixed. |

**Net effect on the pitch.** The cost headline is gone at every level it was
ever stated. What stands is stronger against the objections that actually get
raised: a feasibility argument about joint control, mismatch robustness,
fairness, forecasting, security, and a validity methodology that keeps
catching its own errors. The venue rule's cost condition now reads FAIL —
see `PUBLICATION_ROADMAP.md` §6.

## 0b. Session 35 (2026-08-08): the V-series validity remediation — READ FIRST

A four-perspective validity audit of the whole artifact found eleven defects.
The decisive one is now measured and adjudicated, and **it changes the
headline claim.**

**`RESULTS_EVICTION_PARITY.md` (PREREG_EVICTION_PARITY, frozen before the
run; 1800/1800 valid):**

| comparison | result |
|---|---|
| published `jcac` vs `hpa` | **−36.0%** — reproduced exactly in this campaign |
| `jcac` vs `hpa_fair` | **+0.8%** — cost-neutral, **EP-H1a FAILS** |
| **swing** | **36.8 percentage points** |

`hpa_fair` is the same reactive controller with two accounting asymmetries
removed: the **1.4581× LRU inference charge** that sixteen baseline arms paid
and no `jcac` arm did, and the **128 MB cache the baselines could never
move** (pre-sized to 512 MB, the level `jcac` itself converges to). LRU was
also the most favorable of four comparators in the project's own
`eviction_comparison.csv` — **GDSF beats the proposed cost-aware policy at
both capacities** (factor 0.978, i.e. it would run *against* the proposal),
and the cost-aware policy's measured **1104.9 µs p99 overhead** was never
charged while LRU's 0.0 µs was.

**What survives, and it is real:**
- `jcac` vs `keda_fair`: **−14.1%** (d_z −0.440, p 3.8e-16) — joint control
  still beats event-driven scaling at parity.
- **SLO severity is materially better**: unbounded `mean_excess` 0.2143 vs
  0.4288 (d_z −0.435, p 6.4e-12) — roughly half the overshoot. The saturating
  `mean_violation` (0.0691 vs 0.0699) was *understating* the proposal's
  advantage, not flattering it.
- `ai_cacheable`: **−14.2%** vs `hpa_fair` — the cell where a knob the
  baseline does not have is doing real work.
- Cost against `crud_bursty` is **+107.5%**: a pre-existing, now-explicit
  weakness where no AI knob applies.

**The restated contribution:** joint control is *cost-neutral against a
well-configured replica autoscaler while attaining materially lower SLO
overshoot, and cheaper than event-driven scaling* — with the advantage
concentrated where cache and tier are load-bearing. Narrower, defensible,
and found by the project's own audit before review rather than after it.
`tier=none` step share (7.3% for `jcac`, 0.0% for every reactive arm) is now
a first-class reported column.

**The second adjudication came back the other way, and that matters too.**
`RESULTS_ORDER_PERMUTATION.md` (PREREG_ORDER_PERMUTATION, 540/540): the
coordinate-descent sweep order *is* confounded with priority class — tenant
ids are assigned by slot and the mixes put premium/whale tenants in the low
slots — but measured over five alternative permutations the effect is
**immaterial**. Largest Jain excursion **0.0001**, an order of magnitude
below the 0.01 threshold the prereg fixed in advance; OP-H2 FAILS (spread
under threshold on every mix, including the `whale` fairness stressor);
OP-H3 PASSES (cost order-independent to 0.91%). The published order is the
best on one mix and the *worst* on another — sensitivity, not a thumb on the
scale. **The published fairness results stand.** What changed is that sweep
order is now an explicit seeded parameter (`tenant_order_seed`) with 540 runs
of measured spread behind it, so "what if you rename t00 to t99?" has a
pre-registered answer instead of an argument.

R1/R4 both hold: no published record was edited, and `reproduce.py`
re-derives **19/19 byte-identical** — including, for the first time, a
live-derived record (`PHASE7_ORDINAL.md`).

Also landed this session (all local, $0): the live plane's demand signal was
structurally dead (`RPSWindow` never populated by any production emitter, AI
traffic folded into `crud_read`) — fixed with a `-race`-clean rate tracker;
`check_metrics` now rejects a run that measured nothing; `knob_preflight.py`
is executed by the harness instead of merely documented; the operator retries
on conflict and audits every degraded cycle; `reproduce.py` exits nonzero on
drift. See `docs/REMAINING_WORK.md` for the full ledger.

## 1. Where we stand (reconciled, one paragraph)

Desk research is complete and honestly reported: all six contested segments
resolved, Waves 1–5 closed, artifact reproduction byte-identical under CI,
26 preregs frozen. Done and pushed: **T0–T3** (security + artifact integrity)
and **Phase 3 / T5–T7** (planner cells; monolithic wall reproduced, per-cell
p95 flat). The B1 GPU half is solved and free — **T4a passes on a real Kaggle
P100** (`docs/WAVE4_FREE_ROUTE.md`). **The one live-evidence gap is B1**, and
as of 2026-08-06 it is **no longer blocked at the desk**: M1 shipped the two
missing pieces — live `cache-only`/`tier-only` ablation arms and Policy-CRD
`cacheSizeMB`/`modelTier` bounds that pin a knob at min==max — so the four
frozen arms now render, validate, and clamp as the prereg defines. What M1
could **not** do at the desk is the live actuation dry-run (no Docker on this
machine); that check rides with M2's sitting.

## 2. The main path — six milestones in dependency order

```
        DESK (agent)                         GATED / USER
  M1 ── B1 ablation arms ───► M2 ── B1 live run ──┐
  (unblocks live evidence)     (1 GPU sitting)     │
                                                   ├──► M6 ── manuscript + submit
  M3 ── formal SLO guarantee ──────────────────────┤        (user writes; long pole)
  (TPDS strengthener; parallel to M1/M2)           │
                                                   │
  M4 ── energy/admission (optional) ───────────────┤
                                                   │
  M5 ── OSF / Zenodo / images (user, any time) ────┘

  SIDE TRACK (off this path): leakage-budget controller ──► separate project ──► TDSC
```

| # | Milestone | Owner | Gate | Maps to | Exit criterion |
|---|---|---|---|---|---|
| **M1** | B1 live ablation arms + CRD knob bounds | agent (desk) | none | T4-prerequisite | **DESK-COMPLETE 2026-08-06** — arms wired + CRD bounds + tests green; live actuation dry-run deferred into M2 (needs a cluster) |
| **M2** | Execute B1 three-knob live plane | user opens gate | free Kaggle+Codespace | T4a, T4 | RESULTS committed PASS/FAIL; ledgers reconciled |
| **M3** | Formal SLO guarantee (bounded-violation proof + checker) | agent (desk) | none | **T17 (new)** | invariant checker + validation show measured violation ≤ bound on all campaigns |
| **M4** | Energy/carbon + admission-control knobs *(optional)* | agent (desk) | none | **T18 (new)** | new constraint added off-by-default; R3/R4 bit-identical |
| **M5** | Submission mechanics | user | accounts | T11–T14 | OSF DOIs, Zenodo DOI, GHCR images, tokens rotated |
| **M6** | Manuscript carve + submit | **user** | M1–M5 done | T15, T16 | arXiv + venue submission with artifact/prereg DOIs |

**Critical path = M1 → M2.** M1 is the only desk item that unblocks the sole
live-evidence gap, and B1's frozen WL-H2 preflight was verified against the
operator→planner path, so M1 must land before any later change reopens it.
M3 runs fully in parallel (touches only `research/jcac_sim/`, not the live
path). M6 (manuscript) is the calendar long pole and is user-owned (R8).

## 3. Detailed specs for the NEW work

### M1 / T4-prerequisite — B1 live ablation arms + CRD bounds (critical path)

**Why:** `eval/harness/cluster_backend.py:56` has `OPERATOR_SYSTEMS = {"jcac"}`
— only the full joint arm is wired live. `PREREG_WAVE4_LIVE_PLANE.md` scores
WL-H1 against **four** arms (jcac, replica-only, cache-only, tier-only), so
WL-H1 is not evaluable until the three ablations exist live. Freezing a knob
is not configuration: the Policy CRD (`internal/operator/api/v1alpha1/
policy_types.go`, `deploy/**/crds/polyforge.io_policies.yaml`) has
`replicaMin/Max` but **no bounds for `cacheSizeMB` or `modelTier`**.

- **DO:**
  1. Add live wiring for `replica-only`, `cache-only`, `tier-only` in
     `cluster_backend.py`, faithful to the prereg's arm definitions (a
     frozen knob is pinned to its published constant, the others actuate).
  2. Add CRD bounds `cacheSizeMB{Min,Max}` and `modelTier{Min,Max}` (or a
     pinned-value field) to `policy_types.go` + the two CRD YAMLs; regenerate.
     Planner/operator honor the pin (`internal/operator/controllers/
     policy_controller.go`, `gateway_knobs.go`).
  3. Add the wave4 experiment spec under `eval/experiments/` mirroring the
     four frozen arms × frozen cell classes (already in `workloads.py`).
  4. Non-scored dry-run of the whole integration (no GPU) end-to-end.
- **VERIFY:** R3 green (`gofmt -l .` empty, `go test ./...`, `pytest`,
  `reproduce.py` all MATCH); new Go tests for the pinned-knob CRD path green;
  dry-run shows all four arms actuate/pin as defined. **Arms are frozen by the
  prereg push (R1) — do NOT invent substitutes; if an arm can't be expressed
  faithfully, STOP and report.**
- **DONE-WHEN:** four arms run live in a non-scored dry-run; committed.

**Status 2026-08-06 — DESK-COMPLETE, one check deferred (honest split).**
Shipped: `replica-only` (reactive HPA, cache/tier held by `push_default_knobs`)
plus `cache-only` / `tier-only` as operator arms whose Policy CRs pin two knobs
at min==max; `cacheSizeMBMin/Max` + `modelTierMin/Max` on the CRD with the
clamp enforced at the single actuation point (`clampCache`/`clampTier`), the
bounds forwarded to the planner, and the same freeze mirrored in the sim
(`SystemSpec.knob_freeze`); `eval/experiments/wave4_live_plane.yaml` holds the
frozen 4×4 matrix. Verified at the desk: 220 pytest + full `go test ./...` +
`gofmt`/`go vet` green; **R4 holds — `reproduce.py` re-derives 16/16 records
byte-identical and 19/19 figures**, so the bounds are a true no-op when open;
the spec expands to 16 runs and every rendered CR validates against the real
committed CRDs, guarded by a mutation-tested test
(`test_wave4_crs_are_admissible_against_the_real_crds` — unknown CR fields are
*pruned*, not rejected, so a mistyped bound would silently unfreeze an arm).
**Deferred, not done:** the live actuation dry-run (four arms against a real
cluster) — this machine has no Docker, so it is folded into M2's sitting as
its first step, *before* the scored matrix. Nothing here is scored, so R1/R2
are untouched.

### M2 / T4a + T4 — execute B1 (gated, ~1 sitting)

Unchanged from `Q1_EXECUTION_MAP.md`. Order: T4a free-route preflight (protect
GPU quota) → `knob_preflight.py` WL-H2 liveness gate (inert knob VOIDS WL-H1 —
report, don't fake) → run the frozen matrix once → publish as measured →
reconcile `RESULTS_MASTER.md`, `DEFENSE_QA.md`, `REMAINING_WORK.md`. Apply
R5 (`POLYFORGE_EVAL_SHARED_PG=1`) + R6 (planner auth). Carry
`WAVE4_FREE_ROUTE.md` §4 verbatim (tunnel round-trip → live absolutes are not
quotable tier latencies).

### M3 / T17 — formal SLO guarantee  *(NEW — the TPDS strengthener)*

**Why:** the LLM-serving field is empirical; no controller in print carries a
bounded-violation proof. PolyForge already has an MPC (`research/jcac_sim/
controller.py`) with **per-interval actuation clamps already pinned** by
`test_invariants.py` (`MAX_REPLICA_STEP = 2`, cache/tier lattices). That is the
foundation a recursive-feasibility argument stands on.

**Engineering deliverable (R8-clean — code, not prose):** the *theorem
statement* is user-written; the agent delivers the verified model and the
numbers.
- **DO:**
  1. Formalize the disturbance bound: the max per-interval demand change the
     workload model admits (read it off `model.py` / the workload generator).
  2. Construct a **terminal invariant set** — the state region where the SLO
     constraint holds and the clamped actuation can keep the state inside it
     under the bounded disturbance. Encode it as an executable predicate.
  3. Prove **recursive feasibility** as a checkable condition (feasible now ⇒
     feasible next step under any admitted disturbance), and derive the
     **violation bound** it implies.
  4. Ship an **invariant/feasibility checker** in `research/jcac_sim/` that
     evaluates the terminal-set predicate and the recursive-feasibility
     condition, plus a **validation script** that replays every closed campaign
     and asserts measured SLO violation ≤ the derived bound.
- **VERIFY:** checker + validation green on all campaigns; mutation-test the
  checker (widen the disturbance bound → recursive-feasibility condition must
  fail), per the T3 lesson. R3/R4 green; default OFF/no-op so run identities
  stay bit-identical.
- **DONE-WHEN:** bound derived, checker committed, validation shows
  violation ≤ bound everywhere; a `DEFENSE_QA` entry records it. *(User then
  writes the theorem/proof prose for the manuscript.)*
- **RISK:** must be a real proof, not a heuristic — this is the
  Transactions-accept vs major-revision line. If the terminal set can't be
  constructed cleanly over the actual lattice, report the obstruction; do not
  paper over it.

**Status 2026-08-06 — THE SPECIFIED THEOREM IS VACUOUS; DIRECTION CHANGED
(user decision).** The RISK clause fired. `research/jcac_sim/guarantee.py`
(committed b55f91b) builds the exact construction and shows a
recursive-feasibility theorem here would be *true and empty*: the plant is
memoryless, so no state is a trap; the controller may hold still, so any
configuration clearing the SLO across the orbit is trivially control-invariant;
and replicas are cheap enough that static over-provisioning fits inside both
the replica ceiling and the budget (at the `flash` peak, 6 replicas clear the
SLO and 10 cost $0.00134 against a $0.01389 allowance). "Is there a terminal
set?" collapses to "is the peak servable at all?" — a static capacity question
the ±2 clamp plays no part in. The clamp *is* exceeded (per-interval replica
climb 5 flash_crud, 6 flash_ai, 7 spike_agentic, 3 agentic/joint_stress vs
authority 2; `ramp_gentle` 1, the control cell, as designed) — it just does not
produce infeasibility.

**New M3 target: the reactive-vs-predictive cost separation.** Note the
correction that matters — a reactive controller does *not* have to violate on
an onset; it can over-provision permanently. So the honest theorem is a **cost**
separation, not a violation impossibility:

> A controller choosing `x_{k+1}` without observing `d_{k+1}` must, to hold
> zero violation, be robust to every demand consistent with its information —
> the successor set of what it observed. On an orbit where a trough can be
> followed by either a trough or a burst, that forces peak provisioning during
> troughs. A predictive controller provisions for `d_{k+1}` alone. The gap is
> the **price of reaction**, exact on the finite lattice.

This formalizes the measured headline (−44…−50% cost vs reactive arms at
violation parity) instead of a claim the plant contradicts. Soundness rule for
the next slice: prove the gap by comparing a **lower** bound on reactive cost
(relax the reach constraint) against an **achievable** predictive trajectory
(respecting reach) — two lower bounds would prove nothing.

**DONE 2026-08-06 (bec0f62) — the theorem is executable and it lands on the
measured record.** `price_of_reaction` in `guarantee.py` returns a *floor* on
reactive cost (reach clamp and budget filter relaxed) against a *realised*
predictive cycle (reach, budget and knob bounds all enforced, trajectory closed
and independently rebuilt step-by-step in test). On the published amplitudes,
standard tenant, `replica_max` 10:

| orbit | onset climb vs authority 2 | reactive floor | predictive cycle | gap |
|---|---|---|---|---|
| `flash` | 5 — clamp binds | $0.012800 | $0.006533 | **+49.0%** |
| `ramp_gentle` | 1 — control cell | $0.009733 | $0.009467 | +2.7% |
| same orbit, demands all distinct | — no aliasing | $0.004533 | $0.004800 | **−5.9%** |

Row 1 is the headline: **49.0% derived from the plant constants alone, against
the campaigns' measured −44…−50% cost at violation parity.** Row 2 reproduces
PREREG_V3's specificity check analytically (`ramp_gentle` shares `flash_crud`'s
0.5×–6.0× envelope, differing only in slope). Row 3 is the mechanism test —
remove the observational aliasing and the separation inverts, so the gap is
caused by the information asymmetry the theorem names.

**Campaign replay attempted 2026-08-06 (2bc3bf5) — DOES NOT LAND YET; open.**
Two things block it, both now encoded in the tool rather than argued around:

1. **Regime mismatch.** The separation bounds violation at *every step*; the
   campaigns report the *mean*. An arm at mean 0.13 may be missing the SLO
   completely through a burst and clearing it elsewhere. Addressed by tracing
   each class as a cost/violation frontier (price violation at λ) and reading
   both at equal mean violation — the campaigns' own comparison.
2. **The reactive frontier is sparse, and a λ sweep only recovers its convex
   hull.** A reactive policy chooses once per *observation class*, and `spike`
   has two, so its frontier has no point near the measured 0.224 — the nearest
   is 0.083. A naive read returns "+74.4%" by comparing reactive at 0.083
   against predictive at 0.222, which is not parity.
   `cost_at_violation_parity` now demands a stated target and returns
   `reactive_offset`/`predictive_offset`; a large offset means *no comparison
   available*, not *no gap*.

**What the measured data actually supports.** Of the four v3 classes only
`spike_agentic` is at genuine violation parity (jcac 0.2239 vs hpa 0.2236).
`flash_crud` (0.1118 vs 0.1324), `flash_ai` (0.2716 vs 0.1719) and
`ramp_gentle` (0.1776 vs 0.0275) differ enough that their cost deltas are
confounded — including `flash_crud`, where jcac is measurably *more* expensive.
So the derived 49% and the measured −44…−50% are **consistent in sign and
magnitude but not yet a validated correspondence**, and must not be written up
as one.

**RESOLVED 2026-08-06 (9081a63).** The reactive frontier is now enumerated
exactly rather than swept: with reach relaxed the observation classes are
independent, so the achievable set is a Minkowski sum and pruning dominated
partial sums is lossless. On `spike` that recovers **62 frontier points where
the sweep found 3**, and the reactive side reaches within 0.029 of the target
instead of 0.14. Correctness is pinned against brute force on an orbit small
enough to enumerate every reactive policy directly.

**Derived vs measured — CORRECTED 2026-08-06.** An earlier version of this
table compared derived numbers from *one* cell against measured numbers
averaged over *twelve* (4 tenant mixes × 3 cluster sizes) and reported that all
three classes agreed in sign. **That agreement was an artefact of the
mismatch.** Redone per cell, restricted to the `uniform` mix — 8 identical
tenants, so a per-tenant cost ratio is directly comparable — and scanning all
three reactive arms, which yields **5 violation-parity pairs instead of 1**:

| matched cell (uniform mix) | derived | measured | note |
|---|---|---|---|
| medium / `spike_agentic` vs hpa | **+43.4%** | **+53.1%** | agrees, within 1.2× |
| large / `flash_ai` vs firm | **+42.5%** | **+79.1%** | agrees in sign, 1.9× apart |
| small / `flash_crud` vs firm | **not computable** | +11.5% | see below |
| large / `ramp_gentle` vs hpa | ~0 by construction | +18.9% | smooth orbit, no aliasing |
| large / `ramp_gentle` vs keda | ~0 by construction | +53.1% | smooth orbit, no aliasing |

Derived values use a **symmetric per-tenant share** of the cluster caps
(`replicas//8`, `cache_mb//8`), which the `uniform` mix justifies by symmetry.

**The multi-tenant coupling is load-bearing, not an optional refinement.**
Ignoring the caps entirely makes `flash_crud` come out −19.5% — the *opposite*
sign to the measured +11.5%. Applying the equal share instead makes the cell
*infeasible* (a 3-replica share cannot serve a peak needing 6), yet the real
runs reach that violation. Both bracket the truth without capturing it: the
per-tenant phases are drawn independently, so bursts do not coincide and a
tenant can borrow capacity while its neighbours sit in a trough. Neither bound
models that.

Also note `ramp_gentle`: the theory predicts ~0 separation on a smooth orbit
(every position uniquely identifiable, so reaction is as informed as
prediction), yet jcac is measurably cheaper. The derived class is an *idealised*
reactive policy keyed on the exact demand; real HPA/KEDA merely lag. So the
measured deltas also contain plain reactive lag, which this theorem does not
model.

**Honest status:** 2 matched comparisons agree in sign and within ~1.2–1.9×;
1 is not computable under either coupling model; 2 fall outside what the
theorem describes. This is *suggestive structural corroboration*, materially
weaker than a validated correspondence, and must be cited that way.

**M3 CLOSED 2026-08-06** — `DEFENSE_QA` #28 records the vacuity finding, the
replacement theorem, the retracted sign agreement and the coupling gap, with an
explicit do-not-say list. Theorem prose stays user-owned (R8); the numbers and
the verified model were the agent's deliverable and are committed.

**Open item carried forward (not part of M3):** the multi-tenant extension.
Cluster caps couple tenants and destroy the independence that makes the
frontier enumeration *exact*, so this trades a provable result for an
approximate one — worth doing only if a reviewer presses on it.

### M4 / T18 — energy/carbon + admission control  *(NEW — optional, survey-blessed)*

**Status 2026-08-06 (51d2f65) — energy/carbon DONE; admission control NOT done,
deliberately.** Per-step energy and carbon are reported on every `StepMetrics`,
never priced into `cost_usd`, and the controller has an optional
`carbon_weight` (guarded, default off — R4 re-verified 16/16 byte-identical
after touching both `model.py` and `controller.py`).

**Scope it honestly when citing it.** The intended divergence from dollars
(tier price 1:10:100 vs tier energy ~1:4:16) does **not** materialise under a
constant grid: 240 configurations give **2 discordant (cost, carbon) pairs out
of ~57,000 comparisons**, both near-ties where one side already violates. Every
knob moves cost and carbon the same way, so at fixed intensity minimising spend
already minimises grams — as a static per-step term it would be decorative.
The dimension is real only because **grid intensity varies over time and price
does not follow it**: with carbon unpriced the settled plan is identical at 50
and 900 g/kWh; with it priced the plan moves (7 replicas → 6 → shed). The knob
is sharp — low weights move nothing, high weights shed AI traffic outright.

**Admission control is left open on purpose.** It needs the sim loop to scale
admitted demand plus a research decision on whether a rejected request counts
as an SLO violation, and its own prereg if scored (R2). T18 offers it as an
"and/or" alternative, so the milestone's letter is met; bolting it in
unexamined would be worse than leaving it named.

Add a carbon-intensity or energy term (and/or an admission-control arm for
bursty load) as an **additional constraint/dimension**, off by default (R4).
Directly citable against the IEEE survey's stated open list. Low priority —
strengthening material, not a headline. Needs its own prereg only if scored
(R2).

### SIDE TRACK (off this path) — leakage-budget controller → TDSC

Author's decision: implemented in a **separate project**, not PolyForge.
Recorded here only so the path is complete. Shape: leakage-rate as a fourth
hard MPC constraint + an online channel-capacity estimator + a bounded-exposure
proof. Reuses PolyForge's wire-confirmed side channel as the attack model.
Full detail in `PolyForge_Research_Gap_Analysis.docx` (Downloads).

## 4. Timeline — the 6–8 week map

| Week | Milestone(s) active | Owner | Blocks |
|---|---|---|---|
| 1–2 | **M1** B1 ablation arms + CRD bounds | agent (desk) | unblocks M2 |
| 2–4 | **M3** formal guarantee (parallel to M1) | agent (desk) | feeds M6 |
| 3 | **M2** B1 live run (1 sitting) | user gate + desk | feeds M6 |
| 3–4 | **M5** OSF / Zenodo / images / token rotation | user | feeds M6 |
| 4–8 | **M6** manuscript carve (~25–30 pp) + submit | **user** | terminal |
| any | **M4** energy/admission (optional) | agent (desk) | none |

**~6 weeks** if the manuscript is written in parallel with the last
engineering weeks; **~8 weeks** sequential. The engineering critical path
(M1→M2 + M3) is ~4 weeks of desk work; the manuscript is the long pole and is
yours. Two hard conditions gate the verdict: **B1 must actually run** (M2 — the
sim-only weakness is attacked independently of any new contribution) and **M3
must be a real proof**.

> Free-plan cadence: the ~4 weeks of desk engineering is several bounded
> sessions, not one push (`[[session-scoping-preference]]`). "6–8 weeks" holds
> only if sessions run steadily.

## 5. Rules (unchanged — from `Q1_EXECUTION_MAP.md`)

R1 preregs/RESULTS immutable once closed · R2 new scored measurement needs a
pushed prereg first · R3 universal regression gate before commit · R4 sim/
harness changes default OFF, bit-identical · R5 live runs export
`POLYFORGE_EVAL_SHARED_PG=1` · R6 planner auth posture · R7 cite numbers per
`RESULTS_MASTER.md` · **R8 manuscript prose is user-owned; agents do
engineering only.**

## 6. Next action

~~Start M1~~ — **done at the desk 2026-08-06** (see the M1 status block in §3).
The critical path is now **M2**, which is user-gated: B1 needs a GPU host, and
its first step is the live actuation dry-run M1 could not run here.

~~**Next desk action: M3**~~ — **stale, corrected 2026-08-11.** M3 closed in
session 34 (the specified theorem was vacuous; the redirect to the
reactive-vs-predictive cost separation is done and committed at `bec0f62`,
DEFENSE_QA #28). Two things now supersede this section:

1. **The execution level is `docs/PUBLICATION_ROADMAP.md`**, written after
   this file. Its §6 progress table is the live status; read it, not this
   paragraph. Milestone-level, the map is unchanged: **M2 (B1) is the
   critical path and is user-gated on a GPU**; WP8a, its live actuation
   dry-run, became desk-doable when Docker landed in session 36.
2. **The next desk action is WP1** (trace-replay eviction parity), not M3.
   It is the highest-value open item: `RESULTS_EVICTION_PARITY.md` showed
   the *synthetic* cost headline was substantially eviction accounting, and
   the two real-demand headlines (−70.4% / −42.5%) run through the same
   unfair code path and have never been re-scored. WP7 (push) closed
   2026-08-11; WP1's prereg is anchored at `97f5879`.
