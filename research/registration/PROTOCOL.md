# PROTOCOL: WP14, programme 2's confirmatory simulation campaign (H1 to H4)

**Status:** draft of 2026-11-20 for the author's review; not frozen, not
registered. It is frozen when commit C holds it together with `design.json`
marked `"draft": false`, and the push of C is T0 (section 7). It is
registered on OSF before the round named in section 7 is due. Later changes
only as dated notes after this text, each one also a deviation (section 10).

## 1. Question

When is per-knob control enough for an LLM serving stack, and when does joint
control pay? The account under test (plan v2, Part A3): the loss of searching
each knob on its own is bounded by an interaction index that can be measured
on a running system; the index is small when the layers share no capacity or
move in small steps, and large when they share capacity and move far in one
step. This campaign tests four of the account's predictions in simulation, on
regimes and workloads drawn after registration from a public random round.

## 2. Data seen before registration

All of it is pilot data; none of it is reused as confirmatory evidence.

- **WP4 dose-response pilot** (`research/theory/DOSE_RESPONSE_PILOT.md`): the
  published plant, 25 regimes (kappa in {0, 0.25, 0.5, 1, 2} x step width in
  {1, 2, 4, 8, 16}), 20 design points each, one replication. Theorem 1 held
  on all 456,876 logged decisions. Spearman's rho of the regime's gain on its
  mean worst-case index was +0.74 (quantile index +0.74, realised one-step
  loss +0.79, share of costly disagreements +0.77). Its runs
  (`research/theory/data/dose_response_runs.csv.gz`) fix H2's calibration
  line.
- **WP9 comparator campaigns**
  (`research/comparators/full/plantA_own_large_s120_enforced_ca352ebd/` and
  `plantB_own_large_s120_enforced_58b57ad3/`): each arm's configuration was
  selected on the population's tuning half (120 workloads) and run on its
  evaluation half (120). J(per_knob) - J(joint) at the overall
  configurations: plant A -0.0000 [-0.0000, -0.0000] (joint better on 1
  workload, worse on 6); plant B -0.0000 [-0.0006, +0.0006] (19 / 14). The
  selected joint configurations' mean J on the tuning half: 0.2097 (A) and
  0.2008 (B).
- **Sample size** (`research/comparators/SAMPLE_SIZE.md`, from WP9's pilot
  variances; alpha 0.05, power 0.9): equivalence within 2% of J in plant A
  needs 76 paired workloads (sigma_d 0.0099); superiority at an effect of 2%
  of J in plant B needs 11 (sigma_d 0.0044). This campaign draws 120.
- **Rehearsals**: the campaign and the scorer were run with a made-up master
  seed and two of everything (35 runs), as a pipeline check that gives no
  result: on GitHub (`.github/workflows/campaign.yml`, branch
  `offload/rehearsal`, last on 2026-11-20 with the current scorer, run
  44693289176) and on the laptop the same day. Every run completed each
  time, and both logged the same 3,824 decisions under Theorem 1.
- **Completion check** (the laptop, 2026-11-20): every job of the full
  design, built from another made-up master seed (2,431 jobs, c = 71 in
  section 4), was run once, keeping only whether each completed and its run
  time; no outcome was written or read. All 2,431 completed (10.4
  worker-hours).

## 3. Hypotheses

| # | Prediction | Data | Supported when |
|---|---|---|---|
| H1 | Theorem 1: the realised one-step loss of per-knob search never exceeds the interaction index | every logged decision of the dose part where the theorem applies | at least one decision is checked and none violates it (a count, not a test) |
| H2 | Across new regimes, the mean index predicts the gain of joint search | the 20 regimes of the dose part | rho >= 0.8, Holm-adjusted one-sided p < 0.05 and calibration slope in [0.5, 1.5] |
| H3 | Mode A (API-backed gateway): joint and per-knob search are equivalent | plant A, 120 drawn workloads | two one-sided tests reject a difference of 2% of mean J(joint) or more; Holm-adjusted p < 0.05 |
| H4 | Mode B (self-hosted pools, fast scaling): joint search lowers J | plant B, 120 drawn workloads | one-sided paired t-test of J(per_knob) - J(joint) > 0; Holm-adjusted p < 0.05 |

The plan's other hypotheses are outside this campaign:
- H5 (H3 and H4 on the testbed) is not registered: gate G3 was not met
  (`testbed/wp12/PILOT.md`: the simulator is not validated, and the testbed's
  high regime needs 53 pairs for a predicted effect of 4.3%, below the 10% a
  live regime needs).
- H6 (the simulator predicts the testbed) was scored before this protocol
  (`testbed/validation/VALIDATION.md` and `VALIDATION_V2.md`: attainment error
  65% and 29.8% against a target of 25%; rank correlation 0.89 in WP11b
  against 0.8).
- H7 (the published fused decision) is WP15's, reported as exploratory
  (`research/external/routebalance/RESULTS.md`).
- H8 (the Kubernetes horizontal-with-vertical system, WP17) is registered
  separately if its main runs are made.

## 4. Design

**Dose part (H1, H2): 800 runs.**
- Plant: programme 1's published plant, as WP4 ran it. kappa (`model_form`
  key `wu_ai_scale`) is the share of an AI request's work that lands on the
  replicas (1 = published).
- Regimes: each replica step width w in {1, 2, 4, 8, 16} (WP4's arms; cache
  radius 1, 1, 2, any, any) crossed with four equal strata of kappa in
  [0, 2]. In stratum q (q = 0 to 3), kappa = 0.5 x (q + u) with
  u = run_seed(M, `dose|kappa|w{w}|q{q}`) / 2^64, uniform on [0, 1). That
  gives 20 regimes, and a regime repeats one of the pilot's with probability
  zero.
- Design points: WP4's 20, the workloads `ai_cacheable`, `ai_uncacheable`,
  `agentic`, `joint_stress` and `mmpp_mixed` crossed with the tenant mixes
  `uniform`, `premium_heavy`, `besteffort_heavy` and `whale`; large cluster;
  120 steps; one replication. A design point's demand seed is shared by
  every regime and both arms (identity `dose|demand|{workload}|{mix}`).
- Arms: the joint planner and the decoupled (per-knob) planner at width w
  (`x_w{w}_joint`, `x_w{w}_decoupled`).

**Comparison part (H3, H4): 1,560 + c runs, where c is the number of drawn
workloads in class `conversation`: 600 + 2c in plant A and 960 - c in
plant B (1,620 when c = 60; the whole campaign 2,360 + c runs).** In plant
A the joint and one-knob arms selected another configuration for
`conversation` workloads; in plant B the joint and rules arms did for
`conversation`, and the per-knob, one-knob and layers arms for
`api_or_code`. Each such workload runs both configurations.
- Plants: plant 2 (`research/plant2/`): mode A (API-backed gateway) as
  plant A and mode B (self-hosted pools of model servers) as plant B, with
  the constants `research/plant2/CONSTANTS.md` lists (plant digests
  `c1b720fd0ab5` and `565d559a55a6`, as WP9's `tuned.json` files record them).
- Workloads: 120 new workloads of eight tenants each, drawn from the
  population process (`research/population/population.py`'s sampler on the
  real 600 s windows in `research/population/data/windows_600s.csv.gz`) with
  its generator seeded by run_seed(M, `comp|population`). They are named
  `pop1000` to `pop1119`, so none is one of the 240 workloads WP9 tuned or
  evaluated on. A workload's class (`api_or_code` or `conversation`) follows
  WP9's rule: the stream family that carries the larger share of its demand.
- Settings: tenant mix `own`, budget enforced, large cluster, 120 steps,
  quality weight 0 (as WP9 tuned).
- Arms: WP9's five, each at the configuration WP9 selected on its tuning
  half (frozen in the two `tuned.json` files): `joint` (the joint planner),
  `per_knob` (each knob best-responded alone on the shared objective, winners
  combined), `one_knob` (one knob at a time to a fixed point), `layers`
  (independent per-layer planners) and `rules` (a per-layer stack of rules
  with the budget gate); definitions in `research/comparators/README.md`. Two
  granularities: one configuration for every workload (`overall`) and one per
  class (`per_class`); a configuration selected at both runs once.
- Every arm of a workload sees the same demand (identity
  `comp|demand|{workload}`).

`research/registration/wp14_campaign.py` builds the full job list, in a fixed
order, from M and `design.json`.

## 5. Metrics

- **J**, the composite objective (lower is better), is the outcome of every
  test.
  - Dose part, WP4's `objective_j`: cost / (119 x 8) / 0.01 + 2 x mean
    violation + 0.5 x (1 - mean Jain).
  - Comparison part, WP9's tuning objective
    (`research/comparators/protocol.py`): cost / (scored steps x tenants) /
    0.01 + 2 x mean violation + 0.5 x (1 - mean Jain), with the quality term
    off.
- **Gain** = J(per-knob) - J(joint), positive when joint control is better
  (in the dose part, J(decoupled) - J(joint)).
- **Predictor** (H2): the worst-case interaction index of the joint
  planner's decisions, logged for every tenant at every interval
  (`research/theory/controller_index.py`, which applies
  `research/theory/interaction_index.py`; definitions in
  `research/theory/INDEX_THEORY.md`), as its mean over a run, averaged over
  the regime's design points.
- Reported beside J and not tested: each arm's differences in total cost
  (USD) and in mean violation.

## 6. Tests

alpha = 0.05, with Holm's correction over H2, H3 and H4 (a family of three).
H1 is a count and stays outside the family. `research/registration/wp14_score.py`,
frozen with this file, computes everything below.

- **H1.** Over the runs that completed, count the decisions where Theorem 1
  applies and the decisions that violate it. Supported when at least one
  decision is checked and none violates it.
- **H2.** Per regime, the mean gain and the mean predictor over the design
  points where both arms completed. Spearman's rho of gain on predictor over
  the 20 regimes, with a one-sided permutation p-value (null: no positive
  association): 10,000 permutations of the gains' ranks from a generator
  seeded by run_seed(M, `score|H2|permutation`), and p = (1 + the number of
  permutations whose rho is at least the observed one) / 10,001.
  Calibration slope: the least-squares line of gain on predictor over the
  WP4 pilot's 25 regimes gives each new regime a predicted gain, and the
  observed gains are regressed on the predicted ones. Supported when
  rho >= 0.8, the Holm-adjusted p < 0.05 and the slope lies in [0.5, 1.5].
- **H3.** Plant A, `overall` configurations: for each drawn workload
  d = J(per_knob) - J(joint). Two one-sided paired t-tests against the margin
  +-0.02 x mean J(joint) over the paired workloads (the smallest difference
  of interest, 2% of J); p is the larger of the two. Supported when the
  Holm-adjusted p < 0.05. The 90% and 95% intervals of the mean of d are
  reported.
- **H4.** Plant B, `overall` configurations, the same d: a one-sided paired
  t-test that the mean of d is above zero. Supported when the Holm-adjusted
  p < 0.05. The 95% interval of the mean of d is reported.
- A test that cannot be computed (fewer than two complete pairs or regimes)
  is not supported; its p-value is reported as n/a beside the number of
  pairs or regimes it had.
- Equivalence is claimed only from H3's test; a non-significant difference is
  never called equivalence.

Reported without a test: every WP9 arm against `joint` per plant and
granularity (n, mean J(joint), mean difference with its 95% interval, cost
and violation differences); H2's table by regime; and whether every shard
ran the workloads the scorer draws from M.

## 7. Randomness and seeds

1. D = SHA-256 of this file's bytes as stored in commit C
   (`git show C:research/registration/PROTOCOL.md`; `python seeds.py digest
   PROTOCOL.md` in a fresh checkout of C gives the same).
2. T0 = the time commit C is pushed to the public repository, as GitHub
   records it: the creation time of the first CI run the push starts
   (`gh run list --commit C --json createdAt`). The randomness
   is drand quicknet's (chain
   `52db9ba70e0cc0f6eaf7803dd07447a1f5477735fd3f661792ba94600c84e971`) first
   round due at or after T0 + 24 h; its number follows from the chain's
   genesis time and period (`python seeds.py round --t0 T0`). The OSF
   registration records C, D, T0 and the round's number before that round is
   due.
3. Once the round is due it is fetched from at least two of api.drand.sh,
   api2.drand.sh, api3.drand.sh and drand.cloudflare.com. They must return the
   same round, randomness and signature, and the randomness must be the
   SHA-256 of the signature. If not, there is no draw: a person looks, and no
   relay is preferred over another. Asking before the round is due, or fewer
   than two relays answering, means the same round is fetched again later.
4. Master seed M = SHA-256(D || randomness), both as raw bytes. A run's seed
   is the first 8 bytes of SHA-256(M || the run's identity in UTF-8), read as
   an unsigned big-endian integer; the simulator takes it modulo 2^31 - 1.
5. No alternate round and no re-roll.

## 8. Execution

- Where: GitHub Actions, the workflow `confirmatory`
  (`.github/workflows/confirmatory.yml`), started on commit C by pushing
  a tag named after the round, `wp14-round-<N>`, at C (the branch can move
  on, a deviation log entry for one), the round's number its only input;
  ubuntu-24.04, Python 3.13, the
  dependencies pinned in `eval/requirements-reproduce.txt`.
- Steps (`research/registration/confirm.py`): draw (fetch and check the
  round, derive M, write `draw.json`); 16 shards, shard i running jobs i,
  i + 16, ... of the fixed list (`campaign.json`); one scoring with the
  frozen scorer; a seal (the SHA-256 of every output, published in the run's
  summary).
- Every step refuses a checkout with modified tracked files, a draw made for
  another protocol or commit, and a round due less than 24 h after C's
  commit time.
- The campaign runs once and is scored once.

## 9. Missing data, exclusions and stopping

- No run, workload, design point or regime is excluded for its values.
- A run that raises is recorded with its error and a missing J, and is not
  retried. Its pair (comparison part) or design point (dose part) is left out
  of the tests that need it, and the report gives the count. A failure caused
  by a defect in the code is reported as one; a re-run after a fix is
  exploratory.
- A shard lost to the CI service (a cancelled, timed-out or lost runner, as
  opposed to a run that raised) is re-run from C with the same draw. Every
  run's seed is fixed by its identity, so the re-run repeats the same
  computation; each one is recorded in the deviations log.
- Nothing is scored until the shards hold every job exactly once, each with
  the seed its identity gives; the scorer checks this first.
- Shards on different processors could draw the population with different
  last bits in a float. The scorer reports any difference, and every run is
  scored as its shard ran it.
- No interim look, no early stopping, no extension.

## 10. Deviations

A deviation is anything done after C that this text does not describe: a
change to a frozen file, another round, a re-run other than those in
section 9, an analysis not named here, or a result reported from anything
other than the scored run. Each is written down, dated, when it happens, in
`research/registration/DEVIATIONS.md`, and reported with the results. An
analysis not named here is labelled exploratory.

## 11. Frozen with this file in commit C

- `research/registration/`: `design.json` (`"draft": false`), `campaign.json`,
  `wp14_campaign.py`, `wp14_score.py`, `seeds.py` and `confirm.py`.
- `.github/workflows/confirmatory.yml`.
- WP9's selections: the two `tuned.json` files `design.json` names.
- WP4's pilot runs: `research/theory/data/dose_response_runs.csv.gz`.
- The population's windows: `research/population/data/windows_600s.csv.gz`
  (its SHA-256 is written into every shard's `population.json`).
- The plants, planners and harness as the repository holds them at C.
