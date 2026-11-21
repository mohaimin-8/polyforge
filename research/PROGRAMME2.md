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
| WP5 Morris screen of plant 2 | `research/sensitivity/` | `python morris_pilot.py` (`--crn` for common random numbers) | `MORRIS_PILOT.md` (10 trajectories); `MORRIS_r20_crn.md` (the plan's screen: 20 trajectories, common random numbers) |
| WP5 Sobol indices of the gain | `research/sensitivity/` | `python sobol.py` (after a Morris screen) | `SOBOL.md` (the r20 screen's six leading factors, N = 128) |
| WP5 specification curve | `research/speccurve/` | `python pilot_curve.py`; the plan's curve: `python full_curve.py run`, then `curve` | `SPEC_CURVE_PILOT.md`; `FULL_SPEC_CURVE.md`, `full_spec_curve.png` (every arm re-selected per specification) |
| WP6 cache replay of real prompts (Go-verified) | `research/cachereplay/` | `python run_all.py --preset full --workers 6` | `out/full/` |
| WP7 tier price and quality (RouterBench, mode A; own ARC measurement, mode B) | `research/tierquality/` | `python tiers.py`, `python selfhosted.py run` | `TIERS.md`, `SELFHOSTED.md` |
| WP8 workload population, native replay | `research/population/` | `python features.py`, `python native.py`, `python population.py` | `FEATURES.md`, `POPULATION.md` |
| WP9 comparators and tuning | `research/comparators/` | `python tune.py --plant A\|B --mix own\|native --budget enforced\|plant --references` | `full/*/report.md` (six campaigns), `pilot/*/report.md`, `SAMPLE_SIZE.md` |
| WP10 testbed | `testbed/` | runbook in `testbed/README.md`; `python scripts/bringup.py` | built and up on kind (2026-11-14) |
| WP11 simulator against the testbed | `testbed/validation/` | `cell_schedules.py`, `predict.py`, `run_cells.py`, `score.py`, `diagnose.py`, `coupling.py` | `predictions.json` (committed before the runs), `VALIDATION.md`, `DIAGNOSE.md`, `COUPLING.md` (exploratory: the two tiers share one host); WP11b by the rule in `testbed/hvpa/after_wp11.py` |
| WP12 live pilot of the main contrast | `testbed/wp12/` | `tb_planner.py`, `plans.py`, `pairs.py`, `after_wp11b.py` | `regimes.json`, `design.json`, `tuning/`, `plans.json` (all committed before any live run), then `PILOT.md` with gate G3 and `pilot.json`; started by `testbed/hvpa/after_wp11.py` after WP11b |
| WP13 verifiable seeds (drand quicknet round at or after T0 + 24 h, two agreeing relays, no re-roll) | `research/registration/` | `python seeds.py digest`, `round --t0 ...`, `draw --protocol ... --round N --t0 T0 --out ...` (refuses a round T0 does not name), `run-seed`; `python facts.py --tag wp14-c` (what the registration records: C, D, T0, the round) | `draw.json`, written once after the named round |
| WP13 step 5 the confirmatory campaign in CI, the round its only input | `research/registration/`, `.github/workflows/confirmatory.yml` | `python confirm.py draw\|shards\|run\|score\|seal` (run by the workflow, started by pushing the tag `wp14-round-<N>` at C); what runs: `wp14_campaign.py` (H1-H4: new WP4 regimes, WP9's selected arms on freshly drawn workloads), scored by `wp14_score.py`, both as `design.json` fixes them (marked draft until the protocol fixes every value; a draft runs only as `--rehearsal`) | `campaign.json` (the manifest; it names `PROTOCOL.md`, which the protocol supplies); the workflow's `confirmatory` artifact (every shard's `rows.csv` and `population.json`, then `score.json` and `SCORE.md`) and `SHA256SUMS` in the run summary |
| WP15 external check (RouteBalance) | `research/external/routebalance/` | `python -I analyse.py report` | `REPORT.md`, `RESULTS.md` |
| WP16 benchmark: any controller scored by regret | `research/benchmark/` | `python bench.py --controller module:Class --plant B` (`--controller file.py:Class` for a controller in a file of its own) | per-workload CSV (J, the references, regret, and the shares of steps that left the caps, the move limits or the budget: plant 2 enforces none), summary JSON (means with bootstrap intervals, and WP9's normalised regret against LB2 and LB0) |
| WP17 horizontal with vertical autoscaling | `research/hvpa/`, `testbed/hvpa/` | `python twin_pilot.py`; cluster: `install.py`, `calibrate_service.py`, `twin_predict.py`; live: `run_pilot.py` (the pilot, 54 one-hour runs), then `main_runs.py plan|run` (the main runs: the pilot's design at the size it asks for, the demand seeded by the registered draw; waits for the registration) | `VPA_MODEL.md`, `TWIN_PILOT.md`, `calibration.json`, `predictions.json` (committed before any live run); `PILOT_LIVE.md` (the pilot's 54 runs, records in `testbed/hvpa/pilot_runs/`); `python main_runs.py sizes --pilot pilot_runs` prints the main runs' sizes |
| WP22 controls, adversarial search, blind predictions | `research/controls/` | `python negative_control.py`, `adversarial_search.py`, `blind_predictions.py` | `NEGATIVE_CONTROL.md`, `ADVERSARIAL_SEARCH.md`, `DIAGNOSE_158.md`, `RANDOM_SAMPLE_CHECK.md`, `GATE_REACH.md` |
| WP23 the index as a tool | `tools/index-probe/` | `python index_probe.py log|box ...` | `README.md` |

Reproduce every report built from committed data and check it against the committed text: `pip install -r research/requirements-campaigns.txt` (Python 3.13, as GitHub's Linux runs of it), then `python scripts/reproduce_programme2.py` (37 steps; WP9's eight campaign reports, the WP1, WP3 and WP4 pilot reports and the WP17 live pilot's report are rebuilt from their committed records among them; those needing downloaded inputs or not-yet-committed run data are skipped without them). Not rebuilt: reports only a re-run makes (WP22's adversarial search, its diagnosis of regime 158 and the positive control, a 10 s run) and `research/population/FEATURES.md` (the local traces).

The deposit (WP16: one DOI with code, protocols, run data and the audit scripts):
`python scripts/bundle_programme2.py --audit ../PolyForge_Audit_2026-11-11_scripts` (`--confirmatory <dir>` adds
WP14's workflow artifact) writes the zip of one commit's tracked tree and the audit scripts, its `MANIFEST.sha256`
and `deposit.json` to `research/out/zenodo/` (or `--out <dir>`); a folder holds one deposit, so the bundler refuses one that
holds another commit's zip, and never replaces a built one. Publishing it on Zenodo is the author's step.

The CODECHECK manifest (WP20, level 1): `codecheck.yml` at the repository's root lists the 49 files the
reproduction rebuilds, each with its step and command; `python scripts/codecheck_manifest.py` rewrites the
manifest from the steps (`--check` says whether it is current) and keeps the author's `paper` fields.

The long CPU campaigns (WP9's full protocol, WP5's Morris and Sobol stages) run one after another once the
testbed is free, resumable: `python research/run_queue.py --list`. From 2026-11-17 they can also run on
GitHub Actions, split into shards (`research/offload.py`, `.github/workflows/campaign.yml`, started by
pushing a branch `offload/<campaign>`), while the laptop keeps the live campaigns
(`run_queue.py --only ...`); `python research/offload.py collect` commits the outputs as the queue does,
with the CI run named.

Tests: `python -m pytest research/theory research/control2 research/plant2 research/population
research/speccurve research/comparators research/controls research/cachereplay research/hvpa research/benchmark research/sensitivity tools/index-probe research/registration research/test_run_queue.py research/test_offload.py scripts/test_reproduce_programme2.py scripts/test_bundle_programme2.py scripts/test_codecheck_manifest.py -q`
(CI runs the same line), and `python -m pytest testbed -q` in a process of its own (319 tests; CI runs it
too since 2026-11-20).

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
- WP17's live pilot (54 one-hour runs, none invalid; six schedules per regime): joint control's J was
  below per-knob control's in all three regimes, by 20 % to 60 % of per-knob's J (the twin predicted 13 %
  to 28 %), and below the HPA-plus-VPA pair's in two of three (26 % and 47 %; 10 % above it at width 4,
  sigma 0.2; the twin predicted 52 % to 67 %). Six pairs only size the main runs: every contrast at 90 %
  power needs 66, 36 and 42 schedules, 433 laptop-hours in all, by WP12's formula per contrast (72, 42
  and 48, 488 hours, if the drafted scorer's Holm over the six contrasts stays: `main_runs.py sizes`); not run.

What the full campaigns say (2026-11-17; still exploratory):

- WP9, six campaigns (plants A and B; own and native mixes; enforced and plant-only budgets): the
  joint, per-knob and one-knob planners tie in five of six; with only the plant's budget on plant B the
  joint planner beats per-knob by 0.0011 J [0.0002, 0.0023]; layers and rules lose on average. In plant A
  no tenant reaches its budget, so its two budget modes give identical runs.
- WP5 specification curve, 4,608 specifications: median effect 0; the joint planner is better in 37%
  of them and worse in 25%; whole-curve sign-flip test p = 0.32.
- WP5 Morris (r = 20) and Sobol: replica capacity drives the gain (first-order 0.72, total 0.75);
  congestion, the cache ceiling and the tail slope act mostly through interactions.

Status at the deadline (2026-11-20 14:00), one line per work package of the plan:

| WP | state | where it stands |
|---|---|---|
| WP0 | partly | tag `programme-1-final`; `research/plant2/`, `control2/`, `theory/` (programme 2's campaigns live under `research/`, not `eval/campaign2/`); the chart installs the corrected planner and counts demand by default (31f93f0); publishing programme 1's bundle, the index and route decisions and gate G0's literature table are the author's (no record of them here) |
| WP1 | done | `HEADROOM_PILOT.md` |
| WP2 | done | `FITS.md`, `CONSTANTS.md` |
| WP3, WP18 | done | Theorems 1 and 2 hold on every check, T4 false as planned; the proofs are drafts until a second person reads them |
| WP4 | done | `DOSE_RESPONSE_PILOT.md` |
| WP5 | done | Morris r = 20 with common random numbers, Sobol N = 128, the specification curve of 4,608 specifications |
| WP6 | done | `out/full/` |
| WP7 | done | `TIERS.md`, `SELFHOSTED.md` |
| WP8 | done | `FEATURES.md`, `POPULATION.md` |
| WP9 | done | six full campaigns |
| WP10 | done | the testbed, built and up on kind (2026-11-14) |
| WP11 | done, target missed | attainment error 65 % (WP11) and 29.8 % (WP11b) against 25 %; rank correlation 0.89 met in WP11b |
| WP12 | done, gate G3 not met | `PILOT.md` |
| WP13 | tooling done, protocol drafted | seeds and the CI campaign built, tested and rehearsed; `PROTOCOL.md` drafted on 2026-11-20 for the author's review (`PROTOCOL_NOTES.md` lists the choices left to the author and the steps to freeze it); freezing and registering it are the author's |
| WP14 | built, not run | 2,360 + c runs in 16 shards (c: drawn workloads of class `conversation`; 2,420 at c = 60), rehearsed on GitHub; waits for the protocol, then 24 h for its drand round |
| WP15 | done | `REPORT.md`, `RESULTS.md` |
| WP16 | done but the DOI | the benchmark harness, one-command reproduction (37 steps; on 2026-11-20 each of the 31 it then had rebuilt its report identically on the laptop, line ends aside, none skipped, and on 2026-11-21 so did the 6 added (WP1, WP3, WP4 pilot reports, WP9's campaign reports, WP22's negative control); on Linux with `research/requirements-campaigns.txt`, from a clone and from the deposit's archive committed once, 26 reproduce, 7 of them in the last digits within 1e-6, and the 5 needing unshipped inputs skip), `DATA_LICENCES.md`, the deposit builder; publishing is the author's |
| WP17 | pilot done | 54 live runs, none invalid (`PILOT_LIVE.md`); the main runs need 433 laptop-hours at 90 % power and the registered draw: not run; their scorer is drafted (`testbed/hvpa/main_score.py`, `main_design.json` marked draft; on the pilot's records it gives `PILOT_LIVE.md`'s numbers) |
| WP19 | not started | needs GPU access |
| WP20 | both levels prepared | level 1: `codecheck.yml`, the manifest a codechecker works from (its `paper` fields are the author's); level 2: `testbed/REPLICATION.md`, drafted for site B (WP11b's cells and WP12's pilot, expected values, tolerances proposed from our bootstrap and prediction intervals: the author declares them); requesting the check after the DOI and finding site B are the author's |
| WP21 | the author's decision | |
| WP22 | done | `NEGATIVE_CONTROL.md`, `ADVERSARIAL_SEARCH.md`, blind predictions, `RANDOM_SAMPLE_CHECK.md`, `GATE_REACH.md` |
| WP23 | done | `tools/index-probe/` |
| WP24 | not started | the preprint and outside readers are the author's |

What is left is the author's, in this order (each step names the file that prepares it):

1. The protocol: decide the seven choices in `research/registration/PROTOCOL_NOTES.md`, edit
   `PROTOCOL.md`, set `design.json` to `"draft": false`, commit, tag and push (T0), register on OSF
   before the round 24 h later (`research/registration/facts.py --tag wp14-c` prints what it records); then pushing the tag `wp14-round-<N>` at C once the round is due starts the `confirmatory` workflow, which scores WP14 in
   about 15 minutes.
2. The deposit: after the push, rebuild it from the pushed commit into a new folder
   (`python scripts/bundle_programme2.py --audit ../PolyForge_Audit_2026-11-11_scripts --out research/out/zenodo-<commit>`:
   `research/out/zenodo/` holds the zip built from e172c25, whose reproduction predates its fix of 2026-11-20,
   which read a run outside a git checkout as identical; the bundler refuses a folder holding another
   deposit), then publish that folder's zip on Zenodo (its `deposit.json` holds the metadata); after WP14, a
   new version with `--confirmatory`, into a folder of its own again.
3. WP20: fill `codecheck.yml`'s `paper` fields and request a CODECHECK; declare the tolerances in
   `testbed/REPLICATION.md` and send it to a site B. Part A's proposal is our own 95% interval, which a
   site whose true values equal ours misses about one time in six per metric;
   `testbed/validation/replication_tolerances.py` also prints a second estimate's interval (error
   [10.2%, 88.8%], rank correlation [+0.631, +0.975]), as Part B's prediction interval is for a second mean.
4. WP21 and WP0's decisions with the supervisor (the route, which index defines Q1); WP0's reading
   table (plan B1 and B5, read in full).
5. WP17's main runs, if wanted: register H8 (scorer drafted in `testbed/hvpa/main_score.py`), draw its
   round with `seeds.py draw --t0`, then about 433 laptop-hours (488 if the drafted scorer's Holm over the six
   contrasts stays: H8's registration decides, `research/registration/PROTOCOL_NOTES.md` row 5). WP19 needs GPU access; WP24 the preprint and outside readers.
6. Merging `research-v2` into `main`, and the writing.
