"""The prereg gate must be able to fail, and must fail for the right reason.

R5 through R10 were all one shape: a gate that could not fail, or that claimed
more than it checked. This gate makes the strongest claim in the repository --
that no frozen rule was touched after its result existed -- so it is held to
the same standard as the checks it joins.

Run: python -m pytest scripts/test_check_preregs.py -q
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location(
    "check_preregs", REPO_ROOT / "scripts" / "check_preregs.py")
cp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cp)


def test_the_gate_passes_on_the_real_history():
    assert cp.main() == 0


def test_every_prereg_is_actually_examined():
    preregs = sorted(cp.ANALYSIS.glob("PREREG_*.md"))
    assert len(preregs) >= 44, "preregs vanished; the gate would pass on fewer"
    for prereg in preregs:
        assert cp.commits(prereg), f"{prereg.name} has no history to check"


def _post_result_commit(real_commits):
    """A fake newest commit on one prereg, dated after every real one."""
    def fake(path: Path):
        history = real_commits(path)
        if path.name == fake.victim:
            newest = max(t for t, _, _ in history)
            return [(newest + 10_000_000, "edited after the fact", "deadbeef")] + history
        return history
    fake.victim = None
    return fake


def test_registered_text_altered_after_its_record_is_caught(monkeypatch, capsys):
    """The violation the gate exists for, injected rather than hoped for:
    a threshold in the registered text changed after the result existed."""
    victim = "PREREG_LIVE_SOAK_V8.md"
    fake = _post_result_commit(cp.commits)
    fake.victim = victim
    monkeypatch.setattr(cp, "commits", fake)

    # The registered text is what the gate reads at the frozen revision; make
    # the file on disk differ from it by one character inside that text.
    real_read = Path.read_text
    def tampered(self, *a, **k):
        text = real_read(self, *a, **k)
        if self.name == victim:
            # one character, well inside the registered text
            i = 40
            return text[:i] + ("X" if text[i] != "X" else "Y") + text[i + 1:]
        return text
    monkeypatch.setattr(Path, "read_text", tampered)

    assert cp.main() == 1, "a registered text altered after its record did not fail the gate"
    assert "REGISTERED TEXT CHANGED" in capsys.readouterr().out


def test_a_post_result_commit_that_only_appends_is_reported_not_failed(monkeypatch, capsys):
    """The other half of the rule. An addition after the registered text is
    not a violation -- but it must be printed, with its heading, not absorbed."""
    victim = "PREREG_LIVE_SOAK_V8.md"
    fake = _post_result_commit(cp.commits)
    fake.victim = victim
    monkeypatch.setattr(cp, "commits", fake)

    real_read = Path.read_text
    def appended(self, *a, **k):
        text = real_read(self, *a, **k)
        if self.name == victim:
            return text + "\n## Superseding note (TEST)\nappended text\n"
        return text
    monkeypatch.setattr(Path, "read_text", appended)

    assert cp.main() == 0, "an append-only post-result change was treated as a violation"
    out = capsys.readouterr().out
    assert "PREREG_LIVE_SOAK_V8: registered text intact byte-for-byte" in out
    assert "## Superseding note (TEST)" in out, "the appended heading was not disclosed"


def test_the_three_real_appendices_keep_their_registered_text_intact():
    """The cases that motivated the rule. If any of these ever stops being a
    pure prefix, someone edited registered text, and this fails before the
    gate does."""
    for name in ("PREREG_TIER_RATIO", "PREREG_TIER_WU", "PREREG_WAVE4_LIVE_PLANE"):
        prereg = cp.ANALYSIS / f"{name}.md"
        record = cp.record_for(prereg)
        assert record is not None, name
        first_result = cp.commits(record)[-1][0]
        before = [sha for t, _, sha in cp.commits(prereg) if t < first_result]
        assert before, f"{name} has no commit before its record"
        frozen = cp.blob_at(before[0], prereg)
        current = prereg.read_text(encoding="utf-8")
        assert current.startswith(frozen), f"{name}: registered text no longer intact"
        assert len(current) > len(frozen), f"{name}: expected a post-result addition"


def test_a_stale_disclosed_exception_fails(monkeypatch):
    """An allowlist that is never re-checked becomes a place to hide things."""
    monkeypatch.setitem(cp.DISCLOSED, "PREREG_THAT_IS_CLEAN",
                        "an exception that no longer applies")
    assert cp.main() == 1, "a disclosed exception that no longer applies was not flagged"


def test_the_known_exception_is_still_real():
    """If EVICTION_PARITY ever gains an anchor, the exception must be removed
    rather than left standing as an untrue disclosure."""
    prereg = cp.ANALYSIS / "PREREG_EVICTION_PARITY.md"
    record = cp.record_for(prereg)
    assert record is not None
    assert min(t for t, _, _ in cp.commits(prereg)) >= min(t for t, _, _ in cp.commits(record)), \
        "the prereg now predates its record; remove it from DISCLOSED"


def test_a_shallow_clone_fails_rather_than_passing_blind():
    out = subprocess.run(["git", "rev-parse", "--is-shallow-repository"],
                         cwd=REPO_ROOT, capture_output=True, text=True)
    assert out.stdout.strip() == "false", "this checkout is shallow; the gate should refuse it"
