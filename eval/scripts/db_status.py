"""Quick status of a results DB: run counts by status, per-system valid counts.

    python scripts/db_status.py results/raw_sim_v2.duckdb
"""
import sys

import duckdb

db = sys.argv[1] if len(sys.argv) > 1 else "results/raw_sim_v2.duckdb"
con = duckdb.connect(db, read_only=True)
print(con.sql("select status, count(*) n from runs group by 1 order by 1").fetchall())
print(con.sql("select system, count(*) n from runs where status='valid' group by 1 order by 1").fetchall())
con.close()
