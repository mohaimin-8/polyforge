"""The programme-wide hypothesis ledger is parsed from the records, not typed.

Audit 2026-09-26: Holm is applied only within each record, across ~48
pre-registrations; nothing said which registered PASS verdicts survive a
programme-wide correction.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent

TABLE = """# R
## HT2 (confirmatory)
| baseline | n | J diff | p | d_z | verdict |
|---|---:|---:|---:|---:|---|
| **HPA** | 96 | -0.2 | 3.1e-05 | -0.4 | PASS |
| FIRM | 96 | -0.2 | 0.0133 | -0.4 | FAIL |
| note | 96 | — | — | — | descriptive |

| id | test | n | effect | d_z | Wilcoxon p | Holm alpha | paired-t p | verdict |
|---|---|---:|---|---:|---:|---:|---:|---|
| **TP-H1a** | cost | 96 | -52% | -0.39 | 0.0133 | 0.0125 | 0.000205 | FAIL |
"""


def _mod(monkeypatch, tmp_path):
    monkeypatch.setenv("POLYFORGE_ANALYSIS_OUT", str(tmp_path))
    spec = importlib.util.spec_from_file_location("hl", HERE / "hypothesis_ledger.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_rows_are_parsed_with_their_heading_and_registered_p(monkeypatch, tmp_path):
    mod = _mod(monkeypatch, tmp_path)
    rows = mod.parse("R.md", TABLE)
    assert [(r["label"], r["verdict"], r["p"]) for r in rows] == [
        ("HPA", "PASS", 3.1e-05), ("FIRM", "FAIL", 0.0133), ("TP-H1a", "FAIL", 0.0133)]
    assert rows[0]["heading"] == "HT2 (confirmatory)"
    # The Wilcoxon column is the registered test, not the paired-t beside it.
    assert rows[2]["p"] == 0.0133


def test_benjamini_yekutieli_is_stricter_than_bh():
    spec = importlib.util.spec_from_file_location("hl2", HERE / "hypothesis_ledger.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    p = {"a": 0.001, "b": 0.01, "c": 0.02, "d": 0.03}
    # BH at q=0.05 rejects all four (0.03 <= 4/4*0.05); BY divides by c(4)=2.083.
    assert mod.benjamini_yekutieli(p, 0.05) == {"a": True, "b": True, "c": False, "d": False}


def test_record_builds_and_names_the_marginal_passes(monkeypatch, tmp_path):
    mod = _mod(monkeypatch, tmp_path)
    assert mod.main() == 0
    text = (tmp_path / mod.RECORD).read_text(encoding="utf-8")
    assert "programme-wide" in text.lower()
    assert "TP-H1a" in text                    # the ledger carries the registered tests
