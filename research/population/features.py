"""Features of real LLM request streams, per window (programme 2, WP8 step 1).

Streams (all local, `research/traces/data/`):
  burstgpt/<model>/<log type>  BurstGPT v1.1, three files, seconds since start
  azure2024/conv, azure2024/code  Azure LLM inference trace 2024, one week each

Per non-overlapping window of 600 s and 3,600 s, from 10 s request counts:
mean rate; index of dispersion (variance / mean of the 10 s counts); the 99th
percentile 10 s rate over the mean; autocorrelation of the 10 s counts at
10 s, 60 s, 300 s (and 600 s in hour windows); and from the requests: mean,
median and 90th percentile of prompt and response tokens, and the share of
conversation traffic (BurstGPT's Log Type). Per stream: the daily amplitude of
hourly counts, and (BurstGPT's third file) requests per session.

The traces carry no tenant identity; WP8's tenants are a construction built
from these windows, and every output says so.

    python features.py      # writes data/windows_600s.csv.gz, data/windows_3600s.csv.gz, FEATURES.md
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
TRACES = HERE.parents[0] / "traces" / "data"
BIN_S = 10
WINDOWS = {600: (1, 6, 30), 3600: (1, 6, 30, 60)}  # window seconds -> ACF lags in bins
MIN_REQUESTS = 30  # a window with fewer requests has no meaningful dispersion; kept, flagged


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    burst = [str(TRACES / f"BurstGPT_{i}.csv") for i in (1, 2, 3)]
    con.execute(f"""
        CREATE VIEW burst AS
        SELECT CAST("Timestamp" AS DOUBLE) AS t, "Model" AS model,
               CAST("Request tokens" AS BIGINT) AS req, CAST("Response tokens" AS BIGINT) AS resp,
               "Log Type" AS log, "Session ID" AS session
        FROM read_csv({burst!r}, union_by_name = true, header = true)
        WHERE "Timestamp" IS NOT NULL""")
    for kind in ("conv", "code"):
        path = TRACES / "azure-llm" / f"AzureLLMInferenceTrace_{kind}_1week.csv"
        con.execute(f"""
            CREATE VIEW azure_{kind} AS
            WITH raw AS (SELECT epoch(CAST("TIMESTAMP" AS TIMESTAMPTZ)) AS ts,
                                CAST("ContextTokens" AS BIGINT) AS req,
                                CAST("GeneratedTokens" AS BIGINT) AS resp
                         FROM read_csv('{path.as_posix()}', header = true))
            SELECT ts - min(ts) OVER () AS t, req, resp, NULL::VARCHAR AS log FROM raw""")
    return con


def streams(con) -> dict[str, str]:
    """name -> SQL selecting (t, req, resp, log) for that stream."""
    out = {}
    for model, log in con.execute("SELECT DISTINCT model, log FROM burst WHERE model IS NOT NULL "
                                  "AND log IS NOT NULL ORDER BY 1, 2").fetchall():
        short = "conv" if log.lower().startswith("conversation") else "api"
        out[f"burstgpt/{model}/{short}"] = (f"SELECT t, req, resp, log FROM burst "
                                           f"WHERE model = '{model}' AND log = '{log}'")
    out["azure2024/conv"] = "SELECT t, req, resp, log FROM azure_conv"
    out["azure2024/code"] = "SELECT t, req, resp, log FROM azure_code"
    return out


def bin_counts(con, sql: str) -> np.ndarray:
    rows = con.execute(f"SELECT CAST(floor(t / {BIN_S}) AS BIGINT) AS b, count(*) FROM ({sql}) "
                       "GROUP BY b ORDER BY b").fetchnumpy()
    counts = np.zeros(int(rows["b"].max()) + 1)
    counts[rows["b"]] = rows["count_star()"]
    return counts


def count_features(counts: np.ndarray, window_s: int, lags: tuple) -> pd.DataFrame:
    """Rate, dispersion, p99 ratio and ACF per window, vectorised over windows."""
    per = window_s // BIN_S
    n = len(counts) // per
    x = counts[: n * per].reshape(n, per)
    mean = x.mean(axis=1)
    var = x.var(axis=1, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = {"window": np.arange(n), "start_s": np.arange(n) * window_s, "n": x.sum(axis=1),
               "rate_rps": mean / BIN_S, "dispersion": var / mean,
               "p99_ratio": np.percentile(x, 99, axis=1) / mean}
        dev = x - mean[:, None]
        denom = (dev ** 2).sum(axis=1)
        for lag in lags:
            out[f"acf_{lag * BIN_S}s"] = (dev[:, :-lag] * dev[:, lag:]).sum(axis=1) / denom
    return pd.DataFrame(out)


def token_features(con, sql: str, window_s: int) -> pd.DataFrame:
    return con.execute(f"""
        SELECT CAST(floor(t / {window_s}) AS BIGINT) AS win,
               avg(req) AS req_mean, quantile_cont(req, 0.5) AS req_p50, quantile_cont(req, 0.9) AS req_p90,
               avg(resp) AS resp_mean, quantile_cont(resp, 0.5) AS resp_p50, quantile_cont(resp, 0.9) AS resp_p90
        FROM ({sql}) GROUP BY win""").df().rename(columns={"win": "window"})


def daily_amplitude(counts: np.ndarray) -> pd.DataFrame:
    """Per full day: (max - min) / (2 mean) of hourly counts; days with no
    requests at all are dropped (coverage gaps in the trace)."""
    per_hour = 3600 // BIN_S
    hours = len(counts) // per_hour
    hourly = counts[: hours * per_hour].reshape(hours, per_hour).sum(axis=1)
    days = hours // 24
    h = hourly[: days * 24].reshape(days, 24)
    m = h.mean(axis=1)
    keep = m > 0
    return pd.DataFrame({"day": np.arange(days)[keep],
                         "amplitude": (h.max(axis=1) - h.min(axis=1))[keep] / (2.0 * m[keep]),
                         "requests": h.sum(axis=1)[keep]})


def session_lengths(con) -> pd.Series:
    return con.execute("SELECT count(*) AS n FROM burst WHERE session IS NOT NULL GROUP BY session").df()["n"]


def summarise(windows: dict[int, pd.DataFrame], amplitude: dict[str, pd.DataFrame], sessions: pd.Series) -> str:
    L = ["# Request-stream features, per window (programme 2, WP8 step 1)", "",
         "Generated by `research/population/features.py` from the local BurstGPT v1.1 and Azure LLM "
         "2024 traces. Counts in 10 s bins; windows of 600 s and 3,600 s; a window is *active* when it "
         f"holds at least {MIN_REQUESTS} requests. The traces carry no tenant identity: the population's "
         "tenants are a construction from these windows.", ""]
    for window_s, df in windows.items():
        act = df[df.n >= MIN_REQUESTS]
        L += [f"## {window_s} s windows (active windows; median [10th, 90th percentile])", "",
              "| stream | windows | rate rps | dispersion | p99 / mean | ACF 10 s | ACF 300 s | prompt tokens | response tokens |",
              "|---|---:|---|---|---|---|---|---|---|"]
        for name, g in act.groupby("stream"):
            def q(col, fmt="{:.2f}"):
                v = g[col].dropna()
                return (fmt.format(v.median()) + " [" + fmt.format(v.quantile(0.1)) + ", "
                        + fmt.format(v.quantile(0.9)) + "]") if len(v) else "n/a"
            L.append(f"| {name} | {len(g)} | {q('rate_rps', '{:.3g}')} | {q('dispersion')} | {q('p99_ratio')} | "
                     f"{q('acf_10s')} | {q('acf_300s')} | {q('req_mean', '{:.0f}')} | {q('resp_mean', '{:.0f}')} |")
        L.append("")
    L += ["## Daily amplitude of hourly counts, (max - min) / (2 mean)", "", "| stream | days | median | 10th | 90th |",
          "|---|---:|---:|---:|---:|"]
    for name, a in amplitude.items():
        if len(a):
            L.append(f"| {name} | {len(a)} | {a.amplitude.median():.2f} | {a.amplitude.quantile(0.1):.2f} | "
                     f"{a.amplitude.quantile(0.9):.2f} |")
    if len(sessions):
        L += ["", f"BurstGPT sessions (third file): {len(sessions):,} sessions; requests per session median "
              f"{sessions.median():.0f}, mean {sessions.mean():.2f}, 90th percentile {sessions.quantile(0.9):.0f}.", ""]
    return "\n".join(L)


def main() -> int:
    con = connect()
    windows: dict[int, list] = {w: [] for w in WINDOWS}
    amplitude = {}
    for name, sql in streams(con).items():
        counts = bin_counts(con, sql)
        amplitude[name] = daily_amplitude(counts)
        for window_s, lags in WINDOWS.items():
            df = count_features(counts, window_s, lags).merge(token_features(con, sql, window_s),
                                                              on="window", how="left")
            df.insert(0, "stream", name)
            windows[window_s].append(df)
        print(f"{name}: {int(counts.sum()):,} requests over {len(counts) * BIN_S / 86400:.1f} days", flush=True)
    frames = {w: pd.concat(v, ignore_index=True) for w, v in windows.items()}
    out = HERE / "data"
    out.mkdir(exist_ok=True)
    for w, df in frames.items():
        df.to_csv(out / f"windows_{w}s.csv.gz", index=False)
    text = summarise(frames, amplitude, session_lengths(con))
    (HERE / "FEATURES.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
