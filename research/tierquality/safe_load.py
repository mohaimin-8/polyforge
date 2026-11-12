"""Load the RouterBench DataFrames without trusting their pickles.

`pickle` can run arbitrary code while loading, so the unpickler below allows
only the pandas and numpy reconstruction functions these two files were
found to reference (opcode inspection, no execution) and refuses anything
else. The files are checked against the SHA-256 Hugging Face publishes.
"""

from __future__ import annotations

import hashlib
import io
import pickle
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
SHA256 = {  # https://huggingface.co/api/datasets/withmartian/routerbench/tree/main (2026-11-12)
    "routerbench_0shot.pkl": "ba4f77f19517610a707c374e99322d7750c30fc4ae7ff5527888595a1e65d36d",
    "routerbench_5shot.pkl": "fbd7d3d16fba2759a18fa0ad44409d3e0e92ba80d03d90827eed1a6d084c9ffe",
}
ALLOWED = {
    ("builtins", "slice"),
    ("numpy", "dtype"), ("numpy", "ndarray"),
    ("numpy.core.multiarray", "_reconstruct"), ("numpy.core.numeric", "_frombuffer"),
    ("numpy._core.multiarray", "_reconstruct"), ("numpy._core.numeric", "_frombuffer"),
    ("pandas.core.frame", "DataFrame"),
    ("pandas.core.internals.managers", "BlockManager"),
    ("pandas._libs.internals", "_unpickle_block"),
    ("pandas.core.indexes.base", "_new_Index"), ("pandas.core.indexes.base", "Index"),
    ("pandas.core.indexes.range", "RangeIndex"),
}


class _Restricted(pickle.Unpickler):
    def find_class(self, module: str, name: str):
        if (module, name) not in ALLOWED:
            raise pickle.UnpicklingError(f"refused global {module}.{name}")
        return super().find_class(module, name)


def load(name: str) -> pd.DataFrame:
    if name not in SHA256:
        raise ValueError(f"not a known RouterBench file: {name!r}")
    path = DATA / name
    blob = path.read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    if digest != SHA256[name]:
        raise ValueError(f"{name}: sha256 {digest} is not the published {SHA256[name]}")
    obj = _Restricted(io.BytesIO(blob)).load()
    if not isinstance(obj, pd.DataFrame):
        raise TypeError(f"{name}: expected a DataFrame, got {type(obj).__name__}")
    return obj
