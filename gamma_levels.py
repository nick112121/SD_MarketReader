"""
Options gamma / pin levels vs NQ price — testing the 'options flow drives it' thesis.

The SD indicator's core is sigma/options levels. This builds the observable ones
from the NQ options chain (Databento) and tests whether NQ futures actually
respect them intraday:

  CALL WALL  = strike with the most call open interest  (resistance / pin)
  PUT WALL   = strike with the most put open interest    (support / pin)
  MAX PAIN   = strike minimising total option-holder payout (pin magnet)

OI is end-of-day, so each day's walls are used as the NEXT session's levels
(known in advance -> no look-ahead). We test whether the next session's RTH
high sits near the call wall, low near the put wall, and close near max-pain,
versus randomly chosen strikes.

    python gamma_levels.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

RTH_OPEN, RTH_CLOSE = 9*60+30, 16*60


def to_ny(idx):
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    return idx.tz_convert("America/New_York")


def nq_sessions():
    df = pd.read_parquet("/tmp/nq_trades_3wk.parquet")
    ny = to_ny(df.index)
    mins = ny.hour*60 + ny.minute
    rth = df[(mins >= RTH_OPEN) & (mins < RTH_CLOSE)].copy()
    rth["date"] = to_ny(rth.index).date
    g = rth.groupby("date")["price"]
    return pd.DataFrame({"open": g.first(), "high": g.max(), "low": g.min(), "close": g.last()})


def walls_by_date():
    defn = pd.read_parquet("/tmp/nq_opt_def.parquet")
    oi = pd.read_parquet("/tmp/nq_opt_oi.parquet")
    val = "quantity" if oi["quantity"].abs().sum() > oi["price"].abs().sum() else "price"
    oi = oi[["instrument_id", "ts_event", val]].rename(columns={val: "oi"})
    oi["date"] = to_ny(pd.DatetimeIndex(oi["ts_event"])).date
    m = oi.merge(defn, on="instrument_id", how="inner")
    m = m[m["oi"] > 0]
    m["exp"] = to_ny(pd.DatetimeIndex(m["expiration"])).date
    out = {}
    for d, day in m.groupby("date"):
        future = day[day["exp"] >= d]
        if future.empty:
            continue
        front = future[future["exp"] == future["exp"].min()]      # nearest expiry
        calls = front[front["instrument_class"] == "C"].groupby("strike_price")["oi"].sum()
        puts = front[front["instrument_class"] == "P"].groupby("strike_price")["oi"].sum()
        if calls.empty or puts.empty:
            continue
        call_wall = float(calls.idxmax()); put_wall = float(puts.idxmax())
        # max pain: strike minimising sum of ITM payout to holders
        strikes = np.union1d(calls.index.values, puts.index.values)
        pain = []
        for K in strikes:
            cp = float((np.maximum(K - calls.index.values, 0) * calls.values).sum())
            pp = float((np.maximum(puts.index.values - K, 0) * puts.values).sum())
            pain.append(cp + pp)
        max_pain = float(strikes[int(np.argmin(pain))])
        out[d] = dict(call_wall=call_wall, put_wall=put_wall, max_pain=max_pain,
                      strikes=strikes)
    return out


def main():
    sess = nq_sessions()
    walls = walls_by_date()
    dates = sorted(walls)
    rows = []
    for i in range(len(dates) - 1):
        d, nd = dates[i], dates[i+1]
        if nd not in sess.index:
            continue
        w = walls[d]; s = sess.loc[nd]
        strikes = w["strikes"]
        def rnd():  # random strike near the day's range for a fair baseline
            cand = strikes[(strikes >= s["low"]-200) & (strikes <= s["high"]+200)]
            return float(np.random.choice(cand)) if len(cand) else float(np.random.choice(strikes))
        rows.append(dict(
            d_high_callwall=abs(s["high"] - w["call_wall"]),
            d_low_putwall=abs(s["low"] - w["put_wall"]),
            d_close_maxpain=abs(s["close"] - w["max_pain"]),
            rnd_high=abs(s["high"] - rnd()),
            rnd_close=abs(s["close"] - rnd()),
        ))
    r = pd.DataFrame(rows)
    print("="*66)
    print(f"  OPTIONS WALLS vs NQ  ({len(r)} sessions, next-day test)")
    print("="*66)
    print(f"  median |RTH high - call wall| : {r['d_high_callwall'].median():6.1f} pts")
    print(f"  median |RTH low  - put wall|  : {r['d_low_putwall'].median():6.1f} pts")
    print(f"  median |close - max pain|     : {r['d_close_maxpain'].median():6.1f} pts")
    print(f"  median |high/close - RANDOM strike| : {r['rnd_high'].median():.1f} / {r['rnd_close'].median():.1f} pts (baseline)")
    print("  --")
    for col, base, lbl in [("d_high_callwall", "rnd_high", "high pinned to call wall"),
                           ("d_close_maxpain", "rnd_close", "close pinned to max-pain")]:
        better = float((r[col] < r[base]).mean())
        print(f"  {lbl:<28}: closer than random {better*100:.0f}% of days")
    print("="*66)
    print("  Edge if extremes/close sit MUCH closer to walls than to random strikes.")


if __name__ == "__main__":
    main()
