"""index-probe: how much can per-knob control lose to joint control, here?

Programme 2, WP23. A command-line front end to `research/theory/
interaction_index.py` (definitions and proofs in `research/theory/
INDEX_THEORY.md`). For one decision of one controller, with knobs k = 1..K,
an anchor x0 (the configuration the decision starts from) and a box of
candidate values per knob:

  main effect   f_k(v) = phi(x0 with knob k set to v) - phi(x0)
  remainder     R(x)   = phi(x) - phi(x0) - sum_k f_k(x_k)
  index         I      = max R - min R over the box (points where phi is defined)
  per-knob      x^D    = each knob's best value with the others held at x0
  joint         x^J    = the best point of the box
  Theorem 1     0 <= phi(x^D) - phi(x^J) <= R(x^D) - R(x^J) <= I
                (when the anchor, x^D and x^J are feasible and R is defined
                 at x^J; every other decision is counted under the reason
                 the theorem says nothing about it: no feasible point, the
                 per-knob combination infeasible (it needs repair), the
                 anchor infeasible, or the joint choice off the
                 decomposition (one of its values is infeasible on its own
                 axis), where the loss is defined and may exceed I)

Two modes:

  log   a JSON-lines file of decisions, each with its box and the objective
        value of every evaluated candidate (a joint planner evaluates them
        all anyway). Exact index per decision, plus a summary.

          {"id": "t03@17", "values": [[1, 2, 3], [64, 128], ["small", "mid"]],
           "anchor": [2, 128, "small"],
           "phi": [[[1, 64, "small"], 0.41], [[1, 64, "mid"], null], ...],
           "chosen": [3, 128, "small"]}            # optional: what the system did

        `null` marks an infeasible candidate. A missing candidate counts as
        unevaluated, so the index is then exact only over what was logged; so
        does a box point whose candidate was logged under a label that is not
        one of its knob's values ("128" for 128): that candidate lies outside
        the box, is not used, and is counted (`logged_outside_box`). A
        candidate logged twice must carry one value.

  box   an objective you supply (`module:function`, called with one point as
        a tuple, returning a float or None) and a box (JSON with "values" and
        "anchor"). Exact by default; with --sample m it evaluates only the
        anchor, the axes and m random points, and reports the sampled range:
        never above I, and at least the 5%-95% quantile range with
        probability >= 1 - 2 * 0.95**m (Proposition T5; m = 59 gives 0.903).
        Both ways the box is checked (distinct values per knob, the anchor
        in the box: T5 needs uniform draws).

  Objective values must be finite numbers or None (null) in both modes. The
  exit code is 1 if Theorem 1 is violated on a logged decision, and 2 if the
  input is refused, with one line naming the file, line and decision.

    python index_probe.py log decisions.jsonl [--json out.json]
    python index_probe.py box --objective mymodule:phi --box box.json [--sample 59] [--seed 1]
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import random
import statistics
import sys
from pathlib import Path

_THEORY = Path(__file__).resolve().parents[2] / "research" / "theory"
if str(_THEORY) not in sys.path:
    sys.path.insert(0, str(_THEORY))

from interaction_index import (_validate as validate_box,  # noqa: E402
                               analyse_table, remainder_table, sampling_confidence)

EPS = 1e-9
# Why Theorem 1 says nothing about a decision, in the order they are tested.
OUTSIDE = ("no feasible point", "per-knob combination infeasible", "anchor infeasible",
           "joint choice off the decomposition")
DECISION_FIELDS = ("values", "anchor", "phi")


class ProbeInputError(ValueError):
    """Input the tool refuses: `main` prints it on one line and exits 2
    (exit 1 means Theorem 1 violations)."""


def _point(p) -> tuple:
    return tuple(p)


def _finite_or_none(x: tuple, y):
    """phi's value at x, which must be None (infeasible) or a finite number:
    remainder_table refuses anything else, and so must every path that
    evaluates phi without it, or one NaN or inf gives a wrong range."""
    try:
        if y is None or math.isfinite(y):
            return y
    except TypeError:
        pass
    raise ProbeInputError(f"objective is not finite at {x!r}: {y!r} (use None for infeasible)")


def _logged(entries) -> dict:
    """A decision's logged candidates as {point: value}; a candidate logged
    twice must carry one value, or which one counts would be arbitrary."""
    phi_map = {}
    for p, v in entries:
        x = _point(p)
        _finite_or_none(x, v)
        if x in phi_map and phi_map[x] != v:
            raise ProbeInputError(f"candidate {x!r} is logged twice, with {phi_map[x]!r} and {v!r}")
        phi_map[x] = v
    return phi_map


def outside_reason(a) -> str | None:
    """None when Theorem 1 applies to the analysed decision, else why not.
    With the anchor and x^D feasible, R is defined at x^D (each of its values
    is feasible on its axis), so what is left is R undefined at x^J."""
    if a.theorem1_applies:
        return None
    if a.joint is None:
        return OUTSIDE[0]
    if a.phi_per_knob is None:
        return OUTSIDE[1]
    if a.phi_anchor is None:
        return OUTSIDE[2]
    return OUTSIDE[3]


def analyse_decision(d: dict) -> dict:
    """One logged decision -> its index, bound, losses and flags. Candidates
    outside the box are counted and not used."""
    if not isinstance(d, dict) or any(k not in d for k in DECISION_FIELDS):
        raise ProbeInputError('a decision is a JSON object with "values", "anchor" and "phi"')
    values = [list(v) for v in d["values"]]
    anchor = _point(d["anchor"])
    phi_map = _logged(d["phi"])
    if anchor not in phi_map:
        raise ProbeInputError("the anchor must be logged")
    table = remainder_table(values, lambda x: phi_map.get(_point(x)), anchor)
    evaluated = sum(x in phi_map for x in table.phi)  # box points with a logged value
    a = analyse_table(values, table)
    out = {"id": d.get("id"), "index": a.oscillation, "quantile_index": a.quantile_index,
           "per_knob": a.per_knob, "joint": a.joint, "loss": a.loss, "bound": a.bound,
           "theorem1_applies": a.theorem1_applies, "needs_repair": a.phi_per_knob is None,
           "outside_theorem1": outside_reason(a),
           "evaluated": evaluated, "logged_outside_box": len(phi_map) - evaluated,
           "box_size": len(table.phi)}
    if a.theorem1_applies:
        out["theorem1_holds"] = (-EPS <= a.loss <= a.bound + EPS) and a.bound <= a.oscillation + EPS
    if "chosen" in d and a.phi_joint is not None:
        chosen = phi_map.get(_point(d["chosen"]))
        out["chosen_gap"] = None if chosen is None else chosen - a.phi_joint
    return out


def summarise(rows: list[dict]) -> dict:
    def stat(key):
        v = [r[key] for r in rows if r.get(key) is not None]
        return {"n": len(v), "mean": statistics.fmean(v) if v else None,
                "median": statistics.median(v) if v else None, "max": max(v) if v else None}

    applies = [r for r in rows if r["theorem1_applies"]]
    return {"decisions": len(rows), "index": stat("index"), "quantile_index": stat("quantile_index"),
            "loss": stat("loss"), "bound": stat("bound"),
            "share_needing_repair": sum(r["needs_repair"] for r in rows) / len(rows) if rows else None,
            "share_disagreeing": (sum(r["per_knob"] != r["joint"] for r in applies) / len(applies)
                                  if applies else None),
            "theorem1_checked": len(applies),
            "theorem1_violations": sum(not r["theorem1_holds"] for r in applies),
            "outside_theorem1": {why: sum(r["outside_theorem1"] == why for r in rows) for why in OUTSIDE},
            "outside_loss_above_index": sum(1 for r in rows if r["outside_theorem1"] and r["loss"] is not None
                                            and r["index"] is not None and r["loss"] > r["index"] + EPS),
            "incompletely_logged": sum(r["evaluated"] < r["box_size"] for r in rows),
            "logged_outside_box": sum(r["logged_outside_box"] > 0 for r in rows)}


def run_log(path: Path) -> tuple[list[dict], dict]:
    """Every decision of a JSON-lines log; refused input names its line and decision."""
    rows = []
    with path.open(encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except ValueError as e:
                raise ProbeInputError(f"{path}, line {n}: not JSON ({e})") from e
            try:
                rows.append(analyse_decision(d))
            except (ValueError, KeyError, TypeError) as e:
                name = f" (decision {d['id']!r})" if isinstance(d, dict) and "id" in d else ""
                raise ProbeInputError(f"{path}, line {n}{name}: {e}") from e
    return rows, summarise(rows)


def _objective(spec: str):
    module, _, name = spec.partition(":")
    if not name:
        raise ProbeInputError("objective must be module:function")
    cwd = str(Path.cwd())
    if cwd not in sys.path:  # resolve the module from where the tool is run
        sys.path.insert(0, cwd)
    return getattr(importlib.import_module(module), name)


def _read_box(box_path: Path) -> tuple[list[list], tuple]:
    """The box's values and anchor, checked as remainder_table checks them."""
    try:
        box = json.loads(box_path.read_text(encoding="utf-8"))
        if not isinstance(box, dict) or "values" not in box or "anchor" not in box:
            raise ProbeInputError('a box is a JSON object with "values" and "anchor"')
        values = [list(v) for v in box["values"]]
        anchor = _point(box["anchor"])
        validate_box(values, anchor)
    except (ValueError, TypeError) as e:
        raise ProbeInputError(f"{box_path}: {e}") from e
    return values, anchor


def run_box(objective: str, box_path: Path, sample: int | None, seed: int) -> dict:
    if sample is not None and sample < 1:
        raise ProbeInputError(f"--sample must be at least 1, not {sample}")
    values, anchor = _read_box(box_path)
    user_phi = _objective(objective)

    def phi(x):
        return _finite_or_none(tuple(x), user_phi(x))

    if sample is None:
        table = remainder_table(values, phi, anchor)
        a = analyse_table(values, table)
        return {"mode": "exact", "index": a.oscillation, "quantile_index": a.quantile_index,
                "per_knob": a.per_knob, "joint": a.joint, "loss": a.loss, "bound": a.bound,
                "needs_repair": a.phi_per_knob is None, "evaluations": len(table.phi)}
    # T5: the anchor and the axes give the main effects; m random points give R there.
    rng = random.Random(seed)
    phi0 = phi(anchor)
    if phi0 is None:
        raise ProbeInputError("the objective must be defined at the anchor")
    axis = []
    for k, vals in enumerate(values):
        f = {}
        for v in vals:
            x = list(anchor)
            x[k] = v
            y = phi(tuple(x))
            f[v] = None if y is None else y - phi0
        axis.append(f)
    evaluations = 1 + sum(len(v) for v in values)
    rs, tries = [], 0
    while len(rs) < sample and tries < 50 * sample:  # draws over feasible points, by rejection
        tries += 1
        x = tuple(rng.choice(v) for v in values)
        y = phi(x)
        evaluations += 1
        mains = [axis[k][x[k]] for k in range(len(values))]
        if y is None or any(m is None for m in mains):
            continue
        rs.append(y - phi0 - sum(mains))
    if len(rs) < sample:
        raise ProbeInputError(f"only {len(rs)} feasible points in {tries} draws")
    return {"mode": f"sampled, m = {sample}", "sampled_range": max(rs) - min(rs),
            "covers_quantile_index_with_probability_at_least": sampling_confidence(sample),
            "never_exceeds": "the exact index", "evaluations": evaluations}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="index-probe", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    lg = sub.add_parser("log", help="decisions with every candidate's objective value (JSON lines)")
    lg.add_argument("path", type=Path)
    lg.add_argument("--json", type=Path, help="write per-decision rows and the summary here")
    bx = sub.add_parser("box", help="an objective function and a box")
    bx.add_argument("--objective", required=True, help="module:function, called with one point (a tuple)")
    bx.add_argument("--box", required=True, type=Path, help='JSON: {"values": [[...], ...], "anchor": [...]}')
    bx.add_argument("--sample", type=int, help="estimate from m random points (Proposition T5)")
    bx.add_argument("--seed", type=int, default=1)
    args = ap.parse_args(argv)
    try:
        if args.mode == "log":
            rows, summary = run_log(args.path)
            print(json.dumps(summary, indent=2, default=str))
            if args.json:
                args.json.write_text(json.dumps({"summary": summary, "decisions": rows}, indent=1, default=str),
                                     encoding="utf-8")
            return 1 if summary["theorem1_violations"] else 0
        print(json.dumps(run_box(args.objective, args.box, args.sample, args.seed), indent=2, default=str))
        return 0
    except ProbeInputError as e:
        print(f"index-probe: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
