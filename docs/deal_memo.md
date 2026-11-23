# Pricing memo: 40 MW GB solar PPA and 20 MW / 40 MWh battery

Personal build on public data to October 2026. Illustrative: the assets are notional
and the assumptions are listed in the README. Source numbers:
`output/gb_pricing_summary.json`.

## Recommendation

| Contract | Price | Main risk |
|---|---|---|
| Solar balancing PPA (route to market) | **£1.58/MWh** imbalance fee, before operating cost and margin | National solar forecast error landing on a short system |
| Solar fixed-price PPA, 10 years from 2027, pay as produced | **£63.8/MWh** | Cannibalisation as GB solar heads for 45 GW |
| Battery revenue floor, 5 years | **£1,830/MW/yr** for a £40k floor; **£5,810** for £50k | Response and spread revenue falling as more batteries compete |
| Battery toll, 5 years | **£49,700/MW/yr**, owner keeps Capacity Market income | Same, plus optimiser forecasting skill |

Writing both the solar PPA and the toll leaves the book long the GB price level. About
**3.5 MW of baseload sold forward** offsets the level exposure of this pair.

## Solar fixed price, from the forward

| | GBP/MWh |
|---|---|
| Forward baseload (LCCC winter 2026/27 BMRP £110.3 → annual) | 105.2 |
| Hourly profile and cannibalisation (capture ratio 0.65 over the term) | −37.1 |
| Balancing cost | −1.5 |
| Profile/volume covariance, profile and volume risk, price risk | −2.9 |
| **Price** | **63.8** |

One assumption decides this price: how fast cannibalisation continues. GB's measured
capture ratio has fallen by about 0.01 for every extra GW of solar since 2021 (0.92 in
2023 to 0.84 in 2025). Carried to the Clean Power 2030 build-out, that gives £63.8;
reaching 45 GW five years later gives £72.0; no further fall gives £89.0. The slope is
fitted on 14–25 GW of solar and extrapolated well beyond, and faster battery build-out
would soften it. I would quote the base case and show the £72 case alongside.

## Battery

On 2023–26 GB prices, a 2-hour battery planning on a day-ahead price forecast (not
hindsight) earned £55,800/MW/yr from wholesale trading and frequency response in
September 2025 – August 2026, 87% of what perfect foresight would have earned. Adding
an estimate for the Balancing Mechanism (£3,500) and the Capacity Market (£6,400) gives
£65,600, against £69,000 for Modo Energy's GB 2-hour index over the same months.
Response is capped at half the battery's power because those markets are thin.

The floor and toll are priced on wholesale and response revenue only: one simulated year
in five falls below £40k, one in two below £50k.

## Book risk

| 10-year NPV, GBP m | Mean | P5 |
|---|---|---|
| Solar PPA | 0.6 | −5.3 |
| Battery toll | 0.3 | −2.4 |
| Both | 1.0 | −7.7 |
| Both, with the baseload hedge | 0.9 | 0.5 |

A 30% fall in prices costs the unhedged book £7.1m against base; a 10% lower capture
ratio costs £1.6m; a 30% fall in battery revenue costs £2.2m. The hedged figure reflects
only the risks this model contains: no basis risk between forward and spot, and battery
revenue assumed to move with the price level.

## What would change the numbers

- A full forward curve instead of one winter season.
- A fundamentals view of capture prices beyond 25 GW of solar (for example PyPSA-GB),
  in place of extrapolating the 2021–26 slope.
- Balancing Mechanism dispatch modelled per unit instead of a 3% acceptance rate.
- A site's own forecast record instead of an assumed error size.
