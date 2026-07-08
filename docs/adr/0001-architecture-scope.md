# ADR 0001: Architecture Scope

## Status

Accepted

## Context

The original PolyForge roadmap describes a large adaptive platform that combines backend services, AI gateway behavior, workload classification, Kubernetes control, semantic caching, multi-tenancy, and experimental evaluation.

The project must remain credible as a thesis artifact and practical enough to complete through iterative implementation.

## Decision

PolyForge will be built as a staged platform:

1. A local control-plane slice using Go and in-memory state.
2. A persistent multi-tenant backend using PostgreSQL.
3. AI gateway and cache components.
4. Classifier and controller services.
5. Kubernetes operator integration.
6. Reproducible evaluation harness.

The first implementation uses a deterministic classifier and rule-based controller. Later milestones may replace these with trained models and MPC planning once telemetry and benchmarks exist.

## Consequences

- The project is runnable from the first milestone.
- Claims can evolve from simple measurable behavior to research-grade evidence.
- The architecture avoids premature Kubernetes complexity before the domain model is stable.
- The thesis can clearly separate implemented, evaluated, and future-production behavior.

