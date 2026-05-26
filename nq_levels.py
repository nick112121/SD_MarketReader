"""
NQ key levels — calculate the reference levels and TEST which ones price reacts at.

For each trading day we compute levels known in advance (no look-ahead):
  PDH/PDL/PDC : prior RTH day high / low / close
  ONH/ONL     : overnight (18:00 ET -> 09:30 ET) high / low
  EMU/EMD     : prior close +/- expected move (ATM straddle, options)
  PWH/PWL     : prior week high / low

Then, during the NY session, for every time price first TOUCHES a level we ask:
did it REACT (reverse REACT_PTS back into the range before breaking REACT_PTS
through) or break? The hold-rate per level type vs a random-price baseline tells
us which levels actually matter to NQ.

    python nq_levels.py            # ranking + today's levels
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from all_sessions import load_1m_all, daily_em, to_ny

REACT_PTS = 20.0      # points that counts as a real reaction / break
FWD_MIN = 60          # minutes after the touch to resolve react-vs-break
NY0, NY1 = 930, 1320  # minute-in-TD for the NY session


def level_sets(b, em_rank_series_unused):
    """Per trading day -> dict of named levels (computed from PRIOR info)."""
    rth = b[(b["mit"] >= NY0) & (b["mit"] < NY1)]
    g = rth.groupby("td")
    pdh = g["high"].max(); pdl = g["low"].min(); pdc = g["close"].last()
    on = b[(b["mit"] >= 0) & (b["mit"] < NY0)].groupby("td")
    onh = on["high"].max(); onl = on["low"].min()
    tds = sorted(rth["td"].unique())
    # prior week H/L: rolling 5-prior-session
    pdh_s = pdh.reindex(tds); pdl_s = pdl.reindex(tds)
    pwh = pdh_s.rolling(5).max().shift(1); pwl = pdl_s.rolling(5).min().shift(1)
    lv = {}
    pdh_p, pdl_p, pdc_p = pdh.shift(1), pdl.shift(1), pdc.shift(1)
    for i, td in enumerate(tds):
        d = {}
        if td in pdh_p and not np.isnan(pdh_p.get(td, np.nan)):
            d["PDH"], d["PDL"], d["PDC"] = pdh_p[td], pdl_p[td], pdc_p[td]
        if td in onh.index:
            d["ONH"], d["ONL"] = onh[td], onl[td]
        if not np.isnan(pwh.get(td, np.nan)):
            d["PWH"], d["PWL"] = pwh[td], pwl[td]
        lv[td] = d
    return lv


def react_test(b, lv, em):
    """For each level touched in the NY session, did it hold (react) or break?"""
    hits = {k: [0, 0] for k in ["PDH", "PDL", "PDC", "ONH", "ONL", "PWH", "PWL", "EMU", "EMD", "RANDOM"]}
    rng = np.random.default_rng(0)
    for td, g in b[(b["mit"] >= NY0) & (b["mit"] < NY1)].groupby("td"):
        g = g.sort_values("mit")
        H, L, C = g["high"].to_numpy(float), g["low"].to_numpy(float), g["close"].to_numpy(float)
        if len(C) < 30:
            continue
        levels = dict(lv.get(td, {}))
        # expected-move band off prior close
        emv = em.asof(pd.Timestamp(td) - pd.Timedelta(days=1)) if len(em) else np.nan
        if "PDC" in levels and not np.isnan(emv):
            levels["EMU"] = levels["PDC"] + emv; levels["EMD"] = levels["PDC"] - emv
        if "ONL" in levels and "ONH" in levels:
            levels["RANDOM"] = float(rng.uniform(levels["ONL"], levels["ONH"]))
        for name, Lv in levels.items():
            if Lv is None or np.isnan(Lv):
                continue
            ti = None
            for i in range(len(C)):
                if L[i] <= Lv <= H[i]:
                    ti = i; break
            if ti is None:
                continue
            # resolution: after the touch, which threshold is hit first
            outcome = None
            for j in range(ti + 1, min(ti + FWD_MIN, len(C))):
                up = H[j] - Lv >= REACT_PTS
                dn = Lv - L[j] >= REACT_PTS
                if up and not dn: outcome = "up"; break
                if dn and not up: outcome = "down"; break
                if up and dn: outcome = "both"; break
            if outcome in (None, "both"):
                continue
            # approached from below (price under level) -> resistance; from above -> support
            approach_below = C[ti] <= Lv
            reacted = (outcome == "down") if approach_below else (outcome == "up")
            hits[name][0] += int(reacted); hits[name][1] += 1
    return hits


def main():
    b = load_1m_all()
    em = daily_em()
    lv = level_sets(b, em)
    hits = react_test(b, lv, em)
    print("="*64)
    print(f"  NQ LEVEL REACTION TEST  (react = reverse {REACT_PTS:.0f}pts before breaking)")
    print("="*64)
    print(f"  {'level':<8}{'touches':>9}{'react%':>9}")
    order = sorted([k for k in hits if hits[k][1] >= 20],
                   key=lambda k: -hits[k][0]/max(hits[k][1], 1))
    for k in order:
        r, n = hits[k]
        print(f"  {k:<8}{n:>9}{r/n*100:>8.0f}%")
    base = hits["RANDOM"]
    print(f"  (RANDOM baseline = {base[0]/max(base[1],1)*100:.0f}% — beat this to matter)")

    # today's levels
    tds = sorted(lv)
    last = tds[-1]
    print("\n  TODAY'S LEVELS (most recent day in data, {}):".format(last))
    d = dict(lv[last])
    emv = em.asof(pd.Timestamp(last) - pd.Timedelta(days=1)) if len(em) else np.nan
    if "PDC" in d and not np.isnan(emv):
        d["EMU"] = d["PDC"] + emv; d["EMD"] = d["PDC"] - emv
        print(f"    expected move: +/- {emv:.0f} pts")
    for k in ["PWH", "PDH", "ONH", "EMU", "PDC", "EMD", "ONL", "PDL", "PWL"]:
        if k in d and not np.isnan(d[k]):
            print(f"    {k:<5} {d[k]:>10.1f}")
    print("="*64)


if __name__ == "__main__":
    main()
