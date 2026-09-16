"""docs/THESIS_DRIFT.md must be the list a fresh build produces, and the
claim patterns must find the sentences they exist to find.

Needs the full git history (record and document dates come from it), so the
currency check skips on a shallow clone rather than passing on wrong dates.

Run: python -m pytest scripts/test_thesis_drift.py -q
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _mod():
    spec = importlib.util.spec_from_file_location("thesis_drift", REPO_ROOT / "scripts" / "thesis_drift.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _shallow() -> bool:
    out = subprocess.run(["git", "rev-parse", "--is-shallow-repository"], cwd=REPO_ROOT,
                         capture_output=True, text=True, check=False).stdout.strip()
    return out == "true"


def test_the_committed_report_is_what_a_fresh_build_produces():
    if _shallow():
        pytest.skip("shallow clone: the dates in the report come from git history")
    mod = _mod()
    committed = mod.OUT.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert committed == mod.build(), (
        "docs/THESIS_DRIFT.md is behind the documents or the records; "
        "run `python scripts/thesis_drift.py` and commit the result")


@pytest.mark.parametrize("label,sample", [
    ("BurstGPT cost -70% vs tuned HPA/KEDA/FIRM", r"on real BurstGPT demand: $-70\%$ cost vs.\ tuned"),
    ("Azure LLM 2024 cost -42% per window", "(-70% BurstGPT / -42% Azure cost at violation parity,"),
    ("BurstGPT cost p-value 4.3e-06", r"HPA/KEDA/FIRM ($p \le 4.3{\times}10^{-6}$, $n{=}96$)"),
    ("1,800-run matrix: beats every tuned baseline on cost, d_z 0.66-1.12", r"($\dz$ 0.66--1.12); on real"),
    ("-joint-control ablation +2884% cost", r"$-$joint $+2884\%$ cost"),
    ("composite J: 'wins in every campaign' list stops at the real-demand replay",
     r"($p \le 5.5{\times}10^{-5}$) --- and is the only Pareto-undominated system"),
])
def test_each_claim_pattern_finds_the_sentence_it_exists_for(label, sample):
    mod = _mod()
    pattern = next(p for lbl, p, _, _ in mod.CONTRADICTED if lbl == label)
    assert re.search(pattern, sample), f"{label!r}: pattern misses {sample!r}"


def test_the_azure_pattern_ignores_another_papers_minus_42():
    mod = _mod()
    pattern = next(p for lbl, p, _, _ in mod.CONTRADICTED if lbl.startswith("Azure"))
    assert not re.search(pattern, r"service gaps $-42\%$ vs.\ VTC; locality; efficiency)")


def test_a_record_the_thesis_cites_is_not_listed_as_uncited():
    mod = _mod()
    docs = {p: p.read_text(encoding="utf-8", errors="replace") for p in mod.DOCUMENTS if p.exists()}
    names = {name for _, name, _ in mod.uncited(docs, mod.gate_records())}
    assert "RESULTS_V2.md" not in names and "RESULTS.md" not in names
    assert "RESULTS_WAVE4_DWELL.md" in names, "B1'' is not in the thesis yet; the check should say so"
