# Pre-registration: the live three-knob comparison, replicated on five independent seeds (WL-R; audit Phase 4)

Registered 2026-09-26. Committed and pushed **before** any run of
`eval/experiments/wave4_replication.yaml`; the push event is the timestamp
anchor, checked by `scripts/check_prereg_timing.py`. Scorer:
`analysis_wave4_replication.py`, committed with this file, tested on
synthetic matrices (`test_wave4_replication.py`), not edited after the run.

## Why this exists

The live evidence for the corrected joint controller is B1′
(`RESULTS_WAVE4_CALIBRATED.md`, WL-H1′ PASS). The 2026-09-26 audit found
four problems with it, none of which re-running B1′ would fix:

1. **One seed.** B1′ had two reps, and its bootstrap unit was the 10-second
   bucket pooled across reps, so its independent sample was one host × one
   seed. Arms in a cell also saw different demand (no shared seeds).
2. **In-sample.** The corrected controller was designed after B1 failed, on
   the same four cells and host.
3. **A harness artifact in the replica-only arm.** One tenant stayed pinned
   to the large tier with no cache after the knob preflight
   (`ERRATUM_WAVE4_REPLICA_ONLY.md`). Its "−99%" was the artifact. The
   harness now restores and verifies default knobs (`prepare_live_ai_knobs`).
4. **An untuned live HPA.** The replica-only arm ran the chart's HPA
   defaults (60% CPU target, `minReplicas` 1) against JCAC's floor of one
   replica per tenant.

This sitting measures the same comparison with all four removed.

## What was already seen (disclosed)

- **B1′'s results**, including WL-H1′ PASS in `joint_stress`, bucket-level.
- **The simulator campaigns of this audit.** `RESULTS_FAIR_J.md`,
  `RESULTS_STRUCTURAL_MISMATCH.md` and `RESULTS_RETUNED.md` found the joint
  controller's composite J tying tuned reactive HPA. On the live-fitted
  plant, fair HPA and the layered stack beat even JCAC planning with the
  true model.

No live run of `replica-only-tuned` exists, nor of any arm on `agentic`,
nor of any arm on shared seeds.

## Substrate (frozen)

- **Host:** B1′'s, a fresh `g6e.2xlarge` on-demand instance (8 vCPU, L40S)
  in the account's 8-vCPU G quota. A fresh instance is not a second host
  by design; the record states the instance id and `host_facts.json`.
- **Tiers:** three vLLM tiers (Qwen2.5 0.5B / 3B / 7B), started as for B1′
  (`docs/WAVE4_RENTED_HOST_RUNBOOK.md`).
- **Evidence:** the in-run clause-4 histogram, per-run evidence and 10-s
  fine exports, all as B1′.
- **Harness:** the harness as of this commit (1.1.0 or later):
  - the knob-restore fix;
  - the pre-window export filter, so the WL-H2 preflight's own requests are
    not scored (7eaba72, after B1′);
  - the append-only run history;
  - chaos is not used.
- **Load-validity gate, frozen here and not relaxed during the sitting:**
  - k6 failed requests ≤ 1%;
  - zero dropped iterations;
  - replica-sampler coverage ≥ 90% of the window.

## Arms (frozen)

| arm | role |
|---|---|
| `jcac-calibrated` | **treatment**: B1′'s corrected controller, flags unchanged |
| `jcac` | the published controller |
| `replica-only-tuned` | reactive HPA at the tuned utilization (tuned.yaml `hpa.target_rho` 0.3 → 30% CPU) and JCAC's replica floor (one per tenant = 8); `cluster_backend.tuned_live_hpa_values` |
| `replica-only` | reactive HPA at the chart defaults, as published, now with the knob-restore fix |
| `cache-only`, `tier-only` | the joint controller with two knobs pinned, as B1′ |

The 30% target is a **mapping** of the simulator's tuned target, not a live
tune. The simulator's ρ is replica work-unit utilization; the live metric
is pod CPU against its request.

## Cells and design (frozen)

| cell | role |
|---|---|
| `joint_stress` | primary (B1′'s WL-H1′ cell) |
| `agentic` | **held out**: never used to design or calibrate the controller. It has no live prompt pool (`PROMPT_POOL_BY_CLASS`), so its AI prompts are unique and uncacheable by construction: the cache knob cannot help here, and only replicas and tier can. As with every live cell, agent traffic is served through `/ai/chat`. |
| `ai_cacheable` | in-sample replication of a B1′ cell, descriptive |

Every cell uses the `uniform` mix, the `small` cluster size and 30 steps.

**6 arms × 3 cells × 5 reps = 90 runs**, one sitting. `shared_seeds: true`:
within a (cell, rep) every arm sees identical demand, and the five reps are
five independent seeds (15 distinct seeds).

**Budget:** B1′ measured 14.3 min per run, so about 21.5 h, about $48 at
$2.24/h plus setup (≈ $52).

## Tests (frozen)

The unit is the **seed**. For each comparator, a paired difference per seed
(treatment minus comparator). A comparator is **beaten** when all three hold:

- **Cost:** the exact one-sided sign-flip p of the five cost differences is
  ≤ 0.05. With five seeds that means the treatment is cheaper on every seed
  (p = 1/32).
- **Iso-fairness:** mean Jain is no worse than the comparator's by more than
  0.01 (WL-H1′'s margin).
- **Iso-attainment (new):** mean violation is no worse than the
  comparator's by more than 0.01.

Hypotheses:

- **WL-R1 (primary):** in `joint_stress`, `jcac-calibrated` beats **every**
  comparator. This is an intersection-union test, each component at α =
  0.05 unadjusted.
- **WL-R2 (secondary, out of sample):** the same in held-out `agentic`,
  reported as the out-of-sample check, never pooled with WL-R1.
- **Guard (registered):** WL-R1 recomputed with **control overhead** charged
  to the operator-driven arms. The operator and planner pods are priced as
  control-plane replica-equivalents by CPU request ((50m + 100m) / 100m ×
  $0.048/h over the 300-s window = $0.0060 per run, computed from the
  charts). A WL-R1 PASS that this guard reverses is reported as not robust
  to control cost.
- **Listwise:** a cell with fewer than five complete seeds is NOT
  EVALUABLE. It is not scored on fewer.
- **Descriptive:**
  - `ai_cacheable` with the same table;
  - `replica-only` against `replica-only-tuned`;
  - per-arm means and latencies.

## Predictions

- **WL-R1: FAIL.** The audit's reading of the B1′ evidence is that a
  correctly configured replica-only arm costs about what the calibrated arm
  costs in `joint_stress`. Winning also now needs every one of five seeds.
- **WL-R2: no directional prediction.** Low confidence, leaning FAIL.
- **`replica-only`** costs far less than its B1′ artifact figure.

## What each outcome means (committed before the run)

- **WL-R1 PASS:** the live joint-control result survives independent seeds,
  a tuned HPA, the fixed replica-only arm and an iso-attainment guard. It
  may be stated for `joint_stress` on this host class, with the guard's
  outcome beside it.
- **WL-R1 FAIL:** the B1′ live result does not replicate as a win over
  every arm. The live evidence is reported as this record states it, and
  "beats every arm live" is not claimed.
- **WL-R2 PASS:** the first out-of-sample live evidence for the corrected
  controller.
- **WL-R2 FAIL:** the controller's live advantage is not shown beyond the
  cells it was designed on.

## Stopping rule

- One sitting; the runner resumes, and a crash costs the run in flight.
- No re-run of a completed run, no change of arms, cells, seeds, gate or
  flags after the first run.
- A run the load-validity gate voids is recorded as void, and its seed is
  dropped for every arm in that cell (listwise). Nothing is re-seeded.
- The instance is terminated when the matrix completes or the budget
  ($70) is reached, whichever comes first. A budget stop is recorded with
  the runs banked.

## Pre-run amendment 1 (2026-09-26, before any run)

The sitting's budget is $63, not the $70 assumed above. The runner executes
arm by arm, so a budget stop partway through would leave the last arms
missing in every cell and make WL-R1 and WL-R2 NOT EVALUABLE. Two changes
follow, both made before any run and with no data seen:

- **The descriptive in-sample cell `ai_cacheable` is dropped.** The design
  is 6 arms × 2 cells (`joint_stress`, `agentic`) × 5 reps = **60 runs**:
  about 14.3 h, about $32 plus setup (≈ $36). Both registered hypotheses,
  the guard and the listwise rule are unchanged. The scorer and the
  experiment file drop the cell and change nothing else.
- **The budget cap is $55**, replacing $70. The instance is terminated at
  $55 of accrued cost, or when the matrix completes, whichever comes first.

