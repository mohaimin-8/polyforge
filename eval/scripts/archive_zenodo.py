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
sys.path.insert(0, str(EVAL_DIR))

INCLUDE = [
    "results/raw_sim.duckdb",
    "results/ablations.duckdb",
    "results/smoke.duckdb",
    "results/DAILY.md",
    "experiments/smoke.yaml",
    "experiments/full.yaml",
    "experiments/ablations.yaml",
    "baselines/tuned.yaml",
    "baselines/TUNING.md",
    "baselines/grids",
    "infra/terraform",
    "SMOKE_BUGS.md",
]

DEPOSIT_METADATA = {
    "metadata": {
        "title": "PolyForge evaluation artifact: 1,800-run controller comparison "
                 "+ ablations (harness, baselines, raw results, IaC)",
        "upload_type": "dataset",
        "description": (
            "Raw results and full provenance for the PolyForge multi-tenant "
            "serving-controller evaluation: the YAML-driven harness inputs, "
            "tuned baseline parameters with their grid-search evidence, raw "
            "DuckDB result databases (full matrix, ablations, smoke), and the "
            "Terraform IaC for the experiment cluster. See eval/README.md in "
            "the PolyForge repository for the schema and replay instructions."
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="results/zenodo")
    args = ap.parse_args()

    out_dir = EVAL_DIR / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    files: list[Path] = []
    missing: list[str] = []
    for rel in INCLUDE:
        path = EVAL_DIR / rel
        if path.is_dir():
            files.extend(p for p in sorted(path.rglob("*")) if p.is_file()
                         and ".terraform" not in p.parts and not p.name.endswith(".tfstate"))
        elif path.exists():
            files.append(path)
        else:
            missing.append(rel)

    if missing:
        print("refusing to build an incomplete archive; missing:")
        for m in missing:
            print("  " + m)
        raise SystemExit(1)

    manifest = {str(p.relative_to(EVAL_DIR)).replace("\\", "/"): sha256(p) for p in files}
    manifest_path = out_dir / "MANIFEST.sha256.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    bundle = out_dir / f"polyforge-eval-artifact-{date.today()}.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, arcname=str(p.relative_to(EVAL_DIR)).replace("\\", "/"))
        z.write(manifest_path, arcname="MANIFEST.sha256.json")

    (out_dir / "deposit.json").write_text(
        json.dumps(DEPOSIT_METADATA, indent=2), encoding="utf-8"
    )

    print(f"bundle:   {bundle}  ({bundle.stat().st_size / 1e6:.1f} MB, {len(files)} files)")
    print(f"manifest: {manifest_path}")
    print(f"metadata: {out_dir / 'deposit.json'}")
    print("next (human): create Zenodo deposit, attach the zip, paste deposit.json "
          "metadata, fill in creators, publish -> DOI for paper §Reproducibility")


if __name__ == "__main__":
    main()
