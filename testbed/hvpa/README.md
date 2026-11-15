# testbed/hvpa — programme 2, WP17 (horizontal with vertical autoscaling)

The second real system: a service that burns a fixed amount of CPU per request, scaled by replicas
and by CPU per pod (in-place resize, GA in Kubernetes 1.35), on the WP10 kind cluster in its own
namespace `pf-hvpa`. The simulation twin, the VPA mirror and the a-priori pilot are in
`research/hvpa/`. Static so far: nothing here has been built or applied.

| path | what it is |
|---|---|
| `cpuburn/` | the service (Go, no dependencies): `GET /work` burns `WORK_ROUNDS` rounds of SHA-256; `/metrics` counts requests |
| `images.lock` | VPA 1.8.1 (recommender, updater, admission controller, chart certgen), metrics-server v0.9.1, KEDA 2.21.0 by digest; the service's build |
| `components.lock` | the release files applied: metrics-server `components.yaml`, `keda-2.21.0.yaml`, VPA chart 0.15.0, with size and sha256 |
| `install.py` | metrics-server (+ `--kubelet-insecure-tls`), KEDA (server-side apply), VPA (helm, `--vpa-setting default` or `fast`), the namespace |
| `k8s/base.yaml` | the service: Guaranteed pod, CPU resize without restart, ClusterIP Service |
| `k8s/prometheus.yaml` | WP17's own Prometheus (pf-hvpa pods and cAdvisor); WP10's is left alone |
| `arms.py` | the arms' objects: A1 CPU HPA + VPA, A2 KEDA rate + VPA (A3 and A4 are planner pods) |

kind maps no spare host port (the WP10 cluster was created with five), so the load generator, the
planner and Prometheus run in the cluster and the host acts through the Kubernetes API only; the
load generator opens a new connection per request, so kube-proxy spreads load over new pods.

## Runbook (after WP11; the CPU must be free)

```bash
cd testbed/scripts
python verify_pins.py --lock ../hvpa/images.lock --no-models
python images.py --lock ../hvpa/images.lock pull && python images.py --lock ../hvpa/images.lock build
python images.py --lock ../hvpa/images.lock load && python images.py --lock ../hvpa/images.lock verify
python ../hvpa/install.py --vpa-setting default --dry-run && python ../hvpa/install.py --vpa-setting default
```

Step 3, the service's calibration at 0.25, 0.5, 1 and 2 cores: `python ../hvpa/calibrate_service.py --out
../out/hvpa/curves` (a Job per level runs `measure/cpu_curve.py`, the WP2 protocol on `GET /work`, inside
the cluster), then the closed-loop rate alone at the horizontal points that fit the node (2, 4, 5 and 6 pods
of 1 core, 8 of 0.5, 2 and 3 of 2: WP11 found pods sub-linear on this one host), then `python
research/hvpa/calibration.py --curves testbed/out/hvpa/curves` fits the twin's plant, checks that capacity
is linear in CPU within 10 %, and gives the plant the measured efficiency per total of requested cores when
pods are not linear within 10 %.

Live runs: `python ../hvpa/live.py run --arm A4 --target 2 --regime step_up:0.2:7 --calibration
research/hvpa/calibration.json --out ../out/hvpa/live` resets the cluster (arm objects, VPA checkpoints,
the recommender's history, the service at 2 x 0.5 core), installs the arm (A1/A2 objects, or the planner
pod `planner/planner_pod.py` with the twin's own decision `research/hvpa/lattice.py`), runs the k6 Job
(`load/rate.js`, k6 2.1.0 in the cluster), polls the pods for cost, resizes and reversals, and writes one
JSON per run. The pilot, `python ../hvpa/pilot.py --predictions research/hvpa/predictions.json --calibration research/hvpa/calibration.json --out ../out/hvpa/pilot`, runs six schedules per live regime, each once by A4, A3 and A1 in the six orders of the three in turn (so both contrasts, A4 against A3 and A4 against A1 at its tuned target, get six paired differences from one A4 run per schedule), and writes the paired differences' spread and the pairs a confirmatory run needs (plan WP12) to PILOT.md; it resumes. Still to come before any live run: the twin's
predictions on the calibrated plant, committed, which also fix the regimes and the tuned A1/A2 targets.

```bash
python -m pytest -q        # from testbed/: these tests run with the testbed's
(cd cpuburn && go test ./...)
```
