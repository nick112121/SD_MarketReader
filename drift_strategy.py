"""
Time-of-day drift harvester — honest out-of-sample test.

The pooled study found the real signal isn't breakouts or prime minutes; it's a
persistent intraday DRIFT SHAPE (up mid-morning + into the close, down early
afternoon) with large t-stats. This tests whether that shape is actually
TRADEABLE, without fooling ourselves:

  1. Split the basket of tickers in two.
  2. On the TRAIN half, learn each 30-min bucket's drift direction (long/short).
  3. On the held-out TEST half, trade those fixed directions, net of cost.
  4. Report test expectancy + significance. If it holds out-of-sample, it's real.

    python drift_strategy.py
"""

from __future__ import annotations

import numpy as np

from time_edge_lab import load, sessions, mean_t, mins_of, RTH_OPEN

TICKERS = ["QQQ", "SPY", "DIA", "IWM", "AAPL", "MSFT", "NVDA", "AMZN",
           "META", "GOOGL", "TSLA", "AVGO", "AMD", "NFLX", "COST", "JPM"]
COST_BPS = 0.5
N_BUCKETS = 13          # 09:30..16:00 in 30-min steps


def bucket_returns(sess: list) -> dict[int, list]:
    """Per-session return of each 30-min bucket (first open -> last close), bps."""
    out: dict[int, list] = {}
    for _, g in sess:
        m = mins_of(g)
        for b in range(N_BUCKETS):
            lo, hi = RTH_OPEN + 30 * b, RTH_OPEN + 30 * (b + 1)
            seg = g[(m >= lo) & (m < hi)]
            if len(seg) < 2:
                continue
            r = (float(seg["Close"].iloc[-1]) - float(seg["Open"].iloc[0])) / float(seg["Open"].iloc[0])
            out.setdefault(b, []).append(r * 1e4)
    return out


def fetch(tickers):
    sess_by = {}
    for t in tickers:
        try:
            sess_by[t] = sessions(load(t, "60d", None, None))
        except Exception as exc:
            print(f"  {t}: FAILED ({exc})")
    return sess_by


def main():
    print("Fetching basket (5m/60d)...")
    sess_by = fetch(TICKERS)
    names = list(sess_by)
    train_names = names[0::2]            # every other ticker -> train
    test_names = names[1::2]             # the rest -> held-out test
    train = [s for t in train_names for s in sess_by[t]]
    test = [s for t in test_names for s in sess_by[t]]
    print(f"train: {len(train_names)} names / {len(train)} sessions   "
          f"test: {len(test_names)} names / {len(test)} sessions\n")

    # 1. learn bucket directions on TRAIN
    tr = bucket_returns(train)
    sign = {}
    print("Learned drift directions (TRAIN):")
    for b in range(N_BUCKETS):
        if b not in tr:
            continue
        arr = np.array(tr[b]); m, t, _ = mean_t(arr)
        sign[b] = 1 if m > 0 else -1
        lbl = f"{(RTH_OPEN+30*b)//60:02d}:{(RTH_OPEN+30*b)%60:02d}"
        print(f"   {lbl}  mean {m:+.2f} bps  t={t:+.2f}  -> {'LONG' if sign[b]>0 else 'SHORT'}")

    # 2. trade fixed directions on TEST — collect GROSS (no cost) once, cost later
    gross_trades = []                     # per-bucket sign*return, bps, gross
    day_gross, day_nbkt = [], []          # per-session gross sum + #buckets traded
    for _, g in test:
        m = mins_of(g); tot = 0.0; nb = 0
        for b in range(N_BUCKETS):
            if b not in sign:
                continue
            lo, hi = RTH_OPEN + 30 * b, RTH_OPEN + 30 * (b + 1)
            seg = g[(m >= lo) & (m < hi)]
            if len(seg) < 2:
                continue
            r = (float(seg["Close"].iloc[-1]) - float(seg["Open"].iloc[0])) / float(seg["Open"].iloc[0]) * 1e4
            gr = sign[b] * r
            gross_trades.append(gr)
            tot += gr; nb += 1
        if nb:
            day_gross.append(tot); day_nbkt.append(nb)

    gt = np.array(gross_trades); dg = np.array(day_gross); dn = np.array(day_nbkt)

    print("\n" + "=" * 72)
    print(f"  OUT-OF-SAMPLE RESULT  (test = {len(test_names)} held-out names, {len(test)} sessions)")
    print("=" * 72)
    print(f"  {'cost/side':>9} | {'bps/trade':>9} {'win%':>6} {'t':>6} {'p':>6} {'PF':>5} | "
          f"{'bps/day':>8} {'ann.%':>7}")
    for cost in (0.0, 0.5, 1.0, 2.0, 3.0, 5.0):
        nt = gt - 2 * cost
        nd = dg - 2 * cost * dn
        tm, tt, tp = mean_t(nt)
        dm, dt, dp = mean_t(nd)
        gross = float(nt[nt > 0].sum()); loss = float(-nt[nt < 0].sum())
        pf = gross / loss if loss else float("inf")
        flag = "  <<" if dm > 0 and dp < 0.05 else ""
        print(f"  {cost:>9.1f} | {tm:>+9.2f} {float((nt>0).mean())*100:>5.1f}% {tt:>+6.2f} "
              f"{tp:>6.3f} {pf:>5.2f} | {dm:>+8.2f} {dm*252/100:>+6.1f}%{flag}")
    print("  ('<<' = day-level edge still significant at that cost level)")
    print("  Caveats: liquid-ETF spread ~1-2 bps; correlated names inflate t;")
    print("  60-day sample is one (bullish) regime, so 'long' buckets are partly that.")
    print("=" * 72)


if __name__ == "__main__":
    main()
