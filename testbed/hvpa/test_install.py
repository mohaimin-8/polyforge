"""The WP17 installer without a cluster: the lock, the verified download, the patches."""

import hashlib
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import install  # noqa: E402

MS = """
apiVersion: apps/v1
kind: Deployment
metadata: {name: metrics-server, namespace: kube-system}
spec:
  template:
    spec:
      containers:
        - name: metrics-server
          image: registry.k8s.io/metrics-server/metrics-server:v0.9.1
          args: [--secure-port=10250]
---
apiVersion: v1
kind: Service
metadata: {name: metrics-server}
"""


def test_lock_names_the_three_release_files():
    lock = install.read_lock()
    assert set(lock) == {"metrics-server", "keda", "vpa-chart"}
    assert all(len(e["sha256"]) == 64 and e["url"].startswith("https://github.com/") for e in lock.values())


def test_a_download_is_refused_unless_it_matches(tmp_path):
    body = b"payload"
    entry = {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(), "url": "https://x/y/file.yaml"}
    path = install.fetch_verified("x", entry, tmp_path, download=lambda u: body)
    assert path.read_bytes() == body
    bad = {**entry, "sha256": "0" * 64, "url": "https://x/y/other.yaml"}
    with pytest.raises(ValueError, match="does not match"):
        install.fetch_verified("x", bad, tmp_path, download=lambda u: body)


def test_metrics_server_gets_insecure_kubelet_tls_and_never_pulls():
    docs = install.metrics_server_docs(MS)
    c = docs[0]["spec"]["template"]["spec"]["containers"][0]
    assert "--kubelet-insecure-tls" in c["args"] and c["imagePullPolicy"] == "Never"
    assert install.metrics_server_docs(yaml.safe_dump_all(docs))[0]["spec"]["template"]["spec"]["containers"][0][
        "args"].count("--kubelet-insecure-tls") == 1


def test_vpa_values_set_the_recommender_flags_of_the_setting():
    assert install.vpa_values("default")["recommender"]["extraArgs"] == []
    fast = install.vpa_values("fast")
    assert fast["recommender"]["extraArgs"] == ["--cpu-histogram-decay-half-life=10m", "--confidence-interval-cpu=1h"]
    assert fast["admissionController"]["certGen"]["image"]["pullPolicy"] == "Never"
    with pytest.raises(ValueError):
        install.vpa_values("slow")


def test_the_fast_flags_are_the_twins_fast_setting():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research" / "hvpa"))
    import vpa

    assert vpa.SETTINGS["fast"] == {"half_life_s": 600.0, "confidence_interval_s": 3600.0}
