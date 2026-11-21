# PROTOCOL: WP17, the main live runs of the second system (H8)

**Status:** DRAFT, not frozen. It describes what the built code does
(`testbed/hvpa/main_runs.py`, `main_score.py`, `main_design.json`). The
choices that are the author's are listed in `PROTOCOL_H8_NOTES.md` with a
recommendation each. Freezing: the commit that replaces this line (C8), with
`testbed/hvpa/main_design.json` marked `"draft": false`; the push of C8 is T0
(section 7); registered on OSF before the round named in section 7 is due.
Later changes only as dated notes after this text, each one also a deviation
(section 10).

## 1. Question

Does the account under test (plan v2, Part A3) carry to a second system,
unrelated to LLM serving, on a real cluster? Kubernetes horizontal with
vertical autoscaling: replicas through the scale subresource, CPU per pod
through in-place pod resize. Its cost is replicas x CPU per pod x price, a
product, so the interaction index is non-zero by construction (plan, WP17).
Two predictions (plan v2, Part D, H8): the index ranks the regimes by the
gain of joint control, and joint control beats the same-metric autoscaler
pair, the Horizontal Pod Autoscaler on CPU with the Vertical Pod Autoscaler
on CPU, which the VPA's documentation says should not be used together.

## 2. Data seen before registration

- **The twin's predictions** (`research/hvpa/predictions.json`, committed
  before any live run, 44342a4): nine simulated regimes (step width 1, 2 or
  4; demand volatility sigma 0.05, 0.2 or 0.4), the predicted gain of the
  joint arm over each comparator, the regimes' mean index, and the three
  live regimes chosen from them: (width 2, sigma 0.2), (4, 0.2) and
  (4, 0.05). Over the nine, the twin's Spearman rho between mean index and
  gain was +0.18.
- **The calibration** (`research/hvpa/calibration.json`): the service's CPU
  per request at 0.25, 0.5, 1 and 2 cores was not linear within 10 % (0.25
  core 13.4 % above the median), so the plant takes the measured curve, by
  the plan's rule, written before any prediction
  (`testbed/hvpa/DEVIATIONS.md`, 2026-11-16 09:39).
- **The live pilot** (`research/hvpa/PILOT_LIVE.md`, records in
  `testbed/hvpa/pilot_runs/`): 54 one-hour runs, six schedules per regime,
  none invalid. Gain = mean of J(comparator) - J(A4) over the comparator's
  mean J:

  | regime | twin's mean index | vs A3: pilot (twin) | vs A1: pilot (twin) |
  |---|---:|---:|---:|
  | w2 s0.2 | 0.0181 | +26.6 % (+13.4 %) | +25.8 % (+52.6 %) |
  | w4 s0.2 | 0.0227 | +20.3 % (+19.1 %) | -10.2 % (+52.0 %) |
  | w4 s0.05 | 0.0249 | +60.4 % (+28.0 %) | +47.0 % (+67.4 %) |

  On these, H8's rank part would read rho = +0.5 and its beat part would
  fail at w4 s0.2. The pilot sizes the main runs (section 4); its schedules
  and demand are not reused.

## 3. Hypotheses

| | prediction | data | supported when |
|---|---|---|---|
| H8a (rank) | the twin's mean index ranks the live regimes by the gain of joint over per-knob control | the three live regimes | Spearman's rho between the twin's mean index and the observed gain against A3 is at least 0.8; with three regimes rho is -1, -0.5, +0.5 or +1, so this holds exactly when both orders agree. A criterion, not a test: no permutation p-value can fall below 1/6. |
| H8b (beat) | joint control lowers J against HPA with VPA on CPU | each live regime | one-sided paired t-test of d = J(A1) - J(A4) > 0; Holm-adjusted p < 0.05 |

Also tested, with H8b in one Holm family (choice: notes row 1): joint
control lowers J against the per-knob planner in each regime, d = J(A3) -
J(A4) > 0. H8 holds when H8a holds and H8b is supported in every regime
(choice: notes row 2); each part is reported on its own.

## 4. Design

- **Testbed:** the WP10 kind cluster (node image `kindest/node:v1.36.1`),
  WP17's components as `testbed/hvpa/install.py` installs them from
  `components.lock` and `images.lock` (VPA 1.8.1, metrics-server 0.9.1,
  KEDA 2.21.0, k6 2.1.0), the service `cpuburn` (fixed CPU per request; CPU
  resize does not restart the container), on one host on mains power.
- **Arms:** A4, the joint planner over (replicas, CPU per pod); A3, the
  per-knob planner over the same lattice and objective; A1, the
  autoscaling/v2 HPA on CPU utilisation with a VPA on CPU (update mode
  InPlaceOrRecreate, 250m to 2 cores, the recommender's default flags), its
  target the one the twin tuned for the regime's volatility (0.2 at sigma
  0.2, 0.1 at sigma 0.05). Replicas 1 to 8. A2 (HPA on request rate through
  KEDA, with the VPA) is not run, as in the pilot (notes row 6).
- **Regimes:** the three live regimes of section 2.
- **Schedules:** schedule k of a regime has the demand shape k mod 6 (step
  up, step down, ramp, burst train, Azure app 1, Azure app 2) at the regime's
  volatility, its demand seeded from the draw (section 7). Each schedule is
  run once by each arm, back to back, in the order `pilot.order(k)`: the six
  orders of the three arms in turn, so over six schedules every arm takes
  every place twice (notes row 3). The joint arm's run serves both
  contrasts.
- **Size:** per regime, the largest pair count its two contrasts need by
  WP12's formula at alpha 0.05 one-sided and power 0.9,
  n = ((1.645 + 1.282) sigma_d / Delta)^2, Delta = the twin's predicted gain
  x the comparator's pilot mean J, sigma_d the pilot's, rounded up to a
  multiple of six: 66 schedules at w2 s0.2, 36 at w4 s0.2, 42 at w4 s0.05.
  That is 144 schedules, 432 one-hour runs, about 433 hours (notes row 4).
- **One run** (`testbed/hvpa/live.py`): reset (the arm's objects and the
  VPA's checkpoints deleted, the recommender restarted, the service back to
  2 pods x 0.5 core, every pod ready); the arm applied; a k6 Job in the
  cluster replays the schedule (1 core); the service's pods are polled every
  10 s; then k6's summary and the planner's decision log.

## 5. Metrics

- **J** = cost + 1e-4 x late requests (`pilot.j_live`): cost = the requested
  CPU of the scheduled pods, in core-hours, x 0.1315 USD per core-hour
  (0.526 / 4); late requests = requests x (1 - attainment), with k6's
  dropped iterations counted as late.
- Reported per arm and regime, not tested: cost, attainment, resizes,
  direction reversals of the replica count, the planners' logged index and
  one-step loss.

## 6. Tests

- Per regime and comparator B (A3, A1), on every schedule with a valid run
  of both arms: d_k = J(B) - J(A4). One-sided paired t-test that the mean of
  d is above zero (`wp14_score.superiority`); its 95 % interval reported.
- Holm's correction over the six contrasts (three regimes x two
  comparators), alpha = 0.05 (notes row 1).
- H8a: Spearman's rho on average ranks between `mean_index` of the three
  live regimes in `predictions.json` and the regimes' gain against A3 (the
  mean of d over A3's mean J on the same schedules).
- Scored by `testbed/hvpa/main_score.py`, which refuses a design still
  marked draft and a plan made from other twin predictions.

## 7. Randomness and seeds

1. D8 = SHA-256 of this file's bytes as stored in commit C8.
2. T0 = the time C8 is pushed to the public repository, as GitHub records
   it: the creation time of C8's first CI run (`gh run list --commit C8
   --json createdAt`). The randomness is drand quicknet's (chain
   `52db9ba70e0cc0f6eaf7803dd07447a1f5477735fd3f661792ba94600c84e971`) first
   round due at or after T0 + 24 h; its number follows from the chain's
   genesis time and period. `python research/registration/facts.py --tag
   wp17-c --protocol research/registration/PROTOCOL_H8.md --design
   testbed/hvpa/main_design.json` prints C8, D8, T0 and the round. The OSF
   registration records them before the round is due.
3. Once the round is due it is fetched from at least two of api.drand.sh,
   api2.drand.sh, api3.drand.sh and drand.cloudflare.com, with `python
   research/registration/seeds.py draw --protocol
   research/registration/PROTOCOL_H8.md --round N --t0 T0 --out
   research/registration/draw_h8.json`. They must return the same round,
   randomness and signature, and the randomness must be the SHA-256 of the
   signature; if not, there is no draw and a person looks. The draw refuses
   any round but the one T0 names.
4. Master seed M = SHA-256(D8 || randomness), both as raw bytes. Schedule k
   of the regime (width, sigma) has the seed formed from the first 8 bytes of
   SHA-256(M || "wp17|w<width>|s<sigma>|k<k>" in UTF-8), read as an unsigned
   big-endian integer (`seeds.run_seed`); its demand is drawn as the twin
   draws it (`live.schedule`).
5. No alternate round and no re-roll.

## 8. Execution

- `python testbed/hvpa/main_runs.py plan --pilot testbed/hvpa/pilot_runs
  --draw research/registration/draw_h8.json --out testbed/hvpa/main_plan.json`
  writes the plan once; the plan and the draw are committed before any main
  run.
- `python testbed/hvpa/main_runs.py run --plan testbed/hvpa/main_plan.json
  --out testbed/out/hvpa/main`, detached from the session that starts it
  (`tools/detach.py`), the host kept awake and on mains. It refuses a plan,
  calibration or twin predictions not committed as they are on disk, refuses
  a plan whose run orders are not the three arms once each, and resumes from
  its records (a run whose record exists is not repeated).
- After the last run: the records (without anything else) kept in
  `testbed/hvpa/main_runs/` and committed, then `python
  testbed/hvpa/main_score.py --plan testbed/hvpa/main_plan.json --runs
  testbed/hvpa/main_runs --out testbed/hvpa/main_score`, once.

## 9. Missing data, exclusions and stopping

- A run is not a measurement, and is repeated up to three attempts, when it
  raised, its k6 Job did not succeed, k6 left no summary, two polls of the
  service's pods were more than 30 s apart (the host stalled), or the host
  was on battery at its start or end (`pilot.problems`). Every invalid
  attempt is kept beside the record.
- After three invalid attempts the arm's run is missing for that schedule;
  a missing A4 run ends the schedule (neither contrast could use it). A
  schedule without both runs of a contrast is left out of that contrast and
  the report gives the count. No run, schedule or regime is excluded for its
  values.
- No interim look, no early stopping, no extension.

## 10. Deviations

A deviation is anything done after C8 that this text does not describe: a
change to a frozen file, another round, a re-run other than those of section
9, an analysis not named here, or a result reported from anything other than
the scored runs. Each is written down, dated, when it happens, in
`testbed/hvpa/DEVIATIONS.md`, and reported with the results. An analysis not
named here is labelled exploratory.

## 11. Frozen with this file in commit C8

- `testbed/hvpa/`: `main_design.json` (`"draft": false`), `main_runs.py`,
  `main_score.py`, `pilot.py`, `live.py`, `run_pilot.py`, `hvpa_arms.py`, `install.py`,
  `components.lock`, `images.lock`, and the service, planner, load and
  manifests in `cpuburn/`, `planner/`, `load/` and `k8s/`.
- `research/hvpa/`: `predictions.json`, `calibration.json`, `lattice.py`,
  `service_plant.py`, `workloads.py`, `vpa.py`.
- `research/registration/seeds.py` and `wp14_score.py` (the tests).
- The pilot's records, `testbed/hvpa/pilot_runs/` (the sizes).
