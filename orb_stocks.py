"""
Opening-Range Breakout on "stocks in play" — the one day-trading edge with
peer-reviewed support (Zarattini & Aziz 2023).

Naive ORB on an index has no edge. The documented edge is ONLY in stocks that
are "in play" that day: high relative volume / gapping (news, earnings). The
order-flow imbalance from that event makes the opening move CONTINUE.

Rule tested (per session, per stock):
  - Opening range = first 5-minute bar (09:30-09:35).
  - Enter on the first 5m close beyond the OR (long above / short below), within
    the first hour.
  - Stop = opposite side of the opening range.  Risk R = |entry - stop|.
  - Exit at the close. Result measured in R-multiples (return / risk).
  - "IN PLAY" filter = opening-bar relative volume (vs the stock's own median)
    and/or overnight gap. We compare in-play days vs the rest.

Data: yfinance 5m, 60 days (short -- treat as a directional read, not proof).

    python orb_stocks.py
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
import yfinance as yf

UNIVERSE = ["NVDA", "TSLA", "AMD", "META", "AMZN", "AAPL", "MSFT", "GOOGL",
            "NFLX", "AVGO", "PLTR", "COIN", "SMCI", "MU", "MARA"]
RTH_OPEN, RTH_CLOSE = 9*60+30, 16*60


def to_ny(idx):
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    return idx.tz_convert("America/New_York")


def stock_trades(tk, cost_bps):
    df = yf.download(tk, period="60d", interval="5m", progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna()
    if len(df) < 200:
        return []
    df = df.set_index(to_ny(df.index))
    mins = df.index.hour*60 + df.index.minute
    df = df[(mins >= RTH_OPEN) & (mins < RTH_CLOSE)]
    sessions = [(d, g) for d, g in df.groupby(df.index.date) if len(g) >= 60
                and g.index[0].hour*60+g.index[0].minute <= RTH_OPEN+5]
    open_vols = [float(g["Volume"].iloc[0]) for _, g in sessions]
    med_ov = np.median(open_vols) if open_vols else 1.0
    out, prev_close = [], None
    for _, g in sessions:
        o = g["Open"].to_numpy(float); h = g["High"].to_numpy(float)
        l = g["Low"].to_numpy(float); c = g["Close"].to_numpy(float)
        v = g["Volume"].to_numpy(float)
        m = (g.index.hour*60 + g.index.minute).to_numpy()
        orh, orl = h[0], l[0]                      # first 5-min bar range
        gap = (o[0] / prev_close - 1) if prev_close else 0.0
        rvol = v[0] / med_ov if med_ov else 1.0
        prev_close = c[-1]
        entry = direction = stop = None; entry_i = None
        for i in range(1, len(c)):
            if m[i] > RTH_OPEN + 60:               # only enter in the first hour
                break
            if c[i] > orh:
                direction, entry, stop, entry_i = 1, c[i], orl, i; break
            if c[i] < orl:
                direction, entry, stop, entry_i = -1, c[i], orh, i; break
        if entry is None:
            continue
        risk = abs(entry - stop)
        if risk <= 0:
            continue
        exit_px = c[-1]                              # default: exit at the close
        for i in range(entry_i + 1, len(c)):         # but stop out intrabar if hit
            if direction == 1 and l[i] <= stop:
                exit_px = stop; break
            if direction == -1 and h[i] >= stop:
                exit_px = stop; break
        R = direction * (exit_px - entry) / risk
        ret_bps = direction * (exit_px - entry) / entry * 1e4 - 2*cost_bps
        out.append((abs(gap), rvol, R, ret_bps))
    return out


def summ(rows, label):
    if not rows:
        print(f"  {label:<28} no trades"); return
    R = np.array([r[2] for r in rows]); bps = np.array([r[3] for r in rows])
    exp = R.mean(); win = float((R > 0).mean())
    sd = R.std(ddof=1) if len(R) > 1 else 0
    t = exp/(sd/np.sqrt(len(R))) if sd > 0 else 0
    print(f"  {label:<28} n={len(R):<4} expR {exp:+.2f}  win {win*100:.0f}%  "
          f"t={t:+.2f}  net {bps.mean():+.1f}bps")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cost", type=float, default=2.0)
    ap.add_argument("--rvol", type=float, default=1.5, help="in-play relative-volume threshold")
    ap.add_argument("--gap", type=float, default=0.01, help="in-play gap threshold")
    args = ap.parse_args()
    allrows = []
    for tk in UNIVERSE:
        try:
            r = stock_trades(tk, args.cost)
            allrows += r
            print(f"  {tk}: {len(r)} ORB days")
        except Exception as e:
            print(f"  {tk}: {e}")
    print("\n" + "="*64)
    print(f"  ORB 'STOCKS IN PLAY'  ({len(UNIVERSE)} names, 60d, cost {args.cost}bps/side)")
    print("  R = profit in units of risk (stop = other side of opening range)")
    print("="*64)
    summ(allrows, "ALL ORB days")
    inplay = [r for r in allrows if r[1] >= args.rvol or r[0] >= args.gap]
    quiet = [r for r in allrows if not (r[1] >= args.rvol or r[0] >= args.gap)]
    summ(inplay, f"IN PLAY (RVOL>{args.rvol} or gap>{args.gap*100:.0f}%)")
    summ(quiet, "QUIET (not in play)")
    hi = [r for r in allrows if r[1] >= 2.0]
    summ(hi, "VERY HIGH RVOL (>2x)")
    print("="*64)
    print("  Edge exists if IN-PLAY expR>0 and beats QUIET. (60d = directional read.)")


if __name__ == "__main__":
    main()
