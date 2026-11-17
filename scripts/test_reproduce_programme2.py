"""reproduce_programme2.py: a rebuilt report passes as `numeric` only when its
text is unchanged and its numbers agree within the tolerance."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import reproduce_programme2 as rp  # noqa: E402


def repo_with(tmp_path: Path, files: dict[str, bytes]) -> Path:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@example.org")
    git("config", "user.name", "t")
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    git("add", ".")
    git("commit", "-q", "-m", "records")
    return tmp_path


def test_numbers_that_differ_in_the_last_digits_only(tmp_path):
    repo = repo_with(tmp_path, {"r.json": b'{"a": 0.30000000000000004, "n": 12, "z": 0.0}\n'})
    (repo / "r.json").write_bytes(b'{"a": 0.3, "n": 12, "z": -0.0}\r\n')
    worst = rp.numbers_only("r.json", repo)
    assert worst is not None and 0 < worst < 1e-15


def test_changed_text_binary_files_and_large_differences_are_drift(tmp_path):
    repo = repo_with(tmp_path, {"r.md": b"joint 0.2070 per_knob 0.2070\n", "p.png": b"\x89PNG\0\x01",
                                "s.md": b"0 runs beyond WP9's\n"})
    (repo / "r.md").write_bytes(b"joint 0.2070 layers 0.2070\n")
    (repo / "p.png").write_bytes(b"\x89PNG\0\x02")
    (repo / "s.md").write_bytes(b"10696 runs beyond WP9's\n")
    assert rp.numbers_only("r.md", repo) is None
    assert rp.numbers_only("p.png", repo) is None
    assert rp.numbers_only("s.md", repo) == 1.0 > rp.TOLERANCE


def test_strict_asks_for_byte_identity(tmp_path, monkeypatch):
    repo = repo_with(tmp_path, {"r.json": b'{"a": 0.1234567890123456}\n'})
    monkeypatch.setattr(rp, "REPO", repo)
    script = repo / "rebuild.py"
    script.write_text('open("r.json", "w").write(\'{"a": 0.1234567890123457}\\n\')\n', encoding="utf-8")
    step = ("rebuild", repo, ["rebuild.py"], ["r.json"], [])
    assert rp.run(step)[0] == "numeric"
    assert rp.run(step, strict=True)[0] == "drift"
