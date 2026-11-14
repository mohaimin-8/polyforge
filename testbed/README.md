# testbed — programme 2, work package WP10 (a minimal real system)

A one-node kind cluster that serves real models on this laptop's CPU (mode B) or a mock API tier
(mode A), with the PolyForge gateway (OpenAI-compatible adapter, semantic cache, real embedder) in
front, Prometheus scraping gateway and servers, k6 replaying real arrival schedules, and an actuator
that applies the planner's replica decisions. Prepared and validated statically; **not yet built or
run** (no image pulled, no cluster created).

| path | what it is |
|---|---|
| `kind/kind-config.yaml` | one node, node image pinned by digest, ports on 127.0.0.1 only (set at create time) |
| `k8s/base/` | namespace, gateway, control plane, Postgres, embedder (text-embeddings-inference), Envoy tier load balancer, tier Services, NodePorts, Prometheus, model provisioning (`models.yaml`, `load-models.sh`, `models.lock`) |
| `k8s/mode-b/` | two `llama-server` Deployments (small, mid), fixed CPU limits, `--metrics` |
| `k8s/mode-a/`, `mock/` | the mock tier: latency drawn from a recorded distribution, a stored answer |
| `images.lock` | every image with its index and linux/amd64 digest, read from the registries' public API on 2026-11-13 |
| `scripts/` | `images.py` (plan, pull, build, load, verify), `verify_pins.py`, `validate_static.py`, `provision.py`, `check_run.py` |
| `load/` | `make_schedule.py` (a population workload's native-replay arrivals as a schedule) and `replay.js` (k6) |
| `measure/load_curve.py` | the congestion protocol (`research/plant2/measure_load_curve.py`) against a tier through its NodePort |
| `actuator/actuate.py` | applies replica decisions to the Deployments (`apply`, `replay`, `status`) |
| `validation/` | WP11: the 20 cells, their schedules (`cell_schedules.py`), the cache replay (`cell_hits.py`), the predictions (`predict.py`), the runs (`run_cells.py`), the score (`score.py`) |
| `wp12/` | WP12: the joint and per-knob planners on the testbed's exact knobs and calibrated form (`tb_planner.py`), the regimes, design, tuning and plans committed before any live run (`plans.py`), the ABBA live pairs and pairs needed (`pairs.py`), run end to end by `after_wp11b.py` from the post-WP11 chain |

Load and measurements go through NodePorts, never `kubectl port-forward`: in session 48 a
port-forward pinned all load to one pod and dropped 127-220 connections per run at about 100 rps.

## Runbook (run only when the CPU is free; about 2 GB of images and the model files)

```bash
cd testbed/scripts
python verify_pins.py                    # the pins still match the registries
python validate_static.py                # manifests, kustomize builds, cross-references
python images.py plan                    # every command it would run
kind create cluster --config ../kind/kind-config.yaml
python images.py pull && python images.py build && python images.py load && python images.py verify
kubectl apply -k ../k8s/mode-b           # or ../k8s/mode-a for the mock tier
python provision.py --help               # models and schedules into the cluster (see its docstring)
# first measurement: each tier's latency against load, the congestion protocol, feeds WP2
python ../measure/load_curve.py --help   # e.g. --tier small --replicas 1 --base-url http://127.0.0.1:30091
# a load run: a population workload's native arrivals at a target utilisation
python ../load/make_schedule.py --help   # e.g. native --workload pop007 --steps 180 ...
python check_run.py --help               # checks a finished run (k6 samples, SLO, CPU from Prometheus)
```

`python bringup.py` runs the cluster, image and apply steps in order, plus the gateway admin
Secret the manifests do not carry (a random key in `out/admin-key.txt`); logs in `out/bringup/`.

## WP11: the simulator against this testbed (`validation/`)

The cells (`cells.json`) were fixed before any run; the predictions are committed before the
first cell runs, and `run_cells.py` refuses to start otherwise.

```bash
cd testbed/scripts
python ../validation/cell_schedules.py calibration-prompts --out ../out/calibration_prompts.json
# each tier at one replica, full protocol plus real-prompt baseline and capacity
python ../measure/load_curve.py --tier small --replicas 1 --base-url http://127.0.0.1:30091 \
    --prompts ../out/calibration_prompts.json --out ../out/curves/small_r1 --deployment llama-small
python ../measure/load_curve.py --tier mid --replicas 1 --base-url http://127.0.0.1:30092 \
    --prompts ../out/calibration_prompts.json --out ../out/curves/mid_r1 --deployment llama-mid
# small at 2 and 3 replicas, mid at 2: --phases capacity (the cells' load targets)
python ../validation/cell_schedules.py cells --curves ../out/curves --out ../out/schedules
python ../validation/predict.py --calibration ../out/curves      # commit predictions.json + inputs/ now
python provision.py --meta ../out/schedules/cell00/schedule.meta.json --admin-key-file ../out/admin-key.txt --out-dir ../out
python ../validation/run_cells.py --schedules ../out/schedules --tokens ../out/tokens.json \
    --admin-key-file ../out/admin-key.txt --out ../out/validation
python ../validation/score.py --runs ../out/validation           # VALIDATION.md
```

Scale-out in a public CI runner (the plan's option) is not set up; check the provider's
acceptable-use terms before using it.

```bash
python -m pytest -q        # 152 unit tests of the helpers (no cluster needed)
```
