"""
SD MarketReader — backtest the core signal on real NQ tick data.

Faithfully replicates the indicator's tradeable core on Databento NQ trades:
  - rebuild 4096-tick bars with DELTA (aggressor-signed volume)
  - levels: prior RTH session H/L, prior week H/L, session VWAP, session open
    (sigma/options levels need an options feed -- omitted)
  - delta flip:  minFlip = avgVol * 0.08 ;  dfBull/dfBear as in the indicator
  - SIGNAL (reversal at level):
        resistance swept + dfBear -> SHORT ;  support swept + dfBull -> LONG
  - stop beyond the swept extreme; targets 1.5R / 2.5R (indicator defaults)

Reports expectancy in R, win rate, profit factor -- overall and by level/dir.

    python sd_backtest.py --file /tmp/nq_trades_3wk.parquet --ticks 4096
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

TICK = 0.25
RTH_OPEN, RTH_CLOSE = 9*60+30, 16*60


def to_ny(idx):
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    return idx.tz_convert("America/New_York")


def tick_bars(df, n):
    """Aggregate every n trades into one bar: OHLC, volume, delta, end-time."""
    price = df["price"].to_numpy(float)
    size = df["size"].to_numpy(float)
    side = df["side"].to_numpy(str)
    signed = np.where(side == "B", size, np.where(side == "A", -size, 0.0))
    ts = df.index.values
    bar = np.arange(len(price)) // n
    g = pd.DataFrame({"bar": bar, "p": price, "s": size, "d": signed,
                      "ts": ts})
    agg = g.groupby("bar").agg(O=("p", "first"), H=("p", "max"), L=("p", "min"),
                               C=("p", "last"), V=("s", "sum"), D=("d", "sum"),
                               ts=("ts", "last"))
    agg.index = pd.DatetimeIndex(agg["ts"])
    return agg.drop(columns="ts")


def build_levels(b):
    """prior RTH session H/L, prior week H/L, session VWAP, session open."""
    ny = to_ny(b.index)
    mins = ny.hour*60 + ny.minute
    rth = (mins >= RTH_OPEN) & (mins < RTH_CLOSE)
    date = pd.Series(ny.date, index=b.index)
    week = pd.Series(ny.isocalendar().week.values, index=b.index)

    # session VWAP (RTH, reset daily)
    pv = (b["C"] * b["V"]).where(rth, 0)
    vol = b["V"].where(rth, 0)
    vwap = pv.groupby(date).cumsum() / vol.groupby(date).cumsum().replace(0, np.nan)

    # prior session H/L (RTH only)
    rth_h = b["H"].where(rth); rth_l = b["L"].where(rth)
    sess_h = rth_h.groupby(date).transform("max")
    sess_l = rth_l.groupby(date).transform("min")
    prior_sh = sess_h.groupby(date).transform("first")  # placeholder; map via shift below
    # map each date -> prior date's session H/L
    dh = rth_h.groupby(date).max(); dl = rth_l.groupby(date).min()
    pdh = dh.shift(1); pdl = dl.shift(1)
    prior_sess_h = date.map(pdh); prior_sess_l = date.map(pdl)
    # prior week H/L
    wh = b["H"].where(rth).groupby(week).max(); wl = b["L"].where(rth).groupby(week).min()
    prior_wk_h = week.map(wh.shift(1)); prior_wk_l = week.map(wl.shift(1))

    sess_open = b["O"].where(rth).groupby(date).transform("first")
    return dict(rth=np.asarray(rth), vwap=vwap.values,
                psh=prior_sess_h.values, psl=prior_sess_l.values,
                pwh=prior_wk_h.values, pwl=prior_wk_l.values, sopen=sess_open.values)


def backtest(b, lv, prox_pts, cost_ticks, tp_r):
    O, H, L, C, V, D = (b[k].to_numpy(float) for k in ["O", "H", "L", "C", "V", "D"])
    n = len(C)
    avgV = pd.Series(V).rolling(20).mean().to_numpy()
    res_levels = ["psh", "pwh", "vwap"]; sup_levels = ["psl", "pwl", "vwap"]
    trades = []
    i = 21
    while i < n - 1:
        if not lv["rth"][i] or np.isnan(avgV[i]) or avgV[i] <= 0:
            i += 1; continue
        minFlip = avgV[i] * 0.08
        dfBull = D[i-1] < -minFlip and D[i] > minFlip
        dfBear = D[i-1] > minFlip and D[i] < -minFlip
        sig = lvl = stop = None
        if dfBear:                                   # look for swept resistance
            for k in res_levels:
                Lv = lv[k][i]
                if not np.isnan(Lv) and H[i] >= Lv >= C[i] and abs(H[i]-Lv) <= prox_pts:
                    sig, lvl, stop = -1, k, H[i] + TICK; break
        if sig is None and dfBull:                   # look for swept support
            for k in sup_levels:
                Lv = lv[k][i]
                if not np.isnan(Lv) and L[i] <= Lv <= C[i] and abs(L[i]-Lv) <= prox_pts:
                    sig, lvl, stop = 1, k, L[i] - TICK; break
        if sig is None:
            i += 1; continue
        entry = C[i]; risk = abs(entry - stop)
        if risk < TICK:
            i += 1; continue
        tp = entry + sig * tp_r * risk
        outcome = None
        for j in range(i+1, min(i+200, n)):
            if sig == 1:
                if L[j] <= stop: outcome = -1.0; break
                if H[j] >= tp:   outcome = tp_r; break
            else:
                if H[j] >= stop: outcome = -1.0; break
                if L[j] <= tp:   outcome = tp_r; break
        if outcome is None:
            outcome = sig * (C[min(i+200, n-1)] - entry) / risk
        outcome -= (cost_ticks * TICK) / risk         # cost in R
        trades.append((lvl, sig, outcome))
        i = j + 1 if outcome is not None else i + 1
    return trades


def summ(tr, label):
    if not tr:
        print(f"  {label:<26} no trades"); return
    R = np.array([t[2] for t in tr])
    exp = R.mean(); win = float((R > 0).mean())
    pf_g = R[R > 0].sum(); pf_l = -R[R < 0].sum()
    pf = pf_g / pf_l if pf_l else 9.99
    t = exp/(R.std(ddof=1)/np.sqrt(len(R))) if len(R) > 1 and R.std() > 0 else 0
    print(f"  {label:<26} n={len(R):<4} expR {exp:+.2f}  win {win*100:.0f}%  PF {pf:.2f}  t={t:+.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--ticks", type=int, default=4096)
    ap.add_argument("--prox", type=float, default=5.0, help="proximity to level (points)")
    ap.add_argument("--cost", type=float, default=2.0, help="round-trip cost in ticks")
    ap.add_argument("--tpr", type=float, default=2.5, help="target in R (indicator TP2=2.5)")
    args = ap.parse_args()

    df = pd.read_parquet(args.file)
    print(f"loaded {len(df):,} trades; side values: {df['side'].value_counts().to_dict()}")
    b = tick_bars(df, args.ticks)
    print(f"built {len(b):,} {args.ticks}-tick bars  "
          f"{to_ny(b.index)[0]:%Y-%m-%d} -> {to_ny(b.index)[-1]:%Y-%m-%d}")
    lv = build_levels(b)
    tr = backtest(b, lv, args.prox, args.cost, args.tpr)

    print("=" * 64)
    print(f"  SD MARKETREADER CORE  (sweep+delta-flip reversal, TP {args.tpr}R, "
          f"cost {args.cost}tk)")
    print("=" * 64)
    summ(tr, "ALL signals")
    for k in ["psh", "psl", "pwh", "pwl", "vwap"]:
        summ([t for t in tr if t[0] == k], f"level={k}")
    summ([t for t in tr if t[1] == 1], "LONGs")
    summ([t for t in tr if t[1] == -1], "SHORTs")
    print("=" * 64)
    print("  Edge if expR>0 and PF>1 net of cost. (sigma/options levels omitted.)")


if __name__ == "__main__":
    main()
