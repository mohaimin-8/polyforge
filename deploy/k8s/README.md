# PolyForge on Kubernetes (roadmap W10)

Plain manifests for a local `kind` cluster. Apply in order:

```sh
kind create cluster --name polyforge
kubectl apply -f namespace.yaml
kubectl apply -f postgres.yaml
kubectl apply -f redis.yaml
kubectl apply -f control-plane.yaml
kubectl apply -f ingress.yaml   # requires the NGINX ingress controller
```

The Helm chart under `../helm/polyforge` supersedes `control-plane.yaml`
for anything beyond a first smoke test:

```sh
helm install polyforge ../helm/polyforge --namespace polyforge
```

**NOT VERIFIED LOCALLY**: Docker (and therefore kind) is not installed on the
development machine. These manifests are schema-reviewed only; the W10
verification gates (`helm install` bring-up, zero-downtime
`kubectl rollout restart`) remain unproven until run on a Docker-capable host.

The Postgres manifest uses `POSTGRES_HOST_AUTH_METHOD: trust` to mirror the
local compose stack. That is acceptable only inside a throwaway kind cluster;
a real deployment must switch to password auth via a Secret.
