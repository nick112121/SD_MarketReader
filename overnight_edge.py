"""
Time-based edges that have a CAUSAL basis (not chart numerology).

The minute-of-hour concepts (Goldbach) and the intraday drift shape both washed
out across regimes. This studies time effects that persist in the literature
because they're tied to real mechanics, and checks each with a TEMPORAL
out-of-sample split (train older dates, test newer) so we don't fool ourselves:

  1. OVERNIGHT vs INTRADAY — historically the equity premium accrues overnight
     (close->open); intraday (open->close) is flat/negative. WHEN you hold is
     the edge. (Lou-Polk-Skouras; "A Tug of War", 2019.)
  2. INTRADAY MOMENTUM — the first 30-min (and the overnight gap) predict the
     last 30-min return. (Gao-Han-Li-Zhou 2018.)
  3. TURN-OF-MONTH — returns concentrate around month boundaries (fund flows).
     (Ariel; McConnell-Xu 2008.)

    python overnight_edge.py --json QQQ_long.json
"""

from __future__ import annotations

import argparse

import numpy as np

from time_edge_lab import load, sessions, mins_of, RTH_OPEN


def daily_series(sess: list):
    """Per session: date, open, close, first-30m return, last-30m return."""
    dates, op, cl, f30, l30 = [], [], [], [], []
    for day, g in sess:
        m = mins_of(g)
        o = float(g["Open"].iloc[0]); c = float(g["Close"].iloc[-1])
        fseg = g[(m >= RTH_OPEN) & (m < RTH_OPEN + 30)]
        lseg = g[(m >= 16 * 60 - 30) & (m < 16 * 60)]
        if len(fseg) < 2 or len(lseg) < 2:
            continue
        dates.append(day); op.append(o); cl.append(c)
        f30.append(float(fseg["Close"].iloc[-1]) / float(fseg["Open"].iloc[0]) - 1)
        l30.append(float(lseg["Close"].iloc[-1]) / float(lseg["Open"].iloc[0]) - 1)
    op, cl = np.array(op), np.array(cl)
    intraday = cl / op - 1
    overnight = np.full(len(op), np.nan)
    overnight[1:] = op[1:] / cl[:-1] - 1            # today open vs yesterday close
    full = np.full(len(op), np.nan)
    full[1:] = cl[1:] / cl[:-1] - 1
    return dates, intraday, overnight, full, np.array(f30), np.array(l30)


def stats(r: np.ndarray):
    r = r[~np.isnan(r)]
    n = len(r); m = float(r.mean()); sd = float(r.std(ddof=1))
    t = m / (sd / np.sqrt(n)) if sd > 0 else 0.0
    sharpe = (m / sd) * np.sqrt(252) if sd > 0 else 0.0
    cum = (np.prod(1 + r) - 1) * 100
    return n, m * 1e4, t, m * 252 * 100, sharpe, cum


def line(name, r):
    n, bps, t, ann, sh, cum = stats(r)
    print(f"  {name:<22} n={n:<5} {bps:+7.2f} bps/day  t={t:+6.2f}  "
          f"ann {ann:+7.1f}%  Sharpe {sh:+5.2f}  cum {cum:+8.1f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True)
    args = ap.parse_args()
    sess = sessions(load("", "", None, args.json))
    dates, intraday, overnight, full, f30, l30 = daily_series(sess)
    n = len(dates)
    print("=" * 78)
    print(f"  CAUSAL TIME-EDGE STUDY   ({n} sessions, {dates[0]} -> {dates[-1]})")
    print("=" * 78)

    # 1. overnight vs intraday
    print("\n  1. OVERNIGHT vs INTRADAY (when you hold):")
    line("overnight (C->O)", overnight)
    line("intraday  (O->C)", intraday)
    line("buy&hold  (C->C)", full)
    cut = int(n * 0.6)
    print("     temporal persistence of the OVERNIGHT return:")
    _, b1, t1, *_ = stats(overnight[:cut])
    _, b2, t2, *_ = stats(overnight[cut:])
    print(f"       train {dates[1]}..{dates[cut-1]}: {b1:+.2f} bps/day (t={t1:+.2f})")
    print(f"       test  {dates[cut]}..{dates[-1]}: {b2:+.2f} bps/day (t={t2:+.2f})")
    on_robust = b1 > 0 and b2 > 0 and t2 > 1.5

    # 1b. market-neutral night-minus-day (the cleanest expression of the anomaly)
    spread = overnight - intraday          # long overnight, short intraday, daily
    print("\n  1b. NIGHT-MINUS-DAY spread (long overnight / short intraday, beta~0):")
    line("night - day", spread)
    _, s1, st1, *_ = stats(spread[:cut])
    _, s2, st2, *_ = stats(spread[cut:])
    print(f"       train: {s1:+.2f} bps/day (t={st1:+.2f})   test: {s2:+.2f} bps/day (t={st2:+.2f})")
    spread_robust = s1 > 0 and s2 > 0

    # 2. intraday momentum (predict last 30m)
    print("\n  2. INTRADAY MOMENTUM (does morning predict the last 30 min?):")
    valid = ~np.isnan(overnight)
    for sig_name, sig in (("first-30m", f30), ("overnight gap", overnight)):
        s = sig[valid]; y = l30[valid]
        # train sign on first 60%, apply to last 40%
        c = int(len(s) * 0.6)
        direction = 1 if np.corrcoef(s[:c], y[:c])[0, 1] >= 0 else -1   # momentum or reversal
        pos = direction * np.sign(s[c:])
        pnl = pos * y[c:]
        nn, bps, t, ann, sh, cum = stats(pnl)
        corr = float(np.corrcoef(s[:c], y[:c])[0, 1])
        print(f"     {sig_name:<14} train-corr {corr:+.3f} -> OOS last-30m trade: "
              f"{bps:+.2f} bps  t={t:+.2f}  win {float((pnl>0).mean())*100:.1f}%")

    # 3. turn of month
    print("\n  3. TURN-OF-MONTH (full daily return, C->C):")
    tom = np.zeros(n, dtype=bool)
    months: dict = {}
    for i, d in enumerate(dates):
        months.setdefault((d.year, d.month), []).append(i)
    for idxs in months.values():
        for i in idxs[:3]:           # first 3 trading days
            tom[i] = True
        tom[idxs[-1]] = True         # last trading day
    line("turn-of-month days", full[tom])
    line("rest-of-month days", full[~tom])

    print("\n" + "=" * 78)
    print("  VERDICT")
    on_n, on_bps, on_t, on_ann, on_sh, _ = stats(overnight)
    intr_n, intr_bps, intr_t, *_ = stats(intraday)
    print(f"   - Overnight premium real & persistent? "
          f"{'YES' if on_robust else 'weak'}  ({on_bps:+.2f} bps/day, Sharpe {on_sh:.2f})")
    print(f"   - Intraday (open->close) drift?         {intr_bps:+.2f} bps/day (t={intr_t:+.2f})")
    print("   Single instrument, gross of ~1-2 bps cost; overnight = 1 round-trip/day.")
    print("=" * 78)


if __name__ == "__main__":
    main()
