# PolyForge

PolyForge is a thesis-grade platform engineering research project for adaptive multi-tenant backend infrastructure that hosts hybrid CRUD and AI workloads.

The target research contribution is a workload-aware controller that observes tenant traffic, classifies workload behavior, and recommends joint control actions across replicas, cache budget, and model routing.

**Start here**: [docs/INDEX.md](docs/INDEX.md) (documentation map) · [ARCHITECTURE.md](ARCHITECTURE.md) (system tour) · [research/analysis/RESULTS_MASTER.md](research/analysis/RESULTS_MASTER.md) (every measured result, one page) · [eval/README.md](eval/README.md) (evaluation + results) · [CONTRIBUTING.md](CONTRIBUTING.md) · [docs/adr/](docs/adr/) (design decisions)

## Install the operator (Helm)

```bash
helm install polyforge-operator deploy/helm/polyforge-operator \
  --namespace polyforge-system --create-namespace
kubectl apply -f deploy/operator/samples/tenant-acme.yaml
```

Chart details: [deploy/helm/polyforge-operator/README.md](deploy/helm/polyforge-operator/README.md).

## Current Phase

The current implementation provides a persistent control-plane backend:

- tenant registration API
- complete project CRUD with keyset pagination
- SQLite local persistence and PostgreSQL production persistence behind repository interfaces
- admin and tenant API-key authentication with read/full scopes enforced per route
- API-key listing, revocation, and atomic scope-preserving rotation
- request IDs, W3C `traceparent` propagation, structured JSON errors, and structured request logs
- Prometheus-compatible `/metrics` endpoint for HTTP and accepted telemetry metrics
- in-process token-bucket rate limiting for authenticated and anonymous API traffic
- PostgreSQL row-level security with transaction-local tenant context
- workload telemetry ingestion
- classifier feature-query API over a bounded time window
- deterministic workload classifier
- adaptive policy recommendation endpoint
- AI gateway: multi-backend chat routing by tenant plan / prompt length / daily budget (`deploy/routing.json`), native Ollama + OpenAI-compatible adapters, semantic cache, vector search, agent loop
- tenant isolation ladder (pool → bridge → silo) with an admin promotion API, audited via the outbox (ADR 0008)
- canary releases at two layers: Linkerd TrafficSplit manifests and an in-process weighted split with automatic error-spike rollback (ADR 0009)
- secrets via a Vault Agent file → Vault KV (K8s auth) → env chain; OIDC code+PKCE relying party for human login (ADR 0010)
- security hardening: default-deny NetworkPolicies, OWASP API Top 10 compliance doc (`docs/SECURITY.md`), gitleaks + SBOM in CI, cosign-signed release images
- inference benchmark harness (`cmd/llmbench`) producing the TTFT/throughput/cost CSV behind `docs/INFERENCE_BENCH.md`
- analytical telemetry pipeline: ClickHouse schema + OTel collector config (`deploy/clickhouse/`), best-effort in-process mirror behind `POLYFORGE_CLICKHOUSE_URL` (ADR 0011, `docs/CLICKHOUSE_DESIGN.md`)
- trace research infrastructure: Azure/Alibaba/LMSYS ETL to a normalized schema (`research/traces/`), deterministic replay driver (`cmd/replay`) with cross-language stream-hash proof
- workload classifier: 12-feature pipeline, trained softmax model with drift-guarded JSON artifact (`cmd/classifier-train`, ADR 0012), formal 5-class taxonomy (`research/TAXONOMY.md`)
- online classification: per-tenant labels every 10s with PSI drift detection, label-change events to NATS, admin dashboard at `/admin/workloads` (W27)
- cost-aware semantic-cache eviction: per-tenant bounded cache, 6-policy benchmark (`cmd/evictionbench`, `research/results/eviction_comparison.csv`), paper section at `research/paper/sec-eviction.tex` (W28)
- Kubernetes operator (`cmd/operator`): Tenant/WorkloadProfile/Policy/Budget CRDs with status subresources, ordered finalizer teardown, replica-bound guardrails, OTel-traced reconciles (ADR 0013, W29)
- JCAC joint controller: MPC formulation + offline simulator with Pareto sweep (`research/jcac_sim`, `research/paper/sec-jcac.tex`, W30); live planner service (`services/planner`) called by the operator every 10s with last-good-plan fallback and NATS action audit (ADR 0014, W31)
- multi-tenant fairness: peer-relative noisy-neighbor detector on eBPF-shaped signals feeding an interference penalty into the planner, Jain's index exported per plan cycle (ADR 0015, W32; Pixie adapter + soak deferred to the harness environment)
- evaluation harness (`eval/`): YAML-driven experiment matrix with deterministic run IDs, resume, retry-on-flake, and a standardized DuckDB result schema; sim backend verified, cluster backend (kind + Helm + k6) code-complete for the cloud box (W33)
- five tuned baselines — HPA, KEDA, FIRM-replica (OSDI '20 re-implementation), static over-provisioned, GPTCache+LRU — each grid-searched with committed sweep evidence (`eval/baselines/TUNING.md`, W34)
- 1,800-run full matrix + 500-run ablation matrix executed and validated: exact row counts, zero duplicate/NULL rows, 10/10 bit-identical spot-check replays, Holm-corrected KS reproducibility gate (`eval/SMOKE_BUGS.md`, W35)
- statistical analysis: two-way ANOVA, Cohen's d vs every baseline, 95% CIs, per-component ablation significance, 12 publication figures (600-DPI PNG + vector PDF, color-blind-safe) — `research/analysis/RESULTS.md` (W36)
- open-source packaging: `polyforge-operator` Helm chart (`deploy/helm/polyforge-operator`), architecture guide, contributor guide + templates, Zenodo artifact bundler, release checklist (`docs/RELEASE_CHECKLIST.md`, W39)
- advanced controller work: pluggable demand forecasters (persistence / trend / damped Holt / online-seasonal) with a forecast ablation showing Holt beats the W30 trend default by ~16% on SLO violation at equal cost; self-calibrating JCAC that learns effective capacity from realized-vs-projected feedback; a reconfiguration-realism scoring mode (replica startup lag + cache warm-up) where hysteresis and self-calibration earn their keep (`research/analysis/ADVANCED.md`, figs 13–15)
- **security contribution — cross-tenant cache timing side channel:** a shared semantic cache leaks tenant prompt membership at AUC 0.88 from response time alone; PolyForge's per-tenant bounded cache (W28) returns the attacker to chance, and the joint planner's demand-proportional sizing makes the isolation cheaper than a naive equal split (`research/security/`, `TestCacheGivesNoCrossTenantHit`, fig 16)
- **v2/v3 pre-registered evaluation campaign** (every hypothesis pushed to GitHub before its data existed; nulls published): iso-cost gate quantifying that static/cache-max postures buy their metric wins with 12–36× spend (`RESULTS_V2.md`); overload matrix proving the forecast mechanism (seasonal < trend on violation, p=6e-4) and the honest boundary — tuned reactive scalers buy attainment at 3.2–3.4× spend (`RESULTS_V3.md`); security defense frontier where per-tenant partitioning dominates padding/TTL-jitter (fig 17, `ADVANCED.md`); fairness γ-term honestly nulled under injected interference (`FAIRNESS_V2.md`)
- **real-trace evaluation (BurstGPT v2.0, 10,632,194 real Azure OpenAI/ChatGPT requests):** reproducible fetch+ETL (`research/traces/fetch_burstgpt.py`); forecast ablation on real periodicity — damped Holt −26.9% one-step RMSE vs the trend default, and the synthetic stand-in's seasonal prediction published as *not transferring* (`FORECAST_TRACE_REAL.md`); pre-registered headline replay on real demand shape — first sample (n=16) underpowered but directionally consistent (`RESULTS_TRACE.md`); **powered second sample (n=96) confirmatory: PolyForge beats tuned HPA/KEDA/FIRM on the composite objective at p ≤ 5.5e-05 with cost −70% (p ≤ 4.3e-06), disclosed violation trade +0.07** (`RESULTS_TRACE2.md`)
- **VTC-replica baseline (OSDI '24 fair-scheduler re-implementation) beaten on its own turf:** tuned least-weighted-service-first pool division loses the joint objective to PolyForge (d_z=−1.12, p=3e-19) *and* is less fair on Jain (0.9599 vs 0.9705, p=1.5e-6) at 55% higher cost with 57% higher worst-tenant p95 — fairness emerges from joint optimization more cheaply than from a fairness-only rule (`VTC_FAIRNESS.md`, pre-registered)
- **related-work position:** capability + performance matrix vs SageServe (POMACS '25), Chiron, Aladdin, VTC/Equinox/D²LPM, GPTCache/SCALM/InstCache/MeanCache — no surveyed system co-optimizes replicas, semantic cache, and model tier against one multi-tenant objective, and none publishes pre-registered nulls (`docs/RELATED_WORK.md`)
- **Wave 5 structural-form program (session 24):** the sensitivity axis the economy reruns never touched — the simulator's *functional forms*. Three pre-registered full-matrix reruns (measured latency model, mixture-percentile p95, tier-scaled work units) all PASS with narrowed margins and SLO non-inferiority held; a solver audit measured the coordination gap **zero on 120/120 frozen instances** and en route caught the second CD sweep exceeding the per-interval actuation clamps — adjudicated by a pre-registered clamp-fixed rerun that wins slightly *more* (MC 5/5, 5/5, 2/2), making `anchor_moves` the quotable controller; plus churn-safe/thread-safe planner state, deployment-selectable forecasters, and a multi-resolution seasonal forecaster that lets the *live* planner see daily cycles (`FORECAST_MR.md`, DEFENSE_QA #24–25)

SQLite remains the zero-infrastructure development path. PostgreSQL is the production path and requires separate admin and application roles so row-level security is testable rather than bypassed accidentally.

## Run

```powershell
.\scripts\run-control-plane.ps1
```

The run script uses the local admin key `pf_admin_local`. Create a tenant:

```powershell
$headers = @{ "X-PolyForge-Admin-Key" = "pf_admin_local" }
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8080/v1/tenants `
  -Headers $headers `
  -ContentType "application/json" `
  -Body '{"id":"acme","name":"Acme Research"}'
```

The response contains a one-time `bootstrap_api_key.secret`. Use it as `X-PolyForge-API-Key` for tenant-scoped endpoints.

## Test

```powershell
.\scripts\test.ps1
.\scripts\smoke.ps1
```

The API contract is at `api/openapi.yaml`.

## Observability

Scrape local metrics:

```powershell
Invoke-WebRequest http://localhost:8080/metrics
```

The endpoint exposes request counters, request-latency histograms, accepted telemetry-event counters, telemetry-latency histograms, and payload-size histograms. It intentionally does not expose tenant IDs as Prometheus labels; tenant-level analysis uses the telemetry repository.

Every HTTP response includes `X-Request-ID` and `traceparent`. If a valid incoming `traceparent` header is provided, the trace ID is preserved and PolyForge creates a new server span ID. If it is absent or invalid, PolyForge starts a new trace context.

With Docker installed, start the local Prometheus and Grafana stack:

```powershell
.\scripts\run-observability.ps1
```

Prometheus opens at `http://localhost:9090`. Grafana opens at `http://localhost:3000` with `admin` / `polyforge-local` and provisions the `PolyForge Control Plane` dashboard. Keep the control plane running on port `8080`; Prometheus scrapes it through `host.docker.internal:8080`.

## Classifier Features

Aggregate a tenant's telemetry into per-service classifier features:

```powershell
$headers = @{ "X-PolyForge-API-Key" = $tenantKey }
$uri = "http://localhost:8080/v1/tenants/acme/telemetry/features?since=2026-01-01T00:00:00Z"
Invoke-RestMethod -Uri $uri -Headers $headers
```

The window is `[since, until)`. Both bounds are optional RFC 3339 timestamps; `until`
defaults to now and `since` defaults to 15 minutes earlier. An inverted or unparsable
window returns `400` rather than being silently corrected. Add `service=<name>` to
restrict the aggregation to one service.

## Local CRUD Baseline

Generate a repeatable local SQLite CRUD profile:

```powershell
.\scripts\bench-crud.ps1
```

The default run creates 4 tenants and performs 25 project create/read/update/list/delete cycles per tenant. The latest artifact is `artifacts/m1-crud-baseline.json`.

## HTTP Throughput Baseline

Measure raw handler-path throughput with the Go load generator:

```powershell
.\scripts\bench-http.ps1
```

This builds the server and `cmd/loadgen`, drives `/healthz` for 15 seconds with
a worker pool (2 goroutines per CPU by default), and writes
`artifacts/m1-http-baseline.json`. The run sets `POLYFORGE_LOG_LEVEL=warn` and
disables the rate limiter so the number reflects the handler path rather than
the logger. Latest measurement: **71,893 RPS, p99 1 ms, 0 errors** over 1.07M
requests on an 8-CPU Windows laptop (roadmap gate: ≥ 30,000 RPS). Windows'
~0.5 ms timer granularity makes sub-millisecond percentiles such as p50
unreliable; throughput is measured over the full run and unaffected.

## API-Key Scopes

Tenant API keys carry an immutable scope: `read` (read-only endpoints) or
`full` (read + mutate; the default). Rotation preserves scope, so widening
access always requires issuing a new key. A valid key used beyond its scope
receives `403` with error code `forbidden`; a bad credential receives `401`.

```powershell
$headers = @{ "X-PolyForge-API-Key" = $fullKey }
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8080/v1/tenants/acme/api-keys `
  -Headers $headers `
  -ContentType "application/json" `
  -Body '{"name":"observer","scope":"read"}'
```

## Rate Limiting

The control plane enables a local token-bucket limiter by default:

```text
POLYFORGE_RATE_LIMIT_RPM=600
POLYFORGE_RATE_LIMIT_BURST=60
```

Limits are keyed by remote address for tenant-management routes, tenant API-key fingerprint for tenant-scoped routes, or remote address when no credential is present. Set either value to `0` only for isolated local debugging.

Structured JSON logs default to `info`, which emits one access-log line per
request; set `POLYFORGE_LOG_LEVEL` to `warn`, `error`, or `debug` to change
the threshold.

## PostgreSQL

With Docker installed, run the RLS integration suite:

```powershell
.\scripts\test-postgres.ps1
```

To run the service against the local PostgreSQL fixture:

```powershell
docker compose up -d --wait postgres
$env:POLYFORGE_POSTGRES_ADMIN_URL = "postgres://polyforge_admin@localhost:5432/polyforge?sslmode=disable"
$env:POLYFORGE_POSTGRES_APP_URL = "postgres://polyforge_app@localhost:5432/polyforge?sslmode=disable"
$env:POLYFORGE_ADMIN_KEY = "replace-for-local-use"
go run ./cmd/control-plane
```

The Compose database uses trust authentication and binds only to `127.0.0.1`; it is a test fixture, not a deployment configuration.

## Reproducing the research results

Every measured claim traces to a committed script and a results file
(single reading entry point: `research/analysis/RESULTS_MASTER.md`).

```bash
# Harness matrices (sim backend, runs anywhere)
cd eval && pip install -r requirements.txt
python -m harness.runner experiments/full.yaml --workers 4   # v1 headline
python scripts/validate_results.py experiments/full.yaml

# Statistical analysis, figures, exploratory weight sensitivity
cd ../research/analysis
python run_analysis.py && python sensitivity_j.py
python effect_sizes.py        # d_z + bootstrap CIs (the citable unit)
python planner_scaling.py     # joint-planner latency vs tenant count
python cache_hit_precision.py --conversations '<lmsys glob>'  # hit quality (gated data)
python forecast_azure.py      # second real trace (run etl_azure_llm.py first)

# Calibration (Phase 6): CPU congestion + GPU tier table
cd ../calibration && python measure_congestion.py            # ~35 min
# GPU half: push kaggle_tier_bench.py as a Kaggle GPU kernel (see file docstring)

# Live cluster smoke (Phase 7, needs Docker — a Codespace works out of the box)
bash scripts/phase7_kind_run.sh
```

Real datasets (BurstGPT v2.0, gated LMSYS-Chat-1M, Azure LLM inference
2024) are downloaded, never committed; the ETLs under `research/traces/`
are the committed artifacts.

**Evidence site:** every pre-registration, record and figure, with the gate
status of each, is published at https://mohaimin-8.github.io/polyforge/
(rebuilt from `main` on every push).

**Archived evaluation artifact (DOI):** the raw run databases, every
pre-registration and record, the live-campaign evidence and the IaC are
deposited at [https://doi.org/10.5281/zenodo.22801195](https://doi.org/10.5281/zenodo.22801195)
(Islam, 2026, CC BY 4.0; 1,023 files, SHA-256 manifest). Restoring its
DuckDB files into `eval/results/` enables the archive tier of
`docs/REPRODUCE.md`. Cite the dataset as:

> Islam, M. M. (2026). *PolyForge evaluation artifact: pre-registered
> controller-comparison campaigns (harness, baselines, raw results,
> live-run data, IaC)* [Dataset]. Zenodo. https://doi.org/10.5281/zenodo.22801195

## Limitations (honest boundaries)

- **Substrate.** All headline numbers are simulator-backend decision
  quality under the stated system model (`research/jcac_sim/model.py`),
  driven by real traces where claimed. The congestion form and tier
  ordering are calibrated against real inference servers
  (`research/calibration/`), and the model's *functional forms* — not
  just its parameters — were stress-tested by three pre-registered
  reruns of the full matrix (Wave 5): the measured latency model
  (a=0.86 + ρ-dependent p95/mean tail), a true mixture-percentile
  `ai_p95`, and tier-scaled work units. All rankings survived with
  narrowed margins and the SLO-parity claim held under a frozen
  non-inferiority margin (`RESULTS_LM_ADOPTION.md`,
  `RESULTS_MIXTURE_P95.md`, `RESULTS_TIER_WU.md`, DEFENSE_QA #24).
  Still unvaried: intra-interval demand is deterministic, and p95 is
  the finest latency statistic the sim reports.
- **Actuation clamps.** The project's own coordination-gap audit caught
  the published controller exceeding its declared per-interval move
  clamps through its second coordination sweep (±4 replicas / two cache
  levels where every baseline gets ±2 / one). The pre-registered
  adjudication rerun shows the advantage carried nothing — the
  clamp-fixed controller (`anchor_moves`) wins slightly *more* and is
  the quotable configuration; the published matrices remain the
  bit-reproducible record (`COORD_GAP.md`, `RESULTS_MOVE_CLAMP.md`,
  DEFENSE_QA #25).
- **SLO.** Two pre-registered attempts to beat tuned reactive scalers on
  raw violation failed and are published (v2 H1, v3 H1'). The earned
  claim is violation parity at −43…−48% cost, plus the confirmed
  forecast mechanism.
- **Latency percentile.** p95, not p99 — deliberate, documented deviation
  (`eval/README.md`); measured live percentiles can report both.
- **Cache.** The live control plane exercises per-tenant partitioning
  (the security result), not the semantic cache itself; semantic hit-rate
  numbers come from the committed protocol on real LMSYS-Chat-1M. Hit
  *quality* is measured too (`research/analysis/CACHE_PRECISION.md`,
  protocol frozen pre-run): at τ=0.85, response-agreement precision is
  0.313 overall (0.443 same-model, 0.353 first-turn) with 165.5
  incorrect hits per 1k queries — read against the proxy's own ceiling
  (near-duplicate prompts agree only 33.8%, so response stochasticity
  dominates the absolute level; the relative readings are the claim).
  Staleness/TTL is out of scope.
- **Live cluster.** The cluster backend is verified end-to-end for the
  HPA arm on kind; the PolyForge (operator/planner) arm is fully wired in
  code behind an executable actuation gate (`kubectl wait
  --for=condition=Applied` fails the run before any load if the operator
  never scaled the target) but has not yet run live. The eventual ordinal
  figure exercises the replica-control projection only — the replay data
  plane's cache/tier knobs are inert live, and the figure caption says so.
- **Latency granularity.** Request-level p95, not token-level TTFT/TPOT:
  continuous-batching dynamics belong to the llm-d/AIBrix-class actuation
  layer beneath PolyForge's portfolio decisions (`docs/RELATED_WORK.md`
  §4 maps each knob onto that layer).
- **Fairness altitude.** Jain over per-tenant SLO satisfaction measures
  capacity-allocation fairness at the control plane, not token-level
  service fairness à la VTC/OSDI '24; `vtc_replica` is a replica-level
  transplant, and the claims are scoped accordingly.
- **Cost model.** Two economies, deliberately separable: replica-hours as
  infrastructure ($0.048/replica-hr; metered live) and tier calls at
  market API ratios (1:10:100). Spot/MIG/fractional-GPU levers and
  multi-minute model pulls are out of scope; replica startup lag and cold
  caches are modeled in the realism ablation.
- **Fairness γ-term.** Published null: the joint controller absorbs
  interference without it; the term stays configurable, claims dropped.

The hard-question companion — construct validity of the overload cells,
p-value inflation, goalpost accusations, baseline strength, and the rest —
is `docs/DEFENSE_QA.md`, with evidence pointers per question.

## Long-Term Target

The production/research target includes:

- Go data-plane services
- PostgreSQL tenant isolation
- Redis semantic cache
- AI gateway with local/provider routing
- online workload classifier
- Kubernetes operator and CRDs
- controller/planner
- reproducible benchmark harness
- 1,800-run evaluation matrix
- thesis report, figures, slides, and defense material

See `docs/EXECUTION_PLAN.md` for the full program.
