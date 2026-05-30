"""
Pooled time-edge run — one verdict across a basket of liquid US names.

Free yfinance caps 5m history at ~60 days per symbol, too short to reach
significance on one ticker. This pools sessions across many tickers (returns
are in bps, so comparable) to get a large effective sample NOW, and runs the
same tests from time_edge_lab on the combined set.

Caveat: tech names are correlated, so observations aren't fully independent —
naive p-values are optimistic. Treat significance as suggestive, not final.

    python pool_run.py
"""

from __future__ import annotations

import numpy as np

from time_edge_lab import (load, sessions, drift_map, dr_idr, orb, dr_retrace,
                           mean_t, prop_z)

TICKERS = ["QQQ", "SPY", "DIA", "IWM", "AAPL", "MSFT", "NVDA", "AMZN",
           "META", "GOOGL", "TSLA", "AVGO", "AMD", "NFLX", "COST", "JPM"]
COST_BPS = 0.5
OR_BARS = 1


def main():
    all_sess = []
    print("Fetching + pooling 5m/60d sessions:")
    for t in TICKERS:
        try:
            s = sessions(load(t, "60d", None, None))
            all_sess += s
            print(f"  {t:>5}: {len(s)} sessions")
        except Exception as exc:
            print(f"  {t:>5}: FAILED ({exc})")
    n = len(all_sess)
    print(f"\nPOOLED SESSIONS: {n}\n")
    if n < 100:
        print("[!] not enough pooled data."); return

    print("=" * 72)
    print(f"  POOLED TIME-EDGE RESULT   ({len(TICKERS)} names, {n} sessions)")
    print("=" * 72)

    # A. drift — significant windows only
    print("\n  A. TIME-OF-DAY DRIFT (significant 30-min windows, |t|>2):")
    any_sig = False
    for lbl, c, mean, win, vol, t in drift_map(all_sess):
        if abs(t) > 2:
            any_sig = True
            print(f"     {lbl}  {mean:+.3f} bps/bar  win {win*100:.1f}%  t={t:+.2f}")
    if not any_sig:
        print("     none")

    # B. DR/IDR — pooled hold rate + follow-through
    r = dr_idr(all_sess)
    up, dn = r["up"], r["dn"]
    hold = up["hold"] + dn["hold"]
    conf = up["n"] + dn["n"]
    z, p = prop_z(hold, conf, 0.80)
    fol = np.array(up["follow"] + dn["follow"])
    fm, ft, fp = mean_t(fol)
    print("\n  B. DR/IDR (09:30-10:30 Defining Range):")
    print(f"     confirmed {conf}/{n} sessions")
    print(f"     opposite-extreme HOLDS: {hold/conf*100:.1f}%  (vs 80% claim, z={z:+.2f}, p={p:.3f})")
    print(f"     follow-through to close: {fm:+.2f} bps  win {float((fol>0).mean())*100:.1f}%  t={ft:+.2f}  p={fp:.3f}")

    # C. ORB
    o = orb(all_sess, OR_BARS, COST_BPS)
    om, ot, op = mean_t(o["rets"])
    print("\n  C. OPENING RANGE BREAKOUT (naive, break->close):")
    print(f"     {len(o['rets'])} trades  {om:+.2f} bps  win {float((o['rets']>0).mean())*100:.1f}%  "
          f"t={ot:+.2f}  p={op:.3f}  | buy-open baseline {float(o['bh'].mean()):+.2f} bps")

    # E. improved method — the headline
    e = dr_retrace(all_sess, COST_BPS)
    er = e["rets"]
    em, et, ep = mean_t(er)
    gross = float(er[er > 0].sum()); loss = float(-er[er < 0].sum())
    pf = gross / loss if loss else float("inf")
    print("\n  E. IMPROVED METHOD (DR break -> retrace entry, stop at opp. extreme):")
    print(f"     {len(er)} trades (skipped {e['no_retrace']} no-retrace)")
    print(f"     per-trade {em:+.2f} bps  win {float((er>0).mean())*100:.1f}%  "
          f"t={et:+.2f}  p={ep:.3f}  profit factor {pf:.2f}")

    print("\n" + "=" * 72)
    print("  VERDICT")
    print(f"   - Time-of-day drift windows real?      {'YES' if any_sig else 'no'}")
    print(f"   - DR contains a session extreme ~80%?  {'YES' if hold/conf >= 0.78 else 'no'} ({hold/conf*100:.0f}%)")
    print(f"   - Improved DR method has an edge?       "
          f"{'YES (significant)' if em > 0 and ep < 0.05 else 'positive but not significant' if em > 0 else 'no'}")
    print("   (tech names correlate -> p-values optimistic; suggestive, not final)")
    print("=" * 72)


if __name__ == "__main__":
    main()
