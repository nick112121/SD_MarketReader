"""
Quant factors — the edge that lives in BREADTH, not single-asset timing.

Order-flow work showed single-instrument timing is contemporaneous / latency-
bound. The durable retail-tradeable quant edges are CROSS-SECTIONAL: rank a
universe and hold the strong. This tests momentum-based sector rotation, the
most robust of them (Jegadeesh-Titman; Faber; Antonacci dual momentum):

  EW BUY&HOLD     equal-weight all sectors (baseline)
  REL-MOM         each month hold the top-K sectors by trailing 6-month return
  DUAL-MOM        REL-MOM, but any pick with negative trailing return -> cash (SHY)
                  (absolute-momentum filter = step aside in bear markets)

Monthly rebalance, daily data via yfinance, full-period and out-of-sample.
Note: uses current sector ETFs (no dead tickers), so survivorship is minimal
for sectors but results are still gross of slippage.

    python quant_factors.py --topk 3 --lookback 6
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
import yfinance as yf

SECTORS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB"]
CASH = "SHY"          # short treasuries = cash proxy
BENCH = "SPY"
ANN = 12


def stats(monthly):
    eq = (1 + monthly).cumprod()
    n = len(monthly)
    cagr = eq.iloc[-1] ** (ANN / n) - 1 if n else 0
    sh = monthly.mean() / monthly.std() * np.sqrt(ANN) if monthly.std() > 0 else 0
    dd = float((eq / eq.cummax() - 1).min())
    return cagr, sh, dd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topk", type=int, default=3)
    ap.add_argument("--lookback", type=int, default=6, help="momentum lookback (months)")
    ap.add_argument("--cost", type=float, default=5.0, help="bps per rebalance turn")
    args = ap.parse_args()

    tickers = SECTORS + [CASH, BENCH]
    px = yf.download(tickers, period="max", interval="1d", progress=False, auto_adjust=True)["Close"]
    m = px.resample("ME").last().dropna(how="all")
    m = m.dropna()                                   # common window (starts when all exist)
    rets = m.pct_change()
    L = args.lookback
    mom = m / m.shift(L) - 1                          # trailing L-month return

    uni = SECTORS
    dates = m.index
    rows_rel, rows_dual = [], []
    prev_rel, prev_dual = set(), set()
    cost = args.cost / 1e4
    for i in range(L, len(dates) - 1):
        d, nd = dates[i], dates[i + 1]
        rank = mom.loc[d, uni].dropna().sort_values(ascending=False)
        picks = list(rank.index[:args.topk])
        # relative momentum
        r_rel = rets.loc[nd, picks].mean()
        turn = len(set(picks) ^ prev_rel)
        rows_rel.append(r_rel - turn / args.topk * cost); prev_rel = set(picks)
        # dual momentum: negative-momentum picks -> cash
        dual_picks = [p if rank[p] > 0 else CASH for p in picks]
        r_dual = rets.loc[nd, dual_picks].mean()
        turn2 = len(set(dual_picks) ^ prev_dual)
        rows_dual.append(r_dual - turn2 / args.topk * cost); prev_dual = set(dual_picks)

    idx = dates[L + 1:len(dates)]
    rel = pd.Series(rows_rel, index=idx)
    dual = pd.Series(rows_dual, index=idx)
    ew = rets[uni].mean(axis=1).loc[idx]
    spy = rets[BENCH].loc[idx]
    cut = int(len(idx) * 0.6)

    print("=" * 80)
    print(f"  QUANT FACTOR ROTATION  sectors, top{args.topk} by {L}m momentum, monthly")
    print(f"  {idx[0]:%Y-%m} -> {idx[-1]:%Y-%m}  ({len(idx)} months)   cost {args.cost}bps/turn")
    print("=" * 80)
    print(f"  {'strategy':<16}{'CAGR':>8}{'Sharpe':>8}{'MaxDD':>8} | {'OOS CAGR':>9}{'OOS Shrp':>9}{'OOS DD':>8}")
    for name, s in [("SPY buy&hold", spy), ("EW sectors", ew),
                    ("REL-MOM", rel), ("DUAL-MOM", dual)]:
        c, sh, dd = stats(s); co, sho, ddo = stats(s.iloc[cut:])
        print(f"  {name:<16}{c*100:>7.1f}%{sh:>8.2f}{dd*100:>7.0f}% | {co*100:>8.1f}%{sho:>9.2f}{ddo*100:>7.0f}%")

    # current allocation
    last = dates[-1]
    rank = mom.loc[last, uni].dropna().sort_values(ascending=False)
    picks = list(rank.index[:args.topk])
    dual_now = [p if rank[p] > 0 else CASH for p in picks]
    print("\n  TODAY's allocation:")
    print(f"    REL-MOM : {picks}  (top {L}m momentum: " +
          ", ".join(f"{p} {rank[p]*100:+.0f}%" for p in picks) + ")")
    print(f"    DUAL-MOM: {dual_now}  (negative-momentum picks moved to cash)")
    print("=" * 80)


if __name__ == "__main__":
    main()
