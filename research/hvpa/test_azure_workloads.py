"""The Azure Functions selection rule on a synthetic day."""

import numpy as np
import pandas as pd
import pytest

import azure_workloads as aw


def _day(rows):
    data = {"HashOwner": [], "HashApp": [], "HashFunction": [], "Trigger": [], **{m: [] for m in aw.MINUTES}}
    for app, trig, series in rows:
        data["HashOwner"].append("o"), data["HashApp"].append(app), data["HashFunction"].append(app + trig)
        data["Trigger"].append(trig)
        for m, v in zip(aw.MINUTES, series):
            data[m].append(v)
    return pd.DataFrame(data)


def test_the_two_busiest_steady_http_apps_and_their_peak_hours():
    flat = np.full(1440, 5)
    peak = flat.copy()
    peak[600:660] = 50
    sparse = np.zeros(1440)
    sparse[::3] = 1000             # active in a third of minutes: ruled out, however busy
    day = _day([("a", "http", peak), ("b", "http", flat * 2), ("c", "http", sparse), ("d", "timer", flat * 100)])
    first, second = aw.select(day)
    # b: 10 x 1440 = 14,400 invocations ranks above a: 5 x 1380 + 50 x 60 = 9,900; a's window is its peak
    assert first.tolist() == [10] * 60 and second.tolist() == [50] * 60


def test_too_few_apps_is_an_error():
    with pytest.raises(ValueError):
        aw.select(_day([("a", "http", np.full(1440, 3))]))


def test_azure_shapes_spread_each_minute_and_scale_to_twice_the_base(tmp_path, monkeypatch):
    import workloads

    f = tmp_path / "azure.csv"
    pd.DataFrame({"minute": range(60), "app1": [10] * 30 + [30] * 30, "app2": [4] * 60}).to_csv(f, index=False)
    monkeypatch.setattr(workloads, "AZURE_FILE", f)
    s = workloads.shape("azure_app1", 10.0)
    assert len(s) == 360 and s.mean() == pytest.approx(20.0)
    assert (s[:6] == s[0]).all() and s[180] == pytest.approx(3 * s[0])
    assert workloads.all_shapes()[-2:] == ("azure_app1", "azure_app2")
    monkeypatch.setattr(workloads, "AZURE_FILE", tmp_path / "missing.csv")
    assert workloads.all_shapes() == workloads.SHAPES
