# Response to the reviewer report — point by point

Paper: *Joint versus Per-Layer Control of Replicas, Caches, and Model Tiers in a
Simulated Multi-Tenant LLM Gateway: A Pre-Registered Study* (revised `main.tex`).

How this was done: every reviewer statement was checked against the paper, the
simulator code (`research/jcac_sim/`, `eval/harness/`) and the committed records
(`research/analysis/`). Each item below gives the **verification** (✓ correct,
◐ partly correct, ✗ incorrect), the **action** with its location in the revised
paper, or the **answer** where no change was possible. Nothing new is claimed
that a record or the code does not show; no experiment was run for this revision.

## Count

| Part of the report | Items | Changed in the paper | Answered, no change | Needs new experiments |
|---|---:|---:|---:|---:|
| Major weaknesses (1–9) | 9 (30 sub-points) | 23 | 7 | 3 of the 23 (1b, 1c, 2d: stated as limitations) |
| Minor issues | 8 | 7 | 1 (m6) | 0 |
| Questions | 6 | — | 6 answered | 3 (Q1, Q5; Q4 in part) |
| "What would move me to accept" | 5 | 1 done, 2 in part | — | 2 (A2, A5) |

Reviewer statements found **incorrect or only partly correct**: 1c ("no damping"),
2d ("no real traces"), 4c ("one-sided defect search"), 4d ("selective reporting"),
7 ("caps are per-tenant"). In each case the paper had omitted the fact; the
revision now states it.

Revised paper: 6 pages (blind), A4, 22 references, conclusion in page 6's left
column, all checks pass. Section numbers below refer to the revised paper:
I Introduction · II Related Work · III Methodology (A System Overview, B The JC
Planner, C Plant, Workloads and Objective, D Comparators and Ablations,
E Statistics and Pre-Registration) · IV Results (A Adjudication Chain, B What
Remains, C Why Coordination Buys Little) · V Lessons · VI Threats · VII Conclusion.
Figures: 1 system workflow, 2 adjudication chain, 3 cost–violation trade-off,
4 width. Tables: I campaigns, II arm means, III surviving results, IV checklist.

---

## Major weaknesses

### 1. "The study refutes one weak controller, not joint control"

| # | Reviewer point | Verification | Action / answer |
|---|---|---|---|
| 1a | Without shedding JC's J (0.4668) is worse than re-tuned HPA (0.4006), capped HPA (0.4095) and the static share (0.4226). | ✓ Table II. | **Fixed.** Title, abstract and conclusion now scope the claim to *one rate-limited planner* in *a simulated gateway*. Abstract: "without [shedding] it loses to the capped autoscaler". IV-B: "trails the static share (0.4668 against 0.4226, Table II)". |
| 1b | JC picks one lattice move per interval; the paper never says how the 6-step horizon is used; sequenced actions are ruled out. | ✓ (and the paper omitted it). `research/jcac_sim/controller.py` header: receding-horizon MPC with **move blocking** — each candidate is held fixed across the horizon, only the first move is applied, then the planner re-plans. | **Fixed (method) + limitation.** III-B now states exactly this and the forecaster. VI: "A multi-step planner might find sequenced coordination." |
| 1c | Wide steps degrade a "myopic controller with no damping"; default HPA resembles the wide lattice; wide-step JC never compared with HPA or the stacks. | ◐ "No damping" is incorrect: Φ carries a switching penalty λ·sw (λ = 0.05 per changed knob, Eq. 1). The other two parts are correct (the HPA resemblance was already in V). | **Limitation added.** VI: "wide-step JC was not compared with HPA or the stacks." |
| 1d | INFaaS [5] and Zhu et al. [6] were not re-evaluated; the paper only retracts the authors' own claim. | ✓ | **Scoped.** II now states the difference from [9]: "we adjudicate our own controller and isolate coordination with a decoupled-search ablation." The title/abstract no longer generalize to joint control at large. |

### 2. External validity

| # | Reviewer point | Verification | Action / answer |
|---|---|---|---|
| 2a | Replicas are not GPU model servers; no batching, KV cache or queueing; the title over-claims "LLM serving". | ✓ (`model.py`: models run outside the plant; a replica does the per-request work of every call). | **Fixed.** Title: "…in a Simulated Multi-Tenant LLM Gateway". III-A: "Its replicas are the tenants' serving pods…; no replica is a GPU model server." VI: batching, KV-cache memory and queueing "lie outside the study". |
| 2b | Live-fitted plant ≥ 1,123 units/s (≈ 11× the published 100) and AI never loads its replicas; the coupled plant is a probe-grid stress point. | ✓ (`research/calibration/LIVE_PLANT_FIT.md`: replica_capacity_wu 100 → 1123). | **Already disclosed (IV-C, VI); strengthened.** The mismatch result is now in the Results (see 2f). See Q4. |
| 2c | Fixed cache curve, no similarity threshold or false hits, no backlog, no tier-switch lag. | ✓ | **Acknowledged** (III-C lists what is not modeled; VI). |
| 2d | "There are no real traces." | ◐ Real-trace replays (BurstGPT, Azure LLM 2024) were run earlier in the program, but only against the **pre-audit** comparators; their cost headline was later withdrawn and they were not repeated under the audited protocols. | **Clarified.** VI (External): "earlier real-trace replays ran only against pre-audit comparators." No trace-based claim is made. |
| 2e | No live evidence (2 reps, one GPU host; registered replication not run). | ✓ | **Already disclosed; kept** (VI; VII lists the live replication as future work). |
| 2f | The simulator favours the planner; SM-H2 (layered stack wins by 0.1052) is larger than any Table III effect yet sits in the threats. | ✓ (0.1052 > largest \|ΔJ\| in Table III, 0.0629). | **Fixed.** New Results paragraph IV-B, "A wrong plant model reverses the ranking … more than any difference in Table III." |

### 3. "The objective rewards dropping traffic"

| # | Reviewer point | Verification | Action / answer |
|---|---|---|---|
| 3a | Shedding is priced only through violation capped at 1 per request family. | ✓ | **Fixed.** IV-B: "$J$ prices a shed request only through the violation term, capped at 1 per request family, [so] $J$ favors shedding; the never-shed arm prices it strictly." |
| 3b | Only JC sheds/enforces budgets; it sheds 7.2% (11.3% on unseen demand). | ✓ | **Already stated (III-D, IV-B); kept.** See Q2 for the strict price. |
| 3c | Baselines exceed budget on 3–10% of tenant-steps and J does not penalize it. | ✓ (Table II: 3.1–10.4%). | **Fixed.** VI (Construct): "ignores the baselines' budget overruns (3.1–10.4% of tenant-steps)". |
| 3d | Conclusions flip with the violation weight; report a Pareto front. | ✓ (crossover at weight 2, SENSITIVITY_FAIR). | **Partly.** The crossover was and is reported (VI). Fig. 3 shows cost against violation for every arm; a three-axis (cost, violation, shed) front would need new figures and space and was not added. |

### 4. Pre-registration framing

| # | Reviewer point | Verification | Action / answer |
|---|---|---|---|
| 4a | The campaign sequence is adaptive, not confirmatory in the usual sense. | ✓ | **Fixed.** III-E: "The campaigns are confirmatory one by one but adaptive as a sequence: each was designed after the exploratory re-analysis of its predecessor." |
| 4b | EVICTION_PARITY (late-registered) is the first chain step; Table V's "−27.5% → −0.4%" comes from it while the text cites −0.9% (FJ-H1). | ✓ (FIGURE_ADJUDICATION_CHAIN.md rows: −0.4% EVICTION_PARITY, −0.9% FJ-H1). | **Fixed.** IV-A: "Removing both defects (EVICTION_PARITY, registered after its first run) erased the lead." Table IV caption: each effect is from the campaign that removed the defect. |
| 4c | Defect hunting may have been one-sided. | ◐ Several checks targeted defects **against** JC (see Q3); one was found and fixed (the non-convergent two-sweep solver). The forecaster was not upgraded. | **Fixed.** VI: "Checks for defects against JC found one, the non-convergent solver, which every audited campaign replaced", plus the forecaster gap. |
| 4d | 7 of 57 protocols reported; selective reporting cannot be checked; links withheld. | ◐ All 57 protocols and all 122 p-valued tests (74 pass, 57 after program-wide Holm) are in the committed ledger (`HYPOTHESIS_LEDGER.md`), and III-E reports those counts; the links are withheld only for double-blind review and are printed in the camera-ready version. | **Answered; no change.** |
| 4e | WD-H1 and WD-H5 (published plant) fail program-wide Holm, yet the abstract states the growth unqualified. | ✓ | **Partly fixed.** The abstract now gives the exact effect (4.4- to 5.3-fold); IV-C states which tests survive program-wide Holm. A qualifier in the abstract would exceed IEEE's 250-word limit (now 249). |

### 5. Statistical inference

| # | Reviewer point | Verification | Action / answer |
|---|---|---|---|
| 5a | The bootstrap resamples a fixed factorial grid; the abstract/conclusion generalize. | ✓ | **Fixed.** Abstract: "On 60 synthetic design points…". VI keeps "generalize only over the sampled workloads, mixes and cluster sizes". |
| 5b | ±0.025 J has no operational basis; the abstract quotes the exploratory 5% bound. | ✓ (PREREG_JOINTNESS: the margin is "the smallest difference this design can detect (about 0.025–0.030 J at n = 60, power 0.8)"). | **Fixed.** III-E now gives this rationale; VI: "the margin is the design's detectable difference, not an operational threshold"; the exploratory 5% bound is no longer in the abstract. |
| 5c | "Wins confined to the largest cluster" is descriptive yet a heading. | ✓ | **Fixed.** IV-B run-in now reads "Wins appear only on the largest cluster (descriptive, untested; …)". The per-size figure (old Fig. 3) was removed. |

### 6. Mechanism and checklist

| # | Reviewer point | Verification | Action / answer |
|---|---|---|---|
| 6a | The separability argument is heuristic and close to known results (diagonal dominance, Bristol's RGA); the test is a replication, not blind. | ✓ (the paper already says "heuristic", cites the RGA, and calls WIDTH "a confirmatory replication, not a blind test"). | **No change needed;** the contribution is framed as a pre-registered test of the principle in this domain. |
| 6b | The checklist repeats [7]–[9]. | ◐ The rules echo [7]–[9]; what is new is each rule paired with the effect it had on one study. | **Answered.** |
| 6c | State what is new relative to [9]. | ✓ | **Fixed.** II: "Unlike that study, we adjudicate our own controller and isolate coordination with a decoupled-search ablation." |

### 7. "Multi-tenancy is barely exercised"

| Reviewer point | Verification | Action / answer |
|---|---|---|
| Caps are per-tenant; the interference penalty is omitted (ever nonzero?); only Jain couples tenants; no shared-pool contention. | ◐ **Incorrect about the caps.** `eval/harness/workloads.py` (`CLUSTER_SIZES`): each cluster also caps the tenants' **combined** replicas (24, 48, 96) and cache (2, 4, 8 GB), below the sum of the per-tenant replica caps (8 × 6, 10, 16), and the planner fills these shared caps first-come-first-served per sweep — tenants contend for one pool. **Correct about interference:** noisy-neighbor injection is off in all seven campaigns (`eval/harness/config.py`, default `interference: false`; only `fairness_v2`/`vtc_fairness` enable it), so the penalty was zero throughout. | **Fixed.** III-C now states the shared caps; III-B states that no campaign injected a noisy neighbor. |

### 8. Missing details

| # | Reviewer point | Verification | Action / answer |
|---|---|---|---|
| 8a | The forecaster is never described. | ✓ The evaluated arm uses the controller default `trend`: each tenant's linear trend over its last three intervals, extended over the horizon (`controller.py`, `_trend`). | **Fixed.** III-B describes it; VI adds that on the real BurstGPT trace a damped Holt forecaster cut one-step error by 6.9–26.9% across the trace's segments (`FORECAST_TRACE_REAL.md`; the synthetic stand-in in `FORECAST_TRACE.md` favours trend). |
| 8b | Horizon use; the decoupled arm's "repairing only feasibility"; budget filter vs realized cost. | ✓ | **Fixed.** III-B (move-blocked receding horizon); III-D: the arbiter keeps "the best feasible subset of their moves when together they break a budget or cap" (`controller.py`, `_best_for_tenant_decoupled`). The projected-vs-realized cost point is in Table II's caption. |
| 8c | Tuning grid ranges and the "separate slice". | ✓ | **Fixed.** III-D: tuned on 3 of the 5 workloads, 2 of the 4 mixes, 2 reps, over grids with interior optima (PREREG_RETUNED; TUNING_EXTENDED: HPA ρ* ≈ 0.05, KEDA q* ≈ 0.1). |

### 9. Related work is thin (17 references)

| Verification | Action / answer |
|---|---|
| ✓ | **Partly fixed.** Five references added, each verified against Crossref or arXiv: vLLM (SOSP 2023), Splitwise (ISCA 2024), RouteLLM (arXiv:2406.18665), Sinan (ASPLOS 2021) and Roy et al.'s predictive autoscaler (IEEE CLOUD 2011). II now places SysX above GPU model servers and among coordinated and predictive autoscalers. The rest of the list (Clipper, InferLine, Nexus, AlpaServe, Llumnix, DistServe, SpotServe, Hybrid LLM, FIRM) does not fit the 6-page limit. |

---

## Minor issues

| # | Reviewer point | Verification | Action |
|---|---|---|---|
| m1 | Dense test-ID notation. | ✓ | **Partly fixed:** seven IDs that Fig. 2 or Table III already show were removed from the prose (FJ-H1, RT-H1, JN-B1, HO-B1, HO-C1, AC-H3, AC-H4). |
| m2 | "KEDA loses … loses again" is ambiguous. | ✓ | **Fixed:** "JC beats KEDA at its fair setting, ties it once KEDA is re-tuned, and beats it again when KEDA is held to the caps." |
| m3 | HPA numbers differ (−0.9 → 0.0 in Table V; −0.4% implied by Table II; −2.5% for JN-B4). | ✓ They come from different campaigns (FAIR_J/RETUNED vs JOINTNESS) with their own arms and seeds. | **Fixed:** Table IV caption: each effect is from the campaign that removed the defect. |
| m4 | "Four- to fivefold" is 4.4× and 5.25×. | ✓ (0.0407/0.0092 = 4.4; 0.0231/0.0044 = 5.25). | **Fixed:** abstract says "4.4- to 5.3-fold". |
| m5 | γ′ primed for no reason; planning vs scoring objectives. | ✓ | **Fixed:** γ′ → γ in Eq. (1) and the text. III-C already reports that planning on violation instead changes J by 0.0047 [−0.0025, +0.0121]. |
| m6 | Fig. 1 is overloaded at column width. | ◐ (subjective) | **Kept** as a full system workflow at the author's request; it was compacted earlier. |
| m7 | "Exact potential" gives a per-tenant best-response fixed point, not a global optimum. | ✓ | **Fixed:** III-B: "an equilibrium of the tenants, not a global optimum, with joint search inside each tenant". |
| m8 | "23% worse on unseen demand" needs "instant actuation only". | ✓ | **Fixed:** abstract: "on unseen demand, with instant actuation, it scores 23% worse than that stack." |

---

## Questions for the authors

**Q1. MPC-style multi-step planner — does coordination stay ≈ 1.6%?**
JC is already a receding-horizon (MPC) controller, but with move blocking: one
candidate is held across the six-step horizon (`controller.py`). An unblocked
multi-step planner was not evaluated. *Not done; stated as a limitation (VI).*

**Q2. JC's J with shedding priced realistically?**
The strictest price is the registered never-shed arm (Table II, `jcac_tier_floor`):
J = 0.4668 against 0.3991 with shedding (+0.0677 [0.0432, 0.0945]); it then
trails re-tuned HPA (0.4006), capped HPA (0.4095), capped KEDA (0.4220), the
static share (0.4226) and the cheapest-tier stack (0.3814), and loses to capped
HPA. Intermediate per-request shed penalties were not re-scored. *Answered (IV-B).*

**Q3. Was equal effort spent on defects that disadvantage JC?**
Yes, partly, and one was found:
- the two-sweep solver could stop before every tenant best-responded; every
  audited campaign uses the convergent solver instead (PREREG_FAIR_J);
- sweep order (ORDER_PERMUTATION): OP-H1 and OP-H2 FAIL, but the published order
  lies only 0.0001 *above* the permutation range in Jain (slightly favouring
  JC), and OP-H3 PASSES: cost is order-independent;
- the planning surrogate: planning on violation instead of log-excess changes J
  by +0.0047 [−0.0025, +0.0121] (no effect);
- the 50-sweep cap was never reached where counted (JOINTNESS);
- **not fixed:** the forecaster. On the real BurstGPT trace a damped Holt
  forecaster had 6.9–26.9% lower one-step RMSE than the trend forecaster JC
  used (FORECAST_TRACE_REAL). Joint and decoupled search share the forecast, so
  this cannot explain the coordination result, but it can disadvantage JC
  against the baselines. *Answered (VI).*

**Q4. Why is the published plant 11× slower, and what do results look like on the live-fitted plant?**
The published plant's capacity (100 work units/s) is the model's design value
(`model.py`); the live-fitted value (1,123) came from later measurements of the
deployed system (LIVE_PLANT_FIT). On the live-fitted plant, coordination does
not grow with step width (G 0.0033 → 0.0021; WD-H3, EQ), and with the planner
keeping the published model, the layered stack beats JC by 0.1052 (SM-H2, now in
IV-B). A full comparator campaign on the live-fitted plant was not run. *Answered; not done.*

**Q5. Wide-step JC against default-configured HPA?**
Not run. WIDTH shows wide steps raise JC's own J by 0.0152–0.0859 (WD-H5).
*Not done; stated as a limitation (VI).*

**Q6. Operational basis of the ±0.025 J margin?**
None is claimed: it is the smallest difference the registered design detects at
n = 60 with power 0.8 (PREREG_JOINTNESS), about 6% of JC's J. *Answered (III-E, VI).*

---

## "What would move me to accept"

| # | Condition | Status |
|---|---|---|
| A1 | Reframe to "a rate-limited, one-step joint planner gains little … in this setting". | **Done** — title, abstract, conclusion. |
| A2 | Add a stronger (MPC or multi-step) joint controller. | **Not done** — needs a new controller and a registered campaign. |
| A3 | Real traces, and GPU replicas or drop "LLM serving" from the title. | **Partly** — title now "Simulated Multi-Tenant LLM Gateway"; III-A says what a replica is. Real traces under the audited protocols: not done. |
| A4 | Pareto analysis (cost, violation, shed) as the primary result, shedding priced independently. | **Partly** — Fig. 3 (cost vs violation, every arm) and the never-shed arm as the strict shed price; no three-axis front. |
| A5 | Complete the registered live replication. | **Not done** — stated in VI and VII. |

---

## What changed in the paper (change log)

Title · abstract (scope, 249 words) · II (+5 verified references; difference from
[9]) · III-A (what a replica is) · III-B (move-blocked horizon, trend forecaster,
equilibrium not optimum, interference off, γ′ → γ) · III-C (shared cluster caps)
· III-D (tuning slice, repair rule) · III-E (margin rationale, adaptive sequence)
· IV-A (EVICTION_PARITY late registration, KEDA wording, fewer IDs) · IV-B
(descriptive label, shed pricing, never-shed vs static, SM-H2 moved here) · old
Fig. 3 (per-size) and old Table IV (width per plant) removed to make room — their
key values remain in the text and Fig. 4 · Table IV (checklist) caption · VI
(rewritten: substrate, construct, internal, external, statistical) · VII (scope).

Page budget: the double-blind version is 6 pages. The camera-ready version (with
authors, header and copyright line) is now 7 pages, 2.7 in (about a quarter of a
column) over, and will need that much trimmed if the paper is accepted.
