"""
Morning expected move for NQ — the number to paste into the indicator.

Pulls VXN (the Nasdaq-100 implied-vol index, computed from NDX option prices --
i.e. the option chain's own forward-looking forecast) and the NQ price, and
prints the daily / weekly / monthly expected move in NQ points. Copy the daily
number into the indicator's `expMoveDaily` parameter each morning.

    python morning_em.py                 # uses yfinance NQ price
    python morning_em.py --price 21500   # use YOUR live NQ price (recommended)
"""

from __future__ import annotations

import argparse

import numpy as np
import yfinance as yf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--price", type=float, default=0, help="your live NQ price (else yfinance)")
    args = ap.parse_args()
    px = yf.download(["NQ=F", "^VXN"], period="5d", interval="1d",
                     progress=False, auto_adjust=True)["Close"].dropna()
    px.columns = [str(c) for c in px.columns]
    nq = args.price if args.price > 0 else float(px["NQ=F"].iloc[-1])
    vxn = float(px["^VXN"].iloc[-1])
    f = vxn / 100.0
    pct_d = f * np.sqrt(1/252)
    em_d = nq * pct_d
    em_w = nq * f * np.sqrt(5/252)
    em_m = nq * f * np.sqrt(21/252)
    r = lambda x: int(round(x / 5.0) * 5)   # round to nearest 5 pts

    print("=" * 52)
    print("  NQ MORNING EXPECTED MOVE")
    print("=" * 52)
    print(f"  NQ = {nq:,.0f}   VXN = {vxn:.1f}   (daily vol {pct_d*100:.2f}%)")
    print("-" * 52)
    print(f"  >>> PASTE into indicator  expMoveDaily = {r(em_d)}")
    print("-" * 52)
    print(f"  daily   1-sigma : +/- {r(em_d):>5} pts  ({pct_d*100:.2f}% of price)")
    print(f"  weekly  1-sigma : +/- {r(em_w):>5} pts")
    print(f"  monthly 1-sigma : +/- {r(em_m):>5} pts")
    print(f"\n  daily levels off {nq:,.0f}:")
    for nm, v in [("+1.0s", nq+em_d), ("+0.5s", nq+0.5*em_d),
                  ("-0.5s", nq-0.5*em_d), ("-1.0s", nq-em_d)]:
        print(f"    {nm}  {v:,.0f}")
    print("=" * 52)
    print("  If the NQ price above != your platform, pass --price <live NQ>.")
    print("  Cross-check vs Tradovate's NQ ATM straddle for the exact number.")


if __name__ == "__main__":
    main()
