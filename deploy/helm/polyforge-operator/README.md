# polyforge-operator Helm chart

Installs the PolyForge operator (Tenant / WorkloadProfile / Policy /
Budget CRDs with reconciled guardrails) and, by default, the JCAC planner
that jointly controls replicas, semantic-cache budget and model tier per
tenant every 10 seconds.

## Install

From the OCI registry:

```bash
helm install polyforge-operator oci://ghcr.io/mohaimin-8/polyforge/charts/polyforge-operator \
  --version 1.0.0 \
  --namespace polyforge-system --create-namespace
kubectl apply -f deploy/operator/samples/tenant-acme.yaml
kubectl get tenants.polyforge.io
```

From a local checkout:

```bash
helm install polyforge-operator deploy/helm/polyforge-operator \
  --namespace polyforge-system --create-namespace
```

## Enabling the JCAC control loop

To activate joint planning of replicas, semantic-cache budget, and model tier, the operator needs three settings:

1. **Planner enabled**: `planner.enabled=true` (default)
2. **Feature API URL**: `features.url=http://polyforge-control-plane.polyforge.svc:80` (or your control-plane service)
3. **Admin authentication**: Either:
   - `features.adminKeySecret.name=<secret-name>` pointing to a Secret with key `admin-key` (recommended for production)
   - `features.token=<bearer-token>` (less secure; not stored encrypted)

For the three-knob plane (cache and tier pushed to the AI gateway, not just replicas), also
set `gateway.adminURL` (e.g. `http://polyforge-ai-gateway.polyforge.svc:80`); it
authenticates with the same `features.adminKeySecret`.

Example:

```bash
helm install polyforge-operator oci://ghcr.io/mohaimin-8/polyforge/charts/polyforge-operator \
  --version 1.0.0 \
  --set planner.enabled=true \
  --set features.url=http://polyforge-control-plane.polyforge.svc:80 \
  --set features.adminKeySecret.name=polyforge-admin \
  --namespace polyforge-system --create-namespace
```

## Key values

| value | default | meaning |
|---|---|---|
| `planner.enabled` | `true` | deploy the JCAC planner and point the operator at it; `false` = static Policy guardrails only |
| `planner.alpha/beta/gamma` | `1.0 / 2.0 / 0.5` | default objective weights (cost / SLO / fairness) when the operator's request omits them; `gamma=0` disables the fairness term (ablation) |
| `planner.forecast` | `holt` | demand forecaster — `holt` is the pre-registered jcac_v2 configuration; `seasonal_mr` sees day-scale cycles live |
| `planner.persistence.enabled` | `false` | `false`: forecast state on an emptyDir (survives container crashes, not rescheduling); `true`: a PVC (`size`, `storageClass`), so a rescheduled planner resumes demand history |
| `features.url` | `""` | control-plane feature API (e.g. `http://polyforge-control-plane.polyforge.svc:80`); empty = operator enforces static Policy guardrails only |
| `features.adminKeySecret.name` | `""` | Secret in the operator's namespace containing the platform admin key; key `admin-key` is read from it |
| `networkPolicy.enabled` | `true` | restrict planner ingress to the operator's pods (the planner's HTTP API is unauthenticated by design, ADR 0014); inert on non-enforcing CNIs such as kind's kindnet |
| `operator.otlpEndpoint` | `""` | OTLP endpoint for reconcile spans; empty disables export |
| `operator.leaderElect` | `true` | leader election for multi-replica safety |
| `*.image.repository/tag` | ghcr.io/mohaimin-8/polyforge/…, `appVersion` | container images (the names release.yml publishes); tags follow appVersion v1.0.0 |

**Admin key.** This chart ships no admin key: `features.adminKeySecret.name` is empty, so
the loop stays off until you create the Secret. The control-plane chart
(`deploy/helm/polyforge`) does ship a default `adminKey: polyforge-kind-admin` for
kind; override it before any shared deployment.

## Planner forecast state

The planner snapshots every tenant's demand-forecast history to
`/var/lib/polyforge/planner-state.json` after each plan (atomic rewrite).
By default the file sits on an emptyDir: a container crash or OOM restart
resumes history, a pod reschedule cold-starts it (a trend forecaster
recovers in ~3 control cycles; seasonal history takes a day). Set
`planner.persistence.enabled=true` to back it with a PVC and survive
rescheduling too. The deployment uses the `Recreate` strategy so the
state file always has a single writer.

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
