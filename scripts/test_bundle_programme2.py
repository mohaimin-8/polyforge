"""Programme 2's deposit: what goes in, byte for byte, and what refuses."""

import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research" / "registration"))

import bundle_programme2 as bundle  # noqa: E402
import confirm  # noqa: E402  (the workflow's seal, so the deposit takes exactly what it writes)

GIT = ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "core.autocrlf=false"]


@pytest.fixture
def inputs(tmp_path):
    """A one-commit repository, an audit folder (with bytecode to leave out), a creators file."""
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    (repo / "a.txt").write_bytes(b"alpha\n")
    (repo / "sub" / "b.bin").write_bytes(bytes(range(256)))
    for cmd in (["init", "-q"], ["add", "."], ["commit", "-qm", "init"]):
        subprocess.run([*GIT, *cmd], cwd=repo, check=True)
    audit = tmp_path / "audit"
    (audit / "__pycache__").mkdir(parents=True)
    (audit / "check.py").write_bytes(b"print('audit')\n")
    (audit / "__pycache__" / "check.cpython-312.pyc").write_bytes(b"\0bytecode")
    creators = tmp_path / "creators.json"
    creators.write_text(json.dumps([{"name": "Doe, Jane", "affiliation": "Somewhere"},
                                    {"name": "Roe, Rick", "type": "Supervisor"}]), encoding="utf-8")
    return repo, audit, creators


def build(repo, audit, creators, out, *extra):
    assert bundle.main(["--repo", str(repo), "--audit", str(audit), "--creators", str(creators),
                        "--out", str(out), *extra]) == 0
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True,
                         check=True).stdout.strip()
    meta = json.loads((out / "deposit.json").read_text(encoding="utf-8"))["metadata"]
    return out / f"polyforge-programme2-{sha[:12]}.zip", sha, meta


def test_the_deposit_holds_the_commit_the_audit_scripts_and_a_manifest_of_both(tmp_path, inputs):
    repo, audit, creators = inputs
    (repo / "a.txt").write_bytes(b"edited, never committed\n")        # the working tree is not the deposit
    zpath, sha, meta = build(repo, audit, creators, tmp_path / "out")
    with zipfile.ZipFile(zpath) as zf:
        names = zf.namelist()
        assert names == ["repository/a.txt", "repository/sub/b.bin", "audit_scripts/check.py", "MANIFEST.sha256"]
        assert zf.read("repository/a.txt") == b"alpha\n"
        assert zf.read("repository/sub/b.bin") == bytes(range(256))
        manifest = zf.read("MANIFEST.sha256").decode("utf-8")
        lines = manifest.splitlines()
        assert [line.split("  ", 1)[1] for line in lines] == names[:-1]
        assert all(hashlib.sha256(zf.read(name)).hexdigest() == digest
                   for digest, name in (line.split("  ", 1) for line in lines))
    assert (tmp_path / "out" / "MANIFEST.sha256").read_bytes() == manifest.encode("utf-8")
    assert meta["version"] == sha[:12] and sha in meta["description"]
    assert "audit_scripts/, audit (1 files)" in meta["description"] and "confirmatory" not in meta["description"]
    assert [c["name"] for c in meta["creators"]] == ["Doe, Jane"]
    assert [c["type"] for c in meta["contributors"]] == ["Supervisor"]
    assert not list((tmp_path / "out").glob("*.partial"))


def test_the_same_inputs_give_the_same_zip_byte_for_byte(tmp_path, inputs):
    first, _, _ = build(*inputs, tmp_path / "one")
    second, _, _ = build(*inputs, tmp_path / "two")
    assert first.read_bytes() == second.read_bytes()


def artifact(conf: Path) -> Path:
    """A folder laid out as the workflow's `confirmatory` artifact unpacks, sealed by confirm.py seal."""
    for rel, data in (("draw/draw.json", b'{"round": 1}\n'), ("shard-0/rows.csv", b"identity,j\n"),
                      ("score/SCORE.md", b"# score\n")):
        (conf / rel).parent.mkdir(parents=True, exist_ok=True)
        (conf / rel).write_bytes(data)
    confirm.seal(conf)
    return conf


def test_the_confirmatory_artifact_goes_in_when_given(tmp_path, inputs):
    conf = artifact(tmp_path / "confirmatory")
    zpath, _, meta = build(*inputs, tmp_path / "out", "--confirmatory", str(conf))
    with zipfile.ZipFile(zpath) as zf:
        assert [n for n in zf.namelist() if n.startswith("confirmatory/")] == [
            "confirmatory/SHA256SUMS", "confirmatory/draw/draw.json", "confirmatory/score/SCORE.md",
            "confirmatory/shard-0/rows.csv"]
        sealed = zf.read("confirmatory/SHA256SUMS").decode("utf-8").splitlines()
        assert all(hashlib.sha256(zf.read(f"confirmatory/{name}")).hexdigest() == digest
                   for digest, name in (line.split("  ", 1) for line in sealed))
    assert "confirmatory/, the confirmatory campaign's workflow artifact (4 files)" in meta["description"]


@pytest.mark.parametrize("change, message", [
    (lambda c: (c / "desktop.ini").write_bytes(b"[.ShellClassInfo]\n"), "1 not sealed (desktop.ini)"),
    (lambda c: (c / "shard-0" / "rows.csv").unlink(), "1 missing (shard-0/rows.csv)"),
    (lambda c: (c / "score" / "SCORE.md").write_bytes(b"# edited\n"), "1 changed (score/SCORE.md)"),
    (lambda c: (c / "SHA256SUMS").unlink(), "has no SHA256SUMS"),
    (lambda c: (c / "SHA256SUMS").write_text("0" * 64 + "  ../outside\n", encoding="utf-8"), "SHA256SUMS line 1"),
    (lambda c: (c / "SHA256SUMS").write_text(2 * ("0" * 64 + "  draw/draw.json\n"), encoding="utf-8"),
     "SHA256SUMS line 2 lists draw/draw.json again"),
])
def test_a_confirmatory_folder_unlike_its_seal_refuses_before_anything_is_written(tmp_path, inputs, change, message):
    """The deposit holds the artifact exactly as the run sealed it (the hashes its summary published):
    `sha256sum -c` alone misses a file added after the seal; the bundler does not."""
    conf = artifact(tmp_path / "confirmatory")
    change(conf)
    out = tmp_path / "out"
    with pytest.raises(SystemExit, match=re.escape(message)):
        bundle.main(["--repo", str(inputs[0]), "--audit", str(inputs[1]), "--creators", str(inputs[2]),
                     "--out", str(out), "--confirmatory", str(conf)])
    assert not out.exists()


def test_a_missing_or_empty_folder_a_bad_commit_or_bad_creators_refuse_before_anything_is_written(tmp_path, inputs):
    repo, audit, creators = inputs
    out = tmp_path / "out"
    empty = tmp_path / "empty"
    (empty / "__pycache__").mkdir(parents=True)
    (empty / "__pycache__" / "x.pyc").write_bytes(b"\0")                # only bytecode: nothing to deposit
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"name": "NoComma"}]), encoding="utf-8")
    cases = [(["--audit", str(tmp_path / "nowhere"), "--creators", str(creators)], "is not a directory"),
             (["--audit", str(empty), "--creators", str(creators)], "has no files"),
             (["--audit", str(audit), "--creators", str(creators), "--commit", "no-such-commit"], "rev-parse"),
             (["--audit", str(audit), "--creators", str(bad)], "Family, Given")]
    for args, message in cases:
        with pytest.raises(SystemExit, match=message):
            bundle.main(["--repo", str(repo), "--out", str(out), *args])
    assert not out.exists()
