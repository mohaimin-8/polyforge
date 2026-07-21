# Pre-registration — learned joint controller (the RL analog of the MPC)

Registered 2026-07-21 (session 29). Committed and pushed **before** the
training run, the tuning sweep, or any matrix run; the push is the timestamp
anchor, OSF mirror prospective. This campaign closes the sharpest modern
*mechanism* question the matrix leaves open. PolyForge's thesis is that the
interesting problem is **joint** cross-layer coordination — replicas,
semantic cache, and model tier moved together against one objective — which
it solves by receding-horizon MPC over the known system model
(`controller.py`). The obvious 2026 reviewer objection is that the
hand-designed optimizer may be unnecessary: a **learned** policy could
discover the joint coordination on its own. Until now the answer was an
argument (the existing RL baseline, `firm`, learns only the replica knob,
OSDI '20 shape). This campaign makes it a measurement: it asks whether the
same model-free machinery, given the *full joint action space* and a
generous offline training budget, recovers what the MPC computes with **zero**
training.

## §1 The baseline (frozen; implemented before this push)

`learned` (`research/jcac_sim/baselines.py::LearnedJointController`): per-step
tabular Q-learning — the direct joint-knob generalization of
`FIRMReplicaController` — over the same ≤60-candidate lattice the MPC
enumerates:

    u = (Δreplicas ∈ {-2,-1,0,+1,+2},   # ±2/interval clamp, via apply_action
         Δcache_level ∈ {-1,0,+1},       # one level per interval (thrash rule)
         Δtier ∈ {-1,0,+1} over small/mid/large)

ordered so action index 0 is *hold* (an untrained state holds rather than
sheds to an outage; `tier="none"` is excluded, favoring the baseline).
Reward mirrors the objective the MPC minimizes and every baseline is tuned
on, on the same scales (`_COST_SCALE_USD`, `log1p(excess)` exactly as
`JCACController._project`): `r = -(α·cost_norm + β·log1p(excess))`. The
fairness term (γ·(1−Jain)) is cross-tenant and, like FIRM, is not in the
per-tenant reward; the resulting Jain is **measured, not assumed**.

Two deployment shapes, both frozen here:
- **`learned_trained`** (primary): a **shared, tenant-agnostic** Q-table
  (state includes the SLO class) trained offline and deployed frozen-greedy
  (`train=False`, ε=0) from the committed artifact. This is the standard RL
  deployment model and the only way the policy transfers across episodes and
  tenant counts.
- **`learned_online`** (data-efficiency ablation): FIRM-style per-tenant
  online learning within the run (`shared=False`, ε=0.1) — data-starved over
  the joint space by design.

Experience replay (`replay_batch` updates/step from a bounded buffer) makes
the trained arm a genuinely strong learner, not a straw man. Like every
reactive baseline it evicts LRU and does not itself honor cluster caps (the
v3-disclosed asymmetry, strict against PolyForge). Unit-tested: knob-bound +
cache-thrash compliance, seeded reproducibility, untrained-default-hold,
all-three-knobs exercised, shared-table learning + save/load round-trip,
`make_baseline` construction (`test_jcac.py`).

## §2 Training + tuning (frozen protocol; `baselines/train_learned.py`)

An RL controller is trained offline and deployed frozen, so — unlike the
reactive baselines tuned online by `tune.py` — its selection is a
train/val/test split, none of which touches the eval-matrix seeds:

- **TRAIN.** One shared Q-table accumulated across all 5 workload classes ×
  4 mixes × 3 cluster sizes, TRAIN seeds (base `51_2026`, sha256-derived,
  2 passes), 160 steps/episode, ε decaying linearly 0.4 → 0.02.
- **VAL (tuning).** For each hyperparameter combo — `learning_rate ∈
  {0.2, 0.3}`, `replay_batch ∈ {8, 16}`, `reward_beta ∈ {2.0, 3.0}`
  (`discount`, `replay_size` frozen) — deploy the trained policy
  frozen-greedy on a VAL slice (5 classes × {uniform, whale} × medium, VAL
  seeds base `61_2026`), scored on the paper's J. Lowest-J combo wins — the
  W34 "tune on a disjoint slice, never on the eval cells" rule applied to the
  offline policy. Grid CSV commits to `grids/learned_train.csv`.
- **FIT.** Refit the winner on TRAIN; write the committed artifact
  `research/results/learned_qtable.json`, which `systems.py::learned_trained`
  deploys. The artifact is the pre-registered, reproducible policy.

The training budget (~360 episodes) was de-risked before this push (session
29 exploratory smoke, uncommitted): properly trained, the learned arm reaches
J parity with the MPC on individual cells — a credible baseline, not a straw
man. That smoke fixed no number and is not a result.

## §3 Matrix (frozen)

`eval/experiments/matrix_learned.yaml`: sim backend, 120 steps, **systems
[jcac_anchored, learned_trained, learned_online, hpa]** × 5 workload classes ×
4 tenant mixes × 3 cluster sizes × 5 reps = **1,500 runs**, standard
validation (row counts, no dupes/NULLs). `jcac_anchored` is the quotable
controller; `hpa` rides along for the descriptive sanity reading only.
Blocked-factorial pairing on (workload, mix, size, rep), the same matched-cell
design as every matrix campaign. The 300 matched (jcac_anchored,
learned_trained) cells are the confirmatory unit.

## §4 Hypotheses

- **LR-H1 (confirmatory, non-inferiority):** `jcac_anchored` is non-inferior
  to `learned_trained` on composite J — its mean J does not exceed the
  learned policy's by more than a frozen margin δ = 5% of the learned
  policy's mean J (one-sided paired test over the 300 matched cells,
  p < 0.01). Interpretation: **the MPC matches the offline-trained learned
  policy at zero training cost.** The two-sided paired difference, d_z, and
  its 95% bootstrap CI are reported alongside (the citable unit at this n).
- **LR-H2 (confirmatory, data-efficiency):** `learned_trained` beats
  `learned_online` on J (paired t, p < 0.01, mean diff < 0) — i.e., the
  parity in LR-H1 is *bought* by the committed offline training budget, which
  the MPC never pays. This quantifies the data cost of the learned route.
- **LR-D1 (descriptive, no gate):** per-workload-class J / cost / violation
  of jcac_anchored vs learned_trained vs learned_online vs hpa — where the
  learned policy matches, exceeds, or trails the MPC, and where each fails.
  No pass/fail; this reading exists so the learned arm's strength is on the
  record.

**Falsifier, pre-committed:** if `learned_trained` *beats* `jcac_anchored` on
J at p < 0.01 with |d_z| ≥ 0.5 (LR-H1's non-inferiority direction reversed
into a real loss), that result headlines the thesis limitations — a learned
policy discovers better joint coordination than the hand-designed MPC — and
motivates learned control as the primary future direction, published exactly
as measured. Symmetrically, if `learned_online` matches `learned_trained`
(LR-H2 fails), the offline budget was unnecessary and that is reported too.

## §5 Stopping rule

One training run over the frozen protocol (crash retries only), one tuning
sweep over the frozen grid, one 1,500-run matrix execution (retries: 2), one
analysis pass (`analysis_learned.py` → `RESULTS_LEARNED.md`). No grid
widening, no retraining after any matrix number is seen, no second matrix.
The committed Q-table artifact is frozen once written. SLO confirmatory
attempts remain closed (v3 stopping rule): LR-H1/H2 are J gates; violation
readings ride as disclosure or description only. What is measured is the
mechanism comparison — MPC vs learned joint control — whichever way it falls.
