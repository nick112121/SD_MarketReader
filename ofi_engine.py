"""
OFI Engine — true Order-Flow Imbalance from Databento order-book (mbp-1) data.

This is the real microstructure signal the 5-minute volume proxy could only
shadow. From every top-of-book update we compute OFI (Cont, Kukanov & Stoikov
2014): the net change in best-bid/ask depth that drives short-term price.

  ΔW_bid = Q_bid                 if bid price rose
         = Q_bid - Q_bid_prev    if bid price unchanged
         = -Q_bid_prev           if bid price fell
  ΔW_ask = mirror on the ask
  OFI    = ΔW_bid - ΔW_ask   (summed over a time bucket)

Tests:
  CONTEMPORANEOUS  mid-change_t  ~ OFI_t      (should be strong: the mechanism)
  PREDICTIVE       mid-change_t+1 ~ OFI_t     (the tradeable edge question)
plus the same for trade imbalance (aggressor-signed volume).

    python ofi_engine.py --file /tmp/nq_mbp1_20250515.parquet --bucket 1s
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

TICK = 0.25   # NQ futures tick size (points)


def ols(x, y):
    """slope, intercept, R^2, t-stat of slope."""
    x = np.asarray(x, float); y = np.asarray(y, float)
    m = ~(np.isnan(x) | np.isnan(y))
    x, y = x[m], y[m]; n = len(x)
    if n < 10 or x.std() == 0:
        return 0, 0, 0, 0, n
    b, a = np.polyfit(x, y, 1)
    yhat = a + b * x
    ss_res = float(((y - yhat) ** 2).sum()); ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot else 0
    se = np.sqrt(ss_res / (n - 2)) / (np.sqrt(((x - x.mean()) ** 2).sum()) or 1)
    t = b / se if se else 0
    return b, a, r2, t, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--bucket", default="1s", help="pandas freq for buckets (1s, 5s, 10s)")
    ap.add_argument("--cost_ticks", type=float, default=1.0, help="round-trip cost in ticks")
    args = ap.parse_args()

    df = pd.read_parquet(args.file)
    bpx = df["bid_px_00"].to_numpy(float); apx = df["ask_px_00"].to_numpy(float)
    bsz = df["bid_sz_00"].to_numpy(float); asz = df["ask_sz_00"].to_numpy(float)
    ok = (bpx > 0) & (apx > 0) & (apx >= bpx) & (apx - bpx < 5)
    df = df[ok]; bpx, apx, bsz, asz = bpx[ok], apx[ok], bsz[ok], asz[ok]
    mid = (bpx + apx) / 2
    ts = df.index.values.astype("int64")

    # OFI increments (aligned to event i, i>=1)
    dWb = np.where(bpx[1:] > bpx[:-1], bsz[1:],
          np.where(bpx[1:] < bpx[:-1], -bsz[:-1], bsz[1:] - bsz[:-1]))
    dWa = np.where(apx[1:] < apx[:-1], asz[1:],
          np.where(apx[1:] > apx[:-1], -asz[:-1], asz[1:] - asz[:-1]))
    ofi = dWb - dWa

    # trade imbalance (aggressor-signed size via price vs mid)
    is_trade = (df["action"].to_numpy() == "T")[1:]
    tprice = df["price"].to_numpy(float)[1:]
    tsigned = np.where(is_trade, np.sign(tprice - mid[1:]) * df["size"].to_numpy(float)[1:], 0.0)

    bucket = pd.to_datetime(ts[1:]).floor(args.bucket)
    agg = pd.DataFrame({"ofi": ofi, "ti": tsigned, "mid": mid[1:]}).groupby(bucket).agg(
        ofi=("ofi", "sum"), ti=("ti", "sum"), mid=("mid", "last"))
    agg["ret"] = agg["mid"].diff() / TICK          # next-bucket return in ticks
    agg["fwd"] = agg["ret"].shift(-1)

    print("=" * 74)
    print(f"  OFI ENGINE   {args.file.split('/')[-1]}   bucket={args.bucket}")
    print(f"  {len(df):,} book events -> {len(agg):,} buckets")
    print("=" * 74)

    for name, col in (("OFI", "ofi"), ("Trade-imbalance", "ti")):
        b, a, r2, t, n = ols(agg[col], agg["ret"])
        print(f"\n  {name} CONTEMPORANEOUS  (ret_t ~ {name}_t):")
        print(f"    slope {b:.3e}  R²={r2:.3f}  t={t:+.1f}   <- mechanism strength")
        b2, a2, r22, t2, n2 = ols(agg[col], agg["fwd"])
        print(f"  {name} PREDICTIVE  (ret_t+1 ~ {name}_t):")
        print(f"    slope {b2:.3e}  R²={r22:.4f}  t={t2:+.2f}   <- the edge question")
        # simple sign strategy on the predictive signal, net of cost
        sig = np.sign(agg[col].to_numpy())
        pnl = sig[:-1] * agg["fwd"].to_numpy()[:-1]      # ticks, before cost
        pnl = pnl[~np.isnan(pnl)]
        traded = pnl[sig[:-1][~np.isnan(agg["fwd"].to_numpy()[:-1])] != 0] if len(pnl) else pnl
        net = pnl - args.cost_ticks
        print(f"    sign strategy: gross {pnl.mean():+.3f} ticks/bucket  "
              f"net@{args.cost_ticks}t {net.mean():+.3f}  hit {float((pnl>0).mean())*100:.1f}%  n={len(pnl)}")

    print("\n" + "=" * 74)
    print("  Contemporaneous R² = how mechanically OFI drives price (validation).")
    print("  Predictive t / net ticks = whether it FORECASTS the next bucket (edge).")
    print("=" * 74)


if __name__ == "__main__":
    main()
