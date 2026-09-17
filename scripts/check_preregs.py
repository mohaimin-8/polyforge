"""The cardinal rule, checked against git instead of asserted in prose.

The supplement tells reviewers that each pre-registration "was committed and
pushed before its campaign ran, and none has been edited since". That is the
central methodological claim of this work -- frozen rules are what keep a
published FAIL from being quietly rewritten into a PASS -- and until this
script it was enforced by the author's discipline and by nothing else.

What it establishes, from history rather than from the documents:

1. **No registered text was altered once its results existed.** For every
   PREREG_X.md with a matching record, the file as it stood at its last commit
   BEFORE the record first appeared is the registered text. That text must
   survive today byte-for-byte as an exact prefix of the file. Then every
   later change is strictly an addition after it, and no declared rule,
   threshold or hypothesis can have moved -- a one-character edit fails.

   This replaced a stricter-looking rule that was actually weaker: "no commit
   touches the prereg at or after the record's first commit". On 2026-09-08
   that rule went red on three files, and reading the diffs showed why it was
   the wrong test. All three were additions after the registered text -- a
   superseding note pointing at a re-run, and an amendment declared before a
   run that has not happened -- and the only way to keep that rule green
   would have been to move such notes out of the file they are about, or to
   grow an exception list. Neither makes the registered text safer. Checking
   the text itself does, and it is what a reviewer with the repository can
   also check.

2. **What was added after a result, and under what heading.** Post-result
   additions are not silently tolerated: each one is printed with its byte
   count and every markdown heading it introduced, so the reader sees "a
   superseding note" or "a pre-run amendment" and can open it. An addition
   that quietly contradicted the registered text would still be an addition;
   this gate proves the registered text is intact, and shows the reader
   where to look for the rest.

3. **What was amended, and when.** Several preregs carry more than one
   commit. That is not a violation -- most are amendments made and pushed
   before the run, and PREREG_MULTINODE's records a VOID sitting -- but "none
   has been edited since" is not true of them, so the count is reported
   rather than smoothed over.

**Limits, stated rather than implied.** Git commit dates come from the
committer's clock and can be rewritten; the true anchor is the push event on
GitHub, which is not recoverable from a clone. This checks ORDER and CONTENT
within the history, which is what a reviewer with the repository can also
check. It cannot prove the history was never rewritten wholesale.

    python scripts/check_preregs.py

Exits nonzero if any registered text was altered at or after its record's
first commit, or if a prereg has no commit before its record at all and is
not disclosed as such.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# The one campaign whose prereg carries no independent anchor, named here
# rather than left for a reader to find. Commit 33bd83c added, in a single
# commit: the experiment config, the metrics export, PREREG_EVICTION_PARITY.md
# and RESULTS_EVICTION_PARITY.md. The prereg was therefore not pushed before
# that campaign ran, and for this one result the freeze rests on the document
# alone. Listed so the check fails on a NEW violation instead of sitting
# permanently red -- and so the exception is published rather than absorbed.
# Not every campaign names its record after its pre-registration, so matching
# on the name alone left 12 of 44 preregs with "no matching record" and
# therefore never ordering-checked -- a gate quietly covering three quarters of
# its domain while reporting nothing wrong.
#
# Each entry was derived from the prereg's OWN registered hypothesis prefix
# (the tags in its hypothesis headings) and the record that reports that
# prefix -- not from the first RESULTS_*.md the prereg happens to mention. That
# looser rule produced two false violations: PREREG_VIOLATION_PARITY cites
# TP-* while registering VP-*, and PREREG_LIVE_SOAK_V6 cites the previous
# attempt's record.
RECORD_FOR = {
    "PREREG_COORD_GAP": "COORD_GAP.md",                     # CG-H*
    "PREREG_DEGRADE": "DEGRADE_PROBE.md",                   # DG-H*
    "PREREG_LEARNED_CONTROL": "RESULTS_LEARNED.md",         # LR-H*
    "PREREG_MULTINODE_V2": "RESULTS_MULTINODE.md",          # MN-H*
    "PREREG_PLANNER_CELLS": "PLANNER_CELLS.md",             # PS-H*
    "PREREG_PLANNER_CELLS_DEALIAS": "PLANNER_CELLS_DEALIAS.md",  # PF-H*
    "PREREG_PSEUDO_TENANT": "PSEUDO_TENANT.md",             # PT-H*
    "PREREG_RISK_MPC": "RESULTS_RISK.md",                   # RQ-H*
    "PREREG_VTC": "VTC_FAIRNESS.md",                        # registers no tags
    "PREREG_WIRE_ATTACK": "security/RESULTS_WIRE_ATTACK.md",  # WA-H*
}

# Two preregs have no record, and both dispositions are disclosed rather than
# left as silence. An unreported pre-registration is the file-drawer problem
# these documents exist to prevent, so they are named here instead of sitting
# in an "unmatched" count nobody reads.
NO_RECORD = {
    "PREREG_VIOLATION_PARITY":
        "premise falsified before the ladder ran: the frozen beta ladder is "
        "inert (mean_excess 4.8319 -> 4.8314 across a 32x increase, b184aee). "
        "The prereg stays frozen and PREREG_BUDGET_PARITY discloses that its "
        "ladder is not run.",
    "PREREG_LIVE_SOAK_V6":
        "attempt 8 produced no scoreable sitting, so it has no record of its "
        "own; its evidence is quoted inside RESULTS_LIVE_SOAK_V7.md, which "
        "reports the attempt that followed it.",
}

DISCLOSED = {
    "PREREG_EVICTION_PARITY":
        "committed in the same commit (33bd83c) as its own record and data; "
        "no independent timestamp anchor for this campaign",
}

REPO_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = REPO_ROOT / "research" / "analysis"
RESULT_DIRS = (ANALYSIS, REPO_ROOT / "eval" / "results")


def git(*args: str, strip: bool = True) -> str:
    # encoding is explicit. These documents carry em dashes and the
    # records they point at carry more, so a Windows default of cp1252
    # makes this gate die on a decode error instead of reporting
    # anything. A gate that cannot run on a reviewer's machine is a gate
    # nobody checks.
    out = subprocess.run(("git", *args), cwd=REPO_ROOT, capture_output=True,
                         text=True, encoding="utf-8", check=True).stdout
    return out.strip() if strip else out


def commits(path: Path) -> list[tuple[int, str, str]]:
    """Every commit touching a file AT THIS PATH, newest first.

    Deliberately not --follow. RESULTS_LIVE_SOAK_V5.md was created from
    RESULTS_LIVE_SOAK_V4.md, and --follow walks the record's history back into
    V4's -- a different campaign, two days earlier -- which made V5's prereg
    look as though it postdated its own results. It did not: the V5 record
    first appears 2026-08-27, its prereg 2026-08-26. No prereg has a rename in
    its history at all, so --follow buys nothing there and manufactures a
    false violation here.
    """
    rel = path.relative_to(REPO_ROOT).as_posix()
    raw = git("log", "--format=%ct%x09%H%x09%s", "--", rel)
    out = []
    for line in raw.splitlines():
        when, _, rest = line.partition("\t")
        sha, _, subject = rest.partition("\t")
        out.append((int(when), subject, sha))
    return out


def record_for(prereg: Path) -> Path | None:
    name = RECORD_FOR.get(prereg.stem,
                          "RESULTS_" + prereg.stem[len("PREREG_"):] + ".md")
    for base in RESULT_DIRS:
        if (base / name).exists():
            return base / name
    return None


def blob_at(sha: str, path: Path) -> str:
    """The file's content as of one commit, for comparing against now."""
    rel = path.relative_to(REPO_ROOT).as_posix()
    return git("show", f"{sha}:{rel}", strip=False)


def appended_headings(frozen: str, current: str) -> list[str]:
    """The markdown headings of what was added after the frozen text."""
    return [line.strip() for line in current[len(frozen):].splitlines()
            if line.startswith("#")]


def shallow() -> bool:
    """A depth-1 checkout has no history, so every ordering check would pass
    on a single commit. That is a gate reporting success because it could not
    look -- the exact failure this file exists to prevent elsewhere."""
    return git("rev-parse", "--is-shallow-repository") == "true"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if shallow():
        print("FAILED: shallow clone -- this check needs full history "
              "(actions/checkout needs fetch-depth: 0).")
        return 1

    preregs = sorted(ANALYSIS.glob("PREREG_*.md"))
    if not preregs:
        print("FAILED: no pre-registrations found; the check would pass on "
              "an empty set.")
        return 1

    violations: list[str] = []
    disclosed_seen: set[str] = set()
    amended: list[tuple[str, int]] = []
    appended: list[tuple[str, int, int, list[str]]] = []
    unmatched: list[str] = []
    recordless: list[str] = []
    width = max(len(p.stem) for p in preregs)

    for prereg in preregs:
        history = commits(prereg)
        if len(history) > 1:
            amended.append((prereg.stem, len(history)))
        if prereg.stem in NO_RECORD:
            recordless.append(prereg.stem)
            continue
        record = record_for(prereg)
        if record is None:
            unmatched.append(prereg.stem)
            continue
        record_history = commits(record)
        if not record_history:
            unmatched.append(prereg.stem)
            continue
        first_result = record_history[-1][0]
        after = [(t, subj) for t, subj, _ in history if t >= first_result]
        before = [sha for t, _, sha in history if t < first_result]
        if not after:
            continue
        if not before:
            # The prereg has no commit at all before its record: there is no
            # registered version to compare against, so the freeze rests on
            # the document alone. That is the disclosed-anchor case.
            if prereg.stem in DISCLOSED:
                disclosed_seen.add(prereg.stem)
            else:
                violations.append(
                    f"{prereg.stem}: first appears at or after {record.name}, "
                    "so nothing was registered in advance")
            continue
        # `before[0]` is the newest commit that precedes the record: the
        # REGISTERED text, in the state a reader had when the campaign was
        # scored. The rule is that this text survives verbatim. If it is an
        # exact prefix of the file today, every post-result change is strictly
        # an addition after it, and no declared rule, threshold or hypothesis
        # can have been altered -- which is the claim the gate exists to make.
        # Anything else is a violation, including a one-character edit.
        frozen = blob_at(before[0], prereg)
        current = prereg.read_text(encoding="utf-8")
        if current.startswith(frozen):
            appended.append((prereg.stem, len(after), len(current) - len(frozen),
                             appended_headings(frozen, current)))
        else:
            # DISCLOSED deliberately does not reach here. It excuses a missing
            # anchor, never a changed registered text; an allowlist that could
            # excuse the latter would be the hole this gate exists to close.
            for when, subject in after:
                violations.append(f"{prereg.stem}: REGISTERED TEXT CHANGED at "
                                  f"or after {record.name} first appeared -- "
                                  f"{subject}")

    print(f"pre-registrations           {len(preregs)}")
    print(f"never amended               {len(preregs) - len(amended)}")
    print(f"amended after freezing      {len(amended)}")
    for name, n in sorted(amended):
        print(f"  {name:<{width}} {n} commits")
    print(f"no record, disclosed        {len(recordless)}")
    for name in sorted(recordless):
        print(f"  {name}: {NO_RECORD[name]}")
    if unmatched:
        print(f"UNMAPPED (ordering unchecked) {len(unmatched)}")
        for name in unmatched:
            print(f"  {name} -- add it to RECORD_FOR or NO_RECORD")
    print(f"appended after their results {len(appended)}")
    for name, n_commits, n_bytes, headings in sorted(appended):
        print(f"  {name}: registered text intact byte-for-byte; "
              f"{n_commits} later commit(s) added {n_bytes} bytes after it")
        for heading in headings:
            print(f"      {heading}")
    print(f"edited after their results  {len(violations)}")
    for line in violations:
        print(f"  VIOLATION: {line}")

    print()
    for name in sorted(DISCLOSED):
        state = "applies" if name in disclosed_seen else "NO LONGER APPLIES"
        print(f"  disclosed exception [{state}]: {name}")
        print(f"    {DISCLOSED[name]}")

    stale = set(DISCLOSED) - disclosed_seen
    if violations:
        print("FAILED: a frozen rule was touched after its result existed.")
    if stale:
        print(f"FAILED: {len(stale)} disclosed exception(s) no longer apply "
              f"and must be removed: {chr(44).join(sorted(stale))}")
    if violations or stale:
        return 1
    print("OK: no registered text was altered after its result existed. "
          "Post-result changes, where they exist, are additions after the "
          "registered text and are listed above with their headings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())