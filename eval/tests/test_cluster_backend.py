"""Cluster-backend failure-mode + plan-construction tests (gap 4.5b).

The cluster backend only fully runs on a Docker host, so its error guards
and the deterministic command/manifest builders are exactly the paths a
live sitting never exercises on the happy path. These tests pin them
without a cluster: the environment guard must fail loudly, the tenant
provisioner must tolerate the idempotent-retry 409 but surface real
errors, and the command plan must have the invariants the live run
depends on (delete-first tolerated, operator images/CRs only for the
operator arm, capacity-parity bounds present).
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL_DIR))

from harness import cluster_backend as cb  # noqa: E402
from harness.config import ExperimentSpec, expand  # noqa: E402


def _run(system="jcac", workload="crud_bursty", mix="uniform", cluster="small"):
    spec = ExperimentSpec(name="clus", backend="cluster", steps=12, reps=1,
                          systems=[system], workloads=[workload],
                          tenant_mixes=[mix], cluster_sizes=[cluster])
    return expand(spec)[0]


# --- environment guard (the loud-failure path) ------------------------

def test_execute_raises_backend_unavailable_when_tools_missing(monkeypatch):
    monkeypatch.setattr(cb.shutil, "which", lambda _tool: None)
    with pytest.raises(cb.BackendUnavailable) as exc:
        cb.execute(_run())
    # The message must name what to do, not just fail.
    assert "PATH" in str(exc.value) or "Docker" in str(exc.value)


def test_preflight_lists_exactly_the_missing_tools(monkeypatch):
    present = {"docker", "kubectl"}
    monkeypatch.setattr(cb.shutil, "which",
                        lambda tool: "/usr/bin/" + tool if tool in present else None)
    missing = set(cb.preflight())
    assert missing == set(cb.REQUIRED_TOOLS) - present


# --- command plan invariants ------------------------------------------

def test_plan_starts_with_idempotent_delete(tmp_path):
    plan = cb.command_plan(_run(), tmp_path)
    assert plan[0][:3] == ["kind", "delete", "cluster"], "pre-clean must be first"
    # execute() tolerates only the first step failing (no leftover cluster).
    assert plan[-1][:3] == ["kind", "delete", "cluster"], "teardown must be last"


def test_operator_images_and_crs_only_for_operator_arm(tmp_path):
    jcac = cb.command_plan(_run(system="jcac"), tmp_path)
    hpa = cb.command_plan(_run(system="hpa"), tmp_path)
    flat_jcac = [" ".join(c) for c in jcac]
    flat_hpa = [" ".join(c) for c in hpa]
    assert any(cb.OPERATOR_IMAGE in c for c in flat_jcac)
    assert any(cb.PLANNER_IMAGE in c for c in flat_jcac)
    # The reactive baseline must not load operator/planner images.
    assert not any(cb.OPERATOR_IMAGE in c for c in flat_hpa)
    assert not any(cb.PLANNER_IMAGE in c for c in flat_hpa)


def test_plan_carries_capacity_parity_bounds(tmp_path):
    plan = cb.command_plan(_run(mix="uniform", cluster="small"), tmp_path)
    install = next(c for c in plan if c and c[0] == "helm")
    flags = " ".join(install)
    assert "replicaCount=" in flags   # every arm starts at the sim's world
    assert "autoscaling.hpa.maxReplicas=" in flags  # and shares the ceiling


def test_helm_chart_path_is_absolute(tmp_path):
    plan = cb.command_plan(_run(), tmp_path)
    install = next(c for c in plan if c and c[0] == "helm")
    chart = install[install.index("install") + 2]
    assert Path(chart).is_absolute(), "relative chart path misparses as a repo ref"


# --- kind_mix normalization -------------------------------------------

def test_kind_mix_cumulative_reaches_exactly_one():
    from harness import workloads
    _, buckets, _, _ = workloads.build("ai_cacheable", "uniform", "small", 7, 12)
    tid = sorted({t for b in buckets for t in b})[0]
    pairs = cb.kind_mix(buckets, tid)
    if pairs:  # a tenant with any traffic
        assert pairs[-1][1] == 1.0, "cumulative probability must terminate at 1.0"
        assert all(0.0 <= p <= 1.0 for _, p in pairs)
        assert pairs == sorted(pairs, key=lambda kp: kp[1]), "must be monotone"


# --- provision_tenants error handling ---------------------------------

class _ProvHandler(BaseHTTPRequestHandler):
    fail_keys_for = ""  # class attribute set per-test

    def log_message(self, *_):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        _ = self.rfile.read(length)
        if self.path == "/v1/tenants":
            self._reply(200, {})
        elif self.path.endswith("/api-keys"):
            if self.fail_keys_for and self.fail_keys_for in self.path:
                self._reply(500, {"error": "boom"})
            else:
                self._reply(200, {"secret": "pf_key_" + self.path.split("/")[3]})
        else:
            self._reply(404, {})

    def _reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def prov_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ProvHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", _ProvHandler
    server.shutdown()


def test_provision_tenants_returns_a_token_per_tenant(prov_server):
    base, handler = prov_server
    handler.fail_keys_for = ""
    tokens = cb.provision_tenants(base, ["acme", "globex"])
    assert set(tokens) == {"acme", "globex"}
    assert all(v.startswith("pf_key_") for v in tokens.values())


def test_provision_tenants_surfaces_key_mint_failure(prov_server):
    base, handler = prov_server
    handler.fail_keys_for = "acme"  # the key mint 500s for acme
    with pytest.raises((RuntimeError, KeyError)):
        cb.provision_tenants(base, ["acme"])
    handler.fail_keys_for = ""


# --- _wait_http timeout -----------------------------------------------

def test_wait_http_times_out_on_dead_endpoint():
    # An unroutable port must raise within the deadline, not hang forever.
    with pytest.raises(RuntimeError):
        cb._wait_http("http://127.0.0.1:9/healthz", deadline_s=1.0)
