"""Generate the journal-only tables from the committed records.

    python make_tables.py        (from research/paper-jss/)

Writes tables/tab_alltests.tex: every p-valued registered test of the eleven
campaigns the paper reports, with the effect and interval its record prints
and the programme-wide multiplicity reading from HYPOTHESIS_LEDGER.md. The
other tables and figures are copied from research/paper-ieee (make_assets.py),
which generates them from the same records.
"""
from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ANALYSIS = HERE.parent / "analysis"

# (record, campaign label) in the paper's order.
RECORDS = [
    ("RESULTS_FAIR_J.md", "FAIR\\_J"),
    ("RESULTS_RETUNED.md", "RETUNED"),
    ("RESULTS_JOINTNESS.md", "JOINTNESS"),
    ("RESULTS_ACTUATION.md", "ACTUATION"),
    ("RESULTS_QUALITY.md", "QUALITY"),
    ("RESULTS_HELDOUT.md", "HELDOUT"),
    ("RESULTS_WIDTH.md", "WIDTH"),
    ("RESULTS_REVIEWER_FOLLOWUP.md", "RV"),
    ("RESULTS_TRACE_AUDITED.md", "TA"),
    ("RESULTS_CACHE_SENSITIVITY.md", "CS"),
    ("RESULTS_BUDGET_MATCHED.md", "BM"),
    ("RESULTS_LEVEL.md", "LEVEL"),
]

LEVEL_ARMS = [
    ("jcac_level", "JC, level (treatment)"),
    ("jcac_level_decoupled", "JC, level, decoupled search"),
    ("jcac_converged", "JC, published"),
    ("layered_small_level_gated", "Cheapest-tier stack, budget-gated"),
    ("layered_small_level_capped", "Cheapest-tier stack, capped"),
    ("hpa_level_capped", "HPA, capped"),
    ("keda_level_capped", "KEDA, capped"),
    ("static_share", "Static equal share"),
]
LEVEL_KEYS = {"LV": "main", "MO": "mono", "LG": "lag", "HO": "held-out", "TR": "BurstGPT"}

EFFECT_HEADERS = ("ΔJ", "mean DiD", "J narrow - J wide", "mean diff")
NUM = re.compile(r"[-+−]?\d+(?:\.\d+)?(?:e[-+]?\d+)?")


def cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def ledger() -> dict[str, list[dict]]:
    """Full-ledger rows per record: test, verdict, p, Holm, BY."""
    out: dict[str, list[dict]] = {}
    text = (ANALYSIS / "HYPOTHESIS_LEDGER.md").read_text(encoding="utf-8")
    body = text.split("## Full ledger", 1)[1].split("\n## ", 1)[0]
    for line in body.splitlines():
        if not line.startswith("| `RESULTS_"):
            continue
        c = cells(line)
        if len(c) < 7 or c[3] not in ("PASS", "FAIL"):
            continue
        out.setdefault(c[0].strip("`"), []).append(
            {"test": c[2], "verdict": c[3], "p": c[4], "holm": c[5], "by": c[6]})
    return out


def effects(record: str) -> dict[str, tuple[str, str]]:
    """Test id -> (effect, CI) from every table of the record whose rows start
    with a bold test id; the effect and CI columns are found by header."""
    out: dict[str, tuple[str, str]] = {}
    header: list[str] | None = None
    for line in (ANALYSIS / record).read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            header = None
            continue
        c = cells(line)
        if header is None:
            header = c
            continue
        if set(line.replace("|", "").strip()) <= set("-: "):
            continue
        m = re.fullmatch(r"\*\*(.+?)\*\*", c[0])
        if not m:
            continue
        eff = next((i for i, h in enumerate(header)
                    if any(h.startswith(k) or h == k for k in EFFECT_HEADERS)), None)
        ci = next((i for i, h in enumerate(header) if "CI" in h), None)
        if eff is None or ci is None or eff >= len(c) or ci >= len(c):
            continue
        out[m.group(1)] = (c[eff], c[ci])
    return out


def fmt_num(s: str) -> str:
    s = s.replace("−", "-")
    m = NUM.search(s)
    if not m:
        return s
    x = float(m.group(0))
    return f"${x:+.4f}$"


def fmt_ci(s: str) -> str:
    xs = [float(v.replace("−", "-")) for v in NUM.findall(s.replace("−", "-"))]
    if len(xs) != 2:
        return s
    return f"$[{xs[0]:+.4f},{xs[1]:+.4f}]$"


def fmt_p(s: str) -> str:
    try:
        x = float(s)
    except ValueError:
        return s
    if x < 1e-3:
        mant, exp = f"{x:.1e}".split("e")
        return f"${mant}\\times10^{{{int(exp)}}}$"
    return f"{x:.3g}"


def multiplicity(row: dict) -> str:
    if row["verdict"] != "PASS":
        return "--"
    if row["holm"] == "reject":
        return "Holm"
    if row["by"] == "reject":
        return "BY"
    return "family"


def level_tables(led: dict[str, list[dict]]) -> None:
    """tab_level_tests.tex, tab_level_arms.tex and tab_campaigns.tex (the
    IEEE-generated campaign table plus the LEVEL row and the new total)."""
    text = (ANALYSIS / "RESULTS_LEVEL.md").read_text(encoding="utf-8")
    label = dict(LEVEL_ARMS)
    tests, coord = [], []
    for line in text.splitlines():
        c = cells(line) if line.startswith("|") else []
        if c and re.fullmatch(r"\*\*LV-[A-Z]\d\*\*", c[0]):
            comp = c[2].strip("`")
            tests.append(f"\\texttt{{{c[0].strip('*')}}} & {LEVEL_KEYS[c[1]]} & {label.get(comp, comp)} & "
                         f"{fmt_num(c[4])} & {fmt_ci(c[6])} & {c[10]} \\\\")
        if c and c[0].startswith("LV-C["):
            coord.append(f"\\texttt{{{c[0]}}} & {LEVEL_KEYS[c[1]]} & decoupled search & {fmt_num(c[2])} & "
                         f"{fmt_ci(c[3])} & {'EQ' if c[5] == 'EQUIVALENT' else 'not EQ'} at $\\pm0.01$ \\\\")
    body = ["% generated by make_tables.py -- do not edit",
            "\\begin{tabular}{@{}l l l r c l@{}}", "\\toprule",
            "Test & Substrate & Comparator & $\\Delta J_L$ & 95\\% CI & Verdict \\\\", "\\midrule"]
    body += tests + ["\\midrule"] + coord + ["\\bottomrule", "\\end{tabular}"]
    (HERE / "tables" / "tab_level_tests.tex").write_text("\n".join(body) + "\n", encoding="utf-8")

    section = text.split("## LV:", 1)[1].split("\n## ", 1)[0]
    rows = []
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        c = cells(line)
        arm = c[0].strip("`")
        rows.append(f"{label[arm]} & {float(c[1]):.4f} & {float(c[2]):.4f} & {float(c[4]):.2f} & "
                    f"{float(c[5]):.3f} & {c[9].rstrip('%')} & {c[10].rstrip('%')} \\\\")
        if arm == "jcac_converged":
            rows.append("\\midrule")
    arms = ["% generated by make_tables.py -- do not edit", "\\begin{tabular}{@{}l r r r r r r@{}}", "\\toprule",
            "Arm & $J_L$ & $J$ & Cost & Viol. & Shed & Over. \\\\",
            " & & & (\\$) & & (\\%) & (\\%) \\\\", "\\midrule"] + rows + ["\\bottomrule", "\\end{tabular}"]
    (HERE / "tables" / "tab_level_arms.tex").write_text("\n".join(arms) + "\n", encoding="utf-8")

    lv = led.get("RESULTS_LEVEL.md", [])
    passed, total = sum(r["verdict"] == "PASS" for r in lv), len(lv)
    camp = (HERE / "tables" / "tab_campaigns_base.tex").read_text(encoding="utf-8")
    m = re.search(r"Total & & & & & ([\d,]+) & (\d+)/(\d+) \\\\", camp)
    runs = int(m.group(1).replace(",", "")) + 8832
    new_total = f"Total & & & & & {runs:,} & {int(m.group(2)) + passed}/{int(m.group(3)) + total} \\\\"
    level_row = (f"LEVEL & LV & Level field, final & 8 & 5 subs. & 8,832 & {passed}/{total} \\\\\n\\midrule\n")
    camp = camp.replace(m.group(0), new_total).replace("\\midrule\nTotal", "\\midrule\n" + level_row + "Total")
    camp = camp.replace("% generated by make_assets.py -- do not edit",
                        "% generated by make_tables.py from tab_campaigns_base.tex (paper-ieee make_assets.py) -- do not edit")
    (HERE / "tables" / "tab_campaigns.tex").write_text(camp, encoding="utf-8")
    print(f"tab_level_tests.tex, tab_level_arms.tex, tab_campaigns.tex: LEVEL {passed}/{total}, total runs {runs:,}")


def main() -> int:
    led = ledger()
    level_tables(led)
    lines = [
        "% generated by make_tables.py -- do not edit",
        "\\begin{longtable}{@{}l l r c l l l@{}}",
        "\\toprule",
        "Campaign & Test & Effect & 95\\% CI & $p$ & Verdict & Survives \\\\",
        "\\midrule",
        "\\endfirsthead",
        "\\toprule",
        "Campaign & Test & Effect & 95\\% CI & $p$ & Verdict & Survives \\\\",
        "\\midrule",
        "\\endhead",
        "\\bottomrule",
        "\\endfoot",
    ]
    total = passed = 0
    for record, label in RECORDS:
        rows = led.get(record, [])
        eff = effects(record)
        for k, r in enumerate(rows):
            e, ci = eff.get(r["test"], ("", ""))
            lines.append(
                f"{label if k == 0 else ''} & \\texttt{{{r['test']}}} & "
                f"{fmt_num(e) if e else '--'} & {fmt_ci(ci) if ci else '--'} & "
                f"{fmt_p(r['p'])} & {r['verdict']} & {multiplicity(r)} \\\\")
            total += 1
            passed += r["verdict"] == "PASS"
        if rows:
            lines.append("\\midrule")
    if lines[-1] == "\\midrule":
        lines.pop()
    lines.append("\\end{longtable}")
    (HERE / "tables" / "tab_alltests.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"tab_alltests.tex: {passed}/{total} PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
