"""The plan's specification curve: every arm re-selected under each
specification's objective (programme 2, WP5 step 2; EXPLORATORY).

pilot_curve.py re-scored arms that were tuned for one objective. Here every
specification re-runs the tuning protocol's selection under its own
objective. WP9's full protocol ran all 64 configurations of every arm on all
120 tuning workloads (research/comparators/full/), so selecting under another
objective is a re-scoring of those runs; the configurations a specification
selects are then scored on the 120 evaluation workloads. The ones WP9's own
evaluation did not run are run here with tune.py's jobs and seeds (every arm
and configuration of a workload sees the same demand) and its journal.

Choices (the WP9 protocol's own settings marked *):
  plant              A, B
  joint_budget       enforced*, plant     the joint planner's budget mode
  comparator_budget  enforced*, plant     the comparator's (WP9's --budget)
  granularity        overall*, per_class  one configuration, or one per class
  comparator         per_knob, one_knob, layers, rules
  violation_weight   1, 2*, 4
  fairness_weight    0, 0.5*
  quality_price      0*, 0.5, 1           on mean answer-quality loss
  unserved_price     0*, 1                x the share of AI requests shed
  scale              absolute*, log       J difference, or log ratio
Budget symmetry is the two budget choices being equal; an asymmetric
specification pairs arms from a plant's two WP9 campaigns, whose runs are
independent of each other. WP9 sampled the configurations with no quality
knob (quality weight 0), so a quality price re-selects among quality-blind
configurations.

Effect per evaluation workload = score(comparator) - score(joint): positive =
the joint planner is better; whole-curve inference by sign flips (curve.py).
Before anything else the selection is checked against WP9's own: under WP9's
objective it must pick exactly the configurations tuned.json records.

    python full_curve.py plan               # evaluation runs needed beyond WP9's
    python full_curve.py run --workers 6    # runs them (journaled, resumable)
    python full_curve.py curve              # FULL_SPEC_CURVE.md, full_spec_curve.png
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
COMPARATORS = HERE.parent / "comparators"
WP9 = COMPARATORS / "full"
OUT = HERE / "full"
for _p in (COMPARATORS, HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# curve before tune: tune puts other research directories first on the path
from curve import curve_from_matrix, enumerate_specs, plot, report, sign_flip_test  # noqa: E402

TENANTS = 8
COST_SCALE = 0.01
CHOICES = {
    "plant": ["A", "B"],
    "joint_budget": ["enforced", "plant"],
    "comparator_budget": ["enforced", "plant"],
    "granularity": ["overall", "per_class"],
    "comparator": ["per_knob", "one_knob", "layers", "rules"],
    "violation_weight": [1.0, 2.0, 4.0],
    "fairness_weight": [0.0, 0.5],
    "quality_price": [0.0, 0.5, 1.0],
    "unserved_price": [0.0, 1.0],
    "scale": ["absolute", "log"],
}
OBJECTIVE = ("violation_weight", "fairness_weight", "quality_price", "unserved_price")
WP9_OBJECTIVE = (2.0, 0.5, 0.0, 0.0)   # protocol.objective at quality weight 0
METRICS = ("total_cost_usd", "steps", "mean_violation", "mean_jain", "mean_quality_loss", "ai_shed_request_share")


def py_sum(terms: list[np.ndarray]) -> np.ndarray:
    """Python's built-in sum of floats, element by element. Since 3.12 it
    compensates (Neumaier); protocol.objective's J is such a sum, so plain
    addition can differ from it in the last bit and flip a near-tie."""
    s, c = np.asarray(terms[0], float).copy(), np.zeros(len(terms[0]))
    for x in terms[1:]:
        x = np.asarray(x, float)
        t = s + x
        with np.errstate(invalid="ignore"):
            c += np.where(np.abs(s) >= np.abs(x), (s - t) + x, (x - t) + s)
        s = t
    return np.where((c != 0) & np.isfinite(c), s + c, s)


def score(df: pd.DataFrame, w: tuple[float, float, float, float]) -> np.ndarray:
    """A specification's objective (absolute scale) on run rows, its terms in
    protocol.objective's order and summed as it sums them; at WP9's weights it
    is WP9's J exactly (the added terms are exact zeros)."""
    v, f, q, u = w
    return py_sum([(df.total_cost_usd / (df.steps * TENANTS) / COST_SCALE).to_numpy(float),
                   (v * df.mean_violation).to_numpy(float), (f * (1.0 - df.mean_jain)).to_numpy(float),
                   (q * df.mean_quality_loss).to_numpy(float), (u * df.ai_shed_request_share).to_numpy(float)])


def best(table: np.ndarray, configs: np.ndarray, cols: list[int]) -> int | None:
    """protocol.best_config on a (configs x workloads) score table: lowest mean
    over `cols` among the rows finite on every column, ties to the lower
    configuration index (rows are in ascending config order)."""
    if not cols or table.shape[0] == 0:
        return None
    means = np.array([np.mean(row[cols]) for row in table])
    return int(configs[int(np.argmin(means))])


def select(tuning: pd.DataFrame, classes: dict[str, str], w: tuple) -> dict[str, dict]:
    """protocol.select's rule on re-scored tuning rows: {arm: {"overall":
    config, "per_class": {class: config}}}; a configuration with a non-finite
    score on any tuning workload is out in every scope."""
    df = tuning.assign(s=score(tuning, w))
    workloads = sorted(classes)
    pos = {c: [i for i, wl in enumerate(workloads) if classes[wl] == c] for c in sorted(set(classes.values()))}
    out = {}
    for arm, part in df.groupby("arm", sort=True):
        wide = part.pivot_table(index="config", columns="workload", values="s", aggfunc="first", dropna=False)
        wide = wide.reindex(columns=workloads).sort_index()
        table = wide.to_numpy(float)
        keep = np.isfinite(table).all(axis=1)
        table, configs = np.ascontiguousarray(table[keep]), wide.index.to_numpy()[keep]
        out[arm] = {"overall": best(table, configs, list(range(len(workloads)))),
                    "per_class": {c: k for c, idx in pos.items() if (k := best(table, configs, idx)) is not None}}
    return out


def chosen_config(sel: dict, klass: str, granularity: str) -> int | None:
    """The configuration a scope assigns to an evaluation workload of `klass`
    (per class falls back to the overall one, as protocol.evaluation_plan)."""
    if granularity == "per_class" and klass in sel["per_class"]:
        return sel["per_class"][klass]
    return sel["overall"]


@dataclass(frozen=True)
class Campaign:
    plant: str
    budget: str
    path: Path
    tuned: dict
    tuning: pd.DataFrame
    evaluation: pd.DataFrame   # WP9's evaluation rows and the ones run here, one per (arm, config, workload)

    def classes(self, half: str) -> dict[str, str]:
        return {w: c for c, names in self.tuned["classes"][half].items() for w in names}

    @property
    def journal(self) -> Path:
        return OUT / f"{self.path.name}.journal.jsonl"

    @property
    def extra(self) -> Path:
        """The runs made here, compact and committed (the journal is not)."""
        return OUT / f"{self.path.name}.eval.csv.gz"


def find(plant: str, budget: str, root: Path = WP9, mix: str = "own") -> Path:
    pattern = re.compile(rf"plant{plant}_{mix}_large_s120_{budget}_[0-9a-f]{{8}}$")
    found = [d for d in sorted(root.glob(f"plant{plant}_{mix}_*")) if pattern.match(d.name) and
             (d / "tuned.json").exists()]
    if len(found) != 1:
        raise SystemExit(f"need one finished WP9 campaign (plant {plant}, {mix} mix, budget {budget}) in {root}; "
                         f"found {len(found)}: run research/run_queue.py")
    return found[0]


def journal_rows(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return pd.DataFrame([r for r in rows if not r.get("error")])


def load(plant: str, budget: str, root: Path = WP9) -> Campaign:
    path = find(plant, budget, root)
    tuned = json.loads((path / "tuned.json").read_text(encoding="utf-8"))
    runs = pd.read_csv(path / "runs.csv.gz", float_precision="round_trip")
    runs["error"] = runs["error"].fillna("").astype(str) if "error" in runs.columns else ""
    tuning = runs[runs.phase == "tuning"].copy()
    own = runs[(runs.phase == "evaluation") & (runs.error == "")]
    journal, compact = OUT / f"{path.name}.journal.jsonl", OUT / f"{path.name}.eval.csv.gz"
    extra = journal_rows(journal) if journal.exists() else (
        pd.read_csv(compact, float_precision="round_trip") if compact.exists() else pd.DataFrame())
    cols = ["arm", "config", "workload", *METRICS]
    evaluation = pd.concat([own[cols], extra[cols]] if len(extra) else [own[cols]], ignore_index=True)
    evaluation["config"] = evaluation.config.astype(int)
    evaluation = evaluation.drop_duplicates(["arm", "config", "workload"], keep="first").reset_index(drop=True)
    return Campaign(plant, budget, path, tuned, tuning, evaluation)


def check_against_wp9(c: Campaign) -> None:
    """Under WP9's own objective the re-selection must be tuned.json's."""
    sel = select(c.tuning, c.classes("tuning"), WP9_OBJECTIVE)
    for arm, entry in c.tuned["arms"].items():
        if "configs" not in entry:
            continue
        want = {"overall": entry["overall"]["config"] if entry["overall"] else None,
                "per_class": {k: v["config"] for k, v in entry["per_class"].items() if v}}
        if sel.get(arm) != want:
            raise SystemExit(f"{c.path.name}: re-selection of {arm} under WP9's objective is {sel.get(arm)}, "
                             f"tuned.json says {want}; the scoring here is not WP9's")


def objectives() -> list[tuple]:
    """The specifications' distinct objectives (weights in OBJECTIVE order)."""
    return list(itertools.product(*(CHOICES[k] for k in OBJECTIVE)))


def needed(c: Campaign) -> list[dict]:
    """Every (arm, config, evaluation workload) some specification scores and
    the campaign's evaluation rows lack."""
    have = set(zip(c.evaluation.arm, c.evaluation.config, c.evaluation.workload))
    classes = c.classes("evaluation")
    todo = {}
    for w in objectives():
        for arm, sel in select(c.tuning, c.classes("tuning"), w).items():
            for wl, klass in classes.items():
                for g in CHOICES["granularity"]:
                    config = chosen_config(sel, klass, g)
                    if config is not None and (arm, config, wl) not in have:
                        todo[(arm, config, wl)] = {"arm": arm, "config": config, "workload": wl, "class": klass}
    return [todo[k] for k in sorted(todo)]


def tune_jobs(c: Campaign, plan: list[dict]) -> list[dict]:
    """tune.py's evaluation jobs for `plan`: the campaign's own settings, run
    specs (seeds) and sampled configurations."""
    import tune

    s = c.tuned["settings"]
    args = argparse.Namespace(plant=s["plant"], mix=s["mix"], steps=s["steps"], cluster_size=s["cluster_size"])
    runs = tune.build_runs(args, sorted({p["workload"] for p in plan}))
    configs = {arm: entry["configs"] for arm, entry in c.tuned["arms"].items() if "configs" in entry}
    return [{"budget": s["budget"], "quality_weight": s["quality_weight"], "phase": "evaluation", **p,
             "params": configs[p["arm"]][p["config"]], "run": runs[p["workload"]]} for p in plan]


def recheck(c: Campaign, arms_to_check: tuple[str, ...] = ("rules", "joint")) -> None:
    """Re-run one of WP9's own evaluation runs per arm named through
    tune_jobs and require identical metrics: the jobs built here are WP9's."""
    import tune

    runs = pd.read_csv(c.path / "runs.csv.gz", float_precision="round_trip")
    own = runs[(runs.phase == "evaluation") & runs.error.isna()]
    for arm in arms_to_check:
        r = own[own.arm == arm].sort_values("workload").iloc[0]
        job = tune_jobs(c, [{"arm": arm, "config": int(r.config), "workload": r.workload, "class": r["class"]}])[0]
        again = tune.run_job(job)
        diff = [k for k in ("J", *METRICS) if again.get(k) != r[k] and not (pd.isna(again.get(k)) and pd.isna(r[k]))]
        if again.get("error") or diff:
            raise SystemExit(f"{c.path.name}: re-running WP9's {arm} config {int(r.config)} on {r.workload} "
                             f"gave different {diff or again.get('error')}; not running anything")


def same_plant(camps: dict[tuple[str, str], Campaign]) -> None:
    """An asymmetric specification pairs a plant's two budget campaigns: they
    must have run on the same plant constants."""
    for p in CHOICES["plant"]:
        digests = {b: camps[(p, b)].tuned["settings"].get("plant_digest") for b in CHOICES["joint_budget"]}
        if None in digests.values() or len(set(digests.values())) != 1:
            raise SystemExit(f"plant {p}: the budget campaigns ran on different or unrecorded plant constants "
                             f"{digests}; rerun the older one")


def campaigns(root: Path = WP9) -> dict[tuple[str, str], Campaign]:
    out = {(p, b): load(p, b, root) for p in CHOICES["plant"] for b in CHOICES["joint_budget"]}
    same_plant(out)
    for c in out.values():
        check_against_wp9(c)
    return out


def effects(camps: dict[tuple[str, str], Campaign]) -> tuple[pd.DataFrame, np.ndarray, list[str]]:
    """(specifications, D, evaluation workloads): D[s, w] = score(comparator)
    - score(joint) on workload w under specification s."""
    specs = enumerate_specs(CHOICES)
    workloads = sorted(next(iter(camps.values())).classes("evaluation"))
    sel_cache: dict[tuple, dict] = {}
    score_cache: dict[tuple, dict] = {}

    def sel(key, w):
        if (key, w) not in sel_cache:
            sel_cache[(key, w)] = select(camps[key].tuning, camps[key].classes("tuning"), w)
        return sel_cache[(key, w)]

    def scores(key, w):
        if (key, w) not in score_cache:
            e = camps[key].evaluation
            score_cache[(key, w)] = dict(zip(zip(e.arm, e.config, e.workload), score(e, w)))
        return score_cache[(key, w)]

    d = np.empty((len(specs), len(workloads)))
    for i, s in enumerate(specs.to_dict("records")):
        w = tuple(s[k] for k in OBJECTIVE)
        pair = []
        for arm, budget in (("joint", s["joint_budget"]), (s["comparator"], s["comparator_budget"])):
            key = (s["plant"], budget)
            classes = camps[key].classes("evaluation")
            picks, table = sel(key, w)[arm], scores(key, w)
            try:
                pair.append(np.array([table[(arm, chosen_config(picks, classes[wl], s["granularity"]), wl)]
                                      for wl in workloads]))
            except KeyError as err:
                raise SystemExit(f"{camps[key].path.name}: no evaluation run {err}; run `full_curve.py run`")
        joint, comp = pair
        d[i] = np.log(comp) - np.log(joint) if s["scale"] == "log" else comp - joint
    return specs, d, workloads


def write_curve(camps: dict, out_md: Path = HERE / "FULL_SPEC_CURVE.md", out_png: Path = HERE / "full_spec_curve.png"):
    specs, d, workloads = effects(camps)
    cols = list(CHOICES)
    c = curve_from_matrix(specs, d)
    test = sign_flip_test(d)
    plot(c, cols, out_png, f"Joint vs per-layer comparators, {len(specs)} specifications, arms re-selected "
                           "per specification (exploratory)")
    ranked = c.specs.assign(effect=c.effect, lo=c.lo, hi=c.hi)
    by = {col: ranked.groupby(col).effect.median().to_dict() for col in cols}
    sym = ranked.joint_budget == ranked.comparator_budget
    extra = sum(len(journal_rows(x.journal)) for x in camps.values())
    L = ["# Specification curve, arms re-selected per specification (WP5 step 2; exploratory)", "",
         "Generated by `research/speccurve/full_curve.py` from WP9's full campaigns "
         f"({', '.join(sorted(x.path.name for x in camps.values()))}). Every arm's configuration is selected "
         "under the specification's own objective from WP9's 64 sampled configurations on the 120 tuning "
         f"workloads (WP9's own selection reproduced first), then scored on the {len(workloads)} evaluation "
         f"workloads; {extra} evaluation runs beyond WP9's were run for it. Plot: `full_spec_curve.png`.", "",
         f"**{len(specs)} specifications, {len(workloads)} paired workloads each.** Median effect "
         f"{test['median_effect']:+.4f}; significant for the joint planner in "
         f"{test['share_significant_positive']:.0%} of specifications, against it in "
         f"{test['share_significant_negative']:.0%}. Whole-curve sign-flip test: p = "
         f"{test['p_median_positive']:.4f} (joint better), {test['p_median_negative']:.4f} (joint worse); "
         f"share significant for joint p = {test['p_share_positive']:.4f}, against p = "
         f"{test['p_share_negative']:.4f}.", "",
         f"Budget symmetric (both arms in the same mode): median effect {np.median(ranked.effect[sym]):+.4f}; "
         f"asymmetric: {np.median(ranked.effect[~sym]):+.4f}.", "",
         "## Median effect by choice", "", "| choice | level | median effect |", "|---|---|---:|"]
    for col in cols:
        L += [f"| {col} | {level} | {value:+.4f} |" for level, value in by[col].items()]
    L += ["", "## The curve's ends (10 lowest and 10 highest effects)", ""]
    full = report(c, test, "joint", "comparator", cols).split("\n")
    header, rows = full[:4], full[4:4 + len(specs)]
    L += header + rows[:10] + ["| ... | ... |" + " ... |" * len(cols)] + rows[-10:]
    L += ["", "Positive effect = the joint planner scores better than the comparator on that specification. "
          "WP9 sampled no quality knob, so quality-price specifications re-select among quality-blind "
          "configurations.", ""]
    out_md.write_text("\n".join(L), encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    ranked.to_csv(OUT / "curve_effects.csv.gz", index=False)
    return L


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("step", choices=("plan", "run", "curve"))
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--stall-timeout", type=float, default=3600.0)
    args = ap.parse_args(argv)
    camps = campaigns()
    if args.step == "curve":
        print("\n".join(write_curve(camps)[:30]))
        return 0
    total = 0
    for key, c in sorted(camps.items()):
        plan = needed(c)
        total += len(plan)
        per_arm = pd.Series([p["arm"] for p in plan], dtype=object).value_counts().sort_index().to_dict()
        print(f"{c.path.name}: {len(plan)} evaluation runs needed beyond WP9's {per_arm}", flush=True)
        if args.step == "run" and plan:
            import tune

            recheck(c)
            OUT.mkdir(parents=True, exist_ok=True)
            rows = tune.execute(tune_jobs(c, plan), args.workers, c.path.name, args.stall_timeout, c.journal)
            failed = [r for r in rows if r.get("error")]
            if failed:
                print(f"  {len(failed)} runs failed: {failed[0]['error']}", file=sys.stderr)
                return 1
        if args.step == "run" and c.journal.exists():
            keep = ["arm", "config", "workload", "class", "J", *METRICS]
            journal_rows(c.journal)[keep].sort_values(["arm", "config", "workload"]).to_csv(c.extra, index=False)
    print(f"total: {total} evaluation runs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
