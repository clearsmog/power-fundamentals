import numpy as np

from powerfund import bess


def _day(lo=20.0, hi=120.0):
    price = np.r_[np.full(24, lo), np.full(24, hi)]
    block = np.repeat(np.arange(6), 8)
    return price, block


def test_arbitrage_matches_hand_calculation():
    price, block = _day()
    plan = bess.dispatch_day(price, block, None, power=1, energy=2, rte=0.81, max_cycles=10)
    rev = bess.settle(plan, price, None)["wholesale"]
    # the battery starts and ends half full, so every MWh bought at 20 comes back as
    # 0.81 MWh sold at 120 (one-way efficiency 0.9 each way)
    bought = 0.5 * plan["c"].sum()
    assert np.isclose(rev, bought * (0.81 * 120 - 20), rtol=1e-6)
    assert plan["s"].max() <= 2 + 1e-9 and plan["s"].min() >= -1e-9
    assert np.isclose(plan["s"][-1], 1.0)


def test_cycle_limit_binds():
    price, block = _day()
    plan = bess.dispatch_day(price, block, None, power=1, energy=2, rte=1.0, max_cycles=0.25)
    assert 0.5 * plan["d"].sum() <= 0.5 + 1e-9


def test_response_respects_energy_and_headroom():
    price = np.full(48, 50.0)
    block = np.repeat(np.arange(6), 8)
    resp = np.tile([0.0, 0.0, 30.0], (6, 1))  # only Regulation pays (1 h each way)
    plan = bess.dispatch_day(price, block, resp, power=1, energy=1, rte=0.9)
    # 1 MWh battery at half charge: 0.5 MWh each way -> at most 0.5 MW of a 1-hour pair
    assert np.allclose(plan["r"][:, 2], 0.5, atol=1e-6)
    rev = bess.settle(plan, price, resp)["response"]
    assert np.isclose(rev, 0.5 * 30 * 24, rtol=1e-6)


def test_floor_and_toll_without_risk():
    sims = np.full((1000, 5), 60_000.0)
    f = bess.price_floor(sims, 50_000, rate=0.08, coc=0.1)
    assert f["premium"] == 0 and f["p_triggered"] == 0
    t = bess.price_toll(sims, rate=0.08, coc=0.1)
    assert np.isclose(t["toll"], 60_000)


def test_floor_premium_grows_with_floor():
    rng = np.random.default_rng(0)
    sims = rng.normal(60_000, 15_000, size=(5000, 5))
    p = [bess.price_floor(sims, f, 0.08, 0.1)["premium"] for f in (40_000, 50_000, 60_000)]
    assert p[0] < p[1] < p[2]


def test_response_cap_limits_held_power():
    price = np.full(48, 50.0)
    block = np.repeat(np.arange(6), 8)
    resp = np.tile([10.0, 10.0, 10.0], (6, 1))
    plan = bess.dispatch_day(price, block, resp, power=1, energy=4, rte=0.9, resp_cap=0.3)
    assert np.all(plan["r"].sum(axis=1) <= 0.3 + 1e-9)


def test_bm_estimate_pays_spread_on_spare_power():
    price, block = _day()
    plan = bess.dispatch_day(price, block, None, power=1, energy=2, rte=0.81, max_cycles=10)
    plan["block"] = block
    sp = price + 10.0  # system always short by 10
    bm = bess.bm_estimate(plan, 1.0, price, sp, rate=0.03)
    up = np.clip(1 - plan["d"] + plan["c"], 0, None)
    assert np.isclose(bm, 0.03 * 0.5 * (up * 10).sum())
