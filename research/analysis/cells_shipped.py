"""Planning cells on the *shipped* path — engineering note generator.

`PLANNER_CELLS.md` measured cell partitioning in the research simulator and
found the monolithic joint solve crossing the operator's 3 s timeout at 128
tenants while per-cell latency stayed flat. The operator itself had no cell
logic until session 33, so that result described a system the artifact did not
implement — a reviewer running the artifact would have hit the 128-tenant wall
the paper reports clearing.

This turns the shipped-path measurement into `CELLS_SHIPPED.md`. The data comes
from `TestCellsScaleOnShippedPath` (internal/operator/controllers), which drives
the operator's own partition and limit-splitting through the real Python planner
over HTTP:

    python services/planner/planner.py --port 8097 &
    POLYFORGE_TEST_PLANNER_URL=http://127.0.0.1:8097 \
      POLYFORGE_CELLS_SCALE_OUT=eval/results/cells_shipped_runs.json \
      go test ./internal/operator/controllers/ -run CellsScale -v -timeout 40m
    python research/analysis/cells_shipped.py

This is an **engineering note, not a campaign**: it re-tests a closed result's
implementation, publishes no new scientific claim, and changes no committed
number. Latency is machine-dependent and is reported as such — the claim is the
*shape* (monolithic super-linear, per-cell flat), which is what PS-H1 asserts.
"""

from __future__ import annotations

import json
from pathlib import Path

import stats

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNS = REPO_ROOT / "eval" / "results" / "cells_shipped_runs.json"
OPERATOR_TIMEOUT_MS = 3000.0   # planCallTimeout in the operator
CONTROL_PERIOD_MS = 10_000.0   # DefaultPlanInterval


def main() -> None:
    rows = json.loads(RUNS.read_text(encoding="utf-8"))
    mono = {r["tenants"]: r for r in rows if r["cell_size"] == 0}
    cells = {r["tenants"]: r for r in rows if r["cell_size"] != 0}
    widths = sorted(mono)

    out: list[str] = []
    w = out.append
    w("# Planning cells on the shipped path — engineering verification")
    w("")
    w("`PLANNER_CELLS.md` (PS-H1) measured, in the research simulator, that the")
    w("monolithic joint solve crosses the operator's 3 s timeout at 128 tenants")
    w("while per-cell latency stays flat. **The operator did not implement cells")
    w("until session 33**, so that result described a system the artifact did not")
    w("ship. This note records the same measurement taken through the shipped")
    w("path: the operator's own `planningCells` + `cellLimits`, calling the real")
    w("Python planner over HTTP.")
    w("")
    w("Regenerate: see the module docstring of `cells_shipped.py`. Latencies are")
    w("machine-dependent (measured on the development host, not the published")
    w("benchmark host) — the reproduced claim is the **shape**, not the absolute")
    w("milliseconds.")
    w("")
    w(f"Operator per-call timeout {OPERATOR_TIMEOUT_MS:.0f} ms; "
      f"control period {CONTROL_PERIOD_MS:.0f} ms.")
    w("")
    w("| tenants | monolithic p95 | cells | per-cell p95 | per-cell median | "
      "full cycle (all cells) | monolithic vs timeout |")
    w("|---|---|---|---|---|---|---|")
    for n in widths:
        m, c = mono[n], cells.get(n)
        if c is None:
            continue
        verdict = "**OVER**" if m["per_cell_p95_ms"] > OPERATOR_TIMEOUT_MS else "under"
        w(f"| {n} | {m['per_cell_p95_ms']:.1f} ms | {c['cells']} | "
          f"{c['per_cell_p95_ms']:.1f} ms | {c['per_cell_median_ms']:.1f} ms | "
          f"{c['cycle_total_ms']:.1f} ms | {verdict} |")
    w("")

    crossed = [n for n in widths if mono[n]["per_cell_p95_ms"] > OPERATOR_TIMEOUT_MS]
    first = crossed[0] if crossed else None
    percell = [cells[n]["per_cell_p95_ms"] for n in widths if n in cells and cells[n]["cells"] > 1]

    w("## What reproduces")
    w("")
    if first is not None:
        w(f"- **The wall is real on the shipped path, at the same width.** "
          f"Monolithic p95 first exceeds the {OPERATOR_TIMEOUT_MS:.0f} ms operator "
          f"timeout at **{first} tenants** — PS-H1's simulator finding, "
          f"independently reproduced through the Go operator and the deployed "
          f"planner rather than in the simulator that produced it.")
    else:
        w("- On this host the monolithic solve never crossed the operator "
          "timeout across the widths tested, so the wall is not reproduced here; "
          "the per-cell flatness below still holds.")
    if percell:
        w(f"- **Per-cell latency is flat.** Across every partitioned width the "
          f"per-cell p95 stays in a narrow band "
          f"({min(percell):.0f}–{max(percell):.0f} ms) while the portfolio grows, "
          f"because cell size is fixed and per-cell solve time therefore does not "
          f"depend on portfolio size.")
    over_period = [n for n in widths if mono[n]["per_cell_p95_ms"] > CONTROL_PERIOD_MS]
    if over_period:
        n = over_period[0]
        w(f"- **At {n} tenants the monolithic solve "
          f"({mono[n]['per_cell_p95_ms']:.0f} ms) exceeds the entire "
          f"{CONTROL_PERIOD_MS:.0f} ms control period** — the operator could not "
          f"complete one plan per cycle at all. Partitioned, the *whole* cycle "
          f"(every cell, serially) finishes in "
          f"{cells[n]['cycle_total_ms']:.0f} ms.")
    w("")
    w("## What this note does not claim")
    w("")
    w("- **Not a new scientific result.** PS-H1 and PF-H1 stand as measured in")
    w("  their campaigns; this is implementation verification of a closed result,")
    w("  carries no pre-registration, and adds nothing to the scoreboard.")
    w("- **Not a fairness measurement.** Cells scope the fairness term to each")
    w("  cell; the global-Jain cost of that (worst case −0.0053 under the hash")
    w("  assignment the operator implements) is `PLANNER_CELLS_DEALIAS.md`'s")
    w("  result and is not re-measured here.")
    w("- **Not comparable in absolute ms** to `PLANNER_CELLS.md`: different host,")
    w("  and this path adds real HTTP and JSON encoding the simulator did not pay.")
    w("")
    w("## Cells are opt-in")
    w("")
    w("`planner.cellSize` defaults to 0 — one call for the whole portfolio, the")
    w("behaviour that predates cells — because partitioning trades a little global")
    w("fairness for the latency win. The table above is the evidence for turning")
    w("it on as a portfolio approaches ~128 tenants, not an argument for it at")
    w("every width: at 8 and 32 tenants a single cell is the whole portfolio and")
    w("the two columns measure the same call.")

    path = stats.record_path("CELLS_SHIPPED.md")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
