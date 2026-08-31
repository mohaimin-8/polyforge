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

Usage (from eval/):  python scripts/archive_zenodo.py [--out results/zenodo]
"""

from __future__ import annotations

import argparse
import hashlib
import json
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
    # Live-run evidence directories. Without these the deposit CANNOT deliver
    # what its own description promises -- "reproduce.py then re-derives every
    # published record byte-identically" -- because the live records are built
    # from these trees, not from the DuckDB files. RESULTS_LIVE_SOAK_V2..V8 and
    # RESULTS_WAVE4_LIVE_PLANE all read *_evidence/, and `results/*.duckdb` is
    # a top-level glob that never descends into them. Found by checking the
    # 2026-08-30 bundle against the records it claims to support: none of the
    # attempt evidence was in it.
    "results/live_soak_evidence",
    "results/live_soak_attempt5_evidence",
    "results/live_soak_attempt6_evidence",
    "results/live_soak_attempt7_evidence",
    "results/live_soak_attempt8_evidence",
    "results/live_soak_attempt8b_evidence",
    "results/live_soak_attempt9_evidence",
    "results/live_soak_attempt10_evidence",
    "results/wave4_live_plane_evidence",
    "results/wp8b_substrate_evidence",
    "results/ladder_for_v3_evidence",
    "results/ladder_for_v45_evidence",
    "results/soak_stage_a_evidence",
    "results/soak_stage_b_evidence",
    "results/wp14_attempt2",
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
    "research/analysis/RESULTS_*.md",
    "research/analysis/PREREG_*.md",
    "docs/REPRODUCE.md",
    # RESULTS_SEPARATION_MT_V3.md reads its expensive rows from this artifact
    # (the 268,435,456-vector walk is ~2 h and cannot live in the gate). Ship it
    # or the deposit carries a record nobody can regenerate -- the same defect
    # this INCLUDE list was extended to fix, one level down.
    "research/analysis/separation_mt_v3_walk.json",
]

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
        "creators": [{"name": "PolyForge author"}],  # fill in before upload
        "keywords": ["kubernetes", "autoscaling", "multi-tenancy", "LLM serving",
                     "semantic cache", "MPC", "reproducibility"],
        "license": "cc-by-4.0",
        "publication_date": str(date.today()),
    }
}


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

    eval_files, missing = collect(EVAL_DIR, INCLUDE)
    repo_files, repo_missing = collect(REPO_DIR, REPO_INCLUDE)
    missing.extend(repo_missing)

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
        json.dumps(DEPOSIT_METADATA, indent=2), encoding="utf-8"
    )

    print(f"bundle:   {bundle}  ({bundle.stat().st_size / 1e6:.1f} MB, {len(entries)} files)")
    print(f"manifest: {manifest_path}")
    print(f"metadata: {out_dir / 'deposit.json'}")
    print("next (human): create Zenodo deposit, attach the zip, paste deposit.json "
          "metadata, fill in creators, publish -> DOI for paper §Reproducibility")


if __name__ == "__main__":
    main()
