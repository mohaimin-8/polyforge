"""Programme-wide hypothesis ledger and multiplicity check -> HYPOTHESIS_LEDGER.md.

EXPLORATORY; changes no registered verdict. Every record corrects for
multiplicity within its own family (Holm), and the families were registered
one at a time -- ~48 protocols in all. The audit of 2026-09-26 asked the
question no record answers: which registered PASS verdicts survive a
correction across the whole programme?

The ledger is PARSED from the committed records, never typed: every markdown
table with a `verdict` column and a p-value column contributes one test per
PASS/FAIL row, with the record, the section heading it sits under, the row's
label and the registered p (the Wilcoxon column where a table carries both a
Wilcoxon and a paired-t p, since the Wilcoxon was the registered test). Two
corrections are applied across all of them at 0.05:

* Holm (family-wise error), the strictest reading;
* Benjamini-Yekutieli (false discovery rate), valid under the arbitrary
  dependence these tests share (they reuse one simulator, one objective and
  often one matrix).

Tests scored without a p-value (confidence-interval and rule-based verdicts)
are counted but cannot enter a p-value correction.

    python hypothesis_ledger.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from stats import holm_bonferroni, record_path  # noqa: E402

RECORD = "HYPOTHESIS_LEDGER.md"
ALPHA = 0.05
P_COLUMNS = ("wilcoxon p", "p", "gate p")
VERDICTS = ("PASS", "FAIL")
EXTRA = ("VTC_FAIRNESS.md", "FAIRNESS_V2.md", "ADVANCED.md", "PHASE7_ORDINAL.md")
SKIP = ("RESULTS_MASTER.md",)          # a summary of the others: parsing it double-counts
_NUM = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _clean(cell: str) -> str:
    return cell.replace("**", "").replace("`", "").strip()


def _verdict(cell: str) -> str | None:
    words = _clean(cell).split()
    return words[0] if words and words[0] in VERDICTS else None


def tables(text: str):
    """Yield (section, heading, header, rows) for every markdown table.
    `section` is the nearest `## ` heading, `heading` the nearest heading of
    any level. A row whose cell count differs from its header's is refused:
    a `|` inside a cell would otherwise shift every column silently."""
    lines = text.splitlines()
    section = heading = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
            if re.match(r"##(?!#)", line):
                section = heading
        if (line.startswith("|") and i + 1 < len(lines)
                and re.fullmatch(r"\|[\s:|-]+\|", lines[i + 1].strip())):
            header = [c.lower() for c in _cells(line)]
            j, rows = i + 2, []
            while j < len(lines) and lines[j].startswith("|"):
                row = _cells(lines[j])
                if len(row) != len(header) and any(_verdict(c) for c in row):
                    raise ValueError(f"row with {len(row)} cells under a {len(header)}-column header "
                                     f"in section {section!r}: {lines[j][:80]}")
                rows.append(row)
                j += 1
            yield section, heading, header, rows
            i = j
            continue
        i += 1


def parse(name: str, text: str) -> list[dict]:
    """One dict per PASS/FAIL row of every table with a `verdict` column; `p`
    is the registered p-value where the table carries one, else None."""
    out = []
    for section, heading, header, rows in tables(text):
        if "verdict" not in header:
            continue
        vcol = header.index("verdict")
        pcol = next((header.index(c) for c in P_COLUMNS if c in header), None)
        for r in rows:
            verdict = _verdict(r[vcol]) if len(r) == len(header) else None
            if verdict is None:
                continue
            m = _NUM.search(_clean(r[pcol])) if pcol is not None else None
            if pcol is not None and m is None:
                continue
            out.append({"record": name, "section": section, "heading": heading,
                        "label": _clean(r[0]), "verdict": verdict,
                        "p": float(m.group(0)) if m else None})
    return out


def uncounted(name: str, text: str) -> list[dict]:
    """Tables that carry PASS/FAIL cells but no `verdict` column -- reported,
    never silently skipped (review of bca5acd: RESULTS_LIVE_SOAK_V8's
    per-injection table was dropped without a word)."""
    out = []
    for section, _heading, header, rows in tables(text):
        if "verdict" in header:
            continue
        n = sum(1 for r in rows if any(_verdict(c) for c in r))
        if n:
            out.append({"record": name, "section": section, "rows": n})
    return out


def benjamini_yekutieli(pvals: dict[str, float], q: float) -> dict[str, bool]:
    ordered = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(ordered)
    c_m = sum(1.0 / k for k in range(1, m + 1))
    cutoff = 0
    for rank, (_, p) in enumerate(ordered, 1):
        if p <= rank / (m * c_m) * q:
            cutoff = rank
    return {name: rank <= cutoff for rank, (name, _) in enumerate(ordered, 1)}


def _record_names() -> list[str]:
    names = sorted(p.name for p in HERE.glob("RESULTS_*.md") if p.name not in SKIP)
    return names + [n for n in EXTRA if (HERE / n).exists()]


def ledger() -> list[dict]:
    rows = []
    for n in _record_names():
        rows += parse(n, (HERE / n).read_text(encoding="utf-8"))
    for k, r in enumerate(rows):
        r["key"] = f"t{k:04d}"
    return rows


def not_counted() -> list[dict]:
    out = []
    for n in _record_names():
        out += uncounted(n, (HERE / n).read_text(encoding="utf-8"))
    return out


def split(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """(tests with a p-value, tests scored by an interval or a rule)."""
    return [r for r in rows if r["p"] is not None], [r for r in rows if r["p"] is None]


def build() -> str:
    rows, no_p = split(ledger())
    pvals = {r["key"]: r["p"] for r in rows}
    holm = holm_bonferroni(pvals, ALPHA)
    by = benjamini_yekutieli(pvals, ALPHA)
    passes = [r for r in rows if r["verdict"] == "PASS"]
    lost_holm = [r for r in passes if not holm[r["key"]]["reject"]]
    lost_by = [r for r in passes if not by[r["key"]]]
    records = sorted({r["record"] for r in rows})
    L = ["# Programme-wide hypothesis ledger and multiplicity check", "",
         "Generated by `hypothesis_ledger.py`, which parses every committed result record for tables with a "
         "`verdict` and a p-value column. **Exploratory; changes no registered verdict.** Each record corrects "
         "within its own family; this asks which registered PASS verdicts also survive a correction across "
         "the whole programme.", "",
         "| quantity | value |", "|---|---:|",
         f"| records contributing tests | {len(records)} |",
         f"| scored tests with a p-value | {len(rows)} |",
         f"| registered PASS among them | {len(passes)} |",
         f"| PASS surviving programme-wide Holm at {ALPHA} (family-wise) | {len(passes) - len(lost_holm)} |",
         f"| PASS surviving programme-wide Benjamini–Yekutieli at {ALPHA} (FDR, any dependence) | "
         f"{len(passes) - len(lost_by)} |", "",
         f"A further **{len(no_p)}** scored tests in "
         f"{len({r['record'] for r in no_p})} records carry a verdict but no p-value (confidence-interval and "
         f"rule-based readings; {sum(r['verdict'] == 'PASS' for r in no_p)} PASS). They cannot enter a "
         "p-value correction and are not counted above.", "",
         "## Registered PASS verdicts that do not survive the programme-wide correction", ""]
    if lost_holm:
        L += ["| record | section | test | registered p | Holm threshold | survives BY? |",
              "|---|---|---|---:|---:|---|"]
        for r in sorted(lost_holm, key=lambda r: -r["p"]):
            L.append(f"| `{r['record']}` | {_where(r)} | {r['label']} | {r['p']:.3g} | "
                     f"{holm[r['key']]['threshold']:.2g} | {'yes' if by[r['key']] else 'no'} |")
    else:
        L.append("None.")
    L += ["", "## Full ledger", "",
          "| record | section | test | registered verdict | registered p | programme Holm | programme BY |",
          "|---|---|---|---|---:|---|---|"]
    for r in rows:
        L.append(f"| `{r['record']}` | {_where(r)} | {r['label']} | {r['verdict']} | {r['p']:.3g} | "
                 f"{'reject' if holm[r['key']]['reject'] else '—'} | {'reject' if by[r['key']] else '—'} |")
    skipped = not_counted()
    L += ["", "## Tables with verdicts that are not counted", ""]
    if skipped:
        L += ["These tables carry PASS/FAIL cells but no `verdict` column, so they are listed here rather than "
              "parsed. They are not in the counts above; each should be checked against its record to see "
              "whether it is a registered test or a sub-reading of one.", "",
              "| record | section | rows with PASS/FAIL |", "|---|---|---:|"]
        L += [f"| `{t['record']}` | {t['section'][:70]} | {t['rows']} |" for t in skipped]
    else:
        L.append("None.")
    L += ["", "## Reading", "",
          "A PASS that does not survive is not withdrawn by this record: its registration fixed its own family, "
          "and the record stands as committed. It should be quoted as surviving its registered family only.", ""]
    return "\n".join(L) + "\n"


def _where(r: dict) -> str:
    """`section / subheading`, or the section alone when they coincide."""
    where = r["section"] if r["heading"] in ("", r["section"]) else f"{r['section']} / {r['heading']}"
    return where[:90]


def main() -> int:
    out = record_path(RECORD)
    out.write_text(build(), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
