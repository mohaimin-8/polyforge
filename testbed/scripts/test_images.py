"""Unit tests of the image side-loading plan (no Docker, no kind)."""

from __future__ import annotations

import json
import subprocess

import pytest

import images as im

IMAGES = im.read_lock()


def by_ref(ref: str) -> im.Image:
    return next(i for i in IMAGES if i.ref == ref)


def test_lock_parses_and_pins_every_pulled_image():
    roles = {i.role for i in IMAGES}
    assert roles == {"cluster", "workload", "build"}
    for i in IMAGES:
        if i.role != "build":
            assert i.index_digest.startswith("sha256:") and len(i.index_digest) == 71


def test_lock_rejects_malformed_rows(tmp_path):
    bad = tmp_path / "images.lock"
    bad.write_text("workload\tdocker.io/x\t1\tsha256:a\n")
    with pytest.raises(ValueError, match="7 tab-separated"):
        im.read_lock(bad)
    bad.write_text("workload\tdocker.io/x\t1\tlatest\tsha256:b\t1\tsrc\n")
    with pytest.raises(ValueError, match="digests"):
        im.read_lock(bad)


def test_pull_is_by_digest_then_tagged():
    steps = im.pull_steps(IMAGES)
    llama = by_ref("ghcr.io/ggml-org/llama.cpp:server-b9967")
    assert ["docker", "pull", "--platform", "linux/amd64", llama.pinned] in steps
    assert ["docker", "tag", llama.pinned, llama.ref] in steps
    assert not any("polyforge/" in " ".join(s) for s in steps)


def test_load_saves_single_platform_archives_and_skips_the_node_image():
    steps = im.load_steps(IMAGES, "polyforge-testbed")
    saves = [s for s in steps if s[:2] == ["docker", "save"]]
    assert all(s[2:4] == ["--platform", "linux/amd64"] for s in saves)
    assert not any("kindest/node" in " ".join(s) for s in steps)
    loads = [s for s in steps if s[:3] == ["kind", "load", "image-archive"]]
    assert len(loads) == len(saves) == sum(1 for i in IMAGES if i.role != "cluster")


def test_build_uses_the_repository_dockerfiles():
    assert ["docker", "build", "-f", "Dockerfile.gateway", "-t", "polyforge/ai-gateway:dev", "."] in im.build_steps(IMAGES)


def fake_run(outputs: dict[tuple, tuple[int, str]]):
    def run(cmd):
        code, out = outputs.get(tuple(cmd), (1, ""))
        return subprocess.CompletedProcess(cmd, code, out, "no such image")
    return run


def test_local_pin_check_flags_unpinned_and_missing_images():
    workloads = [i for i in IMAGES if i.role == "workload"]
    first, second = workloads[0], workloads[1]
    outputs = {("docker", "image", "inspect", "--format", "{{json .RepoDigests}}", first.ref):
               (0, json.dumps([first.pinned])),
               ("docker", "image", "inspect", "--format", "{{json .RepoDigests}}", second.ref):
               (0, json.dumps([f"{second.repository}@sha256:{'0' * 64}"]))}
    problems = im.check_local_pins(workloads, run=fake_run(outputs))
    assert not any(first.ref in p for p in problems)
    assert any(second.ref in p and "lack the pinned" in p for p in problems)
    assert len(problems) == len(workloads) - 1


def test_a_docker_hub_pin_reported_by_its_familiar_name_passes():
    hub = next(i for i in IMAGES if i.role == "workload" and i.repository == "docker.io/library/postgres")
    familiar = "postgres@" + hub.index_digest
    run = fake_run({("docker", "image", "inspect", "--format", "{{json .RepoDigests}}", hub.ref):
                    (0, json.dumps([familiar]))})
    assert im.check_local_pins([hub], run=run) == []
    assert im.normalise_digest("envoyproxy/envoy@sha256:ab") == "docker.io/envoyproxy/envoy@sha256:ab"
    assert im.normalise_digest("ghcr.io/x/y@sha256:ab") == "ghcr.io/x/y@sha256:ab"


def test_normalise_matches_containerd_names():
    assert im.normalise("polyforge/ai-gateway:dev") == "docker.io/polyforge/ai-gateway:dev"
    assert im.normalise("docker.io/library/python:3.13.17-slim-bookworm") == "docker.io/library/python:3.13.17-slim-bookworm"
    assert im.normalise("python:3") == "docker.io/library/python:3"
    assert im.normalise("ghcr.io/ggml-org/llama.cpp:server-b9967") == "ghcr.io/ggml-org/llama.cpp:server-b9967"


def test_verify_node_reports_missing_and_records_digests():
    crictl = {"images": [{"repoTags": ["ghcr.io/ggml-org/llama.cpp:server-b9967"],
                          "repoDigests": ["ghcr.io/ggml-org/llama.cpp@sha256:" + "a" * 64]}]}
    run = fake_run({("docker", "exec", "c-control-plane", "crictl", "images", "-o", "json"): (0, json.dumps(crictl))})
    present = im.node_images("c", run=run)
    missing, record = im.verify_node(IMAGES, present)
    assert "ghcr.io/ggml-org/llama.cpp:server-b9967" in record
    assert "polyforge/ai-gateway:dev" in missing


def test_plan_prints_and_runs_nothing(capsys):
    assert im.main(["plan"]) == 0
    out = capsys.readouterr().out
    assert "docker pull" in out and "kind load image-archive" in out
