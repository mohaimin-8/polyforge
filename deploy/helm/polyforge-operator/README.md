# polyforge-operator Helm chart

Installs the PolyForge operator (Tenant / WorkloadProfile / Policy /
Budget CRDs with reconciled guardrails) and, by default, the JCAC planner
that jointly controls replicas, semantic-cache budget and model tier per
tenant every 10 seconds.

## Install

```bash
helm install polyforge-operator deploy/helm/polyforge-operator \
  --namespace polyforge-system --create-namespace
kubectl apply -f deploy/operator/samples/tenant-acme.yaml
kubectl get tenants.polyforge.io
```

From the public repository (after `helm repo add polyforge <repo-url>`):

```bash
helm install polyforge-operator polyforge/polyforge-operator \
  --namespace polyforge-system --create-namespace
```

## Key values

| value | default | meaning |
|---|---|---|
| `planner.enabled` | `true` | deploy the JCAC planner and point the operator at it; `false` = static Policy guardrails only |
| `planner.alpha/beta/gamma` | `1.0 / 2.0 / 0.5` | default objective weights (cost / SLO / fairness) when the operator's request omits them; `gamma=0` disables the fairness term (ablation) |
| `operator.otlpEndpoint` | `""` | OTLP endpoint for reconcile spans; empty disables export |
| `operator.leaderElect` | `true` | leader election for multi-replica safety |
| `*.image.repository/tag` | ghcr.io/polyforge/…, `appVersion` | container images |

## CRDs

CRDs live in `crds/` and are installed by Helm on first install; Helm does
not upgrade or delete CRDs (`helm upgrade` leaves them, `helm uninstall`
keeps them and every Tenant in the cluster). Upgrade CRDs explicitly:

```bash
kubectl apply -f deploy/helm/polyforge-operator/crds/
```

## Verify a render locally

```bash
helm lint deploy/helm/polyforge-operator
helm template polyforge-operator deploy/helm/polyforge-operator | kubectl apply --dry-run=client -f -
```
