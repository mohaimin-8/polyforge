# Learned joint control vs the MPC — results (pre-registered)

Protocol frozen in `PREREG_LEARNED_CONTROL.md`, pushed before the training run, the tuning sweep, or any matrix run. The learned arm is the RL analog of PolyForge's MPC: tabular Q-learning over the same ≤60-candidate replicas×cache×tier lattice, rewarded on the same objective, with a shared tenant-agnostic policy trained offline (`baselines/train_learned.py`, seeds disjoint from this matrix) and deployed frozen-greedy. `learned_online` is the FIRM-style learn-during-the-run ablation. Rerun: `python analysis_learned.py`.

Valid runs: 1200 hpa=300, jcac_anchored=300, learned_online=300, learned_trained=300

## Per-system summary (mean over the matrix cells)

| system | J | total_cost_usd | mean_violation | mean_jain |
|---|---|---|---|---|
| PolyForge MPC (anchored) | 0.4002 | 2.366 | 0.06825 | 0.9697 |
| Learned joint (offline-trained, frozen-greedy) | 0.7761 | 6.17 | 0.05589 | 0.9674 |
| Learned joint (online ablation) | 9.016 | 82.34 | 0.1522 | 0.8754 |
| HPA | 0.5555 | 3.711 | 0.07346 | 0.9626 |

## LR-H1 (confirmatory, non-inferiority) — MPC vs offline-trained learned policy

| reading | n | mean J (MPC) | mean J (learned) | diff (MPC−learned) | margin δ | p (one-sided) | verdict |
|---|---|---|---|---|---|---|---|
| LR-H1: J non-inferiority | 300 | 0.4002 | 0.7761 | -0.376 | 0.03881 | 3.896e-33 | PASS |
| two-sided superiority (reported alongside) | 300 | 0.4002 | 0.7761 | -0.376 | nan | 2.944e-28 | d_z=-0.708 |

95% bootstrap CI of the paired J difference (MPC − learned): [-0.4373, -0.318] — the citable unit at this n.

**LR-H1: PASS — and the pre-declared two-sided reading is stronger than the gate.** Non-inferiority holds, but the MPC is not merely *as good as* the offline-trained learned policy: it **beats** it on the composite objective (diff -0.376, p=2.94e-28, d_z=-0.708). The citable claim is therefore the strong one: **the hand-designed joint controller outperforms a well-trained model-free learner while paying zero training cost**, and it stays interpretable and carries the Props 1–2 guarantees the learned policy cannot offer. The non-inferiority framing was chosen before the data existed precisely because parity was the outcome we expected to have to defend; the measured result exceeded it.

**Mechanism (descriptive).** The learned policy is *not* worse everywhere: it attains slightly **lower violation** than the MPC (0.05589 vs 0.06825, diff +0.01236, p=0.000309) — but buys that attainment with **2.61× the spend** (6.17 vs 2.366 USD, p=2.74e-30). That is the same attainment-for-spend trade the tuned reactive scalers make (RESULTS_V2/V3): the learner rediscovers *buy headroom*, not the cost-efficient joint posture. Finding the cheap configuration — not meeting the SLO — is what the joint optimizer contributes.

## LR-H2 (confirmatory, data-efficiency) — trained vs online learned

| reading | n | mean J (trained) | mean J (online) | diff | p | d_z | verdict |
|---|---|---|---|---|---|---|---|
| LR-H2: J (trained − online) | 300 | 0.7761 | 9.016 | -8.24 | 4.605e-43 | -0.9392 | PASS |

**LR-H2: PASS** — the parity in LR-H1 is *bought* by the committed offline training budget. Model-free learning of the joint policy within a single deployment episode is materially worse; the MPC pays neither cost.

## LR-D1 (descriptive, no gate)

Paired readings vs the MPC (negative = MPC better):

| vs | metric | MPC mean | other mean | diff (MPC−other) | p | d_z |
|---|---|---|---|---|---|---|
| Learned joint (offline-trained, frozen-greedy) | J | 0.4002 | 0.7761 | -0.376 | 2.944e-28 | -0.7076 |
| Learned joint (offline-trained, frozen-greedy) | cost | 2.366 | 6.17 | -3.804 | 2.742e-30 | -0.7403 |
| Learned joint (offline-trained, frozen-greedy) | violation | 0.06825 | 0.05589 | 0.01236 | 0.0003088 | 0.2108 |
| Learned joint (offline-trained, frozen-greedy) | Jain | 0.9697 | 0.9674 | 0.002309 | 0.388 | 0.04992 |
| Learned joint (online ablation) | J | 0.4002 | 9.016 | -8.616 | 7.545e-45 | -0.9666 |
| Learned joint (online ablation) | cost | 2.366 | 82.34 | -79.98 | 1.435e-43 | -0.947 |
| Learned joint (online ablation) | violation | 0.06825 | 0.1522 | -0.08399 | 1.09e-50 | -1.056 |
| Learned joint (online ablation) | Jain | 0.9697 | 0.8754 | 0.09428 | 6.701e-68 | 1.325 |
| HPA | J | 0.4002 | 0.5555 | -0.1553 | 1.654e-47 | -1.007 |
| HPA | cost | 2.366 | 3.711 | -1.345 | 6.828e-29 | -0.7179 |
| HPA | violation | 0.06825 | 0.07346 | -0.005212 | 0.08826 | -0.09874 |
| HPA | Jain | 0.9697 | 0.9626 | 0.007147 | 0.0004038 | 0.2066 |

Per workload class (J diff, MPC − trained-learned; negative = MPC better):

| class | MPC J | learned J | J diff | p | d_z | n |
|---|---|---|---|---|---|---|
| agentic | 0.8554 | 1.45 | -0.595 | 2.343e-08 | -0.8319 | 60 |
| ai_cacheable | 0.5748 | 0.9563 | -0.3815 | 2.846e-19 | -1.705 | 60 |
| ai_uncacheable | 0.4625 | 1.247 | -0.7849 | 5.979e-13 | -1.184 | 60 |
| crud_bursty | 0.08054 | 0.1689 | -0.08832 | 1.788e-26 | -2.414 | 60 |
| crud_steady | 0.02768 | 0.05771 | -0.03003 | 0.0001143 | -0.5337 | 60 |

## Notes

- **Erratum (disclosed, protocol unchanged).** `PREREG_LEARNED_CONTROL` §3 labels the matrix "1,500 runs"; the frozen design it specifies — 4 systems × 5 workload classes × 4 tenant mixes × 3 cluster sizes × 5 reps — is **1,200** runs, and 1,200 is what executed and validated (all green). The stated total was an arithmetic slip in the pre-registration text; the design, the systems, the cells and the confirmatory unit (300 matched pairs, as §3 itself states) are exactly as frozen. Recorded here rather than corrected upstream: frozen protocols are not edited after the fact.
- The learned arm is trained on seeds disjoint from every cell scored here (train/val/test split, `PREREG_LEARNED_CONTROL` §2); the deployed policy is frozen (`train=False`, ε=0), so the committed Q-table alone determines its behavior and the runs replay from it.
- Fairness is not in the learned reward (cross-tenant, as with FIRM); the Jain column is measured, not optimized by that arm.
- Same substrate rules as every matrix campaign: sim-backend decision quality, blocked-factorial matched cells, never mixed with replay tables (ground rules 3–4, 6).
- Stopping rule §5 honored: one training run, one tuning sweep, one matrix execution, one analysis pass. SLO confirmatory attempts remain closed (v3 rule): LR-H1/H2 are J gates only.
