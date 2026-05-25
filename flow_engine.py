"""
Flow Engine — trade WHAT the market is doing, gated by ORDER-FLOW (volume).

Synthesis of market-microstructure theory (Avellaneda-Stoikov; OFI; VPIN /
order-flow toxicity, Easley-Lopez de Prado-O'Hara):

  - Market makers quote a spread and have NO directional view; they provide
    liquidity while flow is balanced (= ACCUMULATION / range).
  - When flow turns TOXIC (one-sided + high volume), MMs widen/withdraw and
    price DISPLACES with little resistance (= the move). It continues until
    flow rebalances.

Testable prediction: a displacement is INFORMED (continues) only if it comes
with abnormal volume. So momentum should be strong on high relative-volume
(RVOL) displacements and absent/negative on low-volume ones.

RVOL controls for the intraday U-shape (volume is mechanically high at the
open/close), so we measure ABNORMAL participation, not the clock. The volume
profile is built on TRAIN dates only and applied to the TEST set.

    python flow_engine.py --json QQQ_vol.json
"""

from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from time_edge_lab import to_ny, RTH_OPEN, RTH_CLOSE


def load_vol(path):
    d = pd.DataFrame(json.load(open(path)))
    d["date"] = pd.to_datetime(d["date"], utc=True)
    d = d.set_index("date").sort_index()
    d = d.rename(columns={c: c.capitalize() for c in d.columns})
    for c in ["Open", "High", "Low", "Close", "Volume"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    d = d.set_index(to_ny(d.index))
    m = d.index.hour * 60 + d.index.minute
    return d[(m >= RTH_OPEN) & (m < RTH_CLOSE)]


def to_sessions(d):
    out = []
    for day, g in d.groupby(d.index.date):
        if len(g) < 60:
            continue
        if g.index[0].hour * 60 + g.index[0].minute > RTH_OPEN + 5:
            continue
        out.append((day, g))
    return out


def atr(h, l, c, n):
    prev = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))
    return pd.Series(tr).rolling(n).mean().to_numpy()


def vol_profile(sess):
    """median volume by minute-of-day (the U-shape), for RVOL normalisation."""
    buf = {}
    for _, g in sess:
        key = g.index.hour * 60 + g.index.minute
        for k, v in zip(key, g["Volume"].to_numpy(float)):
            buf.setdefault(int(k), []).append(v)
    return {k: float(np.median(v)) for k, v in buf.items()}


def stat(b):
    b = np.asarray(b); n = len(b)
    if n < 2:
        return n, 0.0, 0.0, 1.0, 0.0
    m = float(b.mean()); sd = float(b.std(ddof=1))
    t = m / (sd / np.sqrt(n)) if sd > 0 else 0.0
    g = float(b[b > 0].sum()); ls = float(-b[b < 0].sum())
    return n, m, t, (g / ls if ls else float("inf")), float((b > 0).mean())


def trades(sess, prof, P):
    """displacement-momentum trades, each tagged with the displacement bar's RVOL."""
    K, Z, H, A = P["K"], P["Z"], P["H"], P["atr"]
    out = []
    med_all = float(np.median(list(prof.values())))
    for day, g in sess:
        c = g["Close"].to_numpy(float); h = g["High"].to_numpy(float)
        l = g["Low"].to_numpy(float); v = g["Volume"].to_numpy(float)
        key = (g.index.hour * 60 + g.index.minute).to_numpy()
        a = atr(h, l, c, A); n = len(c); i = max(K, A)
        while i < n - 1:
            if np.isnan(a[i]) or a[i] <= 0:
                i += 1; continue
            move = (c[i] - c[i - K]) / a[i]
            if abs(move) < Z:
                i += 1; continue
            rv = v[i] / prof.get(int(key[i]), med_all)        # relative volume of the displacement bar
            j = min(i + H, n - 1)
            ret = (1 if move > 0 else -1) * (c[j] - c[i]) / c[i] * 1e4   # go WITH the move, gross
            out.append((day, rv, ret))
            i = j + 1
    return out


def report(name, tr, cost):
    rv = np.array([t[1] for t in tr]); g = np.array([t[2] for t in tr])
    net = g - 2 * cost
    hi = rv >= np.quantile(rv, 0.66)        # top third by abnormal volume
    lo = rv <= np.quantile(rv, 0.33)        # bottom third
    print(f"\n  {name}: {len(tr)} displacements")
    for tag, mask in (("HIGH-RVOL (toxic flow)", hi), ("LOW-RVOL (thin)", lo)):
        n, m, t, pf, w = stat(g[mask]); _, mn, tn, pfn, _ = stat(net[mask])
        print(f"    {tag:<24} n={n:<5} gross {m:+.2f} t={t:+.2f} PF={pf:.2f} | "
              f"net@{cost} {mn:+.2f} t={tn:+.2f} win {w*100:.0f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True)
    ap.add_argument("--K", type=int, default=3)
    ap.add_argument("--Z", type=float, default=2.0)
    ap.add_argument("--H", type=int, default=12)
    ap.add_argument("--atr", type=int, default=14)
    ap.add_argument("--cost", type=float, default=1.0)
    args = ap.parse_args(); P = vars(args)

    sess = to_sessions(load_vol(args.json))
    cut = int(len(sess) * 0.6)
    train, test = sess[:cut], sess[cut:]
    prof = vol_profile(train)                      # U-shape from train only

    print("=" * 76)
    print(f"  FLOW ENGINE  ({args.json})   displacement gated by relative volume")
    print(f"  {len(sess)} sessions; profile from {len(train)} train, tested on {len(test)}")
    print("=" * 76)

    # the volume U-shape (their intuition: volume IS time-phased)
    print("\n  VOLUME U-SHAPE (median IEX volume by 30-min bucket, % of day):")
    by30 = {}
    for k, val in prof.items():
        by30.setdefault((k - RTH_OPEN) // 30, []).append(val)
    tot = sum(np.mean(v) for v in by30.values())
    for b in sorted(by30):
        share = np.mean(by30[b]) / tot
        lbl = f"{(RTH_OPEN+30*b)//60:02d}:{(RTH_OPEN+30*b)%60:02d}"
        print(f"   {lbl} |{'#'*int(share*200)} {share*100:.1f}%")

    print("\n  --- does volume CONFIRM the move? (the toxic-flow test) ---")
    report("FULL SAMPLE", trades(sess, prof, P), args.cost)
    report("TEST / OUT-OF-SAMPLE", trades(test, prof, P), args.cost)
    print("\n" + "=" * 76)
    print("  If HIGH-RVOL displacements continue but LOW-RVOL don't, the trigger")
    print("  for a real move is order-flow (volume), exactly as MM theory predicts.")
    print("=" * 76)


if __name__ == "__main__":
    main()
