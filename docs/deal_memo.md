# Pricing memo: 40 MW GB solar PPA and 20 MW / 40 MWh battery

Personal build on public data, prices as of the November 2026 data. Illustrative: the
assets are notional and the assumptions are listed in the README. Source numbers:
`output/gb_pricing_summary.json`.

## Recommendation

| Contract | Price | Main risk |
|---|---|---|
| Solar balancing PPA (route to market) | **£0.87/MWh** imbalance fee on top of operating cost and margin | National forecast error landing on a short system |
| Solar fixed-price PPA, 10 years, pay as produced | **£83.5/MWh** | Price level; solar cannibalisation |
| Battery revenue floor, 5 years | **£2,300/MW/yr** for a £40k floor; **£7,000** for £50k | Response prices falling as more batteries compete |
| Battery toll, 5 years | **£47,300/MW/yr** | Same, plus optimiser skill |

Writing both the solar PPA and the toll leaves the book long the GB price level.
Hedge it: about **4.3 MW of baseload sold forward** offsets the level exposure of this
pair.

## Solar fixed price, from the forward

| | GBP/MWh |
|---|---|
| Forward baseload (LCCC winter 2026/27 BMRP £110.3 → annual) | 106.7 |
| Capture ratio 0.83, falling 1% a year | −18.5 |
| Imbalance | −0.8 |
| Risk charge | −3.8 |
| **Price** | **83.5** |

The capture assumption moves the price most: no further cannibalisation gives £86.9,
a 2%-a-year fall gives £80.4. Price-level volatility of 10% or 25% instead of 15% moves it to £84.5 or
£81.9. The site forecast quality barely matters for the fixed price.

## Battery

Backtested on 2024–26 GB prices, a 2-hour battery earned £53k–70k/MW/yr from
September 2025 to August 2026, depending on how well it forecasts prices (the market
index for those months: £69k). Without a cap it would hold most of its power in
frequency response; the pricing allows at most half, because those markets are thin.
Floor and toll are priced on the lower (persistence) revenues: one year in four falls
below £40k, three in five below £50k.

## Book risk

| 10-year NPV, GBP m | Mean | P5 |
|---|---|---|
| Solar PPA | 0.9 | −7.0 |
| Battery toll | 0.3 | −2.3 |
| Both | 1.2 | −9.3 |
| Both, with the baseload hedge | 1.2 | 0.9 |

A 30% fall in prices costs the unhedged book £8.5m against base. A 10% lower capture
ratio costs £2.1m; a 30% fall in battery revenue costs £2.1m. The hedged figure
reflects only the risks this model contains: no basis risk between forward and spot,
and battery revenue assumed to move with the price level.

## What would change the numbers

- A full forward curve instead of one winter season.
- Day-ahead auction prices instead of the APX market index.
- Balancing Mechanism revenue for the battery and curtailment for the solar farm.
- A site's own forecast record instead of an assumed error size.
