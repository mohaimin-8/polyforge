"""codecheck.yml (plan WP20, level 1) against the reproduction's steps."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import codecheck_manifest as cm  # noqa: E402
import reproduce_programme2 as rp  # noqa: E402


def test_the_committed_manifest_is_current():
    assert cm.main(["--check"]) == 0


def test_it_lists_every_report_of_every_step_once_and_each_is_tracked():
    data = yaml.safe_load(cm.TARGET.read_text(encoding="utf-8"))
    files = [item["file"] for item in data["manifest"]]
    assert files == [cm.rel(cwd / r) for _, cwd, _, reports, _ in rp.STEPS for r in reports]
    assert len(set(files)) == len(files)
    assert cm.untracked(files) == []


def test_the_file_follows_codechecks_layout():
    text = cm.TARGET.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    data = yaml.safe_load(text)
    assert data["version"] == "https://codecheck.org.uk/spec/config/2.0/"
    assert {"title", "authors", "reference"} <= set(data["paper"])
    assert data["paper"]["authors"] and all({"name", "ORCID"} <= set(a) for a in data["paper"]["authors"])
    assert data["manifest"] and all(set(item) == {"file", "comment"} for item in data["manifest"])


def test_a_rerun_keeps_the_authors_head_and_rewrites_only_the_manifest(tmp_path):
    target = tmp_path / "codecheck.yml"
    head = cm.HEAD.replace("FILL IN: the paper's title", "A filled title")
    target.write_bytes((head + cm.MARKER + '\nmanifest:\n  - file: "old"\n').encode("utf-8"))
    assert cm.main(["--check"], target) == 1
    assert cm.main([], target) == 0
    text = target.read_text(encoding="utf-8")
    assert text.startswith(head) and '"old"' not in text
    assert cm.main(["--check"], target) == 0


def test_a_file_without_the_marker_is_refused(tmp_path):
    target = tmp_path / "codecheck.yml"
    target.write_text("---\nmanifest: []\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        cm.main([], target)


def test_crlf_line_ends_are_read_as_git_commits_them(tmp_path):
    """An editor on Windows may save CRLF; git commits LF (.gitattributes), so --check and a rerun
    read the file as it would be committed, and a rerun writes LF."""
    target = tmp_path / "codecheck.yml"
    current = cm.TARGET.read_bytes().replace(b"\r\n", b"\n")
    target.write_bytes(current.replace(b"\n", b"\r\n"))
    assert cm.main(["--check"], target) == 0
    assert cm.main([], target) == 0
    assert target.read_bytes() == current
