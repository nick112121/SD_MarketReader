"""
Goldbach Time — does this style of trading actually work?

A self-contained study (NOT wired into the dashboard) that takes Ajay Shanbhag's
"Goldbach Time / Algo Path" method (built on HopiPlaka's Power-of-Three work) and
tests its claims on real 1-minute data instead of taking them on faith.

The method makes three falsifiable claims. We test each one:

  CLAIM 1  Swings print on "GB minutes" — the minute-of-hour marks derived from
           the Goldbach prime pairs of 100, plus their consequent-encroachment
           (CE) midpoints:  {3,7,11,14,17,23,29,35,41,44,47,50,53,56,59}.
           TEST: do swing pivots land on those minutes more often than the ~25%
           you'd expect by chance? (binomial z-test)

  CLAIM 2  The "Algo Path" is a map: a swing at one GB minute tells you the next
           GB minute the market will swing at (his "Google Maps" edge).
           TEST: does his fixed path predict the next swing's minute better than
           a uniform random guess, or than the empirically most-likely next minute?

  CLAIM 3  Knowing this lets you trade every swing profitably.
           TEST: enter at confirmed swings (low->long, high->short) and exit after
           a fixed horizon. Compare expectancy of swings ON a GB minute vs swings
           NOT on a GB minute, net of costs. If GB timing is an edge, GB > non-GB.

No look-ahead: a fractal pivot at bar i is only known at bar i+K, so entries are
taken at i+K (the confirmation bar), never at the pivot itself.

Usage:
    python goldbach_test.py --ticker QQQ --days 30
    python goldbach_test.py --ticker NQ=F --days 30 --horizons 10,20,30
    python goldbach_test.py --csv mydata.csv          # offline (datetime,Open,High,Low,Close)
    python goldbach_test.py --synthetic               # random-walk sanity check (expect NO edge)
"""

from __future__ import annotations

import argparse
import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

# ── Goldbach minute marks ────────────────────────────────────────────────
GB_NUMS = [3, 11, 17, 29, 41, 47, 53, 59, 71, 83, 89, 97]          # prime pairs of 100
CE_NUMS = [7, 14, 23, 35, 44, 50, 56, 65, 77, 86, 93]              # midpoints (consequent encroachment)
# Only marks that fit inside one hour (<= 59) can appear as a minute-of-hour.
GB_MIN  = sorted({n for n in GB_NUMS + CE_NUMS if n <= 59})
GB_BASELINE = len(GB_MIN) / 60.0                                   # ~0.25

# His fixed forward Algo Path (next GB minute a swing should travel to).
# Read forward on the clock; 71<->29 and premium<->discount swaps already applied.
ALGO_PATH = {
    41: 3, 3: 17, 17: 29, 29: 47, 47: 11, 11: 17,
    53: 11, 59: 17, 35: 47, 23: 35, 14: 17, 7: 17,
    44: 3, 50: 11, 56: 11,
}


# ── small stats helpers (no scipy) ───────────────────────────────────────
def norm_sf(z: float) -> float:
    """One-sided upper-tail p-value of the standard normal."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def binom_z(hits: int, n: int, p: float) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    mu = n * p
    sd = math.sqrt(n * p * (1 - p))
    z = (hits - mu) / sd if sd > 0 else 0.0
    return z, norm_sf(z)


def mean_t(x: np.ndarray) -> tuple[float, float, float]:
    """mean, t-stat, two-sided p (normal approx) for H0: mean == 0."""
    n = len(x)
    if n < 2:
        return (float(np.mean(x)) if n else 0.0), 0.0, 1.0
    m = float(np.mean(x))
    se = float(np.std(x, ddof=1)) / math.sqrt(n)
    t = m / se if se > 0 else 0.0
    p = 2.0 * norm_sf(abs(t))
    return m, t, p


# ── data ─────────────────────────────────────────────────────────────────
def fetch_1m(ticker: str, days: int) -> pd.DataFrame:
    """Fetch up to `days` of 1m bars (yfinance caps 1m at ~30d, 7d per call)."""
    import yfinance as yf
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=min(days, 30))
    frames, cur, win = [], start, timedelta(days=7)
    while cur < end:
        nxt = min(cur + win, end)
        df = yf.download(ticker, start=cur, end=nxt, interval="1m",
                         progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if len(df):
            frames.append(df)
        cur = nxt
    if not frames:
        raise RuntimeError(f"No 1m data returned for {ticker}")
    d = pd.concat(frames).sort_index()
    d = d[~d.index.duplicated(keep="first")].dropna()
    return d


def load_csv(path: str) -> pd.DataFrame:
    d = pd.read_csv(path)
    tcol = next(c for c in d.columns if c.lower() in ("datetime", "date", "timestamp", "time"))
    d[tcol] = pd.to_datetime(d[tcol], utc=True)
    d = d.set_index(tcol).sort_index()
    d.columns = [c.capitalize() for c in d.columns]
    return d[["Open", "High", "Low", "Close"]].apply(pd.to_numeric, errors="coerce").dropna()


def load_json(path: str) -> pd.DataFrame:
    """Tiingo IEX JSON: list of {date, open, high, low, close, volume}. Use 1min freq."""
    import json as _json
    with open(path) as f:
        raw = _json.load(f)
    d = pd.DataFrame(raw)
    if "date" not in d.columns:
        raise ValueError("JSON has no 'date' field (expected Tiingo IEX format)")
    d["date"] = pd.to_datetime(d["date"], utc=True)
    d = d.set_index("date").sort_index()
    d = d.rename(columns={c: c.capitalize() for c in d.columns})
    return d[["Open", "High", "Low", "Close"]].apply(pd.to_numeric, errors="coerce").dropna()


def synthetic(days: int = 30, seed: int = 7) -> pd.DataFrame:
    """Random-walk 1m bars over RTH. Used to confirm the tool finds NO edge in noise."""
    rng = np.random.default_rng(seed)
    idx = []
    day = datetime(2024, 1, 1, tzinfo=timezone.utc)
    added = 0
    while added < days:
        if day.weekday() < 5:
            base = day.replace(hour=14, minute=30)  # ~09:30 ET in UTC
            idx += [base + timedelta(minutes=m) for m in range(390)]
            added += 1
        day += timedelta(days=1)
    idx = pd.DatetimeIndex(idx)
    steps = rng.normal(0, 0.5, len(idx))
    close = 400 + np.cumsum(steps)
    high = close + np.abs(rng.normal(0, 0.3, len(idx)))
    low = close - np.abs(rng.normal(0, 0.3, len(idx)))
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close}, index=idx)


def to_ny_minutes(idx: pd.DatetimeIndex) -> np.ndarray:
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    local = idx.tz_convert("America/New_York")
    return np.asarray(local.minute)


# ── swing detection ────────────────────────────────────────────────────────
def find_swings(high: np.ndarray, low: np.ndarray, k: int) -> list[tuple[int, str]]:
    """Fractal pivots: bar i is a swing high if it's the max of [i-k, i+k]."""
    swings = []
    for i in range(k, len(high) - k):
        if high[i] == high[i - k:i + k + 1].max():
            swings.append((i, "H"))
        elif low[i] == low[i - k:i + k + 1].min():
            swings.append((i, "L"))
    return swings


# ── the three tests ─────────────────────────────────────────────────────
def test_clustering(minutes_at_swings: np.ndarray) -> dict:
    n = len(minutes_at_swings)
    exact = int(np.isin(minutes_at_swings, list(GB_MIN)).sum())
    tol1_set = {(m + d) % 60 for m in GB_MIN for d in (-1, 0, 1)}
    tol1 = int(np.isin(minutes_at_swings, list(tol1_set)).sum())
    z_ex, p_ex = binom_z(exact, n, GB_BASELINE)
    z_t1, p_t1 = binom_z(tol1, n, len(tol1_set) / 60.0)
    counts = np.bincount(minutes_at_swings, minlength=60)
    top = sorted(range(60), key=lambda m: counts[m], reverse=True)[:8]
    return {
        "n": n,
        "exact_rate": exact / n if n else 0, "exact_base": GB_BASELINE,
        "exact_z": z_ex, "exact_p": p_ex,
        "tol1_rate": tol1 / n if n else 0, "tol1_base": len(tol1_set) / 60.0,
        "tol1_z": z_t1, "tol1_p": p_t1,
        "counts": counts, "top": top,
    }


def test_algo_path(swing_minutes: list[int]) -> dict:
    """Does his fixed path predict the next swing's minute better than chance?

    The trap: consecutive swings sit a roughly fixed GAP apart, so ANY path
    that encodes "+gap" beats a uniform 1/15 baseline — even on random data.
    So the honest null is the BEST generic fixed-gap predictor ("next swing =
    this minute + G", G chosen in-sample). That baseline is Goldbach-free and
    overfit in its favour, so if his specific path still beats it, the edge is
    real and not just swing spacing.
    """
    gb_seq = [m for m in swing_minutes if m in GB_MIN]
    pairs = [(gb_seq[i], gb_seq[i + 1]) for i in range(len(gb_seq) - 1)]
    if not pairs:
        return {"pairs": 0}
    pred = [(a, b) for a, b in pairs if a in ALGO_PATH]          # pairs his path forecasts
    m = len(pred)
    if m == 0:
        return {"pairs": len(pairs), "pred": 0}
    path_hits = sum(ALGO_PATH[a] == b for a, b in pred)
    path_acc = path_hits / m

    # best single fixed gap, evaluated on the same predicted pairs
    best_g, gap_acc = 0, 0.0
    for G in range(1, 60):
        acc = float(np.mean([((a + G) % 60 == b) for a, b in pred]))
        if acc > gap_acc:
            best_g, gap_acc = G, acc

    p0 = max(gap_acc, 1e-9)
    z = (path_hits - m * p0) / math.sqrt(m * p0 * (1 - p0))
    return {
        "pairs": len(pairs), "pred": m,
        "path_acc": path_acc,
        "gap_acc": gap_acc, "best_gap": best_g,         # Goldbach-free baseline
        "uniform": 1.0 / len(GB_MIN),
        "z": z, "p": norm_sf(z),
    }


def backtest(close: np.ndarray, minutes: np.ndarray, swings: list[tuple[int, str]],
             k: int, horizons: list[int], cost_bps: float) -> dict:
    """Enter at i+k (confirmation), exit after H minutes. low->long, high->short.
    Split results by whether the swing's minute is a GB minute."""
    out = {}
    for H in horizons:
        gb_ret, non_ret = [], []
        for i, typ in swings:
            e = i + k                 # entry bar (pivot known only now)
            x = e + H                 # exit bar
            if x >= len(close):
                continue
            entry, exit_ = close[e], close[x]
            r = (exit_ - entry) / entry if typ == "L" else (entry - exit_) / entry
            r -= 2 * cost_bps / 1e4   # round-trip cost
            (gb_ret if minutes[i] in GB_MIN else non_ret).append(r * 1e4)  # bps
        gb_ret, non_ret = np.array(gb_ret), np.array(non_ret)
        gm, gt, gp = mean_t(gb_ret)
        nm, nt, npv = mean_t(non_ret)
        out[H] = {
            "gb_n": len(gb_ret), "gb_mean": gm, "gb_t": gt, "gb_p": gp,
            "gb_win": float((gb_ret > 0).mean()) if len(gb_ret) else 0,
            "non_n": len(non_ret), "non_mean": nm,
            "non_win": float((non_ret > 0).mean()) if len(non_ret) else 0,
            "diff": gm - nm,
        }
    return out


# ── report ────────────────────────────────────────────────────────────────
def bar(count: int, mx: int, width: int = 40) -> str:
    return "#" * int(width * count / mx) if mx else ""


def run(df: pd.DataFrame, k: int, horizons: list[int], cost_bps: float, label: str):
    high = df["High"].to_numpy(float)
    low = df["Low"].to_numpy(float)
    close = df["Close"].to_numpy(float)
    minutes = to_ny_minutes(df.index)
    swings = find_swings(high, low, k)
    sw_minutes = [int(minutes[i]) for i, _ in swings]

    print("=" * 70)
    print(f"  GOLDBACH TIME — DOES IT WORK?   [{label}]")
    print("=" * 70)
    print(f"  bars: {len(df):,}   range: {df.index[0]:%Y-%m-%d} -> {df.index[-1]:%Y-%m-%d}")
    print(f"  swings detected (fractal k={k}): {len(swings):,}")
    print(f"  GB minutes ({len(GB_MIN)}/60 = {GB_BASELINE:.0%} of clock): {GB_MIN}")

    # CLAIM 1
    c = test_clustering(np.array(sw_minutes))
    print("\n" + "-" * 70)
    print("  CLAIM 1 — swings cluster on GB minutes")
    print("-" * 70)
    print(f"  exact match : {c['exact_rate']:.1%}  vs {c['exact_base']:.1%} baseline"
          f"   z={c['exact_z']:+.2f}  p={c['exact_p']:.3f}")
    print(f"  +/-1 min    : {c['tol1_rate']:.1%}  vs {c['tol1_base']:.1%} baseline"
          f"   z={c['tol1_z']:+.2f}  p={c['tol1_p']:.3f}")
    verdict1 = c["exact_z"] > 2 and c["exact_p"] < 0.05
    print(f"  -> {'EDGE: swings favour GB minutes' if verdict1 else 'NO real edge over chance'}")
    mx = int(c["counts"].max())
    print("  swing count by minute-of-hour (★ = GB minute):")
    for m in range(60):
        star = "★" if m in GB_MIN else " "
        print(f"   {m:02d}{star}|{bar(int(c['counts'][m]), mx)} {int(c['counts'][m])}")

    # CLAIM 2
    a = test_algo_path(sw_minutes)
    print("-" * 70)
    print("  CLAIM 2 — the Algo Path predicts the NEXT swing minute")
    print("-" * 70)
    if a.get("pred"):
        print(f"  GB-swing pairs: {a['pairs']:,}   (his path forecasts {a['pred']:,} of them)")
        print(f"  his fixed path accuracy : {a['path_acc']:.1%}")
        print(f"  best fixed-gap baseline : {a['gap_acc']:.1%}  (gap=+{a['best_gap']}m, Goldbach-free)")
        print(f"  uniform 1/15 guess      : {a['uniform']:.1%}")
        print(f"  path vs gap baseline    : z={a['z']:+.2f}  p={a['p']:.3f}")
        verdict2 = a["z"] > 2 and a["p"] < 0.05
        print(f"  -> {'PATH beats generic spacing' if verdict2 else 'PATH is just swing spacing, not Goldbach'}")
    else:
        verdict2 = False
        print("  not enough GB swings to test")

    # CLAIM 3
    bt = backtest(close, minutes, swings, k, horizons, cost_bps)
    print("-" * 70)
    print(f"  CLAIM 3 — tradeable edge (cost {cost_bps:.1f} bps/side)")
    print("  swing low->long, high->short; GB-timed entries vs non-GB entries")
    print("-" * 70)
    print(f"  {'horizon':>8} | {'GB n':>6} {'GB bps':>8} {'GB win%':>7} {'t':>6} | "
          f"{'non n':>6} {'non bps':>8} {'non win%':>8} | {'GB-non':>7}")
    verdict3 = False
    for H, r in bt.items():
        if r["gb_mean"] > 0 and r["diff"] > 0 and r["gb_p"] < 0.05:
            verdict3 = True
        print(f"  {H:>6}m  | {r['gb_n']:>6} {r['gb_mean']:>+8.2f} {r['gb_win']*100:>6.1f}% "
              f"{r['gb_t']:>+6.2f} | {r['non_n']:>6} {r['non_mean']:>+8.2f} "
              f"{r['non_win']*100:>7.1f}% | {r['diff']:>+7.2f}")
    print(f"  -> {'GB-timed entries show an edge' if verdict3 else 'no significant GB-timing edge'}")

    # VERDICT
    print("\n" + "=" * 70)
    score = sum([verdict1, verdict2, verdict3])
    print(f"  VERDICT: {score}/3 claims hold up on this data")
    print(f"    [{'x' if verdict1 else ' '}] swings cluster on GB minutes")
    print(f"    [{'x' if verdict2 else ' '}] algo path predicts next swing")
    print(f"    [{'x' if verdict3 else ' '}] GB timing produces a tradeable edge")
    if score == 0:
        print("  Reads as numerology on this sample — no measurable edge.")
    elif score < 3:
        print("  Partial: something is there, but not the full 'every swing' claim.")
    else:
        print("  All three hold — worth a deeper, larger-sample study.")
    print("=" * 70)


def main():
    ap = argparse.ArgumentParser(description="Test the Goldbach Time trading method on real data.")
    ap.add_argument("--ticker", default="QQQ", help="yfinance ticker (default QQQ)")
    ap.add_argument("--days", type=int, default=30, help="days of 1m history (<=30)")
    ap.add_argument("--k", type=int, default=3, help="fractal half-width for swings")
    ap.add_argument("--horizons", default="10,20,30", help="exit horizons in minutes, comma-sep")
    ap.add_argument("--cost", type=float, default=0.5, help="cost per side in bps")
    ap.add_argument("--csv", help="load 1m OHLC from CSV instead of yfinance")
    ap.add_argument("--json", help="load 1m OHLC from a Tiingo IEX JSON file")
    ap.add_argument("--synthetic", action="store_true", help="random-walk sanity check (expect no edge)")
    args = ap.parse_args()
    horizons = [int(h) for h in args.horizons.split(",")]

    if args.synthetic:
        df, label = synthetic(args.days), "SYNTHETIC random walk"
    elif args.json:
        df, label = load_json(args.json), f"JSON {args.json}"
    elif args.csv:
        df, label = load_csv(args.csv), f"CSV {args.csv}"
    else:
        try:
            df, label = fetch_1m(args.ticker, args.days), f"{args.ticker} 1m"
        except Exception as exc:
            print(f"[!] Could not fetch {args.ticker}: {exc}")
            print("    Run locally with internet, pass --csv, or try --synthetic.")
            return

    if len(df) < 200:
        print(f"[!] Only {len(df)} bars — need more data for a meaningful test.")
        return
    run(df, args.k, horizons, args.cost, label)


if __name__ == "__main__":
    main()
