"""
Anomaly sweep — an exhaustive, honest search for a retail-tradeable edge.

Tests a battery of well-documented anomalies on DECADES of daily data (free via
yfinance), each net of cost and split temporally (train older / test newer) so a
survivor must hold out-of-sample, not just curve-fit.

Strategies:
  A. OVERNIGHT-ONLY      hold close->open every day (the close->open premium)
  B. INTRADAY-ONLY       hold open->close every day (the comparison)
  C. RSI(2) MEAN-REV     Connors: long when RSI2<10, exit when RSI2>70 (long only)
  D. DOWN-DAYS REVERSAL  long after N down closes, hold H days
  E. TURN-OF-MONTH       long only in the last-1 + first-3 trading days window
  F. DAY-OF-WEEK         long only on the single best weekday (in-sample chosen)
  (BUY&HOLD baseline)

Reports CAGR, Sharpe, MaxDD, exposure, trades/yr -- full period AND out-of-sample.

    python anomaly_sweep.py --tickers SPY QQQ IWM --cost 2
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
import yfinance as yf

ANN = 252


def rsi(series, n):
    d = series.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def metrics(ret: pd.Series, cost_per_turn: float, turns: pd.Series):
    """ret: daily strategy return (decimal, 0 when flat). turns: per-day count of side changes."""
    net = ret - turns * cost_per_turn
    eq = (1 + net).cumprod()
    n = len(net)
    if n < 50 or eq.iloc[-1] <= 0:
        return dict(cagr=0, sharpe=0, maxdd=-1, expo=0, tpy=0, final=eq.iloc[-1] if n else 1)
    cagr = eq.iloc[-1] ** (ANN / n) - 1
    sd = net.std()
    sharpe = net.mean() / sd * np.sqrt(ANN) if sd > 0 else 0
    maxdd = float((eq / eq.cummax() - 1).min())
    expo = float((ret != 0).mean())
    tpy = float(turns.sum()) / n * ANN
    return dict(cagr=cagr, sharpe=sharpe, maxdd=maxdd, expo=expo, tpy=tpy, final=float(eq.iloc[-1]))


def build(df):
    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    cc = c.pct_change().fillna(0)
    overnight = (o / c.shift(1) - 1).fillna(0)
    intraday = (c / o - 1).fillna(0)
    out = {}
    z = pd.Series(0, index=df.index)

    # A. overnight-only: in position every day overnight; 2 turns/day (MOC buy, MOO sell)
    out["A_overnight"] = (overnight, pd.Series(2, index=df.index))
    # B. intraday-only
    out["B_intraday"] = (intraday, pd.Series(2, index=df.index))

    # C. RSI(2) mean reversion (long only), act on prior-day signal -> capture cc
    r2 = rsi(c, 2)
    pos = np.zeros(len(c)); state = 0
    r2v = r2.to_numpy()
    for i in range(1, len(c)):
        if state == 0 and r2v[i-1] < 10:
            state = 1
        elif state == 1 and r2v[i-1] > 70:
            state = 0
        pos[i] = state
    pos = pd.Series(pos, index=df.index)
    out["C_rsi2"] = (pos.shift(0) * cc, pos.diff().abs().fillna(0))

    # D. down-days reversal: long after 2 consecutive down closes, hold 3 days
    down = (c < c.shift(1)).astype(int)
    sig = ((down + down.shift(1)) >= 2).astype(int)        # 2 down days
    pos = sig.replace(0, np.nan).ffill(limit=3).fillna(0).clip(upper=1)
    pos = pos.shift(1).fillna(0)
    out["D_downdays"] = (pos * cc, pos.diff().abs().fillna(0))

    # E. turn-of-month: long only last-1 + first-3 trading days of month
    tom = pd.Series(0, index=df.index)
    grp = df.groupby([df.index.year, df.index.month]).indices
    pos_idx = np.zeros(len(c))
    for _, idxs in grp.items():
        for k in idxs[:3]:
            pos_idx[k] = 1
        pos_idx[idxs[-1]] = 1
    tompos = pd.Series(pos_idx, index=df.index).shift(1).fillna(0)
    out["E_turnofmonth"] = (tompos * cc, tompos.diff().abs().fillna(0))

    # G. trend timing: hold only when above the 200-day SMA
    sma200 = c.rolling(200).mean()
    sma5 = c.rolling(5).mean()
    trend = (c > sma200)
    gpos = trend.astype(float).shift(1).fillna(0)
    out["G_trend200"] = (gpos * cc, gpos.diff().abs().fillna(0))

    # H. RSI(2) + trend filter (Connors' canonical rule): buy dips only in uptrends
    r2v, trv, s5v, cv = r2.to_numpy(), trend.to_numpy(), sma5.to_numpy(), c.to_numpy()
    pos = np.zeros(len(c)); state = 0
    for i in range(1, len(c)):
        if state == 0 and trv[i-1] and r2v[i-1] < 10:
            state = 1
        elif state == 1 and (r2v[i-1] > 70 or cv[i-1] > s5v[i-1]):
            state = 0
        pos[i] = state
    hpos = pd.Series(pos, index=df.index)
    out["H_rsi2_trend"] = (hpos * cc, hpos.diff().abs().fillna(0))

    # F. day-of-week: long only on weekday with best in-sample mean (chosen on train)
    out["_dow_cc"] = cc
    out["_buyhold"] = (cc, (pd.Series(0, index=df.index)))   # ~no turns
    return out


def run_ticker(tk, cost_bps, split=0.6):
    df = yf.download(tk, period="max", interval="1d", progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna()
    if len(df) < 1000:
        print(f"  {tk}: insufficient history"); return
    strat = build(df)
    cpt = cost_bps / 1e4
    cut = int(len(df) * split)

    print(f"\n{'='*92}\n  {tk}   {df.index[0]:%Y-%m-%d} -> {df.index[-1]:%Y-%m-%d}   ({len(df)} days)"
          f"   cost {cost_bps}bps/turn\n{'='*92}")
    print(f"  {'strategy':<16}{'  CAGR':>8}{'Sharpe':>8}{'MaxDD':>8}{'Expo':>7}{'Tr/yr':>7}"
          f" | {'OOS CAGR':>9}{'OOS Shrp':>9}{'OOS DD':>8}")

    items = {k: v for k, v in strat.items() if not k.startswith("_")}
    # F. day-of-week: best weekday chosen on TRAIN only (no lookahead)
    cc = strat["_dow_cc"]
    train_cc = cc.iloc[:cut]
    best_d = int(train_cc.groupby(train_cc.index.weekday).mean().idxmax())
    dpos = pd.Series((df.index.weekday == best_d).astype(float), index=df.index).shift(1).fillna(0)
    items[f"F_dow({best_d})"] = (dpos * cc, dpos.diff().abs().fillna(0))
    items["_buyhold"] = strat["_buyhold"]

    rows = []
    for name, (ret, turns) in items.items():
        full = metrics(ret, cpt, turns)
        oos = metrics(ret.iloc[cut:], cpt, turns.iloc[cut:])
        rows.append((name, full, oos))
    rows.sort(key=lambda r: r[2]["sharpe"], reverse=True)
    for name, f, o in rows:
        nm = name.replace("_buyhold", "BUY&HOLD")
        print(f"  {nm:<16}{f['cagr']*100:>7.1f}%{f['sharpe']:>8.2f}{f['maxdd']*100:>7.0f}%"
              f"{f['expo']*100:>6.0f}%{f['tpy']:>7.0f} | {o['cagr']*100:>8.1f}%{o['sharpe']:>9.2f}{o['maxdd']*100:>7.0f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="+", default=["SPY", "QQQ", "IWM"])
    ap.add_argument("--cost", type=float, default=2.0, help="bps per turn (side)")
    args = ap.parse_args()
    for tk in args.tickers:
        try:
            run_ticker(tk, args.cost)
        except Exception as e:
            print(f"  {tk}: ERROR {e}")


if __name__ == "__main__":
    main()
