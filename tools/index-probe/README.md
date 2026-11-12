# index-probe

How much can per-knob control lose to joint control, for one decision of a
system you run? `index-probe` computes the **interaction index** of a
decision (the range of the anchored remainder over the box of candidates)
and checks the bound it gives (Theorem 1): per-knob search loses at most
R(x^D) − R(x^J) ≤ index. Definitions and proofs: `research/theory/INDEX_THEORY.md`
(drafts awaiting a second reader). Programme 2, WP23.

Requirements: Python 3.10+, nothing beyond the standard library;
`research/theory/interaction_index.py` must sit at its place in this
repository (the tool imports it).

## Mode 1: a log of decisions

One JSON object per line: the box (`values`, one list per knob), the
`anchor` (where the decision started), every evaluated candidate with its
objective value (`null` = infeasible), and optionally the system's `chosen`
point:

```json
{"id": "t03@17", "values": [[1, 2, 3], [64, 128], ["small", "mid"]], "anchor": [2, 128, "small"],
 "phi": [[[1, 64, "small"], 0.41], [[1, 64, "mid"], null]], "chosen": [3, 128, "small"]}
```

```bash
python index_probe.py log decisions.jsonl [--json report.json]
```

Per decision: index, quantile index (5%–95%), per-knob and joint choices,
the per-knob loss and its bound, whether the per-knob combination was
infeasible (needs repair: outside the bound, counted separately), and the
system's own gap to the joint optimum when `chosen` is given. The summary
counts Theorem 1 violations (the exit code is 1 if there are any) and
decisions logged incompletely (the index is then exact only over what was
logged).

## Mode 2: an objective and a box

```bash
python index_probe.py box --objective mymodule:phi --box box.json            # exact: every point
python index_probe.py box --objective mymodule:phi --box box.json --sample 59
```

`phi` is called with one point (a tuple) and returns a float or `None`;
`box.json` is `{"values": [[...], ...], "anchor": [...]}`. With `--sample m`
only the anchor, the axes and m random points are evaluated (Proposition
T5): the sampled range never exceeds the index, and it is at least the
5%–95% quantile range with probability ≥ 1 − 2·0.95^m (0.903 at m = 59).

## Worked examples (`examples/`)

**The T3 instance** (`tight.py`): two knobs, ρ = 1, η = 0.25.

```text
$ python tight.py && python ../index_probe.py box --objective tight:phi --box tight_box.json
  index 1.0, per-knob (1, 1), joint (0, 1), loss 0.75, bound 1.0
$ python ../index_probe.py box --objective tight:phi --box tight_box.json --sample 59
  sampled range 1.0, covers the quantile index with probability >= 0.903, 64 evaluations
```

Per-knob search loses ρ − η of an index ρ; as η → 0 the loss reaches the
index, so the bound cannot be improved.

**The gateway's joint planner** (`gateway_log.py`): ten control intervals of
the simulator's joint planner on one design point (agentic workload, whale
mix, large cluster), every tenant decision with all its candidates.

```text
$ python gateway_log.py --steps 10 && python ../index_probe.py log gateway_log.jsonl
  88 decisions; index mean 0.586, median 0.179, max 4.55; loss mean 0.038, max 1.72;
  per-knob and joint disagree on 6.8%; repair 0%; Theorem 1 checked 88, violated 0
```

**Not yet covered** (the plan asks for three systems): the fused routing
decision (RouteBalance, WP15) and the Kubernetes HPA + VPA pair (WP17) do
not exist in this repository yet; each needs a logger that writes the
format above.

## Tests

```bash
python -m pytest -q      # hand-made decisions, the T3 instance, a real planner log
```
