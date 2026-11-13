"""WP3 property checks at scale: Theorems 1 and 2 on 100,000 random boxes,
T3 on 100,000 random (rho, eta), T5 exactly on every box and on the hardest
tables, and the report PROPERTY_CHECKS.md (which also carries T4's results
from t4_check.py).

Programme 2, WP3 and WP18. The statements are drafts (INDEX_THEORY.md).
Cases are drawn by `property_cases.py` from seed (BASE_SEED, case id), so a
chunked run gives the same cases as a single one. Each command below stays
under about three minutes of one CPU core on the machine this ran on.

    python property_check.py boxes --start 0 --count 25000      # then 25000, 50000, 75000
    python property_check.py t3                                  # 100,000 (rho, eta)
    python property_check.py t5-tables                           # hardest tables, n = 2..400
    python property_check.py t5-mc                               # sample_range against the exact coverage
    python property_check.py report                              # writes PROPERTY_CHECKS.md

Results accumulate in data/property_checks.json (one entry per chunk).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from interaction_index import analyse_box, sampling_confidence, tight_instance  # noqa: E402
from property_cases import (  # noqa: E402
    BASE_SEED, FAMILIES, FAMILY_NOTES, M_DRAWS, MAX_POINTS, SIZE_MAX, SIZE_MIN,
    check_case, check_t3, describe_box, draw_rho_eta, hardest_table_coverage,
    make_case, tie_instance,
)

RESULTS = HERE / "data" / "property_checks.json"
T4_RESULTS = HERE / "data" / "t4_checks.json"
REPORT = HERE / "PROPERTY_CHECKS.md"
N_BOXES = 100_000
N_T3 = 100_000
MAX_EXAMPLES = 20
MIN_KEYS = ("t5_min", "t5_mc_min")  # extreme values kept as minima; the rest as maxima


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")


def merge_worst(into: dict, worst: dict) -> None:
    for k, v in worst.items():
        keep_min = k.startswith(MIN_KEYS)
        if k not in into:
            into[k] = v
        else:
            into[k] = min(into[k], v) if keep_min else max(into[k], v)


def run_boxes(start: int, count: int, mc_every: int, mc_trials: int) -> dict:
    """Cases [start, start + count): counters per family, extremes, failures
    and the shape of the boxes drawn."""
    by_family = {f: Counter() for f in FAMILIES}
    worst, examples = {}, []
    shape = Counter()
    t0 = time.perf_counter()
    for i in range(start, start + count):
        case = make_case(i)
        c, ex, w = check_case(case, mc_trials=mc_trials if mc_every > 0 and i % mc_every == 0 else 0)
        by_family[case.family] += c
        merge_worst(worst, w)
        examples.extend(ex[: MAX_EXAMPLES - len(examples)])
        d = describe_box(case)
        shape[f"knobs_{d['knobs']}"] += 1
        shape[f"max_values_{d['max_values']}"] += 1
        shape["points_sum"] += d["points"]
        shape["points_max"] = max(shape["points_max"], d["points"])
        shape["with_infeasible"] += d["infeasible"]
        shape["min_values_min"] = min(shape.get("min_values_min", SIZE_MAX), d["min_values"])
    return {"start": start, "count": count, "seconds": round(time.perf_counter() - t0, 1),
            "mc_every": mc_every, "mc_trials": mc_trials,
            "by_family": {f: dict(c) for f, c in by_family.items()},
            "worst": worst, "examples": examples, "shape": dict(shape)}


def run_t3(count: int) -> dict:
    """Proposition T3 for `count` random (rho, eta), the eta = 0 tie variant
    and the argument checks of `tight_instance`."""
    fails, examples, min_gap = Counter(), [], math.inf
    for i in range(count):
        rho, eta = draw_rho_eta(i)
        bad = check_t3(rho, eta)
        fails.update(bad)
        if bad and len(examples) < MAX_EXAMPLES:
            examples.append({"i": i, "rho": rho, "eta": eta, "failed": bad})
        min_gap = min(min_gap, (rho - eta) / rho)
        if i % 10 == 0:  # the tie variant: loss equal to the index, exactly
            a = analyse_box(*tie_instance(rho))
            if not (a.per_knob == (1, 1) and a.loss == rho and a.oscillation == rho and a.bound == rho):
                fails["tie_variant"] += 1
    refused = 0
    for rho, eta in ((1.0, 0.0), (1.0, 1.0), (1.0, 2.0), (1.0, -0.5), (1.0, float("nan")), (0.0, 0.0)):
        try:
            tight_instance(rho, eta)
        except ValueError:
            refused += 1
    return {"count": count, "failures": dict(fails), "examples": examples,
            "tie_variant_checked": len(range(0, count, 10)), "invalid_refused": refused,
            "invalid_tried": 6, "smallest_relative_gap": min_gap}


def run_t5_mc(every: int = 200, trials: int = 2000) -> dict:
    """The library's `sample_range` against the exact coverage at m = 59, on
    boxes 0, every, 2 every, ... of the recorded run: exact two-sided
    binomial test per box at level MC_ALPHA."""
    from property_cases import MC_ALPHA
    tot, worst, examples = Counter(), {}, []
    t0 = time.perf_counter()
    for i in range(0, N_BOXES, every):
        c, ex, w = check_case(make_case(i), mc_trials=trials)
        tot += c
        merge_worst(worst, w)
        examples += [e for e in ex if "Monte Carlo" in e][: MAX_EXAMPLES - len(examples)]
    return {"every": every, "trials": trials, "alpha": MC_ALPHA, "boxes": tot["t5_mc_boxes"],
            "disagree": tot["t5_mc_disagree"], "misses": tot["t5_mc_misses"],
            "sample_above_index": tot["t5_sample_above_index"], "draws": tot["t5_draws"],
            "min_pvalue": worst.get("t5_mc_min_pvalue"), "min_empirical": worst.get("t5_mc_min_empirical"),
            "examples": examples, "seconds": round(time.perf_counter() - t0, 1)}


def run_t5_tables(n_max: int = 400) -> dict:
    """Exact coverage on the hardest tables against the bound and the closed form."""
    out = {}
    for m in M_DRAWS:
        worst_margin, at_n, max_err, below = math.inf, None, 0.0, 0
        for n in range(2, n_max + 1):
            exact, closed = hardest_table_coverage(n, m)
            margin = exact - sampling_confidence(m)
            below += margin < -1e-12
            max_err = max(max_err, abs(exact - closed))
            if margin < worst_margin:
                worst_margin, at_n = margin, n
        out[str(m)] = {"bound": sampling_confidence(m), "min_margin": worst_margin, "at_n": at_n,
                       "below_bound": below, "max_abs_error_vs_closed_form": max_err}
    return {"n_range": [2, n_max], "by_m": out}


# ---------------------------------------------------------------- report

def _sum_family(chunks: list[dict]) -> dict:
    tot = {f: Counter() for f in FAMILIES}
    for ch in chunks:
        for f, c in ch["by_family"].items():
            tot[f] += Counter(c)
    return tot


def _boxes_section(data: dict) -> list[str]:
    chunks = sorted(data.get("boxes", {}).values(), key=lambda ch: ch["start"])
    if not chunks:
        return ["## Theorems 1 and 2 on random boxes", "", "Not run yet.", ""]
    covered = sum(ch["count"] for ch in chunks)
    contiguous = all(a["start"] + a["count"] == b["start"] for a, b in zip(chunks, chunks[1:]))
    fam = _sum_family(chunks)
    total = sum(fam.values(), Counter())
    worst = {}
    for ch in chunks:
        merge_worst(worst, ch["worst"])
    shape = Counter()
    for ch in chunks:
        for k, v in ch["shape"].items():
            shape[k] = max(shape[k], v) if k == "points_max" else (
                min(shape.get(k, v), v) if k == "min_values_min" else shape[k] + v)
    # Monte Carlo flags inside chunks came from a z-test that is wrong near
    # p = 1 (see run_t5_mc); the cross-check is reported from `t5-mc` only.
    examples = [e for ch in chunks for e in ch["examples"] if "Monte Carlo" not in e][:MAX_EXAMPLES]
    seconds = sum(ch["seconds"] for ch in chunks)
    lines = [
        "## Theorems 1 and 2 on random boxes",
        "",
        f"Cases {chunks[0]['start']:,} to {chunks[0]['start'] + covered - 1:,} "
        f"({covered:,} cases{'' if contiguous else ', NOT contiguous'}), drawn by `property_cases.make_case` "
        f"from seed ({BASE_SEED}, case id), in {len(chunks)} chunks, {seconds:,.0f} s of one core in total. "
        f"Boxes: {shape['knobs_2']:,} with 2 knobs, {shape['knobs_3']:,} with 3, {shape['knobs_4']:,} with 4; "
        f"{SIZE_MIN} to {SIZE_MAX} values per knob (smallest seen {shape['min_values_min']}, "
        f"{shape['max_values_17']:,} boxes have a knob with 17 values); at most {MAX_POINTS:,} points "
        f"(largest {shape['points_max']:,}, mean {shape['points_sum'] / max(1, covered):,.0f}); "
        f"{shape['with_infeasible']:,} boxes contain infeasible points. Value labels are integers in "
        "order, shuffled integers, strings, floats or tuples. Every case runs the library "
        "(`remainder_table`, `analyse_table`, every tenth case also `analyse_box`, "
        "`coordinatewise_minimum`, `theorem2_terms`, `sample_range`) and an independent numpy "
        "oracle in `property_cases.py`; tolerance 1e-12 x max(1, max |phi|).",
        "",
        "| family | cases | T1 applies | loss > 0 | loss = index | T1 violations | outside hypotheses: loss > index | "
        "constraint coupling | T2 applies (search) | T2 checks (all equilibria) | T2 violations | oracle mismatches |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for f in FAMILIES + ("all",):
        c = total if f == "all" else fam[f]
        name = "**all**" if f == "all" else f"`{f}`"
        mism = c["t1_oracle_mismatch"] + c["t2_oracle_mismatch"] + c["analyse_box_differs"]
        lines.append(
            f"| {name} | {c['cases']:,} | {c['t1_applies']:,} | {c['t1_costly']:,} | {c['t1_loss_equals_index']:,} | "
            f"{c['t1_violation']:,} | {c['t1_outside_loss_above_index']:,} of {c['t1_outside_hypotheses']:,} | "
            f"{c['t1_constraint_coupling']:,} | {c['t2_applies']:,} | {c['t2_equilibrium_checks']:,} | "
            f"{c['t2_violation']:,} | {mism:,} |")
    lines += [
        "",
        "Columns. *T1 applies*: the anchor, x^D and x^J are feasible and R(x^D), R(x^J) are defined "
        "(the library's `theorem1_applies`, which the oracle recomputes). *loss = index*: the bound is "
        "attained. *T1 violations*: a negative loss, or loss > R(x^D) - R(x^J) or R(x^D) - R(x^J) > index. "
        "*Outside hypotheses*: the anchor, x^D and x^J are feasible but a remainder is undefined; there the "
        "loss may exceed the index, so the hypothesis cannot be dropped. *Constraint coupling*: the "
        "anchor is feasible and the combination of per-knob winners is not. *T2 applies*: the library's "
        "search ends at a feasible coordinate-wise minimum x^C and R^C(x^J) is defined. *T2 checks*: the "
        f"oracle checks Theorem 2 at every coordinate-wise minimum of the box (up to 8 per case) against every "
        "joint minimiser (up to 8). *Oracle mismatches*: the library and the oracle disagree on x^D, x^J, "
        "a remainder, the loss, the bound, the index, the quantile index, `theorem1_applies`, the search "
        "result's coordinate-wise minimality or Theorem 2's terms.",
        "",
        f"Further counts: the search did not settle within `max_rounds` in {total['t2_search_not_settled']:,} cases; "
        f"it ended at an infeasible point with no feasible single-knob move in {total['t2_search_ends_infeasible']:,} "
        f"(correctly returning None); R^C(x^J) was undefined in {total['t2_remainder_undefined']:,}; "
        f"the anchor was infeasible in {total['t1_anchor_infeasible']:,} and no point was feasible in "
        f"{total['t1_no_feasible_point']:,}. Tight constructions checked against their expected loss, bound "
        f"and index: {total['tight_t1_cases']:,} (Theorem 1) and {total['tight_t2_cases']:,} (Theorem 2), "
        f"mismatches {total['tight_mismatch']:,}. Largest loss / index seen: "
        f"{worst.get('t1_max_loss_over_index', float('nan')):.6f}.",
        "",
        "## T5 on the same boxes",
        "",
        f"For every box with at least one defined remainder ({total['t5_boxes']:,} boxes), the probability that "
        "the range of m draws (uniform, with replacement, over the points where R is defined) reaches the "
        "quantile index is computed exactly (`property_cases.exact_coverage`, with the same floating-point "
        "comparison `sample_range(...) >= q_hi - q_lo` makes) and compared with 1 - 2 (0.95)^m.",
        "",
        "| m | bound 1 - 2(0.95)^m | smallest exact coverage minus bound |",
        "|---:|---:|---:|",
    ]
    for m in M_DRAWS:
        lines.append(f"| {m} | {sampling_confidence(m):.6f} | {worst.get(f't5_min_margin_m{m}', float('nan')):+.6f} |")
    lines += [
        "",
        f"Boxes with exact coverage below the bound: {total['t5_below_bound']:,}. Smallest exact coverage at "
        f"m = 59: {worst.get('t5_min_coverage_m59', float('nan')):.6f}. The library's `sample_range` "
        f"was drawn {total['t5_draws']:,} times at m = 59; a sampled range above the index: "
        f"{total['t5_sample_above_index']:,}.",
        "",
    ]
    mc = data.get("t5_mc")
    if mc:
        lines += [
            f"Cross-check of the sampler: on boxes 0, {mc['every']}, {2 * mc['every']}, ... of the run "
            f"({mc['boxes']:,} boxes with a defined remainder), `sample_range` was drawn {mc['trials']:,} times "
            f"each and its hit rate compared with the exact coverage by an exact two-sided binomial test at "
            f"level {mc['alpha']:g} per box: flagged {mc['disagree']:,}; smallest p-value "
            f"{mc['min_pvalue']:.3g}; lowest empirical coverage {mc['min_empirical']:.4f}; sampled ranges "
            f"above the index {mc['sample_above_index']:,} of {mc['draws']:,}. (A first version of this check "
            "used a normal approximation, which flagged two boxes whose exact coverage is about 0.99997 and "
            "which missed once in 1,000 draws, an unremarkable event; the exact test replaces it.)",
            "",
        ]
    if examples:
        lines += ["Failures (first 20):", ""] + [f"- {e}" for e in examples] + [""]
    lines += ["Families:", ""] + [f"- `{f}`: {FAMILY_NOTES[f]}" for f in FAMILIES] + [""]
    return lines


def _t3_section(data: dict) -> list[str]:
    t3 = data.get("t3")
    if not t3:
        return ["## Proposition T3", "", "Not run yet.", ""]
    fails = sum(t3["failures"].values())
    return [
        "## Proposition T3 (tightness)",
        "",
        f"{t3['count']:,} random (rho, eta): rho log-uniform over [1e-6, 1e6]; eta / rho uniform on (0, 1), "
        "or within 1e-12 of 0, or within 1e-12 of 1 (smallest (rho - eta) / rho drawn: "
        f"{t3['smallest_relative_gap']:.2e}). On `tight_instance(rho, eta)` the library must give "
        "x^D = (1, 1), a feasible joint optimum at -eta, loss rho - eta, bound rho and index rho "
        f"(tolerance 1e-12 rho). Failures: **{fails}**{(' ' + str(t3['failures'])) if fails else ''}. "
        f"The tie variant (eta = 0, improving value listed first; {t3['tie_variant_checked']:,} values of rho) "
        "must give loss = bound = index = rho exactly: included in the failures above "
        f"({t3['failures'].get('tie_variant', 0)}). Invalid arguments refused: {t3['invalid_refused']} of "
        f"{t3['invalid_tried']} (eta = 0, eta = rho, eta > rho, eta < 0, eta = NaN, rho = 0).",
        "",
    ]


def _t5_tables_section(data: dict) -> list[str]:
    t5 = data.get("t5_tables")
    if not t5:
        return []
    lines = [
        "## T5 on the hardest tables",
        "",
        f"For n = {t5['n_range'][0]} to {t5['n_range'][1]}: ceil(0.05 n) values at the top, as many at the "
        "bottom, the rest between, so the range reaches the quantile index only if both tails are drawn. "
        "Exact coverage against the bound and against the closed form 1 - 2(1-p)^m + (1-2p)^m, "
        "p = ceil(0.05 n) / n.",
        "",
        "| m | bound | smallest margin | at n | below bound | max error vs closed form |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for m, r in sorted(t5["by_m"].items(), key=lambda kv: int(kv[0])):
        lines.append(f"| {m} | {r['bound']:.6f} | {r['min_margin']:+.6f} | {r['at_n']} | {r['below_bound']} | "
                     f"{r['max_abs_error_vs_closed_form']:.1e} |")
    lines += ["", "The slack of the union bound on these tables is (1 - 2p)^m, 0.9^59 = 0.0020 at m = 59 "
              "when n is a multiple of 20: the stated bound is close to the worst case.", ""]
    return lines


FINDINGS = """## Findings about the existing code and text

1. **Non-finite objective values are accepted silently** (`interaction_index.py:113-119`, `:183-185`).
   `remainder_table` treats every value that is not None as feasible, so NaN and +-inf enter the
   remainder, and `analyse_table` sorts a list that may contain NaN. A single NaN gives a *finite and
   wrong* index; an infinite value gives an infinite or NaN index while `theorem1_applies` stays True.
   Reproduction (index 3.0; with the NaN point marked infeasible it is 12.0):

   ```python
   from interaction_index import analyse_box
   v = [[0, 1, 2], [0, 1, 2, 3]]
   t = [1.0, 0.0, 0.0, 2.0, -4.0, 4.0, float("nan"), -4.0, 0.0, -4.0, -2.0, -2.0]
   tab = dict(zip([(a, b) for a in v[0] for b in v[1]], t))
   print(analyse_box(v, tab.__getitem__, (0, 0)).oscillation)   # 3.0
   ```

   The simulator's planner is not affected (`controller.py:1395` returns None for an infeasible
   candidate, and `jain_index` guards its zero case), so INDEX_PILOT and DOSE_RESPONSE_PILOT stand.
   `tools/index-probe` is exposed: Python's `json.loads` accepts `NaN` and `Infinity` in a decision
   log, and `analyse_decision` then reports e.g. `index: inf, loss: inf, theorem1_holds: True`.
   A fix (not made here; this work package adds files only) is to reject non-finite values in
   `remainder_table`, or to treat them as infeasible. `test_property_checks.py` holds a strict xfail
   test that will start failing once the fix lands, so the marker gets removed.
2. **Notation clash in INDEX_THEORY.md** (lines 26 and 81-89): m is both the rank ceil(0.05 n) of
   the quantile index and the number of sampled points in T5, and the proof of T5 uses both
   ("q_hi (the m-th largest)" and "0.95^m"). The mathematics is right; a reader has to separate the
   two. The additions to INDEX_THEORY.md write the rank as j.
3. **Bit-exactness is not available**: from Python 3.12, `sum` over floats is compensated (Neumaier),
   so `remainder_table` is slightly more accurate than a left-to-right sum; library and oracle agree
   to rounding (TOL_REL), not bit for bit. Not a defect; recorded so nobody tests for equality.
4. Checked and fine: `math.ceil(0.05 * n)` equals the exact ceil(n / 20) for every n up to 200,000;
   `index-probe --sample` draws by rejection, so its draws are uniform over the points where R is
   defined, as T5 assumes.
"""


def write_report() -> str:
    data = _load(RESULTS)
    lines = [
        "# Property checks of the index theory (WP3, WP18)",
        "",
        "Generated by `research/theory/property_check.py report` from `data/property_checks.json` and "
        "`data/t4_checks.json`. Programme 2. The statements checked are in `INDEX_THEORY.md`; they are "
        "drafts until a second person has read the proofs, and passing checks do not change that.",
        "",
        "**Method: seeded numpy, not Hypothesis.** Hypothesis is installed neither here nor in CI, and the "
        "existing suite is seeded and dependency-free. Seeds make each of the 100,000 cases reproducible "
        "on its own (case i comes from seed (20261113, i)), and the cases need structured generators "
        "(constructions that reach the bound, budgets that couple the knobs, labels of every type) that a "
        "strategy library would not supply. Shrinking a failing case, which Hypothesis would add, is done "
        "for T4 by its own search. The fast pytest files (`test_property_checks.py`, `test_t4_t6.py`) run a "
        "seeded sample of the same checks.",
        "",
    ]
    lines += _boxes_section(data)
    lines += _t3_section(data)
    lines += _t5_tables_section(data)
    try:
        import t4_check
        lines += t4_check.report_section(_load(T4_RESULTS))
    except ImportError:  # pragma: no cover - t4_check.py sits beside this file
        lines += ["## T4", "", "t4_check.py not found.", ""]
    lines += [FINDINGS]
    text = "\n".join(lines).rstrip() + "\n"
    REPORT.write_text(text, encoding="utf-8")
    return text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("boxes")
    b.add_argument("--start", type=int, default=0)
    b.add_argument("--count", type=int, default=N_BOXES)
    b.add_argument("--mc-every", type=int, default=200)
    b.add_argument("--mc-trials", type=int, default=1000)
    t = sub.add_parser("t3")
    t.add_argument("--count", type=int, default=N_T3)
    sub.add_parser("t5-tables")
    sub.add_parser("t5-mc")
    sub.add_parser("report")
    args = ap.parse_args(argv)
    if args.cmd == "report":
        write_report()
        print(f"wrote {REPORT}")
        return 0
    if args.cmd == "boxes":  # compute first; read, merge and write the results file only at the end
        chunk = run_boxes(args.start, args.count, args.mc_every, args.mc_trials)
        data = _load(RESULTS)
        data.setdefault("boxes", {})[str(args.start)] = chunk
        fam = _sum_family([chunk])
        tot = sum(fam.values(), Counter())
        print(f"cases {args.start}..{args.start + args.count - 1}: {chunk['seconds']} s; "
              f"T1 violations {tot['t1_violation']}, T2 violations {tot['t2_violation']}, "
              f"mismatches {tot['t1_oracle_mismatch'] + tot['t2_oracle_mismatch'] + tot['analyse_box_differs']}, "
              f"tight mismatches {tot['tight_mismatch']}, T5 below bound {tot['t5_below_bound']}")
    elif args.cmd == "t5-mc":
        res = run_t5_mc()
        data = _load(RESULTS)
        data["t5_mc"] = res
        print({k: v for k, v in res.items() if k != "examples"})
    elif args.cmd == "t3":
        res = run_t3(args.count)
        data = _load(RESULTS)
        data["t3"] = res
        print("T3:", res["failures"] or "no failures", "| refused", res["invalid_refused"])
    else:
        res = run_t5_tables()
        data = _load(RESULTS)
        data["t5_tables"] = res
        print({m: (r["min_margin"], r["below_bound"]) for m, r in res["by_m"].items()})
    _save(RESULTS, data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
