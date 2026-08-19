"""Cluster backend: the same RunSpec executed against a real kind cluster.

Pipeline per run (W33 step list):
  1. provision a fresh kind cluster sized from `cluster_size`
  2. deploy the PolyForge variant for `system` via the Helm chart
  3. generate a k6 script replaying this run's demand buckets and drive it
  4. collect metrics from the control plane's Prometheus endpoints
  5. tear the cluster down (deterministic teardown: delete by fixed name)

The backend is command orchestration only — every step is a subprocess
with a timeout, and any non-zero exit fails the run so the runner records
it (retry, then `failed`). Requires Docker, kind, kubectl, helm, and k6 on
PATH; `preflight()` reports exactly what is missing instead of crashing
mid-provision.

Status: VERIFIED live for the hpa arm (session 16d, GitHub Codespace):
phase7_smoke.yaml recorded a valid run with metrics matching the replay
endpoint's physics. The jcac arm is wired in code (session 17: operator
chart install, Tenant/Policy/Budget CRs, admin-key demand plumbing, and a
`kubectl wait --for=condition=Applied` actuation gate before any load) but
has NOT run live yet — phase7_jcac_smoke.yaml is the first thing the next
cluster session should execute.
"""

from __future__ import annotations

import json
import os
import sys
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from model import TenantState  # research/jcac_sim via harness sys.path

from .config import RunSpec
from . import workloads

REQUIRED_TOOLS = ("docker", "kind", "kubectl", "helm", "k6")
CLUSTER_NAME = "polyforge-eval"  # fixed: teardown is idempotent by name
NODES_BY_SIZE = {"small": 2, "medium": 4, "large": 6}

# The chart's default image (deploy/helm/polyforge/values.yaml). The image is
# built locally by scripts/phase7_kind_run.sh and side-loaded into the kind
# nodes — it is not published to any registry, so without the explicit
# `kind load` step every pod would sit in ImagePullBackOff.
CONTROL_PLANE_IMAGE = "polyforge/control-plane:dev"
OPERATOR_IMAGE = "polyforge/operator:dev"
PLANNER_IMAGE = "polyforge/planner:dev"

# Systems that deploy the operator/planner control loop live. The full jcac
# arm and its two-knob ablations (cache-only / tier-only) all run the operator
# — the ablations differ only in which knobs their Policy CRs pin (see
# operator_crs / _arm_knob_bounds). The Wave 4 replica-only arm is NOT here: it
# is reactive HPA with cache/tier held by push_default_knobs, like hpa.
OPERATOR_SYSTEMS = {"jcac", "cache-only", "tier-only"}

REPO_ROOT = Path(__file__).resolve().parents[2]
ADMIN_SECRET_NAME = "polyforge-admin"  # carries ADMIN_KEY for the operator

# Opt-in shared-Postgres eval data plane. The default SQLite path stores
# telemetry per-pod (emptyDir); under replica scaling a single-pod eval-export
# then reads an unrepresentative slice (p95/p99 -> 0). With POLYFORGE_EVAL_SHARED_PG=1
# every control-plane replica writes one Postgres and eval-export sees all
# events. Default off so the SQLite path and the harness tests are unchanged.
EVAL_SHARED_PG = os.environ.get("POLYFORGE_EVAL_SHARED_PG") == "1"
PG_MANIFEST = REPO_ROOT / "eval" / "harness" / "manifests" / "postgres-eval.yaml"
NATS_MANIFEST = REPO_ROOT / "eval" / "harness" / "manifests" / "nats-eval.yaml"
# Set on the operator so PlanRunner.Audit is non-nil. Without it every
# audit record is dropped and SK-H2 scores an empty stream against an
# empty count, which is what it did for four attempts.
NATS_URL = "nats://nats.polyforge.svc:4222"
PG_ADMIN_URL = "postgres://polyforge_admin@postgres:5432/polyforge?sslmode=disable"
PG_APP_URL = "postgres://polyforge_app@postgres:5432/polyforge?sslmode=disable"

# Opt-in three-knob live plane (PREREG_WAVE4_LIVE_PLANE.md §Substrate 2-3).
# With POLYFORGE_EVAL_LIVE_AI=1 the chart deploys the AI gateway, AI-kind
# traffic drives its real semantic cache + tier routing (k6 posts prompts
# with the cell's reuse structure), and non-operator arms hold the sim's
# initial knobs as their fixed posture. Requires the shared-PG data plane
# (gateway telemetry must land in the store eval-export reads) and a
# POLYFORGE_EVAL_TIER_BACKENDS JSON naming the provisioned host's model
# servers — without real tier backends the tier knob is inert and the run
# would void WL-H2, so the harness refuses to build the plan instead.
EVAL_LIVE_AI = os.environ.get("POLYFORGE_EVAL_LIVE_AI") == "1"
# WP14 attempt 5. `execute` ends `eval-export` -> `kind delete cluster`, and
# the finally block deletes again on every exit path. Attempt 4's end-of-run
# extraction lost that race by seconds, and hours 17-24 of a 24 h run went
# with the Postgres that held them -- unrecoverable, because the cluster is
# where the evidence lives until eval-export has read it. With bucketed export
# this is belt-and-braces rather than load-bearing, which is the right
# relationship to have with a teardown race. Default stays delete-on-exit so
# CI never leaks clusters.
EVAL_KEEP_CLUSTER = os.environ.get("POLYFORGE_EVAL_KEEP_CLUSTER") == "1"
# Marker file asserting a load window is open. WP14 attempt 4 had no such
# rule: a `go test` run on the same laptop compiled the operator package
# during the soak and triggered the first restart cascade. The soak has no
# CPU, memory or I/O isolation from the session watching it -- one laptop, one
# kernel, one disk -- and "does not touch Kubernetes objects" is not the same
# claim as "does not affect the run". A marker is enforceable where a note in
# a document is not; scripts/guard-no-local-compute.sh reads it.
SOAK_MARKER = REPO_ROOT / ".soak-running"
# Bucket width for eval-export's time-resolved output. SK-H3 is frozen on
# hour buckets; a short validation stage sets 60 so a 10-minute smoke still
# produces ten scoreable windows.
EVAL_BUCKET_SECONDS = int(os.environ.get("POLYFORGE_EVAL_BUCKET_SECONDS", "3600"))
TIER_BACKENDS_JSON = os.environ.get("POLYFORGE_EVAL_TIER_BACKENDS", "")
GATEWAY_IMAGE = "polyforge/ai-gateway:dev"
GATEWAY_LOCAL_PORT = 18081
# WP14 attempt 5: the CRUD load path is a NodePort published on a kind host
# port, NOT `kubectl port-forward`. Attempt 4 lost a full 24 h sitting to the
# forward, which resolves to ONE pod when it starts and never follows the
# Service: all ~300 req/s landed on a single replica of sixteen, drove it to
# its 256 Mi limit, and each OOM kill took the load path down with it -- 282
# restarts, 4.567% failed requests, run INVALID. A NodePort is balanced by
# kube-proxy in the kernel across every ready endpoint, so no single replica
# carries the run and there is no userspace proxy process left to die.
NODE_PORT = 30080
AI_KINDS = ("chat", "embed", "agent")  # mirrors AI_KINDS in model.py
# Per-tenant prompt-pool sizes: the cell's prompt-reuse structure. A finite
# pool makes the realized hit rate sensitive to the cache-MB knob; classes
# not listed get unique prompts (uncacheable by construction). Pool prompts
# are high-entropy token strings so distinct prompts never collide under
# the deployed lexical embedder (the wire-attack fixture's lesson).
PROMPT_POOL_BY_CLASS = {
    "ai_cacheable": 64,
    "flash_ai": 128,
    "joint_stress": 96,
    "tier_mixed": 4096,
}

# Ablation/baseline toggles per system. Live-wired today: the hpa row
# (autoscaling.hpa.* is a real HPA) and the jcac row (via OPERATOR_SYSTEMS
# -> operator chart install; the polyforge-chart planner.*/classifier.*
# keys here are sim-era placeholders the chart ignores). The remaining
# rows (keda/firm/cache.policy/jcac_no*) are NOT wired into any chart —
# running them on the cluster backend deploys a plain pod under a
# baseline's name, so keep them sim-only until their toggles exist.
HELM_VALUES_BY_SYSTEM = {
    "jcac": {"planner.enabled": "true", "classifier.enabled": "true"},
    # Wave 4 arms: cache-only / tier-only run the same operator chart as jcac
    # (they ARE jcac, with two knobs pinned by their Policy CRs — the chart is
    # knob-agnostic). replica-only is reactive HPA with cache/tier held fixed
    # by push_default_knobs, exactly like hpa.
    "cache-only": {"planner.enabled": "true", "classifier.enabled": "true"},
    "tier-only": {"planner.enabled": "true", "classifier.enabled": "true"},
    "replica-only": {"planner.enabled": "false", "autoscaling.hpa.enabled": "true"},
    "hpa": {"planner.enabled": "false", "autoscaling.hpa.enabled": "true"},
    "keda": {"planner.enabled": "false", "autoscaling.keda.enabled": "true"},
    "firm": {"planner.enabled": "false", "autoscaling.firm.enabled": "true"},
    "static": {"planner.enabled": "false"},
    "gptcache": {"planner.enabled": "false", "cache.policy": "lru"},
    "jcac_noclassifier": {"planner.enabled": "true", "classifier.enabled": "false"},
    "jcac_nojoint": {"planner.enabled": "false", "autoscaling.layered.enabled": "true"},
    "jcac_noeviction": {"planner.enabled": "true", "cache.policy": "lru"},
    "jcac_nofairness": {"planner.enabled": "true", "planner.gamma": "0"},
}


class BackendUnavailable(RuntimeError):
    pass


def preflight() -> list[str]:
    """Names of required tools missing from PATH (empty = ready)."""
    return [tool for tool in REQUIRED_TOOLS if shutil.which(tool) is None]


def kind_config(cluster_size: str) -> str:
    nodes = NODES_BY_SIZE[cluster_size]
    lines = [
        "kind: Cluster",
        "apiVersion: kind.x-k8s.io/v1alpha4",
        "nodes:",
        "  - role: control-plane",
        # Publishes the NodePort on the host so k6 can drive the Service with
        # no proxy in between. kind cannot add a port mapping to a running
        # cluster (kubernetes-sigs/kind#2720), so it has to be here at create
        # time or not at all.
        "    extraPortMappings:",
        f"      - containerPort: {NODE_PORT}",
        f"        hostPort: {NODE_PORT}",
        "        protocol: TCP",
    ]
    lines += ["  - role: worker"] * (nodes - 1)
    return "\n".join(lines) + "\n"


def nodeport_service() -> str:
    """A NodePort in front of the control-plane, for the eval load path only.

    Deliberately a separate object from the chart's ClusterIP Service rather
    than a patch of it: this is evaluation scaffolding and must never ship in
    the product chart. The selector mirrors `polyforge.selectorLabels` from
    deploy/helm/polyforge/templates/_helpers.tpl.

    externalTrafficPolicy stays at the default (Cluster), which is what makes
    this work: traffic entering the one published node is spread by kube-proxy
    across endpoints on *every* node, not just pods local to the entry point.
    """
    return f"""apiVersion: v1
kind: Service
metadata:
  name: polyforge-control-plane-nodeport
  namespace: polyforge
  labels:
    app.kubernetes.io/managed-by: polyforge-eval
spec:
  type: NodePort
  selector:
    app.kubernetes.io/name: polyforge-control-plane
    app.kubernetes.io/instance: polyforge
  ports:
    - name: http
      port: 80
      targetPort: http
      nodePort: {NODE_PORT}
"""


def kind_mix(buckets, tid) -> list[tuple[str, float]]:
    """Per-tenant request-kind distribution averaged over the run's buckets,
    as (kind, cumulative probability) pairs for client-side sampling."""
    totals: dict[str, float] = {}
    for bucket in buckets:
        for kind, rate in bucket[tid].rps.items():
            if rate > 0:
                totals[kind] = totals.get(kind, 0.0) + rate
    grand = sum(totals.values()) or 1.0
    pairs, acc = [], 0.0
    for kind in sorted(totals):
        acc += totals[kind] / grand
        pairs.append((kind, round(acc, 6)))
    if pairs:
        pairs[-1] = (pairs[-1][0], 1.0)
    return pairs


def prompt_pools(run: RunSpec, tenant_ids) -> dict:
    """Deterministic per-tenant prompt pools implementing the cell's
    prompt-reuse structure (PREREG_WAVE4_LIVE_PLANE.md §Substrate 3).
    Pool prompts are high-entropy token strings, so two *distinct* prompts
    never collide under the deployed lexical embedder — only a re-sent pool
    prompt hits, which is what makes the hit rate a function of pool size
    and cache budget. A class with no pool maps to None: every prompt is
    unique, uncacheable by construction."""
    import hashlib

    size = PROMPT_POOL_BY_CLASS.get(run.workload)
    pools = {}
    for tid in tenant_ids:
        if not size:
            pools[tid] = None
            continue
        prompts = []
        for i in range(size):
            tokens = [
                hashlib.sha256(f"{run.seed}:{tid}:{i}:{w}".encode()).hexdigest()[:8]
                for w in range(12)
            ]
            prompts.append(" ".join(tokens))
        pools[tid] = prompts
    return pools


def k6_script(run: RunSpec, interval_s: int = 10) -> str:
    """One k6 scenario per tenant, ramping-arrival-rate stages replaying
    this run's demand buckets. The tenant identity travels as its API key
    (provisioned by execute() before k6 starts); each request samples its
    kind from the tenant's demand mix. CRUD kinds always drive the control
    plane's replay endpoint; with the live-AI plane enabled, AI kinds drive
    the gateway's chat endpoint with the cell's prompt-reuse structure so
    the cache and tier knobs bite on real requests."""
    tenant_ids, buckets, _, _ = workloads.build(
        run.workload, run.tenant_mix, run.cluster_size, run.seed, run.steps
    )
    scenarios = {}
    mixes = {}
    for tid in tenant_ids:
        stages = [
            {"target": max(1, round(bucket[tid].total_rps())), "duration": f"{interval_s}s"}
            for bucket in buckets
        ]
        # VU pool sizing (WP14, session 38). `preAllocatedVUs` was 50, sized
        # for the AVERAGE request: this cell peaks at ~94 req/s per tenant and
        # `http_req_duration` averages 23 ms, so Little's law says ~2 VUs.
        # But the tail reaches 3.4 s, and at peak arrival that demands ~322
        # VUs for as long as the excursion lasts. k6 then has to allocate VUs
        # *dynamically*, and drops iterations while it does — 647 of them in
        # the WP14 smoke run, which `check_k6_delivery`'s zero-drop threshold
        # correctly rejected.
        #
        # 150 is not tuned to make a gate pass: the smoke run's own pool grew
        # to 968 VUs across 8 tenants (peak 832 in use), so this pre-allocates
        # roughly what the load demonstrably needed instead of making k6
        # discover it mid-excursion. maxVUs keeps generous headroom above it.
        scenarios[f"tenant_{tid}"] = {
            "executor": "ramping-arrival-rate",
            "startRate": stages[0]["target"],
            "timeUnit": "1s",
            "preAllocatedVUs": 150,
            "maxVUs": 1000,
            "stages": stages,
            "env": {"TENANT": tid},
        }
        mixes[tid] = kind_mix(buckets, tid)
    # Thresholds make k6 itself exit non-zero on a bad run. Without them k6
    # exits 0 even when every request failed, and the generated script does
    # not check response status — so a dead port-forward, a bad token or a
    # 429 storm is indistinguishable from a clean run, and produces a
    # beautifully low cost and violation from traffic that never landed.
    options = {
        "scenarios": scenarios,
        "discardResponseBodies": True,
        "thresholds": {
            # >1% failed requests is a broken run, not a measurement.
            #
            # WP14 attempt 5: `abortOnFail` makes k6 stop the moment the run is
            # already unscoreable, instead of driving load for the remaining
            # hours to produce a number the delivery gate will reject anyway.
            # Attempt 4 spent 6.5 h in that state -- the OOM cycle started
            # around T+17.5 h and the run continued to 24 h to be rejected at
            # 4.567%. `delayAbortEval` gives the cluster its startup window
            # first, so a cold-start blip cannot abort a healthy run.
            "http_req_failed": [
                {"threshold": "rate<0.01", "abortOnFail": True,
                 "delayAbortEval": "60s"}
            ],
            # The load generator must not be the bottleneck: dropped
            # iterations mean k6 could not keep up with its own arrival rate,
            # so the tenant did not receive the demand the cell specifies.
            "dropped_iterations": [
                {"threshold": "count<1", "abortOnFail": True,
                 "delayAbortEval": "60s"}
            ],
        },
    }
    pools = prompt_pools(run, tenant_ids) if EVAL_LIVE_AI else {}
    return f"""// generated by eval/harness for run {run.run_id} — do not edit
import http from 'k6/http';

export const options = {json.dumps(options, indent=2)};

const BASE = __ENV.POLYFORGE_URL || 'http://localhost:8080';
const GATEWAY = __ENV.POLYFORGE_GATEWAY_URL || '';
const LIVE_AI = {json.dumps(EVAL_LIVE_AI)};
const AI_KINDS = {json.dumps(list(AI_KINDS))};
const MIX = {json.dumps(mixes, indent=2)};
const POOLS = {json.dumps(pools)};

function sampleKind(tenant) {{
  const r = Math.random();
  for (const [kind, cum] of MIX[tenant]) {{
    if (r <= cum) return kind;
  }}
  return MIX[tenant][MIX[tenant].length - 1][0];
}}

// Pool sampling is power-law (favors low indices) so the working set has a
// hot head: the realized hit rate then responds to the cache-MB knob, which
// is the WL-H2 liveness property. No pool -> a unique high-entropy prompt.
function promptFor(tenant) {{
  const pool = POOLS[tenant];
  if (!pool) {{
    let tokens = [];
    for (let w = 0; w < 12; w++) tokens.push(Math.random().toString(16).slice(2, 10));
    return tokens.join(' ');
  }}
  const idx = Math.floor(pool.length * Math.pow(Math.random(), 2.5));
  return pool[Math.min(idx, pool.length - 1)];
}}

export default function () {{
  const tenant = __ENV.TENANT;
  const token = __ENV[`TOKEN_${{tenant}}`];
  const kind = sampleKind(tenant);
  if (LIVE_AI && GATEWAY && AI_KINDS.includes(kind)) {{
    http.post(`${{GATEWAY}}/v1/tenants/${{tenant}}/ai/chat`,
      JSON.stringify({{messages: [{{role: 'user', content: promptFor(tenant)}}]}}),
      {{headers: {{'X-PolyForge-API-Key': token, 'Content-Type': 'application/json'}}}});
    return;
  }}
  http.post(`${{BASE}}/v1/tenants/${{tenant}}/workloads/replay`,
    JSON.stringify({{run: '{run.run_id}', kind: kind}}),
    {{headers: {{'X-PolyForge-API-Key': token, 'Content-Type': 'application/json'}}}});
}}
"""


# Every eval install runs the pod self-contained: SQLite on an emptyDir
# (the chart deploys no PostgreSQL), no ingress (k6 rides a port-forward),
# and the platform rate limiter out of the way — the run's arrival rates
# are the experiment, not a policy under test.
HELM_EVAL_BASE_VALUES = {
    "postgres.adminURL": "",
    "postgres.appURL": "",
    # The eval cluster deploys no Redis, and the chart's default
    # `redis://redis:6379/0` (deploy/helm/polyforge/values.yaml:20) therefore
    # points at a service that does not resolve. The limiter is documented to
    # "degrade to the local bucket" on a Redis error -- but the DIAL is what
    # fails, five attempts behind a DNS timeout, so every rate-limited request
    # blocks for seconds first. `/healthz` is exempt from the limiter
    # (test_rate_limit_bypasses_healthz), which is exactly why WP8a's dry-run
    # saw health checks answer instantly while POST /v1/tenants timed out.
    # Empty disables Redis, as the chart's own comment says.
    "redis.url": "",
    "ingress.enabled": "false",
    "rateLimit.requestsPerMinute": "1000000",
    "rateLimit.burst": "100000",
}


def _arm_knob_bounds(system: str, initial: "TenantState", size) -> tuple:
    """Per-arm Policy-CRD knob bounds for the Wave 4 live plane
    (PREREG_WAVE4_LIVE_PLANE.md §Arms). Returns
    ((replicaMin, replicaMax), (cacheMin, cacheMax), (tierMin, tierMax)).

    A frozen knob is pinned min==max at the sim's initial world; a free knob
    spans the full envelope (cacheMax 0 = no ceiling, the cluster limit still
    binds; tiers none..large). This mirrors the sim's SystemSpec.knob_freeze so
    the live ablations pin exactly the knobs their sim counterparts do:
      - jcac          all three free
      - replica-only  cache + tier frozen, replicas free
      - cache-only    replicas + tier frozen, cache free
      - tier-only     replicas + cache frozen, tier free

    WP8a (session 38) found `replica-only` falling through to the jcac branch,
    so its CRs rendered **byte-identical to jcac's** — the full three-knob
    envelope on an arm the prereg describes as "cache/tier held fixed". The
    pin was never actually absent: that arm runs `planner.enabled=false` and
    takes its knobs from `push_default_knobs`, so nothing moved them and no
    measurement was affected (the live plane has not been run). But the
    invariant was enforced only by a helm value and a comment, invisible in
    the CRs and uncheckable by the apiserver. It is declared here as well, so
    the arm's own manifests state what the arm is.
    """
    free_r = (1, size.replica_max)
    frozen_r = (initial.replicas, initial.replicas)
    free_c = (0, 0)
    frozen_c = (initial.cache_mb, initial.cache_mb)
    free_t = ("none", "large")
    frozen_t = (initial.tier, initial.tier)
    if system == "replica-only":
        return free_r, frozen_c, frozen_t
    if system == "cache-only":
        return frozen_r, free_c, frozen_t
    if system == "tier-only":
        return frozen_r, frozen_c, free_t
    return free_r, free_c, free_t  # jcac (full joint controller)


def operator_crs(run: RunSpec) -> str:
    """Tenant/Policy/Budget CR manifests for the jcac arm, one triple per
    eval tenant, all pointed at the shared eval Deployment (the reconciler
    sums per-tenant contributions onto it).

    Order matters: Policies and Budgets come BEFORE Tenants. The tenant
    reconciler's ensureDefaults creates missing defaults but leaves
    existing objects alone, so applying the eval spec first wins the race
    against a default Policy that targets a per-tenant gateway which does
    not exist in the eval cluster.

    Every number mirrors the sim so live and sim arms start from the same
    world: initial state from model.TenantState defaults, per-tenant
    replica ceiling and budgets from the run's own workloads.build()
    output.
    """
    tenant_ids, _, configs, _ = workloads.build(
        run.workload, run.tenant_mix, run.cluster_size, run.seed, run.steps
    )
    size = workloads.CLUSTER_SIZES[run.cluster_size]
    initial = TenantState()
    (rmin, rmax), (cmin, cmax), (tmin, tmax) = _arm_knob_bounds(
        run.system, initial, size)
    docs = []
    for tid in tenant_ids:
        config = configs[tid]
        # Knob bounds encode the arm's freeze (min==max pins a knob). jcac
        # emits the full envelope (identical to the pre-bounds CR modulo the
        # explicit bound lines); cache-only / tier-only pin two knobs each.
        docs.append(f"""apiVersion: polyforge.io/v1alpha1
kind: Policy
metadata:
  name: {tid}
spec:
  tenantRef: {tid}
  targetDeployment: polyforge/polyforge-control-plane
  replicas: {initial.replicas}
  replicaMin: {rmin}
  replicaMax: {rmax}
  cacheSizeMB: {initial.cache_mb}
  cacheSizeMBMin: {cmin}
  cacheSizeMBMax: {cmax}
  modelTier: {initial.tier}
  modelTierMin: {tmin}
  modelTierMax: {tmax}""")
        docs.append(f"""apiVersion: polyforge.io/v1alpha1
kind: Budget
metadata:
  name: {tid}
spec:
  tenantRef: {tid}
  hourlyCapMilliUSD: {int(round(config.hourly_budget_usd * 1000))}
  fairnessWeightPermille: {int(round(config.fairness_weight * 1000))}""")
    for tid in tenant_ids:
        config = configs[tid]
        docs.append(f"""apiVersion: polyforge.io/v1alpha1
kind: Tenant
metadata:
  name: {tid}
spec:
  displayName: {tid}
  isolationMode: pool
  sloClass: {config.slo_class}""")
    return "\n---\n".join(docs) + "\n"


def operator_install_plan(run: RunSpec, workdir: Path) -> list[list[str]]:
    """Subprocess steps that arm the live jcac control loop, executed after
    the control plane is up and before k6 drives load. The final `kubectl
    wait` is the honesty gate made executable: the run fails before any
    load if the operator has not actually scaled the target Deployment
    (Applied=True), so a fixed-replica pod can never be recorded under
    PolyForge's name."""
    size = workloads.CLUSTER_SIZES[run.cluster_size]
    return [
        ["kubectl", "--namespace", "polyforge", "create", "secret", "generic",
         ADMIN_SECRET_NAME, f"--from-literal=admin-key={ADMIN_KEY}"],
        ["helm", "install", "polyforge-operator",
         str(REPO_ROOT / "deploy" / "helm" / "polyforge-operator"),
         "--namespace", "polyforge", "--wait", "--timeout", "300s",
         "--set=fullnameOverride=polyforge-operator",
         "--set=operator.leaderElect=false",
         f"--set=operator.natsURL={NATS_URL}",
         "--set=operator.image.repository=polyforge/operator",
         "--set=operator.image.tag=dev",
         "--set=planner.image.repository=polyforge/planner",
         "--set=planner.image.tag=dev",
         "--set=features.url=http://polyforge-control-plane.polyforge.svc:80",
         f"--set=features.adminKeySecret.name={ADMIN_SECRET_NAME}",
         f"--set=planner.limits.replicas={size.limits_replicas}",
         f"--set=planner.limits.cacheMB={size.limits_cache_mb}",
         # Live-AI plane: the operator pushes each Policy's cache/tier knobs
         # to the gateway, and the Applied actuation gate covers them.
         *([f"--set=gateway.adminURL=http://polyforge-ai-gateway.polyforge.svc:80"]
           if EVAL_LIVE_AI else [])],
        ["kubectl", "apply", "-f", str(workdir / "operator-crs.yaml")],
        ["kubectl", "wait", "--for=condition=Applied", "policies.polyforge.io",
         "--all", "--timeout=180s"],
    ]


def command_plan(run: RunSpec, workdir: Path) -> list[list[str]]:
    """The exact subprocess sequence for one run, in order. Split out so
    tests and --dry-run can inspect it without Docker."""
    if EVAL_LIVE_AI and not EVAL_SHARED_PG:
        raise RuntimeError(
            "POLYFORGE_EVAL_LIVE_AI=1 requires POLYFORGE_EVAL_SHARED_PG=1: "
            "gateway telemetry must land in the store eval-export reads, or "
            "every AI metric would silently read zero"
        )
    if EVAL_LIVE_AI and not TIER_BACKENDS_JSON:
        raise RuntimeError(
            "POLYFORGE_EVAL_LIVE_AI=1 requires POLYFORGE_EVAL_TIER_BACKENDS "
            "(tier -> model-server JSON): without real tier backends the tier "
            "knob is inert and the run would void WL-H2 "
            "(PREREG_WAVE4_LIVE_PLANE.md preflight gate)"
        )
    n_tenants = len(workloads.TENANT_MIXES[run.tenant_mix])
    size = workloads.CLUSTER_SIZES[run.cluster_size]
    values = {
        **HELM_EVAL_BASE_VALUES,
        # Capacity parity across arms is a property of the cell, not the
        # system: every arm starts from the sim's initial world (2 replicas
        # per tenant on the shared data plane) and may never exceed the
        # cell's cluster-size replica ceiling — the same bounds the sim
        # enforces via ClusterLimits.
        "replicaCount": str(n_tenants * TenantState().replicas),
        "autoscaling.hpa.maxReplicas": str(size.limits_replicas),
        **HELM_VALUES_BY_SYSTEM[run.system],
    }
    if EVAL_LIVE_AI:
        # The tierBackends JSON travels via the gw-values.yaml file (-f):
        # its braces/commas would be mangled by --set parsing.
        values["gateway.enabled"] = "true"
        values["gateway.image.repository"] = "polyforge/ai-gateway"
        values["gateway.image.tag"] = "dev"
    if EVAL_SHARED_PG:
        # URLs go via a values file (-f) to avoid --set '=' escaping; drop the
        # SQLite-mode empties so they cannot override that file.
        values.pop("postgres.adminURL", None)
        values.pop("postgres.appURL", None)
    set_flags = [f"--set={k}={v}" for k, v in sorted(values.items())]
    live_operator = run.system in OPERATOR_SYSTEMS
    plan = [
        ["kind", "delete", "cluster", "--name", CLUSTER_NAME],  # idempotent pre-clean
        ["kind", "create", "cluster", "--name", CLUSTER_NAME,
         "--config", str(workdir / "kind.yaml"), "--wait", "120s"],
        ["kind", "load", "docker-image", CONTROL_PLANE_IMAGE, "--name", CLUSTER_NAME],
    ]
    if EVAL_LIVE_AI:
        plan += [["kind", "load", "docker-image", GATEWAY_IMAGE, "--name", CLUSTER_NAME]]
    if live_operator:
        plan += [
            ["kind", "load", "docker-image", OPERATOR_IMAGE, "--name", CLUSTER_NAME],
            ["kind", "load", "docker-image", PLANNER_IMAGE, "--name", CLUSTER_NAME],
        ]
    plan += [
        # kind ships no metrics-server; without it the HPA arm reads no CPU
        # and silently never scales. --kubelet-insecure-tls is the standard
        # kind accommodation (kubelets use self-signed certs).
        ["kubectl", "apply", "-f",
         "https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml"],
        ["kubectl", "--namespace", "kube-system", "patch", "deployment",
         "metrics-server", "--type=json",
         "-p", '[{"op":"add","path":"/spec/template/spec/containers/0/args/-",'
               '"value":"--kubelet-insecure-tls"}]'],
        ["kubectl", "--namespace", "kube-system", "rollout", "status",
         "deployment/metrics-server", "--timeout=180s"],
    ]
    if live_operator:
        # The audit backbone, stood up before the operator chart: the operator
        # calls events.Connect at startup and os.Exit(1)s if the broker is
        # unreachable, so ordering here is load-bearing rather than tidy.
        plan += [
            ["kubectl", "apply", "-f", str(NATS_MANIFEST)],
            ["kubectl", "--namespace", "polyforge", "rollout", "status",
             "deployment/nats", "--timeout=180s"],
        ]
    # Shared Postgres (opt-in): stood up BEFORE the chart so the control-plane
    # connects on startup and every replica writes one telemetry store.
    if EVAL_SHARED_PG:
        plan += [
            ["kubectl", "apply", "-f", str(PG_MANIFEST)],
            ["kubectl", "--namespace", "polyforge", "rollout", "status",
             "deployment/postgres", "--timeout=240s"],
        ]
    helm_install = [
        # Absolute chart path: the runner's cwd is eval/, and a relative
        # path that does not exist makes helm parse "deploy/..." as a repo
        # reference ("repo deploy not found" — first live smoke, session 16d).
        "helm", "install", "polyforge",
        str(REPO_ROOT / "deploy" / "helm" / "polyforge"),
        "--namespace", "polyforge", "--create-namespace", "--wait", "--timeout", "300s",
        *set_flags,
    ]
    if EVAL_SHARED_PG:
        helm_install += ["-f", str(workdir / "pg-values.yaml")]
    if EVAL_LIVE_AI:
        helm_install += ["-f", str(workdir / "gw-values.yaml")]
    plan += [
        helm_install,
        ["kubectl", "--namespace", "polyforge", "rollout", "status",
         "deployment/polyforge-control-plane", "--timeout=180s"],
        # The eval load path. Applied after the rollout so the Service has
        # ready endpoints to select the moment k6 starts.
        ["kubectl", "apply", "-f", str(workdir / "nodeport.yaml")],
    ]
    if EVAL_LIVE_AI:
        plan += [
            ["kubectl", "--namespace", "polyforge", "rollout", "status",
             "deployment/polyforge-ai-gateway", "--timeout=180s"],
        ]
    if live_operator:
        plan += operator_install_plan(run, workdir)
    plan += [
        ["k6", "run", "--summary-export", str(workdir / "k6-summary.json"),
         str(workdir / "replay.js")],
        # The pod's rootfs is read-only and distroless has no tar (kubectl cp
        # needs it), so the export streams to stdout and execute() captures it.
        ["kubectl", "--namespace", "polyforge", "exec", "deploy/polyforge-control-plane", "--",
         "/control-plane", "eval-export", "--format=json", "--out=-",
         f"--bucket-seconds={EVAL_BUCKET_SECONDS}"],
    ]
    if not EVAL_KEEP_CLUSTER:
        plan += [["kind", "delete", "cluster", "--name", CLUSTER_NAME]]
    return plan


ADMIN_KEY = "polyforge-kind-admin"  # deploy/helm/polyforge/values.yaml default

# One demand bucket's wall-clock duration. Mirrors CONTROL_INTERVAL_S in
# research/jcac_sim/model.py — the sim and the live plane must agree on what
# one step is, or their per-step metrics are not the same quantity.
STEP_SECONDS = 10


def check_k6_delivery(summary_path: Path) -> None:
    """Fail the run if the load generator did not deliver the demand.

    k6 exits 0 even when every request failed, the generated script ignores
    response status, and this summary file was written by every run and read
    by none — so an expired token, a dead port-forward or a 429 storm all
    produced a complete-looking result with an artificially low cost and
    violation. The thresholds in k6_script make k6 itself fail; this is the
    second line, because a threshold can be edited out of a spec but this
    runs on the recorded numbers.
    """
    if not summary_path.exists():
        raise RuntimeError(
            f"{summary_path.name} missing: cannot confirm the load generator "
            "delivered the demand, so the run is not scoreable")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    metrics = summary.get("metrics", {})

    failed = metrics.get("http_req_failed", {})
    fail_rate = failed.get("rate", failed.get("value", 0.0)) or 0.0
    if fail_rate > 0.01:
        raise RuntimeError(
            f"k6 reported {fail_rate:.1%} failed requests (>1%): the load did "
            "not land, so the low cost/violation this run would record are "
            "artifacts of traffic that never arrived")

    dropped = metrics.get("dropped_iterations", {})
    n_dropped = dropped.get("count", dropped.get("value", 0)) or 0
    if n_dropped > 0:
        raise RuntimeError(
            f"k6 dropped {n_dropped} iterations: the generator could not keep "
            "up with its own arrival rate, so the tenants did not receive the "
            "demand this cell specifies")


def check_load_distribution(sampler: "LoadDistributionSampler",
                            max_share: float = 0.25,
                            min_active_fraction: float = 0.8) -> None:
    """Fail the run when the load landed on too few replicas.

    With sixteen replicas an even split is 6.25% each, so a 25% ceiling is
    four times the fair share -- generous enough to absorb kube-proxy's
    imperfect balancing and the noise in a CPU proxy, while still catching
    pinning instantly (attempt 4's pinned pod carried effectively 100%).

    This runs in EVERY stage including the 10-minute smoke, which is the
    point: pinning must never again be discoverable only at hour 24.
    """
    shares = sampler.shares()
    if not shares:
        raise RuntimeError(
            f"load distribution unmeasured: {sampler.samples} usable samples, "
            f"{sampler.failures} failed. The gate that would catch a pinned "
            "load path is itself blind, so the run cannot be trusted")
    hottest, share = max(shares.items(), key=lambda kv: kv[1])
    active = sum(1 for s in shares.values() if s > 0.001)
    if share > max_share:
        raise RuntimeError(
            f"load was pinned: pod {hottest} took {share:.1%} of the work "
            f"(ceiling {max_share:.0%}, fair share {1 / len(shares):.1%} "
            f"across {len(shares)} replicas). This is the WP14 attempt-4 "
            "failure mode -- one replica carrying the run until it OOMs")
    if active < min_active_fraction * len(shares):
        raise RuntimeError(
            f"only {active} of {len(shares)} replicas served any traffic "
            f"(floor {min_active_fraction:.0%}): the load path is not "
            "reaching the whole Deployment")


def check_audit_stream(namespace: str = "polyforge") -> int:
    """Fail the run when the audit backbone carried nothing.

    SK-H2 is scored as "degraded cycles == audit records, exactly". For four
    WP14 attempts that computed 0 == 0 and returned *true*, because
    PlanRunner.audit no-ops when Audit is nil and POLYFORGE_NATS_URL was set
    nowhere. A hypothesis scored against an absent instrument is not a passed
    hypothesis; it is an unrun one, and nothing in the harness noticed.

    Reads JetStream's own accounting through the NATS monitoring port, which
    needs no client library on the Python side. Returns the message count so a
    caller can record it as evidence.
    """
    proc = subprocess.run(
        ["kubectl", "--namespace", namespace, "exec", "deploy/nats", "--",
         "wget", "-qO-", "http://127.0.0.1:8222/jsz"],
        capture_output=True, text=True, timeout=60,
        encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(
            "audit stream unreadable: could not reach the NATS monitoring "
            f"endpoint ({proc.stderr.strip()[-300:]}). SK-H2 cannot be scored "
            "against a stream that cannot be counted")
    try:
        messages = int(json.loads(proc.stdout).get("messages", 0))
    except (ValueError, AttributeError) as exc:
        raise RuntimeError(f"audit stream stats unparseable: {exc}") from exc
    if messages == 0:
        raise RuntimeError(
            "audit stream is EMPTY after the load window: the operator "
            "published nothing, so SK-H2 would score 0 == 0 and report a pass "
            "while testing nothing. This is the WP14 attempt-1-to-4 failure "
            "mode -- check POLYFORGE_NATS_URL reached the operator")
    return messages


def check_sampler_coverage(sampler: "ReplicaSampler", load_duration_s: float) -> None:
    """Fail the run if replica sampling did not cover the load window.

    infra_cost_usd sums samples and multiplies by the nominal interval, so a
    sampler that died partway through silently under-prices the run — and on
    a CRUD cell infra cost IS total_cost_usd.
    """
    coverage = sampler.coverage(load_duration_s)
    if coverage < 0.9:
        raise RuntimeError(
            f"replica sampler covered {coverage:.0%} of the load window "
            f"({len(sampler.samples)} samples, {sampler.failures} failed "
            "attempts): infra cost would be under-counted")
# Mirrors REPLICA_COST_USD_HR in research/jcac_sim/model.py — live infra cost
# is priced exactly like the sim's so the ranking comparison is like-for-like.
REPLICA_COST_USD_HR = 0.048


def _wait_http(url: str, deadline_s: float = 90.0) -> None:
    import urllib.request

    t0 = time.time()
    while time.time() - t0 < deadline_s:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status < 500:
                    return
        except Exception:
            time.sleep(1)
    raise RuntimeError(f"{url} not reachable within {deadline_s}s")


def run_knob_preflight(gateway_base: str, tenant_id: str, api_key: str,
                       workdir: Path) -> None:
    """Execute the WL-H2 knob-liveness gate, raising on an inert substrate.

    PREREG_WAVE4_LIVE_PLANE.md makes an inert cache or tier knob VOID WL-H1:
    a three-knob result measured on a substrate where two knobs do nothing is
    not a weaker result, it is not a result. The check must therefore run
    inside the harness, before load, on every live-AI run — not as a step in
    a runbook that a tired operator can skip at 2am.

    The verdict JSON is written into the run's workdir so the artifact carries
    the evidence that the gate ran, not just the assertion that it did.
    """
    tiers = sorted(json.loads(TIER_BACKENDS_JSON))[:2] if TIER_BACKENDS_JSON else []
    if len(tiers) < 2:
        raise RuntimeError(
            "knob preflight needs two configured tiers in "
            "POLYFORGE_EVAL_TIER_BACKENDS to prove the tier knob routes")
    report = workdir / "knob_preflight.json"
    proc = subprocess.run(
        [sys.executable, str(EVAL_DIR / "scripts" / "knob_preflight.py"),
         "--gateway", gateway_base, "--tenant", tenant_id,
         "--api-key", api_key, "--admin-key", ADMIN_KEY,
         "--tiers", ",".join(tiers), "--report", str(report)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
    )
    print(proc.stdout, end="")
    if proc.returncode != 0:
        raise RuntimeError(
            "WL-H2 knob-liveness preflight FAILED — WL-H1 is void on this "
            f"substrate, so the run is not scored: {proc.stderr.strip() or proc.stdout.strip()}")


def push_default_knobs(gateway_base: str, tenant_ids) -> None:
    """Fixed data-plane posture for non-operator arms on the live-AI plane:
    every tenant holds the sim's initial world (TenantState: tier `small`,
    cache 128 MB) so single-knob baselines face the same starting cache and
    tier economy as the operator arm — they just never move them
    (PREREG_WAVE4_LIVE_PLANE.md §Arms). The operator arm's knobs come from
    its Policy CRs instead, pushed by the reconciler."""
    import urllib.request

    initial = TenantState()
    for tid in tenant_ids:
        payload = json.dumps({
            "model_tier": initial.tier,
            "cache_size_mb": initial.cache_mb,
        }).encode()
        req = urllib.request.Request(
            f"{gateway_base}/admin/tenants/{tid}/knobs", data=payload, method="PUT",
            headers={"Content-Type": "application/json",
                     "X-PolyForge-Admin-Key": ADMIN_KEY})
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status != 200:
                raise RuntimeError(f"default knob push for {tid} -> {resp.status}")


def provision_tenants(base: str, tenant_ids, slo_classes=None) -> dict[str, str]:
    """Create each tenant and mint it a full-scope API key via the admin
    key the chart's Secret carries. Returns tenant -> key secret, exported
    to k6 as TOKEN_<tenant>.

    `slo_classes` maps tenant -> premium/standard/best-effort and MUST be
    supplied for any non-uniform mix. The control-plane tenant store defaults
    an absent plan to "standard", and eval-export scores latency against the
    plan's SLO scale — so omitting it silently graded every tenant at 2.5x,
    whatever the mix said. Premium tenants were judged 2.5x too leniently and
    best-effort 3.2x too strictly, which voids any live/sim parity reading on
    premium_heavy, besteffort_heavy or whale."""
    import urllib.error
    import http.client
    import urllib.request

    def post(path: str, body: dict) -> dict:
        req = urllib.request.Request(
            base + path, data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json",
                     "X-PolyForge-Admin-Key": ADMIN_KEY})
        # Retry transient transport failures. Behind a NodePort the request
        # can land on any of sixteen replicas, and a replica that is restarting
        # closes the connection without answering -- `RemoteDisconnected`,
        # which is not an HTTPError and so used to abort the whole run during
        # setup. WP14 Stage B died that way 167 s in, on a cluster that was
        # healthy 30 seconds later. The underlying cause (concurrent GRANTs
        # crashing a replica at startup) is fixed in
        # internal/storage/postgres/migrate.go, but provisioning should not be
        # one dropped connection away from losing a multi-hour stage either.
        last: Exception | None = None
        for attempt in range(5):
            try:
                # 60 s, not 15: a cold control plane runs its schema migration
                # on the first write, and the WP8a dry-run (session 38) timed
                # out here on an otherwise healthy cluster. `_wait_http` only
                # proves /healthz answers, which it does before the store is
                # ready.
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return json.loads(resp.read() or b"{}")
            except urllib.error.HTTPError as err:
                if err.code == 409:  # tenant already exists on a retried attempt
                    return {}
                # A status code is the server's considered answer, not a
                # transport failure: retrying it would just repeat the error.
                raise RuntimeError(
                    f"POST {path} -> {err.code}: {err.read()[:500]}") from err
            except (urllib.error.URLError, http.client.HTTPException,
                    ConnectionError, TimeoutError) as err:
                last = err
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(
            f"POST {path} failed after 5 attempts, last error: {last}") from last

    tokens = {}
    for tid in tenant_ids:
        body = {"id": tid, "name": tid}
        if slo_classes and tid in slo_classes:
            body["plan"] = slo_classes[tid]
        post("/v1/tenants", body)
        key = post(f"/v1/tenants/{tid}/api-keys", {"name": "eval", "scope": "full"})
        tokens[tid] = key["secret"]
    return tokens


class LoadDistributionSampler(threading.Thread):
    """Samples per-pod CPU across the control-plane during the load window.

    Exists because of the single most expensive omission in WP14: across four
    attempts, *nothing ever checked that requests reached more than one pod*.
    `kubectl port-forward` pinned every request to one replica of sixteen, and
    every health signal was satisfied by that one replica working -- pods
    Running, sixteen Service endpoints, /healthz answering 200. The run was
    only revealed as invalid at hour 24, four times.

    CPU is a **proxy** for request share, not a count of requests. It is used
    because metrics-server is already deployed for the HPA arm, so this costs
    no product change, and because the failure it must catch is not subtle: in
    attempt 4 the pinned pod ran at 1571m while its fifteen siblings sat at
    8-16m. A proxy that separates those by two orders of magnitude is an
    adequate gate. If it ever proves too noisy, the rigorous replacement is a
    pod label on `telemetry_events` written from the Downward API.
    """

    INTERVAL_S = 30.0

    def __init__(self, namespace: str = "polyforge",
                 selector: str = "app.kubernetes.io/name=polyforge-control-plane"):
        super().__init__(daemon=True)
        self.namespace = namespace
        self.selector = selector
        self.cpu_ms: dict[str, float] = {}
        self.samples = 0
        self.failures = 0
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            self._sample()
            self._stop.wait(self.INTERVAL_S)

    def _sample(self) -> None:
        try:
            proc = subprocess.run(
                ["kubectl", "--namespace", self.namespace, "top", "pods",
                 "--selector", self.selector, "--no-headers"],
                capture_output=True, text=True, timeout=30,
                encoding="utf-8", errors="replace")
            if proc.returncode != 0:
                self.failures += 1
                return
            seen = False
            for line in proc.stdout.splitlines():
                parts = line.split()
                if len(parts) < 2 or not parts[1].endswith("m"):
                    continue
                pod, cpu = parts[0], float(parts[1][:-1])
                # Accumulate CPU-milli-seconds, so a pod that is hot for half
                # the run and idle for the rest is weighted accordingly.
                self.cpu_ms[pod] = self.cpu_ms.get(pod, 0.0) + cpu * self.INTERVAL_S
                seen = True
            if seen:
                self.samples += 1
        except Exception:
            self.failures += 1

    def shares(self) -> dict[str, float]:
        total = sum(self.cpu_ms.values())
        if total <= 0:
            return {}
        return {pod: cpu / total for pod, cpu in sorted(self.cpu_ms.items())}


class ReplicaSampler(threading.Thread):
    """Samples the deployment's ready replica count while k6 drives load;
    replica-seconds price the live run's infra cost (the component
    eval-export cannot see from inside the pod)."""

    def __init__(self, interval_s: float = 10.0):
        super().__init__(daemon=True)
        self.interval_s = interval_s
        self.samples: list[int] = []
        self.failures = 0  # sampling attempts that produced no reading
        self._halt = threading.Event()

    def run(self) -> None:
        while not self._halt.is_set():
            try:
                proc = subprocess.run(
                    ["kubectl", "--namespace", "polyforge", "get",
                     "deployment/polyforge-control-plane", "-o", "jsonpath={.status.replicas}"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
                if proc.returncode == 0 and proc.stdout.strip().isdigit():
                    self.samples.append(int(proc.stdout.strip()))
                else:
                    self.failures += 1
            except Exception:
                # A TimeoutExpired used to escape here and kill the thread.
                # It is a daemon thread and join() returns immediately, so
                # the death went unnoticed and infra cost was computed from
                # however many samples had been taken — a run whose sampler
                # died three minutes into twenty was priced ~85% too low.
                self.failures += 1
            self._halt.wait(self.interval_s)

    def stop(self) -> None:
        self._halt.set()

    def coverage(self, load_duration_s: float) -> float:
        """Fraction of the expected samples actually taken. Infra cost is the
        whole of total_cost_usd on CRUD cells, so a sampler that stopped early
        silently under-prices the run."""
        expected = max(1.0, load_duration_s / self.interval_s)
        return len(self.samples) / expected

    def infra_cost_usd(self) -> float:
        replica_seconds = sum(self.samples) * self.interval_s
        return replica_seconds / 3600.0 * REPLICA_COST_USD_HR


def evidence_dir_for(run: RunSpec) -> Path:
    """Where a live run's diagnostics are kept **after** it ends.

    Under `eval/results/`, inside the repo — never `%TEMP%`. Two WP14 losses
    taught this: the harness wrote `k6-summary.json` into a
    `tempfile.TemporaryDirectory` that self-deleted the moment the delivery
    gate rejected the run, and the session-side fault journal was first
    written to `%LOCALAPPDATA%\\Temp`, which Windows may clean on its own
    schedule. Evidence for a scored record does not live where the OS is
    allowed to delete it.
    """
    return REPO_ROOT / "eval" / "results" / f"{run.experiment}_evidence"


def _run_step(cmd: list[str], *, env, timeout_s: float,
              live_log: "Path | None" = None):
    """Run one plan step, optionally streaming its output to a file as it goes.

    WP14 attempt 5. The load generator used to run under `capture_output=True`,
    which buffers everything in memory until the process exits -- so the runner
    log was **0 bytes for 24 hours** and `http_req_failed`, the number that
    decides whether the whole sitting counts, was unknowable until the end.
    Attempt 2's record could not even say whether its 36.4% failures began at
    minute one or accumulated late, because nothing observed the run while it
    ran.

    Streaming to a file gives k6's periodic progress lines a home, at a few KB
    per hour. `--out csv` would give a finer timeline and was rejected: at
    ~300 req/s over 24 h it writes millions of rows per metric, which is a
    second data-management problem rather than a fix for this one.

    The tail is read back into both stdout and stderr so the caller's existing
    error reporting (`proc.stderr[-2000:]`) keeps working unchanged.
    """
    if live_log is None:
        return subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout_s, env=env)
    live_log.parent.mkdir(parents=True, exist_ok=True)
    with live_log.open("w", encoding="utf-8", errors="replace") as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT,
                              timeout=timeout_s, env=env)
    try:
        tail = live_log.read_text(encoding="utf-8", errors="replace")[-4000:]
    except OSError:
        tail = ""
    return subprocess.CompletedProcess(cmd, proc.returncode,
                                       stdout=tail, stderr=tail)


def _preserve_evidence(workdir: Path, evidence_dir: Path | None,
                       supervisor: "PortForwardSupervisor | None",
                       loadspread: "LoadDistributionSampler | None" = None) -> None:
    """Copy the run's diagnostics out of the temp workdir. Best-effort and
    never raising: a failure to save evidence must not also fail the run."""
    if evidence_dir is None:
        return
    try:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        for name in ("k6-summary.json", "eval-export.json"):
            src = workdir / name
            if src.exists():
                shutil.copy2(src, evidence_dir / name)
        if supervisor is not None:
            (evidence_dir / "port_forward_summary.json").write_text(
                json.dumps(supervisor.summary(), indent=2), encoding="utf-8")
        if loadspread is not None:
            # SK-H6's evidence. Written even when the gate rejects the run,
            # because a pinned distribution IS the finding in that case.
            (evidence_dir / "load_distribution.json").write_text(
                json.dumps({"samples": loadspread.samples,
                            "failures": loadspread.failures,
                            "cpu_ms": loadspread.cpu_ms,
                            "shares": loadspread.shares()}, indent=2),
                encoding="utf-8")
    except Exception:
        pass


class PortForwardSupervisor(threading.Thread):
    """Keeps a `kubectl port-forward` alive for the whole load window.

    **No longer on the CRUD load path (WP14 attempt 5).** Supervising this
    proxy treated a symptom: however reliably the forward is respawned, it
    still pins every request to ONE pod, and attempt 4 lost its 24 h sitting
    exactly that way -- 282 supervised restarts, because each OOM kill of the
    pinned replica killed the forward with it. The load path is now a NodePort
    (`NODE_PORT`), where kube-proxy balances across all ready endpoints.

    Kept because a single-pod service still sometimes needs forwarding, and
    because the AI-gateway path below is still an unsupervised `Popen` that
    should eventually adopt this.

    WP14 attempt 2 (session 38) lost a full 24 h soak to this being absent:
    `check_k6_delivery` rejected the run at **36.4% failed requests**, and the
    leading explanation is that the forward died or was pinned to a pod that
    was replaced. `kubectl port-forward service/X` resolves to ONE pod when it
    starts and does not follow the Service afterwards, so a single pod restart
    silently black-holes every subsequent request — while k6 keeps posting and
    exits 0, which is exactly the trap `check_k6_delivery`'s docstring warns
    about.

    The old code spawned the forward once with `subprocess.Popen(..., stderr=
    DEVNULL)` and never looked at it again for the life of the run. This polls
    the forwarded port and respawns on failure, mirroring `ReplicaSampler`'s
    structure — including the lesson in its comments, that a daemon thread
    dying quietly corrupts the run it was meant to protect.

    Every restart is logged with a timestamp and reason so a post-mortem can
    tell "the forward flapped" from "the service was genuinely down".
    """

    def __init__(self, namespace: str, service: str, local_port: int,
                 evidence_log: Path | None = None, interval_s: float = 15.0):
        super().__init__(daemon=True)
        self.namespace = namespace
        self.service = service
        self.local_port = local_port
        self.evidence_log = evidence_log
        self.interval_s = interval_s
        self.proc: subprocess.Popen | None = None
        self.restarts = 0
        self.probe_failures = 0
        self.events: list[str] = []
        self._halt = threading.Event()

    # -- lifecycle ---------------------------------------------------------
    def _log(self, message: str) -> None:
        line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}"
        self.events.append(line)
        if self.evidence_log is not None:
            try:
                with self.evidence_log.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass  # never let evidence logging take down the run

    def _spawn(self) -> None:
        self.proc = subprocess.Popen(
            ["kubectl", "--namespace", self.namespace, "port-forward",
             f"service/{self.service}", f"{self.local_port}:80"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def start_forward(self) -> str:
        """Spawn the initial forward and record which pod it resolved to.

        The pod identity is the diagnostic attempt 2 lacked: without it there
        is no way to correlate a delivery collapse with a specific pod's
        restart.
        """
        self._spawn()
        endpoints = self._resolve_endpoints()
        self._log(f"forward started pid={self.proc.pid if self.proc else '?'} "
                  f"service={self.service} endpoints={endpoints}")
        return endpoints

    def _resolve_endpoints(self) -> str:
        try:
            proc = subprocess.run(
                ["kubectl", "--namespace", self.namespace, "get", "endpoints",
                 self.service, "-o",
                 "jsonpath={.subsets[*].addresses[*].ip}"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=30)
            return proc.stdout.strip() or "(none)"
        except Exception:
            return "(unreadable)"

    def _healthy(self) -> bool:
        # Imported locally, matching this module's existing convention
        # (`_wait_http`, `provision_tenants`, `push_default_knobs` all do the
        # same). Module-level would be tidier, but a NameError raised inside a
        # daemon thread mid-soak is silent — the thread dies, the forward stops
        # being supervised, and the run degrades exactly the way this class
        # exists to prevent.
        import urllib.request

        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.local_port}/healthz", timeout=5) as resp:
                return resp.status == 200
        except Exception:
            return False

    def run(self) -> None:
        while not self._halt.is_set():
            self._halt.wait(self.interval_s)
            if self._halt.is_set():
                break
            dead = self.proc is not None and self.proc.poll() is not None
            healthy = self._healthy()
            if dead or not healthy:
                self.probe_failures += 1
                # One failed probe can be a transient blip during a planner
                # crash (which this soak injects deliberately). Confirm once
                # before restarting, so a fault does not cause a stampede.
                if not dead and self._healthy():
                    continue
                reason = "process exited" if dead else "health probe failed"
                self.restarts += 1
                self._log(f"RESTART #{self.restarts} ({reason}); "
                          f"endpoints={self._resolve_endpoints()}")
                try:
                    if self.proc is not None:
                        self.proc.terminate()
                except Exception:
                    pass
                self._spawn()

    def stop(self) -> None:
        self._halt.set()
        try:
            if self.proc is not None:
                self.proc.terminate()
        except Exception:
            pass

    def summary(self) -> dict:
        return {"restarts": self.restarts, "probe_failures": self.probe_failures,
                "events": list(self.events)}


def execute(run: RunSpec, timeout_s: int = 3600) -> dict:
    # WP14: the per-step subprocess timeout has to cover the load window, or
    # a long run is killed mid-k6 and recorded as failed. The default 3600 s
    # was written when every live run was minutes; a 24 h soak is 8640 steps
    # x 10 s. Derived from the run rather than raised globally, so short runs
    # keep the tight timeout that catches a hung step.
    timeout_s = max(timeout_s, run.steps * STEP_SECONDS + 1800)

    missing = preflight()
    if missing:
        raise BackendUnavailable(
            f"cluster backend needs {missing} on PATH; run this on the W35a "
            "cloud box or a machine with Docker (sim backend runs anywhere)"
        )

    tenant_ids, _, tenant_configs, _ = workloads.build(
        run.workload, run.tenant_mix, run.cluster_size, run.seed, run.steps
    )
    # The mix's SLO classes must reach the control plane, or eval-export
    # grades every tenant at the "standard" default (see provision_tenants).
    slo_classes = {tid: cfg.slo_class for tid, cfg in tenant_configs.items()}

    started = time.time()
    with tempfile.TemporaryDirectory(prefix=f"pf-eval-{run.run_id}-") as tmp:
        workdir = Path(tmp)
        (workdir / "kind.yaml").write_text(kind_config(run.cluster_size), encoding="utf-8")
        (workdir / "nodeport.yaml").write_text(nodeport_service(),
                                               encoding="utf-8")
        (workdir / "replay.js").write_text(k6_script(run), encoding="utf-8")
        if run.system in OPERATOR_SYSTEMS:
            (workdir / "operator-crs.yaml").write_text(operator_crs(run), encoding="utf-8")
        if EVAL_SHARED_PG:
            (workdir / "pg-values.yaml").write_text(
                f"postgres:\n  adminURL: {PG_ADMIN_URL}\n  appURL: {PG_APP_URL}\n",
                encoding="utf-8",
            )
        if EVAL_LIVE_AI:
            # json.dumps of the JSON string doubles as a valid YAML
            # double-quoted scalar, so braces/commas survive helm intact.
            (workdir / "gw-values.yaml").write_text(
                f"gateway:\n  tierBackends: {json.dumps(TIER_BACKENDS_JSON)}\n",
                encoding="utf-8",
            )

        plan = command_plan(run, workdir)
        # WP14: diagnostics land here and survive the run (and the workdir).
        evidence_dir = evidence_dir_for(run)
        evidence_dir.mkdir(parents=True, exist_ok=True)
        portforward = None
        gw_portforward = None
        sampler = None
        loadspread = None
        infra_cost = 0.0
        try:
            for cmd in plan:
                env = None
                if cmd[0] == "k6":
                    # The load window: reach the service, mint tenant keys,
                    # meter replicas while k6 replays the demand buckets.
                    #
                    # WP14 attempt 5: no forward at all. Attempts 2-4 tried
                    # progressively harder to keep a `kubectl port-forward`
                    # alive -- unsupervised, then supervised with automatic
                    # respawn -- and attempt 4 still lost the run to it, at
                    # 282 restarts. Supervising a proxy that pins every
                    # request to one pod treats the symptom; a NodePort
                    # removes the proxy, and kube-proxy spreads the load
                    # across all ready endpoints in the kernel.
                    base = f"http://127.0.0.1:{NODE_PORT}"
                    _wait_http(base + "/healthz")
                    tokens = provision_tenants(base, tenant_ids, slo_classes)
                    env = dict(os.environ, POLYFORGE_URL=base,
                               **{f"TOKEN_{tid}": tok for tid, tok in tokens.items()})
                    if EVAL_LIVE_AI:
                        gw_portforward = subprocess.Popen(
                            ["kubectl", "--namespace", "polyforge", "port-forward",
                             "service/polyforge-ai-gateway", f"{GATEWAY_LOCAL_PORT}:80"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        gw_base = f"http://127.0.0.1:{GATEWAY_LOCAL_PORT}"
                        _wait_http(gw_base + "/healthz")
                        if run.system not in OPERATOR_SYSTEMS:
                            push_default_knobs(gw_base, tenant_ids)
                        env["POLYFORGE_GATEWAY_URL"] = gw_base
                        # WL-H2 liveness gate, executed rather than described.
                        # An inert cache or tier knob VOIDS WL-H1, so this runs
                        # before any load and raises on an inert substrate. It
                        # previously existed only as a script referenced from
                        # prose, callable from no code path, which meant a
                        # four-arm campaign could complete with both knobs dead
                        # and every row looking valid.
                        run_knob_preflight(gw_base, tenant_ids[0],
                                           tokens[tenant_ids[0]], workdir)
                    sampler = ReplicaSampler()
                    sampler.start()
                    loadspread = LoadDistributionSampler()
                    loadspread.start()
                    try:
                        SOAK_MARKER.write_text(
                            f"run_id={run.run_id}\n"
                            f"started={time.time():.0f}\n"
                            "No local builds, test runs or heavy queries "
                            "until this file disappears.\n",
                            encoding="utf-8")
                    except OSError:
                        pass  # never fail a run over its own courtesy marker
                exporting = "eval-export" in cmd
                if exporting:
                    cmd = cmd + [f"--infra-cost-usd={infra_cost:.6f}"]
                # Only the load generator streams: every other step's stdout
                # is consumed as data (eval-export's JSON above all), and a
                # file handle would take that away.
                proc = _run_step(
                    cmd, env=env, timeout_s=timeout_s,
                    live_log=(evidence_dir / "k6-live.log")
                    if (cmd[0] == "k6" and evidence_dir) else None,
                )
                if exporting and proc.returncode == 0:
                    (workdir / "eval-export.json").write_text(proc.stdout, encoding="utf-8")
                if cmd[0] == "k6":
                    if sampler is not None:
                        sampler.stop()
                        sampler.join(timeout=30)
                        infra_cost = sampler.infra_cost_usd()
                        check_sampler_coverage(sampler, run.steps * STEP_SECONDS)
                    # WP14: copy the delivery evidence OUT of the temp workdir
                    # BEFORE check_k6_delivery can raise. Attempt 2's summary —
                    # the only artifact carrying the failure timeline — died
                    # with the TemporaryDirectory when the gate rejected the
                    # run, which is why that failure could not be diagnosed.
                    if loadspread is not None:
                        loadspread.stop()
                        loadspread.join(timeout=60)
                    _preserve_evidence(workdir, evidence_dir, portforward,
                                       loadspread)
                    # Order matters. check_k6_delivery answers "did the
                    # requests succeed"; check_load_distribution answers "did
                    # they reach more than one pod". Attempt 4 passed the
                    # second question for 17 h without anyone asking it, so it
                    # is asked here on every run, before the delivery verdict
                    # is trusted to mean anything about the Deployment.
                    check_k6_delivery(workdir / "k6-summary.json")
                    if loadspread is not None:
                        check_load_distribution(loadspread)
                    if run.system in OPERATOR_SYSTEMS:
                        audit_messages = check_audit_stream()
                        print(f"[audit] {audit_messages} record(s) on the "
                              "backbone", flush=True)
                    if portforward is not None:
                        portforward.stop()
                        portforward = None
                    if gw_portforward is not None:
                        gw_portforward.terminate()
                        gw_portforward = None
                # The pre-clean delete may fail on a machine with no leftover
                # cluster; every other step must succeed.
                if proc.returncode != 0 and cmd is not plan[0]:
                    raise RuntimeError(
                        f"step {' '.join(cmd[:3])} exited {proc.returncode}: "
                        f"{proc.stderr[-2000:]}"
                    )
        finally:
            if sampler is not None:
                sampler.stop()
            if loadspread is not None:
                loadspread.stop()
            SOAK_MARKER.unlink(missing_ok=True)
            # Second attempt at preserving evidence: the first is before the
            # delivery gate (so a rejected run keeps its diagnostics); this one
            # covers every other exit path, including an exception mid-load.
            _preserve_evidence(workdir, evidence_dir, portforward)
            if portforward is not None:
                portforward.stop()
            if gw_portforward is not None:
                gw_portforward.terminate()
            if EVAL_KEEP_CLUSTER:
                print(f"[cluster] POLYFORGE_EVAL_KEEP_CLUSTER=1: leaving "
                      f"{CLUSTER_NAME} up. Remove it with: "
                      f"kind delete cluster --name {CLUSTER_NAME}", flush=True)
            else:
                subprocess.run(
                    ["kind", "delete", "cluster", "--name", CLUSTER_NAME],
                    capture_output=True, timeout=600,
                )

        export = json.loads((workdir / "eval-export.json").read_text(encoding="utf-8"))
    return {
        "run": run,
        "wall_s": time.time() - started,
        "tenants": len(tenant_ids),
        "metrics": {
            "total_cost_usd": export["total_cost_usd"],
            "mean_violation": export["mean_violation"],
            "violation_step_share": export["violation_step_share"],
            "mean_jain": export["mean_jain"],
            "cache_hit_rate": export["cache_hit_rate"],
            "crud_p95_ms": export["crud_p95_ms"],
            "ai_p95_ms": export["ai_p95_ms"],
            # p99 is a live-only order statistic (evalexport.go); surfaced here
            # so it persists through the harness (PREREG_LIVE_CHAOS_P99.md Part B).
            "crud_p99_ms": export.get("crud_p99_ms"),
            "ai_p99_ms": export.get("ai_p99_ms"),
            # The exporter has always emitted n_events; the harness used to
            # drop it, which is precisely the field that distinguishes "this
            # run measured a low latency" from "this run measured nothing".
            # check_metrics now rejects a run that observed zero events.
            "n_events": export.get("n_events"),
            "steps": run.steps,
        },
        "timeseries": export.get("timeseries", []),
    }
