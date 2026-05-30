"""
Order-Book Imbalance -> next move. The classic passive market-making signal.

Unlike OFI (which moves price contemporaneously), top-of-book size imbalance
  OBI = bid_size / (bid_size + ask_size)
is genuinely PREDICTIVE of the next mid move (Cartea-Jaimungal; Stoikov). This
tests how strong that is on real NQ data, and -- crucially -- whether the
predicted move is big enough to cross the spread (a taker edge) or only
capturable passively by earning the spread with queue priority (the MM edge).

Runs on the already-downloaded parquet -- no new Databento cost.

    python ob_imbalance.py --file /tmp/nq_mbp1_20250515.parquet
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

TICK = 0.25


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--horizons", default="10,50,200", help="forward horizons in book events")
    args = ap.parse_args()

    df = pd.read_parquet(args.file)
    bpx = df["bid_px_00"].to_numpy(float); apx = df["ask_px_00"].to_numpy(float)
    bsz = df["bid_sz_00"].to_numpy(float); asz = df["ask_sz_00"].to_numpy(float)
    ok = (bpx > 0) & (apx > 0) & (apx >= bpx) & (apx - bpx < 5) & (bsz + asz > 0)
    bpx, apx, bsz, asz = bpx[ok], apx[ok], bsz[ok], asz[ok]
    mid = (bpx + apx) / 2
    spread = float(np.median(apx - bpx)) / TICK
    obi = bsz / (bsz + asz)            # 0..1 ; >0.5 = more bid depth = upward pressure

    print("=" * 72)
    print(f"  ORDER-BOOK IMBALANCE -> NEXT MOVE   {args.file.split('/')[-1]}")
    print(f"  {len(mid):,} states   median spread {spread:.1f} tick")
    print("=" * 72)

    for H in [int(x) for x in args.horizons.split(",")]:
        fwd = (np.r_[mid[H:], [np.nan] * H] - mid) / TICK     # forward mid move (ticks)
        m = ~np.isnan(fwd)
        x = obi[m] - 0.5; y = fwd[m]
        corr = float(np.corrcoef(x, y)[0, 1])
        hit = float((np.sign(x) == np.sign(y))[y != 0].mean())
        # decile table
        q = pd.qcut(obi[m], 10, labels=False, duplicates="drop")
        dec = pd.DataFrame({"q": q, "fwd": y}).groupby("q")["fwd"].mean()
        print(f"\n  horizon = next {H} book events:")
        print(f"    corr(OBI, fwd move) = {corr:+.3f}   sign hit-rate = {hit*100:.1f}%")
        print(f"    mean fwd move by OBI decile (ticks), low->high imbalance:")
        print("      " + "  ".join(f"{v:+.2f}" for v in dec.values))
        edge_top = float(dec.values[-1]); edge_bot = float(dec.values[0])
        print(f"    most bid-heavy decile: {edge_top:+.2f} tk   most ask-heavy: {edge_bot:+.2f} tk")
        print(f"    -> predicted edge {max(abs(edge_top),abs(edge_bot)):.2f} tk vs "
              f"{spread:.1f} tk spread to cross: "
              f"{'TAKER-tradeable' if max(abs(edge_top),abs(edge_bot))>spread else 'only PASSIVE (need queue priority)'}")

    print("\n" + "=" * 72)
    print("  OBI predicts direction (corr>0, hit-rate>50%) -- but the move is a")
    print("  fraction of the spread, so it's the market-MAKER's edge (post & earn")
    print("  the spread with queue priority), not a taker signal. Pure latency game.")
    print("=" * 72)


if __name__ == "__main__":
    main()
