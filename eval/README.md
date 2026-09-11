# PolyForge evaluation (M9: W33–W36)

Everything the paper's evaluation section is built from: a YAML-driven
experiment harness, five tuned baselines, the run matrices, the raw
results, and the validation tooling. One command runs a full experiment
from a clean state; every experiment lands in one DuckDB file with the
same schema.

```
eval/
  harness/        experiment engine: config, workloads, systems, backends, DB
  experiments/    smoke.yaml (50 runs) · full.yaml (1,800) · ablations.yaml (500)
  baselines/      tune.py grid search · tuned.yaml · grids/*.csv · TUNING.md
  scripts/        validate_results · spot_check · ks_check · archive_zenodo
  infra/terraform Hetzner 4-node k3s cluster for the cloud runs (W35a)
  results/        DuckDB databases + DAILY.md burn log (gitignored except logs)
  tests/          harness unit tests (pytest)
```

## Quickstart

```bash
cd eval
pip install -r requirements.txt
python -m pytest tests -q                     # harness self-test
python -m harness.runner experiments/smoke.yaml --dry-run
python -m harness.runner experiments/smoke.yaml --workers 4
python scripts/validate_results.py experiments/smoke.yaml
python scripts/spot_check.py experiments/smoke.yaml --n 10
```

## The experiment model

An experiment YAML names the factors; the harness expands the cross
product into runs:

    systems × workload classes × tenant mixes × cluster sizes × reps

- **Systems** — `jcac` (PolyForge) plus baselines `hpa`, `keda`, `firm`,
  `static`, `gptcache`, plus ablations `jcac_no{classifier,joint,eviction,fairness}`.
  Definitions: `harness/systems.py`.
- **Workload classes** — the five W26 taxonomy classes made executable
  (`harness/workloads.py`).
- **Tenant mixes** — who shares the cluster: `uniform`, `premium_heavy`,
  `besteffort_heavy`, `whale`.
- **Cluster sizes** — how much cluster there is to share: `small`,
  `medium`, `large`.
- **Reps** — independent Poisson-jittered draws of the same scenario.

Every run has a deterministic `run_id` (hash of all identity factors) and
a seed derived from it: replaying a run_id reproduces its metrics exactly,
resume skips already-valid runs, and no result can be silently mixed
across schema versions.

## Result schema (DuckDB)

| table | grain | contents |
|---|---|---|
| `runs` | one row per attempted run | identity, `status` (valid/invalid/failed), attempts, error, provenance |
| `metrics` | one row per **valid** run | `total_cost_usd`, `mean_violation`, `violation_step_share`, `mean_jain`, `cache_hit_rate`, `crud_p95_ms`, `ai_p95_ms` |
| `timeseries` | per step × tenant (first rep only) | replicas, cache_mb, tier, cost, violation, p95s |

Analysis must select `status = 'valid'` via the `metrics` table; failed or
invalid runs keep their error strings in `runs` and never reach analysis.

## Backends

- **`sim`** (verified) — replays through `research/jcac_sim`, the shared
  system model every controller is scored against. Runs anywhere; this is
  what produced the committed result databases.
- **`cluster`** (**verified live, hpa arm** — session 16d smoke: valid run,
  metrics match the replay burn exactly; jcac arm gated on the operator
  wiring) — provisions a kind
  cluster per run, deploys the Helm chart variant, replays demand with a
  generated k6 script, and collects metrics from the control plane.
  Requires Docker/kind/kubectl/helm/k6 (`harness.cluster_backend.preflight()`
  tells you what is missing). A Docker-capable environment is one click away:
  `.devcontainer/` gives GitHub Codespaces every preflight tool, and
  `scripts/phase7_kind_run.sh` is the whole runbook (build image → side-load
  into kind → run `experiments/phase7_live.yaml`). Integration-point status
  for the first live run:
  1. `control-plane eval-export` — **DONE** (session 16b, unit-tested):
     dumps the pod's observed metrics in the schema above. The infra-cost
     component is injected via `--infra-cost-usd` because replica-hours are
     not visible in-pod; wiring that flag from the harness lands with the
     first live run.
  2. Helm values used by `HELM_VALUES_BY_SYSTEM` wired into the chart —
     **hpa + jcac wired (sessions 16d, 17)**: `autoscaling.hpa.*` is a real
     autoscaling/v2 HPA (metrics-server installed per run), the eval base
     values run the pod self-contained (SQLite on emptyDir, no ingress,
     limiter opened), and the hpa arm is verified end-to-end
     (`experiments/phase7_smoke.yaml`). The `jcac` arm (session 17,
     not yet run live): `operator_install_plan()` installs the operator
     chart with the planner, creates per-tenant Tenant/Policy/Budget CRs
     mirroring the sim world, and ends with `kubectl wait
     --for=condition=Applied` on every Policy — the honesty gate is
     executable, so a run in which the operator never scaled the target
     fails before any load. Capacity parity is a cell property: every arm
     starts at the sim's initial world (2 replicas x tenants) under the
     cell's cluster-size replica ceiling. Still open: KEDA/FIRM arms,
     `cache.policy`.
     **Scope disclosure for the ordinal figure**: the replay data plane
     burns fixed CPU per request kind, so the cache-size and model-tier
     knobs are inert live — the live jcac-vs-hpa comparison validates the
     replica-control projection of the joint controller, and its figure
     caption must say so (docs/DEFENSE_QA.md §2).
  3. `/v1/tenants/{tenant_id}/workloads/replay` data-plane endpoint —
     **DONE (session 16d)**: burns CPU per the sim's work-unit table so
     real autoscalers see genuine load, records telemetry for eval-export;
     `execute()` provisions tenants + API keys via the chart's admin
     Secret, exports `TOKEN_<tenant>` to k6 over a port-forward, meters
     replica-seconds during the load window, and injects the infra cost
     into eval-export (closing point 1's flag wiring).

## Latency metric note (deliberate deviation)

The roadmap says "p99 latency"; the model's latency estimator produces
p95 (`P95_FACTOR` in `research/jcac_sim/model.py`), so the sim-backend
metric is **p95** and is reported as such everywhere. The cluster backend
measures real percentiles and can report both; do not silently relabel.

Latency is also **request-level**, not token-level: the model does not
represent continuous-batching dynamics (TTFT vs TPOT, heavy-tailed decode
lengths, head-of-line blocking inside an engine). PolyForge's claims are
scoped to portfolio-level capacity decisions; token-level latency belongs
to the llm-d-class actuation layer underneath (docs/RELATED_WORK.md §4,
docs/DEFENSE_QA.md §9/§11).

## Reproducing the committed results

```bash
python -m harness.runner experiments/full.yaml --workers 4       # ~1 h wall
python -m harness.runner experiments/ablations.yaml --workers 4  # ~25 min
python scripts/validate_results.py experiments/full.yaml
python scripts/spot_check.py experiments/full.yaml --n 10
python scripts/ks_check.py
python scripts/archive_zenodo.py    # ready-to-upload Zenodo bundle
#   creators come from eval/zenodo_creators.json ([{"name": "Family, Given", ...}]);
#   without it the deposit carries a placeholder and the build says so
```

Statistical analysis and figures: `research/analysis/` (W36).

One-command reproduction of every generated record and figure from the
committed data (never touching the frozen originals): `python
scripts/reproduce.py` from the repo root — tiers and mechanics in
`docs/REPRODUCE.md`.
