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
    ("composite J p <= 5.5e-5 quoted for 'wins in every campaign'",
     r"($p \le 5.5{\times}10^{-5}$) --- and is the only Pareto-undominated system"),
    # Audit 2026-09-26 and the 2026-10-12 status: each sample is a line the
    # thesis, slides or paper carried when the rows were added.
    ("only Pareto-undominated system", r"and is the only Pareto-undominated system --- non-dominated in 48 of"),
    ("composite J: beats every tuned baseline, in every campaign",
     r"together beats every tuned per-layer baseline on the composite objective in"),
    ("composite J: beats every tuned baseline, in every campaign",
     r"\small Wins composite $J$ vs.\ every tuned baseline"),
    ("composite J: beats every tuned baseline, in every campaign",
     r"$J$ in every campaign, incl.\ vs.\ a learned (RL) controller; the"),
    ("B1': the corrected controller beats all four live arms",
     r"the corrected controller beats every one of the four comparators on cost at"),
    ("B1': the corrected controller beats all four live arms",
     r"FAIL); the corrected controller beats all four arms at"),
    ("Phase 7 live ordinal check read as a ranking (DISAGREE)",
     r"Live ordinal check: DISAGREE; 24\,h soak (SK-H4 PASS, SK-H1/H3 FAIL);"),
    # WIDTH 2026-10-22: the paper's title and conclusion as they stood.
    ("per-layer optima do not compose",
     r"\title{Per-layer optima do not compose: joint replica, semantic-cache and"),
    ("per-layer optima do not compose", r"campaign. They do not compose. Returning to the contributions of"),
    ("per-layer optima do not compose", r"    Per-layer local optima do \alert{not} compose. A controller that plans"),
])
def test_each_claim_pattern_finds_the_sentence_it_exists_for(label, sample):
    mod = _mod()
    pattern = next(p for lbl, p, _, _ in mod.CONTRADICTED if lbl == label)
    assert re.search(pattern, sample), f"{label!r}: pattern misses {sample!r}"


def test_the_compose_pattern_ignores_the_open_question():
    # The abstract asks whether the optima compose; only the answer "they do
    # not" is the contradicted claim.
    mod = _mod()
    pattern = next(p for lbl, p, _, _ in mod.CONTRADICTED if lbl == "per-layer optima do not compose")
    assert not re.search(pattern, "sound alone; whether their local optima compose had not been tested. We")
    assert not re.search(pattern, r"  \textbf{Question: do per-layer local optima compose?}")


def test_no_row_still_recommends_the_layered_stack_win():
    # JOINTNESS (2026-10-16) showed FJ-H3/RT-H3 measured a price-blind tier
    # rule; a row telling the author to cite them would re-introduce the claim.
    mod = _mod()
    for label, _, now, _ in mod.CONTRADICTED:
        assert "cite that instead" not in now, label


def test_a_withdrawn_number_beside_its_withdrawal_is_not_drift(tmp_path):
    mod = _mod()
    bare = tmp_path / "bare.tex"
    bare.write_text(r"on real BurstGPT demand: $-70\%$ cost vs.\ tuned HPA/KEDA/FIRM.", encoding="utf-8")
    qualified = tmp_path / "qualified.tex"
    qualified.write_text("cost $-70\\%$ against the baselines as tuned\n"
                         "--- withdrawn as a like-for-like comparison.", encoding="utf-8")
    rows = {r[0]: r for r in mod.contradicted({p: p.read_text(encoding="utf-8") for p in (bare, qualified)})}
    label, _, _, where, n_qualified = rows["BurstGPT cost -70% vs tuned HPA/KEDA/FIRM"]
    assert where == [f"{bare.as_posix()}:1"]
    assert n_qualified == 1


def test_naming_the_rl_or_concurrency_campaign_no_longer_qualifies_a_j_claim(tmp_path):
    # Before the audit those names marked the 'list has grown' reading; after
    # it the whole claim is withdrawn, so listing more campaigns qualifies
    # nothing (slides.tex carried exactly this line).
    mod = _mod()
    doc = tmp_path / "slides.tex"
    doc.write_text(r"$J$ in every campaign, incl.\ vs.\ a learned (RL) controller; the", encoding="utf-8")
    rows = {r[0]: r for r in mod.contradicted({doc: doc.read_text(encoding="utf-8")})}
    _, _, _, where, n_qualified = rows["composite J: beats every tuned baseline, in every campaign"]
    assert where == [f"{doc.as_posix()}:1"] and n_qualified == 0


def test_the_azure_pattern_ignores_another_papers_minus_42():
    mod = _mod()
    pattern = next(p for lbl, p, _, _ in mod.CONTRADICTED if lbl.startswith("Azure"))
    assert not re.search(pattern, r"service gaps $-42\%$ vs.\ VTC; locality; efficiency)")


def test_uncited_means_named_by_neither_file_nor_hypothesis_tag(tmp_path):
    """Independent of what the thesis cites today: a document naming a
    record by its tex-escaped file name excludes it, one naming a hypothesis
    tag the record carries excludes it, and an empty corpus excludes none."""
    mod = _mod()
    records = mod.gate_records()
    everything = {name for _, name, _ in mod.uncited({}, records)}
    assert everything == {r.name for r in records}
    by_file = {tmp_path / "a.tex": r"see \evfile{RESULTS\_V2.md} for the v2 matrix"}
    assert "RESULTS_V2.md" not in {n for _, n, _ in mod.uncited(by_file, records)}
    by_tag = {tmp_path / "b.tex": "the damped controller failed WL-H6 and WL-H7"}
    assert "RESULTS_WAVE4_DWELL.md" not in {n for _, n, _ in mod.uncited(by_tag, records)}


def test_the_thesis_cites_the_records_behind_every_figure_it_includes():
    mod = _mod()
    docs = {p: p.read_text(encoding="utf-8", errors="replace") for p in mod.DOCUMENTS if p.exists()}
    names = {name for _, name, _ in mod.uncited(docs, mod.gate_records())}
    for record in ("RESULTS.md", "ADVANCED.md", "RESULTS_RISK.md", "RESULTS_RISK_BUDGET.md",
                   "RESULTS_WAVE4_CALIBRATED.md", "RESULTS_WAVE4_DWELL.md"):
        assert record not in names, f"a figure from {record} is in the thesis but the record is never cited"
