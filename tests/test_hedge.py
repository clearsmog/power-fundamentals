import numpy as np
import pandas as pd
import pytest

from powerfund import hedge


def _e(**kw):
    base = dict(revenue=[5000.0], exp_gen_mwh=[100.0], forward=[80.0], baseload=[100.0],
                gen_mwh=[100.0], capture=[0.5], exp_capture=[0.5])
    base.update({k: [v] for k, v in kw.items()})
    e = pd.DataFrame(base)
    e["budget"] = e["exp_gen_mwh"] * e["forward"] * e["exp_capture"]
    return e


def test_hedge_payoff_sign():
    # sold 100 MWh at 80, settled at 100: hedge loses 2,000
    e = _e()
    assert hedged_value(e, 1.0) == 5000 - 2000


def hedged_value(e, h):
    return float(hedge.hedged_revenue(e, h).iloc[0])


def test_capture_weighted_hedge_neutralises_price_surprise():
    # revenue = G * B * c; hedging h = c of expected volume removes exposure to B when output is as expected
    for b in (60.0, 100.0, 140.0):
        e = _e(baseload=b, revenue=100 * b * 0.5)
        assert hedged_value(e, 0.5) == pytest.approx(100 * 80 * 0.5)


def test_budget_variance_zero_when_everything_as_expected():
    e = _e(baseload=80.0, revenue=100 * 80 * 0.5)
    assert float(hedge.budget_variance(e, 0.0).iloc[0]) == pytest.approx(0.0)


def test_risk_decomposition_shares_sum_to_one():
    rng = np.random.default_rng(0)
    n = 40
    e = pd.DataFrame({
        "gen_mwh": 100 * np.exp(rng.normal(0, 0.1, n)), "exp_gen_mwh": 100.0,
        "baseload": 80 * np.exp(rng.normal(0, 0.3, n)), "forward": 80.0,
        "capture": 0.5 * np.exp(rng.normal(0, 0.2, n)), "exp_capture": 0.5,
    })
    shares = hedge.risk_decomposition(e)
    assert sum(shares.values()) == pytest.approx(1.0)
    assert shares["price"] > shares["shape"] > shares["volume"]
