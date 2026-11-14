"""The shared planner decision: standard library only, and the twin uses it."""

import ast
import sys
from pathlib import Path

import lattice
import twin
from service_plant import Plant

HERE = Path(__file__).resolve().parent
POD_MODULES = ("lattice.py", "service_plant.py")   # mounted into the planner pod (python, no numpy)


def test_the_pod_modules_import_only_the_standard_library():
    for name in POD_MODULES:
        tree = ast.parse((HERE / name).read_text(encoding="utf-8"))
        mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert mods <= set(sys.stdlib_module_names) | {"__future__"}, (name, mods)


def test_the_twins_planner_is_the_shared_decision():
    p = Plant()
    for joint in (True, False):
        for width in (1, 2, 4):
            for rate, r, c in ((5.0, 2, 0.5), (40.0, 3, 1.0), (90.0, 6, 1.0)):
                assert twin.Planner(joint, width).decide(p, rate, r, c) == lattice.decide(p, rate, r, c, width, joint)
