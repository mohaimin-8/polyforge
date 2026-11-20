"""Build programme 2's deposit (plan WP16: one DOI with code, protocols, run
data and the audit scripts): one zip, its manifest and the deposit metadata,
ready for a Zenodo upload. Publishing mints the DOI and needs the author's
Zenodo account; this script stops at the files.

The zip holds
  repository/      the tracked tree of one commit as `git archive` gives it,
                   the committed bytes (.gitattributes pins LF): code,
                   protocols, predictions committed before their runs, every
                   committed run record and report;
  audit_scripts/   the audit's scripts, kept outside the repository;
  confirmatory/    WP14's workflow artifact, when the campaign ran, exactly as
                   its SHA256SUMS seals it (a file missing, changed or added
                   since the seal refuses the build);
  MANIFEST.sha256  the sha256 of every other file in the zip (sha256sum format).
The out directory gets the zip, the same MANIFEST.sha256 and deposit.json
(creators from eval/zenodo_creators.json, checked by programme 1's reader).
The same inputs give the same zip byte for byte: entries in a fixed order,
all dated at the commit. The working tree never enters the deposit.

    python scripts/bundle_programme2.py --audit ../PolyForge_Audit_2026-11-11_scripts \
        [--commit HEAD] [--confirmatory DIR] [--out research/out/zenodo]
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import tarfile
import zipfile
from collections.abc import Iterator
from datetime import date, datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CREATORS = REPO / "eval" / "zenodo_creators.json"
PROGRAMME1_BUILDER = REPO / "eval" / "scripts" / "archive_zenodo.py"
SKIP_DIRS = frozenset({"__pycache__"})      # bytecode is derived, never deposited
SEAL = "SHA256SUMS"                          # what confirm.py seal writes at the artifact's root
SEAL_LINE = re.compile(r"([0-9a-f]{64})  (\S.*)")
KEYWORDS = ["kubernetes", "autoscaling", "LLM serving", "joint control", "interaction index",
            "vertical pod autoscaling", "pre-registration", "reproducibility"]


def programme1():
    """eval/scripts/archive_zenodo.py: one reader checks the creators of both deposits."""
    spec = importlib.util.spec_from_file_location("archive_zenodo", PROGRAMME1_BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"git {' '.join(args)}: {proc.stderr.strip() or f'exit {proc.returncode}'}")
    return proc.stdout.strip()


def commit_of(repo: Path, rev: str) -> tuple[str, tuple[int, ...]]:
    """The full commit id and its committer time (UTC) as a zip timestamp."""
    sha = git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}")
    t = datetime.fromtimestamp(int(git(repo, "show", "-s", "--format=%ct", sha)), tz=timezone.utc)
    return sha, (t.year, t.month, t.day, t.hour, t.minute, t.second)


def tracked_files(repo: Path, sha: str) -> Iterator[tuple[str, bytes]]:
    """(path, bytes) of every file in the commit's tree, in git's order, streamed."""
    proc = subprocess.Popen(["git", "-c", "core.autocrlf=false", "archive", "--format=tar", sha],
                            cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    complete = False
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
            for member in tar:
                if member.isdir():
                    continue
                if not member.isfile():     # a symlink or submodule would not survive the zip
                    raise SystemExit(f"{member.name}: not a regular file; the deposit holds files only")
                yield member.name, tar.extractfile(member).read()
        proc.stdout.read()      # the rest of the trailer's padding: unread, git could block writing it
        complete = True
    finally:
        if not complete:
            proc.kill()
        proc.stdout.close()
        err = proc.stderr.read().decode(errors="replace").strip()
        code = proc.wait()
    if code != 0:
        raise SystemExit(f"git archive {sha}: {err or f'exit {code}'}")


def folder_listing(folder: Path, prefix: str) -> list[tuple[str, Path]]:
    """(relative path, file) under folder, sorted, __pycache__ left out; refuses a
    missing or empty folder before anything is written."""
    if not folder.is_dir():
        raise SystemExit(f"{prefix}: {folder} is not a directory")
    files = sorted((p.relative_to(folder).as_posix(), p) for p in folder.rglob("*")
                   if p.is_file() and not SKIP_DIRS & set(p.relative_to(folder).parts[:-1]))
    if not files:
        raise SystemExit(f"{prefix}: {folder} has no files")
    return files


def sealed_names(sums: Path) -> dict[str, str]:
    """SHA256SUMS as confirm.py seal writes it: '<sha256>  <path>' lines, each path relative and inside."""
    names: dict[str, str] = {}
    for n, line in enumerate(sums.read_text(encoding="utf-8").splitlines(), 1):
        m = SEAL_LINE.fullmatch(line)
        name = m.group(2) if m else ""
        if not m or name.startswith("/") or "\\" in name or ":" in name or ".." in name.split("/"):
            raise SystemExit(f"confirmatory: {SEAL} line {n} is not '<sha256>  <path inside the folder>'")
        if name in names:
            raise SystemExit(f"confirmatory: {SEAL} line {n} lists {name} again")
        names[name] = m.group(1)
    return names


def check_seal(folder: Path, files: list[tuple[str, Path]]) -> None:
    """The artifact exactly as the run sealed it: its SHA256SUMS lists every other file with its
    sha256, and nothing else is there, so the deposit holds what the run's summary published.
    `sha256sum -c` alone would miss a file added after the seal."""
    if not (folder / SEAL).is_file():
        raise SystemExit(f"confirmatory: {folder} has no {SEAL}; give the folder the workflow's "
                         "`confirmatory` artifact unpacks to")
    sealed = sealed_names(folder / SEAL)
    present = {rel: path for rel, path in files if rel != SEAL}
    found = {"missing": sorted(set(sealed) - set(present)),
             "changed": sorted(r for r in set(sealed) & set(present) if file_sha256(present[r]) != sealed[r]),
             "not sealed": sorted(set(present) - set(sealed))}
    wrong = [f"{len(v)} {k} ({', '.join(v[:3])}{', ...' if len(v) > 3 else ''})" for k, v in found.items() if v]
    if wrong:
        raise SystemExit(f"confirmatory: {folder} does not match its {SEAL}: {'; '.join(wrong)}. Deposit the "
                         "artifact as downloaded (research/registration/PROTOCOL_NOTES.md, step 8).")


def add(zf: zipfile.ZipFile, name: str, data: bytes, stamp: tuple[int, ...]) -> str:
    """One entry, written the same way on every platform; its sha256."""
    info = zipfile.ZipInfo(name, date_time=stamp)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3                  # Unix, so the mode bits mean the same everywhere
    info.external_attr = 0o644 << 16
    zf.writestr(info, data)
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def build(repo: Path, rev: str, audit: Path, confirmatory: Path | None, out: Path) -> dict:
    """Write the zip and MANIFEST.sha256 into out; what went in."""
    sha, stamp = commit_of(repo, rev)
    folders = {"audit_scripts": folder_listing(audit, "audit_scripts")}
    if confirmatory is not None:
        folders["confirmatory"] = folder_listing(confirmatory, "confirmatory")
        check_seal(confirmatory, folders["confirmatory"])
    parts = [("repository", tracked_files(repo, sha)),
             *((prefix, ((rel, p.read_bytes()) for rel, p in files)) for prefix, files in folders.items())]
    out.mkdir(parents=True, exist_ok=True)
    zip_path = out / f"polyforge-programme2-{sha[:12]}.zip"
    partial = out / f"{zip_path.name}.partial"
    manifest, counts = [], {}
    try:
        with zipfile.ZipFile(partial, "w") as zf:
            for prefix, files in parts:
                counts[prefix] = 0
                for rel, data in files:
                    manifest.append(f"{add(zf, f'{prefix}/{rel}', data, stamp)}  {prefix}/{rel}")
                    counts[prefix] += 1
            text = "\n".join(manifest) + "\n"
            add(zf, "MANIFEST.sha256", text.encode("utf-8"), stamp)
    except BaseException:
        partial.unlink(missing_ok=True)     # never leave a half-built deposit behind
        raise
    partial.replace(zip_path)
    (out / "MANIFEST.sha256").write_text(text, encoding="utf-8", newline="\n")
    return {"commit": sha, "zip": zip_path, "counts": counts}


def deposit(sha: str, people: list[dict], counts: dict, audit_name: str, today: str) -> dict:
    """Zenodo's deposit metadata; entries with a "type" are contributors, as in programme 1's."""
    parts = [f"repository/, the tracked tree of the PolyForge repository at commit {sha} ({counts['repository']} "
             "files: code, protocols, predictions committed before the runs they predict, every committed run "
             "record and report)",
             f"audit_scripts/, {audit_name} ({counts['audit_scripts']} files)"]
    if "confirmatory" in counts:
        parts.append(f"confirmatory/, the confirmatory campaign's workflow artifact ({counts['confirmatory']} files)")
    meta = {
        "title": "PolyForge programme 2: code, protocols, run data and audit scripts",
        "upload_type": "dataset",
        "version": sha[:12],
        "description": ("Programme 2 of PolyForge: " + "; ".join(parts) + ". MANIFEST.sha256 gives every file's "
                        "sha256. In repository/ (a git archive: commit its files once, git init -q && git add -A && "
                        "git -c user.name=deposit -c user.email=deposit@invalid commit -qm deposit, or clone "
                        "that commit), with Python 3.13 and pip install -r research/requirements-campaigns.txt, "
                        "python scripts/reproduce_programme2.py rebuilds every report from the committed data and "
                        "compares it with the committed text; "
                        "research/PROGRAMME2.md maps the work packages to their files; "
                        "research/DATA_LICENCES.md names the third-party data included and those fetched by "
                        "script."),
        "creators": [c for c in people if "type" not in c],
        "keywords": KEYWORDS,
        "license": "cc-by-4.0",
        "publication_date": today,
    }
    contributors = [c for c in people if "type" in c]
    if contributors:
        meta["contributors"] = contributors
    return {"metadata": meta}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--audit", type=Path, required=True, help="the audit scripts folder (outside the repository)")
    ap.add_argument("--commit", default="HEAD", help="the commit whose tracked tree is deposited (default HEAD)")
    ap.add_argument("--confirmatory", type=Path, help="WP14's downloaded workflow artifact, when the campaign ran")
    ap.add_argument("--out", type=Path, default=REPO / "research" / "out" / "zenodo")
    ap.add_argument("--creators", type=Path, default=CREATORS)
    ap.add_argument("--repo", type=Path, default=REPO, help="the repository (default: this one)")
    args = ap.parse_args(argv)
    p1 = programme1()
    people = p1.creators(args.creators)     # a malformed file refuses before anything is built
    result = build(args.repo, args.commit, args.audit, args.confirmatory, args.out)
    meta = deposit(result["commit"], people, result["counts"], args.audit.resolve().name, str(date.today()))
    (args.out / "deposit.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    z = result["zip"]
    print(f"{z} ({z.stat().st_size / 1e6:.1f} MB, sha256 {file_sha256(z)}): "
          + ", ".join(f"{k} {v} files" for k, v in result["counts"].items())
          + f"; MANIFEST.sha256 and deposit.json beside it. Commit {result['commit']}.")
    if people == p1.PLACEHOLDER_CREATORS:
        print(f"WARNING: placeholder creators; write {args.creators} before publishing.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
