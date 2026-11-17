# Programme 2: where everything is

Programme 1 ends at tag `programme-1-final`. Programme 2 follows the plan
`PolyForge_Q1_Research_Plan_v2_detailed_2026-11-12.md` (outside the repository). Everything below is
**exploratory** until a registration says otherwise; proofs are drafts until a second person reads them.

| work package | directory | run | result |
|---|---|---|---|
| WP1 reference points (clairvoyant bounds, best static) | `research/theory/` | `python headroom_pilot.py` | `HEADROOM_PILOT.md` |
| WP2 plant 2 (measured constants, modes A and B) | `research/plant2/` | `python fits.py`, `python plant.py`, `python measure_load_curve.py` | `FITS.md`, `CONSTANTS.md` |
| WP3, WP18 index theory and property checks | `research/theory/` | `python property_check.py ...`, `python t4_check.py ...` | `INDEX_THEORY.md`, `PROPERTY_CHECKS.md` |
| WP3 index on the planner's decisions | `research/theory/` | `python index_pilot.py` | `INDEX_PILOT.md` |
| WP4 dose-response (step width x coupling) | `research/theory/`, `research/control2/` | `python dose_response_pilot.py` | `DOSE_RESPONSE_PILOT.md` |
| WP5 Morris screen of plant 2 | `research/sensitivity/` | `python morris_pilot.py` (`--crn` for common random numbers) | `MORRIS_PILOT.md` (10 trajectories); `MORRIS_r20_crn.md` (the plan's screen) once run |
| WP5 Sobol indices of the gain | `research/sensitivity/` | `python sobol.py` (after a Morris screen) | `SOBOL.md` once run |
| WP5 specification curve | `research/speccurve/` | `python pilot_curve.py` | `SPEC_CURVE_PILOT.md`, `spec_curve_pilot.png` |
| WP6 cache replay of real prompts (Go-verified) | `research/cachereplay/` | `python run_all.py --preset full --workers 6` | `out/full/` |
| WP7 tier price and quality (RouterBench, mode A; own ARC measurement, mode B) | `research/tierquality/` | `python tiers.py`, `python selfhosted.py run` | `TIERS.md`, `SELFHOSTED.md` |
| WP8 workload population, native replay | `research/population/` | `python features.py`, `python native.py`, `python population.py` | `FEATURES.md`, `POPULATION.md` |
| WP9 comparators and tuning | `research/comparators/` | `python tune.py --plant B ...` | `pilot/*/report.md`, `SAMPLE_SIZE.md` |
| WP10 testbed | `testbed/` | runbook in `testbed/README.md`; `python scripts/bringup.py` | built and up on kind (2026-11-14) |
| WP11 simulator against the testbed | `testbed/validation/` | `cell_schedules.py`, `predict.py`, `run_cells.py`, `score.py`, `diagnose.py`, `coupling.py` | `predictions.json` (committed before the runs), `VALIDATION.md`, `DIAGNOSE.md`, `COUPLING.md` (exploratory: the two tiers share one host); WP11b by the rule in `testbed/hvpa/after_wp11.py` |
| WP12 live pilot of the main contrast | `testbed/wp12/` | `tb_planner.py`, `plans.py`, `pairs.py`, `after_wp11b.py` | `regimes.json`, `design.json`, `tuning/`, `plans.json` (all committed before any live run), then `PILOT.md` with gate G3 and `pilot.json`; started by `testbed/hvpa/after_wp11.py` after WP11b |
| WP13 verifiable seeds (drand quicknet round at or after T0 + 24 h, two agreeing relays, no re-roll) | `research/registration/` | `python seeds.py digest`, `round --t0 ...`, `draw --protocol ... --round N --out ...`, `run-seed` | `draw.json`, written once after the named round |
| WP13 step 5 the confirmatory campaign in CI, the round its only input | `research/registration/`, `.github/workflows/confirmatory.yml` | `python confirm.py draw\|shards\|run\|score\|seal` (run by the workflow, started by hand on commit C) | needs `campaign.json` (what runs and how it is scored, frozen with the protocol); the workflow's `confirmatory` artifact and `SHA256SUMS` in the run summary |
| WP15 external check (RouteBalance) | `research/external/routebalance/` | `python -I analyse.py report` | `REPORT.md`, `RESULTS.md` |
| WP16 benchmark: any controller scored by regret | `research/benchmark/` | `python bench.py --controller module:Class --plant B` | per-workload CSV, summary JSON |
| WP17 horizontal with vertical autoscaling | `research/hvpa/`, `testbed/hvpa/` | `python twin_pilot.py`; cluster: `install.py`, `calibrate_service.py`, `twin_predict.py`, `pilot.py` | `VPA_MODEL.md`, `TWIN_PILOT.md` (a-priori plant); calibration and predictions once measured |
| WP22 controls, adversarial search, blind predictions | `research/controls/` | `python negative_control.py`, `adversarial_search.py`, `blind_predictions.py` | `NEGATIVE_CONTROL.md`, `ADVERSARIAL_SEARCH.md`, `DIAGNOSE_158.md`, `RANDOM_SAMPLE_CHECK.md`, `GATE_REACH.md` |
| WP23 the index as a tool | `tools/index-probe/` | `python index_probe.py log|box ...` | `README.md` |

Reproduce every report from committed data and check it against the committed text: `python scripts/reproduce_programme2.py` (24 steps; those needing downloaded inputs or not-yet-committed run data are skipped without them).

The long CPU campaigns (WP9's full protocol, WP5's Morris and Sobol stages) run one after another once the
testbed is free, resumable: `python research/run_queue.py --list`. From 2026-11-17 they can also run on
GitHub Actions, split into shards (`research/offload.py`, `.github/workflows/campaign.yml`, started by
pushing a branch `offload/<campaign>`), while the laptop keeps the live campaigns
(`run_queue.py --only ...`); `python research/offload.py collect` commits the outputs as the queue does,
with the CI run named.

Tests: `python -m pytest research/theory research/control2 research/plant2 research/population
research/speccurve research/comparators research/controls research/cachereplay research/hvpa research/benchmark research/sensitivity tools/index-probe -q`
(CI runs the same line), plus `testbed/` (`python -m pytest -q` inside it).

What the pilots say so far, in one line each (details in the files above):

- Coordination pays only when step width and coupling are both large (WP4); at Kubernetes-like steps
  the measured plant leaves little to coordinate whatever its constants (WP5 Morris), and tuned
  per-layer control matches joint control within about 2% of J (WP9 pilot).
- The verdict on joint against per-layer control is set by evaluation choices (WP5 curve).
- Theorems 1 and 2 hold on every check; the run-level conjecture T4 is false as planned (WP18).
- The index predicts the gain only weakly; an adversarially found regime with a small index and a
  large gain draws most of its gain from decisions that start infeasible, outside Theorem 1 (WP22).
  Blind, on 40 fresh regimes, a rule fixed beforehand (one-step loss and index) ranks the gains at
  Spearman +0.39 (one-sided p = 0.006).
- On RouteBalance's decision the index is large and the per-knob loss 4-30 times ours (WP15).
- The simulator misses the testbed's attainment (WP11: median error 65% against the 25% target,
  optimistic in most cells): capacity grows sub-linearly with pods, and on the one-host testbed each
  busy core of the other tier slows a request by about 23% (COUPLING.md, exploratory).
- The semantic cache, replayed on 1,000,000 real first turns through the gateway's own logic,
  saturates at about 300 entries (1.19 MiB of heap) with a ceiling of 0.21 at tau 0.85 (WP6); plant 2
  now uses that curve, which makes the cache knob close to on/off at the simulator's sizes.
