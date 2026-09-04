#!/usr/bin/env python3
"""Build the GitHub Pages evidence supplement (FINAL_ROADMAP D3).

What it publishes: every pre-registration, every generated record, and every
figure, cross-linked, with each record marked according to whether
`scripts/reproduce.py` verifies it byte-identically.

**The gate flag is imported, never re-declared.** `RECORDS` and
`CAMPAIGN_RECORDS` are read out of `reproduce.py` itself, so "this record is in
the reproduction gate" is true on the site because it is true in the gate — not
because a second list agrees today and drifts tomorrow. Same reasoning as
`trace_demand.py` calling the simulator's own `window_buckets`.

The build only reads the evidence base; it writes nothing into
`research/analysis/` or `eval/results/`, so R4 is untouched by design.

Determinism: the output is a pure function of the inputs. The commit stamp
comes from `POLYFORGE_PAGES_COMMIT` (CI sets it) rather than from calling git,
so two local builds are byte-identical and the test can assert that.

Usage:
    python scripts/build_pages.py                 # -> site/
    python scripts/build_pages.py --out DIR
    python scripts/build_pages.py --strict        # fail on a missing caption
"""

from __future__ import annotations

import argparse
import html
import importlib.util
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pages_markdown import to_html  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = REPO_ROOT / "research" / "analysis"
FIGURE_DIR = REPO_ROOT / "eval" / "results" / "figures"
CAPTION_FILE = FIGURE_DIR / "FIGURES.md"
MARKER = ".polyforge-pages"
NO_CAPTION = "No caption in FIGURES.md — the inventory is behind the figure set."

STYLE = """
:root { --bg:#fbfbfa; --fg:#1a1a19; --muted:#5f5f5b; --rule:#e2e2dd;
        --accent:#7a4b2a; --card:#ffffff; --pass:#2f6b3a; --fail:#8c2f2f; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#16161a; --fg:#e8e8e3; --muted:#a0a09a; --rule:#2e2e34;
          --accent:#d0a172; --card:#1e1e23; --pass:#7cc48a; --fail:#e08b8b; }
}
* { box-sizing: border-box; }
body { background: var(--bg); color: var(--fg); margin: 0;
       font: 16px/1.6 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
main { max-width: 62rem; margin: 0 auto; padding: 2.5rem 1.25rem 5rem; }
nav { border-bottom: 1px solid var(--rule); background: var(--card); }
nav div { max-width: 62rem; margin: 0 auto; padding: .85rem 1.25rem;
          display: flex; gap: 1.25rem; flex-wrap: wrap; align-items: baseline; }
nav a { color: var(--muted); text-decoration: none; font-size: .92rem; }
nav a.here { color: var(--fg); }
nav a:hover, a:hover { color: var(--accent); }
nav .brand { font-weight: 700; color: var(--fg); letter-spacing: -.01em; }
a { color: var(--accent); }
h1 { font-size: 1.9rem; line-height: 1.25; letter-spacing: -.02em; margin: 0 0 .6rem; }
h2 { font-size: 1.3rem; margin: 2.2rem 0 .6rem; letter-spacing: -.01em; }
h3 { font-size: 1.05rem; margin: 1.6rem 0 .4rem; }
blockquote { margin: 1.1rem 0; padding: .1rem 0 .1rem 1rem;
             border-left: 3px solid var(--rule); color: var(--muted); }
code { font: .87em ui-monospace, SFMono-Regular, Consolas, monospace;
       background: var(--card); border: 1px solid var(--rule);
       border-radius: 4px; padding: .05em .3em; }
pre { background: var(--card); border: 1px solid var(--rule); border-radius: 8px;
      padding: .9rem 1rem; }
pre code { border: 0; background: none; padding: 0; }
.scroll { overflow-x: auto; max-width: 100%; }
table { border-collapse: collapse; width: 100%; font-size: .93rem; margin: 1rem 0; }
th, td { border-bottom: 1px solid var(--rule); padding: .45rem .6rem; vertical-align: top; }
th { font-weight: 600; color: var(--muted); font-size: .82rem;
     text-transform: uppercase; letter-spacing: .04em; }
hr { border: 0; border-top: 1px solid var(--rule); margin: 2rem 0; }
.lede { color: var(--muted); font-size: 1.05rem; }
.tag { font-size: .74rem; letter-spacing: .04em; text-transform: uppercase;
       border: 1px solid var(--rule); border-radius: 999px; padding: .1rem .5rem;
       color: var(--muted); white-space: nowrap; }
.tag.gated { color: var(--pass); border-color: var(--pass); }
.tag.ungated { color: var(--fail); border-color: var(--fail); }
figure { margin: 2.2rem 0; }
figure img { width: 100%; border: 1px solid var(--rule); border-radius: 8px;
             background: #fff; }
figcaption { color: var(--muted); font-size: .9rem; margin-top: .5rem; }
.grid { display: grid; gap: 1.25rem; margin: 1.5rem 0;
        grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr)); }
.card { background: var(--card); border: 1px solid var(--rule); border-radius: 10px;
        padding: 1rem 1.1rem; }
.card .n { font-size: 1.9rem; font-weight: 700; letter-spacing: -.02em; display: block; }
.card .l { color: var(--muted); font-size: .86rem; }
footer { color: var(--muted); font-size: .85rem; border-top: 1px solid var(--rule);
         margin-top: 3rem; padding-top: 1rem; }
"""

NAV = [("index.html", "Overview"), ("records.html", "Records"),
       ("preregs.html", "Pre-registrations"), ("figures.html", "Figures")]


def gated_records() -> frozenset[str]:
    """The record names `scripts/reproduce.py` actually verifies."""
    path = Path(__file__).resolve().with_name("reproduce.py")
    spec = importlib.util.spec_from_file_location("polyforge_reproduce", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    names = {name for name, _ in module.RECORDS}
    names |= {record for _, _, record in module.CAMPAIGN_RECORDS}
    return frozenset(names)


def captions() -> dict[str, str]:
    """Figure captions keyed by file stem, from the committed inventory."""
    found: dict[str, str] = {}
    for line in CAPTION_FILE.read_text(encoding="utf-8").splitlines():
        if not line.startswith("- **fig"):
            continue
        stem, _, rest = line[4:].partition("**")
        found[stem] = rest.lstrip(" —-").strip()
    return found


def figures_without_captions() -> list[str]:
    known = captions()
    return sorted(p.stem for p in FIGURE_DIR.glob("fig*.png") if p.stem not in known)


def title_of(document: Path) -> str:
    for line in document.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return document.stem


def page(title: str, body: str, active: str) -> str:
    links = "".join(
        '<a href="{}" class="{}">{}</a>'.format(href, "here" if href == active else "", label)
        for href, label in NAV)
    return ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            "<title>{} — PolyForge evidence</title>"
            "<style>{}</style></head><body>"
            '<nav><div><span class="brand">PolyForge evidence</span>{}</div></nav>'
            "<main>{}"
            "<footer>Generated by <code>scripts/build_pages.py</code> from the "
            "repository's own records. Records are written by their generator "
            "scripts and never edited by hand; a record whose verdict was FAIL "
            "stands as committed.</footer></main></body></html>\n").format(
        html.escape(title), STYLE, links, body)


def linker(published: frozenset[str]):
    """Links to a published document resolve; every other link keeps its words
    and loses its anchor rather than becoming a 404."""

    def target(text: str, href: str) -> str:
        if href.startswith(("http://", "https://", "#")):
            return '<a href="{}">{}</a>'.format(html.escape(href, quote=True), text)
        if href.endswith(".md"):
            stem = href.split("/")[-1][:-3]
            if stem in published:
                return '<a href="{}.html">{}</a>'.format(stem, text)
        return text

    return target


def document_list(documents: list[Path], gated: frozenset[str], show_gate: bool) -> str:
    rows = []
    for document in documents:
        cell = ""
        if show_gate:
            in_gate = document.name in gated
            label = "in gate" if in_gate else "not gated"
            cell = '<td><span class="tag {}">{}</span></td>'.format(
                "gated" if in_gate else "ungated", label)
        rows.append('<tr><td><a href="{}.html">{}</a></td><td>{}</td>{}</tr>'.format(
            document.stem, html.escape(document.name), html.escape(title_of(document)), cell))
    head = "<tr><th>File</th><th>Title</th>{}</tr>".format(
        "<th>Reproduction</th>" if show_gate else "")
    return '<div class="scroll"><table><thead>{}</thead><tbody>{}</tbody></table></div>'.format(
        head, "".join(rows))


def index_body(records: list[Path], preregs: list[Path], gated: frozenset[str],
               figure_count: int, commit: str | None) -> str:
    in_gate = sum(1 for r in records if r.name in gated)
    cards = [(len(preregs), "pre-registrations"), (len(records), "generated records"),
             (in_gate, "records in the byte-identical gate"), (figure_count, "figures")]
    grid = "".join('<div class="card"><span class="n">{}</span>'
                   '<span class="l">{}</span></div>'.format(n, label) for n, label in cards)
    stamp = ("<p>Built from commit <code>{}</code>.</p>".format(html.escape(commit))
             if commit else "")
    return """<h1>PolyForge — evidence supplement</h1>
<p class="lede">Every claim this project makes has a pre-registration frozen
before the run and a record generated from the data, and most records are
verified byte-identically by one command. This site publishes all three so a
reviewer can check a number without cloning anything.</p>
<div class="grid">{grid}</div>
<h2>How to verify it yourself</h2>
<p>A clean clone rebuilds the gated records and every figure from the committed
CSV exports, then diffs them against what is published here:</p>
<pre class="scroll"><code>git clone &lt;repo&gt; &amp;&amp; cd polyforge
pip install -r eval/requirements.txt
python scripts/reproduce.py --check   # inputs, dependencies, plan
python scripts/reproduce.py           # rebuild and diff every gated record</code></pre>
<p>Records outside the gate are listed as such on the
<a href="records.html">records</a> page — mostly because their generators need a
dataset that is not vendored. Nothing is hidden by omission.</p>
<h2>The ground rules these documents follow</h2>
<ul>
<li>A pre-registration is committed <em>and pushed</em> before its campaign runs;
the push is the timestamp anchor.</li>
<li>Every hypothesis is reported PASS or FAIL whichever way it lands, and a FAIL
is written up as the headline finding of its record.</li>
<li>A published record is never edited. A later run supersedes it with a new
record; the old one stands.</li>
<li>Any change to the simulator, the harness or an analysis script must leave
every gated record byte-identical.</li>
</ul>
{stamp}""".format(grid=grid, stamp=stamp)


def figures_body(caption_map: dict[str, str]) -> str:
    blocks = []
    for image in sorted(FIGURE_DIR.glob("fig*.png")):
        caption = caption_map.get(image.stem, NO_CAPTION)
        pdf = "figures/{}.pdf".format(image.stem)
        link = ('  <a href="{}">vector PDF</a>'.format(pdf)
                if (FIGURE_DIR / (image.stem + ".pdf")).exists() else "")
        blocks.append(
            '<figure id="{stem}"><img src="figures/{stem}.png" alt="{stem}" loading="lazy">'
            "<figcaption><strong>{stem}</strong> — {caption}{link}</figcaption></figure>".format(
                stem=image.stem, caption=to_html(caption, lambda t, h: t).removeprefix(
                    "<p>").removesuffix("</p>"), link=link))
    return ("<h1>Figures</h1><p class=\"lede\">Every figure is rebuilt from the "
            "committed exports by <code>scripts/reproduce.py</code> and compared "
            "against the committed copy. Captions come from "
            "<code>eval/results/figures/FIGURES.md</code>.</p>" + "".join(blocks))


def write_document(out: Path, document: Path, gated: frozenset[str],
                   published: frozenset[str], is_record: bool) -> None:
    body = to_html(document.read_text(encoding="utf-8"), linker(published))
    if is_record:
        in_gate = document.name in gated
        banner = ('<p><span class="tag {}">{}</span> <code>{}</code></p>'.format(
            "gated" if in_gate else "ungated",
            "verified byte-identically by scripts/reproduce.py" if in_gate
            else "not in the reproduction gate", html.escape(document.name)))
        body = banner + body
    (out / (document.stem + ".html")).write_text(
        page(title_of(document), body, ""), encoding="utf-8")


def prepare(out: Path, force: bool) -> None:
    if out.exists():
        if not (out / MARKER).exists() and not force:
            raise SystemExit(
                "refusing to overwrite {}: it is not a previous build "
                "(no {}). Use --force if that is really the target.".format(out, MARKER))
        shutil.rmtree(out)
    (out / "figures").mkdir(parents=True)
    (out / MARKER).write_text("generated by scripts/build_pages.py\n", encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")


def build(out: Path, strict: bool = False, force: bool = False) -> dict[str, int]:
    gated = gated_records()
    # Four gated records predate the RESULTS_* convention (RESULTS.md itself,
    # ADVANCED.md, FAIRNESS_V2.md, PHASE7_ORDINAL.md). Taking the union with the
    # gate means the supplement cannot silently omit a verified record — a
    # name-prefix glob alone dropped the headline record on the first build.
    records = sorted(set(ANALYSIS.glob("RESULTS_*.md")) | {ANALYSIS / n for n in gated},
                     key=lambda p: p.name)
    preregs = sorted(ANALYSIS.glob("PREREG_*.md"))
    published = frozenset(d.stem for d in records + preregs)
    caption_map = captions()
    missing = figures_without_captions()
    if strict and missing:
        raise SystemExit("figures without a caption in FIGURES.md: " + ", ".join(missing))

    prepare(out, force)
    for image in sorted(FIGURE_DIR.glob("fig*.png")) + sorted(FIGURE_DIR.glob("fig*.pdf")):
        shutil.copy2(image, out / "figures" / image.name)
    for document in records:
        write_document(out, document, gated, published, is_record=True)
    for document in preregs:
        write_document(out, document, gated, published, is_record=False)

    commit = os.environ.get("POLYFORGE_PAGES_COMMIT") or None
    figure_count = len(list(FIGURE_DIR.glob("fig*.png")))
    pages = {
        "index.html": (index_body(records, preregs, gated, figure_count, commit), "Overview"),
        "records.html": ("<h1>Generated records</h1><p class=\"lede\">Written by their "
                         "generator scripts, never by hand. The reproduction column is read "
                         "from <code>scripts/reproduce.py</code> at build time.</p>"
                         + document_list(records, gated, True), "Records"),
        "preregs.html": ("<h1>Pre-registrations</h1><p class=\"lede\">Each was committed and "
                         "pushed before its campaign ran, and none has been edited since.</p>"
                         + document_list(preregs, gated, False), "Pre-registrations"),
        "figures.html": (figures_body(caption_map), "Figures"),
    }
    for name, (body, title) in pages.items():
        (out / name).write_text(page(title, body, name), encoding="utf-8")

    return {"records": len(records), "preregs": len(preregs),
            "gated": sum(1 for r in records if r.name in gated),
            "figures": figure_count, "missing_captions": len(missing)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(REPO_ROOT / "site"), help="output directory")
    parser.add_argument("--strict", action="store_true",
                        help="fail if any figure has no caption in FIGURES.md")
    parser.add_argument("--force", action="store_true",
                        help="overwrite the output directory even if it was not built here")
    args = parser.parse_args(argv)

    counts = build(Path(args.out), strict=args.strict, force=args.force)
    print("wrote {} records, {} pre-registrations, {} figures to {}".format(
        counts["records"], counts["preregs"], counts["figures"], args.out))
    print("  {} of {} records are in the reproduction gate".format(
        counts["gated"], counts["records"]))
    if counts["missing_captions"]:
        print("  {} figure(s) have no caption in FIGURES.md: {}".format(
            counts["missing_captions"], ", ".join(figures_without_captions())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
