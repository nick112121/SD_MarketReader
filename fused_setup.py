"""
Fused setup — options-flow regime + order-flow continuation + structure risk.

Built from the only drivers that survived honest testing, combined into ONE
mechanical day-trading setup and tested with a train/test split on ~years of
1-minute NQ data:

  REGIME (options flow): daily expected move (ATM straddle settle, front expiry,
      1-day scaled). Trade only when today's EM is in the TOP tercile of the
      trailing 60 days -> a big-range / trend day is forecast.
  DIRECTION (continuation): opening range = 09:30-10:00 high/low. After 10:00,
      enter on the first 1m close beyond the OR, IN the break direction (never
      fade -- fading loses t=-6).
  RISK (structure): stop = opposite side of the OR; target = TP_R * risk; flat
      at the close.

Compares ALL days vs HIGH-EM vs LOW-EM, in-sample and out-of-sample, net of cost.

    python fused_setup.py --tpr 2 --cost_ticks 1.5
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

TICK = 0.25
RTH_OPEN, OR_END, RTH_CLOSE = 9*60+30, 10*60, 16*60


def to_ny(idx):
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    return idx.tz_convert("America/New_York")


def load_1m():
    b = pd.read_parquet("/tmp/nq_1m_3y.parquet")
    b = b.set_index(to_ny(b.index))
    b["min"] = b.index.hour*60 + b.index.minute
    b["date"] = b.index.date
    return b[(b["min"] >= RTH_OPEN) & (b["min"] < RTH_CLOSE)]


def daily_em():
    defn = pd.read_parquet("/tmp/nq_def_6m.parquet")
    st = pd.read_parquet("/tmp/nq_settle_6m.parquet")
    st["date"] = to_ny(pd.DatetimeIndex(st["ts_event"])).date
    m = st.merge(defn, on="instrument_id", how="inner")
    m["exp"] = to_ny(pd.DatetimeIndex(m["expiration"])).date
    em = {}
    # reference close per date from 1m
    b = load_1m()
    close = b.groupby("date")["close"].last()
    for d, day in m.groupby("date"):
        px = close.get(d)
        if px is None or np.isnan(px):
            continue
        fut = day[day["exp"] > d]
        if fut.empty:
            continue
        E = fut["exp"].min(); front = fut[fut["exp"] == E]
        ks = front["strike_price"].dropna().unique()
        if len(ks) < 2:
            continue
        atm = ks[np.argmin(np.abs(ks - px))]
        cc = front[(front.instrument_class == "C") & (front.strike_price == atm)]["settle"]
        pp = front[(front.instrument_class == "P") & (front.strike_price == atm)]["settle"]
        if cc.empty or pp.empty or np.isnan(cc.iloc[0]) or np.isnan(pp.iloc[0]):
            continue
        dte = max((E - d).days, 1)
        em[d] = (float(cc.iloc[0]) + float(pp.iloc[0])) / np.sqrt(dte)
    return pd.Series(em).sort_index()


def backtest(b, em, tpr, cost_ticks, fade=False):
    trades = []
    em_rank = em.rolling(60, min_periods=20).apply(lambda x: (x.iloc[-1] > x).mean(), raw=False)
    for d, g in b.groupby("date"):
        g = g.sort_index()
        o_r = g[g["min"] < OR_END]
        aft = g[g["min"] >= OR_END]
        if len(o_r) < 10 or len(aft) < 10:
            continue
        orh, orl = float(o_r["high"].max()), float(o_r["low"].min())
        H, L, C = aft["high"].to_numpy(float), aft["low"].to_numpy(float), aft["close"].to_numpy(float)
        entry = direction = ei = None
        for i in range(len(C)):
            if C[i] > orh:
                direction, entry, ei = 1, C[i], i; break
            if C[i] < orl:
                direction, entry, ei = -1, C[i], i; break
        if entry is None:
            continue
        if fade:
            direction = -direction
        stop = orl if direction == 1 else orh
        risk = abs(entry - stop)
        if risk < TICK:
            continue
        tp = entry + direction * tpr * risk
        out = None
        for j in range(ei+1, len(C)):
            if direction == 1:
                if L[j] <= stop: out = -1.0; break
                if H[j] >= tp: out = tpr; break
            else:
                if H[j] >= stop: out = -1.0; break
                if L[j] <= tp: out = tpr; break
        if out is None:
            out = direction * (C[-1] - entry) / risk
        out -= (cost_ticks * TICK) / risk
        emr = em_rank.get(d, np.nan)
        trades.append((d, out, emr))
    return pd.DataFrame(trades, columns=["date", "R", "emrank"])


def stats(R):
    R = np.asarray(R)
    if len(R) < 5:
        return f"n={len(R)} (too few)"
    exp = R.mean(); win = (R > 0).mean()
    pf_g = R[R > 0].sum(); pf_l = -R[R < 0].sum()
    pf = pf_g/pf_l if pf_l else 9.99
    t = exp/(R.std(ddof=1)/np.sqrt(len(R))) if R.std() > 0 else 0
    return f"n={len(R):<4} expR {exp:+.2f}  win {win*100:.0f}%  PF {pf:.2f}  t={t:+.2f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tpr", type=float, default=2.0)
    ap.add_argument("--cost_ticks", type=float, default=1.5)
    args = ap.parse_args()
    b = load_1m()
    em = daily_em()
    print(f"1m sessions: {b['date'].nunique()}   EM dates: {len(em)}")
    tr = backtest(b, em, args.tpr, args.cost_ticks)
    cut = tr["date"].quantile(0.6) if len(tr) else None

    print("="*72)
    print(f"  FUSED SETUP  (ORB continuation, TP {args.tpr}R, cost {args.cost_ticks}tk)")
    print("="*72)
    print(f"  ALL days continuation : {stats(tr['R'])}")
    has_em = tr.dropna(subset=["emrank"])
    hi = has_em[has_em["emrank"] >= 0.66]; lo = has_em[has_em["emrank"] <= 0.33]
    print(f"  HIGH expected-move days: {stats(hi['R'])}")
    print(f"  LOW  expected-move days: {stats(lo['R'])}")
    if cut is not None:
        tr_te = tr[tr["date"] > cut]; hi_te = hi[hi["date"] > cut]
        print("  --- OUT-OF-SAMPLE (newest 40%) ---")
        print(f"  ALL days  OOS          : {stats(tr_te['R'])}")
        print(f"  HIGH-EM   OOS          : {stats(hi_te['R'])}")
    print("="*72)


if __name__ == "__main__":
    main()
