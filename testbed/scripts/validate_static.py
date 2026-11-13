"""Static consistency checks of the WP10 testbed (no cluster, no network).

Renders both overlays with `kubectl kustomize` and checks what a schema
validator cannot see: that the pieces agree with each other.

  images     every container image is a tag of images.lock (workload or build)
             with imagePullPolicy Never; no floating tags
  models     every wait-model init container waits for the digest models.lock
             gives its set; llama-server's --model is a file of that set; the
             loader's MODEL_SETS covers every set the overlay waits for
  cores      llama-server --threads == --threads-batch == CPU request == CPU
             limit == bounds.json cores_per_pod; bounds name real Deployments
  routing    gateway-tiers.json URLs hit tier-lb ports that Envoy listens on;
             Envoy's clusters resolve the headless tier Services
  ports      every NodePort has a kind port mapping on 127.0.0.1, and back
  scrape     every prometheus.io/port annotation is a declared container port

    python validate_static.py [--kubectl kubectl]     # exit 1 on any finding
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Sequence

import yaml

HERE = Path(__file__).resolve().parent
TESTBED = HERE.parent
BASE = TESTBED / "k8s" / "base"
OVERLAYS = {"mode-b": TESTBED / "k8s" / "mode-b", "mode-a": TESTBED / "k8s" / "mode-a"}
KIND_CONFIG = TESTBED / "kind" / "kind-config.yaml"
BOUNDS = TESTBED / "actuator" / "bounds.json"
sys.path.insert(0, str(HERE))
import images as images_lock  # noqa: E402


def render(overlay: Path, kubectl: str = "kubectl") -> list[dict]:
    proc = subprocess.run([kubectl, "kustomize", str(overlay)], capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"kubectl kustomize {overlay} failed: {proc.stderr.strip()}")
    return [d for d in yaml.safe_load_all(proc.stdout) if d]


def model_lock_rows(path: Path = BASE / "models.lock") -> list[str]:
    text = path.read_bytes().decode("utf-8")
    if "\r" in text:
        raise ValueError(f"{path} has CR line endings: the in-cluster digest would differ")
    return [line for line in text.split("\n") if line and not line.startswith("#")]


def set_digest(rows: Sequence[str], name: str) -> str:
    """sha256 of the set's lock lines, as load-models.sh computes it."""
    chosen = [r for r in rows if r.split("\t")[0] == name]
    if not chosen:
        raise ValueError(f"models.lock has no set {name!r}")
    return hashlib.sha256("".join(r + "\n" for r in chosen).encode("utf-8")).hexdigest()


def pod_specs(docs: Iterable[dict]) -> Iterable[tuple[str, dict]]:
    for d in docs:
        if d.get("kind") in ("Deployment", "Job"):
            yield f"{d['kind']}/{d['metadata']['name']}", d["spec"]["template"]["spec"]


def arg_value(args: Sequence[str], flag: str) -> str | None:
    for i, a in enumerate(args):
        if a == flag and i + 1 < len(args):
            return args[i + 1]
    return None


def env_value(container: dict, name: str) -> str | None:
    for e in container.get("env") or []:
        if e.get("name") == name:
            return e.get("value")
    return None


def check_images(docs: list[dict], lock: Sequence[images_lock.Image]) -> list[str]:
    allowed = {i.ref for i in lock if i.role in ("workload", "build")}
    findings = []
    for where, spec in pod_specs(docs):
        for c in (spec.get("initContainers") or []) + spec["containers"]:
            if c["image"] not in allowed:
                findings.append(f"{where}/{c['name']}: image {c['image']} is not in images.lock")
            if c.get("imagePullPolicy") != "Never":
                findings.append(f"{where}/{c['name']}: imagePullPolicy must be Never (side-loaded images)")
            if c["image"].endswith(":latest") or ":" not in c["image"]:
                findings.append(f"{where}/{c['name']}: floating image reference {c['image']}")
    return findings


READY_MARKER = re.compile(r"/models/\.ready/([A-Za-z0-9_-]+)")


def check_models(docs: list[dict], rows: Sequence[str]) -> list[str]:
    findings, waited = [], set()
    paths = {r.split("\t")[1]: r.split("\t")[0] for r in rows}
    sets = {r.split("\t")[0] for r in rows}
    loader_sets: set[str] = set()
    for where, spec in pod_specs(docs):
        for c in spec.get("initContainers") or []:
            if c["name"] != "wait-model":
                continue
            found = set(READY_MARKER.findall(" ".join(c.get("args") or [])))
            if len(found) != 1 or not found <= sets:
                findings.append(f"{where}: wait-model must wait for exactly one models.lock set, found {sorted(found)}")
                continue
            name = found.pop()
            waited.add(name)
            if env_value(c, "EXPECTED_SET_DIGEST") != set_digest(rows, name):
                findings.append(f"{where}: EXPECTED_SET_DIGEST is not models.lock's digest of set {name}")
            for main in spec["containers"]:
                model = arg_value(main.get("args") or [], "--model")
                if model and paths.get(model.removeprefix("/models/")) != name:
                    findings.append(f"{where}: --model {model} is not a file of set {name}")
        if where == "Job/model-loader":
            loader_sets = set((env_value(spec["containers"][0], "MODEL_SETS") or "").split())
    if not loader_sets:
        findings.append("Job/model-loader: MODEL_SETS is empty or the Job is missing")
    missing = waited - loader_sets
    if missing:
        findings.append(f"Job/model-loader does not load sets {sorted(missing)} that pods wait for")
    return findings


def check_cores(docs: list[dict], bounds: dict) -> list[str]:
    findings = []
    deployments = {d["metadata"]["name"]: d for d in docs if d.get("kind") == "Deployment"}
    for tier, tb in bounds["tiers"].items():
        d = deployments.get(tb["deployment"])
        if d is None:
            findings.append(f"bounds.json tier {tier}: no Deployment {tb['deployment']}")
            continue
        c = d["spec"]["template"]["spec"]["containers"][0]
        res = c.get("resources", {})
        values = {"--threads": arg_value(c["args"], "--threads"),
                  "--threads-batch": arg_value(c["args"], "--threads-batch"),
                  "cpu request": str(res.get("requests", {}).get("cpu")),
                  "cpu limit": str(res.get("limits", {}).get("cpu")),
                  "cores_per_pod": str(tb["cores_per_pod"])}
        if len(set(values.values())) != 1:
            findings.append(f"{tb['deployment']}: cores disagree {values}")
        if d["spec"]["template"]["metadata"]["labels"].get("polyforge.io/tier") != tier:
            findings.append(f"{tb['deployment']}: pod label polyforge.io/tier is not {tier}")
    return findings


def envoy_config(docs: list[dict]) -> dict:
    for d in docs:
        if d.get("kind") == "ConfigMap" and d["metadata"]["name"].startswith("tier-lb"):
            return yaml.safe_load(d["data"]["envoy.yaml"])
    raise ValueError("no tier-lb ConfigMap rendered")


def check_routing(docs: list[dict]) -> list[str]:
    findings = []
    envoy = envoy_config(docs)
    listen = {l["address"]["socket_address"]["port_value"] for l in envoy["static_resources"]["listeners"]}
    services = {d["metadata"]["name"]: d for d in docs if d.get("kind") == "Service"}
    for c in envoy["static_resources"]["clusters"]:
        host = c["load_assignment"]["endpoints"][0]["lb_endpoints"][0]["endpoint"]["address"]["socket_address"]["address"]
        svc = services.get(host.split(".")[0])
        if svc is None or svc["spec"].get("clusterIP") != "None":
            findings.append(f"envoy cluster {c['name']}: {host} is not a headless Service")
    tiers_cm = next(d for d in docs if d.get("kind") == "ConfigMap" and d["metadata"]["name"].startswith("gateway-tiers"))
    tiers = json.loads(tiers_cm["data"]["gateway-tiers.json"])
    lb_ports = {p["port"] for p in services["tier-lb"]["spec"]["ports"]}
    for tier, spec in tiers.items():
        url = spec["base_url"]
        port = int(url.split(":")[2].split("/")[0])
        if not url.startswith("http://tier-lb:") or port not in listen or port not in lb_ports:
            findings.append(f"gateway tier {tier}: {url} is not an Envoy listener behind Service tier-lb")
    return findings


def check_ports(docs: list[dict], kind_config: dict) -> list[str]:
    findings = []
    mappings = kind_config["nodes"][0].get("extraPortMappings", [])
    mapped = {m["containerPort"] for m in mappings}
    for m in mappings:
        if m.get("listenAddress") != "127.0.0.1":
            findings.append(f"kind port {m['containerPort']} is not bound to 127.0.0.1")
    node_ports = {p["nodePort"] for d in docs if d.get("kind") == "Service"
                  for p in d["spec"].get("ports", []) if "nodePort" in p}
    for p in sorted(node_ports - mapped):
        findings.append(f"NodePort {p} has no kind extraPortMapping")
    for p in sorted(mapped - node_ports):
        findings.append(f"kind maps {p} but no Service uses that NodePort")
    return findings


def check_scrape(docs: list[dict]) -> list[str]:
    findings = []
    for d in docs:
        if d.get("kind") != "Deployment":
            continue
        meta = d["spec"]["template"]["metadata"]
        port = (meta.get("annotations") or {}).get("prometheus.io/port")
        if port is None:
            continue
        declared = {p["containerPort"] for c in d["spec"]["template"]["spec"]["containers"] for p in c.get("ports", [])}
        if int(port) not in declared:
            findings.append(f"Deployment/{d['metadata']['name']}: scrape port {port} is not a container port")
    return findings


def validate(kubectl: str = "kubectl") -> dict[str, list[str]]:
    lock = images_lock.read_lock()
    rows = model_lock_rows()
    bounds = json.loads(BOUNDS.read_text(encoding="utf-8"))
    kind_config = yaml.safe_load(KIND_CONFIG.read_text(encoding="utf-8"))
    report: dict[str, list[str]] = {}
    for name, overlay in OVERLAYS.items():
        docs = render(overlay, kubectl)
        findings = (check_images(docs, lock) + check_models(docs, rows) + check_routing(docs)
                    + check_ports(docs, kind_config) + check_scrape(docs))
        if name == "mode-b":
            findings += check_cores(docs, bounds)
        report[name] = findings
    return report


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--kubectl", default="kubectl")
    args = ap.parse_args(argv)
    report = validate(args.kubectl)
    for name, findings in report.items():
        print(f"{name}: {'ok' if not findings else f'{len(findings)} finding(s)'}")
        for f in findings:
            print(f"  - {f}")
    return 1 if any(report.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
