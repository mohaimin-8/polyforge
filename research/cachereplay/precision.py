"""WP6 step 5: is a cache hit correct? Two measures of the wrong-hit rate per
threshold tau (programme 2).

1. Quora Question Pairs (GLUE `qqp`, labelled train + validation pairs): the
   cosine of every labelled pair under the cache's own encoder and key text,
   the precision-recall curve of the rule "hit if cosine >= tau", and the
   wrong-hit rate 1 - precision(tau) with a Wilson 95% interval.
2. LMSYS response agreement: the proxy already measured in
   research/analysis/CACHE_PRECISION.md (read, not re-run): a hit counts
   correct when the served and the actual response embeddings agree at
   cosine >= 0.70.

    python precision.py --preset smoke     # 500 validation pairs (<= 1,000 questions)
    python precision.py --preset full      # every labelled pair (downloads ~37 MB)

The QQP parquet files are downloaded into data/qqp/ (git-ignored) and treated
as untrusted data: size-capped, read with pyarrow only, schema-checked.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from streams import DATA, ENCODER, Encoder, key_text

HERE = Path(__file__).resolve().parent
QQP_URL = "https://huggingface.co/datasets/nyu-mll/glue/resolve/main/qqp/{split}-00000-of-00001.parquet"
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
CACHE_PRECISION_MD = HERE.parents[0] / "analysis" / "CACHE_PRECISION.md"
TAUS = (0.75, 0.80, 0.85, 0.90, 0.95)
CURVE_GRID = tuple(round(0.50 + 0.01 * i, 2) for i in range(51))
PRESETS = {"smoke": {"splits": ("validation",), "limit_pairs": 500, "threads": 2},
           "full": {"splits": ("train", "validation"), "limit_pairs": None, "threads": None}}


def download(split: str, dest_dir: Path = DATA / "qqp") -> Path:
    """Fetch one GLUE qqp split once; refuse oversize or non-parquet payloads."""
    dest = dest_dir / f"{split}.parquet"
    if dest.exists():
        return dest
    dest_dir.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(QQP_URL.format(split=split), headers={"User-Agent": "research-script"})
    part = dest.with_suffix(".part")
    with urllib.request.urlopen(req, timeout=120) as resp, part.open("wb") as out:
        total = 0
        while chunk := resp.read(1 << 20):
            total += len(chunk)
            if total > MAX_DOWNLOAD_BYTES:
                raise SystemExit(f"{split}: download exceeds {MAX_DOWNLOAD_BYTES} bytes; refused")
            out.write(chunk)
    if part.read_bytes()[:4] != b"PAR1":
        part.unlink()
        raise SystemExit(f"{split}: not a parquet file; refused")
    part.replace(dest)
    return dest


def load_pairs(path: Path, limit: int | None) -> pd.DataFrame:
    """Labelled pairs, schema-checked: two non-empty strings and a 0/1 label."""
    table = pq.read_table(path, columns=["question1", "question2", "label"])
    df = table.to_pandas()
    if limit is not None:
        df = df.head(limit)
    bad = ~df["label"].isin([0, 1]) | df["question1"].isna() | df["question2"].isna()
    if bad.any():
        raise SystemExit(f"{path.name}: {int(bad.sum())} rows without a 0/1 label or a question")
    return df.reset_index(drop=True)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pair_similarity(df: pd.DataFrame, encoder: Encoder, key_format: str) -> np.ndarray:
    """Cosine of each pair, embedding every distinct question once."""
    questions = pd.unique(pd.concat([df.question1, df.question2], ignore_index=True))
    vecs = encoder.encode([key_text(q, key_format) for q in questions]).astype(np.float64)
    row = {q: i for i, q in enumerate(questions)}
    a = vecs[df.question1.map(row).to_numpy()]
    b = vecs[df.question2.map(row).to_numpy()]
    return np.einsum("ij,ij->i", a, b)


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return float("nan"), float("nan")
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return centre - half, centre + half


def pr_at(sims: np.ndarray, labels: np.ndarray, tau: float) -> dict:
    hit = sims >= tau
    n_hit, tp, pos = int(hit.sum()), int((hit & (labels == 1)).sum()), int((labels == 1).sum())
    precision = tp / n_hit if n_hit else float("nan")
    lo, hi = wilson(n_hit - tp, n_hit)
    return {"tau": tau, "pairs_at_or_above": n_hit, "precision": precision,
            "recall": tp / pos if pos else float("nan"),
            "wrong_hit_rate": 1 - precision if n_hit else float("nan"), "wrong_hit_ci95": [lo, hi]}


def average_precision(sims: np.ndarray, labels: np.ndarray) -> float:
    order = np.argsort(-sims, kind="stable")
    y = labels[order] == 1
    if not y.any():
        return float("nan")
    precision_at_k = np.cumsum(y) / np.arange(1, len(y) + 1)
    return float(precision_at_k[y].mean())


def lmsys_proxy(path: Path = CACHE_PRECISION_MD) -> dict:
    """The published response-agreement table: tau -> hit rate, precision at
    rho 0.60/0.70/0.80, incorrect hits per 1,000 queries."""
    text = path.read_text(encoding="utf-8")
    rows = re.findall(r"^\| (0\.\d\d) \| (0\.\d+) \| (0\.\d+) \| (0\.\d+) \| (0\.\d+) \| ([\d.]+) \|$", text, re.M)
    if not rows:
        raise SystemExit(f"no table rows parsed from {path}")
    out = {}
    for tau, rate, p60, p70, p80, bad in rows:
        out[tau] = {"hit_rate": float(rate), "precision_rho_0.60": float(p60), "precision_rho_0.70": float(p70),
                    "precision_rho_0.80": float(p80), "wrong_hit_rate": round(1 - float(p70), 3),
                    "incorrect_per_1k": float(bad)}
    first = re.search(r"First-turn-only pairs[^:]*: precision (0\.\d+) over (\d+) hits", text)
    return {"by_tau": out, "first_turn_precision_at_0.85": float(first.group(1)) if first else None,
            "source": "research/analysis/CACHE_PRECISION.md",
            "protocol": "100k reservoir (prompt, response) pairs, all user turns, raw text, insert first half, "
                        "query second half, unbounded cache; correct if response cosine >= 0.70"}


def plot_curve(curve: pd.DataFrame, marks: list[dict], path: Path) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    fig, ax = plt.subplots(figsize=(4.6, 3.6), dpi=150)
    ax.plot(curve.recall, curve.precision, color="#3b6ea5", lw=1.6)
    for m in marks:
        ax.plot(m["recall"], m["precision"], "o", color="#c0504d", ms=4)
        ax.annotate(f"{m['tau']:.2f}", (m["recall"], m["precision"]), textcoords="offset points",
                    xytext=(4, 4), fontsize=7)
    ax.set(xlabel="recall (duplicates found)", ylabel="precision (hits that are duplicates)",
           xlim=(0, 1), ylim=(0, 1), title="QQP: threshold rule cosine >= tau")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preset", choices=sorted(PRESETS), default="smoke")
    ap.add_argument("--key-format", default="gateway", choices=("gateway", "raw"))
    ap.add_argument("--threads", type=int)
    ap.add_argument("--out", help="output directory (default out/<preset>)")
    args = ap.parse_args()
    cfg = PRESETS[args.preset]
    out = Path(args.out) if args.out else HERE / "out" / args.preset
    out.mkdir(parents=True, exist_ok=True)
    files = {s: download(s) for s in cfg["splits"]}
    df = pd.concat([load_pairs(p, cfg["limit_pairs"]) for p in files.values()], ignore_index=True)
    t0 = time.perf_counter()
    sims = pair_similarity(df, Encoder(threads=args.threads or cfg["threads"]), args.key_format)
    seconds = time.perf_counter() - t0
    labels = df.label.to_numpy()
    curve = pd.DataFrame([pr_at(sims, labels, t) for t in CURVE_GRID])
    curve.drop(columns=["wrong_hit_ci95"]).to_csv(out / "qqp_pr_curve.csv", index=False)
    marks = [pr_at(sims, labels, t) for t in TAUS]
    plotted = plot_curve(curve, marks, out / "qqp_pr_curve.png")
    questions = int(pd.unique(pd.concat([df.question1, df.question2])).size)
    result = {
        "qqp": {"splits": list(cfg["splits"]), "pairs": len(df), "duplicates": int((labels == 1).sum()),
                "questions_embedded": questions, "embed_seconds": seconds, "encoder": ENCODER,
                "key_format": args.key_format, "average_precision": average_precision(sims, labels),
                "files": {s: {"sha256": sha256(p), "bytes": p.stat().st_size} for s, p in files.items()},
                "plot": "qqp_pr_curve.png" if plotted else None},
        "qqp_wrong_hit_rate": {f"{m['tau']:.2f}": m for m in marks},
        "lmsys_wrong_hit_rate": lmsys_proxy(),
        "notes": ["QQP pairs were sampled to be topically related (about 37% are duplicates), so its "
                  "precision is not the precision of a cache's hits; it bounds how often a pair at a given "
                  "cosine is a true paraphrase.",
                  "The LMSYS proxy counts a hit wrong when responses disagree, including stylistic "
                  "disagreement between models; it is a pessimistic bound (CACHE_PRECISION.md)."]}
    (out / "precision.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    from report import precision_markdown  # noqa: PLC0415
    (out / "PRECISION.md").write_text(precision_markdown(result), encoding="utf-8")
    print(f"wrote {out / 'precision.json'}: {len(df)} pairs, AP {result['qqp']['average_precision']:.3f}, "
          f"embed {seconds:.1f}s for {questions} questions")


if __name__ == "__main__":
    main()
