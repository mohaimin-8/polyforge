"""Each script the chain and the queue run as `python <script>` loads in a fresh interpreter, with
every module it imports anywhere in its body (pytest puts each test folder on sys.path, so an import
that works under the tests can still fail when the script runs alone, as calibrate_service.py's did)."""

import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS = ("after_wp11.py", "calibrate_service.py", "install.py", "live.py", "main_runs.py", "pilot.py",
           "run_pilot.py", "smoke_live.py")

CHECK = """
import ast, importlib, runpy, sys
path = sys.argv[1]
runpy.run_path(path)   # module level only (the script's own sys.path set-up), not its main
names = set()
for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
    if isinstance(node, ast.Import):
        names.update(alias.name for alias in node.names)
    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module and node.module != "__future__":
        names.add(node.module)
for name in sorted(names):
    importlib.import_module(name)
"""


@pytest.mark.parametrize("script", SCRIPTS)
def test_the_script_and_every_module_it_imports_load_in_a_fresh_interpreter(script):
    proc = subprocess.run([sys.executable, "-c", CHECK, script], cwd=HERE, capture_output=True, text=True,
                          timeout=120)
    assert proc.returncode == 0, (proc.stderr.strip().splitlines() or ["no output"])[-1]
