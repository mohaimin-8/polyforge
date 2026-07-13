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

# Systems that deploy the operator/planner control loop live. Only the full
# jcac arm is wired for live execution; the jcac_* ablations remain sim-only
# (V2_README Phase 7 measures the ordinal jcac-vs-hpa slice, nothing wider).
OPERATOR_SYSTEMS = {"jcac"}

REPO_ROOT = Path(__file__).resolve().parents[2]
ADMIN_SECRET_NAME = "polyforge-admin"  # carries ADMIN_KEY for the operator

# Ablation/baseline toggles the chart understands (deploy/helm/polyforge).
HELM_VALUES_BY_SYSTEM = {
    "jcac": {"planner.enabled": "true", "classifier.enabled": "true"},
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
    ]
    lines += ["  - role: worker"] * (nodes - 1)
    return "\n".join(lines) + "\n"


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


def k6_script(run: RunSpec, interval_s: int = 10) -> str:
    """One k6 scenario per tenant, ramping-arrival-rate stages replaying
    this run's demand buckets against the replay endpoint. The tenant
    identity travels as its API key (provisioned by execute() before k6
    starts); each request samples its kind from the tenant's demand mix."""
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
        scenarios[f"tenant_{tid}"] = {
            "executor": "ramping-arrival-rate",
            "startRate": stages[0]["target"],
            "timeUnit": "1s",
            "preAllocatedVUs": 50,
            "maxVUs": 500,
            "stages": stages,
            "env": {"TENANT": tid},
        }
        mixes[tid] = kind_mix(buckets, tid)
    options = {"scenarios": scenarios, "discardResponseBodies": True}
    return f"""// generated by eval/harness for run {run.run_id} — do not edit
import http from 'k6/http';

export const options = {json.dumps(options, indent=2)};

const BASE = __ENV.POLYFORGE_URL || 'http://localhost:8080';
const MIX = {json.dumps(mixes, indent=2)};

function sampleKind(tenant) {{
  const r = Math.random();
  for (const [kind, cum] of MIX[tenant]) {{
    if (r <= cum) return kind;
  }}
  return MIX[tenant][MIX[tenant].length - 1][0];
}}

export default function () {{
  const tenant = __ENV.TENANT;
  const token = __ENV[`TOKEN_${{tenant}}`];
  http.post(`${{BASE}}/v1/tenants/${{tenant}}/workloads/replay`,
    JSON.stringify({{run: '{run.run_id}', kind: sampleKind(tenant)}}),
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
    "ingress.enabled": "false",
    "rateLimit.requestsPerMinute": "1000000",
    "rateLimit.burst": "100000",
}


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
    docs = []
    for tid in tenant_ids:
        config = configs[tid]
        docs.append(f"""apiVersion: polyforge.io/v1alpha1
kind: Policy
metadata:
  name: {tid}
spec:
  tenantRef: {tid}
  targetDeployment: polyforge/polyforge-control-plane
  replicas: {initial.replicas}
  replicaMin: 1
  replicaMax: {size.replica_max}
  cacheSizeMB: {initial.cache_mb}
  modelTier: {initial.tier}""")
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
         "--set=operator.image.repository=polyforge/operator",
         "--set=operator.image.tag=dev",
         "--set=planner.image.repository=polyforge/planner",
         "--set=planner.image.tag=dev",
         "--set=features.url=http://polyforge-control-plane.polyforge.svc:80",
         f"--set=features.adminKeySecret.name={ADMIN_SECRET_NAME}",
         f"--set=planner.limits.replicas={size.limits_replicas}",
         f"--set=planner.limits.cacheMB={size.limits_cache_mb}"],
        ["kubectl", "apply", "-f", str(workdir / "operator-crs.yaml")],
        ["kubectl", "wait", "--for=condition=Applied", "policies.polyforge.io",
         "--all", "--timeout=180s"],
    ]


def command_plan(run: RunSpec, workdir: Path) -> list[list[str]]:
    """The exact subprocess sequence for one run, in order. Split out so
    tests and --dry-run can inspect it without Docker."""
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
    set_flags = [f"--set={k}={v}" for k, v in sorted(values.items())]
    live_operator = run.system in OPERATOR_SYSTEMS
    plan = [
        ["kind", "delete", "cluster", "--name", CLUSTER_NAME],  # idempotent pre-clean
        ["kind", "create", "cluster", "--name", CLUSTER_NAME,
         "--config", str(workdir / "kind.yaml"), "--wait", "120s"],
        ["kind", "load", "docker-image", CONTROL_PLANE_IMAGE, "--name", CLUSTER_NAME],
    ]
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
        # Absolute chart path: the runner's cwd is eval/, and a relative
        # path that does not exist makes helm parse "deploy/..." as a repo
        # reference ("repo deploy not found" — first live smoke, session 16d).
        ["helm", "install", "polyforge",
         str(REPO_ROOT / "deploy" / "helm" / "polyforge"),
         "--namespace", "polyforge", "--create-namespace", "--wait", "--timeout", "300s",
         *set_flags],
        ["kubectl", "--namespace", "polyforge", "rollout", "status",
         "deployment/polyforge-control-plane", "--timeout=180s"],
    ]
    if live_operator:
        plan += operator_install_plan(run, workdir)
    plan += [
        ["k6", "run", "--summary-export", str(workdir / "k6-summary.json"),
         str(workdir / "replay.js")],
        # The pod's rootfs is read-only and distroless has no tar (kubectl cp
        # needs it), so the export streams to stdout and execute() captures it.
        ["kubectl", "--namespace", "polyforge", "exec", "deploy/polyforge-control-plane", "--",
         "/control-plane", "eval-export", "--format=json", "--out=-"],
        ["kind", "delete", "cluster", "--name", CLUSTER_NAME],
    ]
    return plan


ADMIN_KEY = "polyforge-kind-admin"  # deploy/helm/polyforge/values.yaml default
LOCAL_PORT = 18080
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


def provision_tenants(base: str, tenant_ids) -> dict[str, str]:
    """Create each tenant and mint it a full-scope API key via the admin
    key the chart's Secret carries. Returns tenant -> key secret, exported
    to k6 as TOKEN_<tenant>."""
    import urllib.error
    import urllib.request

    def post(path: str, body: dict) -> dict:
        req = urllib.request.Request(
            base + path, data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json",
                     "X-PolyForge-Admin-Key": ADMIN_KEY})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as err:
            if err.code == 409:  # tenant already exists on a retried attempt
                return {}
            raise RuntimeError(f"POST {path} -> {err.code}: {err.read()[:500]}") from err

    tokens = {}
    for tid in tenant_ids:
        post("/v1/tenants", {"id": tid, "name": tid})
        key = post(f"/v1/tenants/{tid}/api-keys", {"name": "eval", "scope": "full"})
        tokens[tid] = key["secret"]
    return tokens


class ReplicaSampler(threading.Thread):
    """Samples the deployment's ready replica count while k6 drives load;
    replica-seconds price the live run's infra cost (the component
    eval-export cannot see from inside the pod)."""

    def __init__(self, interval_s: float = 10.0):
        super().__init__(daemon=True)
        self.interval_s = interval_s
        self.samples: list[int] = []
        self._halt = threading.Event()

    def run(self) -> None:
        while not self._halt.is_set():
            proc = subprocess.run(
                ["kubectl", "--namespace", "polyforge", "get",
                 "deployment/polyforge-control-plane", "-o", "jsonpath={.status.replicas}"],
                capture_output=True, text=True, timeout=30)
            if proc.returncode == 0 and proc.stdout.strip().isdigit():
                self.samples.append(int(proc.stdout.strip()))
            self._halt.wait(self.interval_s)

    def stop(self) -> None:
        self._halt.set()

    def infra_cost_usd(self) -> float:
        replica_seconds = sum(self.samples) * self.interval_s
        return replica_seconds / 3600.0 * REPLICA_COST_USD_HR


def execute(run: RunSpec, timeout_s: int = 3600) -> dict:
    missing = preflight()
    if missing:
        raise BackendUnavailable(
            f"cluster backend needs {missing} on PATH; run this on the W35a "
            "cloud box or a machine with Docker (sim backend runs anywhere)"
        )

    tenant_ids, _, _, _ = workloads.build(
        run.workload, run.tenant_mix, run.cluster_size, run.seed, run.steps
    )

    started = time.time()
    with tempfile.TemporaryDirectory(prefix=f"pf-eval-{run.run_id}-") as tmp:
        workdir = Path(tmp)
        (workdir / "kind.yaml").write_text(kind_config(run.cluster_size), encoding="utf-8")
        (workdir / "replay.js").write_text(k6_script(run), encoding="utf-8")
        if run.system in OPERATOR_SYSTEMS:
            (workdir / "operator-crs.yaml").write_text(operator_crs(run), encoding="utf-8")

        plan = command_plan(run, workdir)
        portforward = None
        sampler = None
        infra_cost = 0.0
        try:
            for cmd in plan:
                env = None
                if cmd[0] == "k6":
                    # The load window: reach the service, mint tenant keys,
                    # meter replicas while k6 replays the demand buckets.
                    portforward = subprocess.Popen(
                        ["kubectl", "--namespace", "polyforge", "port-forward",
                         "service/polyforge-control-plane", f"{LOCAL_PORT}:80"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    base = f"http://127.0.0.1:{LOCAL_PORT}"
                    _wait_http(base + "/healthz")
                    tokens = provision_tenants(base, tenant_ids)
                    env = dict(os.environ, POLYFORGE_URL=base,
                               **{f"TOKEN_{tid}": tok for tid, tok in tokens.items()})
                    sampler = ReplicaSampler()
                    sampler.start()
                exporting = "eval-export" in cmd
                if exporting:
                    cmd = cmd + [f"--infra-cost-usd={infra_cost:.6f}"]
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=timeout_s, env=env
                )
                if exporting and proc.returncode == 0:
                    (workdir / "eval-export.json").write_text(proc.stdout, encoding="utf-8")
                if cmd[0] == "k6":
                    if sampler is not None:
                        sampler.stop()
                        sampler.join(timeout=30)
                        infra_cost = sampler.infra_cost_usd()
                    if portforward is not None:
                        portforward.terminate()
                        portforward = None
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
            if portforward is not None:
                portforward.terminate()
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
            "steps": run.steps,
        },
        "timeseries": export.get("timeseries", []),
    }
