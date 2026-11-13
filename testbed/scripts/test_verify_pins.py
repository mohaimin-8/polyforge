"""Unit tests of the pin verifier, against fake registry and Hub responses."""

from __future__ import annotations

import json

import verify_pins as vp

IMAGE = vp.images_lock.Image("workload", "ghcr.io/ggml-org/llama.cpp", "server-b9967", "sha256:" + "1" * 64,
                             "sha256:" + "2" * 64, "1", "src")


def registry(index_digest: str, amd64: str):
    def get(url, headers, method):
        if "/token" in url:
            return 200, {}, json.dumps({"token": "t"}).encode()
        assert headers["Authorization"] == "Bearer t" and method == "GET"
        index = {"manifests": [{"digest": amd64, "platform": {"os": "linux", "architecture": "amd64"}},
                               {"digest": "sha256:" + "9" * 64, "platform": {"os": "linux", "architecture": "arm64"}}]}
        return 200, {"docker-content-digest": index_digest}, json.dumps(index).encode()
    return get


def test_image_pin_matches_or_not():
    assert vp.check_image(IMAGE, registry(IMAGE.index_digest, IMAGE.amd64_digest)).ok
    moved = vp.check_image(IMAGE, registry("sha256:" + "3" * 64, IMAGE.amd64_digest))
    assert not moved.ok and "tag now" in moved.detail


def test_unknown_registry_and_failed_token():
    other = vp.images_lock.Image("workload", "quay.io/x/y", "1", IMAGE.index_digest, IMAGE.amd64_digest, "1", "s")
    assert not vp.check_image(other).ok
    assert not vp.check_image(IMAGE, lambda u, h, m: (401, {}, b"")).ok


def test_model_pin_uses_linked_size_and_lfs_etag():
    sha = "a" * 64
    row = ["small", "m.gguf", sha, "100", "https://huggingface.co/x/resolve/c/m.gguf"]
    good = lambda u, h, m: (302, {"x-linked-size": "100", "x-linked-etag": f'"{sha}"'}, b"")
    assert vp.check_model(row, good).ok
    wrong = lambda u, h, m: (302, {"x-linked-size": "100", "x-linked-etag": f'"{"b" * 64}"'}, b"")
    assert not vp.check_model(row, wrong).ok
    small_file = lambda u, h, m: (307, {"x-linked-size": "100", "x-linked-etag": '"d1514c31"'}, b"")
    assert vp.check_model(row, small_file).ok
    assert not vp.check_model(row, lambda u, h, m: (404, {}, b"")).ok


def test_models_lock_rows_have_five_columns():
    rows = vp.read_models()
    assert rows and all(len(r) == 5 for r in rows)
    assert {r[0] for r in rows} == {"small", "mid", "mid3b", "embedder"}
