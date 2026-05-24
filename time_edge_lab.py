"""
Time-Edge Lab — is there a real time-of-day structure to when moves fire?

Standalone study (NOT wired into the dashboard). The premise being tested: if
markets are algo-driven, directional bursts (bull/bear) should fire in
non-random time windows. Goldbach Time (prime minutes) failed that test
(goldbach_test.py). This lab tests the credible, non-ICT-numerology cousins:

  A. TIME-OF-DAY DRIFT MAP  — average return / win-rate by 30-min bucket across
     all sessions. Shows WHEN directional drift and volatility concentrate.

  B. DR / IDR  (TheMas7er)  — the 09:30-10:30 "Defining Range". His claim: once
     price closes beyond the DR after 10:30, the OPPOSITE extreme becomes the
     session high/low ~80% of the time, and price expands in the break direction.
     TEST: confirmation rate, "opposite-extreme holds" rate vs 80%, and the
     tradeable follow-through from confirmation to the close.

  C. OPENING RANGE BREAKOUT  (Zarattini et al.) — the first 5-min bar sets a
     range; trade the first close beyond it to the close. Peer-reviewed to have
     an edge. TEST: expectancy vs a buy-the-open baseline.

  D. QUARTERLY THEORY (lite, Daye) — 90-min cycles split into four 22.5-min
     sub-quarters. AMD says the cycle's true extreme forms early (manipulation
     in Q1/Q2). TEST: is the sub-quarter where the high/low forms non-uniform?

Uses 5-minute bars over ~60 days (more sessions than 1m/30d), RTH only.

Usage:
    python time_edge_lab.py --ticker QQQ
    python time_edge_lab.py --ticker SPY --cost 0.5
    python time_edge_lab.py --csv data.csv         # offline (datetime,O,H,L,C)
"""

from __future__ import annotations

import argparse
import math
from datetime import timezone

import numpy as np
import pandas as pd

RTH_OPEN, RTH_CLOSE = 9 * 60 + 30, 16 * 60   # 09:30, 16:00 (minutes from midnight, ET)


# ── stats helpers (no scipy) ──────────────────────────────────────────────
def norm_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def mean_t(x: np.ndarray) -> tuple[float, float, float]:
    n = len(x)
    if n < 2:
        return (float(np.mean(x)) if n else 0.0), 0.0, 1.0
    m = float(np.mean(x))
    se = float(np.std(x, ddof=1)) / math.sqrt(n)
    t = m / se if se > 0 else 0.0
    return m, t, 2.0 * norm_sf(abs(t))


def prop_z(hits: int, n: int, p0: float) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    sd = math.sqrt(p0 * (1 - p0) / n)
    z = (hits / n - p0) / sd if sd > 0 else 0.0
    return z, norm_sf(z)


# ── data ─────────────────────────────────────────────────────────────────
def to_ny(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    return idx.tz_convert("America/New_York")


def load(ticker: str, period: str, csv: str | None) -> pd.DataFrame:
    if csv:
        d = pd.read_csv(csv)
        tcol = next(c for c in d.columns if c.lower() in ("datetime", "date", "timestamp", "time"))
        d[tcol] = pd.to_datetime(d[tcol], utc=True)
        d = d.set_index(tcol)
        d.columns = [c.capitalize() for c in d.columns]
    else:
        import yfinance as yf
        d = yf.download(ticker, period=period, interval="5m", progress=False, auto_adjust=True)
        if isinstance(d.columns, pd.MultiIndex):
            d.columns = d.columns.get_level_values(0)
    d = d.dropna()
    d = d.set_index(to_ny(d.index))
    mins = d.index.hour * 60 + d.index.minute
    return d[(mins >= RTH_OPEN) & (mins < RTH_CLOSE)]


def sessions(df: pd.DataFrame) -> list[tuple]:
    out = []
    for day, g in df.groupby(df.index.date):
        if len(g) < 60:
            continue
        first = g.index[0].hour * 60 + g.index[0].minute
        if first > RTH_OPEN + 5:        # must start ~09:30
            continue
        out.append((day, g))
    return out


def mins_of(g: pd.DataFrame) -> np.ndarray:
    return g.index.hour * 60 + g.index.minute


# ── A. time-of-day drift map ───────────────────────────────────────────────
def drift_map(sess: list) -> list:
    buckets: dict[int, list] = {}
    for _, g in sess:
        c = g["Close"].to_numpy(float)
        ret = np.diff(c) / c[:-1]
        m = mins_of(g)[1:]
        for mi, r in zip(m, ret):
            buckets.setdefault((mi - RTH_OPEN) // 30, []).append(r)
    rows = []
    for b in sorted(buckets):
        arr = np.array(buckets[b]) * 1e4
        mean, t, _ = mean_t(arr)
        lbl = f"{(RTH_OPEN + b*30)//60:02d}:{(RTH_OPEN + b*30)%60:02d}"
        rows.append((lbl, len(arr), mean, float((arr > 0).mean()), float(arr.std()), t))
    return rows


# ── B. DR / IDR ────────────────────────────────────────────────────────────
def dr_idr(sess: list) -> dict:
    up = {"n": 0, "hold": 0, "follow": []}
    dn = {"n": 0, "hold": 0, "follow": []}
    no_conf = 0
    for _, g in sess:
        m = mins_of(g)
        dr = g[(m >= RTH_OPEN) & (m < RTH_OPEN + 60)]          # 09:30-10:30
        after = g[m >= RTH_OPEN + 60]
        if len(dr) < 10 or len(after) < 5:
            continue
        drh, drl = float(dr["High"].max()), float(dr["Low"].min())
        ac = after["Close"].to_numpy(float)
        conf_i = direction = None
        for i, px in enumerate(ac):
            if px > drh:
                direction, conf_i = "up", i; break
            if px < drl:
                direction, conf_i = "dn", i; break
        if direction is None:
            no_conf += 1
            continue
        rest = after.iloc[conf_i:]
        conf_px = ac[conf_i]
        close_px = float(g["Close"].iloc[-1])
        if direction == "up":
            up["n"] += 1
            up["hold"] += float(rest["Low"].min()) >= drl       # DR low stays the low
            up["follow"].append((close_px - conf_px) / conf_px * 1e4)
        else:
            dn["n"] += 1
            dn["hold"] += float(rest["High"].max()) <= drh
            dn["follow"].append((conf_px - close_px) / conf_px * 1e4)
    return {"up": up, "dn": dn, "no_conf": no_conf, "total": len(sess)}


# ── C. opening range breakout ──────────────────────────────────────────────
def orb(sess: list, n_or: int, cost_bps: float) -> dict:
    rets, longs, shorts, bh = [], [], [], []
    for _, g in sess:
        o_h = float(g["High"].iloc[:n_or].max())
        o_l = float(g["Low"].iloc[:n_or].min())
        after = g.iloc[n_or:]
        entry = direction = None
        for px in after["Close"].to_numpy(float):
            if px > o_h:
                direction, entry = "long", px; break
            if px < o_l:
                direction, entry = "short", px; break
        bh.append((float(g["Close"].iloc[-1]) - float(g["Open"].iloc[0])) / float(g["Open"].iloc[0]) * 1e4)
        if entry is None:
            continue
        exit_px = float(g["Close"].iloc[-1])
        r = (exit_px - entry) / entry if direction == "long" else (entry - exit_px) / entry
        r = r * 1e4 - 2 * cost_bps
        rets.append(r)
        (longs if direction == "long" else shorts).append(r)
    return {"rets": np.array(rets), "longs": np.array(longs),
            "shorts": np.array(shorts), "bh": np.array(bh)}


# ── D. quarterly theory (lite) ──────────────────────────────────────────────
def quarterly(sess: list) -> dict:
    hi = [0, 0, 0, 0]
    lo = [0, 0, 0, 0]
    tot = 0
    for _, g in sess:
        m = mins_of(g)
        for start in (RTH_OPEN, RTH_OPEN + 90, RTH_OPEN + 180, RTH_OPEN + 270):
            cyc = g[(m >= start) & (m < start + 90)]
            if len(cyc) < 12:
                continue
            cm = (cyc.index.hour * 60 + cyc.index.minute) - start          # 0..89
            hq = min(int(cm[int(cyc["High"].to_numpy().argmax())] // 22.5), 3)
            lq = min(int(cm[int(cyc["Low"].to_numpy().argmin())] // 22.5), 3)
            hi[hq] += 1
            lo[lq] += 1
            tot += 1
    exp = tot / 4.0
    chi = sum((hi[i] - exp) ** 2 / exp for i in range(4)) + \
          sum((lo[i] - exp) ** 2 / exp for i in range(4)) if exp else 0.0
    return {"hi": hi, "lo": lo, "tot": tot, "chi": chi}


# ── E. improved method: DR retrace-continuation ────────────────────────────
def dr_retrace(sess: list, cost_bps: float) -> dict:
    """Improvement built on the validated pieces:
      trigger  = first 5m close beyond the 09:30-10:30 DR after 10:30
      entry    = LIMIT back at the broken DR edge (retrace, not chase)
      stop     = the OPPOSITE DR extreme (held ~85%+ in test B)
      target   = session close
    Skips sessions that confirm but never retrace to the edge."""
    rets, no_retrace = [], 0
    for _, g in sess:
        m = mins_of(g)
        dr = g[(m >= RTH_OPEN) & (m < RTH_OPEN + 60)]
        after = g[m >= RTH_OPEN + 60]
        if len(dr) < 10 or len(after) < 5:
            continue
        drh, drl = float(dr["High"].max()), float(dr["Low"].min())
        if drh <= drl:
            continue
        ac = after["Close"].to_numpy(float)
        ah = after["High"].to_numpy(float)
        al = after["Low"].to_numpy(float)
        direction = conf_i = None
        for i, px in enumerate(ac):
            if px > drh:
                direction, conf_i = "long", i; break
            if px < drl:
                direction, conf_i = "short", i; break
        if direction is None:
            continue
        edge = drh if direction == "long" else drl
        stop = drl if direction == "long" else drh
        entry = ej = None
        for j in range(conf_i + 1, len(after)):
            if direction == "long" and al[j] <= edge:
                entry, ej = edge, j; break
            if direction == "short" and ah[j] >= edge:
                entry, ej = edge, j; break
        if entry is None:
            no_retrace += 1
            continue
        exit_px = float(g["Close"].iloc[-1])
        for j in range(ej, len(after)):
            if direction == "long" and al[j] <= stop:
                exit_px = stop; break
            if direction == "short" and ah[j] >= stop:
                exit_px = stop; break
        r = (exit_px - entry) / entry if direction == "long" else (entry - exit_px) / entry
        rets.append(r * 1e4 - 2 * cost_bps)
    return {"rets": np.array(rets), "no_retrace": no_retrace}


# ── report ──────────────────────────────────────────────────────────────
def run(df: pd.DataFrame, cost_bps: float, or_bars: int, label: str):
    sess = sessions(df)
    print("=" * 72)
    print(f"  TIME-EDGE LAB   [{label}]")
    print("=" * 72)
    print(f"  bars: {len(df):,}   sessions: {len(sess)}   "
          f"range: {df.index[0]:%Y-%m-%d} -> {df.index[-1]:%Y-%m-%d}")
    if len(sess) < 15:
        print("  [!] too few sessions for a meaningful read.")
        return

    # A
    print("\n" + "-" * 72)
    print("  A. TIME-OF-DAY DRIFT MAP  (mean per-bar return by 30-min bucket)")
    print("-" * 72)
    print(f"  {'window':>7} | {'n':>6} {'mean bps':>9} {'win%':>6} {'vol':>6} {'t':>6}")
    cum = 0.0
    for lbl, n, mean, win, vol, t in drift_map(sess):
        cum += mean
        flag = "  <<" if abs(t) > 2 else ""
        print(f"  {lbl:>7} | {n:>6} {mean:>+9.3f} {win*100:>5.1f}% {vol:>6.1f} {t:>+6.2f}{flag}")
    print(f"  (cumulative intraday drift: {cum:+.1f} bps; '<<' = |t|>2, locally significant)")

    # B
    r = dr_idr(sess)
    print("-" * 72)
    print("  B. DR / IDR  (09:30-10:30 Defining Range; TheMas7er '80%' claim)")
    print("-" * 72)
    conf = r["up"]["n"] + r["dn"]["n"]
    print(f"  sessions confirmed (close beyond DR after 10:30): {conf}/{r['total']} "
          f"({conf/r['total']*100:.0f}%);  no confirmation: {r['no_conf']}")
    for k, d in (("UP", r["up"]), ("DN", r["dn"])):
        if d["n"]:
            z, p = prop_z(d["hold"], d["n"], 0.80)
            fol = np.array(d["follow"])
            fm, ft, fp = mean_t(fol)
            print(f"  {k}-confirm  n={d['n']:>3}  opposite-extreme holds: "
                  f"{d['hold']/d['n']*100:>5.1f}% (vs 80% claim, z={z:+.2f}) | "
                  f"follow-through {fm:+.1f} bps  win {float((fol>0).mean())*100:.0f}%  t={ft:+.2f}")
    all_fol = np.array(r["up"]["follow"] + r["dn"]["follow"])
    fm, ft, fp = mean_t(all_fol)
    edgeB = fm > 0 and fp < 0.05
    print(f"  combined directional follow-through: {fm:+.1f} bps  t={ft:+.2f}  p={fp:.3f}"
          f"  -> {'EDGE' if edgeB else 'no significant edge'}")

    # C
    o = orb(sess, or_bars, cost_bps)
    print("-" * 72)
    print(f"  C. OPENING RANGE BREAKOUT  ({or_bars}x5m range; enter break -> close; "
          f"cost {cost_bps} bps)")
    print("-" * 72)
    if len(o["rets"]):
        m, t, p = mean_t(o["rets"])
        bm = float(o["bh"].mean())
        lm = float(o["longs"].mean()) if len(o["longs"]) else 0
        sm = float(o["shorts"].mean()) if len(o["shorts"]) else 0
        edgeC = m > 0 and p < 0.05 and m > bm
        print(f"  trades {len(o['rets'])}  | per-trade {m:+.1f} bps  win {float((o['rets']>0).mean())*100:.0f}%"
              f"  t={t:+.2f}  p={p:.3f}")
        print(f"  long {len(o['longs'])} ({lm:+.1f})   short {len(o['shorts'])} ({sm:+.1f})"
              f"   buy-the-open baseline {bm:+.1f} bps")
        print(f"  -> {'EDGE (beats baseline, significant)' if edgeC else 'no significant edge'}")
    else:
        edgeC = False
        print("  no breakouts captured")

    # D
    q = quarterly(sess)
    print("-" * 72)
    print("  D. QUARTERLY THEORY (lite)  (which 22.5-min sub-quarter forms the extreme)")
    print("-" * 72)
    if q["tot"]:
        share_h = [f"{x/q['tot']*100:.0f}%" for x in q["hi"]]
        share_l = [f"{x/q['tot']*100:.0f}%" for x in q["lo"]]
        print(f"  90-min cycles: {q['tot']}   (uniform would be 25% each)")
        print(f"  cycle HIGH forms in  Q1 {share_h[0]}  Q2 {share_h[1]}  Q3 {share_h[2]}  Q4 {share_h[3]}")
        print(f"  cycle LOW  forms in  Q1 {share_l[0]}  Q2 {share_l[1]}  Q3 {share_l[2]}  Q4 {share_l[3]}")
        edgeD = q["chi"] > 7.815      # df-ish > 0.05 crit for 4 cats (rough)
        print(f"  chi-sq vs uniform = {q['chi']:.1f}  -> "
              f"{'NON-uniform: extremes cluster in time' if edgeD else 'roughly uniform (no timing structure)'}")
    else:
        edgeD = False

    # E
    e = dr_retrace(sess, cost_bps)
    print("-" * 72)
    print("  E. IMPROVED METHOD  (DR break -> retrace-to-edge entry, stop at opp. extreme)")
    print("-" * 72)
    er = e["rets"]
    edgeE = False
    if len(er):
        em, et, ep = mean_t(er)
        edgeE = em > 0 and ep < 0.05
        print(f"  trades {len(er)} (skipped {e['no_retrace']} no-retrace) | per-trade {em:+.1f} bps"
              f"  win {float((er>0).mean())*100:.0f}%  t={et:+.2f}  p={ep:.3f}")
        gross = float(er[er > 0].sum())
        loss = float(-er[er < 0].sum())
        print(f"  profit factor {gross/loss:.2f}" if loss else "  profit factor inf")
        print(f"  -> {'EDGE (significant)' if edgeE else 'positive but not significant on this sample' if em>0 else 'no edge'}")
    else:
        print("  no qualifying trades")

    # summary
    print("\n" + "=" * 72)
    print("  SUMMARY — is time wired into when moves fire?")
    print(f"    A drift map     : {'directional windows exist' if any(abs(x[5])>2 for x in drift_map(sess)) else 'flat'}")
    print(f"    B DR/IDR        : {'EDGE' if edgeB else 'weak/none'}")
    print(f"    C ORB           : {'EDGE' if edgeC else 'weak/none'}")
    print(f"    D quarterly     : {'time-clustered extremes' if edgeD else 'uniform'}")
    print(f"    E improved DR   : {'EDGE' if edgeE else 'see above'}")
    print("=" * 72)


def main():
    ap = argparse.ArgumentParser(description="Test time-based intraday edges on real data.")
    ap.add_argument("--ticker", default="QQQ")
    ap.add_argument("--period", default="60d", help="yfinance period for 5m data (<=60d)")
    ap.add_argument("--or-bars", type=int, default=1, help="opening-range length in 5m bars")
    ap.add_argument("--cost", type=float, default=0.5, help="cost per side in bps")
    ap.add_argument("--csv", help="load 5m OHLC from CSV instead of yfinance")
    args = ap.parse_args()
    try:
        df = load(args.ticker, args.period, args.csv)
    except Exception as exc:
        print(f"[!] Could not load data: {exc}")
        print("    Run locally with internet, or pass --csv.")
        return
    if len(df) < 600:
        print(f"[!] Only {len(df)} bars — need more for a meaningful test.")
        return
    run(df, args.cost, args.or_bars, args.csv or f"{args.ticker} 5m {args.period}")


if __name__ == "__main__":
    main()
