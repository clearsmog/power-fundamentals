import pandas as pd
import pytest

from powerfund import metrics as m


def test_capture_rate_cannibalised_solar():
    # solar produces only in the cheap hours, so it captures less than baseload
    price = pd.Series([100.0, 100.0, 20.0, 20.0])
    solar = pd.Series([0.0, 0.0, 10.0, 10.0])
    assert m.capture_price(price, solar) == 20.0
    assert m.capture_rate(price, solar) == pytest.approx(20.0 / 60.0)


def test_capture_rate_flat_profile_is_one():
    price = pd.Series([10.0, 50.0, 90.0])
    assert m.capture_rate(price, pd.Series([5.0, 5.0, 5.0])) == pytest.approx(1.0)


def test_capture_ignores_missing_generation():
    price = pd.Series([10.0, 30.0])
    gen = pd.Series([1.0, float("nan")])
    assert m.capture_price(price, gen) == 10.0


def test_negative_hours():
    assert m.negative_hours(pd.Series([-1.0, 0.0, 5.0, -0.01])) == 2


def test_gas_srmc_standard_ccgt():
    # TTF 35, EUA 80: (35 + 80*0.202) / 0.4913 = 104.13
    srmc = m.gas_srmc(pd.Series([35.0]), pd.Series([80.0]))
    assert srmc.iloc[0] == pytest.approx(104.13, abs=0.01)
    css = m.clean_spark_spread(pd.Series([110.0]), pd.Series([35.0]), pd.Series([80.0]))
    assert css.iloc[0] == pytest.approx(5.87, abs=0.01)


def test_gas_near_margin_share():
    price = pd.Series([100.0, 112.0, 130.0, 50.0])
    srmc = pd.Series([100.0, 100.0, 100.0, 100.0])
    assert m.gas_near_margin_share(price, srmc) == 0.5
