# Milestone 0: Setup

## Goal

Establish the repository and first runnable control-plane service.

## Deliverables

- `README.md`
- `docs/EXECUTION_PLAN.md`
- `docs/adr/0001-architecture-scope.md`
- Go control-plane service
- tenant and project APIs
- telemetry ingestion API
- workload classifier
- controller recommendation API

## Verification

Run:

```powershell
go test ./...
go run ./cmd/control-plane
```

Manual checks:

```powershell
Invoke-RestMethod http://localhost:8080/healthz
Invoke-RestMethod http://localhost:8080/v1/tenants
```

## Student Learning Targets

You should be able to explain:

- why the project starts with a small local control-plane
- what a tenant is in PolyForge
- what telemetry fields are required for workload classification
- how the first controller maps workload labels to policy recommendations

