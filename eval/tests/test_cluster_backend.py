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


# --- three-knob live plane (PREREG_WAVE4_LIVE_PLANE.md) ----------------

TIER_JSON = '{"small": {"kind": "openai", "base_url": "http://h:9101/v1", "model": "m0"}}'


def _live_ai(monkeypatch, shared_pg=True, tiers=TIER_JSON):
    monkeypatch.setattr(cb, "EVAL_LIVE_AI", True)
    monkeypatch.setattr(cb, "EVAL_SHARED_PG", shared_pg)
    monkeypatch.setattr(cb, "TIER_BACKENDS_JSON", tiers)


def test_live_ai_without_shared_pg_fails_loudly(monkeypatch, tmp_path):
    _live_ai(monkeypatch, shared_pg=False)
    with pytest.raises(RuntimeError, match="SHARED_PG"):
        cb.command_plan(_run(), tmp_path)


def test_live_ai_without_tier_backends_fails_loudly(monkeypatch, tmp_path):
    _live_ai(monkeypatch, tiers="")
    with pytest.raises(RuntimeError, match="TIER_BACKENDS"):
        cb.command_plan(_run(), tmp_path)


def test_live_ai_plan_deploys_gateway(monkeypatch, tmp_path):
    _live_ai(monkeypatch)
    plan = cb.command_plan(_run(system="jcac", workload="joint_stress"), tmp_path)
    flat = [" ".join(c) for c in plan]
    assert any(cb.GATEWAY_IMAGE in c and c.startswith("kind load") for c in flat)
    install = next(c for c in plan if c and c[0] == "helm" and "install" in c)
    flags = " ".join(install)
    assert "gateway.enabled=true" in flags
    assert "gw-values.yaml" in flags, "tierBackends JSON must travel via -f, not --set"
    # The operator must be told to push knobs at the gateway.
    operator_install = next(c for c in plan if c and c[0] == "helm" and "polyforge-operator" in " ".join(c))
    assert "gateway.adminURL=" in " ".join(operator_install)
    # And the gateway rollout must gate the run like the control plane's.
    assert any("deployment/polyforge-ai-gateway" in c for c in flat)


def test_default_plan_has_no_gateway_leg(tmp_path):
    plan = cb.command_plan(_run(system="hpa"), tmp_path)
    flat = [" ".join(c) for c in plan]
    assert not any(cb.GATEWAY_IMAGE in c for c in flat)
    assert not any("gateway.enabled=true" in c for c in flat)


def test_k6_script_routes_ai_kinds_to_gateway_when_live(monkeypatch):
    _live_ai(monkeypatch)
    script = cb.k6_script(_run(system="jcac", workload="ai_cacheable"))
    assert "/ai/chat" in script
    assert "const LIVE_AI = true" in script
    # The cacheable cell must carry its reuse pool (64 prompts per tenant).
    pools = json.loads(script.split("const POOLS = ")[1].split(";\n")[0])
    sizes = {len(v) for v in pools.values() if v}
    assert sizes == {64}


def test_k6_script_default_mode_never_touches_gateway():
    script = cb.k6_script(_run(system="hpa", workload="ai_cacheable"))
    assert "const LIVE_AI = false" in script


def test_prompt_pools_deterministic_distinct_and_class_scoped():
    run = _run(system="jcac", workload="ai_cacheable")
    ids = ["t00", "t01"]
    a, b = cb.prompt_pools(run, ids), cb.prompt_pools(run, ids)
    assert a == b, "pools must be deterministic in the run seed"
    assert len(set(a["t00"])) == 64, "pool prompts must be distinct"
    assert a["t00"] != a["t01"], "tenants must not share pools"
    uncacheable = cb.prompt_pools(_run(system="jcac", workload="agentic"), ids)
    assert uncacheable["t00"] is None, "unlisted classes are uncacheable by construction"


def test_wave4_cells_build():
    from harness import workloads
    for cell in ("tier_mixed", "joint_stress"):
        tenant_ids, buckets, _, _ = workloads.build(cell, "uniform", "small", 3, 12)
        assert len(tenant_ids) == 8 and len(buckets) == 13


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


# --- side-loaded images must exist before kind is touched ---------------

def test_images_in_plan_reads_every_sideload_from_the_plan(tmp_path):
    plan = cb.command_plan(_run(), tmp_path)
    images = cb.images_in_plan(plan)
    assert cb.CONTROL_PLANE_IMAGE in images
    assert cb.NATS_IMAGE in images and cb.METRICS_SERVER_IMAGE in images
    # postgres is side-loaded only on the shared-telemetry (R5) route
    assert (cb.POSTGRES_IMAGE in images) == cb.EVAL_SHARED_PG
    # and nothing that is not a kind-load / docker-save target
    assert all(":" in img for img in images)


def test_missing_images_names_exactly_the_absent_ones(monkeypatch):
    present = {cb.NATS_IMAGE}

    class _Done:
        def __init__(self, rc):
            self.returncode = rc

    def fake_run(cmd, **_kw):
        assert cmd[:3] == ["docker", "image", "inspect"]
        return _Done(0 if cmd[3] in present else 1)

    monkeypatch.setattr(cb.subprocess, "run", fake_run)
    images = [cb.NATS_IMAGE, cb.POSTGRES_IMAGE, cb.GATEWAY_IMAGE]
    assert cb.missing_images(images) == [cb.POSTGRES_IMAGE, cb.GATEWAY_IMAGE]


def test_execute_refuses_before_kind_when_an_image_is_absent(monkeypatch):
    """A fresh rented host has none of the images; the failure must name the
    script, not surface as a `docker save` error mid-plan."""
    monkeypatch.setattr(cb, "preflight", lambda: [])
    monkeypatch.setattr(cb, "missing_images", lambda imgs: [cb.GATEWAY_IMAGE])
    touched = []
    monkeypatch.setattr(cb.subprocess, "run",
                        lambda cmd, **kw: touched.append(cmd) or (_ for _ in ()).throw(AssertionError(cmd)))
    with pytest.raises(cb.BackendUnavailable) as exc:
        cb.execute(_run())
    assert "b1_images.sh" in str(exc.value) and cb.GATEWAY_IMAGE in str(exc.value)
    assert touched == [], "no subprocess may run before the image check"


# --- the host the run ran on is evidence, not a claim ------------------

def test_host_facts_survive_a_host_with_no_tools(monkeypatch):
    def no_tool(cmd, **_kw):
        raise FileNotFoundError(cmd[0])
    monkeypatch.setattr(cb.subprocess, "run", no_tool)
    facts = cb.host_facts()
    assert facts["cpu_count"] == cb.os.cpu_count()
    assert facts["gpus"] == [] and facts["tools"]["docker"] is None
    assert facts["machine"] and facts["captured_at"].endswith("Z")


def test_host_facts_parse_nvidia_smi(monkeypatch):
    class _Done:
        def __init__(self, out, rc=0):
            self.stdout, self.returncode = out, rc

    def fake_run(cmd, **_kw):
        if cmd[0] == "nvidia-smi":
            return _Done("NVIDIA A100-SXM4-40GB, 40960 MiB, 22811 MiB, 550.90.07")
        return _Done("", rc=1)
    monkeypatch.setattr(cb.subprocess, "run", fake_run)
    gpu = cb.host_facts()["gpus"][0]
    assert gpu == {"name": "NVIDIA A100-SXM4-40GB", "memory_total": "40960 MiB",
                   "memory_used": "22811 MiB", "driver": "550.90.07"}


def test_preserve_evidence_writes_host_facts(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "host_facts", lambda: {"cpu_count": 30})
    work = tmp_path / "work"; work.mkdir()
    ev = tmp_path / "evidence"
    cb._preserve_evidence(work, ev, None)
    assert json.loads((ev / "host_facts.json").read_text()) == {"cpu_count": 30}


def test_live_ai_install_lifts_the_gateway_limiter_like_the_control_planes(monkeypatch, tmp_path):
    """joint_stress bursts a tenant to ~1,200 AI RPM; the gateway's default
    budget is 600. Session 48's per-request k6 CSV put 734 of 740 failures
    on HTTP 429 from that limiter -- the '8 cores cannot serve the cell'
    reading was this policy. The eval install must lift it exactly as it
    lifts the control plane's, on the same helm install."""
    _live_ai(monkeypatch)
    plan = cb.command_plan(_run(), tmp_path)
    installs = [" ".join(c) for c in plan if c[:2] == ["helm", "install"]]
    gw = [c for c in installs if "gateway.enabled=true" in c]
    assert gw, "no helm install enables the gateway on the live-AI plane"
    for c in gw:
        assert "--set=gateway.rateLimit.requestsPerMinute=1000000" in c
        assert "--set=gateway.rateLimit.burst=100000" in c
        # and the control plane's, which was already lifted
        assert "--set=rateLimit.requestsPerMinute=1000000" in c


def test_no_thread_subclass_shadows_thread_stop():
    """Thread.join() on Python 3.10 calls self._stop(); a subclass that stores
    an Event under that name breaks join() with "'Event' object is not
    callable" -- after the load window, on the rented host, every run. CI's
    3.13 never calls it, so this test checks the name, not the behaviour."""
    import inspect, threading
    for name, cls in inspect.getmembers(cb, inspect.isclass):
        if issubclass(cls, threading.Thread) and cls is not threading.Thread:
            src = inspect.getsource(cls)
            assert "self._stop =" not in src and "self._stop=" not in src, name


def test_load_distribution_sampler_joins_after_stop():
    s = cb.LoadDistributionSampler.__new__(cb.LoadDistributionSampler)
    threading_thread_init = cb.threading.Thread.__init__
    threading_thread_init(s, daemon=True)
    s._halt = cb.threading.Event(); s.samples = 0; s.failures = 0
    s._sample = lambda: None
    s.start(); s.stop(); s.join(timeout=5)
    assert not s.is_alive()


def test_ai_gateway_load_path_is_a_nodeport_not_a_port_forward(monkeypatch, tmp_path):
    """Session 48: 127-220 EOFs per run between k6 and the gateway pod with
    no gateway-side error -- kubectl port-forward dropping streams, the WP14
    attempt-4 defect on the AI path. Both load paths now enter through kind
    host-port mappings and kube-proxy."""
    _live_ai(monkeypatch)
    import inspect
    src = inspect.getsource(cb.execute)
    assert 'port-forward",' not in src, "execute() still spawns a kubectl port-forward"
    cfg = cb.kind_config("small")
    assert f"containerPort: {cb.NODE_PORT}" in cfg and f"containerPort: {cb.GATEWAY_NODE_PORT}" in cfg
    svc = cb.nodeport_service()
    assert "polyforge-ai-gateway-nodeport" in svc and f"nodePort: {cb.GATEWAY_NODE_PORT}" in svc
    assert "app.kubernetes.io/name: polyforge-ai-gateway" in svc


def test_every_run_keeps_its_own_evidence(tmp_path, monkeypatch):
    """The 2026-09-15 sitting overwrote 15 of 16 per-run exports. Each run
    now also lands in <experiment>_evidence/runs/<arm>__<cell>__<mix>__<size>__repN/."""
    run = _run(system="jcac", workload="joint_stress")
    sub = cb.run_evidence_dir(run)
    assert sub.parent.name == "runs" and sub.name == "jcac__joint_stress__uniform__small__rep0"
    assert sub.parent.parent == cb.evidence_dir_for(run)
    monkeypatch.setattr(cb, "host_facts", lambda: {"cpu_count": 8})
    work = tmp_path / "work"; work.mkdir()
    (work / "k6-summary.json").write_text("{}", encoding="utf-8")
    (work / "metrics_histogram.json").write_text('{"metric": "x"}', encoding="utf-8")
    ev = tmp_path / "evidence"; per = ev / "runs" / "jcac__joint_stress"
    cb._preserve_evidence(work, ev, None, per_run=per)
    for name in ("k6-summary.json", "metrics_histogram.json", "host_facts.json"):
        assert (ev / name).exists() and (per / name).exists(), name


def test_every_operator_arm_has_chart_values():
    """jcac-calibrated was registered in systems.py and OPERATOR_SYSTEMS but
    not in HELM_VALUES_BY_SYSTEM, and the first run of it failed at
    command_plan with a KeyError -- on a paid box. Every arm the cluster
    backend can be asked to run must carry its chart values."""
    from harness.systems import SYSTEMS
    for name in cb.OPERATOR_SYSTEMS:
        assert name in cb.HELM_VALUES_BY_SYSTEM, name
        assert name in SYSTEMS, name
    for name in ("jcac", "replica-only", "cache-only", "tier-only", "jcac-calibrated"):
        assert name in cb.HELM_VALUES_BY_SYSTEM, name
