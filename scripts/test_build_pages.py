"""Tests for the evidence supplement build (FINAL_ROADMAP D3).

The load-bearing properties are not cosmetic. A supplement that omits a
verified record, mislabels one as verified, or ships dead links misrepresents
the evidence base — which is the one thing this project cannot afford. Those
three are asserted against the repository's real inputs rather than fixtures,
so a future record or figure is covered the day it lands.

Run: python -m pytest scripts/test_build_pages.py -q
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_pages  # noqa: E402
from pages_markdown import to_html  # noqa: E402


def KEEP_TEXT(text: str, href: str) -> str:
    """The 'nothing is published' linker: every link keeps its words, loses its anchor."""
    return text


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("site")
    build_pages.build(out, force=True)
    return out


def _pages(site: Path) -> list[Path]:
    return sorted(site.glob("*.html"))


def test_every_record_in_the_reproduction_gate_gets_a_page(site: Path) -> None:
    # Arrange
    gated = build_pages.gated_records()

    # Act
    published = {path.stem for path in _pages(site)}

    # Assert - the first build silently dropped RESULTS.md, ADVANCED.md,
    # FAIRNESS_V2.md and PHASE7_ORDINAL.md because they predate the RESULTS_*
    # naming convention. A gated record missing from the supplement is the
    # worst failure this build has.
    missing = sorted(name for name in gated if Path(name).stem not in published)
    assert missing == [], f"gated records with no page: {missing}"


def test_the_gate_column_reports_exactly_what_reproduce_verifies(site: Path) -> None:
    # Arrange
    gated = build_pages.gated_records()
    rows = (site / "records.html").read_text(encoding="utf-8").split("<tr>")

    # Act
    marked_in_gate = {
        match.group(1) for row in rows
        if "in gate" in row and (match := re.search(r'href="([^"]+)\.html"', row))
    }

    # Assert
    assert marked_in_gate == {Path(name).stem for name in gated}


def test_no_page_links_to_something_that_was_not_published(site: Path) -> None:
    # Arrange
    targets = {path.name for path in site.rglob("*") if path.is_file()}
    targets |= {f"figures/{path.name}" for path in (site / "figures").iterdir()}

    # Act
    broken = []
    for page in _pages(site):
        for href in re.findall(r'href="([^"]+)"', page.read_text(encoding="utf-8")):
            if href.startswith(("http://", "https://", "#")):
                continue
            if href not in targets and Path(href).name not in targets:
                broken.append((page.name, href))

    # Assert
    assert broken == [], f"dead links: {broken[:5]}"


def test_two_builds_of_the_same_inputs_are_byte_identical(tmp_path: Path) -> None:
    # Arrange
    first, second = tmp_path / "a", tmp_path / "b"

    # Act
    build_pages.build(first, force=True)
    build_pages.build(second, force=True)

    def digest(root: Path) -> dict[str, str]:
        return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(root.rglob("*")) if p.is_file()}

    # Assert - determinism is what lets a reviewer diff the deployed site
    # against a local build.
    assert digest(first) == digest(second)


def test_a_figure_with_no_caption_is_marked_rather_than_dropped(site: Path) -> None:
    # Arrange
    uncaptioned = build_pages.figures_without_captions()
    figures = (site / "figures.html").read_text(encoding="utf-8")

    # Act / Assert - fig18 and fig19 have no entry in FIGURES.md today. They
    # still appear, carrying the reason, instead of vanishing from the gallery.
    for stem in uncaptioned:
        assert f'id="{stem}"' in figures
    if uncaptioned:
        assert build_pages.NO_CAPTION.split(" — ")[0] in figures


def test_strict_mode_refuses_to_build_with_a_missing_caption(tmp_path: Path) -> None:
    if not build_pages.figures_without_captions():
        pytest.skip("every figure is captioned; strict mode has nothing to catch")
    with pytest.raises(SystemExit):
        build_pages.build(tmp_path / "strict", strict=True, force=True)


def test_the_build_refuses_to_delete_a_directory_it_did_not_create(tmp_path: Path) -> None:
    # Arrange
    target = tmp_path / "not-a-site"
    target.mkdir()
    (target / "important.txt").write_text("do not delete me", encoding="utf-8")

    # Act / Assert
    with pytest.raises(SystemExit):
        build_pages.build(target)
    assert (target / "important.txt").exists()


def test_the_build_writes_nothing_into_the_evidence_base(tmp_path: Path) -> None:
    # Arrange - R4 depends on these bytes; the supplement is a reader.
    watched = sorted(build_pages.ANALYSIS.glob("*.md")) + \
        sorted(build_pages.FIGURE_DIR.glob("fig*"))
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in watched}

    # Act
    build_pages.build(tmp_path / "site", force=True)

    # Assert
    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in watched}
    assert before == after


def test_a_record_page_says_whether_the_record_is_verified(site: Path) -> None:
    verified = (site / "RESULTS_TRACE_LIVE.html").read_text(encoding="utf-8")
    assert "verified byte-identically" in verified


# --- renderer units ------------------------------------------------------


def test_a_list_item_wrapped_across_lines_keeps_its_emphasis_intact() -> None:
    source = "- MC-H3 (violation non-inferiority where the **clamp advantage\n  should bite**): PASS"
    assert "<strong>clamp advantage should bite</strong>" in to_html(source, KEEP_TEXT)
    assert "**" not in to_html(source, KEEP_TEXT)


def test_emphasis_inside_a_code_span_is_left_alone() -> None:
    assert "<code>a**b**c</code>" in to_html("`a**b**c`", KEEP_TEXT)


def test_a_right_aligned_table_column_survives_rendering() -> None:
    html = to_html("| a | b |\n|---|---:|\n| 1 | 2 |", KEEP_TEXT)
    assert 'style="text-align:right"' in html
    assert html.count("<td") == 2


def test_a_link_to_an_unpublished_document_keeps_its_words_and_drops_the_anchor() -> None:
    link = build_pages.linker(frozenset({"PREREG_TRACE_LIVE"}))
    assert to_html("see [the prereg](PREREG_TRACE_LIVE.md)", link) == \
        '<p>see <a href="PREREG_TRACE_LIVE.html">the prereg</a></p>'
    assert to_html("see [the roadmap](../../docs/FINAL_ROADMAP.md)", link) == \
        "<p>see the roadmap</p>"


def test_html_in_the_source_is_escaped_not_executed() -> None:
    assert "<script>" not in to_html("a <script>alert(1)</script> b", KEEP_TEXT)
