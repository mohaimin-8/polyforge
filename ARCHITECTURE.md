# PolyForge architecture

PolyForge is an adaptive control plane for multi-tenant clusters serving
**hybrid workloads** — classic CRUD traffic and AI inference (chat,
embeddings, agent chains) from the same tenants on the same
infrastructure. The research claim: per-layer local optima (an autoscaler
here, a cache there, a model router somewhere else) do not compose into a
joint optimum; a controller that plans **replicas, semantic-cache budget,
and model tier together** dominates them on cost, SLO compliance, and
fairness simultaneously.

## The loop, end to end

```
        requests (CRUD + AI, per tenant, JWT-scoped)
              │
              ▼
 ┌─────────────────────────┐      ┌──────────────────────────┐
 │ control plane (Go)      │      │ AI gateway (Go)          │
 │ tenants · projects ·    │      │ chat routing by plan/    │
 │ API keys · rate limits ·│      │ budget · semantic cache  │
 │ RLS-backed storage      │      │ (cost-aware eviction) ·  │
 │                         │      │ vector search · agents   │
 └───────────┬─────────────┘      └────────────┬─────────────┘
             │  telemetry (per request)         │
             ▼                                  ▼
 ┌───────────────────────────────────────────────────────────┐
 │ telemetry: Prometheus /metrics · OTel traces · ClickHouse │
 │ mirror · 12-feature windows (15 min) per tenant           │
 └───────────┬───────────────────────────────────────────────┘
             ▼
 ┌─────────────────────────┐   every 10 s   ┌─────────────────┐
 │ workload classifier     │───────────────►│ JCAC planner    │
 │ 5-class taxonomy ·      │  WorkloadProfile│ (Python svc)   │
 │ softmax model · PSI     │                │ MPC over lattice│
 │ drift detection         │                │ α·cost+β·SLO+   │
 └─────────────────────────┘                │ γ·(1−Jain)      │
                                            └───────┬─────────┘
             noisy-neighbor detector (W32)          │ plan
             feeds interference scores ─────────────┤
                                                    ▼
 ┌───────────────────────────────────────────────────────────┐
 │ Kubernetes operator (Go, cmd/operator)                    │
 │ CRDs: Tenant · WorkloadProfile · Policy · Budget          │
 │ reconciles plans under guardrails: replica bounds, one    │
 │ cache level per step, per-tenant budget caps, last-good-  │
 │ plan fallback when the planner is unreachable             │
 └───────────────────────────────────────────────────────────┘
              │ scales deployments · resizes caches · retiers models
              ▼
        the same data plane the requests hit
```

## Components

| component | code | role |
|---|---|---|
| Control plane | `cmd/control-plane`, `internal/` | tenant/project/API-key APIs, RLS-enforced PostgreSQL (SQLite dev path), rate limiting, telemetry ingestion, canary + isolation-ladder admin APIs |
| AI gateway | `cmd/ai-gateway`, `internal/ai` | multi-backend chat routing (plan/length/budget), semantic cache with **cost-aware eviction** (W28), vector search, agent loop |
| Classifier | `internal/classifier`, `cmd/classifier-train` | 12-feature window vectors → 5-class taxonomy (`research/TAXONOMY.md`), online labels every 10 s with PSI drift detection |
| JCAC planner | `services/planner` + `research/jcac_sim` | receding-horizon MPC on a move-blocked action lattice; **the service imports the simulator's controller, so offline and online planning are provably the same code** |
| Operator | `cmd/operator`, `deploy/operator`, `deploy/helm/polyforge-operator` | 4 CRDs, reconcile with guardrails, ordered finalizer teardown, OTel-traced reconciles, optional **planning cells** (below) |
| Fairness | `internal/` (W32) | peer-relative noisy-neighbor detection on kernel PSI signals → interference penalty in the planner objective; Jain's index exported per cycle. The production feed is PromQL over cAdvisor's PSI series (`fairness.PSIFeed`), off unless `POLYFORGE_PSI_PROMETHEUS_URL` is set; there is no syscall signal unless the cluster runs an eBPF exporter |
| Evaluation | `eval/` | YAML-driven harness, 5 tuned baselines, 1,800-run matrix + ablations, DuckDB results, validation/spot-check/KS tooling (`eval/README.md`) |

## Planning cells, and what fairness means under them

The joint solve is super-linear in tenants — the price of optimizing a
*shared* fairness term rather than each tenant independently. Measured
exponent 1.59; p95 crosses the operator's 3 s per-call timeout at 128 tenants
and, at 256, exceeds the entire 10 s control period
(`research/analysis/CELLS_SHIPPED.md`, reproducing `PLANNER_CELLS.md` PS-H1
on the shipped path).

Past that width the operator can partition the portfolio into fixed-size
**planning cells** (`planner.cellSize`, default 0 = off) and issue one planner
call per cell. Cell size is fixed rather than cell count, so per-cell solve
time stays flat as the portfolio grows — measured 272–378 ms per cell from 64
to 256 tenants, against 10.2 s monolithic at 256.

**The honest cost, stated because it changes what a claim means.** With cells
on, the fairness objective is solved *within each cell*, not across the
portfolio: the planner equalizes SLO satisfaction among a cell's tenants and
no longer sees the others. So "globally fair joint optimization" becomes
"fair within cells, approximately fair globally". The size of that
approximation is measured, not assumed — worst-case global-Jain change
**−0.0053** across N ∈ [8, 1024] (`PLANNER_CELLS_DEALIAS.md`, PF-H1).

That number depends on the assignment rule, which is why the rule is not
arbitrary. Assigning by tenant index aliases with any periodic structure in
the portfolio: with a whale every 8th tenant, every cell count divisible by 8
concentrated the whales and cost up to **−0.089** of global Jain. The operator
therefore orders tenants by `md5(tenant_id)` and chunks — membership cannot
correlate with index. Contiguous-by-budget assignment (all whales in one cell)
remains the adversarial worst case and is named as future work, not measured.

Cells are off by default because that trade should be a deployment decision:
under ~128 tenants the monolithic call is inside the deadline and costs no
fairness at all.

## Design decisions

Every architectural choice with alternatives has an ADR in `docs/adr/`
(0001–0015). Highlights: RLS at the database not the query builder
(0003), outbox-audited isolation ladder (0008), enumeration solver
instead of a MIP backend for the planner (0014), peer-relative rather
than absolute noisy-neighbor detection (0015).

## The research artifacts

- Formal taxonomy: `research/TAXONOMY.md` (paper §3)
- Cost-aware eviction study: `cmd/evictionbench` → `research/results/eviction_comparison.csv` (paper §4)
- JCAC formulation + Pareto sweep: `research/jcac_sim` (paper §5)
- Evaluation: `eval/` + `research/analysis/` (paper §8) — 1,800-run
  matrix, ablations, ANOVA/effect sizes, 12 figures
