"""GB deal pricing: solar balancing PPA fee, fixed-price solar PPA, BESS floor and toll, portfolio.

Prices are the N2EX GB day-ahead hourly auction (NESO / Nord Pool), Oct 2021 onward.
Every number this prints is written to output/gb_pricing_*.{csv,json,png}.
Assumptions are the constants below; the README says which are measured and which chosen.
"""

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from powerfund import bess, forecast, gb, gbdata, ppa, sim

DA_START, END = "2021-10-01", "2026-10-31"
SP_START = "2024-01-01"  # system prices cached from here (balancing module)
BESS_START = "2023-11-03"  # first full EFA day of NESO's EAC response results
OUT = Path(__file__).resolve().parent.parent / "output"
DAILY = OUT / "gb_pricing_bess_daily.csv"

# chosen assumptions (README "Assumptions")
SITE_MW = 40.0
BESS_MW = 20.0
RTE, MAX_CYCLES = 0.88, 1.5
DEG_COST = 5.0  # GBP per MWh discharged; sensitivities 0 and 10
RESP_CAP = 0.5  # share of battery power that may sit in frequency response (contract base case)
BM_RATE = 0.03  # share of spare power accepted in the BM (Modo Energy's ~3% dispatch rate)
NMAE = [0.04, 0.06, 0.08]
NMAE_BASE = 0.06
RHO = 0.95
SEEDS = 30
PPA_YEARS, BESS_YEARS = 10, 5
FIRST_YEAR = 2027
RATE, COC, Q = 0.07, 0.10, 0.95
LEVEL_VOL = 0.15
N_SIMS = 20_000
# installed GB solar (PV_Live basis): straight line to the Clean Power 2030 floor of 45 GW
# at the end of 2030, then +2 GW a year (the plan's 45-47 GW is a DESNZ basis, not PV_Live's)
SOLAR_2030_GW, SOLAR_GROWTH_AFTER = 45.0, 2.0
# Capacity Market T-4 clearing prices (GBP/kW/yr) and the 2-hour battery de-rating factor
CM_T4 = {"2025/26": 30.59, "2026/27": 63.00, "2027/28": 65.00, "2028/29": 60.00, "2029/30": 27.10}
CM_DERATE_2H = 0.2094


def load() -> pd.DataFrame:
    da = gbdata.fetch_n2ex()
    pv = gbdata.fetch_pvlive(DA_START, END)
    hh = pd.date_range(pd.Timestamp(DA_START, tz="Europe/London").tz_convert("UTC"),
                       pd.Timestamp(END, tz="Europe/London").tz_convert("UTC") + pd.Timedelta(hours=23, minutes=30),
                       freq="30min")
    df = pd.DataFrame(index=hh)
    df["da"] = da.reindex(hh, method="ffill", limit=1)
    df = df.join(pv)
    sp = gb.fetch_gb(SP_START, END).rename(columns={"ssp": "sp"})[["sp", "niv", "mid"]]
    df = df.join(sp).join(gb.fetch_solar(SP_START, END))
    return df.dropna(subset=["da", "pv_cf"])


# --- A. balancing PPA ------------------------------------------------------------


def balancing(df: pd.DataFrame) -> pd.DataFrame:
    d = df.dropna(subset=["sp", "solar", "solar_fc"])
    year = d.index.tz_convert("Europe/London").year
    mwh_year = (d["pv_cf"] * 0.5).groupby(year).sum()
    rows = []
    for target in [None] + NMAE:
        sigma = 0.0 if target is None else ppa.calibrate_sigma(d, target, RHO)
        annual = []
        for seed in range(1 if target is None else SEEDS):
            e = ppa.site_error_cf(d, sigma, RHO, np.random.default_rng(seed))
            annual.append(ppa.imbalance_cost(d, e).groupby(year).sum() / mwh_year)
        a = pd.concat(annual, axis=1)
        e0 = ppa.site_error_cf(d, sigma, RHO, np.random.default_rng(0))
        mean_cost = float(a.mul(mwh_year, axis=0).sum().mean() / mwh_year.sum())
        risk = COC * (sim.cvar(a.to_numpy().ravel(), Q, upper=True) - mean_cost)
        rows.append({
            "case": "national fleet" if target is None else f"site nMAE {target:.0%}",
            "nmae": ppa.nmae(e0, d["pv_cf"].to_numpy()), "sigma": sigma, "cost_per_mwh": mean_cost,
            **{f"cost_{y}": float(a.loc[y].mean()) for y in a.index},
            "risk_charge": risk, "fee": mean_cost + risk,
        })
    return pd.DataFrame(rows)


def solar_shape(df: pd.DataFrame) -> pd.DataFrame:
    year = df.index.tz_convert("Europe/London").year
    g = df.assign(w=df["da"] * df["pv_cf"], neg=df["da"] < 0, wneg=df["pv_cf"] * (df["da"] < 0)).groupby(year)
    out = pd.DataFrame({
        "baseload_da": g["da"].mean(),
        "solar_capture": g["w"].sum() / g["pv_cf"].sum(),
        "solar_cf": g["pv_cf"].mean(),
        "negative_hours": g["neg"].sum() / 2,
        "solar_mwh_share_at_negative": g["wneg"].sum() / g["pv_cf"].sum(),
        "installed_gw_mean": g["pv_cap_mwp"].mean() / 1000,
    })
    out["capture_ratio"] = out["solar_capture"] / out["baseload_da"]
    return out


# --- C. battery -----------------------------------------------------------------


def battery(df: pd.DataFrame) -> pd.DataFrame:
    if DAILY.exists():
        return pd.read_csv(DAILY, index_col=[0, 1, 2], parse_dates=[2])
    hourly = gbdata.fetch_n2ex()
    hourly = hourly[hourly.index < pd.Timestamp(END, tz="UTC") + pd.Timedelta(days=1)]
    fc_h = forecast.to_hourly(forecast.lear_forecast(hourly, BESS_START), hourly)
    d = df[df.index >= pd.Timestamp(BESS_START, tz="Europe/London").tz_convert("UTC") - pd.Timedelta(hours=1)]
    actual = d["da"]
    fc = fc_h.reindex(d.index, method="ffill", limit=1)
    persist = actual.shift(freq="24h").reindex(d.index)
    pairs = bess.pair_prices(gbdata.fetch_response_prices())
    pairs_prev = pairs.shift(freq="24h").reindex(pairs.index)
    ok = fc.notna() & persist.notna()
    mae = {"lear": float((fc[ok] - actual[ok]).abs().mean()),
           "persistence": float((persist[ok] - actual[ok]).abs().mean())}
    (OUT / "gb_pricing_forecast_mae.json").write_text(json.dumps(mae, indent=2))
    runs = [
        ("2h wholesale only", 2.0, False, 1.0, [("perfect", DEG_COST), ("ex-ante", DEG_COST)]),
        ("2h stacked, no cap", 2.0, True, 1.0, [("perfect", DEG_COST), ("ex-ante", DEG_COST)]),
        ("2h stacked, cap 50%", 2.0, True, RESP_CAP,
         [("perfect", DEG_COST), ("ex-ante", DEG_COST), ("persistence", DEG_COST), ("ex-ante", 0.0), ("ex-ante", 10.0)]),
        ("1h stacked, cap 50%", 1.0, True, RESP_CAP, [("perfect", DEG_COST), ("ex-ante", DEG_COST)]),
    ]
    frames = []
    for name, h, stacked, cap, cases in runs:
        for foresight, deg in cases:
            plan_price = {"perfect": None, "ex-ante": fc, "persistence": persist}[foresight]
            plan_pairs = None if foresight == "perfect" or not stacked else pairs_prev
            r = bess.run_backtest(
                actual, plan_price, pairs if stacked else None, plan_pairs, sp=d["sp"], bm_rate=BM_RATE,
                power=1.0, energy=h, rte=RTE, max_cycles=MAX_CYCLES, deg_cost=deg, resp_cap=cap,
            )
            r["config"] = name
            r["case"] = f"{foresight}, deg {deg:g}"
            frames.append(r)
            print(f"battery {name} / {foresight}, deg {deg:g}: {len(r)} days", flush=True)
    out = pd.concat(frames).reset_index().set_index(["config", "case", "day"])
    out.to_csv(DAILY)
    return out


def monthly(daily: pd.DataFrame, config: str, case: str, cols=("wholesale", "response")) -> pd.Series:
    d = daily.loc[(config, case)]
    total = d[list(cols)].sum(axis=1, min_count=1)
    days = total.groupby([d.index.year, d.index.month]).size()
    s = total.groupby([d.index.year, d.index.month]).sum()
    keep = days >= 27
    dim = pd.Series([pd.Period(f"{y}-{m:02d}").days_in_month for y, m in s[keep].index], index=s[keep].index)
    return s[keep] / days[keep] * dim


# --- run ------------------------------------------------------------------------


def main():
    OUT.mkdir(exist_ok=True)
    df = load()
    summary: dict = {"window": {"da": [DA_START, END], "imbalance": [SP_START, END], "battery": [BESS_START, END]},
                     "assumptions": {k: v for k, v in globals().items() if k.isupper() and k not in ("OUT", "DAILY")}}

    shape = solar_shape(df)
    shape.to_csv(OUT / "gb_pricing_solar_shape.csv")
    print(shape.round(3).to_string())

    bal = balancing(df)
    bal.to_csv(OUT / "gb_pricing_balancing_fee.csv", index=False)
    print(bal.round(3).to_string())
    summary["balancing"] = bal.to_dict(orient="records")
    imb = float(bal.loc[bal["case"] == f"site nMAE {NMAE_BASE:.0%}", "cost_per_mwh"].iloc[0])

    # B. capture vs installed solar, forward anchor, Monte Carlo
    hist = ppa.monthly_history(df)
    reg = ppa.capture_regression(hist)
    summary["capture_regression"] = reg
    print("capture regression", reg)
    last_gw = float(hist["cap_gw"].iloc[-1])
    years = np.arange(FIRST_YEAR, FIRST_YEAR + PPA_YEARS)
    t_now = 2026 + 10 / 12
    mid_year = years + 0.5
    gw = np.where(mid_year <= 2031, last_gw + (SOLAR_2030_GW - last_gw) * (mid_year - t_now) / (2031 - t_now),
                  SOLAR_2030_GW + SOLAR_GROWTH_AFTER * (mid_year - 2031))
    gw_slow = last_gw + (SOLAR_2030_GW - last_gw) * (mid_year - t_now) / (2036 - t_now)
    summary["solar_gw_path"] = dict(zip(years.tolist(), gw.round(1).tolist()))

    lccc = gbdata.fetch_lccc_forecast()
    bmrp = lccc[lccc["technology"].str.startswith("Forecast BMRP") & lccc["period"].str.startswith("Determination")]
    winter_bmrp = float(bmrp.groupby("period")["price"].mean().mean())
    mshape = ppa.month_shape(hist)
    winter_factor = float(mshape.loc[[10, 11, 12, 1, 2, 3]].mean())
    base_annual = winter_bmrp / winter_factor
    summary["forward"] = {"winter_bmrp": winter_bmrp, "winter_shape_factor": winter_factor,
                          "base_annual": base_annual, "month_shape": mshape.round(4).to_dict()}

    avail_ppa = {m: sorted(y for y, mm in hist.index if mm == m) for m in range(1, 13)}
    rng = np.random.default_rng(42)
    draws = sim.sample_months(avail_ppa, N_SIMS, PPA_YEARS, rng)
    levels = sim.level_paths(N_SIMS, PPA_YEARS, LEVEL_VOL, rng)

    def fixed(per_gw=reg["per_gw"], path=gw, lv=None, imb_=imb):
        lev = levels if lv is None else sim.level_paths(N_SIMS, PPA_YEARS, lv, np.random.default_rng(7))
        s = ppa.simulate_ppa(hist, draws, lev, base_annual, per_gw, path)
        return ppa.price_fixed_ppa(s, base_annual, imb_, RATE, COC, Q)

    ppa_base = fixed()
    sens = {
        "base": ppa_base["fixed_price"],
        "no cannibalisation (slope 0)": fixed(per_gw=0.0)["fixed_price"],
        "slope -1 s.e.": fixed(per_gw=reg["per_gw"] - reg["se"])["fixed_price"],
        "slope +1 s.e.": fixed(per_gw=reg["per_gw"] + reg["se"])["fixed_price"],
        "45 GW reached in 2035, not 2030": fixed(path=gw_slow)["fixed_price"],
        "level vol 10%": fixed(lv=0.10)["fixed_price"],
        "level vol 25%": fixed(lv=0.25)["fixed_price"],
        "site nMAE 8%": fixed(imb_=float(bal.loc[bal["case"] == "site nMAE 8%", "cost_per_mwh"].iloc[0]))["fixed_price"],
    }
    summary["fixed_ppa"] = ppa_base | {"sensitivity": sens}
    print(json.dumps(summary["fixed_ppa"], indent=1))

    # C. battery backtest
    daily = battery(df)
    summary["forecast_mae"] = json.loads((OUT / "gb_pricing_forecast_mae.json").read_text())
    rows = []
    for (config, case), d in daily.groupby(level=[0, 1]):
        d = d.droplevel([0, 1])
        tot = d["wholesale"] + d["response"]
        row = {"config": config, "case": case}
        for y, v in (tot.groupby(d.index.year).mean() * 365).items():
            row[f"gbp_mw_yr_{y}"] = v
        win = d.loc["2025-09-01":"2026-08-31"]
        row["sep25_aug26"] = float((win["wholesale"] + win["response"]).mean() * 365)
        row["sep25_aug26_bm"] = float(win["bm"].mean() * 365)
        row["response_share"] = float(d["response"].sum() / tot.sum())
        rows.append(row)
    bt = pd.DataFrame(rows)
    pf = bt.set_index(["config", "case"])["sep25_aug26"]
    bt["share_of_perfect"] = [
        r["sep25_aug26"] / pf.get((r["config"], f"perfect, deg {DEG_COST:g}"), np.nan) for _, r in bt.iterrows()
    ]
    bt.to_csv(OUT / "gb_pricing_bess_annual.csv", index=False)
    print(bt.round(2).to_string())
    summary["bess_backtest"] = bt.to_dict(orient="records")
    cm_2526 = CM_T4["2025/26"] * 1000 * CM_DERATE_2H
    base_case = f"ex-ante, deg {DEG_COST:g}"
    b = bt.set_index(["config", "case"]).loc[("2h stacked, cap 50%", base_case)]
    summary["modo_comparison_sep25_aug26"] = {
        "wholesale_plus_response": float(b["sep25_aug26"]), "bm_estimate": float(b["sep25_aug26_bm"]),
        "capacity_market": cm_2526,
        "total": float(b["sep25_aug26"] + b["sep25_aug26_bm"] + cm_2526),
        "modo_index_gbp_mw_yr": 69000,
    }
    print(summary["modo_comparison_sep25_aug26"])

    # C. floor and toll: 2h, response cap 50%, ex-ante plan, wholesale + response (BM and CM excluded)
    bess_m = monthly(daily, "2h stacked, cap 50%", base_case)
    avail_b = {m: sorted(y for y, mm in bess_m.index if mm == m) for m in range(1, 13)}
    rng_b = np.random.default_rng(43)
    draws_b = sim.sample_months(avail_b, N_SIMS, BESS_YEARS, rng_b)
    lev_b = sim.level_paths(N_SIMS, BESS_YEARS, LEVEL_VOL, rng_b)
    rev = sim.lookup(bess_m.to_dict(), draws_b).sum(axis=2) * lev_b
    floors = [bess.price_floor(rev, f, RATE, COC, Q) for f in (30_000, 40_000, 50_000, 60_000)]
    toll = bess.price_toll(rev, RATE, COC, Q)
    cm_years = {y: CM_T4[y] * 1000 * CM_DERATE_2H for y in ("2027/28", "2028/29", "2029/30")}
    summary["bess_contracts"] = {"floors": floors, "toll": toll, "cm_owner_retained_gbp_mw_yr": cm_years,
                                 "annual_revenue_p5_p50_p95": np.quantile(rev, [0.05, 0.5, 0.95]).tolist()}
    print(pd.DataFrame(floors).round(0).to_string(), toll)

    # D. portfolio on joint draws: months with both solar and battery history
    avail = {m: sorted(set(avail_ppa[m]) & set(avail_b[m])) for m in range(1, 13)}
    rng_j = np.random.default_rng(44)
    dj = sim.sample_months(avail, N_SIMS, PPA_YEARS, rng_j)
    lj = sim.level_paths(N_SIMS, PPA_YEARS, LEVEL_VOL, rng_j)
    sj = ppa.simulate_ppa(hist, dj, lj, base_annual, reg["per_gw"], gw)
    rev10 = sim.lookup(bess_m.to_dict(), dj).sum(axis=2) * lj
    toll10 = bess.price_toll(rev10, RATE, COC, Q)["toll"]
    dfac = sim.discount_factors(PPA_YEARS, RATE)
    pnl_solar = SITE_MW * (sj["value"] - (ppa_base["fixed_price"] + imb) * sj["energy"])
    pnl_bess = BESS_MW * (rev10 - toll10)
    npv = {"solar PPA": (pnl_solar * dfac).sum(axis=1), "battery toll": (pnl_bess * dfac).sum(axis=1)}
    npv["book"] = npv["solar PPA"] + npv["battery toll"]
    hedge_unit = (8760 * base_annual * (1 - lj) * dfac).sum(axis=1)
    hedge_mw = float(-np.cov(npv["book"], hedge_unit)[0, 1] / hedge_unit.var())
    npv["book + baseload hedge"] = npv["book"] + hedge_mw * hedge_unit
    port: dict = {k: {"mean": float(v.mean()), "std": float(v.std()), "p5": float(np.quantile(v, 0.05)),
                      "cvar95": sim.cvar(v, Q, upper=False)} for k, v in npv.items()}
    port["corr_npv"] = float(np.corrcoef(npv["solar PPA"], npv["battery toll"])[0, 1])
    port["baseload_hedge_mw"] = hedge_mw
    port["toll_10y"] = toll10
    hv = (hist["capture"] * hist["mwh_per_mw"]).rename("solar_value")
    joint = pd.concat([hv, bess_m.rename("bess")], axis=1).dropna()
    port["corr_monthly_history"] = float(joint.corr().iloc[0, 1])
    port["corr_monthly_history_deseasonalised"] = float(
        joint.groupby(level=1).transform(lambda x: x - x.mean()).corr().iloc[0, 1])
    port["history_months"] = int(len(joint))
    scen = {}
    for name, lv_mult, cap_mult, bess_mult in (
        ("base", 1.0, 1.0, 1.0), ("prices -30%", 0.7, 1.0, 1.0), ("prices +30%", 1.3, 1.0, 1.0),
        ("capture ratio -10%", 1.0, 0.9, 1.0), ("battery revenue -30%", 1.0, 1.0, 0.7),
    ):
        s_ = SITE_MW * (sj["value"] * lv_mult * cap_mult - (ppa_base["fixed_price"] + imb) * sj["energy"])
        b_ = BESS_MW * (rev10 * lv_mult * bess_mult - toll10)
        scen[name] = {"solar": float((s_ * dfac).sum(axis=1).mean() / 1e6),
                      "battery": float((b_ * dfac).sum(axis=1).mean() / 1e6)}
        scen[name]["book"] = scen[name]["solar"] + scen[name]["battery"]
    port["scenarios_gbp_m"] = scen
    summary["portfolio"] = port
    print(json.dumps({k: v for k, v in port.items() if k != "scenarios_gbp_m"}, indent=1))

    (OUT / "gb_pricing_summary.json").write_text(json.dumps(summary, indent=2, default=float))
    charts(bal, floors, npv, ppa_base, hist, reg)


def charts(bal, floors, npv, wf, hist, reg):
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(bal["case"], bal["cost_per_mwh"], label="expected imbalance cost")
    ax.bar(bal["case"], bal["risk_charge"], bottom=bal["cost_per_mwh"], label="risk charge")
    ax.set_ylabel("GBP per MWh of solar output")
    ax.set_title("Solar balancing fee: national fleet vs a single 40 MW site")
    ax.set_ylim(0, 1.15 * bal["fee"].max())
    ax.legend(frameon=False, loc="upper left", ncols=2)
    fig.tight_layout()
    fig.savefig(OUT / "gb_pricing_balancing_fee.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 4))
    f = pd.DataFrame(floors)
    ax.plot(f["floor"] / 1e3, f["fair_premium"] / 1e3, marker="o", label="expected top-up")
    ax.plot(f["floor"] / 1e3, f["premium"] / 1e3, marker="o", label="with risk charge")
    ax.set_xlabel("Floor, GBP k/MW/yr")
    ax.set_ylabel("Annual cost to the optimiser, GBP k/MW")
    ax.set_title("2-hour battery: cost of a 5-year revenue floor\n(ex-ante plan, response capped at 50% of power)",
                 fontsize=10)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(OUT / "gb_pricing_bess_floor.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 3.6))
    names = list(npv)[::-1]
    for i, k in enumerate(names):
        p5, p50, p95 = np.quantile(npv[k], [0.05, 0.5, 0.95]) / 1e6
        ax.plot([p5, p95], [i, i], lw=6, color="#9db4d0", solid_capstyle="butt")
        ax.plot([p50], [i], "o", color="#1f3b63")
    ax.set_yticks(range(len(names)), names)
    ax.axvline(0, color="grey", lw=0.8)
    ax.set_xlabel("Offtaker 10-year NPV, GBP m (bar 5th–95th percentile, dot median)")
    ax.set_title("40 MW solar PPA + 20 MW / 40 MWh battery toll", fontsize=11)
    fig.tight_layout()
    fig.savefig(OUT / "gb_pricing_portfolio.png", dpi=150)
    plt.close(fig)

    steps = [("Baseload\nforward", wf["baseload"]), ("Hourly profile &\ncannibalisation", wf["hourly_profile_cost"]),
             ("Profile/volume\ncovariance", wf["profile_volume_covariance"]), ("Balancing", wf["balancing_cost"]),
             ("Profile &\nvolume risk", wf["profile_volume_risk"]), ("Price\nrisk", wf["price_risk"])]
    fig, ax = plt.subplots(figsize=(8, 4))
    level = 0.0
    for i, (_, v) in enumerate(steps):
        bottom = 0 if i == 0 else level + min(v, 0)
        colour = "#1f3b63" if i == 0 else ("#c0504d" if v < 0 else "#4f81bd")
        ax.bar(i, abs(v), bottom=bottom, color=colour)
        ax.text(i, bottom + abs(v) + 1, f"{v:+.1f}" if i else f"{v:.1f}", ha="center", fontsize=8)
        level += v
    ax.bar(len(steps), wf["fixed_price"], color="#2e7d32")
    ax.text(len(steps), wf["fixed_price"] + 1, f"{wf['fixed_price']:.1f}", ha="center", fontsize=8)
    ax.set_xticks(range(len(steps) + 1), [s[0] for s in steps] + ["Fixed\nprice"], fontsize=8)
    ax.set_ylabel("GBP/MWh")
    ax.set_title("10-year GB solar PPA: from baseload forward to fixed price", fontsize=11)
    fig.tight_layout()
    fig.savefig(OUT / "gb_pricing_ppa_waterfall.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 4))
    sc = ax.scatter(hist["cap_gw"], hist["kappa"], c=hist.index.get_level_values("month"), cmap="viridis", s=22)
    ax.set_xlabel("Installed GB solar, GW (PV_Live)")
    ax.set_ylabel("Monthly solar capture ratio (day-ahead)")
    ax.set_title(f"Capture ratio vs installed solar: {reg['per_gw']:+.4f} per GW (t = {reg['t']:.1f}),\n"
                 "after calendar-month effects", fontsize=10)
    fig.colorbar(sc, label="month")
    fig.tight_layout()
    fig.savefig(OUT / "gb_pricing_capture_vs_capacity.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
