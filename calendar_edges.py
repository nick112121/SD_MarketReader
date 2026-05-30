"""
Calendar / event time edges — the legitimate 'time-based' hypothesis.

Intraday clock-timing (Goldbach minutes) failed. But CALENDAR timing is driven
by real institutional mechanics (month-end rebalancing, options expiration,
holiday liquidity, fund flows) and can carry a directional tilt. This tests
those on decades of daily data, full-period and out-of-sample.

    python calendar_edges.py --ticker SPY
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
import yfinance as yf


def tstat(x):
    x = np.asarray(x, float); n = len(x)
    if n < 20:
        return 0.0, 0.0, n
    m = x.mean(); se = x.std(ddof=1) / np.sqrt(n)
    return m, (m / se if se else 0.0), n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="SPY")
    args = ap.parse_args()
    df = yf.download(args.ticker, period="max", interval="1d", progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna()
    c = df["Close"]; ret = (c.pct_change().shift(-1)).fillna(0)   # next-day return (act today)
    idx = df.index
    cut = int(len(df) * 0.6)

    print("=" * 78)
    print(f"  CALENDAR EDGES  {args.ticker}  {idx[0]:%Y-%m-%d}->{idx[-1]:%Y-%m-%d}  "
          f"(next-day return, bps; OOS = newest 40%)")
    print("=" * 78)

    def report(name, mask):
        mask = np.asarray(mask)
        m, t, n = tstat(ret[mask].values * 1e4)
        mo, to, no = tstat(ret[mask][cut <= np.arange(len(df))[mask]].values * 1e4) if False else (0,0,0)
        # OOS: restrict to test rows
        test_mask = mask & (np.arange(len(df)) >= cut)
        mo, to, no = tstat(ret[test_mask].values * 1e4)
        flag = "  <<" if abs(t) > 2 and abs(to) > 1.5 and np.sign(m) == np.sign(mo) else ""
        print(f"  {name:<26} n={n:<5} {m:+7.2f} bps  t={t:+5.2f} | OOS {mo:+7.2f} t={to:+5.2f}{flag}")

    gap = np.r_[(idx[1:] - idx[:-1]).days, [1]]
    wd = idx.weekday
    dom_rank = pd.Series(1, index=idx).groupby([idx.year, idx.month]).cumsum().values  # trading day # in month
    month = idx.month

    # baseline
    print(f"  {'ALL DAYS (baseline)':<26} n={len(ret)-1:<5} {ret[:-1].mean()*1e4:+7.2f} bps  "
          f"t={tstat(ret[:-1].values*1e4)[1]:+5.2f}")
    print("  " + "-" * 74)
    # pre-holiday: today is the session before an unusual calendar gap
    preh = ((wd < 4) & (gap >= 2)) | ((wd == 4) & (gap >= 4))
    report("PRE-HOLIDAY", preh)
    # turn of month: last trading day + first 3
    tom = np.zeros(len(idx), bool)
    for _, ix in df.groupby([idx.year, idx.month]).indices.items():
        for k in ix[:3]:
            tom[k] = True
        tom[ix[-1]] = True
    report("TURN-OF-MONTH", tom)
    # turn of year: December last 5 + January first 2
    toy = ((month == 12) & (dom_rank >= (pd.Series(dom_rank, index=idx).groupby([idx.year, idx.month]).transform("max").values - 4))) | \
          ((month == 1) & (dom_rank <= 2))
    report("TURN-OF-YEAR (Santa)", toy)
    # options expiration week (week containing the 3rd Friday)
    third_fri = (wd == 4) & (idx.day >= 15) & (idx.day <= 21)
    opex_week = pd.Series(third_fri, index=idx).rolling(5, min_periods=1).max().astype(bool).values
    report("OPEX WEEK", opex_week)
    # first half vs second half of month
    report("FIRST HALF of month", idx.day <= 15)
    report("SECOND HALF of month", idx.day > 15)
    print("  " + "-" * 74)
    # month-of-year tilt
    print("  month-of-year mean next-day return (bps):")
    mm = pd.Series(ret.values * 1e4, index=idx).groupby(idx.month).mean()
    print("   " + "  ".join(f"{['','J','F','M','A','M','J','J','A','S','O','N','D'][k]}{mm[k]:+.1f}" for k in range(1, 13)))

    print("=" * 78)
    print("  '<<' = significant full-period AND same-sign, t>1.5 out-of-sample.")
    print("=" * 78)


if __name__ == "__main__":
    main()
