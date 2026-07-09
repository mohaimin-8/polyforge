# Linkerd service mesh (roadmap W22, ADR 0009)

Linkerd over Istio: simpler operational model, Rust micro-proxy sidecar
with roughly 5x lower memory footprint, and mTLS on by default with
automatically rotated certificates.

## Install (kind or any cluster)

```sh
curl --proto '=https' --tlsv1.2 -sSfL https://run.linkerd.io/install | sh
linkerd check --pre
linkerd install --crds | kubectl apply -f -
linkerd install | kubectl apply -f -
linkerd viz install | kubectl apply -f -          # dashboard + tap
curl -sL https://linkerd.github.io/linkerd-smi/install | sh   # TrafficSplit support
linkerd check
```

## Mesh the PolyForge namespaces

Workload templates already carry `linkerd.io/inject: enabled`
(deploy/k8s/control-plane.yaml, deploy/k8s/ai-gateway.yaml), so applying
them on a Linkerd cluster injects the sidecar. To mesh everything in the
namespace instead:

```sh
kubectl annotate namespace polyforge linkerd.io/inject=enabled
kubectl rollout restart deploy -n polyforge
```

## Verify mTLS (W22 gate)

```sh
linkerd viz tap deploy/control-plane -n polyforge   # every row shows tls=true
linkerd viz edges deployment -n polyforge           # SECURED column
linkerd viz authz deploy/ai-gateway-v1 -n polyforge # per-Service policy
```

## Canary release

`trafficsplit.yaml` sends 95% of `ai-gateway` traffic to v1 and 5% to v2:

```sh
kubectl apply -f deploy/k8s/ai-gateway.yaml
kubectl apply -f deploy/linkerd/trafficsplit.yaml
linkerd viz stat trafficsplit -n polyforge   # per-backend success rate
```

Watch v2's success rate on the Linkerd dashboard / Grafana; promote by
editing weights toward 0/100, roll back by zeroing v2. Inside each gateway
process, `gateway.CanaryProvider` provides the same split against a second
model host with automatic rollback on error spike — that variant is
unit-tested (`internal/ai/gateway/canary_test.go`) and needs no cluster.

## Status

Manifests and runbook are in-repo and reviewed; **not yet executed against
a live cluster** — this development machine runs no Docker/K8s. First
execution target: a kind cluster in CI or on the deployment box.
