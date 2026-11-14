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
| WP5 Morris screen of plant 2 | `research/sensitivity/` | `python morris_pilot.py` | `MORRIS_PILOT.md` |
| WP5 specification curve | `research/speccurve/` | `python pilot_curve.py` | `SPEC_CURVE_PILOT.md`, `spec_curve_pilot.png` |
| WP6 cache replay of real prompts (Go-verified) | `research/cachereplay/` | `python run_all.py --preset full --workers 6` | `out/full/` |
| WP7 tier price and quality (RouterBench, mode A) | `research/tierquality/` | `python tiers.py` | `TIERS.md` |
| WP8 workload population, native replay | `research/population/` | `python features.py`, `python native.py`, `python population.py` | `FEATURES.md`, `POPULATION.md` |
| WP9 comparators and tuning | `research/comparators/` | `python tune.py --plant B ...` | `pilot/*/report.md`, `SAMPLE_SIZE.md` |
| WP10 testbed | `testbed/` | runbook in `testbed/README.md`; `python scripts/bringup.py` | built and up on kind (2026-11-14) |
| WP11 simulator against the testbed | `testbed/validation/` | `cell_schedules.py`, `predict.py`, `run_cells.py`, `score.py` | `VALIDATION.md` once the cells have run |
| WP15 external check (RouteBalance) | `research/external/routebalance/` | `python -I analyse.py report` | `REPORT.md`, `RESULTS.md` |
| WP17 horizontal with vertical autoscaling (twin) | `research/hvpa/` | `python twin_pilot.py` | `VPA_MODEL.md`, `TWIN_PILOT.md` (a-priori plant) |
| WP22 controls, adversarial search, blind predictions | `research/controls/` | `python negative_control.py`, `adversarial_search.py`, `blind_predictions.py` | `NEGATIVE_CONTROL.md`, `ADVERSARIAL_SEARCH.md`, `DIAGNOSE_158.md`, `RANDOM_SAMPLE_CHECK.md`, `GATE_REACH.md` |
| WP23 the index as a tool | `tools/index-probe/` | `python index_probe.py log|box ...` | `README.md` |

Reproduce every report from committed data and check it against the committed text: `python scripts/reproduce_programme2.py` (11 steps; two need downloaded inputs and are skipped without them).

Tests: `python -m pytest research/theory research/control2 research/plant2 research/population
research/speccurve research/comparators research/controls research/cachereplay research/hvpa tools/index-probe -q`
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
- The semantic cache, replayed on 1,000,000 real first turns through the gateway's own logic,
  saturates at about 300 entries (1.19 MiB of heap) with a ceiling of 0.21 at tau 0.85 (WP6); plant 2
  now uses that curve, which makes the cache knob close to on/off at the simulator's sizes.
