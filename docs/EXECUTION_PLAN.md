# PolyForge Execution Plan

## Objective

Build PolyForge as a production/research-grade thesis project with a defensible platform artifact, reproducible evaluation, and publishable-quality thesis material.

This is an 8-11 month program if executed honestly. The work is divided into milestones so each phase produces something runnable, measurable, and explainable.

## Non-Negotiable Standards

- Every major claim must map to code, experiment data, or cited literature.
- Every service must have a documented API and a repeatable local run path.
- Every experiment must be reproducible from a config file and must produce immutable result artifacts.
- The thesis must distinguish implemented behavior, simulated behavior, and future work.
- The student must be able to explain every architectural decision and defend the evaluation method.

## Milestone 0: Program Setup

Deliverables:

- repository structure
- architecture decision records
- local control-plane seed service
- first thesis scope document
- weekly work protocol

Exit criteria:

- `go run ./cmd/control-plane` starts locally
- health, tenant, telemetry, classifier, and controller endpoints work
- next milestone has concrete tasks

## Milestone 1: Multi-Tenant Backend Core

Deliverables:

- control-plane service
- PostgreSQL schema
- tenant isolation model
- project CRUD
- API key authentication
- structured logging
- OpenAPI contract
- integration tests

Exit criteria:

- tenant A cannot read tenant B data
- CRUD API supports load testing
- test suite runs locally and in CI

## Milestone 2: Observability and Telemetry

Deliverables:

- request telemetry schema
- metrics and traces
- local dashboard
- telemetry ingestion service
- ClickHouse or PostgreSQL analytical store

Exit criteria:

- every request emits tenant, service, latency, payload, cache, and model metadata
- telemetry can be queried for classifier features

## Milestone 3: AI Gateway and Semantic Cache

Deliverables:

- AI gateway API
- local/mock/provider routing abstraction
- cache keying and semantic-cache simulation
- model-tier cost model
- cache baseline policies

Exit criteria:

- AI-like requests can be replayed
- cache hit rate and estimated cost are measurable per tenant

## Milestone 4: Workload Classifier

Deliverables:

- five-class workload taxonomy
- feature extraction pipeline
- offline classifier training/evaluation
- online classifier service
- drift detection

Exit criteria:

- classifier labels tenant streams every control window
- held-out classification report exists
- latency is below online-control threshold

## Milestone 5: Adaptive Controller

Deliverables:

- controller model
- action vector: replicas, cache budget, model tier
- policy constraints: SLO, budget, fairness
- simulator
- live recommendation API

Exit criteria:

- controller improves at least two evaluation metrics against simple baselines in simulation
- decisions are auditable and explainable

## Milestone 6: Kubernetes Operator

Deliverables:

- CRDs: Tenant, WorkloadProfile, Policy, Budget
- reconcile loops
- controller integration
- Helm chart
- local kind deployment

Exit criteria:

- applying a Tenant resource creates required platform resources
- policy changes patch Kubernetes objects
- operator survives restart without state loss

## Milestone 7: Evaluation Harness

Deliverables:

- trace normalization
- replay driver
- baseline implementations
- experiment YAML schema
- result database
- smoke test suite

Exit criteria:

- one command launches a clean experiment
- invalid runs are detected and excluded
- smoke runs are deterministic within tolerance

## Milestone 8: Full Evaluation

Target matrix:

- 5 workload classes
- 4 tenant-mix profiles
- 3 cluster sizes
- PolyForge + 5 baselines
- 5 repetitions

Total: 1,800 runs.

Metrics:

- cost
- p99 latency
- SLO violation rate
- cache hit rate
- Jain fairness index

Exit criteria:

- raw results archived
- statistical analysis notebook complete
- figures and tables exported

## Milestone 9: Thesis and Publication Package

Deliverables:

- full thesis report
- related work matrix
- architecture diagrams
- evaluation figures
- defense slides
- viva Q&A
- repository demo script

Exit criteria:

- report compiles
- claims are traceable
- demo can be completed from a clean machine

## Working Protocol

Every work session should produce one of:

- runnable code
- passing tests
- measured data
- thesis text
- a decision record

At the end of each session, update:

- current milestone status
- what changed
- how it was verified
- what the student should learn/explain next

