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


def test_a_prereg_edited_after_its_record_is_caught(monkeypatch):
    """The violation the gate exists for, injected rather than hoped for."""
    real = cp.commits
    victim = "PREREG_LIVE_SOAK_V8.md"

    def fake(path: Path):
        history = real(path)
        if path.name == victim:            # push its newest commit far forward
            newest = max(t for t, _ in history)
            return [(newest + 10_000_000, "edited after the fact")] + history
        return history

    monkeypatch.setattr(cp, "commits", fake)
    assert cp.main() == 1, "a prereg edited after its record did not fail the gate"


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
    assert min(t for t, _ in cp.commits(prereg)) >= min(t for t, _ in cp.commits(record)), \
        "the prereg now predates its record; remove it from DISCLOSED"


def test_a_shallow_clone_fails_rather_than_passing_blind():
    out = subprocess.run(["git", "rev-parse", "--is-shallow-repository"],
                         cwd=REPO_ROOT, capture_output=True, text=True)
    assert out.stdout.strip() == "false", "this checkout is shallow; the gate should refuse it"
