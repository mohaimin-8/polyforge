"""The static checks pass on the shipped manifests and catch seeded faults."""

from __future__ import annotations

import copy
import json
import shutil

import pytest
import yaml

import validate_static as vs

KUBECTL = shutil.which("kubectl")
needs_kubectl = pytest.mark.skipif(KUBECTL is None, reason="kubectl not on PATH")


@pytest.fixture(scope="module")
def mode_b() -> list[dict]:
    if KUBECTL is None:
        pytest.skip("kubectl not on PATH")
    return vs.render(vs.OVERLAYS["mode-b"])


@needs_kubectl
def test_shipped_testbed_has_no_findings():
    assert vs.validate() == {"mode-b": [], "mode-a": []}


def test_set_digest_matches_the_loader_definition():
    rows = vs.model_lock_rows()
    small = [r for r in rows if r.startswith("small\t")]
    assert vs.set_digest(rows, "small") == __import__("hashlib").sha256((small[0] + "\n").encode()).hexdigest()
    with pytest.raises(ValueError):
        vs.set_digest(rows, "huge")


def test_crlf_lock_is_refused(tmp_path):
    lock = tmp_path / "models.lock"
    lock.write_bytes(b"small\ta\tb\t1\tu\r\n")
    with pytest.raises(ValueError, match="CR"):
        vs.model_lock_rows(lock)


def deployment(docs, name):
    return next(d for d in docs if d.get("kind") == "Deployment" and d["metadata"]["name"] == name)


def test_wrong_digest_and_wrong_model_are_caught(mode_b):
    docs = copy.deepcopy(mode_b)
    small = deployment(docs, "llama-small")["spec"]["template"]["spec"]
    small["initContainers"][0]["env"][0]["value"] = "0" * 64
    args = small["containers"][0]["args"]
    args[args.index("--model") + 1] = "/models/qwen2.5-1.5b-instruct-q4_k_m.gguf"
    findings = vs.check_models(docs, vs.model_lock_rows())
    assert any("EXPECTED_SET_DIGEST" in f for f in findings)
    assert any("is not a file of set small" in f for f in findings)


def test_threads_disagreeing_with_cpu_limit_is_caught(mode_b):
    docs = copy.deepcopy(mode_b)
    c = deployment(docs, "llama-mid")["spec"]["template"]["spec"]["containers"][0]
    c["args"][c["args"].index("--threads") + 1] = "4"
    bounds = json.loads(vs.BOUNDS.read_text())
    assert any("llama-mid: cores disagree" in f for f in vs.check_cores(docs, bounds))


def test_unpinned_image_and_pull_policy_are_caught(mode_b):
    docs = copy.deepcopy(mode_b)
    c = deployment(docs, "prometheus")["spec"]["template"]["spec"]["containers"][0]
    c["image"] = "docker.io/prom/prometheus:latest"
    c["imagePullPolicy"] = "IfNotPresent"
    findings = vs.check_images(docs, vs.images_lock.read_lock())
    assert len([f for f in findings if "Deployment/prometheus" in f]) == 3


def test_routing_and_port_faults_are_caught(mode_b):
    docs = copy.deepcopy(mode_b)
    cm = next(d for d in docs if d.get("kind") == "ConfigMap" and d["metadata"]["name"].startswith("gateway-tiers"))
    tiers = json.loads(cm["data"]["gateway-tiers.json"])
    tiers["mid"]["base_url"] = "http://tier-lb:9009/v1"
    cm["data"]["gateway-tiers.json"] = json.dumps(tiers)
    assert any("gateway tier mid" in f for f in vs.check_routing(docs))
    kind = yaml.safe_load(vs.KIND_CONFIG.read_text())
    kind["nodes"][0]["extraPortMappings"].pop()
    kind["nodes"][0]["extraPortMappings"][0]["listenAddress"] = "0.0.0.0"
    findings = vs.check_ports(docs, kind)
    assert any("not bound to 127.0.0.1" in f for f in findings)
    assert any("NodePort 30092 has no kind extraPortMapping" in f for f in findings)


def test_scrape_port_must_be_declared(mode_b):
    docs = copy.deepcopy(mode_b)
    deployment(docs, "ai-gateway")["spec"]["template"]["metadata"]["annotations"]["prometheus.io/port"] = "9999"
    assert vs.check_scrape(docs) == ["Deployment/ai-gateway: scrape port 9999 is not a container port"]
