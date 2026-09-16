#!/usr/bin/env python3
"""What the thesis, the slides and the paper draft say that the records no
longer do -- listed mechanically, so the writing that will close the gap
starts from a checklist instead of a re-read.

The documents under thesis/ and research/paper/ were last written in July
2026 (session 23) and copy numbers from the records of that time. Since
then the adjudications withdrew the comparative cost claims
(RESULTS_EVICTION_PARITY, RESULTS_TRACE_PARITY, RESULTS_BUDGET_PARITY),
re-scored the -joint-control ablation against a competent comparator
(RESULTS_LAYERED_FIX), and thirty-odd records landed that no document cites
(the 24 h soak, the multi-node sitting, the live plane B1/B1'/B1'', the wire
attack, the S3 floor ...). The records are gated byte-for-byte by
scripts/reproduce.py; the prose that copies from them is not, so nothing
said which sentences had gone stale. This does. It changes no document.

Three checks:

  1. CONTRADICTED -- a curated table of the claims the adjudications
     withdrew or bounded: the regex that finds the claim in the tex, and
     what the record says now. A hit is a sentence the author must change.
  2. UNCITED -- every record the gate knows (RECORDS + CAMPAIGN_RECORDS +
     UNGATED in reproduce.py) that no document names by file or by
     hypothesis tag, newest first. Not all must be cited; each is a
     decision the author has not yet made.
  3. UNUSED FIGURES -- committed figures no document includes.

The output is docs/THESIS_DRIFT.md. `scripts/test_thesis_drift.py` fails
when the committed copy is behind a fresh build, so the list cannot go
quietly stale the way the documents did.

    python scripts/thesis_drift.py            # -> docs/THESIS_DRIFT.md
    python scripts/thesis_drift.py --out FILE
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = REPO_ROOT / "research" / "analysis"
FIGURES = REPO_ROOT / "eval" / "results" / "figures"
OUT = REPO_ROOT / "docs" / "THESIS_DRIFT.md"

DOCUMENTS = (
    sorted((REPO_ROOT / "thesis" / "report" / "chapters").glob("*.tex"))
    + [REPO_ROOT / "thesis" / "slides" / "slides.tex"]
    + sorted((REPO_ROOT / "research" / "paper").glob("*.tex"))
    # The defence Q&A is prose that quotes the records too, and is rehearsed from.
    + [REPO_ROOT / "docs" / "DEFENSE_QA.md"]
)

# The claims the adjudications withdrew or bounded, as the tex spells them.
# Each regex runs over the raw tex of every document. "now" quotes the
# record's own reading (RESULTS_MASTER.md's scoreboard reconciles them).
CONTRADICTED = [
    ("BurstGPT cost -70% vs tuned HPA/KEDA/FIRM",
     r"[-−–]\s*70\s*\\?%",
     "withdrawn as a comparative claim. Every comparator carried a 1.4581× LRU "
     "inference charge and a pinned 128 MB cache; against `hpa_fair` the win "
     "shrinks to −52.2% and FAILS its pre-registered Wilcoxon (TP-H1a p=0.0133 "
     "vs Holm 0.01250, jcac dearer in 57.3% of windows); against comparators "
     "carrying jcac's own per-tenant budget rule BP-H1a/b FAIL (p=0.112/0.191). "
     "Citable now: the per-tenant budget is satisfiable only by a controller "
     "holding the tier knob — a feasibility statement, not a performance one.",
     "RESULTS_TRACE_PARITY.md, RESULTS_BUDGET_PARITY.md"),
    ("Azure LLM 2024 cost -42% per window",
     r"Azure.*[-−–]\s*42(\.5)?\s*\\?%|[-−–]\s*42(\.5)?\s*\\?%.*Azure",
     "withdrawn as a comparative claim: −7.1% at eviction parity (TP-H1a/b "
     "PASS), then −3.3%/−1.3% at budget parity (BP-H1a/b FAIL, p=0.379/0.91).",
     "RESULTS_TRACE_PARITY.md, RESULTS_BUDGET_PARITY.md"),
    ("BurstGPT cost p-value 4.3e-06",
     r"4\.3\s*(\{\\times\}|\\times|e-0?6)",
     "the p-value of a comparison that is withdrawn (see the −70% row).",
     "RESULTS_TRACE_PARITY.md"),
    ("1,800-run matrix: beats every tuned baseline on cost, d_z 0.66-1.12",
     r"0\.66\s*(--|–|-)\s*1\.12",
     "at eviction parity the matrix is cost-neutral against `hpa_fair` (+0.8%, "
     "EP-H1a FAIL); only −14.1% against `keda_fair` survives (p=3.8e-16).",
     "RESULTS_EVICTION_PARITY.md"),
    ("-joint-control ablation +2884% cost",
     r"2884",
     "rests on the `layered`/`gptcache` comparator's absorbing-tier defect "
     "(tiers ranked by name, de-escalation only below 0.3× target, so agentic "
     "traffic sticks at the dearest tier). Against a competent layer-local tier "
     "rule the ablation delta shrinks by −86.4% cost (LF-H1 PASS); the "
     "GPTCache composite-J comparison survives at the smaller margin (LF-H2 "
     "PASS). Do not cite +2884% without the caveat.",
     "RESULTS_LAYERED_FIX.md"),
    ("composite J: 'wins in every campaign' list stops at the real-demand replay",
     r"5\.5\s*(\{\\times\}|\\times|e-0?5)",
     "still true, and the list has since grown: the 2026-stack concurrency "
     "arm (p=1.3e-44), the 32-tenant slice (p≤2.2e-4), the Azure replay "
     "(p≤5.3e-22) and the offline-trained RL joint controller over the same "
     "action space (p=2.9e-28, d_z=−0.708).",
     "RESULTS_CONCURRENCY.md, RESULTS_TENANT_SCALE.md, RESULTS_LEARNED.md"),
]

# A registered hypothesis tag, as records_index.py reads it.
TAG = re.compile(r"\b([A-Z]{2,4}[0-9]?-H[0-9][a-z]?)\b")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True,
                          text=True, check=False).stdout.strip()


def last_commit_date(path: Path) -> str:
    return git("log", "-1", "--format=%cs", "--", str(path.relative_to(REPO_ROOT))) or "uncommitted"


def first_commit_date(path: Path) -> str:
    dates = git("log", "--diff-filter=A", "--format=%cs", "--follow", "--",
                str(path.relative_to(REPO_ROOT))).splitlines()
    return dates[-1] if dates else "uncommitted"


def gate_records() -> list[Path]:
    """Every record reproduce.py knows: gated or excused. The list is read
    out of the gate, never re-declared here."""
    spec = importlib.util.spec_from_file_location("reproduce", REPO_ROOT / "scripts" / "reproduce.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    names = ({r for r, _ in mod.RECORDS} | {r for _, _, r in mod.CAMPAIGN_RECORDS} | set(mod.UNGATED))
    found = []
    for name in sorted(names):
        hits = [ANALYSIS / name] if (ANALYSIS / name).exists() else list((REPO_ROOT / "eval" / "results").rglob(name))
        found.extend(hits)
    return found


def headline(record: Path) -> str:
    for line in record.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def rel(path: Path) -> str:
    """Repo-relative POSIX path; the path itself when it lies outside the repo (tests)."""
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def detex(text: str) -> str:
    """Enough of the tex to match a file name: `RESULTS\\_V2.md` -> `RESULTS_V2.md`."""
    return text.replace("\\_", "_")


# A withdrawn number may still appear in the text -- as history, beside its
# withdrawal. A mention is BARE (drift) only when no qualifier sits within
# two lines of it; qualified mentions are counted, not flagged.
QUALIFIED = re.compile(
    r"withdrawn|as tuned|caveat|published comparator|layered comparator|adjudicat|"
    r"not the (thesis's )?claim|history of the claim|no longer|superseded|WITHDRAWN|"
    r"smaller (number|margin)|percentage.*withdrawn|\(Not\)|never the|as published|"
    r"shrinks to|rank test|eviction parity|budget parity|concurrency arm|learned \(RL\)|"
    r"RL\) joint|withdrew|shrinks from",
    re.IGNORECASE)
QUALIFIER_WINDOW = 3


def contradicted(docs: dict[Path, str]) -> list[tuple[str, str, str, list[str], int]]:
    rows = []
    for label, pattern, now, record in CONTRADICTED:
        rx = re.compile(pattern)
        bare, qualified = [], 0
        for path, text in docs.items():
            lines = text.splitlines()
            for i, line in enumerate(lines, 1):
                if not rx.search(line):
                    continue
                window = "\n".join(lines[max(0, i - 1 - QUALIFIER_WINDOW):i + QUALIFIER_WINDOW])
                if QUALIFIED.search(window):
                    qualified += 1
                else:
                    bare.append(f"{rel(path)}:{i}")
        rows.append((label, now, record, bare, qualified))
    return rows


def uncited(docs: dict[Path, str], records: list[Path]) -> list[tuple[str, str, str]]:
    corpus = "\n".join(detex(t) for t in docs.values())
    rows = []
    for record in records:
        if record.name in corpus:
            continue
        tags = sorted(set(TAG.findall(record.read_text(encoding="utf-8", errors="replace"))))
        if any(tag in corpus for tag in tags):
            continue
        rows.append((first_commit_date(record), record.name, headline(record)))
    return sorted(rows, reverse=True)


def unused_figures(docs: dict[Path, str]) -> list[str]:
    included = set()
    for text in docs.values():
        for m in re.finditer(r"\\includegraphics(\[[^\]]*\])?\{([^}]*)\}", text):
            included.add(Path(m.group(2)).stem)
    return sorted(p.stem for p in FIGURES.glob("fig*.png") if p.stem not in included)


def build() -> str:
    docs = {p: p.read_text(encoding="utf-8", errors="replace") for p in DOCUMENTS if p.exists()}
    records = gate_records()
    newest = max((first_commit_date(r) for r in records), default="?")
    rows_c = contradicted(docs)
    rows_u = uncited(docs, records)
    figs = unused_figures(docs)
    hit_c = [r for r in rows_c if r[3]]

    L = ["# Thesis drift — what the documents say that the records no longer do", "",
         "Generated by `scripts/thesis_drift.py`; changes no document. The records are "
         "gated byte-for-byte by `scripts/reproduce.py`; the documents that copy from them "
         "are prose, and this is the only check that reads it. Three lists: the claims the "
         "adjudications withdrew or bounded and where the tex still makes them; the records "
         "no document cites; the committed figures no document includes.", "",
         # No per-document commit dates: they would change on every commit that
         # touches a chapter and make this report stale without any drift.
         "## Documents", ""]
    L += [f"- `{rel(p)}`" for p in docs]
    L += ["", f"Newest record in the gate: **{newest}**. Records known to the gate: **{len(records)}**; "
          f"cited by at least one document: **{len(records) - len(rows_u)}**.", "",
          f"## 1. Contradicted claims — {len(hit_c)} of {len(rows_c)} still made bare in the text", "",
          "A mention counts as bare when no qualifier (withdrawn, as tuned, caveat, adjudicated, ...) "
          "sits within three lines of it; mentions that carry their withdrawal are counted in the last "
          "column and are not drift.", "",
          "| claim as written | bare, where | what the record says now | record | qualified mentions |",
          "|---|---|---|---|---|"]
    for label, now, record, where, qualified in rows_c:
        loc = "<br>".join(f"`{w}`" for w in where) if where else "— (none)"
        L.append(f"| {label} | {loc} | {now} | `{record}` | {qualified} |")
    L += ["", f"## 2. Records no document cites — {len(rows_u)}", "",
          "By file name or by any hypothesis tag the record names; newest first. Not every one "
          "belongs in the thesis; every one is a decision the author has not yet made.", "",
          "| first committed | record | headline |", "|---|---|---|"]
    for date, name, head in rows_u:
        L.append(f"| {date} | `{name}` | {head} |")
    L += ["", f"## 3. Committed figures no document includes — {len(figs)}", ""]
    L += [f"- `{f}`" for f in figs] or ["- none"]
    L.append("")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    args.out.write_text(build(), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
