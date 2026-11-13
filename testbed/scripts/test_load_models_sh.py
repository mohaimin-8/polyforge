"""The in-cluster model loader (k8s/base/load-models.sh), run locally against
file:// URLs: it downloads, verifies size and sha256, refuses a mismatch and
writes the set marker the pods wait for. Skipped where sh or curl is missing."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import validate_static as vs

LOADER = vs.BASE / "load-models.sh"
SH, CURL = shutil.which("sh"), shutil.which("curl")
pytestmark = pytest.mark.skipif(SH is None or CURL is None, reason="needs sh and curl")


def lock_line(set_name: str, rel: str, payload: bytes, src: Path) -> str:
    return "\t".join([set_name, rel, hashlib.sha256(payload).hexdigest(), str(len(payload)), src.as_uri()])


def run_loader(tmp_path: Path, lines: list[str], sets: str) -> subprocess.CompletedProcess:
    lock = tmp_path / "models.lock"
    lock.write_bytes(("# comment\n" + "\n".join(lines) + "\n").encode())
    env = {**os.environ, "LOCK": lock.as_posix(), "DEST": (tmp_path / "models").as_posix(), "MODEL_SETS": sets}
    return subprocess.run([SH, LOADER.as_posix()], env=env, capture_output=True, text=True, timeout=60)


def test_loader_verifies_and_marks_the_set(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.gguf").write_bytes(b"model-a")
    (src / "cfg.json").write_bytes(b"{}")
    lines = [lock_line("small", "a.gguf", b"model-a", src / "a.gguf"),
             lock_line("embedder", "enc/cfg.json", b"{}", src / "cfg.json")]
    proc = run_loader(tmp_path, lines, "small embedder")
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "models" / "enc" / "cfg.json").read_bytes() == b"{}"
    marker = (tmp_path / "models" / ".ready" / "small").read_text().strip()
    assert marker == vs.set_digest(lines, "small")
    again = run_loader(tmp_path, lines, "small")  # a second run reuses verified files
    assert again.returncode == 0 and "already present" in again.stdout


def test_loader_refuses_a_wrong_hash_and_leaves_no_marker(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.gguf").write_bytes(b"tampered")
    line = "\t".join(["small", "a.gguf", hashlib.sha256(b"original").hexdigest(), "8", (src / "a.gguf").as_uri()])
    proc = run_loader(tmp_path, [line], "small")
    assert proc.returncode != 0 and "sha256 mismatch" in proc.stderr
    assert not (tmp_path / "models" / "a.gguf").exists()
    assert not (tmp_path / "models" / ".ready" / "small").exists()


def test_loader_refuses_an_unknown_set(tmp_path):
    proc = run_loader(tmp_path, [], "nothing")
    assert proc.returncode == 2 and "no entries for set" in proc.stderr
