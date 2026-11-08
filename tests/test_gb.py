import numpy as np
import pandas as pd
import pytest

from powerfund import gb


def _df(**cols):
    return pd.DataFrame(cols)


def test_perfect_forecast_has_no_imbalance_cost():
    d = _df(
        wind_fc=[100.0, 200.0], wind=[100.0, 200.0], sp=[300.0, 20.0], mid=[80.0, 70.0]
    )
    assert gb.imbalance_cost(d).abs().sum() == 0
    assert gb.cost_per_mwh(d) == 0


def test_short_wind_in_short_system_is_a_cost():
    # forecast 100 MW, delivered 60 MW: 20 MWh short over the half hour, bought back at SP 300 vs MID 100
    d = _df(wind_fc=[100.0], wind=[60.0], sp=[300.0], mid=[100.0])
    assert gb.imbalance_cost(d).iloc[0] == pytest.approx(20 * 200)


def test_long_wind_in_long_system_is_a_cost():
    # over-delivered 20 MWh, spilled at SP 10 vs MID 60
    d = _df(wind_fc=[60.0], wind=[100.0], sp=[10.0], mid=[60.0])
    assert gb.imbalance_cost(d).iloc[0] == pytest.approx(20 * 50)


def test_revenue_identity():
    # F*MID + (A-F)*SP == A*MID - cost
    d = _df(
        wind_fc=[120.0, 40.0, 80.0],
        wind=[90.0, 70.0, 80.0],
        sp=[150.0, 30.0, 55.0],
        mid=[100.0, 45.0, 60.0],
    )
    f, a = d["wind_fc"] * 0.5, d["wind"] * 0.5
    revenue = f * d["mid"] + (a - f) * d["sp"]
    assert np.allclose(revenue, a * d["mid"] - gb.imbalance_cost(d))


def test_decomposition_adds_up():
    rng = np.random.default_rng(0)
    n = 500
    d = _df(
        wind_fc=rng.uniform(0, 100, n),
        wind=rng.uniform(0, 100, n),
        sp=rng.normal(80, 40, n),
        mid=rng.normal(75, 10, n),
    )
    c = gb.cost_decomposition(d)
    assert c["total"] == pytest.approx(c["mean_term"] + c["cov_term"])
    assert c["total"] == pytest.approx(gb.imbalance_cost(d).mean())


def test_spread_by_niv_sign():
    d = _df(sp=[200.0, 20.0, 100.0], mid=[100.0, 60.0, 100.0], niv=[500.0, -300.0, 0.0])
    g = gb.spread_by_niv_sign(d)
    assert g.loc["short", "spread"] == 100
    assert g.loc["long", "spread"] == -40
    assert g["share"].sum() == pytest.approx(1)
