"""WP14: the frozen scorer of the confirmatory campaign (the plan's Part D, as design.json fixes it).

confirm.py runs this once, on every shard's output (campaign.json names it):

    python wp14_score.py --master HEX --results DIR --out DIR     # reads DIR/shard-*/rows.csv
    python wp14_score.py --rehearsal --results DIR --out DIR

It first checks that the shards hold every job of the campaign exactly once,
each with its own seed (the job list is rebuilt from the master and the frozen
design). Then:

  H1  Theorem 1 on every logged decision of the dose part: holds when some
      decision was checked and none violates it. A count, not a test.
  H2  per new regime, the gain J(decoupled) - J(joint) and the predictor (the
      joint planner's mean of the logged quantity the design names), each a
      mean over the regime's design points; Spearman's rho of gain on
      predictor with its one-sided permutation p-value (H0: no positive
      association; permutations seeded from the master), and the calibration
      slope: the observed gains regressed on the gains that the WP4 pilot's
      least-squares line (gain on predictor over the pilot's regimes)
      predicts. Supported when the Holm-adjusted p is below alpha, rho is at
      least rank_correlation_min and the slope lies in slope_range.
  H3  per drawn workload, d = J(comparator) - J(joint) at the named plant and
      granularity; equivalence by two one-sided paired t-tests against
      +-margin x mean J(joint). Supported when the Holm-adjusted p is below alpha.
  H4  the same d; one-sided paired t-test that the joint planner is lower
      (mean d > 0). Supported when the Holm-adjusted p is below alpha.

Holm's correction runs over the design's `holm` list, its size the family's.
A failed run is missing data: its pair or design point is left out, and the
count is reported. Every arm against joint, per plant and granularity, is
reported beside the tests without a test, and so is whether every shard ran
the workloads the master seed draws (each shard draws them itself; a
difference is reported, never acted on). Writes score.json and SCORE.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import seeds  # noqa: E402
import wp14_campaign as campaign  # noqa: E402

PREDICTORS = ("index", "quantile_index", "loss", "costly")
REGIME = ["kappa", "width"]
POINT = ["kappa", "width", "workload", "tenant_mix"]


# --------------------------------------------------------------------------
# Reading and checking the shards
# --------------------------------------------------------------------------

def read_rows(results: Path) -> pd.DataFrame:
    files = sorted(results.glob("shard-*/rows.csv"), key=lambda p: int(p.parent.name.split("-", 1)[1]))
    if not files:
        raise SystemExit(f"no shard-*/rows.csv under {results}")
    rows = pd.concat([pd.read_csv(f, float_precision="round_trip", low_memory=False) for f in files],
                     ignore_index=True)
    rows["error"] = rows["error"].fillna("").astype(str)
    return rows


def check_coverage(rows: pd.DataFrame, jobs: list[dict]) -> None:
    """Every job exactly once, with the seed its identity gives."""
    want = {j["identity"]: j["seed"] for j in jobs}
    dup = sorted(set(rows.identity[rows.identity.duplicated()]))
    missing = sorted(set(want) - set(rows.identity))
    extra = sorted(set(rows.identity) - set(want))
    if dup or missing or extra:
        raise SystemExit(f"the shards do not hold the campaign once: {len(dup)} duplicated, {len(missing)} missing, "
                         f"{len(extra)} unknown (first: {(dup + missing + extra)[:3]})")
    wrong = [i for i, s in zip(rows.identity, rows.seed) if int(s) != want[i]]
    if wrong:
        raise SystemExit(f"{len(wrong)} run(s) carry another seed than their identity gives (first: {wrong[0]})")


def leaves(x) -> list:
    """A JSON value's keys and leaves in a fixed order, so two values of one shape line up."""
    if isinstance(x, dict):
        return [v for k in sorted(x) for v in (k, *leaves(x[k]))]
    if isinstance(x, list):
        return [v for item in x for v in leaves(item)]
    return [x]


def largest_difference(a, b) -> float:
    """The largest relative difference between the numbers of two JSON values of one shape;
    infinite when anything other than a number differs."""
    xs, ys = leaves(a), leaves(b)
    if len(xs) != len(ys):
        return math.inf
    worst = 0.0
    for x, y in zip(xs, ys):
        if x == y:
            continue
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (x, y)):
            return math.inf
        worst = max(worst, abs(x - y) / max(abs(x), abs(y)))
    return worst


def population_check(results: Path, drawn: list[dict]) -> dict:
    """Whether every shard ran the workloads the scorer draws from the master seed.

    Each shard draws the population itself, and the comparison part pairs runs across shards; numpy
    picks its SIMD code per processor, so shards on different processors could disagree in a float's
    last bits. Reported, never acted on: every run is scored as its shard ran it."""
    drawn = json.loads(json.dumps(drawn))
    digest = lambda w: hashlib.sha256(json.dumps(w, sort_keys=True).encode("utf-8")).hexdigest()  # noqa: E731
    shards = []
    for path in sorted(results.glob("shard-*/population.json"), key=lambda p: int(p.parent.name.split("-", 1)[1])):
        ran = json.loads(path.read_text(encoding="utf-8"))["workloads"]
        prov = path.parent / "provenance.json"
        cpu = json.loads(prov.read_text(encoding="utf-8")).get("cpu", "") if prov.is_file() else ""
        shards.append({"shard": int(path.parent.name.split("-", 1)[1]), "cpu": cpu, "sha256": digest(ran),
                       "largest_difference": largest_difference(drawn, ran)})
    differ = [s for s in shards if s["sha256"] != digest(drawn)]
    return {"workloads": len(drawn), "sha256": digest(drawn), "shards": len(shards),
            "versions": len({s["sha256"] for s in shards}), "differ": [s["shard"] for s in differ],
            "largest_difference": max((s["largest_difference"] for s in differ), default=0.0), "by_shard": shards}


def population_line(p: dict) -> str:
    if not p["differ"] and p["versions"] <= 1:
        return (f"Drawn population: every shard ran the same {p['workloads']} workloads, the ones the scorer "
                f"draws from the master seed (sha256 `{p['sha256']}`).")
    return (f"Drawn population: the {p['shards']} shards ran {p['versions']} version(s) of the {p['workloads']} "
            f"drawn workloads; {len(p['differ'])} of {p['shards']} shards differ from the scorer's own draw "
            f"(shards {', '.join(map(str, p['differ']))}), by at most {p['largest_difference']:.1e} relative in "
            "any number. Every run is scored as its shard ran it; score.json names each shard's processor.")


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------

def spearman(x, y) -> float:
    rx, ry = stats.rankdata(x), stats.rankdata(y)
    if len(rx) < 2 or np.std(rx) == 0 or np.std(ry) == 0:
        return math.nan
    return float(np.corrcoef(rx, ry)[0, 1])


def permutation_p(x, y, draws: int, seed: int) -> float:
    """One-sided: the share of permutations of y whose rho is at least the observed (with the +1 rule)."""
    rho = spearman(x, y)
    if not math.isfinite(rho):
        return math.nan
    rx, ry = stats.rankdata(x), stats.rankdata(y)
    rng = np.random.default_rng(seed)
    perm = np.array([rng.permutation(ry) for _ in range(draws)])
    rxc = (rx - rx.mean()) / rx.std()
    pc = (perm - perm.mean(axis=1, keepdims=True)) / perm.std(axis=1, keepdims=True)
    rhos = (pc * rxc).mean(axis=1)
    return float((1 + np.sum(rhos >= rho - 1e-12)) / (draws + 1))


def line(x, y) -> tuple[float, float]:
    """Least-squares (intercept, slope) of y on x; NaN when x does not vary."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < 2 or np.ptp(x) == 0:
        return math.nan, math.nan
    slope, intercept = np.polyfit(x, y, 1)
    return float(intercept), float(slope)


def paired_t(d: np.ndarray) -> tuple[float, float, int]:
    """(mean, standard error, degrees of freedom) of paired differences."""
    n = len(d)
    if n < 2:
        return (float(d.mean()) if n else math.nan), math.nan, n - 1
    return float(d.mean()), float(d.std(ddof=1) / math.sqrt(n)), n - 1


def ci(mean: float, se: float, df: int, level: float) -> list[float]:
    if not (math.isfinite(se) and df >= 1):
        return [math.nan, math.nan]
    h = float(stats.t.ppf(0.5 + level / 2, df)) * se
    return [mean - h, mean + h]


def tost(d: np.ndarray, margin: float) -> dict:
    mean, se, df = paired_t(d)
    if not (math.isfinite(se) and math.isfinite(margin)):
        p = math.nan
    elif se == 0:
        p = 0.0 if abs(mean) < margin else 1.0
    else:
        p = max(1 - stats.t.cdf((mean + margin) / se, df), stats.t.cdf((mean - margin) / se, df))
    return {"mean_d": mean, "se": se, "n": len(d), "margin": margin, "p": float(p),
            "ci90": ci(mean, se, df, 0.90), "ci95": ci(mean, se, df, 0.95)}


def superiority(d: np.ndarray) -> dict:
    mean, se, df = paired_t(d)
    if not math.isfinite(se):
        p = math.nan
    elif se == 0:
        p = 0.0 if mean > 0 else 1.0
    else:
        p = 1 - stats.t.cdf(mean / se, df)
    return {"mean_d": mean, "se": se, "n": len(d), "p": float(p), "ci95": ci(mean, se, df, 0.95)}


def holm(pvals: dict[str, float], family: list[str]) -> dict[str, float]:
    """Holm-adjusted p-values; the family's size counts every listed hypothesis."""
    k = len(family)
    finite = sorted((pvals[h], h) for h in family if math.isfinite(pvals.get(h, math.nan)))
    adj, running = {h: math.nan for h in family}, 0.0
    for i, (p, h) in enumerate(finite):
        running = max(running, min(1.0, (k - i) * p))
        adj[h] = running
    return adj


# --------------------------------------------------------------------------
# The hypotheses
# --------------------------------------------------------------------------

def with_columns(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """`df` with every column of `cols`, a missing one all NaN (when every run that writes it failed)."""
    return df.assign(**{c: np.nan for c in cols if c not in df.columns})


def per_regime(runs: pd.DataFrame, predictor: str) -> pd.DataFrame:
    """Per regime: mean gain J(decoupled) - J(joint) and mean predictor over design points with both runs."""
    runs = with_columns(runs, ["J", predictor])
    joint = runs[runs.search == "joint"].set_index(POINT)
    dec = runs[runs.search == "decoupled"].set_index(POINT)
    paired = joint[["J", predictor]].join(dec[["J"]].rename(columns={"J": "J_dec"}), how="inner")
    paired = paired[np.isfinite(paired.J) & np.isfinite(paired.J_dec) & np.isfinite(paired[predictor])]
    paired = paired.assign(gain=paired.J_dec - paired.J)
    return paired.groupby(level=REGIME).agg(gain=("gain", "mean"), x=(predictor, "mean"),
                                            n=("gain", "size")).reset_index()


def score_h1(dose: pd.DataFrame) -> dict:
    """Theorem 1 over the completed runs' logged decisions. It holds only when some decision was
    checked and none violates it: with nothing checked (every run failed, or the theorem never
    applied) there is no evidence either way (until 2026-11-20 that read as holding, and a dose part
    whose every run failed stopped the scorer)."""
    ok = with_columns(dose[dose.error == ""], ["theorem_applies", "theorem_violations"])
    checked, violations = int(ok.theorem_applies.sum()), int(ok.theorem_violations.sum())
    return {"checked": checked, "violations": violations, "failed_runs": int(len(dose) - len(ok)),
            "holds": checked > 0 and violations == 0}


def score_h2(dose: pd.DataFrame, spec: dict, master: str, repo: Path) -> dict:
    predictor = spec["predictor"]
    if predictor not in PREDICTORS:
        raise SystemExit(f"H2's predictor must be one of {PREDICTORS}")
    reg = per_regime(dose, predictor)
    pilot = per_regime(pd.read_csv(repo / spec["calibration"], float_precision="round_trip"), predictor)
    a, b = line(pilot.x, pilot.gain)
    predicted = a + b * reg.x
    _, slope = line(predicted, reg.gain)
    rho = spearman(reg.x, reg.gain)
    p = permutation_p(reg.x, reg.gain, int(spec["permutations"]), seeds.run_seed(master, "score|H2|permutation"))
    return {"predictor": predictor, "regimes": int(len(reg)), "rho": rho, "p": p, "calibration_slope": slope,
            "pilot_line": {"intercept": a, "slope": b, "regimes": int(len(pilot))},
            "table": reg.assign(predicted_gain=predicted).to_dict(orient="records")}


def pairs(comp: pd.DataFrame, plant: str, arm: str, granularity: str) -> tuple[pd.DataFrame, int]:
    """Per workload, the joint planner's and `arm`'s rows at one plant and granularity; and how many
    workloads are left out because a run failed."""
    cols = ["J", "total_cost_usd", "mean_violation"]
    comp = with_columns(comp, cols)
    at = comp[(comp.plant == plant) & comp.granularities.str.split("+").apply(lambda g: granularity in g)]
    j = at[at.arm == "joint"].set_index("workload")[cols]
    c = at[at.arm == arm].set_index("workload")[cols]
    both = j.join(c, how="inner", lsuffix="_joint", rsuffix="_arm")
    ok = both[np.isfinite(both.J_joint) & np.isfinite(both.J_arm)]
    return ok, int(len(both) - len(ok))


def score_comparison(comp: pd.DataFrame, spec: dict) -> dict:
    ok, lost = pairs(comp, spec["plant"], spec["comparator"], spec["granularity"])
    d = (ok.J_arm - ok.J_joint).to_numpy()
    if spec["test"] == "equivalence":
        out = tost(d, float(spec["margin"]) * float(ok.J_joint.mean()) if len(ok) else math.nan)
    elif spec["test"] == "superiority":
        out = superiority(d)
    else:
        raise SystemExit(f"unknown test {spec['test']!r}: equivalence or superiority")
    return {**{k: spec[k] for k in ("plant", "comparator", "granularity", "test")}, **out,
            "mean_J_joint": float(ok.J_joint.mean()) if len(ok) else math.nan, "left_out": lost}


def describe(comp: pd.DataFrame, granularities: list[str]) -> list[dict]:
    out = []
    for plant in sorted(comp.plant.unique()):
        for g in granularities:
            for arm in sorted(set(comp.arm) - {"joint"}):
                ok, lost = pairs(comp, plant, arm, g)
                d = (ok.J_arm - ok.J_joint).to_numpy()
                mean, se, df = paired_t(d)
                out.append({"plant": plant, "granularity": g, "arm": arm, "n": int(len(ok)), "left_out": lost,
                            "mean_J_joint": float(ok.J_joint.mean()) if len(ok) else math.nan,
                            "mean_d_J": mean, "ci95": ci(mean, se, df, 0.95),
                            "mean_d_cost_usd": float((ok.total_cost_usd_arm - ok.total_cost_usd_joint).mean()),
                            "mean_d_violation": float((ok.mean_violation_arm - ok.mean_violation_joint).mean())})
    return out


def score(rows: pd.DataFrame, design: dict, master: str, repo: Path = campaign.REPO) -> dict:
    tests = design["tests"]
    dose, comp = rows[rows.part == "dose"], rows[rows.part == "comp"]
    result = {"H1": score_h1(dose), "H2": score_h2(dose, tests["H2"], master, repo),
              "H3": score_comparison(comp, tests["H3"]), "H4": score_comparison(comp, tests["H4"])}
    alpha, family = float(tests["alpha"]), list(tests["holm"])
    adjusted = holm({h: result[h]["p"] for h in family}, family)
    for h in family:
        result[h]["p_holm"] = adjusted[h]
    for h in ("H2", "H3", "H4"):
        r = result[h]
        significant = math.isfinite(r.get("p_holm", r["p"])) and r.get("p_holm", r["p"]) < alpha
        if h == "H2":
            lo, hi = tests["H2"]["slope_range"]
            r["supported"] = bool(significant and r["rho"] >= tests["H2"]["rank_correlation_min"]
                                  and lo <= r["calibration_slope"] <= hi)
        else:
            r["supported"] = bool(significant)
    return {"hypotheses": result, "descriptive": describe(comp, list(design["comp"]["granularities"])),
            "runs": {"total": int(len(rows)), "failed": int((rows.error != "").sum()),
                     "dose": int(len(dose)), "comp": int(len(comp))}}


# --------------------------------------------------------------------------
# Report and command line
# --------------------------------------------------------------------------

def f(x: float, fmt: str = "+.4f") -> str:
    return "n/a" if x is None or not math.isfinite(x) else format(x, fmt)


def intervals(r: dict) -> str:
    """The mean of d with its intervals: H3's 90% one (the TOST holds exactly when it lies inside the
    margins) beside the 95% one, as section 6 of the protocol reports both; H4's 95% one."""
    levels = ("90", "95") if r["test"] == "equivalence" else ("95",)
    return f"{f(r['mean_d'])} (" + "; ".join(
        f"{lv}% CI [{f(r[f'ci{lv}'][0])}, {f(r[f'ci{lv}'][1])}]" for lv in levels) + ")"


def report(s: dict, meta: dict) -> str:
    h = s["hypotheses"]
    L = ["# WP14 confirmatory campaign: scores", ""]
    if meta["rehearsal"]:
        L += ["**REHEARSAL: a made-up master seed and two of everything. Pipeline check only; no result.**", ""]
    L += [f"Generated by `research/registration/wp14_score.py`. Master seed `{meta['master']}`; design.json "
          f"sha256 `{meta['design_sha256']}`; {s['runs']['total']} runs ({s['runs']['dose']} dose, "
          f"{s['runs']['comp']} comp), {s['runs']['failed']} failed (left out). alpha {meta['alpha']}; Holm over "
          f"{', '.join(meta['holm'])}.", ""]
    if "population" in s:
        L += [population_line(s["population"]), ""]
    L += ["| Hypothesis | Test | Estimate | p | Holm p | Supported |", "|---|---|---|---:|---:|---|",
          f"| H1 | Theorem 1 on every logged decision | {h['H1']['violations']} violations in "
          f"{h['H1']['checked']:,} decisions | | | {'yes' if h['H1']['holds'] else 'no'} |",
          f"| H2 | Spearman rho of gain on mean {h['H2']['predictor']} over {h['H2']['regimes']} regimes; "
          f"calibration slope | rho {f(h['H2']['rho'], '.3f')}; slope {f(h['H2']['calibration_slope'], '.3f')} | "
          f"{f(h['H2']['p'], '.4f')} | {f(h['H2'].get('p_holm', math.nan), '.4f')} | "
          f"{'yes' if h['H2']['supported'] else 'no'} |"]
    for k in ("H3", "H4"):
        r = h[k]
        what = (f"TOST, margin {f(r['margin'], '.4f')}" if r["test"] == "equivalence" else "one-sided paired t") + \
            f": J({r['comparator']}) - J(joint), plant {r['plant']}, {r['granularity']}, n {r['n']}"
        L.append(f"| {k} | {what} | {intervals(r)} | {f(r['p'], '.4f')} | "
                 f"{f(r.get('p_holm', math.nan), '.4f')} | {'yes' if r['supported'] else 'no'} |")
    L += ["", "## H2 by regime", "", "| width | kappa | design points | mean predictor | gain | predicted gain |",
          "|---:|---:|---:|---:|---:|---:|"]
    for r in sorted(h["H2"]["table"], key=lambda r: (r["width"], r["kappa"])):
        L.append(f"| {int(r['width'])} | {r['kappa']:.3f} | {r['n']} | {f(r['x'], '.4f')} | {f(r['gain'])} | "
                 f"{f(r['predicted_gain'])} |")
    L += ["", "## Every arm against the joint planner (descriptive, no test)", "",
          "| plant | granularity | arm | n | mean J(joint) | mean J(arm) - J(joint) [95% CI] | cost (USD) | violation |",
          "|---|---|---|---:|---:|---|---:|---:|"]
    for r in s["descriptive"]:
        L.append(f"| {r['plant']} | {r['granularity']} | {r['arm']} | {r['n']} | {f(r['mean_J_joint'], '.4f')} | "
                 f"{f(r['mean_d_J'])} [{f(r['ci95'][0])}, {f(r['ci95'][1])}] | {f(r['mean_d_cost_usd'], '+.4f')} | "
                 f"{f(r['mean_d_violation'])} |")
    return "\n".join(L) + "\n"


def clean(x):
    """JSON without NaN: NaN and infinities become null."""
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, (np.floating, float)):
        return float(x) if math.isfinite(x) else None
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, np.bool_):
        return bool(x)
    return x


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--master", help="the registered draw's master seed (confirm.py passes it)")
    ap.add_argument("--results", type=Path, required=True, help="the directory holding shard-0, shard-1, ...")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--design", type=Path, default=campaign.DESIGN)
    ap.add_argument("--rehearsal", action="store_true")
    args = ap.parse_args(argv)
    master = campaign.master_of(args)
    design = campaign.load_design(args.design, args.rehearsal)
    jobs, drawn = campaign.plan(master, design, args.rehearsal)
    rows = read_rows(args.results)
    check_coverage(rows, jobs)
    s = {**score(rows, design, master), "population": population_check(args.results, drawn)}
    meta = {"master": master, "rehearsal": args.rehearsal, "alpha": design["tests"]["alpha"],
            "holm": design["tests"]["holm"],
            "design_sha256": hashlib.sha256(args.design.read_bytes()).hexdigest()}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "score.json").write_text(json.dumps(clean({**meta, **s}), indent=1, allow_nan=False) + "\n",
                                         encoding="utf-8")
    text = report(s, meta)
    (args.out / "SCORE.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
