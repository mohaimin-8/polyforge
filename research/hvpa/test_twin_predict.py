"""The twin's prediction file on a tiny grid (the placeholder plant)."""

import twin_pilot as tp
import twin_predict
from service_plant import Plant


def test_a_tiny_grid_gives_regimes_live_choices_and_targets(monkeypatch):
    monkeypatch.setattr(tp, "SEEDS", 1)
    monkeypatch.setattr(tp, "TUNING_SEEDS", (100,))
    monkeypatch.setattr(tp, "TARGETS", (0.3, 0.6))
    monkeypatch.setattr(tp.workloads, "all_shapes", lambda: ("step_up",))
    out = twin_predict.predict(Plant(), widths=(1, 2), sigmas=(0.05,))
    assert len(out["regimes"]) == 2 and len(out["live_regimes"]) == 3
    gains = sorted(r["gain_vs_A3"] for r in out["regimes"])
    chosen = [next(r for r in out["regimes"] if (r["width"], r["sigma"]) == (c["width"], c["sigma"]))["gain_vs_A3"]
              for c in out["live_regimes"]]
    assert chosen[0] == gains[0] and chosen[-1] == gains[-1]
    assert set(out["tuned_targets"]["0.05"].values()) <= {0.3, 0.6}
