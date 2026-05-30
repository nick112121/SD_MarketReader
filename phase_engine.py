"""
Phase Engine — a state machine that trades WHAT the market is doing, not the clock.

Every algo sits in a state until an INPUT flips it. Here the input is a
DISPLACEMENT (a move >= Z*ATR over K bars) and the three states are:

  ACCUMULATION : |move| < Z*ATR  -> quiet / ranging -> stand aside.
  BUY          : move >= +Z*ATR  -> the algo is committing upward.
  SELL         : move <= -Z*ATR  -> committing downward.

Empirical finding behind the default direction (tested on 6.4y of 5m QQQ/SPY/
IWM): intraday moves CONTINUE, they don't mean-revert -- fading a sharp move
loses with t = -6 to -7, robust out-of-sample. So the default action is
MOMENTUM (go WITH the displacement). `--mode reversal` flips it to fade.

This fires at any time of day (state-based, not clock-based). Honest checks
built in: entry-by-hour histogram, temporal out-of-sample split, cost sweep.

NOTE ON COSTS: the per-trade continuation edge is small. It only clears a
realistic ~1-2 bps/side on rare, large displacements (high Z), where the
sample is thin. Treat this as a directional-bias engine, not a HFT money
printer -- the cost sweep below shows exactly where it dies.

    python phase_engine.py --json QQQ_long.json
    python phase_engine.py --json IWM_long.json --Z 3 --H 24 --mode momentum
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from time_edge_lab import load, sessions


def atr(h, l, c, n):
    prev = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))
    return pd.Series(tr).rolling(n).mean().to_numpy()


def run_session(g, P):
    c = g["Close"].to_numpy(float); h = g["High"].to_numpy(float); l = g["Low"].to_numpy(float)
    mins = (g.index.hour * 60 + g.index.minute).to_numpy()
    n = len(c)
    a = atr(h, l, c, P["atr"])
    K, Z, H = P["K"], P["Z"], P["H"]
    sgn = 1 if P["mode"] == "momentum" else -1
    trades = {"ACCUM": 0, "BUY": 0, "SELL": 0}
    rows, i = [], max(K, P["atr"])
    while i < n - 1:
        if np.isnan(a[i]) or a[i] <= 0:
            i += 1; continue
        move = (c[i] - c[i - K]) / a[i]
        if move >= Z:
            state = "BUY"
        elif move <= -Z:
            state = "SELL"
        else:
            trades["ACCUM"] += 1; i += 1; continue
        trades[state] += 1
        entry = c[i]; j = min(i + H, n - 1); exit_px = c[j]
        direction = sgn * (1 if move > 0 else -1)
        ret = direction * (exit_px - entry) / entry * 1e4
        rows.append((g.index[i], int(mins[i]), ret))
        i = j + 1
    return rows, trades


def stat(b):
    b = np.asarray(b); n = len(b)
    if n < 2:
        return n, 0.0, 0.0, 1.0
    m = float(b.mean()); sd = float(b.std(ddof=1))
    t = m / (sd / np.sqrt(n)) if sd > 0 else 0.0
    g = float(b[b > 0].sum()); ls = float(-b[b < 0].sum())
    return n, m, t, (g / ls if ls else float("inf"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True)
    ap.add_argument("--K", type=int, default=3, help="displacement lookback (bars)")
    ap.add_argument("--Z", type=float, default=2.0, help="displacement size in ATRs")
    ap.add_argument("--H", type=int, default=12, help="hold (bars)")
    ap.add_argument("--atr", type=int, default=14)
    ap.add_argument("--mode", choices=["momentum", "reversal"], default="momentum")
    ap.add_argument("--cost", type=float, default=1.0, help="bps per side")
    args = ap.parse_args()
    P = vars(args)

    sess = sessions(load("", "", None, args.json))
    rows, tot = [], {"ACCUM": 0, "BUY": 0, "SELL": 0}
    for s in sess:
        r, ph = run_session(s[1], P)
        rows += r
        for k in tot: tot[k] += ph[k]
    rows.sort(key=lambda x: x[0])
    gross = np.array([r[2] for r in rows])
    mins = np.array([r[1] for r in rows])
    rt = 2 * args.cost
    net = gross - rt

    print("=" * 74)
    print(f"  PHASE ENGINE [{args.mode}]  ({args.json})")
    print(f"  {len(sess)} sessions, {rows[0][0]:%Y-%m-%d} -> {rows[-1][0]:%Y-%m-%d}")
    print("=" * 74)
    bars = sum(tot.values())
    print(f"  states: ACCUM {tot['ACCUM']/bars*100:.1f}%  BUY {tot['BUY']}  SELL {tot['SELL']}"
          f"  (trades {len(gross)})")

    n, gm, gt, gpf = stat(gross)
    nn, nm, nt, npf = stat(net)
    print(f"\n  GROSS: {gm:+.2f} bps  t={gt:+.2f}  PF={gpf:.2f}   "
          f"(the raw continuation signal)")
    print(f"  NET @ {args.cost} bps/side: {nm:+.2f} bps  t={nt:+.2f}  PF={npf:.2f}  win {float((net>0).mean())*100:.0f}%")

    print("\n  entries by hour-of-day (ET) — state-based, fires across the day:")
    for hr in range(9, 16):
        cnt = int(((mins >= hr*60) & (mins < hr*60+60)).sum())
        print(f"   {hr:02d}:00 |{'#'*int(40*cnt/max(len(mins),1))} {cnt}")

    cut = int(len(gross) * 0.6)
    _, _, t1, _ = stat(gross[:cut]); _, gm2, t2, pf2 = stat(gross[cut:])
    print(f"\n  TEMPORAL OUT-OF-SAMPLE (gross): train t={t1:+.2f}  |  test {gm2:+.2f} bps t={t2:+.2f} PF={pf2:.2f}")

    print("\n  cost sweep (bps/side):")
    for cps in (0.0, 0.5, 1.0, 2.0):
        _, mm, tt, pff = stat(gross - 2*cps)
        flag = "  <<" if mm > 0 and tt > 2 else ""
        print(f"   {cps:>4.1f} | {mm:+.2f} bps  t={tt:+.2f}  PF={pff:.2f}{flag}")
    print("=" * 74)


if __name__ == "__main__":
    main()
