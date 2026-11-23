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
         "solar_fc": cf * 900, "mid": mid, "sp": sp},
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


def test_fixed_price_equals_capture_price_without_risk():
    hist = pd.DataFrame(
        {"base": 100.0, "kappa": 0.8, "mwh_per_mw": 80.0},
        index=pd.MultiIndex.from_product([[2025], range(1, 13)], names=["year", "month"]),
    )
    draws = sim.sample_months({m: [2025] for m in range(1, 13)}, 100, 5, np.random.default_rng(0))
    value, energy = ppa.simulate_ppa(hist, draws, np.ones((100, 5)), base_annual=100.0)
    res = ppa.price_fixed_ppa(value, energy, 100.0, imb_per_mwh=2.0, rate=0.07, coc=0.1)
    assert np.isclose(res["fixed_price"], 80.0 - 2.0)
    assert np.isclose(res["risk_charge"], 0.0)


def test_waterfall_adds_up():
    rng = np.random.default_rng(3)
    idx = pd.MultiIndex.from_product([[2024, 2025], range(1, 13)], names=["year", "month"])
    hist = pd.DataFrame({"base": rng.uniform(60, 120, 24), "kappa": rng.uniform(0.6, 1.0, 24),
                         "mwh_per_mw": rng.uniform(20, 150, 24)}, index=idx)
    draws = sim.sample_months({m: [2024, 2025] for m in range(1, 13)}, 2000, 10, rng)
    levels = sim.level_paths(2000, 10, 0.2, rng)
    value, energy = ppa.simulate_ppa(hist, draws, levels, 100.0, capture_drift=-0.01)
    r = ppa.price_fixed_ppa(value, energy, 100.0, 1.5, 0.07, 0.1)
    parts = r["baseload"] + r["shape_and_cannibalisation"] + r["imbalance"] + r["risk_charge"]
    assert np.isclose(parts, r["fixed_price"])
    assert r["risk_charge"] < 0
