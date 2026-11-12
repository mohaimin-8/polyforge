"""Runtime estimate for `run_all.py --preset full`, from measurements on the
smoke data (programme 2, WP6).

Wall-clock rates measured while another campaign loads the machine are not
transferable, so every component is measured in process CPU time on a small
sample (single thread) and projected as CPU-seconds / (cores x efficiency):

    python estimate.py              # about 30-60 s of CPU; writes out/smoke/ESTIMATE.md

Measured here: encoder CPU-seconds per text for LMSYS first turns, QQP
questions and old-protocol user turns; replay CPU-microseconds per lookup per
policy; cost-aware victim scans at three cache sizes; single-thread float32
GEMM rate and argpartition cost (the neighbour index).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

from cachesim import NeighborIndex, replay_stream
from engine import CostAware, store_cost_usd
from streams import DATA, LMSYS_GLOB, doc_tiekeys, first_turns, load_stream

HERE = Path(__file__).resolve().parent
OUT = HERE / "out" / "smoke"
SAMPLE = 192
LMSYS_ROWS = 1_000_000
QQP_QUESTIONS = 537_000       # distinct questions in GLUE qqp train + validation (approximate)
OLD_SAMPLE = 200_000
FULL = {"stream_length": 32768, "capacities": [64 * 2 ** i for i in range(9)] + [0], "taus": 5,
        "policies": ("costaware", "lru"), "classes": ("all", "conversation", "code"), "workers": 6}


def cpu(fn, *args, min_s: float = 0.0):
    """CPU seconds per call of fn, repeating it until at least min_s of CPU
    time has accumulated (Windows reports process time in ~15.6 ms ticks)."""
    t0, calls = time.process_time(), 0
    while True:
        out = fn(*args)
        calls += 1
        spent = time.process_time() - t0
        if spent >= min_s:
            return spent / calls, out


def encoder_cpu_per_text() -> dict:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(1)
    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2", device="cpu")
    _, lmsys = first_turns(sorted(Path(LMSYS_GLOB).parent.glob("*.parquet"))[0].as_posix(), "gateway", 2000)
    rng = np.random.default_rng(0)

    def pick(xs: list[str]) -> list[str]:
        return [xs[i] for i in rng.choice(len(xs), size=min(SAMPLE, len(xs)), replace=False)]

    qqp = pd.read_parquet(DATA / "qqp" / "validation.parquet", columns=["question1"]).question1.head(2000)
    old_texts = [t[len("user: "):-1] for t in lmsys]  # raw prompts stand in for raw user turns
    out = {}
    for name, texts in (("lmsys_first_turn", pick(lmsys)), ("qqp_question", pick(list(qqp))),
                        ("old_raw_turn", pick(old_texts))):
        model.encode(texts[:8])  # warm-up
        seconds, _ = cpu(lambda t: model.encode(t, batch_size=128, show_progress_bar=False), texts)
        tokens = [min(256, len(model.tokenizer(t)["input_ids"])) for t in texts]
        out[name] = {"cpu_s_per_text": seconds / len(texts), "mean_tokens": float(np.mean(tokens))}
    return out


def replay_cpu() -> dict:
    st = load_stream("gateway", 2000, lambda: None)
    vec, m = np.ascontiguousarray(st.vectors), st.meta
    comp, keyb, docs = m.completion_bytes.to_numpy(), m.key_bytes.to_numpy(), tuple(m.doc)
    costs = np.array([store_cost_usd(int(b)) for b in comp])
    nb = NeighborIndex(vec, k=64)
    out = {}
    for policy in ("costaware", "lru"):
        seconds, _ = cpu(lambda: replay_stream(vec, costs, comp, keyb, threshold=0.85, capacity=256,
                                               policy=policy, dt=1.2, warm_start=1000, docs=docs,
                                               tiekeys=doc_tiekeys(docs), neighbors=nb), min_s=0.5)
        out[f"{policy}_us_per_lookup"] = seconds / len(vec) * 1e6
    rng = np.random.default_rng(1)
    for n in (1024, 4096, 16384):
        p = CostAware(size=n)
        for i in range(n):
            p.on_store(i, 10 ** rng.uniform(-6, -3), rng.random(), i, float(i))
        seconds, _ = cpu(lambda: p.victim(float(n) + 10), min_s=0.25)
        out[f"victim_us_n{n}"] = seconds * 1e6
    return out


def kernel_cpu() -> dict:
    """CPU cost of the neighbour index's two kernels (process time sums all
    threads, so the rate is per CPU-second whatever the thread count)."""
    rng = np.random.default_rng(2)
    a = rng.normal(size=(512, 384)).astype(np.float32)
    b = rng.normal(size=(8192, 384)).astype(np.float32)
    seconds, s = cpu(lambda: a @ b.T, min_s=0.5)
    part, _ = cpu(lambda: np.argpartition(s, 8192 - 64, axis=1), min_s=0.5)
    return {"gemm_gflop_per_cpu_s": 2 * 512 * 8192 * 384 / seconds / 1e9,
            "argpartition_ns_per_element": part / s.size * 1e9}


def old_loader_cpu() -> dict:
    """The old script's reservoir loader on the 1,000-conversation slice."""
    from oldcheck import old_module, slice_source

    old = old_module()
    path = slice_source(1000)
    seconds, prompts = cpu(lambda: old.load_lmsys_prompts(path, 600, 42), min_s=0.5)
    return {"cpu_s_per_conversation": seconds / 1000}


def _victim_extra_us(rep: dict, n: int) -> float:
    xs = np.array([256, 1024, 4096, 16384], float)
    ys = np.array([rep["victim_us_n1024"] * 0.9, rep["victim_us_n1024"], rep["victim_us_n4096"],
                   rep["victim_us_n16384"]])
    return max(0.0, float(np.interp(n, xs, ys)) - ys[0])


def replay_cpu_hours(rep: dict, ker: dict, tenants: dict) -> dict:
    L = FULL["stream_length"]
    half_pairs = L * L / 2
    nb = half_pairs * 768 / (ker["gemm_gflop_per_cpu_s"] * 1e9) + 2 * half_pairs * ker["argpartition_ns_per_element"] * 1e-9
    per_tenant = nb
    for policy in FULL["policies"]:
        lookup = rep[f"{policy}_us_per_lookup"] * 1e-6
        for cap in FULL["capacities"]:
            evictions = max(0, L - cap) if cap else 0          # upper bound: every lookup misses
            extra = _victim_extra_us(rep, cap) * 1e-6 if policy == "costaware" and cap else 0.0
            per_tenant += FULL["taus"] * (L * lookup + evictions * extra)
    n_tenants = sum(tenants.values())
    return {"tenants": tenants, "cpu_s_per_tenant": per_tenant, "neighbour_index_cpu_s_per_tenant": nb,
            "cpu_hours": n_tenants * per_tenant / 3600}


def project(enc: dict, rep: dict, ker: dict, old: dict, parallel: float) -> dict:
    meta = pd.read_parquet(next((DATA / "vectors" / "gateway").glob("*first2000.meta.parquet")))
    valid = len(meta) / (int(meta.row.max()) + 1)
    n_ft = LMSYS_ROWS * valid
    shares = {"all": 1.0, "code": float(meta.code.mean())}
    shares["conversation"] = 1.0 - shares["code"]
    tenants = {c: int(n_ft * shares[c] // FULL["stream_length"]) for c in FULL["classes"]}
    rows = {
        "embed LMSYS first turns": (n_ft * enc["lmsys_first_turn"]["cpu_s_per_text"] / 3600, parallel),
        "replay grid": (replay_cpu_hours(rep, ker, tenants)["cpu_hours"], min(parallel, FULL["workers"])),
        "embed QQP questions": (QQP_QUESTIONS * enc["qqp_question"]["cpu_s_per_text"] / 3600, parallel),
        "old check: loader": (LMSYS_ROWS * old["cpu_s_per_conversation"] / 3600, 1.0),
        "old check: embed 200,000 turns": (OLD_SAMPLE * enc["old_raw_turn"]["cpu_s_per_text"] / 3600, parallel),
        "old check: old NN + engine": (3 * (OLD_SAMPLE / 2) ** 2 * 768 / (ker["gemm_gflop_per_cpu_s"] * 1e9) / 3600
                                       + (OLD_SAMPLE / 2) ** 2 * ker["argpartition_ns_per_element"] * 1e-9 / 3600
                                       + 8 * OLD_SAMPLE * 40e-6 / 3600, parallel / 2),
        "fit and Go heap measurement": (5 / 60, 1.0),
    }
    table = {k: {"cpu_hours": c, "parallelism": p, "wall_hours": c / p} for k, (c, p) in rows.items()}
    return {"first_turns": n_ft, "tenants": tenants, "components": table,
            "total_wall_hours": sum(v["wall_hours"] for v in table.values())}


def markdown(r: dict) -> str:
    L = ["# Runtime estimate for `python run_all.py --preset full --workers 6`", "",
         f"Measured {time.strftime('%Y-%m-%d %H:%M')} in process CPU time on small samples (single thread), "
         f"projected with an effective parallelism of {r['parallel']} core-equivalents (this machine: "
         f"{r['machine']}). First turns: {r['first_turns']:,.0f}; tenants of {FULL['stream_length']}: "
         + ", ".join(f"{k} {v}" for k, v in r["tenants"].items()) + ".", "",
         "| stage | CPU hours | parallelism | wall hours |", "|---|---|---|---|"]
    for k, v in r["components"].items():
        L.append(f"| {k} | {v['cpu_hours']:.2f} | {v['parallelism']:.1f} | {v['wall_hours']:.2f} |")
    L += ["", f"**Total: about {r['total_wall_hours']:.1f} h of wall time on an otherwise idle machine** "
          f"(the replay grid bounds evictions as if every lookup missed, so it is an upper bound). "
          "Embedding is cached under data/, so re-running the replay or the fit skips it.", "",
          "Measurements:", "", "```", json.dumps(r["measured"], indent=1), "```"]
    return "\n".join(L) + "\n"


def main() -> None:
    enc = encoder_cpu_per_text()
    rep = replay_cpu()
    ker = kernel_cpu()
    old = old_loader_cpu()
    parallel = 4.5
    r = project(enc, rep, ker, old, parallel)
    r.update(parallel=parallel, machine="Intel i5-1135G7, 4 cores / 8 threads, 15.7 GB",
             measured={"encoder": enc, "replay": rep, "kernels": ker, "old_loader": old})
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "estimate.json").write_text(json.dumps(r, indent=1), encoding="utf-8")
    (OUT / "ESTIMATE.md").write_text(markdown(r), encoding="utf-8")
    print(f"total about {r['total_wall_hours']:.1f} h; " + "; ".join(
        f"{k} {v['wall_hours']:.2f} h" for k, v in r["components"].items()))


if __name__ == "__main__":
    main()
