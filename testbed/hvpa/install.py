"""Install WP17's cluster components on the WP10 kind cluster, pinned (programme 2).

Release files come from components.lock (downloaded into testbed/out/hvpa/,
refused unless size and sha256 match); images from images.lock, side-loaded
into kind first (`python testbed/scripts/images.py --lock testbed/hvpa/images.lock
pull|build|load|verify`), so every container here has imagePullPolicy Never.

  metrics-server v0.9.1  components.yaml plus --kubelet-insecure-tls (kind's
                         kubelet certificate is self-signed);
  KEDA v2.21.0           keda-2.21.0.yaml, server-side apply (its CRDs are
                         too large for client-side apply's annotation);
  VPA 1.8.1              chart 0.15.0 into kube-system; the recommender's
                         flags for --vpa-setting (research/hvpa/vpa.SETTINGS):
                         default = the upstream defaults, fast =
                         --cpu-histogram-decay-half-life=10m
                         --confidence-interval-cpu=1h;
  WP17 namespace         k8s/base.yaml (the service), k8s/prometheus.yaml.

    python install.py [--vpa-setting default|fast] [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Callable, Sequence

import yaml

HERE = Path(__file__).resolve().parent
TESTBED = HERE.parent
OUT = TESTBED / "out" / "hvpa"
CONTEXT = "kind-polyforge-testbed"
FAST_FLAGS = ["--cpu-histogram-decay-half-life=10m", "--confidence-interval-cpu=1h"]

Runner = Callable[[Sequence[str], str | None], int]


def read_lock(path: Path = HERE / "components.lock") -> dict[str, dict]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            name, size, sha, url = line.split("\t")
            out[name] = {"bytes": int(size), "sha256": sha, "url": url}
    return out


def fetch_verified(name: str, entry: dict, out: Path = OUT,
                   download: Callable[[str], bytes] = lambda u: urllib.request.urlopen(u, timeout=120).read()) -> Path:
    path = out / Path(entry["url"]).name
    if not path.exists():
        out.mkdir(parents=True, exist_ok=True)
        path.write_bytes(download(entry["url"]))
    data = path.read_bytes()
    if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise ValueError(f"{name}: {path} does not match components.lock (size or sha256)")
    return path


def never_pull(docs: list[dict]) -> list[dict]:
    """Every container and init container of every workload: imagePullPolicy Never."""
    for d in docs:
        spec = (d or {}).get("spec", {})
        pod = spec.get("template", {}).get("spec") if d and d.get("kind") in ("Deployment", "Job", "DaemonSet") else None
        if pod:
            for c in pod.get("containers", []) + pod.get("initContainers", []):
                c["imagePullPolicy"] = "Never"
    return docs


def metrics_server_docs(text: str) -> list[dict]:
    docs = [d for d in yaml.safe_load_all(text) if d]
    for d in docs:
        if d["kind"] == "Deployment" and d["metadata"]["name"] == "metrics-server":
            args = d["spec"]["template"]["spec"]["containers"][0].setdefault("args", [])
            if "--kubelet-insecure-tls" not in args:
                args.append("--kubelet-insecure-tls")
    return never_pull(docs)


def vpa_values(setting: str) -> dict:
    if setting not in ("default", "fast"):
        raise ValueError(f"unknown VPA setting {setting!r}")
    image = {"pullPolicy": "Never"}
    return {"admissionController": {"image": dict(image), "certGen": {"image": dict(image)}},
            "recommender": {"image": dict(image), "extraArgs": FAST_FLAGS if setting == "fast" else []},
            "updater": {"image": dict(image)}}


def run_cmd(cmd: Sequence[str], stdin: str | None = None) -> int:
    return subprocess.run(list(cmd), input=stdin, text=True).returncode


def install(setting: str, dry_run: bool, run: Runner = run_cmd) -> int:
    lock = read_lock()
    kc = ["kubectl", "--context", CONTEXT]
    dry = ["--dry-run=server"] if dry_run else []
    ms = metrics_server_docs(fetch_verified("metrics-server", lock["metrics-server"]).read_text(encoding="utf-8"))
    keda = never_pull([d for d in yaml.safe_load_all(fetch_verified("keda", lock["keda"]).read_text(encoding="utf-8")) if d])
    chart = fetch_verified("vpa-chart", lock["vpa-chart"])
    values = OUT / f"vpa-values-{setting}.yaml"
    values.write_text(yaml.safe_dump(vpa_values(setting)), encoding="utf-8")
    steps = [
        (kc + ["apply", *dry, "-f", "-"], yaml.safe_dump_all(ms)),
        (kc + ["apply", "--server-side", "--force-conflicts", *dry, "-f", "-"], yaml.safe_dump_all(keda)),
        (["helm", "--kube-context", CONTEXT, "upgrade", "--install", "vpa", str(chart), "--namespace", "kube-system",
          "-f", str(values), *(["--dry-run"] if dry_run else [])], None),
        (kc + ["apply", *dry, "-f", str(HERE / "k8s" / "base.yaml"), "-f", str(HERE / "k8s" / "prometheus.yaml")], None),
    ]
    for cmd, stdin in steps:
        print("+ " + " ".join(cmd), flush=True)
        rc = run(cmd, stdin)
        if rc != 0:
            return rc
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vpa-setting", default="default", choices=("default", "fast"))
    ap.add_argument("--dry-run", action="store_true", help="server-side dry run; helm --dry-run")
    args = ap.parse_args(argv)
    return install(args.vpa_setting, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
