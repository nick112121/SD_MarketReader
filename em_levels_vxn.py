"""
Expected-move S/R levels for NQ — robust, free, daily-runnable (VXN-based).

VXN is the Nasdaq-100 implied-volatility index (annualised %). The expected move
over N trading days is:   EM = price * (VXN/100) * sqrt(N/252).

We project support/resistance from the prior close at three horizons:
  DAILY   N=1     WEEKLY  N=5     MONTHLY N=21
each at +/-0.5 and +/-1.0 sigma. These are PROBABILITY ZONES: ~68% of closes
land inside the 1-sigma band; the band edges mark where a move is "extended"
(target / fade-watch), and the weekly/monthly bands are the bigger shelves.

Validated on history: does the daily +/-1sigma actually contain the next close?

    python em_levels_vxn.py --ticker "NQ=F"
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
import yfinance as yf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="NQ=F")
    args = ap.parse_args()
    px = yf.download([args.ticker, "^VXN"], period="max", interval="1d",
                     progress=False, auto_adjust=True)["Close"].dropna()
    px.columns = [str(c) for c in px.columns]
    p = px[args.ticker]; vxn = px["^VXN"]
    ref = p.shift(1)                                   # prior close (no look-ahead)
    sig1d = ref * (vxn.shift(1) / 100) * np.sqrt(1/252)   # use prior-day VXN
    d = pd.DataFrame({"close": p, "ref": ref, "em_d": sig1d}).dropna()

    # validation: next close within prior-close +/- daily 1sigma
    c1 = (np.abs(d["close"] - d["ref"]) <= d["em_d"]).mean()
    c05 = (np.abs(d["close"] - d["ref"]) <= 0.5*d["em_d"]).mean()
    c2 = (np.abs(d["close"] - d["ref"]) <= 2*d["em_d"]).mean()
    print("="*60)
    print(f"  NQ EXPECTED-MOVE LEVELS (VXN)   validated on {len(d)} days")
    print(f"  {d.index[0]:%Y-%m-%d} -> {d.index[-1]:%Y-%m-%d}")
    print("="*60)
    print(f"  close within daily +/-0.5 sigma : {c05*100:.0f}%   (theory ~38%)")
    print(f"  close within daily +/-1.0 sigma : {c1*100:.0f}%   (theory ~68%)")
    print(f"  close within daily +/-2.0 sigma : {c2*100:.0f}%   (theory ~95%)")
    print(f"  median daily EM: {d['em_d'].median():.0f} NQ pts")

    # today's levels
    last_ref = float(p.iloc[-1]); last_vxn = float(vxn.iloc[-1])
    emd = last_ref * (last_vxn/100) * np.sqrt(1/252)
    emw = last_ref * (last_vxn/100) * np.sqrt(5/252)
    emm = last_ref * (last_vxn/100) * np.sqrt(21/252)
    print(f"\n  TODAY  (anchor = last close {last_ref:.0f}, VXN {last_vxn:.1f})")
    print(f"  daily EM +/-{emd:.0f}   weekly +/-{emw:.0f}   monthly +/-{emm:.0f} pts")
    print("  ---- the 4 KEY intraday levels (daily) ----")
    for nm, v in [("R2  +1.0sigma", last_ref+emd), ("R1  +0.5sigma", last_ref+0.5*emd),
                  ("S1  -0.5sigma", last_ref-0.5*emd), ("S2  -1.0sigma", last_ref-emd)]:
        print(f"    {nm:<14} {v:>10.1f}")
    print("  ---- bigger shelves ----")
    for nm, v in [("Wk +1s", last_ref+emw), ("Wk -1s", last_ref-emw),
                  ("Mo +1s", last_ref+emm), ("Mo -1s", last_ref-emm)]:
        print(f"    {nm:<14} {v:>10.1f}")
    print("="*60)
    print("  Trade them as zones: in NY, a push to +/-1sigma daily on a high-VXN")
    print("  day is an 'extended' target; mid-band (+/-0.5) is the day's fair range.")


if __name__ == "__main__":
    main()
