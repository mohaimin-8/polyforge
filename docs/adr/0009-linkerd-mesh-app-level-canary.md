# ADR 0009: Linkerd mesh for mTLS; canary at two layers

Status: accepted · 2026-07-09 · app-level canary implemented; mesh manifests authored, unexecuted

## Context

W22 requires encrypted service-to-service traffic and canary releases.
The mesh choice was Linkerd vs Istio; the canary question was where the
split lives — in the mesh, in the app, or both.

## Decisions

1. **Linkerd over Istio.** The roadmap's own rationale holds: one-command
   install, a purpose-built Rust micro-proxy (~5x lower memory than
   Envoy), and mTLS with automatic certificate rotation on by default
   rather than as configuration. PolyForge needs mTLS and TrafficSplit,
   not Istio's VirtualService programmability.
2. **Canary at two layers, deliberately.**
   - *Mesh layer* (`deploy/linkerd/trafficsplit.yaml`): SMI TrafficSplit,
     95/5 between `ai-gateway-v1`/`-v2` pods. Right tool when the canary
     is a new build of the same service.
   - *Application layer* (`gateway.CanaryProvider`): weighted split
     between two model hosts inside one gateway process, with an error-
     window breaker that automatically shifts traffic back to stable.
     Right tool when the canary is a new *model backend* (the thing this
     platform actually iterates on), and — decisively for a thesis that
     must prove its claims — testable in CI without a cluster. The W22
     verification "error spike on v2 → traffic auto-shifts back to v1" is
     enforced by `TestCanaryErrorSpikeAutoRollsBack`, not by hand.
3. **Deterministic split, not random.** The app-level canary routes by
   request position modulo 100, so weight=5 sends exactly 5 of every 100
   requests to the canary. Reproducible in tests, gradual at low traffic.

## Consequences

- Mesh manifests are authored and reviewed but **unexecuted**: no
  local Docker/K8s (see repo conventions). `linkerd viz tap`-based mTLS
  verification is a runbook step in deploy/linkerd/README.md, to be
  performed on the first real cluster and logged then.
- Per-route golden signals come from `linkerd viz stat` once meshed; until
  then the gateway's own /metrics remains the observability source.
