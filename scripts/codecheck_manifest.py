"""The CODECHECK manifest of programme 2 (plan WP20, level 1): `codecheck.yml` at the
repository's root, written from scripts/reproduce_programme2.py's steps so the two cannot drift.

CODECHECK's configuration (https://codecheck.org.uk/spec/config/2.0/): the author lists in
`manifest` the output files a codechecker recreates, and names the paper in `paper`; the
codechecker adds `codechecker`, `report`, `summary` and `certificate` after the check. Every report
a reproduction step rebuilds is listed with the step and command that rebuild it, and a step that
needs files the repository does not ship says which. The file's head (`version`, `paper`) is the
author's: it starts as placeholders, and a rerun rewrites only the manifest below MARKER.

    python scripts/codecheck_manifest.py           # write codecheck.yml
    python scripts/codecheck_manifest.py --check   # exit 1 when codecheck.yml's manifest is stale
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import reproduce_programme2 as rp  # noqa: E402

REPO = rp.REPO
TARGET = REPO / "codecheck.yml"
MARKER = "# manifest: written by scripts/codecheck_manifest.py from scripts/reproduce_programme2.py's steps"
HEAD = """---
version: https://codecheck.org.uk/spec/config/2.0/
# Recreate every file in the manifest: pip install -r research/requirements-campaigns.txt (Python
# 3.13; what GitHub's Linux runs of the reproduction installed), then python
# scripts/reproduce_programme2.py (--list prints the steps). Inputs the repository does not ship
# are named in research/DATA_LICENCES.md.
paper:
  title: "FILL IN: the paper's title"
  authors:
    - name: "FILL IN: the corresponding author"
      ORCID: "FILL IN"
  reference: "FILL IN: the paper's DOI or URL"
"""


def rel(path: Path) -> str:
    return path.resolve().relative_to(REPO.resolve()).as_posix()


def untracked(paths: list[str], repo: Path = REPO) -> list[str]:
    """The paths (files or folders) under which git tracks nothing."""
    out = []
    for p in paths:
        listed = subprocess.run(["git", "ls-files", "--", p], cwd=repo, capture_output=True, text=True, check=True)
        if not listed.stdout.strip():
            out.append(p)
    return out


def entries(steps=rp.STEPS) -> list[dict]:
    """One manifest item per report, in the steps' order; a report two steps rebuild is refused."""
    items, seen = [], set()
    for name, cwd, cmd, reports, needs in steps:
        missing = untracked([rel(cwd / n) for n in needs])
        note = f"; needs files the repository does not ship: {', '.join(missing)}" if missing else ""
        for report in reports:
            path = rel(cwd / report)
            if path in seen:
                raise SystemExit(f"{path} is rebuilt by two steps")
            seen.add(path)
            items.append({"file": path, "comment": f"{name}: python {' '.join(cmd)} (in {rel(cwd)}){note}"})
    return items


def manifest_text(items: list[dict]) -> str:
    lines = [MARKER, "manifest:"]
    for item in items:   # JSON strings are valid YAML double-quoted scalars
        lines += [f"  - file: {json.dumps(item['file'])}", f"    comment: {json.dumps(item['comment'])}"]
    return "\n".join(lines) + "\n"


def head_of(text: str) -> str:
    """The author's part of an existing file: everything above MARKER."""
    at = text.find(MARKER)
    if at < 0:
        raise SystemExit(f"{TARGET.name} has no line {MARKER!r}: the manifest's start is unknown")
    return text[:at]


def main(argv: list[str] | None = None, target: Path = TARGET) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="exit 1 when the manifest is stale")
    args = ap.parse_args(argv)
    body = manifest_text(entries())
    # read as git commits it (LF, .gitattributes): an editor on Windows may have saved CRLF
    old = target.read_bytes().decode("utf-8").replace("\r\n", "\n") if target.exists() else None
    if args.check:
        current = old is not None and old[len(head_of(old)):] == body
        print(f"{target.name}: manifest {'current' if current else 'STALE (run scripts/codecheck_manifest.py)'}")
        return 0 if current else 1
    target.write_bytes(((head_of(old) if old is not None else HEAD) + body).encode("utf-8"))
    print(f"{target.name}: {body.count('- file:')} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
