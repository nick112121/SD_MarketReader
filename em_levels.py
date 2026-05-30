"""
Expected-move S/R levels — daily / weekly / monthly, from the options chain.

For a reference price (prior settle), project probabilistic support/resistance
from the implied expected move at three horizons, using the ATM straddle of the
expiry nearest each horizon:

  EM(horizon) ~= ATM call + ATM put at the expiry closest to that horizon
  daily 1-sigma  = straddle(nearest ~1-day expiry), scaled to 1 trading day
  weekly 1-sigma = straddle(nearest ~5-day expiry)
  monthly 1-sigma= straddle(nearest ~21-day expiry)

Levels = ref +/- {0.5, 1.0} sigma at each horizon. These are PROBABILITY ZONES
(a 1-sigma band should contain ~68% of outcomes), not hard bounce lines.

Validated: does the daily +/-1sigma band actually contain the day's range ~68%?

    python em_levels.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from all_sessions import load_1m_all, to_ny

NY0, NY1 = 930, 1320


def chain():
    import os
    sfx = "_2y" if os.path.exists("/tmp/nq_settle_2y.parquet") else "_6m"
    defn = pd.read_parquet(f"/tmp/nq_def{sfx}.parquet")
    st = pd.read_parquet(f"/tmp/nq_settle{sfx}.parquet")
    st["date"] = to_ny(pd.DatetimeIndex(st["ts_event"])).date
    m = st.merge(defn, on="instrument_id", how="inner")
    m["exp"] = to_ny(pd.DatetimeIndex(m["expiration"])).date
    return m


def straddle(day, ref, lo_dte, hi_dte):
    """ATM straddle of the nearest expiry whose trading-DTE is in [lo,hi]."""
    exps = sorted(day["exp"].unique())
    cand = [(e, int(np.busday_count(day["d0"].iloc[0], e))) for e in exps]
    cand = [(e, dte) for e, dte in cand if lo_dte <= dte <= hi_dte]
    if not cand:
        return None, None
    e, dte = min(cand, key=lambda x: x[1])     # nearest in the bucket
    fr = day[day["exp"] == e]
    ks = fr["strike_price"].dropna().unique()
    if len(ks) < 2:
        return None, None
    atm = ks[np.argmin(np.abs(ks - ref))]
    c = fr[(fr.instrument_class == "C") & (fr.strike_price == atm)]["settle"]
    p = fr[(fr.instrument_class == "P") & (fr.strike_price == atm)]["settle"]
    if c.empty or p.empty or np.isnan(c.iloc[0]) or np.isnan(p.iloc[0]):
        return None, None
    return float(c.iloc[0]) + float(p.iloc[0]), max(dte, 1)


def build():
    b = load_1m_all()
    rth = b[(b["mit"] >= NY0) & (b["mit"] < NY1)]
    g = rth.groupby("td")
    close = g["close"].last(); high = g["high"].max(); low = g["low"].min(); openp = g["open"].first()
    m = chain()
    rows = {}
    for d, day in m.groupby("date"):
        ref = close.get(d)
        if ref is None or np.isnan(ref):
            continue
        day = day.copy(); day["d0"] = d
        sd, dte_d = straddle(day, ref, 1, 3)
        sw, _ = straddle(day, ref, 4, 10)
        sm, _ = straddle(day, ref, 18, 45)
        if sd is None:
            continue
        rows[d] = dict(ref=ref,
                       em_d=sd / np.sqrt(dte_d),     # scale nearest expiry to 1 trading day
                       em_w=sw if sw else np.nan,
                       em_m=sm if sm else np.nan)
    return b, close, high, low, openp, pd.DataFrame(rows).T


def main():
    b, close, high, low, openp, em = build()
    dates = sorted(em.index)
    cc = c1 = c05 = rng1 = n = 0
    for i in range(len(dates) - 1):
        d, nd = dates[i], dates[i+1]
        if nd not in close.index:
            continue
        emd = em.loc[d, "em_d"]; ref = em.loc[d, "ref"]
        if np.isnan(emd):
            continue
        n += 1
        c1 += abs(close[nd] - ref) <= emd                 # CLOSE within +/-1sigma (~68%)
        c05 += abs(close[nd] - ref) <= 0.5*emd            # CLOSE within +/-0.5sigma
        rng1 += (high[nd] <= ref+emd) and (low[nd] >= ref-emd)  # full range inside (stricter)
    print("="*60)
    print(f"  EXPECTED-MOVE LEVELS — validation ({n} days)")
    print("="*60)
    print(f"  next CLOSE within daily +/-1sigma : {c1/n*100:.0f}%   (theory ~68%)")
    print(f"  next CLOSE within daily +/-0.5sig : {c05/n*100:.0f}%   (theory ~38%)")
    print(f"  full range stays inside +/-1sigma : {rng1/n*100:.0f}%   (range > close move, so lower)")
    med = em.median(numeric_only=True)
    print(f"  median EM:  daily {med['em_d']:.0f}  weekly {med['em_w']:.0f}  monthly {med['em_m']:.0f} pts")

    last = dates[-1]
    r = em.loc[last]; ref = r["ref"]
    print(f"\n  TODAY'S LEVELS  (anchor = prior close {ref:.0f}, {last}):")
    ladder = [
        ("+1sigma MONTHLY", ref + r["em_m"]), ("+1sigma WEEKLY", ref + r["em_w"]),
        ("+1sigma DAILY",  ref + r["em_d"]),  ("+0.5sigma daily", ref + 0.5*r["em_d"]),
        ("ANCHOR (pclose)", ref),
        ("-0.5sigma daily", ref - 0.5*r["em_d"]), ("-1sigma DAILY", ref - r["em_d"]),
        ("-1sigma WEEKLY", ref - r["em_w"]), ("-1sigma MONTHLY", ref - r["em_m"]),
    ]
    for name, lvl in ladder:
        if not np.isnan(lvl):
            print(f"    {name:<18} {lvl:>10.1f}")
    print("="*60)
    print("  Use as PROBABILITY ZONES: ~68% of days stay inside daily +/-1sigma;")
    print("  the +/-1sigma daily edges are where a move is 'extended' (target/fade")
    print("  zone), weekly/monthly bands are the bigger S/R shelves.")


if __name__ == "__main__":
    main()
