import numpy as np
import pandas as pd
import pytest

from powerfund import risk


def _prices(n=300, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-01", periods=n)
    base = {"DE": 90.0, "FR": 80.0, "TTF": 40.0, "EUA": 70.0}
    return pd.DataFrame({f: b + rng.normal(0, 1, n).cumsum() for f, b in base.items()}, index=idx)


def test_positions_net_by_factor():
    pos = risk.positions()
    assert pos["DE"] == -50_400
    assert pos["FR"] == 14_400
    assert list(pos.index) == risk.FACTORS


def test_var_es_on_known_sample():
    pnl = np.arange(-100, 900)  # 1,000 points, losses down to -100
    var, es = risk.var_es(pnl, 0.99, 0.975)
    assert var == pytest.approx(90.01, abs=0.01)  # 1% quantile of -100..899
    assert es == pytest.approx(88.0, abs=0.5)     # mean of the 25 worst (-100..-76)
    assert es > 0


def test_scenario_pnl_is_move_times_position():
    moves = pd.DataFrame({"DE": [10.0], "FR": [0.0], "TTF": [-2.0], "EUA": [1.0]})
    pos = pd.Series({"DE": -100.0, "FR": 0.0, "TTF": 50.0, "EUA": 10.0})
    assert float(risk.scenario_pnl(moves, pos).iloc[0]) == -1000 - 100 + 10


def test_rolling_var_has_no_look_ahead():
    p = _prices()
    moves = risk.changes(p)
    pos = risk.positions()
    a = risk.rolling_var(moves, pos, window=100, method="fhs")
    shocked = moves.copy()
    shocked.iloc[200:] *= 10
    b = risk.rolling_var(shocked, pos, window=100, method="fhs")
    cut = moves.index[200]
    # forecasts made up to and including day 200 only use moves before it
    pd.testing.assert_series_equal(a.loc[:cut, "var"], b.loc[:cut, "var"])


def test_filtered_moves_unchanged_when_vol_is_constant():
    w = pd.DataFrame({"DE": [1.0, -2.0, 3.0]})
    vol = pd.DataFrame({"DE": [2.0, 2.0, 2.0]}, index=w.index)
    out = risk.filtered_moves(w, vol, pd.Series({"DE": 2.0}))
    pd.testing.assert_frame_equal(out, w)


def test_filtered_moves_scale_to_todays_vol():
    w = pd.DataFrame({"DE": [1.0, -2.0]})
    vol = pd.DataFrame({"DE": [1.0, 1.0]}, index=w.index)
    out = risk.filtered_moves(w, vol, pd.Series({"DE": 3.0}))
    assert out["DE"].tolist() == [3.0, -6.0]


def test_kupiec_accepts_expected_count_and_rejects_many():
    _, p_ok = risk.kupiec(5, 500, 0.01)
    _, p_bad = risk.kupiec(20, 500, 0.01)
    assert p_ok == pytest.approx(1.0)
    assert p_bad < 0.001


def test_christoffersen_flags_clustered_exceptions():
    spread = np.zeros(500, dtype=int)
    spread[[50, 150, 250, 350, 450]] = 1
    clustered = np.zeros(500, dtype=int)
    clustered[200:205] = 1
    assert risk.christoffersen(spread)[1] > 0.5
    assert risk.christoffersen(clustered)[1] < 0.01


def test_traffic_light_zones():
    assert [risk.traffic_light(x) for x in (4, 5, 9, 10)] == ["green", "yellow", "yellow", "red"]


def test_input_checks_find_each_problem():
    p = _prices(30)
    p.loc[p.index[5], "TTF"] = np.nan
    p.loc[p.index[10:14], "EUA"] = 70.0
    p.loc[p.index[20], "DE"] = p["DE"].iloc[19] + 200
    p.loc[p.index[25], "FR"] = -5.0
    c = risk.input_checks(p)
    found = set(zip(c["factor"], c["check"]))
    assert {("TTF", "missing"), ("EUA", "stale"), ("DE", "outlier"), ("FR", "non_positive")} <= found
    stale_days = c[(c.factor == "EUA") & (c.check == "stale")]["date"]
    assert len(stale_days) == 3  # the first of four equal prices is not stale


def test_clean_rolls_forward_missing_price():
    p = pd.DataFrame({"TTF": [40.0, np.nan, 42.0]}, index=pd.bdate_range("2024-01-01", periods=3))
    assert risk.clean(p)["TTF"].tolist() == [40.0, 40.0, 42.0]


def test_relative_moves_undefined_through_zero():
    p = pd.DataFrame({"DE": [10.0, 0.0, 5.0]})
    rel = risk.changes(p, "relative")
    assert np.isinf(rel["DE"].iloc[-1])


def test_apply_shock_mixes_absolute_and_relative():
    now = pd.Series({"DE": 100.0, "FR": 90.0, "TTF": 40.0, "EUA": 70.0})
    move = risk.apply_shock({"TTF": ("rel", 0.5), "DE": ("abs", 60)}, now)
    assert move.to_dict() == {"DE": 60.0, "FR": 0.0, "TTF": 20.0, "EUA": 0.0}


def test_stress_table_worst_window_is_the_minimum():
    p = _prices()
    pos = risk.positions()
    t = risk.stress_table(p, pos)
    worst1 = t[t["scenario"].str.contains("1-day")]["pnl"].iloc[0]
    assert worst1 == pytest.approx(float((p.diff().dropna() @ pos).min()))


def test_limit_monitor_stop_loss_resets_each_month():
    idx = pd.to_datetime(["2024-01-30", "2024-01-31", "2024-02-01"])
    mon = risk.limit_monitor(pd.Series([-6.0, -6.0, -6.0], index=idx), stop_loss=10, peak_to_trough=15)
    assert mon["stop_loss_breach"].tolist() == [False, True, False]
    assert mon["drawdown_breach"].tolist() == [False, False, True]
    assert risk.first_breaches(mon, "stop_loss_breach") == [idx[1]]


def test_margin_price_shock_pays_worst_loss_as_vm():
    p = _prices()
    pos = risk.positions()
    stress = risk.stress_table(p, pos)
    m = risk.margin_scenarios(p, pos, stress).set_index("scenario")
    assert m.loc["Price shock", "variation_margin"] == pytest.approx(-stress["pnl"].min())
    assert m.loc["Margin parameter change (+25%)", "initial_margin"] == pytest.approx(
        1.25 * m.loc["Today", "initial_margin"])
