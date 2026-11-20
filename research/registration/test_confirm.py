"""WP13 step 5: the CI driver refuses what the procedure forbids and seals what it ran."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import confirm  # noqa: E402
import facts  # noqa: E402
import seeds  # noqa: E402
from test_seeds import GENESIS, PERIOD, ROUND_1000, fake  # noqa: E402

DUE = seeds.round_time(1000, GENESIS, PERIOD)
HEAD = {"commit": "c" * 40, "commit_time": DUE - seeds.DELAY_S - 60, "clean": True}   # committed, pushed a minute later
T0 = seeds.utc(DUE - seeds.DELAY_S)          # its first run: exactly 24 h before round 1000, which it names
MASTER = "8d5e855bca040a95993c79d2a6a6a500e6101f680f55e9be1892cd147313ab9d"   # test_seeds' pinned value

EMIT = """import pathlib, sys
master, shard, shards, rnd, out = sys.argv[1:6]
pathlib.Path(out, "emitted.txt").write_text(" ".join([master, shard, shards, rnd]), encoding="utf-8")
print("emitted shard", shard)
"""
SCORE = """import pathlib, sys
results, out = sys.argv[1:3]
names = sorted(p.name for p in pathlib.Path(results).iterdir() if p.name.startswith("shard-"))
pathlib.Path(out, "score.txt").write_text(" ".join(names), encoding="utf-8")
"""


def make_repo(root: Path, shards: int = 2, run_cmd: list[str] | None = None) -> dict:
    (root / "reg").mkdir()
    (root / "reg" / "PROTOCOL.md").write_bytes(b"protocol v1\n")
    (root / "reg" / "emit.py").write_text(EMIT, encoding="utf-8")
    (root / "reg" / "score.py").write_text(SCORE, encoding="utf-8")
    (root / "reg" / "fail.py").write_text("import sys\nprint('boom')\nsys.exit(3)\n", encoding="utf-8")
    manifest = {"protocol": "reg/PROTOCOL.md", "shards": shards,
                "run": [{"name": "sim", "cwd": "reg",
                         "command": run_cmd or ["python", "emit.py", "{master}", "{shard}", "{shards}",
                                                "{round}", "{out}"]}],
                "score": [{"name": "score", "cwd": "reg", "command": ["python", "score.py", "{results}", "{out}"]}]}
    path = root / "reg" / "campaign.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return confirm.load_manifest(path, root)


def drawn(tmp_path: Path, manifest: dict) -> Path:
    fetch = fake({r: ROUND_1000 for r in seeds.RELAYS})
    confirm.do_draw(1000, tmp_path / "out", manifest, HEAD, fetch=fetch, now=DUE, t0=T0)
    return tmp_path / "out" / "draw.json"


@pytest.mark.parametrize("change, why", [
    ({"protocol": "../outside.md"}, "outside the repository"),
    ({"protocol": "reg/missing.md"}, "not in this commit"),
    ({"shards": 0}, "shards"), ({"shards": 257}, "shards"), ({"shards": True}, "shards"),
    ({"run": []}, "non-empty"),
    ({"run": [{"name": "a b", "cwd": "reg", "command": ["python"]}]}, "name"),
    ({"run": [{"name": "x", "cwd": "nowhere", "command": ["python"]}]}, "not a directory"),
    ({"run": [{"name": "x", "cwd": "reg", "command": []}]}, "command"),
    ({"score": [{"name": "s", "cwd": "reg", "command": ["python", "{shard}"]}]}, "placeholders"),
    ({"run": [{"name": "x", "cwd": "reg", "command": ["python"]}] * 2}, "distinct"),
])
def test_the_manifest_is_held_to_its_rules(tmp_path, change, why):
    make_repo(tmp_path)
    path = tmp_path / "reg" / "campaign.json"
    path.write_text(json.dumps({**json.loads(path.read_text(encoding="utf-8")), **change}), encoding="utf-8")
    with pytest.raises(confirm.Refused, match=why):
        confirm.load_manifest(path, tmp_path)


def test_no_manifest_no_campaign(tmp_path):
    with pytest.raises(confirm.Refused, match="no manifest"):
        confirm.load_manifest(tmp_path / "campaign.json", tmp_path)


def test_the_draw_records_the_commit_and_is_made_once(tmp_path):
    manifest = make_repo(tmp_path)
    d = json.loads(drawn(tmp_path, manifest).read_text(encoding="utf-8"))
    assert d["master_seed"] == MASTER and d["commit"] == HEAD["commit"] and d["round"] == 1000
    assert d["t0"] == T0 and d["commit_time_utc"] == seeds.utc(HEAD["commit_time"])
    with pytest.raises(confirm.Refused, match="never repeated"):
        drawn(tmp_path, manifest)


@pytest.mark.parametrize("rnd, t0", [
    (999, T0),                                    # a typo, one round early
    (1001, T0),                                   # a typo, or a round picked later
    (1000, seeds.utc(DUE - seeds.DELAY_S + 1)),   # a later commit: its first run names round 1001
])
def test_only_the_round_t0_names_is_drawn_and_no_other_is_fetched(tmp_path, rnd, t0):
    manifest = make_repo(tmp_path)
    asked, answer = [], fake({r: ROUND_1000 for r in seeds.RELAYS})

    def fetch(url: str) -> dict:
        asked.append(url)
        return answer(url)
    with pytest.raises(confirm.Refused, match="not the named round"):
        confirm.do_draw(rnd, tmp_path / "out", manifest, HEAD, fetch=fetch, now=DUE + 3600, t0=t0)
    assert not (tmp_path / "out" / "draw.json").exists()
    assert asked and all(url.endswith("/info") for url in asked)   # the chain's clock, never a round


def test_a_committer_clock_ahead_of_github_does_not_refuse_the_named_round(tmp_path):
    """The commit's time is the committer's clock; the rule is T0's (GitHub's), which it may run ahead of."""
    manifest = make_repo(tmp_path)
    ahead = {**HEAD, "commit_time": DUE - seeds.DELAY_S + 120}    # two minutes after its own first run
    d = confirm.do_draw(1000, tmp_path / "out", manifest, ahead, fetch=fake({r: ROUND_1000 for r in seeds.RELAYS}),
                        now=DUE, t0=T0)
    assert d["master_seed"] == MASTER


def test_the_draw_waits_for_the_round(tmp_path):
    manifest = make_repo(tmp_path)
    with pytest.raises(seeds.NotYet):
        confirm.do_draw(1000, tmp_path / "out", manifest, HEAD, fetch=fake({r: ROUND_1000 for r in seeds.RELAYS}),
                        now=DUE - 1, t0=T0)


def test_a_shard_runs_with_the_master_seed_and_keeps_its_log(tmp_path):
    manifest = make_repo(tmp_path)
    out = tmp_path / "res" / "shard-1"
    confirm.do_run(drawn(tmp_path, manifest), 1, out, manifest, HEAD)
    assert (out / "emitted.txt").read_text(encoding="utf-8") == f"{MASTER} 1 2 1000"
    assert "emitted shard 1" in (out / "sim.log").read_text(encoding="utf-8")
    prov = json.loads((out / "provenance.json").read_text(encoding="utf-8"))
    assert (prov["commit"], prov["master_seed"], prov["shard"], prov["round"]) == (HEAD["commit"], MASTER, 1, 1000)
    assert prov["cpu"] == confirm.cpu_model() and prov["cpu"]
    with pytest.raises(confirm.Refused, match="not empty"):
        confirm.do_run(tmp_path / "out" / "draw.json", 1, out, manifest, HEAD)


def test_a_shard_outside_the_manifest_is_refused(tmp_path):
    manifest = make_repo(tmp_path)
    with pytest.raises(confirm.Refused, match="not one of"):
        confirm.do_run(drawn(tmp_path, manifest), 2, tmp_path / "res" / "shard-2", manifest, HEAD)


def test_a_draw_for_another_protocol_commit_or_seed_is_refused(tmp_path):
    manifest = make_repo(tmp_path)
    path = drawn(tmp_path, manifest)
    with pytest.raises(confirm.Refused, match="another commit|made on commit"):
        confirm.do_run(path, 0, tmp_path / "a", manifest, {**HEAD, "commit": "d" * 40})
    d = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**d, "master_seed": "00" * 32}), encoding="utf-8")
    with pytest.raises(confirm.Refused, match="master seed"):
        confirm.do_run(path, 0, tmp_path / "b", manifest, HEAD)
    path.write_text(json.dumps({**d, "randomness": "00" * 32}), encoding="utf-8")
    with pytest.raises(confirm.Refused, match="SHA-256 of its signature"):
        confirm.do_run(path, 0, tmp_path / "c", manifest, HEAD)
    path.write_text(json.dumps(d), encoding="utf-8")
    (tmp_path / "reg" / "PROTOCOL.md").write_bytes(b"protocol v2\n")
    with pytest.raises(confirm.Refused, match="another protocol"):
        confirm.do_run(path, 0, tmp_path / "e", manifest, HEAD)


def test_a_failing_component_fails_the_step(tmp_path):
    manifest = make_repo(tmp_path, run_cmd=["python", "fail.py"])
    out = tmp_path / "res" / "shard-0"
    with pytest.raises(confirm.Failed, match="exited 3"):
        confirm.do_run(drawn(tmp_path, manifest), 0, out, manifest, HEAD)
    assert "boom" in (out / "sim.log").read_text(encoding="utf-8")


def test_scoring_needs_every_shard_from_the_same_draw(tmp_path):
    manifest = make_repo(tmp_path)
    path, res = drawn(tmp_path, manifest), tmp_path / "res"
    confirm.do_run(path, 0, res / "shard-0", manifest, HEAD)
    with pytest.raises(confirm.Refused, match="partial campaign"):
        confirm.do_score(path, res, res / "score", manifest, HEAD)
    confirm.do_run(path, 1, res / "shard-1", manifest, HEAD)
    confirm.do_score(path, res, res / "score", manifest, HEAD)
    assert (res / "score" / "score.txt").read_text(encoding="utf-8") == "shard-0 shard-1"


def test_a_shard_from_another_draw_is_not_scored(tmp_path):
    manifest = make_repo(tmp_path)
    path, res = drawn(tmp_path, manifest), tmp_path / "res"
    for i in (0, 1):
        confirm.do_run(path, i, res / f"shard-{i}", manifest, HEAD)
    prov = res / "shard-1" / "provenance.json"
    prov.write_text(json.dumps({**json.loads(prov.read_text(encoding="utf-8")), "master_seed": "00" * 32}),
                    encoding="utf-8")
    with pytest.raises(confirm.Refused, match="shard 1"):
        confirm.do_score(path, res, res / "score", manifest, HEAD)


def test_the_seal_hashes_every_file_once(tmp_path):
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "x.csv").write_bytes(b"1,2\n")
    (tmp_path / "a.json").write_bytes(b"{}")
    lines = confirm.seal(tmp_path)
    assert lines == [f"{hashlib.sha256(b'{}').hexdigest()}  a.json", f"{hashlib.sha256(b'1,2' + bytes([10])).hexdigest()}  b/x.csv"]
    assert (tmp_path / confirm.SUMS).read_text(encoding="utf-8") == "\n".join(lines) + "\n"
    with pytest.raises(confirm.Refused, match="sealed once"):
        confirm.seal(tmp_path)


def test_the_cli_refuses_a_modified_checkout(tmp_path, monkeypatch, capsys):
    make_repo(tmp_path)
    monkeypatch.setattr(confirm, "MANIFEST", tmp_path / "reg" / "campaign.json")
    monkeypatch.setattr(confirm, "REPO", tmp_path)
    monkeypatch.setattr(confirm, "head_commit", lambda repo: {**HEAD, "clean": False})
    assert confirm.main(["shards"]) == 0 and capsys.readouterr().out.strip() == "[0, 1]"
    assert confirm.main(["draw", "--round", "1000", "--t0", T0, "--out", str(tmp_path / "out")]) == 2
    assert "tracked files are modified" in capsys.readouterr().err


def cli_repo(tmp_path: Path, monkeypatch) -> None:
    make_repo(tmp_path)
    monkeypatch.setattr(confirm, "MANIFEST", tmp_path / "reg" / "campaign.json")
    monkeypatch.setattr(confirm, "REPO", tmp_path)
    monkeypatch.setattr(confirm, "head_commit", lambda repo: HEAD)
    monkeypatch.setattr(seeds, "fetch_json", fake({r: ROUND_1000 for r in seeds.RELAYS}))


def test_the_cli_draws_once(tmp_path, monkeypatch, capsys):
    cli_repo(tmp_path, monkeypatch)
    argv = ["draw", "--round", "1000", "--t0", T0, "--out", str(tmp_path / "out")]
    assert confirm.main(argv) == 0 and MASTER in capsys.readouterr().out
    assert confirm.main(argv) == 2 and "never repeated" in capsys.readouterr().err


def test_the_cli_in_ci_reads_t0_from_github_and_refuses_another_round(tmp_path, monkeypatch, capsys):
    cli_repo(tmp_path, monkeypatch)
    asked = []

    def first_run(commit: str, repo: Path, runner) -> str:
        asked.append((commit, repo))
        return T0
    monkeypatch.setattr(facts, "first_run", first_run)
    argv = ["draw", "--t0-from-github", "--out", str(tmp_path / "out"), "--round"]
    assert confirm.main([*argv, "1001"]) == 2
    assert "names round 1000" in capsys.readouterr().err
    assert confirm.main([*argv, "1000"]) == 0
    assert f"named by T0 {T0}" in capsys.readouterr().out
    assert asked == [(HEAD["commit"], tmp_path)] * 2


def test_the_cli_waits_when_github_does_not_answer_for_the_runs(tmp_path, monkeypatch, capsys):
    cli_repo(tmp_path, monkeypatch)

    def first_run(commit: str, repo: Path, runner) -> str:
        raise facts.NoT0(f"gh could not list the runs of {commit[:12]} (HTTP 403)")
    monkeypatch.setattr(facts, "first_run", first_run)
    assert confirm.main(["draw", "--round", "1000", "--t0-from-github", "--out", str(tmp_path / "out")]) == 3
    assert "re-run the failed jobs" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("t0_args", [[], ["--t0", "2026-11-17T11:29:04"], ["--t0", T0, "--t0-from-github"]])
def test_the_cli_draw_needs_exactly_one_utc_t0(tmp_path, monkeypatch, capsys, t0_args):
    cli_repo(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as stop:
        confirm.main(["draw", "--round", "1000", "--out", str(tmp_path / "out"), *t0_args])
    assert stop.value.code == 2 and "--t0" in capsys.readouterr().err


def test_the_seal_is_in_sha256sums_format_on_every_platform(tmp_path):
    """SHA256SUMS written in text mode had CRLF on Windows, and `sha256sum -c` then found no file."""
    import hashlib
    (tmp_path / "shard-0").mkdir()
    (tmp_path / "shard-0" / "rows.csv").write_bytes(b"1,2")
    (tmp_path / "draw.json").write_bytes(b"{}")
    confirm.seal(tmp_path)
    raw = (tmp_path / "SHA256SUMS").read_bytes()
    assert 13 not in raw                                   # no CR
    for line in raw.decode("utf-8").splitlines():
        digest, name = line.split("  ", 1)
        assert hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() == digest
