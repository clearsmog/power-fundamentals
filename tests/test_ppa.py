import numpy as np
import pandas as pd

from powerfund import ppa, sim


def _frame(days=60, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-03-01", periods=48 * days, freq="30min", tz="UTC")
    hour = idx.hour + idx.minute / 60
    cf = np.clip(np.sin((hour - 6) / 12 * np.pi), 0, None) * 0.6
    mid = 80 + 20 * rng.standard_normal(len(idx))
    sp = mid + np.where(rng.random(len(idx)) < 0.5, 20.0, -18.0)
    return pd.DataFrame(
        {"pv_cf": cf, "pv_mw": cf * 1000, "pv_cap_mwp": 1000.0, "solar": cf * 900,
         "solar_fc": cf * 900, "da": mid, "sp": sp},
        index=idx,
    )


def test_perfect_national_forecast_and_no_site_noise_costs_nothing():
    df = _frame()
    err = ppa.site_error_cf(df, 0.0, 0.9, np.random.default_rng(1))
    assert np.allclose(err, 0)
    assert ppa.imbalance_cost(df, err).sum() == 0


def test_calibration_hits_target_nmae():
    df = _frame()
    s = ppa.calibrate_sigma(df, 0.05, 0.9)
    e = ppa.site_error_cf(df, s, 0.9, np.random.default_rng(0))
    assert abs(ppa.nmae(e, df["pv_cf"].to_numpy()) - 0.05) < 1e-3


def test_forecast_never_negative_or_above_capacity():
    df = _frame()
    e = ppa.site_error_cf(df, 2.0, 0.9, np.random.default_rng(2))
    f = df["pv_cf"].to_numpy() + e
    assert f.min() >= 0 and f.max() <= 1


def _hist(years, rng=None):
    idx = pd.MultiIndex.from_product([years, range(1, 13)], names=["year", "month"])
    n = len(idx)
    if rng is None:
        return pd.DataFrame({"base": 100.0, "kappa": 0.8, "mwh_per_mw": 80.0, "cap_gw": 20.0}, index=idx)
    return pd.DataFrame({"base": rng.uniform(60, 120, n), "kappa": rng.uniform(0.6, 1.0, n),
                         "mwh_per_mw": rng.uniform(20, 150, n), "cap_gw": 20.0}, index=idx)


def test_fixed_price_equals_capture_price_without_risk():
    hist = _hist([2025])
    draws = sim.sample_months({m: [2025] for m in range(1, 13)}, 100, 5, np.random.default_rng(0))
    s = ppa.simulate_ppa(hist, draws, np.ones((100, 5)), base_annual=100.0)
    res = ppa.price_fixed_ppa(s, 100.0, imb_per_mwh=2.0, rate=0.07, coc=0.1)
    assert np.isclose(res["fixed_price"], 80.0 - 2.0)
    assert np.isclose(res["price_risk"], 0.0) and np.isclose(res["profile_volume_risk"], 0.0)
    assert np.isclose(res["hourly_profile_cost"], -20.0)


def test_waterfall_adds_up():
    rng = np.random.default_rng(3)
    hist = _hist([2024, 2025], rng)
    draws = sim.sample_months({m: [2024, 2025] for m in range(1, 13)}, 2000, 10, rng)
    levels = sim.level_paths(2000, 10, 0.2, rng)
    s = ppa.simulate_ppa(hist, draws, levels, 100.0, per_gw=-0.01, future_gw=np.linspace(22, 40, 10))
    r = ppa.price_fixed_ppa(s, 100.0, 1.5, 0.07, 0.1)
    parts = sum(r[k] for k in ("baseload", "hourly_profile_cost", "profile_volume_covariance",
                               "balancing_cost", "profile_volume_risk", "price_risk"))
    assert np.isclose(parts, r["fixed_price"])
    assert r["price_risk"] < 0 and r["profile_volume_risk"] < 0


def test_more_solar_lowers_capture():
    hist = _hist([2025])
    draws = sim.sample_months({m: [2025] for m in range(1, 13)}, 10, 3, np.random.default_rng(0))
    s = ppa.simulate_ppa(hist, draws, np.ones((10, 3)), 100.0, per_gw=-0.01, future_gw=np.array([20.0, 30.0, 40.0]))
    assert np.allclose(s["kappa"][:, 0], 0.8) and np.allclose(s["kappa"][:, 2], 0.6)


def test_month_shape_uses_complete_years_relative_to_their_mean():
    idx = pd.MultiIndex.from_product([[2022, 2023], range(1, 13)], names=["year", "month"])
    base = np.r_[np.full(12, 200.0), np.full(12, 100.0)]  # a crisis year at twice the level
    base[0], base[12] = 400.0, 200.0  # January twice its year's other months in both years
    hist = pd.DataFrame({"base": base}, index=idx)
    shape = ppa.month_shape(hist)
    assert np.isclose(shape.loc[1] / shape.loc[2], 2.0)
