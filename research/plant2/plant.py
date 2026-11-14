"""Plant 2's two modes as simulator overrides, and its constants table
(programme 2, WP2).

Every constant has a status:
  measured   read from a measurement file of this project
  fitted     a parameter fitted to such a file (`fits.py`), with an interval
  derived    computed from measured quantities by a stated rule
  structural a property of the mode's architecture, not a number to measure
  price      a public price, with its source and retrieval date
  swept      no measurement exists; WP5 varies it over the stated range
and a range for WP5. The rule for a measured or fitted constant without an
interval is +/-50% of its value (a choice, stated, not a measurement).

Mode A (API-backed gateway): the replicas are gateway pods that do not
congest in the operating range (the live fit); AI requests go to external
endpoints, priced per request, so the replica pool and the tier choice share
no capacity. Mode B (self-hosted pools): a replica is a model server; AI
cache misses load the tenant's pool at the chosen tier's service cost, and
money is replica-hours, not per-request prices. Mode B is defined for AI
traffic: CRUD requests put no work on the model servers (wu_crud_scale 0),
and a CRUD request present in a mode-B run is scored with the pool's
congestion, so mode-B workloads are AI-only.

Both modes use the dollar admission gate and the attainment SLO metric
(model.py, "plant 2"). Not yet in the plant: the Ramani-Tantawi queue as the
congestion law (FITS.md compares it with the power form; the simulator still
uses the power form with the measured exponent).

    python plant.py     # writes CONSTANTS.md from the data files
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
CAL = HERE.parents[0] / "calibration"
REPO = HERE.parents[1]


@dataclass(frozen=True)
class Constant:
    name: str            # model_form / economy key, or a descriptive name
    value: float | None  # None: no default, the caller must set it
    unit: str
    status: str          # measured | fitted | derived | structural | swept
    source: str
    sweep: tuple | None  # (low, high) used by WP5; None = fixed
    modes: str           # "A", "B" or "A,B"
    note: str = ""


def _half(v: float) -> tuple:
    return (0.5 * v, 1.5 * v)


def _routerbench_tiers() -> list[Constant]:
    """Mode A's per-request prices and answer quality: the three RouterBench
    models chosen by `research/tierquality/tiers.py`'s cost rule."""
    rb = json.loads((HERE.parents[0] / "tierquality" / "tier_summary.json").read_text(encoding="utf-8"))
    by = {k: {r["model"]: r for r in v} for k, v in rb["tables"].items()}
    out = []
    for tier, model_name in rb["tiers"].items():
        z, f = by["0-shot"][model_name], by["5-shot"][model_name]
        src = f"research/tierquality/TIERS.md: RouterBench, {model_name}"
        out.append(Constant(f"tier_cost_{tier}", float(f"{z['mean_cost_usd']:.4g}"), "USD/request", "price",
                            src + ", mean cost per prompt at the March 2024 prices",
                            (float(f"{z['mean_cost_usd']:.4g}"), float(f"{f['mean_cost_usd']:.4g}")), "A",
                            "range: 0-shot to 5-shot prompts"))
        lo, hi = sorted((z["quality"], f["quality"]))
        out.append(Constant(f"tier_quality_{tier}", round(z["quality"], 4), "share", "measured",
                            src + ", mean score (0-shot)", (round(lo, 4), round(hi, 4)), "A",
                            "range: 0-shot to 5-shot"))
    return out


def _cache_constants() -> list[Constant]:
    """The semantic cache's hit curve measured by WP6 (research/cachereplay): LMSYS
    first turns per tenant replayed through the gateway's own cache logic. Default:
    class all, the gateway's cost-aware eviction, tau 0.85; ranges over both eviction
    policies and every tau (0.75-0.95) of class all, so WP5 sweeps the threshold."""
    path = HERE.parents[0] / "cachereplay" / "out" / "full" / "plant2_cache.json"
    d = json.loads(path.read_text(encoding="utf-8"))
    p = d["plant2"]
    fits = [c["mb"] for policy in d["curves"]["all"].values() for c in policy.values()]
    src = "research/cachereplay/out/full (WP6: class all, cost-aware eviction, tau 0.85)"
    return [
        Constant("cache_hit_max", round(p["cache_hit_max"], 4), "", "fitted", src,
                 (round(min(f["cache_hit_max"] for f in fits), 3), round(max(f["cache_hit_max"] for f in fits), 3)),
                 "A,B", "interval {:.3f}-{:.3f}; range = every tau and both policies".format(*p["cache_hit_max_ci95"])),
        Constant("cache_half_mb", round(p["cache_half_mb"], 3), "MiB heap", "fitted", src,
                 (round(min(f["half"] for f in fits), 3), round(max(f["half"] for f in fits), 3)),
                 "A,B", "interval {:.3f}-{:.3f}; gateway heap per tenant, measured bytes per entry".format(
                     *p["cache_half_mb_ci95"])),
    ]


def _qwen_quality() -> dict:
    sys.path.insert(0, str(HERE.parents[0] / "jcac_sim"))
    import model

    return {t: model._DEFAULT_TIER_QUALITY[t] for t in ("small", "mid", "large")}


def constants() -> list[Constant]:
    fits = json.loads((HERE / "data" / "fits.json").read_text(encoding="utf-8"))
    live = json.loads((CAL / "live_plant_b1prime.json").read_text(encoding="utf-8"))
    batched = pd.read_csv(CAL / "tier_bench_batched.csv")
    single = pd.read_csv(CAL / "tier_bench_1gpu.csv").set_index("tier")
    t4 = pd.read_csv(CAL / "tier_bench_t4.csv").set_index("tier")

    def rps(tier: str, batch: int) -> float:
        row = batched[(batched.tier == tier) & (batched.batch == str(batch)) & (batched.new_tokens == 48)]
        return float(row.throughput_rps.iloc[0])

    power = fits["cpu"]["forms"]["power, L0 and mu measured"]
    tail = fits["cpu"]["tail"]
    power_new = fits["cpu_remeasured"]["forms"]["power, L0 fitted"]
    power_new_l0 = fits["cpu_remeasured"]["forms"]["power, L0 and mu measured"]
    tail_new = fits["cpu_remeasured"]["tail"]
    crud_scales = sorted(c["crud_base_scale"] for c in live["cells"].values())
    crud_median = 0.5 * (crud_scales[1] + crud_scales[2])
    cap_small_64, cap_mid_64 = rps("small", 64), rps("mid", 64)
    cap_small_32, cap_mid_32 = rps("small", 32), rps("mid", 32)
    wu_mid = cap_small_64 / cap_mid_64
    wu_large_hi = wu_mid * 7.0 / 3.0
    lat = live["tier_latency_ms"]
    cal = "research/calibration/"
    return [
        # ---- both modes
        Constant("congestion_exponent", round(power_new["params"]["a"], 4), "", "fitted",
                 "research/plant2/FITS.md: re-measured curve, power form with L0 fitted (lowest held-out error)",
                 (round(min(power_new_l0["ci95"]["a"][0], power["ci95"]["a"][0]), 3), 1.0), "A,B",
                 "runs disagree beyond their intervals (L0 measured: July {:.2f} [{:.2f}, {:.2f}], November "
                 "{:.2f} [{:.2f}, {:.2f}]); range spans both runs, both methods and the published 1.0".format(
                     power["params"]["a"], *power["ci95"]["a"], power_new_l0["params"]["a"], *power_new_l0["ci95"]["a"])),
        Constant("p95_tail_f0", round(tail_new["params"]["f0"], 4), "", "fitted",
                 "research/plant2/FITS.md: re-measured curve (flat: b's interval holds 0)",
                 (round(min(tail_new["ci95"]["f0"][0], tail["ci95"]["f0"][0]), 3),
                  round(max(tail_new["ci95"]["f0"][1], tail["ci95"]["f0"][1]), 3)), "A,B",
                 "range spans both runs' intervals"),
        Constant("p95_tail_b", 0.0, "", "fitted",
                 "research/plant2/FITS.md: November b = {:.2f} [{:.2f}, {:.2f}], July {:.2f} [{:.2f}, {:.2f}]".format(
                     tail_new["params"]["b"], *tail_new["ci95"]["b"], tail["params"]["b"], *tail["ci95"]["b"]),
                 (0.0, round(tail["ci95"]["b"][1], 3)), "A,B", "the rising tail did not replicate; the model requires b >= 0"),
        *_cache_constants(),
        Constant("cacheable_uniform", 1, "", "structural", "the LMSYS curve is over all prompts", None, "A,B"),
        Constant("mixture_p95", 1, "", "structural", "hit/miss mixture percentile", None, "A,B"),
        Constant("admission_gate", 1, "", "structural", "plan WP2: token bucket in dollars", None, "A,B"),
        Constant("attainment_slo", 1, "", "structural", "plan WP2: attainment is the SLO metric", None, "A,B"),
        # ---- mode A
        Constant("replica_capacity_wu", round(live["replica_capacity_wu"], 1), "wu/s", "measured",
                 cal + "live_plant_b1prime.json", (round(live["replica_capacity_wu"], 1),
                                                   round(5 * live["replica_capacity_wu"], 1)), "A",
                 "a lower bound: no live run reached congestion; swept upward only"),
        Constant("wu_ai_scale", 0.0, "", "structural", "AI requests never reach a gateway replica", None, "A"),
        Constant("crud_base_scale", round(crud_median, 6), "", "measured",
                 cal + "live_plant_b1prime.json (median of 4 cells)",
                 (round(crud_scales[0], 6), round(crud_scales[-1], 6)), "A", "range = the four live cells"),
        Constant("tier_latency_small", round(lat["small"], 1), "ms", "measured",
                 cal + "live_plant_b1prime.json (sequential probes)", _half(round(lat["small"], 1)), "A"),
        Constant("tier_latency_mid", round(lat["mid"], 1), "ms", "derived",
                 cal + "live_plant_b1prime.json: geometric mean of small and large, NOT measured",
                 (round(lat["small"], 1), round(lat["large"], 1)), "A"),
        Constant("tier_latency_large", round(lat["large"], 1), "ms", "measured",
                 cal + "live_plant_b1prime.json (sequential probes)", _half(round(lat["large"], 1)), "A"),
        *_routerbench_tiers(),
        # ---- mode B
        Constant("wu_ai_scale", 0.05, "", "structural",
                 "normalisation: one chat request = 1 wu = one small-tier request (published chat = 20 wu)",
                 None, "B", "embed and agent keep the published 5 : 20 : 40 ratio (an assumption)"),
        Constant("replica_capacity_wu", cap_small_64, "wu/s", "measured",
                 cal + "tier_bench_batched.csv (small, batch 64, 48 tokens, one T4)",
                 (cap_small_32, cap_small_64), "B", "range = batch limit 32 to 64"),
        Constant("wu_tier_mid", round(wu_mid, 4), "", "derived",
                 cal + "tier_bench_batched.csv: small / mid throughput at batch 64",
                 (round(cap_small_32 / cap_mid_32, 4), round(wu_mid, 4)), "B", "range = batch 32 to 64"),
        Constant("wu_tier_large", round(math.sqrt(wu_mid * wu_large_hi), 4), "", "swept",
                 "no batched measurement of large", (round(wu_mid, 4), round(wu_large_hi, 4)), "B",
                 "from mid's ratio to mid x 7B/3B parameters; default = geometric mean"),
        Constant("tier_latency_small", float(single.loc["small", "mean_ms"]), "ms", "measured",
                 cal + "tier_bench_1gpu.csv (single stream, 48 tokens)", None, "B"),
        Constant("tier_latency_mid", float(single.loc["mid", "mean_ms"]), "ms", "measured",
                 cal + "tier_bench_1gpu.csv (single stream, 48 tokens)", None, "B"),
        Constant("tier_latency_large", float(t4.loc["large", "mean_ms"]), "ms", "measured",
                 cal + "tier_bench_t4.csv (two T4s, sharded)", None, "B",
                 "small and mid run 25% faster on one card than sharded; large needs two"),
        Constant("wu_crud_scale", 0.0, "", "structural", "CRUD never reaches a model server", None, "B"),
        Constant("tier_cost_small", 0.0, "USD/request", "structural", "self-hosted: no per-request bill", None, "B"),
        Constant("tier_cost_mid", 0.0, "USD/request", "structural", "self-hosted: no per-request bill", None, "B"),
        Constant("tier_cost_large", 0.0, "USD/request", "structural", "self-hosted: no per-request bill", None, "B"),
        *[Constant(f"tier_quality_{t}", round(q, 4), "share", "published",
                   "Qwen2.5 model-card benchmark means (model.py TIER_BENCHMARKS)", None, "B",
                   "own measurement pending (WP7, mode B)")
          for t, q in _qwen_quality().items()],
        Constant("replica_cost_usd_hr", 0.526, "USD/hour", "price",
                 "AWS g4dn.xlarge (one T4, the measured GPU), us-east-1 on-demand; third-party price "
                 "aggregators DevZero, DoiT and Holori agree; retrieved 2026-11-12, AWS's own page not checked",
                 (0.2143, 0.526), "B", "range: lowest recent spot price (DoiT) to on-demand"),
    ]


ECONOMY = {"tier_cost_small", "tier_cost_mid", "tier_cost_large", "cache_hit_max", "cache_half_mb",
           "replica_cost_usd_hr", "tier_quality_small", "tier_quality_mid", "tier_quality_large"}


def _bundle(mode: str, overrides: dict) -> tuple[dict, dict]:
    """(model_form, economy) for one mode, defaults from the table."""
    table = {c.name: c for c in constants() if mode in c.modes.split(",")}
    unknown = set(overrides) - set(table)
    if unknown:
        raise ValueError(f"mode {mode} has no constants {sorted(unknown)}")
    values = {name: overrides.get(name, c.value) for name, c in table.items()}
    missing = sorted(name for name, v in values.items() if v is None)
    if missing:
        raise ValueError(f"mode {mode}: no default for {missing}; set them (swept factors)")
    form = {k: v for k, v in values.items() if k not in ECONOMY}
    econ = {k: v for k, v in values.items() if k in ECONOMY}
    return form, econ


def mode_a(**overrides) -> tuple[dict, dict]:
    return _bundle("A", overrides)


def mode_b(**overrides) -> tuple[dict, dict]:
    return _bundle("B", overrides)


def constants_md() -> str:
    L = ["# Plant 2 constants (programme 2, WP2)", "",
         "Generated by `research/plant2/plant.py` from the data files named in each row. Status: "
         "measured, fitted (interval in `FITS.md`), derived (rule stated), structural (a property of "
         "the mode), price (public, dated), swept (no measurement; WP5 varies it). A measured or fitted constant without an "
         "interval is swept over +/-50% of its value, a stated choice. Mode A: API-backed gateway; "
         "mode B: self-hosted pools of model servers.", "",
         "| key | modes | value | unit | status | WP5 range | source | note |",
         "|---|---|---|---|---|---|---|---|"]
    for c in constants():
        value = "set by caller" if c.value is None else f"{c.value:g}"
        sweep = "" if c.sweep is None else f"{c.sweep[0]:g} to {c.sweep[1]:g}"
        L.append(f"| `{c.name}` | {c.modes} | {value} | {c.unit} | {c.status} | {sweep} | {c.source} | {c.note} |")
    counts = pd.Series([c.status for c in constants()]).value_counts()
    L += ["", "Rows by status: " + ", ".join(f"{k} {v}" for k, v in counts.items()) + ".", "",
          "Not in the table because they are specification choices, varied by the specification curve "
          "(WP5), not plant constants: SLO targets and class factors, objective weights, the price of "
          "an unserved request.", ""]
    return "\n".join(L)


def main() -> int:
    (HERE / "CONSTANTS.md").write_text(constants_md(), encoding="utf-8")
    print(constants_md())
    return 0


if __name__ == "__main__":
    sys.exit(main())
