# European power fundamentals monitor

Hourly day-ahead prices, load and renewable output for Germany, Spain and France,
January 2023 to September 2026, joined to Dutch TTF gas and EU carbon prices.
It measures what a renewable asset owner hedging merchant output cares about:
how much of the baseload price solar and wind actually capture, how often prices
go negative, where gas sets the price, and what a gas plant earns.

Personal build, public data only.

## Data

| Series | Source | Notes |
|---|---|---|
| Day-ahead price, load, residual load, solar, wind, gas generation | [Energy-Charts API](https://api.energy-charts.info) (Fraunhofer ISE; ENTSO-E / SMARD), CC BY 4.0 | 15-minute data averaged to hourly; bidding zones DE-LU, ES, FR |
| TTF front month (EUR/MWh) | Yahoo Finance `TTF=F` | daily close, forward-filled over non-trading days |
| EUA (EUR/t) | Yahoo Finance `CO2.L` (SparkChange physical EUA ETC) | daily proxy for the EUA price; tracks spot EUA less fees |

All three markets are grouped by CET delivery time.

## Method

- **Capture rate** = generation-weighted average price ÷ time-weighted (baseload) price.
- **Gas SRMC** (CCGT short-run marginal cost) = (TTF + 0.202 × EUA) ÷ 0.4913, the standard clean-spark-spread convention (49.13% efficiency, 0.202 tCO2 per MWh of gas).
- **Clean spark spread** = daily baseload price − gas SRMC.
- **Gas near the margin** = share of hours with price within ±15% of gas SRMC. A rough proxy only.
- **Price regression**: hourly OLS of price on gas SRMC and residual load (GW), by market and year, with HAC (Newey-West, 24 lags) errors.

## Results (2026 = January to September)

| Market | Year | Baseload (EUR/MWh) | Solar capture rate | Wind capture rate | Negative-price hours | Regression R² | +1 GW residual load (EUR/MWh) |
|---|---|---|---|---|---|---|---|
| DE | 2023 | 95.2 | 0.76 | 0.84 | 301 | 0.77 | +3.0 |
| DE | 2024 | 78.5 | 0.59 | 0.84 | 457 | 0.66 | +3.1 |
| DE | 2025 | 89.3 | 0.52 | 0.88 | 576 | 0.77 | +3.1 |
| DE | 2026 | 107.7 | 0.53 | 0.90 | 471 | 0.73 | +3.4 |
| ES | 2023 | 87.1 | 0.84 | 0.87 | 0 | 0.59 | +5.0 |
| ES | 2024 | 63.0 | 0.67 | 0.88 | 247 | 0.76 | +4.7 |
| ES | 2025 | 65.3 | 0.55 | 0.97 | 556 | 0.72 | +5.6 |
| ES | 2026 | 74.1 | 0.52 | 0.92 | 747 | 0.77 | +6.5 |
| FR | 2023 | 96.9 | 0.86 | 0.89 | 147 | 0.54 | +3.0 |
| FR | 2024 | 58.0 | 0.68 | 0.90 | 352 | 0.60 | +2.9 |
| FR | 2025 | 61.1 | 0.59 | 0.90 | 513 | 0.66 | +3.2 |
| FR | 2026 | 81.5 | 0.55 | 0.90 | 563 | 0.53 | +4.5 |

Full table: `output/yearly_fundamentals.csv`; regressions: `output/price_regression.json`.

What it shows:

- **Solar cannibalisation.** The German solar capture rate fell from 0.76 in 2023 to 0.52 in 2025, and Spain's from 0.84 to 0.55. A pay-as-produced solar PPA priced off baseload now earns roughly half of it.
- **Negative prices spread south.** Spain had no negative hour in 2023 and 747 in the first nine months of 2026.
- **Wind holds up.** Wind capture rates stay at 0.84 to 0.97, because wind output is less concentrated in the cheapest hours than solar.
- **Merit order.** Residual load and gas SRMC explain 53% to 77% of hourly price variance. In Germany each extra GW of residual load adds about EUR 3/MWh, and the curve steepens sharply above about 45 GW (`output/de_merit_order_q3_2026.png`).
- **Gas plant economics follow a duck curve.** The German clean spark spread averages negative in both baseload and the 08:00–20:00 weekday peak block, because solar depresses the midday hours (2025 mean at 13:00: EUR −57/MWh). A standard CCGT earns in the morning and evening ramps instead (2025 mean at 19:00: EUR +33/MWh): the spread was positive in 37% of 2025 hours, averaging EUR +28/MWh in those hours (`scripts/export_web.py`).

## Hedging a solar asset with baseload futures

`powerfund/hedge.py` backtests a notional 100 MW German solar asset (hourly output = national solar output ÷ installed AC capacity, from Energy-Charts) over the 33 months January 2024 – September 2026. Each month the owner sells a baseload forward for h × expected output and is judged on revenue versus a budget set at the start of the month:

- expected output = same month last year's capacity factor × capacity × hours;
- forward price = last month's realised baseload average (proxy: there is no free history of EEX month futures);
- expected capture rate = same month last year's;
- budget = expected output × forward × expected capture rate.

| Hedge (share of expected volume) | Revenue vs budget, std | Worst month |
|---|---|---|
| 0% (merchant) | 37.6% | −49.9% |
| 70% | 26.1% | −44.2% |
| **100%** | **24.4%** | −43.2% |
| Capture-weighted (≈72%) | 29.2% | −44.9% |

- **Hedge volume, not value.** The variance-minimising ratio is 80–110% of expected volume across three forward proxies (previous month, trailing three months, seasonally adjusted). Hedging only the capture-weighted share leaves more risk. The reason: solar's capture price moves almost one-for-one in euros with baseload (slope 0.95), so the shape discount behaves like a fixed euro amount rather than a fixed percentage, and price surprises and capture surprises are positively correlated (0.48).
- **Shape is the largest risk, and baseload cannot hedge it.** Of the unhedged revenue variance, 48–56% is shape (capture rate vs expectation), 31–44% price and 8–13% volume. The best baseload hedge cuts variance by about a third, but the worst month is still 43% below budget: the case for shaped products, PPAs priced on capture and co-located storage.

## GB balancing and wind imbalance

A second module looks at real-time balancing in Great Britain: how the
cash-out (imbalance) price relates to the traded market, and what a day-ahead
wind forecast error costs when it is settled at that price.

**Data** (Elexon BMRS API, half-hourly settlement periods, UK time):

| Series | Dataset | Window |
|---|---|---|
| System price and net imbalance volume (NIV) | DISEBSP | Jan 2024 to Oct 2026 |
| Market index price, APX (volume-weighted short-term traded price) | MID | Jan 2024 to Oct 2026 |
| Wind outturn, onshore + offshore | AGWS | Jan 2025 to Oct 2026 |
| Day-ahead wind forecast, onshore + offshore | DGWS | Jan 2025 to Oct 2026 |

Contains BMRS data © Elexon Limited copyright and database right 2026
([licence](https://www.elexon.co.uk/bsc/data/balancing-mechanism-reporting-agent/copyright-licence-bmrs-data/)).

**Method.**
- GB has a single imbalance price: the system sell and buy prices were equal in every one of the 49,680 periods. NIV > 0 means the system was short.
- A wind portfolio that sells its day-ahead forecast F at the market index price and settles the difference to outturn A at the system price SP earns F × MID + (A − F) × SP. Against a perfect forecast its imbalance cost is (F − A) × (SP − MID).
- The mean cost per period splits into forecast bias × average premium and the covariance of forecast error with the premium (the wrong-way term: under-delivering when the system is short).

**Results** (`output/gb_yearly.csv`, `gb_spread_by_niv.csv`, `gb_wind_imbalance_monthly.csv`, `gb_summary.json`):

| | 2024 | 2025 | 2026 (Jan to Oct) |
|---|---|---|---|
| System price, mean (GBP/MWh) | 71.2 | 80.6 | 108.0 |
| System price, max | 669 | 2,900 | 800 |
| Share of periods system short | 47.6% | 46.0% | 47.7% |
| SP − MID when short / long (GBP/MWh) | +19.6 / −18.2 | +22.2 / −17.6 | +21.7 / −18.9 |
| Wind capture vs time-weighted MID | — | 0.90 | 0.89 |
| Day-ahead wind forecast MAE, % of mean output | — | 18.6% | 23.9% |
| Imbalance cost, GBP per MWh of wind | — | 0.44 | 0.26 |
| of which wrong-way term | — | 0.33 | 0.14 |

- **The sign of the imbalance sets the cash-out premium more than its size.** Short periods average about £20/MWh above the market index; long periods about £18 below. The premium only creeps up with the size of the imbalance: +£20 at 0–250 MWh short to +£28 above 1,000 MWh (`gb_spread_by_niv.png`). Spikes are rare: 0.06% of periods above £500.
- In 11.5% of periods the system price equalled the market index exactly (15.3% when long, 7.2% when short).
- **The national day-ahead wind forecast runs high.** It averaged 1.8 GW above outturn in 2025–26, about 18% of mean output. Part of this is likely wind curtailed for transmission constraints, which lowers metered outturn but is paid through the Balancing Mechanism, not settled as imbalance (not tested here).
- **At national scale the imbalance cost is small:** £0.36 per MWh of wind over Jan 2025 to Oct 2026, 0.4% of output value at the market index; two-thirds of it is the wrong-way term. The monthly range was −£1.73 to +£1.99 per MWh (`gb_wind_imbalance_cost.png`).

**Caveats.**
- This is the national wind portfolio. A single farm's forecast error is far less diversified, so its imbalance cost is higher; this does not measure it.
- MID (APX) is a short-term traded index, not the day-ahead auction price, so "sell the forecast at MID" is a simplification.
- The wind forecast is the latest day-ahead publication (around 16:45 the day before), after the GB day-ahead auctions close.
- AGWS wind outturn is inconsistent through 2024 (June 2024 averages about 3.6 GW against about 8.3 GW in both the day-ahead forecast and FUELHH transmission-metered wind), so the wind analysis starts in January 2025.
- Curtailment and Balancing Mechanism payments to wind are not modelled.

## GB deal pricing: solar PPAs and battery floors and tolls

A third module prices the contracts an energy trader writes for GB renewable and
storage assets: a balancing (route-to-market) PPA fee for a solar farm, a fixed-price
pay-as-produced solar PPA, and a revenue floor and toll for a battery. It then puts a
solar PPA and a battery toll in one book. A one-page deal memo built from these
numbers is in [`docs/deal_memo.md`](docs/deal_memo.md).

Notional assets: a 40 MW solar farm with the national PV_Live output profile, and a
2-hour battery (results per MW; 20 MW / 40 MWh in the portfolio). Window: January 2024
to October 2026, half-hourly.

**Data** (all public, no key):

| Series | Source |
|---|---|
| System price, NIV, market index (MID, APX) | Elexon BMRS, as above |
| GB solar outturn and latest day-ahead solar forecast | Elexon BMRS (AGWS / DGWS, solar) |
| National solar output and installed capacity | [PV_Live](https://www.solar.sheffield.ac.uk/pvlive/), Sheffield Solar, University of Sheffield |
| DC / DM / DR clearing prices per EFA block | [NESO data portal](https://www.neso.energy/data-portal/eac-auction-results), EAC results summary (Nov 2023 onward), OGL |
| Forward baseload: winter 2026/27 Baseload Market Reference Price (LEBA forward-season trades) | [Low Carbon Contracts Company](https://www.lowcarboncontracts.uk/data-portal/dataset/forecast-market-reference-prices), forecast market reference prices, OGL |

Run: `uv run python scripts/run_pricing.py` (first run fetches and caches `data/gb/` and
`data/gb_pricing/`, then takes a few minutes for about 8,000 daily battery LPs; the daily battery results
are cached in `output/gb_pricing_bess_daily.csv`, so delete that file to re-run them).

### A. Solar balancing PPA fee

The offtaker pays the market index for every metered MWh and sells the farm's
day-ahead forecast, so it carries (forecast − actual) × (system price − MID). A single
site's forecast error is modelled as the national day-ahead solar error plus a
site-specific AR(1) error proportional to output, sized to an assumed daylight nMAE
(4%, 6% or 8% of capacity; the national forecast's own nMAE is 2.6%), 30 random paths
each.

| Case | Expected imbalance cost | 2024 / 2025 / 2026 | Risk charge | Fee (GBP/MWh) |
|---|---|---|---|---|
| National solar fleet | 0.79 | 0.96 / 0.94 / 0.45 | 0.02 | 0.81 |
| Site, nMAE 4% | 0.80 | 0.95 / 0.96 / 0.47 | 0.04 | 0.84 |
| Site, nMAE 6% | 0.81 | 0.95 / 0.98 / 0.48 | 0.05 | 0.87 |
| Site, nMAE 8% | 0.81 | 0.94 / 0.99 / 0.48 | 0.07 | 0.88 |

- **The expected cost comes from the national error, not the site's own.** Doubling the
  site error (4% → 8%) adds only £0.01/MWh of expected cost, because a site-specific
  miss is as likely to fall in a long period as a short one. What it adds is variance,
  so the risk charge grows from £0.04 to £0.07.
- Per MWh, solar's imbalance cost is about twice the £0.36 measured for national wind
  in the module above (different windows: 2024–26 here, 2025–26 there).
- The fee here covers imbalance only: no operating cost, credit or margin, and no
  value for embedded benefits or REGOs.

### B. Fixed-price solar PPA (10 years)

Monte Carlo, 20,000 paths. Each simulated year takes every calendar month from a
randomly chosen historical year (2024–2026), so solar output, capture ratio and
battery revenue stay jointly consistent within a month. The price level follows a
lognormal random walk (15% a year) around a forward anchor: LCCC's winter 2026/27
BMRP, **£110.3/MWh**, converted to an annual baseload of **£106.7/MWh** with the
historical monthly shape.

| Waterfall (GBP/MWh) | |
|---|---|
| Forward baseload, annual | 106.7 |
| Solar shape and cannibalisation (capture ratio 0.83, falling 1% a year) | −18.5 |
| Imbalance (site nMAE 6%) | −0.8 |
| Risk charge: 10% × (mean − CVaR 5% of NPV) | −3.8 |
| **Fixed price** | **83.5** |

Measured GB solar capture ratio against MID: 0.89 (2024), 0.82 (2025), 0.82 (2026 to
October); 7–8% of solar output in 2025–26 fell in negative-price periods.

| Sensitivity | Fixed price |
|---|---|
| Base | 83.5 |
| No cannibalisation drift | 86.9 |
| Capture ratio falling 2% a year | 80.4 |
| Price-level volatility 10% / 25% | 84.5 / 81.9 |
| Site nMAE 8% | 83.5 |

### C. Battery revenue, floor and toll

One linear programme per day (HiGHS): wholesale trading against the half-hourly MID
plus capacity held in DC, DM and DR, each sold as a symmetric low + high pair per EFA
block with the energy each service needs held in reserve. 88% round-trip efficiency, at
most 1.5 cycles a day, starting and ending each day half full. Two foresight cases:
perfect (an upper bound) and persistence (plan on yesterday's prices, settle at
today's).

| 2-hour battery, GBP/MW/yr | 2024 | 2025 | 2026 (to Oct) | Response share |
|---|---|---|---|---|
| Wholesale only, perfect foresight | 34,900 | 40,200 | 47,300 | 0% |
| Wholesale only, persistence | 14,200 | 18,600 | 21,400 | 0% |
| Stacked, perfect foresight | 58,300 | 108,500 | 98,200 | 83% |
| Stacked, response ≤ 50% of power, perfect foresight | 49,500 | 77,300 | 75,500 | 57% |
| Stacked, response ≤ 50% of power, persistence | 32,000 | 60,900 | 55,500 | 77% |

- With no limit, response pays more than trading: the optimiser holds the battery in
  Regulation and Moderation most of the time. That treats one battery as a price-taker
  in markets that clear at most about 0.5 GW (DM, DR) and 1.5 GW (DC) per product and direction, so the
  contract base case caps response at half the battery's power (an assumption).
- For September 2025 to August 2026 the capped case earns **£69,500/MW/yr** with
  perfect foresight and **£53,000** with persistence. Modo Energy's GB 2-hour index for
  the same months averaged £69k, with the day-ahead TB2 spread implying £46k
  ([Modo](https://modoenergy.com/research/gb-bess-revenue-swaps-fca-regulated-index)).
  The model brackets the index; it does not include the Balancing Mechanism, which the
  index does.

Contracts are priced on the capped persistence revenues (conservative for the
optimiser), 5 years, 7% discount rate, risk charge 10% × the tail beyond the mean
(CVaR 95%). Simulated annual revenue: P5 £29,900, P50 £47,400, P95 £73,800 per MW.

| Floor (GBP/MW/yr) | Years triggered | Expected top-up | With risk charge |
|---|---|---|---|
| 30,000 | 5% | 175 | 410 |
| 40,000 | 26% | 1,500 | 2,300 |
| 50,000 | 58% | 5,650 | 6,990 |
| 60,000 | 82% | 12,800 | 14,420 |

Toll: expected revenue £49,100, less a £1,800 risk charge: **£47,300/MW/yr** fixed.

### D. Portfolio: 40 MW solar PPA + 20 MW / 40 MWh battery toll

Both contracts at the prices above, on the same 20,000 paths, 10 years.

| Offtaker NPV, GBP m | Mean | Std | P5 |
|---|---|---|---|
| Solar PPA | 0.92 | 5.89 | −7.00 |
| Battery toll | 0.30 | 1.94 | −2.29 |
| Together | 1.22 | 7.82 | −9.28 |
| Together, plus 4.3 MW baseload sold forward | 1.18 | 0.18 | 0.91 |

- **The two contracts do not hedge each other.** Both leave the offtaker long the
  price level, so their NPVs move together (correlation 0.99). In history the monthly
  solar value and battery revenue are also positively correlated (0.21; 0.46 after
  removing the seasonal pattern).
- Selling 4.3 MW of flat baseload forward removes the price-level risk. The residual
  £0.18m standard deviation is what the model has left: shape, volume and battery-spread
  risk. This overstates real hedge effectiveness, because the model's forward and spot
  levels share one random walk (no basis risk) and battery revenue is assumed to scale
  with the price level.

| Scenario, expected NPV, GBP m | Solar | Battery | Book |
|---|---|---|---|
| Base | 0.92 | 0.30 | 1.22 |
| Prices −30% | −5.47 | −1.77 | −7.24 |
| Prices +30% | 7.31 | 2.37 | 9.68 |
| Capture ratio −10% | −1.21 | 0.30 | −0.91 |
| Battery revenue −30% | 0.92 | −1.77 | −0.85 |

### Assumptions and limits

| Item | Value | Status |
|---|---|---|
| Site forecast error | daylight nMAE 4–8% of capacity, AR(1) ρ = 0.95 per half-hour | chosen |
| Forward curve | one winter BMRP, flat in real terms after; monthly shape from 2024–26 | chosen; no free GB forward curve |
| Price-level volatility | 15% a year | chosen |
| Capture-ratio drift | −1% a year | chosen; measured ratio fell from 0.89 to 0.82 over 2024–26 |
| Battery | 88% RTE, 1.5 cycles/day, response ≤ 50% of power | chosen |
| Discount rate, cost of capital, tail | 7%, 10%, CVaR 95% | chosen |
| Prices | MID (APX short-term index), not the day-ahead auctions | data limit |
| Not modelled | Balancing Mechanism, intraday, Capacity Market, network charges, degradation, curtailment, credit, REGOs, embedded benefits | |

Three years of history is a short sample for a 10-year contract, and month-by-month
resampling cannot produce a regime the sample never saw.

## Caveats

- Since 1 October 2025 the day-ahead market clears in 15-minute periods; hourly averages smooth intra-hour negatives, so negative-hour counts from Q4 2025 onward can differ from counts on 15-minute data.
- The EUA series is an exchange-traded proxy, not the ICE EUA futures settlement.
- TTF is the front-month contract, not day-ahead gas.
- The hedge backtest uses a forward-price proxy, not traded EEX settlements, and a national solar profile, not a single site; 33 months is a short sample.
- The ±15% band test cannot tell gas from coal or imports when they clear at a similar level.

## Run

```
uv sync
uv run python scripts/run_analysis.py   # fetches and caches data/ on first run
uv run python scripts/run_gb.py         # GB balancing module (Elexon BMRS, no key)
uv run python scripts/run_pricing.py    # GB deal pricing (Elexon, PV_Live, NESO, LCCC)
uv run pytest
```
