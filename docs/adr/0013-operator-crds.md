# ADR 0013: Kubernetes operator with four CRDs as the control surface

Status: accepted · 2026-07-10 · operator implemented (W29)

## Context

M8 turns PolyForge's per-layer mechanisms (routing, cache bounds, isolation
ladder, canary) into one closed control loop. The loop needs a place where
desired state lives, survives process death, and is observable with standard
tooling. The W29 plan prescribes kubebuilder + controller-runtime with four
CRDs: Tenant, WorkloadProfile, Policy, Budget.

## Decision

Build the operator on controller-runtime v0.22 with hand-written API types
under `internal/operator/api/v1alpha1` (group `polyforge.io`), generating
deepcopy methods and CRD manifests with `controller-gen` invoked via
`go run` — no kubebuilder scaffold.

Two reconcilers, matching the plan's causal arrows:

- **TenantReconciler** — namespace (silo tenants get `pf-tenant-<name>`;
  pool/bridge share `polyforge-tenants` per the ADR 0008 ladder), read-only
  tenant RBAC, and default Policy/Budget/WorkloadProfile. A finalizer
  (`polyforge.io/tenant-cleanup`) deletes dependents in order — CRs first,
  namespace second, Tenant last — so nothing is ever stranded.
- **PolicyReconciler** — clamps replicas to `[replicaMin, replicaMax]`,
  patches the target Deployment, and writes the per-tenant
  cache-size/model-tier ConfigMap the gateway reads.

All four kinds use the status subresource; the operator maintains `Ready`
(Tenant) and `Applied` (Policy) conditions so `kubectl get`/`describe` show
live state. Every reconcile runs inside an OTel span.

Design rules encoded in the types:

- Spec is desired state written by humans or the planner; status is observed
  state written only by the operator. Classifier predictions are
  observations, so they live in WorkloadProfile **status**, not spec.
- CRD schemas reject floats, so money is integer milli-USD
  (`hourlyCapMilliUSD`) and probabilities are permille
  (`confidencePermille`). Float money is a bug factory anyway.
- The Tenant reconciler creates defaults but never updates them: after
  creation the Policy spec belongs to the planner (and humans), and a tenant
  re-reconcile must not stomp a live plan.

## Deviations from the week plan

- **No kubebuilder scaffold.** The scaffold assumes a standalone module with
  its own Makefile/Dockerfile layout; PolyForge is a single Go module.
  controller-runtime and controller-gen are what kubebuilder generates
  anyway — the scaffold is the part we skip, not the pattern.
- **CRDs are cluster-scoped.** Tenants are a platform-level concept that
  owns namespaces; nesting them inside a namespace would invert that
  ownership.

## Verification

Reconcilers are tested against the controller-runtime fake client:
namespace/RBAC/default creation, pool-vs-silo namespace placement, finalizer
delete order, shared-namespace survival, replica clamping (including
inverted bounds), missing-target requeue with condition, ConfigMap writes.

Not verified locally: behavior against a real API server (`kubectl apply`,
kill -9 resume from etcd). No Docker/kind on this machine; that requires a
cluster and is deferred to the W33 harness environment. The fake client does
not run garbage collection or namespace termination, so those paths are
asserted only up to the API calls the operator issues.
