"""
Expected-move (IV ±1 sigma) levels vs NQ — the indicator's actual 'EXP sigma'.

The expected move is the options market's own forecast of the daily range:
  EM ~= ATM straddle (call + put settle at the strike nearest spot, front expiry)
  scaled to one day.  The +/-EM band around the prior close is the +-1 sigma
  expected range. We test, with NO look-ahead (today's EOD settle -> tomorrow):

  CONTAINMENT : does the next RTH range stay within close +/- EM? (~68% if 1 sigma)
  RESPECT     : when price tags +/-EM, does it close back inside (rejection)?
  SKILL       : does IV-EM predict next-day realized range better than a
                trailing-realized (ATR-style) band? -> does options add info?

    python expected_move.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

RTH_OPEN, RTH_CLOSE = 9*60+30, 16*60


def to_ny(idx):
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    return idx.tz_convert("America/New_York")


def nq_sessions():
    df = pd.read_parquet("/tmp/nq_trades_3wk.parquet")
    ny = to_ny(df.index); mins = ny.hour*60 + ny.minute
    rth = df[(mins >= RTH_OPEN) & (mins < RTH_CLOSE)].copy()
    rth["date"] = to_ny(rth.index).date
    g = rth.groupby("date")["price"]
    s = pd.DataFrame({"open": g.first(), "high": g.max(), "low": g.min(), "close": g.last()})
    s["range"] = s["high"] - s["low"]
    return s


def expected_moves(ref_close):
    defn = pd.read_parquet("/tmp/nq_opt_def_full.parquet")
    st = pd.read_parquet("/tmp/nq_opt_settle.parquet")
    st["date"] = to_ny(pd.DatetimeIndex(st["ts_event"])).date
    m = st.merge(defn, on="instrument_id", how="inner")
    m["exp"] = to_ny(pd.DatetimeIndex(m["expiration"])).date
    out = {}
    for d, day in m.groupby("date"):
        px = ref_close.get(d)
        if px is None or np.isnan(px):
            continue
        fut = day[day["exp"] > d]
        if fut.empty:
            continue
        E = fut["exp"].min()
        front = fut[fut["exp"] == E]
        strikes = front["strike_price"].dropna().unique()
        if len(strikes) < 2:
            continue
        atm = strikes[np.argmin(np.abs(strikes - px))]
        c = front[(front["instrument_class"] == "C") & (front["strike_price"] == atm)]["settle"]
        p = front[(front["instrument_class"] == "P") & (front["strike_price"] == atm)]["settle"]
        if c.empty or p.empty or np.isnan(c.iloc[0]) or np.isnan(p.iloc[0]):
            continue
        straddle = float(c.iloc[0]) + float(p.iloc[0])
        dte = max((E - d).days, 1)
        em = straddle / np.sqrt(dte)        # scale to ~1 day
        out[d] = dict(em=em, close=px)
    return out


def main():
    sess = nq_sessions()
    em = expected_moves(sess["close"].to_dict())
    dates = sorted(em)
    rows = []
    realized = sess["range"]
    atr = realized.rolling(5).mean().shift(1)        # trailing realized band (proxy)
    for i in range(len(dates) - 1):
        d, nd = dates[i], dates[i+1]
        if nd not in sess.index:
            continue
        E = em[d]["em"]; close = em[d]["close"]; s = sess.loc[nd]
        up, dn = close + E, close - E
        contained = (s["high"] <= up) and (s["low"] >= dn)
        tagged_up = s["high"] >= up; tagged_dn = s["low"] <= dn
        # reject = tagged the band but CLOSED back inside
        reject_up = tagged_up and (s["close"] < up)
        reject_dn = tagged_dn and (s["close"] > dn)
        rows.append(dict(em=E, realized=s["range"], close=close,
                         contained=contained, tagged=tagged_up or tagged_dn,
                         reject=(reject_up if tagged_up else np.nan) if tagged_up
                                 else (reject_dn if tagged_dn else np.nan),
                         atr=atr.get(nd, np.nan)))
    r = pd.DataFrame(rows)
    print("="*68)
    print(f"  EXPECTED-MOVE (IV +/-1sigma) vs NQ   ({len(r)} sessions, next-day)")
    print("="*68)
    print(f"  median 1-day expected move : {r['em'].median():.0f} pts")
    print(f"  median realized RTH range  : {r['realized'].median():.0f} pts")
    print(f"  CONTAINMENT (range within close +/-EM): {r['contained'].mean()*100:.0f}%   "
          f"(1-sigma theory ~68%)")
    tagged = r[r["tagged"]]
    rej = tagged["reject"].dropna()
    if len(rej):
        print(f"  when price TAGS the band: closes back inside (rejection) "
              f"{rej.mean()*100:.0f}%  (n={len(rej)})")
    # skill: does IV-EM predict realized range better than trailing ATR?
    rr = r.dropna(subset=["atr"])
    if len(rr) > 5:
        c_em = np.corrcoef(rr["em"], rr["realized"])[0, 1]
        c_atr = np.corrcoef(rr["atr"], rr["realized"])[0, 1]
        print(f"  predicting next-day range:  IV-EM corr {c_em:+.2f}   "
              f"trailing-range corr {c_atr:+.2f}")
        print(f"  -> options add info beyond realized vol: {'YES' if c_em > c_atr else 'no'}")
    print("="*68)
    print("  Edge if containment ~>=68% AND band gets respected (high rejection),")
    print("  or IV-EM predicts the range better than trailing realized vol.")


if __name__ == "__main__":
    main()
