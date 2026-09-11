"""The deposit must carry what its own description promises.

Three bundles were built before anyone checked: 2026-08-30 had no live
evidence, 2026-08-31 had no records or pre-registrations, and the rebuilt
2026-08-31 still lacked RESULTS.md (the headline record, missed by a
RESULTS_* glob), seven more gated records, and five evidence trees including
the ones behind RESULTS_TRACE_LIVE.md and RESULTS_MULTINODE.md. Each was the
same defect: a hand-kept list beside another hand-kept list. These tests hold
the bundler to the gate and to the analysis scripts, which are the only two
places that know what a record is and what it reads.

Run: python -m pytest eval/tests/test_archive_zenodo.py -q
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = EVAL_DIR.parent

spec = importlib.util.spec_from_file_location(
    "archive_zenodo", EVAL_DIR / "scripts" / "archive_zenodo.py")
az = importlib.util.module_from_spec(spec)
spec.loader.exec_module(az)


def _resolved() -> set[str]:
    """Archive names the bundler would write, computed the way main() does."""
    eval_files, missing = az.collect(az.EVAL_DIR, az.INCLUDE + az.evidence_dirs())
    eval_files = [p for p in eval_files if not az.is_not_evidence(p)]
    records = [f"research/analysis/{n}" for n in az.gated_records()
               if (REPO_DIR / "research" / "analysis" / n).exists()]
    repo_files, repo_missing = az.collect(az.REPO_DIR, az.REPO_INCLUDE + records)
    # The raw DuckDBs are gitignored and Zenodo-archived, so on a clean clone
    # (CI) that glob is legitimately empty and the bundler would refuse to
    # build -- correct for the bundler, since a deposit without them is not
    # the deposit. These tests check what git carries; that one absence is
    # tolerated and nothing else is.
    tolerated = {"results/*.duckdb"}
    assert set(missing) <= tolerated and not repo_missing, (missing, repo_missing)
    names = {str(p.relative_to(EVAL_DIR)).replace("\\", "/") for p in eval_files}
    names |= {str(p.relative_to(REPO_DIR)).replace("\\", "/") for p in repo_files}
    return names


def test_every_record_the_gate_names_is_in_the_deposit():
    names = _resolved()
    for record in az.gated_records():
        assert (f"research/analysis/{record}" in names
                or f"results/security/{record}" in names), \
            f"{record} is a gated record and is not in the deposit"


def test_the_headline_record_is_in_the_deposit():
    """The one a RESULTS_* glob cannot see."""
    assert "research/analysis/RESULTS.md" in _resolved()


def test_every_evidence_tree_an_analysis_reads_is_in_the_deposit():
    names = _resolved()
    token = re.compile(r"[a-z0-9_]+_evidence[a-z0-9_]*|wp14_attempt[0-9]+")
    sources = list((REPO_DIR / "research" / "analysis").glob("analysis_*.py"))
    sources.append(REPO_DIR / "scripts" / "reproduce.py")
    for src in sources:
        for t in set(token.findall(src.read_text(encoding="utf-8"))):
            tree = EVAL_DIR / "results" / t
            if not tree.is_dir() or any(m in t for m in az.NOT_EVIDENCE):
                continue
            assert any(n.startswith(f"results/{t}/") for n in names), \
                f"{src.name} reads results/{t}/ and the deposit does not carry it"


def test_no_mock_or_probe_artifact_travels_as_evidence():
    for name in _resolved():
        assert not any(m in name for m in az.NOT_EVIDENCE), \
            f"{name} is a mock/probe artifact and must not be in the deposit"


def test_the_bundler_can_fail():
    """A gate that cannot fail is not a gate."""
    fake = Path(EVAL_DIR / "results" / "wave4_jointstress_PROBE_MOCK.duckdb")
    assert az.is_not_evidence(fake)
    assert not az.is_not_evidence(EVAL_DIR / "results" / "raw_sim_v2.duckdb")


def test_every_pre_registration_is_in_the_deposit():
    names = _resolved()
    preregs = sorted((REPO_DIR / "research" / "analysis").glob("PREREG_*.md"))
    assert len(preregs) >= 45
    for p in preregs:
        assert f"research/analysis/{p.name}" in names, p.name


def test_creators_come_from_the_tracked_file(tmp_path):
    """deposit.json lives in a gitignored directory and is rewritten on every
    build, so the names must come from somewhere the rebuild after B1 will
    still find them."""
    f = tmp_path / "zenodo_creators.json"
    f.write_text('[{"name": "Doe, Jane", "affiliation": "RUET", '
                 '"orcid": "0000-0002-1825-0097"}]', encoding="utf-8")
    got = az.creators(f)
    assert got == [{"name": "Doe, Jane", "affiliation": "RUET",
                    "orcid": "0000-0002-1825-0097"}]
    meta = az.deposit_metadata(got)["metadata"]
    assert meta["creators"] == got
    assert az.DEPOSIT_METADATA["metadata"]["creators"] is None, "template mutated"


def test_missing_creators_file_yields_the_placeholder(tmp_path):
    assert az.creators(tmp_path / "absent.json") is az.PLACEHOLDER_CREATORS


def test_malformed_creators_refuse_the_build(tmp_path):
    cases = {
        "no comma": '[{"name": "Jane Doe"}]',
        "empty given": '[{"name": "Doe,"}]',
        "not json": '[{"name": "Doe, Jane"',
        "bad orcid": '[{"name": "Doe, Jane", "orcid": "1234"}]',
        "unknown field": '[{"name": "Doe, Jane", "email": "x@y"}]',
        "empty": '[]',
        "not a list": '{"name": "Doe, Jane"}',
    }
    for label, text in cases.items():
        f = tmp_path / f"{label}.json"
        f.write_text(text, encoding="utf-8")
        with pytest.raises(SystemExit):
            az.creators(f)
