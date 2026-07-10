# Contributing to PolyForge

Thanks for looking under the hood. PolyForge is a research system (thesis
+ FGCS paper) and a working K8s operator; contributions are welcome in
both capacities.

## Ground rules

- Be kind. The [Code of Conduct](CODE_OF_CONDUCT.md) applies everywhere.
- One change per PR; describe what breaks without it.
- Every behavioral change needs a test that fails before and passes after.
- Architectural decisions get an ADR in `docs/adr/` — see the existing
  fifteen for the format. If a change contradicts an ADR, amend the ADR in
  the same PR, don't silently diverge.

## Getting a dev environment

```bash
go test ./...                         # Go control plane + operator (SQLite path, no Docker needed)
cd research/jcac_sim && python -m unittest   # controller + baselines
cd eval && pip install -r requirements.txt && python -m pytest tests -q  # harness
```

PostgreSQL-backed tests need Docker (`compose.yaml`); without it they
skip — CI runs them. Lint matches CI: `gofmt`, `go vet`,
`golangci-lint run`.

## Layout in one breath

Go control plane (`internal/`, `cmd/control-plane`), AI gateway
(`cmd/ai-gateway`), K8s operator (`cmd/operator` + `deploy/operator`),
JCAC planner (`services/planner`, imports the simulator in
`research/jcac_sim` so offline and online planning are the same code),
evaluation harness (`eval/`), paper research (`research/`). The full tour:
[ARCHITECTURE.md](ARCHITECTURE.md).

## Reporting issues

Use the issue templates. For results/reproducibility issues, include the
`run_id` and the experiment YAML — every result row is replayable by
design (`eval/scripts/spot_check.py`).

## Security

Do not open public issues for vulnerabilities — see `docs/SECURITY.md`.
