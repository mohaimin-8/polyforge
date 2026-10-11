"""demo_all.py runs everything; the parts that decide what the board says are tested here.

Run: python -m pytest scripts/test_demo_all.py -q   (pytest only: the harness
is imported lazily, inside the part that runs it)
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location(
    "demo_all", Path(__file__).resolve().parent / "demo_all.py")
da = importlib.util.module_from_spec(spec)
sys.modules["demo_all"] = da  # @dataclass resolves its module here
spec.loader.exec_module(da)


GO_TEST = """ok  \tpolyforge/internal/auth\t0.412s
?   \tpolyforge/cmd/operator\t[no test files]
--- FAIL: TestSomething (0.00s)
FAIL\tpolyforge/internal/platform\t1.20s
ok  \tpolyforge/internal/operator/planner\t(cached)
FAIL
"""


def test_go_test_output_counts_packages():
    assert da.parse_go_test(GO_TEST) == (2, ["polyforge/internal/platform"])
    assert da.parse_go_test("ok  \tpolyforge/x\t0.1s\n") == (1, [])


def _outcome(system, j):
    return SimpleNamespace(system=system, metrics={"J": j})


def test_sweep_summary_counts_polyforge_wins_honestly():
    rows = [
        [_outcome("hpa_fair", 0.3), _outcome("jcac_nojoint_v2_tuned", 1.1), _outcome("jcac_converged", 0.36)],
        [_outcome("hpa_fair", 0.8), _outcome("jcac_nojoint_v2_tuned", 3.1), _outcome("jcac_converged", 0.7)],
        [_outcome("hpa_fair", 0.5), _outcome("jcac_nojoint_v2_tuned", 0.4), _outcome("jcac_converged", 0.5)],
    ]
    comparisons = [SimpleNamespace(outcomes=r) for r in rows]
    assert da.summarize_sweep(comparisons) == (1, 2, 3)


def test_board_lists_every_part_and_fails_on_any_failure():
    parts = [da.PartResult("A. Platform", "PASS", "13/13 checks"),
             da.PartResult("C. Kubernetes", "SKIP", "Docker is not running"),
             da.PartResult("D. Evidence", "FAIL", "1 Go package failed")]
    board = da.render_board(parts)
    board.encode("ascii")  # cp1252 consoles
    for p in parts:
        assert p.name in board and p.summary in board
    assert da.exit_code(parts) == 1
    assert da.exit_code(parts[:2]) == 0  # a skipped part is not a failure
