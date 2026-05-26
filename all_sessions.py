"""
All-sessions ORB continuation — Asia / London / NY, with the options regime filter.

Generalises the fused setup to every futures session a day-trader works, using
the full 24h NQ 1-minute data. A "trading day" (TD) starts at 18:00 ET (Globex
reopen); minute-in-TD = minutes since 18:00, so sessions are monotonic:

  ASIA   : 18:00-02:00 ET  (OR 18:00-18:30)
  LONDON : 03:00-08:00 ET  (OR 03:00-03:30)
  NY     : 09:30-16:00 ET  (OR 09:30-10:00)

Each session: break of its own opening range -> trade WITH the move, stop at the
opposite side of the OR, target TP_R, flat at session end. Optional regime gate:
trade only when that TD's expected move (prior settlement) is in the top tercile.

    python all_sessions.py --tpr 2 --cost_ticks 1.5
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

TICK = 0.25
SESSIONS = {            # name: (OR_start, OR_end, session_end)  in minutes-since-18:00 ET
    "ASIA":   (0,   30,  480),
    "LONDON": (540, 570, 840),
    "NY":     (930, 960, 1320),
}


def to_ny(idx):
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    return idx.tz_convert("America/New_York")


def load_1m_all():
    b = pd.read_parquet("/tmp/nq_1m_3y.parquet")
    b = b.set_index(to_ny(b.index)).sort_index()
    h, mn = b.index.hour, b.index.minute
    b["mit"] = ((h - 18) % 24) * 60 + mn
    b["td"] = (b.index + pd.Timedelta(hours=6)).date
    return b[["open", "high", "low", "close", "mit", "td"]]


def daily_em():
    import os
    sfx = "_2y" if os.path.exists("/tmp/nq_settle_2y.parquet") else "_6m"
    defn = pd.read_parquet(f"/tmp/nq_def{sfx}.parquet")
    st = pd.read_parquet(f"/tmp/nq_settle{sfx}.parquet")
    st["date"] = to_ny(pd.DatetimeIndex(st["ts_event"])).date
    m = st.merge(defn, on="instrument_id", how="inner")
    m["exp"] = to_ny(pd.DatetimeIndex(m["expiration"])).date
    b = load_1m_all()
    close = b[b["mit"].between(930, 1320)].groupby("td")["close"].last()
    em = {}
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
        em[pd.Timestamp(d)] = (float(cc.iloc[0]) + float(pp.iloc[0])) / np.sqrt(max((E-d).days, 1))
    s = pd.Series(em).sort_index()
    return s.rolling(60, min_periods=20).apply(lambda x: (x.iloc[-1] > x).mean(), raw=False)


def backtest_session(b, win, tpr, cost_ticks, em_rank, mode="cont"):
    o0, o1, send = win
    rows = []
    for td, g in b.groupby("td"):
        s = g[(g["mit"] >= o0) & (g["mit"] < send)].sort_values("mit")
        o_r = s[s["mit"] < o1]; aft = s[s["mit"] >= o1]
        if len(o_r) < 10 or len(aft) < 10:
            continue
        orh, orl = float(o_r["high"].max()), float(o_r["low"].min())
        width = orh - orl
        if width < TICK:
            continue
        H, L, C = aft["high"].to_numpy(float), aft["low"].to_numpy(float), aft["close"].to_numpy(float)
        brk = entry = ei = None
        for i in range(len(C)):
            if C[i] > orh: brk, entry, ei = 1, C[i], i; break
            if C[i] < orl: brk, entry, ei = -1, C[i], i; break
        if entry is None:
            continue
        if mode == "cont":                       # trade WITH the break
            direction = brk
            stop = orl if brk == 1 else orh
            tp = entry + direction * tpr * abs(entry - stop)
        else:                                    # FADE the break (range reversion)
            direction = -brk
            stop = orh + 0.5*width if brk == 1 else orl - 0.5*width
            tp = orl if brk == 1 else orh        # target = opposite side of the range
        risk = abs(entry - stop)
        if risk < TICK:
            continue
        out = None
        for j in range(ei+1, len(C)):
            if direction == 1:
                if L[j] <= stop: out = -(entry-stop)/risk if stop < entry else -1.0; break
                if H[j] >= tp: out = (tp-entry)/risk; break
            else:
                if H[j] >= stop: out = -(stop-entry)/risk if stop > entry else -1.0; break
                if L[j] <= tp: out = (entry-tp)/risk; break
        if out is None:
            out = direction * (C[-1] - entry) / risk
        out -= (cost_ticks * TICK) / risk
        emr = em_rank.asof(pd.Timestamp(td) - pd.Timedelta(days=1)) if len(em_rank) else np.nan
        rows.append((td, out, emr))
    return pd.DataFrame(rows, columns=["td", "R", "emrank"])


def stats(R):
    R = np.asarray(R, float)
    if len(R) < 5:
        return f"n={len(R)} (too few)"
    exp = R.mean(); win = (R > 0).mean()
    g = R[R > 0].sum(); l = -R[R < 0].sum(); pf = g/l if l else 9.99
    t = exp/(R.std(ddof=1)/np.sqrt(len(R))) if R.std() > 0 else 0
    return f"n={len(R):<4} expR {exp:+.2f}  win {win*100:.0f}%  PF {pf:.2f}  t={t:+.2f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tpr", type=float, default=2.0)
    ap.add_argument("--cost_ticks", type=float, default=1.5)
    args = ap.parse_args()
    b = load_1m_all()
    em_rank = daily_em()
    print(f"TDs: {b['td'].nunique()}   EM dates: {em_rank.notna().sum()}")
    print("="*74)
    print(f"  ALL-SESSIONS ORB CONTINUATION  (TP {args.tpr}R, cost {args.cost_ticks}tk)")
    print("="*74)
    for name, win in SESSIONS.items():
        print(f"\n  {name}")
        for mode in ("cont", "fade"):
            tr = backtest_session(b, win, args.tpr, args.cost_ticks, em_rank, mode)
            cut = tr["td"].quantile(0.6) if len(tr) else None
            oos = tr[tr["td"] > cut] if cut is not None else tr
            hi = tr.dropna(subset=["emrank"]); hi = hi[hi["emrank"] >= 0.66]
            print(f"    [{mode}] all: {stats(tr['R'])}")
            print(f"    [{mode}] OOS: {stats(oos['R'])}   high-EM: {stats(hi['R'])}")
    print("="*74)


if __name__ == "__main__":
    main()
