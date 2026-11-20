# REPLICATION: two of programme 2's live results, re-measured on your hardware (site B)

**Status:** draft of 2026-11-20 for the author (plan v2, WP20 level 2). The
tolerances in section 2 are proposals until the author declares them, before
this file is sent; the author then replaces this line with the date of that
declaration and the commit it refers to.

## 1. What this asks

Programme 2 measured two things on one laptop: how well a simulator predicts
a small Kubernetes LLM-serving testbed (WP11b), and how a joint and a
per-knob replica planner compare on that testbed (WP12). Please repeat both on
your machine with the code at the commit of the deposit's DOI, and send the
raw results back. Your numbers are reported as "site B", whatever they show.

- **Part A** (about 18 hours of cell runs, after the calibration): calibrate the testbed on your
  machine, have the simulator predict WP11b's 20 cells from your calibration,
  send the predictions before any cell runs, then run each cell three times
  and score them.
- **Part B** (about 6 hours of live runs, after the planners' tuning on the
  CPU): WP12's live pilot on your calibration: the planners' plans for two
  load regimes, then eight ABBA pairs per regime. The low regime's result is
  the one compared; the high regime is reported too.
- **Before both** (once, about 7 hours): the prompt data and their vectors
  (section 3).

## 2. Expected values and the tolerances declared in advance

**Part A.** Ours (`testbed/validation/VALIDATION_V2.md`, 20 cells, three valid
runs each): attainment's median absolute percentage error 29.8% and Spearman
rank correlation +0.89 between predicted and measured attainment. Your result
reproduces ours when both of yours fall inside our 95% bootstrap intervals over
cells (10,000 resamples of the 20 cells, seed 20261120): error in
[15.9%, 71.5%] and rank correlation in [+0.705, +0.949] *(proposed)*. The plan's
targets for the simulator (error at most 25%, rank correlation at least 0.8)
are reported for your run as they were for ours.

**Part B.** Ours (`testbed/wp12/PILOT.md`, low regime, 8 pairs): J(per-knob) -
J(joint) = +0.0036 with sigma_d 0.0052, on a joint mean of 0.0806, that is
+4.51% of the joint mean. J's level depends on the hardware, so the comparison
is relative: your mean difference over your joint mean reproduces ours when it
falls inside [-2.37%, +11.38%] *(proposed)*, our 95% prediction interval for a
second mean of 8 pairs (t with 14 degrees of freedom, our sigma_d).

## 3. What you need

- An x86-64 machine you can leave alone for about a day, on mains power,
  with nothing else running during the runs. Ours: Intel Core i5-1135G7
  (4 cores, 8 threads), 15.7 GB RAM, Windows 11 with Docker Desktop on WSL2.
  The runs record the power line on Windows only; elsewhere they record
  "unknown", so keep the machine on mains and say so.
- About 20 GB of free disk (images, model files, results).
- Docker, kind, kubectl, k6, git and Python 3.13. Ours: Docker 29.6.2,
  kind v0.32.0, kubectl v1.36.1, k6 v2.1.0, Python 3.13.5. Images and model
  files are pinned by digest (`testbed/images.lock`, `testbed/k8s/base/models.lock`).
- The prompts, which the repository does not ship (`research/DATA_LICENCES.md`):
  LMSYS-Chat-1M is gated, so accept its licence agreement on Hugging Face
  (`lmsys/lmsys-chat-1m`) and download its parquet files into
  `research/traces/data/lmsys-chat-1m/data/`. Its licence forbids passing
  the text on: never send us prompt text or vectors. The vectors are built
  with torch and sentence-transformers (ours: torch 2.6.0+cpu,
  sentence-transformers 5.6.0, transformers 5.13.1) and the parquet files
  are read with pyarrow (ours: 25.0.0); the pinned requirements include none
  of them (step 1 installs them).

## 4. Steps

Each step starts at the repository's root, and paths in the text are relative to it.

1. Clone the repository and check out the deposit's commit (`git clone <the repository's URL>`,
   then `git checkout <the commit>`): steps 6 and 8 commit, and `run_cells.py` checks that the
   predictions are committed, so an unpacked deposit needs `git init -q && git add -A && git -c
   user.name=site-b -c user.email=site-b@invalid commit -qm deposit` first. Git refuses to commit
   without a name and an e-mail, and step 8 commits on its own after hours of tuning: if
   `git config user.name` prints nothing in the clone, set both there first (`git config user.name
   site-b` and `git config user.email site-b@invalid`). Install the Python packages:
   `python -m pip install -r eval/requirements-reproduce.txt pyarrow==25.0.0` (pyarrow reads the
   parquet files in steps 3, 5 and 6), and for step 3's vectors `python -m pip install torch==2.6.0`
   (a CPU build is enough; on Linux add `--index-url https://download.pytorch.org/whl/cpu`), then
   `python -m pip install sentence-transformers==5.6.0 transformers==5.13.1`. On Windows, clone
   or unpack it into a short folder: the longest tracked path is 148 characters, Windows' default
   limit is 260, and a clone more than about 110 characters deep fails at checkout unless git has
   `core.longpaths` set (`git clone -c core.longpaths=true ...`; seen on 2026-11-20).
2. Record your machine and versions in `site-b/versions.txt`: CPU model,
   cores and threads, RAM, operating system, and the output of
   `docker version`, `kind version`, `kubectl version --client`,
   `k6 version`, `python --version` and
   `python -m pip show torch sentence-transformers transformers`.
3. Build the prompt vectors once (about 7 hours on our machine; it embeds
   the first user turns and replays the cache grid, WP6's replay stage):
   `python research/cachereplay/run_all.py --preset full --workers 6 --skip gomem,precision,oldcheck,fit`.
4. Bring the testbed up (`testbed/README.md`, "Runbook"):
   ```
   cd testbed/scripts
   python verify_pins.py
   python validate_static.py
   python bringup.py
   ```
5. Calibrate (Part A starts here):
   ```
   cd testbed/validation
   python cell_schedules.py calibration-prompts --out ../out/calibration_prompts.json
   python calibrate.py --prompts ../out/calibration_prompts.json --out ../out/curves
   ```
   Then copy each of the five curves' `load_curve_requests.csv` and
   `load_curve_meta.json` from `testbed/out/curves/<name>/` to
   `testbed/validation/calibration/<name>/`, replacing ours.
6. Schedules and predictions from your calibration, committed in your clone
   before any cell runs (`run_cells.py` refuses to start otherwise):
   ```
   cd testbed/validation
   python cell_schedules.py cells --cells cells_v2.json --curves calibration --out ../out/schedules_v2 --inputs inputs_v2
   python predict.py --calibration calibration --cells cells_v2.json --inputs inputs_v2 --replica-scaling fitted --out predictions_v2.json
   git add calibration inputs_v2 predictions_v2.json && git commit -m "site B: calibration and predictions"
   ```
   **Send us `predictions_v2.json` and that commit's hash now, before step 7.**
7. Provision the tenants, then run and score the cells:
   ```
   cd testbed/scripts
   python provision.py --meta ../out/schedules_v2/cell00/schedule.meta.json --admin-key-file ../out/admin-key.txt --out-dir ../out
   cd ../validation
   python run_cells.py --cells cells_v2.json --predictions predictions_v2.json --schedules ../out/schedules_v2 --tokens ../out/tokens.json --admin-key-file ../out/admin-key.txt --out ../out/validation_v2
   python run_cells.py --cells cells_v2.json --predictions predictions_v2.json --schedules ../out/schedules_v2 --tokens ../out/tokens.json --admin-key-file ../out/admin-key.txt --out ../out/validation_v2 --redo-invalid
   python score.py --runs ../out/validation_v2 --cells cells_v2.json --predictions predictions_v2.json --report VALIDATION_V2.md --plot validation_v2_bland_altman.png
   ```
8. Part B, on the cluster Part A leaves: `cd testbed/wp12 && python after_wp11b.py`.
   It reads your `VALIDATION_V2.md`, designs the two regimes on your
   calibration, tunes the planners, commits the plans before any live run,
   runs the ABBA pairs and writes `PILOT.md` (its log:
   `testbed/out/wp12/after_wp11b.log`).
9. Send back, as one archive: `site-b/versions.txt`; `testbed/out/curves/`;
   `testbed/validation/predictions_v2.json`, `VALIDATION_V2.md` and the
   commit hash of step 6; `testbed/out/validation_v2/` (every run's
   `run.json`, `check.json`, `k6-summary.json` and `k6-samples.csv.gz`);
   `testbed/wp12/PILOT.md`, `pilot.json`, `plans.json`, `regimes.json` and
   `testbed/wp12/runs/`; and every log under `testbed/out/`.

## 5. Rules

- Do not change any code or parameter. If a step fails, stop, keep its log
  and tell us; a fix is ours to make and to record.
- Runs the tools mark invalid are rerun by the tools (step 7's
  `--redo-invalid`; Part B retries an arm up to three times). Send every run,
  valid or not.
- Write down anything you did differently from this file, with the time,
  and send it with the results.
