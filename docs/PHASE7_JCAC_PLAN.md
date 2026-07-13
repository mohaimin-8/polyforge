# Phase 7 remainder — the jcac live arm and the ordinal figure

Written 2026-07-13 (session 16e), after the hpa arm was verified live
(`ok: true`, physics-exact metrics — SESSION_LOG 16d). This is the exact,
sized remainder; everything below it in the stack is already proven.

## What is already staged (this session)

- `Dockerfile.operator` and `services/planner/Dockerfile` — both images the
  operator chart needs, mirroring the control-plane image's shape (static /
  slim, numeric nonroot). Neither existed; GHCR has no published images.
- The chart, CRDs (`polyforge.io_{tenants,policies,budgets,workloadprofiles}.yaml`),
  operator binary, and planner service all exist and are unit/envtest-tested.

## The wiring that remains (one Codespace session, honest sizing)

1. **Build + side-load both images** in the runbook (mirror the control-plane
   pattern: `docker build -f Dockerfile.operator -t polyforge/operator:dev .`,
   `kind load ...`; same for the planner).
2. **Install the operator chart** in `command_plan` when `system == "jcac"`:
   `helm install polyforge-operator deploy/helm/polyforge-operator` with
   local image repos/tags and `POLYFORGE_PLANNER_URL` pointing at the
   planner Service. CRDs land via the chart's `crds/`.
3. **Per-tenant CRs**: the harness must create `Tenant`/`Policy` CRs for
   each eval tenant (mirror `provision_tenants`): the plan loop plans per
   Tenant CR and writes Policy specs (`plan_runner.go`). Schema source:
   `internal/operator/api/v1alpha1` + `deploy/operator/` samples if present.
4. **Demand plumbing**: `cmd/operator` enables the JCAC loop only when
   `POLYFORGE_PLANNER_URL` *and* a demand source are set — wire the demand
   source to the control plane's `/v1/tenants/{id}/telemetry/features`
   (admin or per-tenant key via a Secret the harness already mints).
5. **Actuation check**: confirm what enforces `Policy.Spec.Replicas` onto
   the target Deployment (policy_controller) and that its target selector
   can name `polyforge-control-plane` in the eval namespace. If Policy
   enforcement stops at CR state, add the Deployment-scale step to the
   policy controller — that is the only potentially non-trivial code.
6. **First jcac live smoke** (expect a bug tail like the hpa arm's five),
   then the ordinal slice: `experiments/phase7_live.yaml` (12 runs ≈ 5 h
   Codespace wall) or a declared reduced slice (2×1×3 = 6 runs ≈ 2.5 h).
7. **Figure**: paired jcac-vs-hpa live J/cost/violation vs the sim's
   ranking on the same cells — ordinal agreement only (ground rule 4).

## Honesty gate (unchanged)

`jcac` must not run live until step 5 is verified — a fixed-replica pod
recorded under PolyForge's name would be a mislabeled baseline, which is
worse than no figure. The hpa arm stays the only live-verified system
until then.
