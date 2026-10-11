"""One command: start the whole of PolyForge, verify every layer and every link, show the results.

    python scripts/demo_all.py                # everything, about 12 minutes
    python scripts/demo_all.py --pause        # wait for Enter between parts (presenting)
    python scripts/demo_all.py --no-cluster   # skip Kubernetes (no Docker needed), about 2 minutes
    python scripts/demo_all.py --reproduce    # also the 20-minute byte-identity gate

  A. Platform services       control plane + AI gateway + three mock model tiers on
                             this machine; 7 self-checking acts (scripts/demo_platform.py)
  B. Controller comparison   PolyForge against fair HPA, KEDA and the layered stack,
                             live in the simulator; J per workload; a knob trace (PNG)
  C. Kubernetes control loop kind cluster with operator, planner, PostgreSQL and NATS;
                             every link from traffic to Deployment verified while k6
                             load runs (scripts/demo_cluster.py); needs Docker
  D. Evidence                Go tests, simulator tests, the evidence site
                             (tmp/site/index.html), optionally reproduce.py

Ends with one results board and opens the knob trace and the evidence site.
Everything it starts is stopped; the kind cluster is deleted.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
EVAL = REPO / "eval"
WORK = REPO / "tmp" / "demo"
SITE = REPO / "tmp" / "site"
TRACE_PNG = WORK / "agentic_trace.png"
TREATMENT = "jcac_converged"
LAYERED = "jcac_nojoint_v2_tuned"
RULE = "=" * 78


@dataclass(frozen=True)
class PartResult:
    name: str
    status: str  # PASS | FAIL | SKIP
    summary: str


def parse_go_test(output: str) -> tuple[int, list[str]]:
    ok = sum(1 for line in output.splitlines() if line.startswith("ok  "))
    failed = [line.split("\t")[1] for line in output.splitlines()
              if line.startswith("FAIL\t") and len(line.split("\t")) > 1]
    return ok, failed


def summarize_sweep(comparisons) -> tuple[int, int, int]:
    """(workloads where PolyForge has the lowest J, workloads where it beats the
    layered stack, workloads) -- counted, never asserted."""
    wins = beats_layered = 0
    for c in comparisons:
        j = {o.system: o.metrics["J"] for o in c.outcomes}
        wins += j[TREATMENT] == min(j.values())
        beats_layered += j[TREATMENT] < j[LAYERED]
    return wins, beats_layered, len(comparisons)


def render_board(parts: list[PartResult]) -> str:
    width = max(len(p.name) for p in parts)
    lines = [RULE, "PolyForge end-to-end demonstration: results", ""]
    lines += [f"  {p.status:4s}  {p.name:{width}s}  {p.summary}" for p in parts]
    return "\n".join(lines + [RULE])


def exit_code(parts: list[PartResult]) -> int:
    return 1 if any(p.status == "FAIL" for p in parts) else 0


def _banner(title: str, pause: bool) -> None:
    if pause:
        try:
            input(f"\n[Enter] to start {title} ... ")
        except EOFError:  # no keyboard attached: carry on rather than crash
            pass
    print(f"\n{RULE}\n{title}\n{RULE}")


def _load(name: str):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # @dataclass resolves its module here
    spec.loader.exec_module(module)
    return module


def part_a() -> PartResult:
    checks = _load("demo_platform").run_demo()
    passed = sum(1 for _, ok, _ in checks.results if ok)
    total = len(checks.results)
    status = "PASS" if checks.exit_code() == 0 else "FAIL"
    return PartResult("A. Platform services", status,
                      f"{passed}/{total} checks: tenants, isolation, cache, per-tenant cache, "
                      "tier knob, classifier, metrics")


def part_b() -> PartResult:
    sys.path.insert(0, str(EVAL))
    from harness import demo  # noqa: E402 (needs eval/ on the path)

    comparison = demo.compare()
    print(demo.render_table(comparison))
    print("\n  " + demo.render_takeaway(comparison))
    print("\nJ for each workload class ('<' = lowest J):\n")
    comparisons = demo.sweep()
    print(demo.render_sweep(comparisons))
    WORK.mkdir(parents=True, exist_ok=True)
    demo.plot_trace(demo.compare(workload="agentic", collect_rows=True), TRACE_PNG)
    print(f"\n  knob trace (agentic traffic): {TRACE_PNG}")
    wins, beats, n = summarize_sweep(comparisons)
    return PartResult("B. Controller comparison", "PASS",
                      f"lowest J in {wins}/{n} workloads, beats the layered stack in {beats}/{n} "
                      "(one seed; the tested result is RESULTS_FAIR_J.md)")


def part_c(keep_cluster: bool, rebuild: bool) -> PartResult:
    dc = _load("demo_cluster")
    result = dc.run(keep_cluster=keep_cluster, rebuild=rebuild)
    print("\n" + dc.render(result))
    if result.skipped:
        return PartResult("C. Kubernetes control loop", "SKIP", result.skipped)
    statuses = [status for _, _, status, _ in result.rows]
    verified = statuses.count("PASS")
    warned = statuses.count("WARN")
    m = result.metrics
    measured = (f"; cost ${m.get('total_cost_usd', 0):.2f}, SLO violation {m.get('mean_violation')}"
                if m else "")
    status = "PASS" if verified + warned == len(statuses) else "FAIL"
    note = f" ({warned} warning)" if warned else ""
    return PartResult("C. Kubernetes control loop", status,
                      f"{verified}/{len(statuses)} links verified{note}{measured}")


def _run(cmd: list[str], cwd: Path = REPO) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def part_d(reproduce: bool) -> PartResult:
    print("  go test ./...  (about 40 s)")
    go = _run(["go", "test", "./..."])
    ok, failed = parse_go_test(go.stdout + go.stderr)
    print(f"    {ok} Go packages passed" + (f", FAILED: {', '.join(failed)}" if failed else ""))
    print("  python -m pytest research/jcac_sim -q  (204 tests, about 3 minutes)")
    sim = _run([sys.executable, "-m", "pytest", "research/jcac_sim", "-q"])
    sim_line = (sim.stdout.strip().splitlines() or ["no output"])[-1]
    print(f"    {sim_line}")
    site = _run([sys.executable, "scripts/build_pages.py", "--out", str(SITE)])
    print(f"    evidence site: {(site.stdout.strip().splitlines() or ['?'])[0]}")
    parts = [f"Go: {ok} packages passed", f"simulator tests: {sim_line}", f"site: {SITE / 'index.html'}"]
    good = go.returncode == 0 and sim.returncode == 0 and site.returncode == 0
    if reproduce:
        print("  python scripts/reproduce.py  (about 20 minutes)")
        gate = _run([sys.executable, "scripts/reproduce.py"])
        tail = gate.stdout.strip().splitlines()[-7:]
        print("\n".join(f"    {line}" for line in tail))
        records = next((line.split(None, 1)[1] for line in tail if line.strip().startswith("records")), "?")
        parts.append(f"reproduce: {records.strip()}")
        good = good and gate.returncode == 0
    return PartResult("D. Evidence", "PASS" if good else "FAIL", "; ".join(parts))


def _open(path: Path) -> None:
    if not path.exists():
        return
    if hasattr(os, "startfile"):
        os.startfile(path)  # noqa: S606 (a local file the demo just wrote)
    else:
        webbrowser.open(path.as_uri())


def _guarded(name: str, fn, *args) -> PartResult:
    try:
        return fn(*args)
    except Exception as err:  # one part failing must not stop the rest of the demo
        print(f"\n  {name} stopped: {type(err).__name__}: {err}")
        return PartResult(name, "FAIL", f"stopped: {type(err).__name__}: {err}"[:120])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pause", action="store_true", help="wait for Enter between parts")
    ap.add_argument("--no-cluster", action="store_true", help="skip Part C (no Docker needed)")
    ap.add_argument("--reproduce", action="store_true", help="add the 20-minute reproduce.py gate")
    ap.add_argument("--keep-cluster", action="store_true", help="leave the kind cluster up afterwards")
    ap.add_argument("--rebuild-images", action="store_true", help="rebuild the Docker images first")
    ap.add_argument("--no-open", action="store_true", help="do not open the plot and site at the end")
    args = ap.parse_args(argv)

    started = time.time()
    print(__doc__.split("\n\n")[0])
    parts = []
    _banner("Part A. Platform services", args.pause)
    parts.append(_guarded("A. Platform services", part_a))
    _banner("Part B. Controller comparison (simulator, live)", args.pause)
    parts.append(_guarded("B. Controller comparison", part_b))
    if args.no_cluster:
        parts.append(PartResult("C. Kubernetes control loop", "SKIP", "--no-cluster"))
    else:
        _banner("Part C. Kubernetes control loop (about 10 minutes)", args.pause)
        parts.append(_guarded("C. Kubernetes control loop", part_c, args.keep_cluster,
                              args.rebuild_images))
    _banner("Part D. Evidence", args.pause)
    parts.append(_guarded("D. Evidence", part_d, args.reproduce))

    print("\n" + render_board(parts))
    minutes, seconds = divmod(int(time.time() - started), 60)
    print(f"total time: {minutes} min {seconds} s")
    if not args.no_open:
        _open(TRACE_PNG)
        _open(SITE / "index.html")
    return exit_code(parts)


if __name__ == "__main__":
    sys.exit(main())
