"""The Alibaba ETL must not silently collapse a trace into one tenant.

Audit 2026-09-26 (LOW): `normalize` maps a job to its deployment unit only
for the synthetic job-name shape. Real v2018 job names (`j_<id>`) match no
unit, and every job fell into one `unmapped_j` tenant, a single-tenant trace
under a multi-tenant name. No committed record reads this ETL's output, but
the next one would have.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etl_alibaba_v2018 as etl  # noqa: E402


def test_the_synthetic_trace_maps_mostly_to_real_tenants():
    # The synthetic generator deliberately references some units its
    # container table lacks (~15% of rows); that stays under the ceiling.
    tasks, containers = etl.synthetic_raw(42)
    frame = etl.normalize(tasks, containers)
    assert frame["tenant_id"].str.startswith("unmapped_").mean() <= etl.MAX_UNMAPPED_SHARE
    assert frame["tenant_id"].nunique() > 10


def test_real_shaped_job_names_are_refused_not_collapsed():
    tasks, containers = etl.synthetic_raw(42)
    real_shaped = tasks.assign(job_name=[f"j_{i}" for i in range(len(tasks))])
    with pytest.raises(ValueError, match="unmapped"):
        etl.normalize(real_shaped, containers)
