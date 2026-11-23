"""Monte Carlo helpers shared by the PPA and battery pricers.

Future years are built from history month by month: every simulated year takes each
calendar month from a randomly chosen historical year, and the PPA and battery models
use the SAME draw, so the link between solar output, solar capture and battery
spreads within a month is kept. A common lognormal random walk scales the price level.
"""

import numpy as np


def sample_months(avail: dict[int, list[int]], n_sims: int, n_years: int, rng) -> np.ndarray:
    """Historical year to use for each (sim, contract year, calendar month 1..12): shape (n_sims, n_years, 12)."""
    out = np.empty((n_sims, n_years, 12), dtype=int)
    for m in range(1, 13):
        years = np.asarray(avail[m])
        out[:, :, m - 1] = years[rng.integers(0, len(years), size=(n_sims, n_years))]
    return out


def level_paths(n_sims: int, n_years: int, vol: float, rng) -> np.ndarray:
    """Mean-one lognormal random walk of the price level, shape (n_sims, n_years)."""
    z = rng.standard_normal((n_sims, n_years)) * vol
    t = np.arange(1, n_years + 1)
    return np.exp(np.cumsum(z, axis=1) - 0.5 * vol**2 * t)


def lookup(table: dict[tuple[int, int], float], draws: np.ndarray) -> np.ndarray:
    """Map (historical year, month) draws to values, shape (n_sims, n_years, 12)."""
    months = np.broadcast_to(np.arange(1, 13), draws.shape)
    return np.vectorize(lambda y, m: table[(y, m)])(draws, months)


def discount_factors(n_years: int, rate: float) -> np.ndarray:
    return (1 + rate) ** -np.arange(1, n_years + 1)


def cvar(x: np.ndarray, q: float, upper: bool) -> float:
    """Mean of the worst tail: above the q quantile if upper, else below the (1 - q) quantile."""
    if upper:
        return float(x[x >= np.quantile(x, q)].mean())
    return float(x[x <= np.quantile(x, 1 - q)].mean())
