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

Scale-out in a public CI runner (the plan's option) is not set up; check the provider's
acceptable-use terms before using it.

```bash
python -m pytest -q        # 116 unit tests of the helpers (no cluster needed)
```
