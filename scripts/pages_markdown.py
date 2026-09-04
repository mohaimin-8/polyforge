"""A small Markdown renderer for the evidence supplement (D3).

Deliberately not a general Markdown implementation and deliberately not a
dependency: the pages it renders are this repository's *generated* records and
its frozen pre-registrations, which use one narrow dialect — ATX headings,
pipe tables, fenced code, blockquotes, flat lists, and inline code/bold/
italic/links. Adding `markdown` to `eval/requirements.txt` would change the
environment that `scripts/reproduce.py` runs in, and R4 forbids touching that
for a cosmetic reason.

Link policy is the load-bearing part. A record links to its pre-registration
as `PREREG_X.md`; the supplement renders both, so that becomes `PREREG_X.html`.
A link whose target is *not* published — a path into the repository, a
document the supplement does not render — keeps its words and loses its
anchor, because a supplement full of 404s reads as carelessness about the
evidence itself.
"""

from __future__ import annotations

import html
import re
from typing import Callable

LinkTarget = Callable[[str, str], str]

_CODE_SPAN = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![*\w])\*([^*\n]+?)\*(?!\*)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_LIST_ITEM = re.compile(r"^\s*([-*]|\d+\.)\s+(.*)$")
_RULE = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")
_SENTINEL = "\x00code{}\x00"


def render_inline(text: str, link: LinkTarget) -> str:
    """Escape, then apply inline markup. Code spans are masked first so their
    contents are never re-interpreted as emphasis."""
    spans: list[str] = []

    def stash(match: re.Match[str]) -> str:
        spans.append(html.escape(match.group(1), quote=False))
        return _SENTINEL.format(len(spans) - 1)

    masked = _CODE_SPAN.sub(stash, text)
    out = html.escape(masked, quote=False)
    out = _LINK.sub(lambda m: link(m.group(1), m.group(2)), out)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    out = _ITALIC.sub(r"<em>\1</em>", out)
    for index, span in enumerate(spans):
        out = out.replace(_SENTINEL.format(index), f"<code>{span}</code>")
    return out


# Known quirk, faithful on purpose: three generated records (RESULTS.md,
# RESULTS_V2.md, RESULTS_V3.md) write `|dz|>=0.5` inside a table cell without
# escaping the pipes, so those rows split into extra cells and leave a stray
# `**`. GitHub renders them exactly as wrong. Splitting on unescaped pipes is
# what Markdown specifies, the records are frozen, and guessing which surplus
# cells to re-join would be inventing content — so the supplement reproduces
# the source rather than quietly repairing it.
def _is_table_row(line: str) -> bool:
    return line.startswith("|") and line.rstrip().endswith("|")


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _alignments(delimiter: str) -> list[str] | None:
    aligns = []
    for cell in _cells(delimiter):
        if not re.fullmatch(r":?-{1,}:?", cell):
            return None
        if cell.endswith(":"):
            aligns.append("right" if not cell.startswith(":") else "center")
        else:
            aligns.append("left")
    return aligns


def _table(lines: list[str], start: int, link: LinkTarget) -> tuple[str, int]:
    aligns = _alignments(lines[start + 1])
    if aligns is None:  # not an assert: -O strips those, and this guards a real path
        raise ValueError(f"not a table delimiter row: {lines[start + 1]!r}")
    head = _cells(lines[start])
    rows, index = [], start + 2
    while index < len(lines) and _is_table_row(lines[index]):
        rows.append(_cells(lines[index]))
        index += 1

    def cell(tag: str, value: str, position: int) -> str:
        align = aligns[position] if position < len(aligns) else "left"
        return f'<{tag} style="text-align:{align}">{render_inline(value, link)}</{tag}>'

    out = ['<div class="scroll"><table>', "<thead><tr>"]
    out += [cell("th", value, i) for i, value in enumerate(head)]
    out.append("</tr></thead><tbody>")
    for row in rows:
        out.append("<tr>" + "".join(cell("td", v, i) for i, v in enumerate(row)) + "</tr>")
    out.append("</tbody></table></div>")
    return "\n".join(out), index


def _fence(lines: list[str], start: int) -> tuple[str, int]:
    index = start + 1
    body: list[str] = []
    while index < len(lines) and not lines[index].startswith("```"):
        body.append(lines[index])
        index += 1
    escaped = html.escape("\n".join(body), quote=False)
    return f'<pre class="scroll"><code>{escaped}</code></pre>', index + 1


def _list(lines: list[str], start: int, link: LinkTarget) -> tuple[str, int]:
    """Consecutive items, each folding its indented continuation lines.

    Folding is not cosmetic: these documents wrap at ~76 columns, so an item's
    emphasis routinely opens on one line and closes on the next. Rendering each
    physical line separately left stray `**` on the page."""
    ordered = _LIST_ITEM.match(lines[start]).group(1) not in ("-", "*")
    parts: list[list[str]] = []
    index = start
    while index < len(lines):
        line = lines[index]
        match = _LIST_ITEM.match(line)
        if match:
            parts.append([match.group(2)])
        elif parts and line.strip() and line[:1].isspace():
            parts[-1].append(line.strip())
        else:
            break
        index += 1
    items = "".join(f"<li>{render_inline(' '.join(part), link)}</li>" for part in parts)
    tag = "ol" if ordered else "ul"
    return f"<{tag}>{items}</{tag}>", index


def _quote(lines: list[str], start: int, link: LinkTarget) -> tuple[str, int]:
    body, index = [], start
    while index < len(lines) and lines[index].startswith(">"):
        body.append(lines[index].lstrip(">").strip())
        index += 1
    return f"<blockquote><p>{render_inline(' '.join(body), link)}</p></blockquote>", index


def _paragraph(lines: list[str], start: int, link: LinkTarget) -> tuple[str, int]:
    body, index = [], start
    while index < len(lines):
        line = lines[index]
        if not line.strip() or line.startswith(("#", ">", "```")) or _is_table_row(line):
            break
        if _LIST_ITEM.match(line) or _RULE.match(line.strip()):
            break
        body.append(line.strip())
        index += 1
    return f"<p>{render_inline(' '.join(body), link)}</p>", index


def to_html(text: str, link: LinkTarget) -> str:
    """Render one document. `link(text, href)` decides what each link becomes."""
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        heading = _HEADING.match(line)
        if not line.strip():
            index += 1
        elif line.startswith("```"):
            block, index = _fence(lines, index)
            out.append(block)
        elif heading:
            level = len(heading.group(1))
            out.append(f"<h{level}>{render_inline(heading.group(2), link)}</h{level}>")
            index += 1
        elif _is_table_row(line) and index + 1 < len(lines) and _alignments(lines[index + 1]):
            block, index = _table(lines, index, link)
            out.append(block)
        elif line.startswith(">"):
            block, index = _quote(lines, index, link)
            out.append(block)
        elif _LIST_ITEM.match(line):
            block, index = _list(lines, index, link)
            out.append(block)
        elif _RULE.match(line.strip()):
            out.append("<hr>")
            index += 1
        else:
            block, index = _paragraph(lines, index, link)
            out.append(block)
    return "\n".join(out)
