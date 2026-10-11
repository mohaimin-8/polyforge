"""Part C of the demonstration: the whole control loop on a local kind cluster.

Runs eval/experiments/demo_cluster.yaml (never evidence: its own gitignored
output) with shared PostgreSQL and the replica-independent demand signal,
and while it runs checks every link between the layers, announcing each one
the moment it is verified:

  load -> control plane (N pods) -> PostgreSQL -> feature API -> operator
       -> JCAC planner -> Policy objects -> Kubernetes Deployment, audited on NATS

Needs Docker Desktop running plus kind, kubectl, helm and k6; builds the
three images if they are missing. Deletes the cluster afterwards unless
--keep-cluster. About 10 minutes.

    python scripts/demo_cluster.py
    python scripts/demo_cluster.py --keep-cluster --rebuild-images
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

REPO = Path(__file__).resolve().parents[1]
EVAL = REPO / "eval"
RESULTS = EVAL / "results"
RUN_LOG = REPO / "tmp" / "demo" / "cluster-run.log"
EXPORT = RESULTS / "demo_cluster_evidence" / "eval-export.json"
EXPERIMENT = "experiments/demo_cluster.yaml"
CLUSTER = "polyforge-eval"
NAMESPACE = "polyforge"
ADMIN_KEY = "polyforge-kind-admin"  # deploy/helm/polyforge/values.yaml default
NODE_URL = "http://127.0.0.1:30080"
TRACE_TENANT = "t00"
POLL_S = 5
HEARTBEAT_S = 30
CLI_TIMEOUT_S = 60  # docker/kind housekeeping calls; a half-started engine must not hang the demo
DELETE_TIMEOUT_S = 300
RUN_TIMEOUT_S = 25 * 60
RUN_ENV = {
    "POLYFORGE_EVAL_SHARED_PG": "1",      # one store for every control-plane pod
    "POLYFORGE_EVAL_KEEP_CLUSTER": "1",   # the last link is checked after the run
    "POLYFORGE_EVAL_DEMAND_RATE": "count",  # replica-independent demand (090237a)
}
COMPONENTS = ("polyforge-control-plane", "postgres", "nats", "polyforge-operator",
              "polyforge-operator-planner")
IMAGES = {
    "polyforge/control-plane:dev": ["docker", "build", "-q", "-t", "polyforge/control-plane:dev", "."],
    "polyforge/operator:dev": ["docker", "build", "-q", "-f", "Dockerfile.operator",
                               "-t", "polyforge/operator:dev", "."],
    "polyforge/planner:dev": ["docker", "build", "-q", "-f", "services/planner/Dockerfile",
                              "-t", "polyforge/planner:dev", "."],
}
LINKS = (
    ("layers", "every layer is running (control plane, PostgreSQL, NATS, operator, planner)"),
    ("applied", "operator -> Kubernetes: every Policy reports Applied"),
    ("replicas", "Kubernetes runs what the Policies ask (pods = sum of Policy replicas)"),
    ("telemetry", "traffic -> control plane -> PostgreSQL -> feature API"),
    ("planner", "operator -> JCAC planner: every Policy is planner-authored"),
    ("acting", "the planner changes knobs in response to demand"),
    ("audit", "every plan audited on NATS; the run passes the harness's validity checks"),
)
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


@dataclass(frozen=True)
class Policy:
    tenant: str
    replicas: int
    cache_mb: int
    tier: str
    source: str
    applied: bool


def deployments(doc: dict) -> dict[str, tuple[int, int]]:
    return {d["metadata"]["name"]: (int(d.get("spec", {}).get("replicas") or 0),
                                    int(d.get("status", {}).get("readyReplicas") or 0))
            for d in doc.get("items", [])}


def policies(doc: dict) -> list[Policy]:
    rows = []
    for p in doc.get("items", []):
        spec, status = p.get("spec", {}), p.get("status", {})
        applied = any(c.get("type") == "Applied" and c.get("status") == "True"
                      for c in status.get("conditions", []))
        rows.append(Policy(spec.get("tenantRef", "?"), int(spec.get("replicas") or 0),
                           int(spec.get("cacheSizeMB") or 0), spec.get("modelTier", "?"),
                           status.get("lastPlanSource", ""), applied))
    return rows


def parse_run_log(text: str) -> tuple[int | None, bool]:
    audit = re.search(r"\[audit\] (\d+) record", text)
    ok = re.search(r'"ok":\s*true', text) is not None
    return (int(audit.group(1)) if audit else None), ok


@dataclass
class LinkTracker:
    """Each link is FAIL until a sample verifies it; once verified it stays so."""
    passed: dict = field(default_factory=dict)  # key -> detail
    first_seen: dict = field(default_factory=dict)  # tenant -> (replicas, cache, tier)
    planner_seen: bool = False
    audit: tuple | None = None

    def update(self, deps: dict, pols: list[Policy], events: int | None) -> None:
        ready = [n for n in COMPONENTS if deps.get(n, (0, 0))[1] >= 1]
        if len(ready) == len(COMPONENTS):
            self._pass("layers", f"{len(ready)} of {len(COMPONENTS)} components ready")
        if pols and all(p.applied for p in pols):
            self._pass("applied", f"{len(pols)} of {len(pols)} Policies Applied")
        spec, ready_pods = deps.get("polyforge-control-plane", (0, 0))
        wanted = sum(p.replicas for p in pols)
        if pols and spec == wanted == ready_pods:
            self._pass("replicas", f"{ready_pods} pods = sum of {len(pols)} Policies' replicas")
        if events:
            self._pass("telemetry", f"{events} events from {TRACE_TENANT} in the last 10 s")
        if pols and all(p.source == "planner" for p in pols):
            self.planner_seen = True
            self._pass("planner", f"{len(pols)} of {len(pols)} Policies authored by the planner")
        self._track_changes(pols)

    def _track_changes(self, pols: list[Policy]) -> None:
        for p in pols:
            knobs = (p.replicas, p.cache_mb, p.tier)
            before = self.first_seen.setdefault(p.tenant, knobs)
            if knobs != before:
                self._pass("acting", f"{p.tenant}: {before[0]}/{before[1]} MB/{before[2]} -> "
                                     f"{knobs[0]}/{knobs[1]} MB/{knobs[2]}")

    def finish(self, log_text: str) -> None:
        self.audit = parse_run_log(log_text)

    def _pass(self, key: str, detail: str) -> None:
        self.passed.setdefault(key, detail)

    def status(self, key: str) -> str:
        if key == "audit":
            records, ok = self.audit or (None, False)
            return "PASS" if records and ok else "FAIL"
        if key in self.passed:
            return "PASS"
        if key == "acting" and self.planner_seen:
            return "WARN"  # holding every knob is a valid plan at a light load
        return "FAIL"

    def detail(self, key: str) -> str:
        if key == "audit":
            records, ok = self.audit or (None, False)
            return f"{records or 0} plan records on NATS, run {'valid' if ok else 'NOT valid'}"
        if key == "acting" and key not in self.passed and self.planner_seen:
            return "planner held every knob (a valid plan at this load)"
        return self.passed.get(key, "not observed")

    def rows(self) -> list[tuple[str, str, str, str]]:
        return [(key, label, self.status(key), self.detail(key)) for key, label in LINKS]


def _quiet(cmd: list[str], timeout_s: int) -> int:
    """Run a housekeeping command; a timeout counts as failure, never a hang."""
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout_s).returncode
    except subprocess.TimeoutExpired:
        return 1


def missing_prereqs() -> list[str]:
    missing = [t for t in ("docker", "kind", "kubectl", "helm", "k6") if shutil.which(t) is None]
    if "docker" not in missing and _quiet(["docker", "info"], CLI_TIMEOUT_S):
        missing.append("a running Docker engine (start Docker Desktop)")
    return missing


def ensure_images(rebuild: bool, echo) -> None:
    for image, cmd in IMAGES.items():
        have = _quiet(["docker", "image", "inspect", image], CLI_TIMEOUT_S) == 0
        if have and not rebuild:
            continue
        echo(f"  building {image} (the first build downloads base images: ~8 min) ...")
        subprocess.run(cmd, cwd=REPO, check=True, capture_output=True)


def _kubectl(*args: str) -> dict:
    try:
        proc = subprocess.run(["kubectl", *args, "-o", "json"], capture_output=True, text=True,
                              timeout=20)
        return json.loads(proc.stdout) if proc.returncode == 0 else {}
    except (subprocess.TimeoutExpired, ValueError):
        return {}


def recent_events(tenant: str = TRACE_TENANT) -> int | None:
    since = (datetime.now(timezone.utc) - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    request = urllib.request.Request(
        f"{NODE_URL}/v1/tenants/{tenant}/telemetry/features?since={quote(since)}",
        headers={"X-PolyForge-Admin-Key": ADMIN_KEY})
    try:
        with _OPENER.open(request, timeout=5) as resp:
            body = json.loads(resp.read())
    except (OSError, ValueError):
        return None
    return sum(int(i.get("event_count") or 0) for i in body.get("items", []))


def _reset(echo) -> None:
    _quiet(["kind", "delete", "cluster", "--name", CLUSTER], DELETE_TIMEOUT_S)
    for stale in RESULTS.glob("demo_cluster*"):
        if stale.is_dir():
            shutil.rmtree(stale)
        else:
            stale.unlink()
    RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
    echo("  cleared any previous demo cluster and its outputs")


def _watch(proc: subprocess.Popen, tracker: LinkTracker, echo) -> None:
    started = last_beat = time.time()
    announced: set[str] = set()
    while proc.poll() is None and time.time() - started < RUN_TIMEOUT_S:
        tracker.update(deployments(_kubectl("get", "deploy", "-n", NAMESPACE)),
                       policies(_kubectl("get", "policies.polyforge.io", "-A")), recent_events())
        clock = time.strftime("%M:%S", time.gmtime(time.time() - started))
        for key, label, status, detail in tracker.rows():
            if status == "PASS" and key not in announced:
                announced.add(key)
                echo(f"  [{clock}] verified: {label}\n           {detail}")
        if time.time() - last_beat >= HEARTBEAT_S:
            last_beat = time.time()
            waiting = next((label for key, label, status, _ in tracker.rows()
                            if status != "PASS" and key != "audit"), "the run to finish")
            echo(f"  [{clock}] waiting for: {waiting}")
        time.sleep(POLL_S)


def read_metrics() -> dict:
    try:
        return json.loads(EXPORT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@dataclass(frozen=True)
class ClusterResult:
    rows: list
    metrics: dict
    skipped: str = ""


def run(keep_cluster: bool = False, rebuild: bool = False, echo=print) -> ClusterResult:
    if missing := missing_prereqs():
        return ClusterResult([], {}, skipped="missing: " + ", ".join(missing))
    ensure_images(rebuild, echo)
    _reset(echo)
    tracker = LinkTracker()
    env = {**os.environ, **RUN_ENV}
    echo("  starting the cluster run (about 4 min to come up, then 5 min of load)")
    with RUN_LOG.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen([sys.executable, "-m", "harness.runner", EXPERIMENT, "--workers", "1"],
                                cwd=EVAL, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            _watch(proc, tracker, echo)
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=30)
            if not keep_cluster:
                _quiet(["kind", "delete", "cluster", "--name", CLUSTER], DELETE_TIMEOUT_S)
    tracker.finish(RUN_LOG.read_text(encoding="utf-8", errors="replace"))
    return ClusterResult(tracker.rows(), read_metrics())


def render(result: ClusterResult) -> str:
    if result.skipped:
        return f"Part C skipped: {result.skipped}"
    lines = [f"  {status:4s}  {label}\n        {detail}" for _, label, status, detail in result.rows]
    m = result.metrics
    if m:
        lines.append(f"\n  measured: cost ${m.get('total_cost_usd', 0):.2f} for the window, "
                     f"SLO violation {m.get('mean_violation')}, Jain {m.get('mean_jain')}, "
                     f"p95 CRUD {m.get('crud_p95_ms', 0):.1f} ms / AI {m.get('ai_p95_ms', 0):.1f} ms")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep-cluster", action="store_true", help="leave the kind cluster up")
    ap.add_argument("--rebuild-images", action="store_true", help="rebuild the three images first")
    args = ap.parse_args(argv)
    result = run(args.keep_cluster, args.rebuild_images)
    print("\n" + render(result))
    if result.skipped:
        return 2
    return 0 if all(status in ("PASS", "WARN") for _, _, status, _ in result.rows) else 1


if __name__ == "__main__":
    sys.exit(main())
