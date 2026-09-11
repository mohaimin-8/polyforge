"""W35c artifact packaging: build the Zenodo deposit as a single zip plus
a deposit metadata JSON ready for the Zenodo API/web upload.

Bundles exactly what the FGCS reproducibility checklist wants:
- raw result DuckDB files (full matrix + ablations + smoke)
- experiment YAMLs, tuned baseline params, tuning grids
- Terraform IaC for the cluster the cloud runs execute on
- SMOKE_BUGS.md and the burn log
- a MANIFEST with sha256 of every file

Publishing needs a human with a Zenodo account (the DOI is minted on
publish); this script stops at a ready-to-upload bundle.

Creators are read from eval/zenodo_creators.json (tracked) when it exists:
    [{"name": "Family, Given", "affiliation": "...", "orcid": "0000-0000-0000-0000"}]
Without it the deposit carries a placeholder and the build says so.

Usage (from eval/):  python scripts/archive_zenodo.py [--out results/zenodo]
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
import zipfile
from datetime import date
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = EVAL_DIR.parent
sys.path.insert(0, str(EVAL_DIR))

# Globs are supported. results/*.duckdb picks up every campaign database
# (v1 matrix + ablations, v2/v3, VTC, fairness, iso-cost, chaos, the five
# Wave-5 reruns, scale32/64, concurrency, forecasters, realism, phase7);
# restoring these into eval/results/ is exactly what unlocks the "archive"
# tier of docs/REPRODUCE.md. The csv/csv.gz exports and security data are
# also in git, but the deposit is self-contained by design.
INCLUDE = [
    "results/*.duckdb",
    "results/*.csv",
    "results/*.csv.gz",
    "results/DAILY.md",
    "results/security",
    "results/figures",
    # Live-run evidence directories are NOT listed here. They are derived
    # below (see `evidence_dirs`) from what the analysis scripts actually
    # read, because a hand-kept list drifted twice: the 2026-08-30 bundle had
    # none of them, and the 2026-08-31 one -- built after that was fixed --
    # still lacked five, including the trees behind RESULTS_TRACE_LIVE.md and
    # RESULTS_MULTINODE.md, the records that close W4 and W7. A list that is
    # maintained by hand next to a list of records maintained by hand will
    # drift again; a list read out of the scripts cannot.
    "experiments",
    "baselines/tuned.yaml",
    "baselines/TUNING.md",
    "baselines/grids",
    "infra/terraform",
    "SMOKE_BUGS.md",
]

# Paths relative to the repository root, not eval/. The scored records and the
# pre-registrations live in research/analysis/, one level ABOVE EVAL_DIR, so no
# eval-relative glob can reach them -- the 2026-08-31 bundle carried 326 files
# and zero of either. Without them the deposit cannot deliver its own
# description: reproduce.py establishes a record by re-deriving it and diffing
# against the committed copy, so a reviewer who restores only the deposit has
# nothing to diff against, and the pre-registrations that make every PASS/FAIL
# auditable are absent entirely. Same class of omission as the live-evidence
# one above, one directory level up.
REPO_INCLUDE = [
    # Every record is derived from scripts/reproduce.py below (`gated_records`)
    # rather than globbed. The 2026-08-31 bundle used "RESULTS_*.md", which
    # does not match RESULTS.md -- the headline record -- nor ADVANCED.md,
    # FAIRNESS_V2.md, PHASE7_ORDINAL.md, RECORDS_INDEX.md, CELLS_SHIPPED.md or
    # any older record without the prefix. Eight gated records were absent
    # from a deposit whose description promises every one. Session 43 found
    # and fixed the identical glob in build_pages.py; this file had it too.
    "research/analysis/PREREG_*.md",
    "docs/REPRODUCE.md",
    # RESULTS_SEPARATION_MT_V3.md reads its expensive rows from this artifact
    # (the 268,435,456-vector walk is ~2 h and cannot live in the gate). Ship it
    # or the deposit carries a record nobody can regenerate -- the same defect
    # this INCLUDE list was extended to fix, one level down.
    "research/analysis/separation_mt_v3_walk.json",
]

# Anything matching these is a mock or a probe: fixed sleeps that the tier
# server documents as never entering a record, kept in the tree under NOT
# EVIDENCE labels because they are the raw proof of a mechanism. They must
# not travel in a deposit beside real evidence, which is exactly the
# confusion session 45 had to undo once already inside the repository.
NOT_EVIDENCE = ("PROBE_MOCK", "REHEARSAL_MOCK", "_probe_evidence", "_PROBE_")

_EVIDENCE_TOKEN = re.compile(r"[a-z0-9_]+_evidence[a-z0-9_]*|wp14_attempt[0-9]+")


def gated_records() -> list[str]:
    """Every record scripts/reproduce.py knows about: the ones it regenerates
    and the ones it lists as UNGATED with a reason. Read from the script, so
    the deposit and the gate cannot disagree about what a record is."""
    path = REPO_DIR / "scripts" / "reproduce.py"
    spec = importlib.util.spec_from_file_location("polyforge_reproduce", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    names = {name for name, _ in module.RECORDS}
    names |= {record for _, _, record in module.CAMPAIGN_RECORDS}
    names |= set(module.UNGATED)
    return sorted(names)


def evidence_dirs() -> list[str]:
    """Every results/ directory an analysis script or the gate reads, that
    exists on disk and is not a mock. Derived, not listed."""
    sources = list((REPO_DIR / "research" / "analysis").glob("analysis_*.py"))
    sources.append(REPO_DIR / "scripts" / "reproduce.py")
    tokens: set[str] = set()
    for src in sources:
        tokens |= set(_EVIDENCE_TOKEN.findall(src.read_text(encoding="utf-8")))
    found = sorted(t for t in tokens
                   if (EVAL_DIR / "results" / t).is_dir()
                   and not any(m in t for m in NOT_EVIDENCE))
    return [f"results/{t}" for t in found]


def is_not_evidence(path: Path) -> bool:
    return any(m in path.name or m in str(path) for m in NOT_EVIDENCE)

DEPOSIT_METADATA = {
    "metadata": {
        "title": "PolyForge evaluation artifact: pre-registered controller-"
                 "comparison campaigns (harness, baselines, raw results, "
                 "live-run data, IaC)",
        "upload_type": "dataset",
        "description": (
            "Raw results and full provenance for the PolyForge multi-tenant "
            "serving-controller evaluation: the YAML-driven harness inputs, "
            "tuned baseline parameters with their grid-search evidence, every "
            "pre-registered campaign's raw DuckDB result database (v1 matrix "
            "and ablations, v2/v3, VTC and fairness slices, iso-cost, chaos, "
            "the Wave-5 structural-form reruns, tenant-scale, concurrency, "
            "phase-7 live reference), the live-campaign and trace-replay "
            "CSVs, the security study data, and the Terraform IaC for the "
            "experiment cluster. It also carries every frozen "
            "pre-registration and every scored analysis record "
            "(research/analysis/PREREG_*.md and RESULTS_*.md), so each "
            "PASS/FAIL verdict can be audited against the hypothesis that was "
            "committed before its campaign ran. Restoring the DuckDB files "
            "into eval/results/ of the PolyForge repository enables the "
            "archive tier of docs/REPRODUCE.md (included): scripts/reproduce.py "
            "then re-derives every published record and diffs it against the "
            "committed copy in this deposit, byte for byte. See eval/README.md "
            "for the schema and replay instructions."
        ),
        "creators": None,  # filled from creators() at build time
        "keywords": ["kubernetes", "autoscaling", "multi-tenancy", "LLM serving",
                     "semantic cache", "MPC", "reproducibility"],
        "license": "cc-by-4.0",
        "publication_date": str(date.today()),
    }
}


# Zenodo wants each creator as "Family, Given", optionally with an affiliation
# and an ORCID. The names are not in the repository -- the thesis still has
# placeholders for author and supervisor -- and results/zenodo/ is gitignored,
# so a name typed into deposit.json is lost on the next rebuild. A rebuild is
# due the moment B1 lands (its record and evidence belong in the deposit), so
# the names live in a tracked file and are read at build time, once.
CREATORS_FILE = EVAL_DIR / "zenodo_creators.json"
PLACEHOLDER_CREATORS = [{"name": "PolyForge author"}]
CREATOR_FIELDS = {"name", "affiliation", "orcid"}
_ORCID = re.compile(r"^[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9X]$")


def creators(path: Path = CREATORS_FILE) -> list[dict]:
    """The deposit's creators from the tracked file. A malformed file refuses
    the build; a missing one yields the placeholder, so a bundle can be built
    and inspected before the names are final but never published with it
    unnoticed (main() prints the warning)."""
    if not path.exists():
        return PLACEHOLDER_CREATORS
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"{path}: not valid JSON ({e})") from e
    if not isinstance(data, list) or not data:
        raise SystemExit(f"{path}: expected a non-empty JSON list of creators")
    for i, c in enumerate(data):
        family, _, given = str(c.get("name", "")).partition(",") if isinstance(c, dict) else ("", "", "")
        if not family.strip() or not given.strip():
            raise SystemExit(f"{path}: creator {i} needs 'name': 'Family, Given'")
        unknown = set(c) - CREATOR_FIELDS
        if unknown:
            raise SystemExit(f"{path}: creator {i} has unknown field(s) {sorted(unknown)}")
        orcid = c.get("orcid")
        if orcid is not None and not _ORCID.match(str(orcid)):
            raise SystemExit(f"{path}: creator {i} orcid {orcid!r} is not 0000-0000-0000-000X")
    return data


def deposit_metadata(creator_list: list[dict]) -> dict:
    """DEPOSIT_METADATA with the creators filled in; a new dict, the template
    is never written to."""
    return {"metadata": {**DEPOSIT_METADATA["metadata"], "creators": creator_list}}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect(root: Path, includes: list[str]) -> tuple[list[Path], list[str]]:
    """Resolve an include list against one root. Returns (files, missing globs)."""
    files: list[Path] = []
    missing: list[str] = []
    for rel in includes:
        if "*" in rel or "?" in rel:
            matches = [p for p in sorted(root.glob(rel)) if p.is_file()]
            if matches:
                files.extend(matches)
            else:
                missing.append(rel)
            continue
        path = root / rel
        if path.is_dir():
            files.extend(p for p in sorted(path.rglob("*")) if p.is_file()
                         and ".terraform" not in p.parts and not p.name.endswith(".tfstate"))
        elif path.exists():
            files.append(path)
        else:
            missing.append(rel)
    return files, missing


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="results/zenodo")
    args = ap.parse_args()

    out_dir = EVAL_DIR / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    # A malformed creators file refuses the build here, before the 48 MB zip
    # is written, not after it.
    creator_list = creators()

    eval_files, missing = collect(EVAL_DIR, INCLUDE + evidence_dirs())

    # Records live in research/analysis/ except the live security record,
    # which sits in eval/results/security/ and is already collected above.
    # Resolve each name to where it is; an unresolvable one refuses the build.
    records: list[str] = []
    eval_set = set(eval_files)
    for name in gated_records():
        in_analysis = REPO_DIR / "research" / "analysis" / name
        in_results = EVAL_DIR / "results" / "security" / name
        if in_analysis.exists():
            records.append(f"research/analysis/{name}")
        elif in_results.exists() and in_results in eval_set:
            continue
        else:
            missing.append(f"gated record {name} (not in research/analysis/ "
                           "nor already collected from results/security/)")
    repo_files, repo_missing = collect(REPO_DIR, REPO_INCLUDE + records)
    missing.extend(repo_missing)

    swept = [p for p in eval_files if is_not_evidence(p)]
    eval_files = [p for p in eval_files if not is_not_evidence(p)]
    if swept:
        print(f"excluded {len(swept)} mock/probe file(s) that are not evidence:")
        for p in swept:
            print("  " + str(p.relative_to(EVAL_DIR)).replace("\\", "/"))

    # Archive names stay relative to the root each group was collected from, so
    # eval/ files keep the results/... layout docs/REPRODUCE.md tells the
    # reviewer to restore, and repo files carry their repo-relative path.
    entries = [(p, str(p.relative_to(EVAL_DIR)).replace("\\", "/")) for p in eval_files]
    entries += [(p, str(p.relative_to(REPO_DIR)).replace("\\", "/")) for p in repo_files]

    if missing:
        print("refusing to build an incomplete archive; missing:")
        for m in missing:
            print("  " + m)
        raise SystemExit(1)

    manifest = {arc: sha256(p) for p, arc in entries}
    manifest_path = out_dir / "MANIFEST.sha256.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    bundle = out_dir / f"polyforge-eval-artifact-{date.today()}.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as z:
        for p, arc in entries:
            z.write(p, arcname=arc)
        z.write(manifest_path, arcname="MANIFEST.sha256.json")

    (out_dir / "deposit.json").write_text(
        json.dumps(deposit_metadata(creator_list), indent=2), encoding="utf-8"
    )

    print(f"bundle:   {bundle}  ({bundle.stat().st_size / 1e6:.1f} MB, {len(entries)} files)")
    print(f"manifest: {manifest_path}")
    print(f"metadata: {out_dir / 'deposit.json'}")
    if creator_list is PLACEHOLDER_CREATORS:
        print(f"creators: PLACEHOLDER -- write {CREATORS_FILE.relative_to(REPO_DIR).as_posix()} "
              "and rebuild before upload")
    else:
        print(f"creators: {len(creator_list)} from {CREATORS_FILE.relative_to(REPO_DIR).as_posix()}")
    print("next (human): create Zenodo deposit, attach the zip, paste deposit.json "
          "metadata, publish -> DOI for paper §Reproducibility")


if __name__ == "__main__":
    main()
