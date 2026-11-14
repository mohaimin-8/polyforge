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

Next, not written yet: the calibration of the service at 0.25, 0.5, 1 and 2 cores (WP17 step 3), the
planner pod for A3 and A4, the in-cluster load generator, and the paired live runs.

```bash
python -m pytest -q        # from testbed/: these tests run with the testbed's
(cd cpuburn && go test ./...)
```
