"""What the OSF registration records for C, read from the tag (freezing steps 4 to 6 in one place)."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import facts  # noqa: E402
import seeds  # noqa: E402

QUICKNET_INFO = {"hash": seeds.QUICKNET, "genesis_time": 1692803367, "period": 3}
PROTOCOL = b"# Protocol\n\nH1 to H4, frozen.\n"


def fetch(url: str) -> dict:
    assert url.endswith(f"/{seeds.QUICKNET}/info")
    return QUICKNET_INFO


def gh(*created: str):
    """A stand-in for `gh run list --json createdAt` answering with these runs, newest first."""
    calls = []

    def run(args, **kw):
        calls.append(args)
        return SimpleNamespace(stdout=json.dumps([{"createdAt": t} for t in created]))
    run.calls = calls
    return run


def frozen_repo(root: Path, draft: bool = False, protocol: bytes | None = PROTOCOL) -> str:
    def git(*args: str) -> str:
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@invalid", "-c", "core.autocrlf=false",
                               *args], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    reg = root / "research" / "registration"
    reg.mkdir(parents=True)
    (reg / "design.json").write_bytes(json.dumps({"draft": draft, "alpha": 0.05}).encode())
    if protocol is not None:
        (reg / "PROTOCOL.md").write_bytes(protocol)
    git("init", "-q")
    git("add", ".")
    git("commit", "-qm", "C")
    git("tag", "wp14-c")
    return git("rev-parse", "HEAD")


def test_the_facts_of_a_frozen_tag(tmp_path):
    c = frozen_repo(tmp_path)
    runner = gh("2026-11-17T11:31:40Z", "2026-11-17T11:29:04Z")   # the tag's run, then the branch's
    f = facts.facts("wp14-c", repo=tmp_path, runner=runner, fetch=fetch)
    assert f["C"] == c and len(c) == 40
    assert f["D"] == hashlib.sha256(PROTOCOL).hexdigest()
    assert f["T0"] == "2026-11-17T11:29:04Z"                        # the first run, not the last
    assert (f["round"], f["round_due_utc"]) == (34065994, "2026-11-18T11:29:06Z")   # as drawn live on 2026-11-20
    assert runner.calls == [["gh", "run", "list", "--commit", c, "--limit", "1000", "--json", "createdAt"]]


def test_D_is_the_committed_file_not_the_working_copy(tmp_path):
    frozen_repo(tmp_path)
    (tmp_path / "research" / "registration" / "PROTOCOL.md").write_bytes(PROTOCOL.replace(b"\n", b"\r\n"))
    f = facts.facts("wp14-c", repo=tmp_path, runner=gh("2026-11-17T11:29:04Z"), fetch=fetch)
    assert f["D"] == hashlib.sha256(PROTOCOL).hexdigest()


def test_a_draft_design_is_refused(tmp_path):
    frozen_repo(tmp_path, draft=True)
    with pytest.raises(SystemExit, match="draft"):
        facts.facts("wp14-c", repo=tmp_path, runner=gh("2026-11-17T11:29:04Z"), fetch=fetch)


def test_a_commit_without_the_protocol_is_refused(tmp_path):
    frozen_repo(tmp_path, protocol=None)
    with pytest.raises(SystemExit, match="PROTOCOL.md"):
        facts.facts("wp14-c", repo=tmp_path, runner=gh("2026-11-17T11:29:04Z"), fetch=fetch)


def test_a_design_that_is_not_a_json_object_is_refused(tmp_path):
    frozen_repo(tmp_path)
    for broken in (b"{draft: false", b"[]"):
        (tmp_path / "research" / "registration" / "design.json").write_bytes(broken)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@invalid", "commit", "-qam", "C2"],
                       cwd=tmp_path, check=True)
        subprocess.run(["git", "tag", "-f", "wp14-c"], cwd=tmp_path, check=True, capture_output=True)
        with pytest.raises(SystemExit, match="not a JSON object"):
            facts.facts("wp14-c", repo=tmp_path, runner=gh("2026-11-17T11:29:04Z"), fetch=fetch)


def test_no_run_yet_means_push_first(tmp_path, capsys):
    frozen_repo(tmp_path)
    with pytest.raises(facts.NoT0, match="no run"):
        facts.facts("wp14-c", repo=tmp_path, runner=gh(), fetch=fetch)
    assert facts.main(["--tag", "wp14-c"], repo=tmp_path, fetch=fetch, runner=gh()) == 2
    assert "push the branch and the tag" in capsys.readouterr().err


def test_t0_given_by_hand_does_not_ask_github(tmp_path):
    frozen_repo(tmp_path)

    def no_gh(args, **kw):
        raise AssertionError("gh was asked")
    f = facts.facts("wp14-c", t0="2026-11-17T11:29:04Z", repo=tmp_path, runner=no_gh, fetch=fetch)
    assert f["round"] == 34065994


def test_the_command_prints_what_the_registration_records(tmp_path, capsys):
    c = frozen_repo(tmp_path)
    assert facts.main(["--tag", "wp14-c", "--t0", "2026-11-17T11:29:04Z"], repo=tmp_path, fetch=fetch,
                      now=seeds.parse_time("2026-11-17T12:29:04Z")) == 0
    out = capsys.readouterr().out
    for want in (f"C      {c}", f"D      {hashlib.sha256(PROTOCOL).hexdigest()}", "T0     2026-11-17T11:29:04Z",
                 "round  34065994, due 2026-11-18T11:29:06Z", "23.0 h"):
        assert want in out


@pytest.mark.parametrize("t0", ["2026-11-17T11:29:04", "yesterday"])
def test_a_t0_that_is_not_utc_iso_is_refused_with_a_message(tmp_path, capsys, t0):
    frozen_repo(tmp_path)
    assert facts.main(["--tag", "wp14-c", "--t0", t0], repo=tmp_path, fetch=fetch) == 2
    assert "UTC with Z" in capsys.readouterr().err


def test_the_first_run_is_the_earliest_whatever_the_order_gh_lists(tmp_path):
    frozen_repo(tmp_path)
    runner = gh("2026-11-17T11:30:00Z", "2026-11-17T11:29:04Z", "2026-11-17T11:29:59Z")
    assert facts.facts("wp14-c", repo=tmp_path, runner=runner, fetch=fetch)["T0"] == "2026-11-17T11:29:04Z"


def test_without_gh_it_says_to_give_t0_by_hand(tmp_path, capsys):
    frozen_repo(tmp_path)

    def missing(args, **kw):
        raise FileNotFoundError("gh")

    def logged_out(args, **kw):
        raise subprocess.CalledProcessError(4, args, stderr="gh auth login")
    for runner in (missing, logged_out):
        with pytest.raises(facts.NoT0, match="could not list"):
            facts.facts("wp14-c", repo=tmp_path, runner=runner, fetch=fetch)
        assert facts.main(["--tag", "wp14-c"], repo=tmp_path, fetch=fetch, runner=runner) == 2
        assert "--t0" in capsys.readouterr().err
