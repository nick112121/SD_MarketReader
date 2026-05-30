"""
4-Market Expected Move — anchored at the 9:30 ET RTH open, frozen all day.

Page sections (top to bottom, in order of the trading day):

  PRE-MARKET    cash index implied opens (SPX/NDX/RUT/DJI) from overnight
                US futures moves, gap size as fraction of today's daily EM,
                plus global indices (FTSE/DAX/Nikkei/Hang Seng).
  INTRADAY EM   ES/NQ/YM/RTY ranked by σD from the 9:30 anchor, daily S/R
                levels, paste-able daily/weekly/monthly EM numbers.
  OPEN RANGE    first-30-min (9:30-10:00 ET) range vs daily EM. > 40% of
                EM in the first 30 min → trend day likely; < 20% → chop.
  GAMMA WALLS   for SPY and QQQ, the call-wall (max call OI strike) and
                put-wall (max put OI strike near spot) on the nearest
                option expiry — the dealer-positioning levels.

The 9:30-anchor and EM are frozen at the open and held all day (matches
impliedopen.com's convention). Implied vol indices drive EM where Yahoo has
them (VIX/VXN/VXD); RTY falls back to realized vol since ^RVX is
unavailable. Free delayed data (~15 min) — positioning, not execution.

Local:   uvicorn four_em:app --reload
Deploy:  uvicorn em_web:app (em_web re-exports this module's app)
"""

from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI(title="4-Market Expected Move")

ET = ZoneInfo("America/New_York")

# code, futures, vol index, cash index, friendly name
MARKETS = [
    ("ES",  "ES=F",  "^VIX", "^GSPC", "S&P 500"),
    ("NQ",  "NQ=F",  "^VXN", "^NDX",  "Nasdaq 100"),
    ("YM",  "YM=F",  "^VXD", "^DJI",  "Dow 30"),
    ("RTY", "RTY=F", "^RVX", "^RUT",  "Russell 2000"),
]
GLOBAL_IDX = [
    ("FTSE", "^FTSE",  "FTSE 100"),
    ("DAX",  "^GDAXI", "DAX"),
    ("N225", "^N225",  "Nikkei 225"),
    ("HSI",  "^HSI",   "Hang Seng"),
]
GAMMA_TICKERS = ["SPY", "QQQ"]

# ETF proxy + cash↔futures ratio used to convert the ETF's listed weekly
# ATM straddle into a futures-equivalent 1-σ expected move.
WEEKLY_RANGE_MARKETS = [
    ("ES",  "ES=F",  "SPY", "S&P 500"),
    ("NQ",  "NQ=F",  "QQQ", "Nasdaq 100"),
    ("YM",  "YM=F",  "DIA", "Dow 30"),
    ("RTY", "RTY=F", "IWM", "Russell 2000"),
    ("GC",  "GC=F",  "GLD", "Gold"),
]

# Individual tech leaders — stocks are their own underlying, so the weekly
# 1-σ EM is just (their own ATM straddle) × √(π/2). No ETF proxy needed.
TECH_STOCKS = [
    ("MSFT", "Microsoft"),
    ("AAPL", "Apple"),
    ("NVDA", "Nvidia"),
    ("GOOG", "Alphabet"),
    ("AMZN", "Amazon"),
    ("META", "Meta"),
    ("TSLA", "Tesla"),
    ("AVGO", "Broadcom"),
]
SECTOR_ETFS = [
    ("XLF", "Financials"),
    ("XLE", "Energy"),
    ("XLV", "Healthcare"),
    ("XLK", "Technology"),
    ("SMH", "Semiconductors"),
]

import math
# ATM straddle × √(π/2) = 1-σ expected move. This is the convention every
# major EM tracker (impliedopen etc.) uses, and the factor cleanly converts
# the straddle price into the theoretical 1-σ band.
SQRT_PI_2 = math.sqrt(math.pi / 2)


# ── helpers ────────────────────────────────────────────────────────────────

def _to_et(idx):
    if idx.tz is None:
        return idx.tz_localize("UTC").tz_convert(ET)
    return idx.tz_convert(ET)


def _find_930_bar(intra, target_date):
    if intra is None or intra.empty:
        return None
    idx_et = _to_et(intra.index)
    mask = (idx_et.hour == 9) & (idx_et.minute == 30) & (idx_et.date == target_date)
    matched = intra[mask]
    if matched.empty:
        return None
    for _, row in matched.iterrows():
        if pd.notna(row.get("Open", None)):
            return row
    return None


def _most_recent_930(intra):
    if intra is None or intra.empty:
        return None, None
    idx_et = _to_et(intra.index)
    mask = (idx_et.hour == 9) & (idx_et.minute == 30)
    matched = intra[mask]
    if matched.empty:
        return None, None
    matched_et_index = _to_et(matched.index)
    for i in range(len(matched) - 1, -1, -1):
        row = matched.iloc[i]
        if pd.notna(row.get("Open", None)):
            return row, matched_et_index[i].date()
    return None, None


def _status(sd, sw):
    """Level-specific position vs the daily EM bands (R1/R2/S1/S2/etc.)."""
    a = abs(sd)
    side = "R" if sd >= 0 else "S"
    if a < 0.4:    base = f"inside — between open and {side}1"
    elif a < 0.6:  base = f"AT {side}1 (0.5 EM)"
    elif a < 0.9:  base = f"between {side}1 and {side}2"
    elif a < 1.15: base = f"AT {side}2 (1 EM) — daily EM"
    elif a < 1.4:  base = f"ABOVE {side}2 — past daily EM"
    elif a < 1.6:  base = f"AT {side}3 (1.5 EM)"
    elif a < 1.9:  base = f"ABOVE {side}3 — well past 1.5 EM"
    elif a < 2.1:  base = f"AT {side}4 (2 EM) — daily extreme"
    else:          base = "OUTLIER — past 2 EM"
    if a >= 1.0:
        aw = abs(sw)
        if aw >= 1.0:   base += " · AT weekly EM"
        elif aw >= 0.7: base += " · nearing weekly EM"
        else:           base += " · heading to weekly"
    return base


def _gap_class(frac):
    a = abs(frac)
    if a < 0.2: return ("small",         "#888")
    if a < 0.5: return ("medium",        "#ffcc44")
    if a < 1.0: return ("LARGE",         "#ff8c44")
    return        ("OUTLIER (>1σ)", "#ff4466")


# ── section computers ─────────────────────────────────────────────────────


def _next_friday_str(now_et: pd.Timestamp) -> str:
    """Next Friday's date as YYYY-MM-DD (today if today IS Friday before the
    cash close, else the following Friday)."""
    today = now_et.date()
    days_ahead = (4 - today.weekday()) % 7
    if days_ahead == 0 and now_et.hour >= 16:
        days_ahead = 7
    return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")


def _atm_straddle_mid(etf: str, target_str: str, cur_etf: float):
    """ATM straddle price (call mid + put mid) at the option expiry closest
    to target_str. Falls back to lastPrice if bid/ask aren't quoted."""
    t = yf.Ticker(etf)
    exps = t.options
    if not exps:
        return None, None
    if target_str in exps:
        exp = target_str
    else:
        from datetime import datetime as _dt
        target_dt = _dt.strptime(target_str, "%Y-%m-%d")
        exp = min(exps, key=lambda e: abs((_dt.strptime(e, "%Y-%m-%d") - target_dt).days))
    try:
        oc = t.option_chain(exp)
    except Exception:
        return None, exp
    if oc.calls.empty or oc.puts.empty:
        return None, exp
    c = oc.calls.iloc[(oc.calls["strike"] - cur_etf).abs().idxmin()]
    p = oc.puts.iloc[(oc.puts["strike"] - cur_etf).abs().idxmin()]

    def _mid(row):
        b = float(row.get("bid", 0) or 0)
        a = float(row.get("ask", 0) or 0)
        if b > 0 and a > 0:
            return (b + a) / 2
        return float(row.get("lastPrice", 0) or 0)

    return _mid(c) + _mid(p), exp


def compute_weekly_range(fut_intra) -> dict:
    """Weekly expected range for each market (incl. GC), anchored at the
    current futures price and sized by the 1-σ weekly move derived from the
    ETF proxy's ATM straddle (× √(π/2)). Returns the rows for the page and
    a {code: emW} map the intraday section can use for σW math."""
    now_et = pd.Timestamp.now(tz=ET)
    target = _next_friday_str(now_et)
    rows = []
    em_map: dict[str, int] = {}
    for code, fsym, etf, name in WEEKLY_RANGE_MARKETS:
        try:
            # Current futures price — from the cached intraday set if present,
            # else a quick daily pull for GC which isn't in the main MARKETS.
            if fsym in fut_intra.columns.get_level_values(0):
                cur_s = fut_intra[fsym]["Close"].dropna()
                cur_fut = float(cur_s.iloc[-1]) if len(cur_s) else None
            else:
                d = yf.download(fsym, period="5d", interval="1d",
                                progress=False, auto_adjust=True)
                v = d["Close"].iloc[-1]
                cur_fut = float(v.item() if hasattr(v, "item") else v)
            t = yf.Ticker(etf)
            try:
                cur_etf = float(t.fast_info.get("lastPrice") or
                                t.fast_info.get("regularMarketPrice"))
            except Exception:
                cur_etf = float(t.history(period="1d")["Close"].iloc[-1])
            if not cur_fut or cur_etf <= 0:
                rows.append({"code": code, "name": name, "error": "no price"})
                continue
            straddle, exp = _atm_straddle_mid(etf, target, cur_etf)
            if straddle is None or straddle <= 0:
                rows.append({"code": code, "name": name, "error": "no option chain"})
                continue
            ratio = cur_fut / cur_etf
            em_fut = straddle * SQRT_PI_2 * ratio  # 1-σ in futures terms
            em_map[code] = int(round(em_fut))
            rows.append({
                "code": code, "name": name,
                "anchor": round(cur_fut, 2),
                "low":    round(cur_fut - em_fut, 2),
                "high":   round(cur_fut + em_fut, 2),
                "emW":    int(round(em_fut)),
                "etf": etf, "etfPx": round(cur_etf, 2),
                "straddle": round(straddle, 2),
                "ratio": round(ratio, 2),
                "expiry": exp,
            })
        except Exception as e:
            rows.append({"code": code, "name": name, "error": str(e)})
    return {"targetFriday": target, "rows": rows, "emMap": em_map}


def _compute_weekly_underlyings(items: list[tuple[str, str]]) -> dict:
    """Weekly 1-σ expected range for any list of (symbol, friendly name)
    pairs whose own option chain gives the straddle. Shared by stocks and
    sector ETFs — for stocks the underlying IS the ticker, no proxy."""
    now_et = pd.Timestamp.now(tz=ET)
    target = _next_friday_str(now_et)
    rows = []
    for sym, name in items:
        try:
            t = yf.Ticker(sym)
            try:
                cur = float(t.fast_info.get("lastPrice") or
                            t.fast_info.get("regularMarketPrice"))
            except Exception:
                cur = float(t.history(period="1d")["Close"].iloc[-1])
            if not cur or cur <= 0:
                rows.append({"sym": sym, "name": name, "error": "no price"})
                continue
            straddle, exp = _atm_straddle_mid(sym, target, cur)
            if straddle is None or straddle <= 0:
                rows.append({"sym": sym, "name": name, "error": "no option chain"})
                continue
            em = straddle * SQRT_PI_2
            rows.append({
                "sym": sym, "name": name,
                "anchor": round(cur, 2),
                "low":    round(cur - em, 2),
                "high":   round(cur + em, 2),
                "emW":    round(em, 2),
                "straddle": round(straddle, 2),
                "expiry": exp,
            })
        except Exception as e:
            rows.append({"sym": sym, "name": name, "error": str(e)})
    return {"targetFriday": target, "rows": rows}


def compute_weekly_stocks() -> dict:
    return _compute_weekly_underlyings(TECH_STOCKS)


def compute_weekly_sectors() -> dict:
    return _compute_weekly_underlyings(SECTOR_ETFS)


def compute_daily_em_nq(fut_intra) -> dict:
    """Per-day cumulative 1-σ band for NQ for each Mon-Fri of the upcoming
    week, using the QQQ daily-expiry ATM straddle × √(π/2) × (NQ/QQQ ratio).
    Matches RDGD's "Daily Levels For Futures – NQ!" weekend format. The mid
    is the current NQ price (Friday close on weekends) — same anchor for
    every day. Each day's EM grows because each day's expiry has more time
    value baked in. Skips holidays (no expiry on that date)."""
    now_et = pd.Timestamp.now(tz=ET)
    today = now_et.date()
    # Next Monday (today if today IS Monday during the day)
    days_to_mon = (7 - today.weekday()) % 7
    if today.weekday() == 0 and now_et.hour < 16:
        days_to_mon = 0
    monday = today + timedelta(days=days_to_mon)

    rows: list[dict] = []
    try:
        if "NQ=F" in fut_intra.columns.get_level_values(0):
            s = fut_intra["NQ=F"]["Close"].dropna()
            cur_nq = float(s.iloc[-1]) if len(s) else None
        else:
            d = yf.download("NQ=F", period="5d", interval="1d", progress=False, auto_adjust=True)
            v = d["Close"].iloc[-1]
            cur_nq = float(v.item() if hasattr(v, "item") else v)
        t = yf.Ticker("QQQ")
        try:
            cur_qqq = float(t.fast_info.get("lastPrice") or
                            t.fast_info.get("regularMarketPrice"))
        except Exception:
            cur_qqq = float(t.history(period="1d")["Close"].iloc[-1])
        if not cur_nq or not cur_qqq or cur_qqq <= 0:
            return {"market": "NQ", "ratio": None, "anchor": None, "rows": [{"error": "no price"}]}
        ratio = cur_nq / cur_qqq
        exps_avail = set(t.options or [])
    except Exception as e:
        return {"market": "NQ", "ratio": None, "anchor": None, "rows": [{"error": str(e)}]}

    for i in range(5):
        d = monday + timedelta(days=i)
        day_name = d.strftime("%a")
        dstr = d.strftime("%Y-%m-%d")
        if dstr not in exps_avail:
            rows.append({"day": day_name, "date": dstr, "error": "no expiry (holiday?)"})
            continue
        try:
            oc = t.option_chain(dstr)
            if oc.calls.empty or oc.puts.empty:
                rows.append({"day": day_name, "date": dstr, "error": "empty chain"})
                continue
            c = oc.calls.iloc[(oc.calls["strike"] - cur_qqq).abs().idxmin()]
            p = oc.puts.iloc[(oc.puts["strike"] - cur_qqq).abs().idxmin()]

            def _mid(r):
                b = float(r.get("bid", 0) or 0)
                a = float(r.get("ask", 0) or 0)
                return (b + a) / 2 if (b > 0 and a > 0) else float(r.get("lastPrice", 0) or 0)

            straddle = _mid(c) + _mid(p)
            if straddle <= 0:
                rows.append({"day": day_name, "date": dstr, "error": "no straddle quote"})
                continue
            em = straddle * SQRT_PI_2 * ratio
            rows.append({
                "day":   day_name,
                "date":  dstr,
                "anchor": round(cur_nq, 2),
                "low":   round(cur_nq - em, 2),
                "high":  round(cur_nq + em, 2),
                "em":    int(round(em)),
                "straddle": round(straddle, 2),
            })
        except Exception as e:
            rows.append({"day": day_name, "date": dstr, "error": str(e)})
    return {"market": "NQ", "ratio": round(ratio, 2), "anchor": round(cur_nq, 2),
            "rows": rows}



def compute_intraday(fut_intra, daily, weekly_em_map: dict | None = None):
    today_et = pd.Timestamp.now(tz=ET).date()
    monday_et = today_et - timedelta(days=today_et.weekday())
    month_start_et = today_et.replace(day=1)
    rows = []
    for code, fsym, vsym, _csym, name in MARKETS:
        try:
            fi = fut_intra[fsym].copy()
            fd = daily[fsym].dropna(subset=["Close"])
        except Exception:
            rows.append({"code": code, "name": name, "error": "data"})
            continue
        stale = False; anchor_date = today_et
        bar = _find_930_bar(fi, today_et)
        if bar is None:
            bar, anchor_date = _most_recent_930(fi)
            stale = bar is not None and anchor_date != today_et
        if bar is None or pd.isna(bar.get("Open")):
            rows.append({"code": code, "name": name, "error": "no 9:30"})
            continue
        open_930 = float(bar["Open"])
        vol_930 = None
        try:
            vd = daily[vsym].dropna(subset=["Close"])
            today_vd = vd[vd.index.date == anchor_date]
            if not today_vd.empty and pd.notna(today_vd["Open"].iloc[0]) and today_vd["Open"].iloc[0] > 0:
                vol_930 = float(today_vd["Open"].iloc[0])
            elif not today_vd.empty and pd.notna(today_vd["Close"].iloc[0]) and today_vd["Close"].iloc[0] > 0:
                vol_930 = float(today_vd["Close"].iloc[0])
            else:
                vol_930 = float(vd["Close"].iloc[-1])
        except Exception:
            pass
        if vol_930 and vol_930 > 0:
            sig_pct = (vol_930 / 100.0) / np.sqrt(252); vol_src = "implied"
        else:
            rets = np.log(fd["Close"]).diff().dropna()
            sig_pct = float(rets.tail(20).std()) if len(rets) >= 5 else 0
            vol_src = "realized"
        em_d = open_930 * sig_pct
        # Prefer the option-implied 1-σ weekly EM (from the ATM straddle) when
        # it's available — matches every major EM tracker, and is ~25% tighter
        # than the √5 × daily approximation in normal contango regimes.
        # Falls back to √5 × daily if the option chain wasn't available.
        em_w_opt = (weekly_em_map or {}).get(code)
        em_w = float(em_w_opt) if em_w_opt and em_w_opt > 0 else em_d * np.sqrt(5)
        em_m = em_d * np.sqrt(21)
        cur_s = fi["Close"].dropna()
        cur = float(cur_s.iloc[-1]) if len(cur_s) else open_930
        wk_bar = _find_930_bar(fi, monday_et)
        wk_open = float(wk_bar["Open"]) if wk_bar is not None and pd.notna(wk_bar.get("Open")) else open_930
        month_bars = fd[fd.index.date >= month_start_et]
        mo_open = float(month_bars["Open"].iloc[0]) if len(month_bars) and pd.notna(month_bars["Open"].iloc[0]) else open_930
        move = cur - open_930; wk_move = cur - wk_open
        sd = move / em_d if em_d > 0 else 0
        sw = wk_move / em_w if em_w > 0 else 0
        # Round the EM once and use that value for both display AND the R/S
        # levels — otherwise the dashboard's levels (computed off the un-
        # rounded float) don't match what the indicator draws when you
        # paste the rounded integer in. Off by a fraction otherwise.
        emD_int = int(round(em_d))
        emW_int = int(round(em_w))
        emM_int = int(round(em_m))
        rows.append({
            "code": code, "name": name,
            "price": round(cur, 2), "open": round(open_930, 2),
            "weekOpen": round(wk_open, 2), "monthOpen": round(mo_open, 2),
            "emD": emD_int, "emW": emW_int, "emM": emM_int,
            "sigD": round(sd, 2), "sigW": round(sw, 2),
            "pctD": int(round(abs(sd) * 100)),
            "dir": 1 if move >= 0 else -1,
            "vol": round(vol_930, 2) if vol_930 else None, "volSrc": vol_src,
            "anchorDate": str(anchor_date), "stale": stale,
            "status": _status(sd, sw),
            "lvls": {
                "r2": round(open_930 + emD_int, 2),
                "r1": round(open_930 + emD_int * 0.5, 2),
                "s1": round(open_930 - emD_int * 0.5, 2),
                "s2": round(open_930 - emD_int, 2),
            },
            "_em_d": em_d,
        })
    return sorted(rows, key=lambda r: abs(r.get("sigD", 0)), reverse=True)


def compute_premarket(fut_intra, daily, cash_daily, intraday_rows):
    today_et = pd.Timestamp.now(tz=ET).date()
    em_map = {r["code"]: r for r in intraday_rows if "_em_d" in r}
    intra_map = {r["code"]: r for r in intraday_rows if "code" in r}
    us_rows = []
    for code, fsym, _vsym, csym, name in MARKETS:
        try:
            cd = cash_daily[csym].dropna(subset=["Close"])
            # Cash close = the most recent close BEFORE today (yesterday's
            # cash close, not today's forming bar).
            cash_prev = cd[cd.index.date < today_et]
            cash_close = float(cash_prev["Close"].iloc[-1]) if not cash_prev.empty else float(cd["Close"].iloc[-1])

            # If today's 9:30 RTH has fired and the anchor isn't stale, show
            # the REALIZED open + actual overnight gap. Otherwise estimate an
            # implied open from current futures vs yesterday's futures close.
            intra = intra_map.get(code, {})
            is_realized = bool(intra and not intra.get("stale", True) and intra.get("anchorDate") == str(today_et))

            fd_d = daily[fsym].dropna(subset=["Close"])
            fi = fut_intra[fsym]
            cur_fut = float(fi["Close"].dropna().iloc[-1])
            prev_fut_bars = fd_d[fd_d.index.date < today_et]
            prev_fut = float(prev_fut_bars["Close"].iloc[-1]) if not prev_fut_bars.empty else cur_fut

            if is_realized:
                shown_open = float(intra["open"])
                gap = shown_open - cash_close
            else:
                gap = cur_fut - prev_fut
                shown_open = cash_close + gap

            em_today = em_map.get(code, {}).get("_em_d", 0)
            frac = gap / em_today if em_today > 0 else 0
            gap_cls, gap_color = _gap_class(frac)
            us_rows.append({
                "code": code, "name": name,
                "cashClose": round(cash_close, 2),
                "impliedOpen": round(shown_open, 2),
                "overnight": round(gap, 2),
                "futNow": round(cur_fut, 2), "futPrev": round(prev_fut, 2),
                "gapEmFrac": round(frac, 2),
                "gapClass": gap_cls, "gapColor": gap_color,
                "dir": 1 if gap >= 0 else -1,
                "isRealized": is_realized,
            })
        except Exception as e:
            us_rows.append({"code": code, "name": name, "error": str(e)})

    g_rows = []
    for code, isym, name in GLOBAL_IDX:
        try:
            sub = cash_daily[isym].dropna(subset=["Close"])
            last = float(sub["Close"].iloc[-1])
            prev = float(sub["Close"].iloc[-2]) if len(sub) >= 2 else last
            move = last - prev
            pct = (move / prev) * 100 if prev else 0
            g_rows.append({
                "code": code, "name": name,
                "last": round(last, 2), "prev": round(prev, 2),
                "move": round(move, 2), "pct": round(pct, 2),
                "dir": 1 if move >= 0 else -1,
            })
        except Exception as e:
            g_rows.append({"code": code, "name": name, "error": str(e)})
    return {"us": us_rows, "global": g_rows}


def compute_open_range(fut_intra, intraday_rows):
    now_et = pd.Timestamp.now(tz=ET)
    today_et = now_et.date()
    if now_et.hour < 10:
        return {"available": False, "msg": "first 30-min range computes after 10:00 ET"}
    em_map = {r["code"]: r for r in intraday_rows if "_em_d" in r}
    rows = []
    for code, fsym, _vsym, _csym, name in MARKETS:
        try:
            fi = fut_intra[fsym].copy()
            idx_et = _to_et(fi.index)
            # 9:30 through 9:55 (six 5-min bars covering the first 30 minutes)
            mask = (idx_et.date == today_et) & (idx_et.hour == 9) & (idx_et.minute >= 30)
            window = fi[mask].dropna(subset=["High", "Low"])
            if window.empty:
                rows.append({"code": code, "name": name, "error": "no first-30 data"})
                continue
            h = float(window["High"].max())
            l = float(window["Low"].min())
            rng = h - l
            em_d = em_map.get(code, {}).get("_em_d", 0)
            frac = rng / em_d if em_d > 0 else 0
            if frac >= 0.40:   verdict, vc = "TREND DAY likely", "#00ff88"
            elif frac >= 0.20: verdict, vc = "neutral",          "#ffcc44"
            else:              verdict, vc = "CHOP likely",      "#888"
            rows.append({
                "code": code, "name": name,
                "high": round(h, 2), "low": round(l, 2),
                "range": round(rng, 1), "emFrac": round(frac, 2),
                "verdict": verdict, "verdictColor": vc,
            })
        except Exception as e:
            rows.append({"code": code, "name": name, "error": str(e)})
    return {"available": True, "rows": rows}


def compute_gamma():
    rows = []
    for tkr in GAMMA_TICKERS:
        try:
            t = yf.Ticker(tkr)
            exps = t.options
            if not exps:
                rows.append({"ticker": tkr, "error": "no expirations"})
                continue
            exp = exps[0]
            oc = t.option_chain(exp)
            try:
                cur = float(t.fast_info.get("lastPrice") or t.fast_info.get("regularMarketPrice"))
            except Exception:
                cur = float(t.history(period="1d")["Close"].iloc[-1])
            calls = oc.calls.dropna(subset=["openInterest"])
            puts  = oc.puts.dropna(subset=["openInterest"])
            # Call wall: highest call-OI strike STRICTLY at/above spot, within
            # ~10% — the resistance dealers defend. Put wall: highest put-OI
            # strike STRICTLY at/below spot, within ~10% — the support.
            # Without these strict sides, a deep-OI ITM strike could land on
            # the wrong side (e.g. SPY call-wall printing below spot).
            calls_above = calls[(calls["strike"] >= cur) & (calls["strike"] <= cur * 1.10)]
            cw_row = (calls_above.loc[calls_above["openInterest"].idxmax()]
                      if not calls_above.empty else None)
            puts_below = puts[(puts["strike"] <= cur) & (puts["strike"] >= cur * 0.90)]
            pw_row = (puts_below.loc[puts_below["openInterest"].idxmax()]
                      if not puts_below.empty else None)
            call_wall = float(cw_row["strike"]) if cw_row is not None else None
            call_oi   = int(cw_row["openInterest"]) if cw_row is not None else 0
            put_wall  = float(pw_row["strike"]) if pw_row is not None else None
            put_oi    = int(pw_row["openInterest"]) if pw_row is not None else 0

            # ── Position vs the walls ─────────────────────────────────────
            # "At" the wall = within 0.5% of the strike (≈ a few pts on SPY/QQQ).
            position = position_short = "—"
            position_color = "#888"
            dist_cw = dist_pw = pct_in_range = None
            if call_wall is not None and put_wall is not None and cur:
                THR = 0.005
                dist_cw = round(cur - call_wall, 2)
                dist_pw = round(cur - put_wall, 2)
                cw_thr = call_wall * THR
                pw_thr = put_wall * THR
                if call_wall > put_wall:
                    pct_in_range = round((cur - put_wall) / (call_wall - put_wall) * 100, 1)
                if cur > call_wall + cw_thr:
                    position = "ABOVE call wall — squeeze / dealer short gamma"
                    position_short = "ABOVE CW"
                    position_color = "#ffcc44"
                elif abs(cur - call_wall) <= cw_thr:
                    position = "AT call wall — magnet / pin"
                    position_short = "AT CW"
                    position_color = "#ff8c44"
                elif cur < put_wall - pw_thr:
                    position = "BELOW put wall — support broken"
                    position_short = "BELOW PW"
                    position_color = "#ff4466"
                elif abs(cur - put_wall) <= pw_thr:
                    position = "AT put wall — support test"
                    position_short = "AT PW"
                    position_color = "#ff8c44"
                else:
                    position = "between walls — normal range"
                    position_short = "BETWEEN"
                    position_color = "#00cc88"

            rows.append({
                "ticker": tkr, "expiry": exp, "price": round(cur, 2),
                "callWall": call_wall, "callOI": call_oi,
                "putWall":  put_wall,  "putOI":  put_oi,
                "position": position, "positionShort": position_short,
                "positionColor": position_color,
                "distCallWall": dist_cw, "distPutWall": dist_pw,
                "pctInRange": pct_in_range,
            })
        except Exception as e:
            rows.append({"ticker": tkr, "error": str(e)})
    return rows


# ── top-level ─────────────────────────────────────────────────────────────

def compute():
    fut_syms = [m[1] for m in MARKETS]
    vol_syms = [m[2] for m in MARKETS]
    cash_syms = [m[3] for m in MARKETS] + [g[1] for g in GLOBAL_IDX]

    fut_intra = yf.download(fut_syms, period="5d", interval="5m",
                            group_by="ticker", progress=False, auto_adjust=True)
    daily = yf.download(fut_syms + vol_syms, period="2mo", interval="1d",
                        group_by="ticker", progress=False, auto_adjust=True)
    cash_daily = yf.download(cash_syms, period="5d", interval="1d",
                             group_by="ticker", progress=False, auto_adjust=True)

    # Weekly EM first so the intraday section can use the option-implied
    # value (matches impliedopen.com etc.) for emW instead of √5 × daily.
    weekly = compute_weekly_range(fut_intra)
    intraday_rows = compute_intraday(fut_intra, daily, weekly.get("emMap"))
    pre = compute_premarket(fut_intra, daily, cash_daily, intraday_rows)
    or_data = compute_open_range(fut_intra, intraday_rows)
    gamma = compute_gamma()
    weekly_stocks = compute_weekly_stocks()
    weekly_sectors = compute_weekly_sectors()
    daily_nq = compute_daily_em_nq(fut_intra)

    movers = [r for r in intraday_rows if abs(r.get("sigD", 0)) >= 0.5]
    if not movers:
        regime = "quiet — all inside daily EM"
    else:
        ups = sum(1 for r in movers if r.get("dir", 0) > 0)
        dns = len(movers) - ups
        if ups and not dns:   regime = "risk-ON — movers aligned UP"
        elif dns and not ups: regime = "risk-OFF — movers aligned DOWN"
        else:                 regime = "MIXED — markets diverging"

    for r in intraday_rows:
        r.pop("_em_d", None)

    # Drop the internal map before returning; only the row data is needed downstream.
    weekly.pop("emMap", None)

    return {
        "asOf": pd.Timestamp.now(tz=ET).strftime("%Y-%m-%d %H:%M ET"),
        "regime": regime,
        "intraday": intraday_rows,
        "premarket": pre,
        "openRange": or_data,
        "gamma": gamma,
        "weekly": weekly,
        "weeklyStocks": weekly_stocks,
        "weeklySectors": weekly_sectors,
        "dailyNq": daily_nq,
    }


@app.get("/api/four")
def api_four():
    try:
        return JSONResponse(compute())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)


PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Expected Move</title><style>
body{background:#0d0d0d;color:#eee;font-family:system-ui,Segoe UI,sans-serif;margin:0 auto;padding:20px;max-width:min(96vw,1280px);font-size:16px}
/* Side-by-side groups — sections inside one of these wrap as columns on
   wide screens, then collapse to a single column on narrow ones. */
.group{display:grid;gap:18px;margin:0}
.group-2{grid-template-columns:repeat(auto-fit,minmax(300px,1fr))}
.group-3{grid-template-columns:repeat(auto-fit,minmax(280px,1fr))}
.col{min-width:0}
h1{font-size:1rem;letter-spacing:.2em;color:#00ff88;text-transform:uppercase;margin:0 0 6px;text-align:center}
.regime{text-align:center;font-size:1.1rem;font-weight:700;margin:0 0 4px;color:#ffcc44}
.asof{text-align:center;font-size:.72rem;color:#666;margin:0 0 6px;letter-spacing:.05em}
.section{margin:26px 0 10px;display:flex;align-items:center;gap:10px}
.section h2{font-size:.82rem;letter-spacing:.25em;color:#999;text-transform:uppercase;margin:0;font-weight:700}
.section .line{flex:1;height:1px;background:#222}
.section .hint{font-size:.65rem;color:#555;font-style:italic}

/* Intraday cards */
.card{background:#141414;border:1px solid #222;border-radius:12px;padding:14px 16px;margin:0 0 12px;position:relative}
.lead{border-color:#00ff88;box-shadow:0 0 0 1px #00ff8855}
.rank{position:absolute;top:12px;right:14px;font-size:.7rem;color:#666;letter-spacing:.1em}
.stale{position:absolute;top:12px;left:14px;font-size:.65rem;color:#ff8c44;font-weight:700;letter-spacing:.06em}
.top{display:flex;justify-content:space-between;align-items:baseline;margin-top:10px}
.code{font-size:1.35rem;font-weight:800}
.name{font-size:.78rem;color:#777;margin-left:6px}
.sig{font-size:2rem;font-weight:800}
.up{color:#00ff88}.dn{color:#ff4466}
.bar{height:8px;background:#1c1c1c;border-radius:4px;margin:11px 0 8px;position:relative;overflow:hidden}
.fill{height:100%;border-radius:4px}
.mark{position:absolute;top:-2px;width:2px;height:12px;background:#888}
.status{font-size:.9rem;color:#bbb}
.meta{display:flex;gap:14px;font-size:.76rem;color:#666;margin-top:9px;flex-wrap:wrap}
.meta b{color:#aaa;font-weight:600}
.lvls{display:flex;gap:12px;font-size:.75rem;margin-top:7px;flex-wrap:wrap}
.lvls span{color:#777}
.lvls .r{color:#00cc88}.lvls .s{color:#ff6688}
.em{display:flex;gap:14px;font-size:.8rem;margin-top:8px;flex-wrap:wrap;border-top:1px solid #1c1c1c;padding-top:8px}
.em b{color:#00ff88;font-weight:700}.em span{color:#888}

/* Pre-market rows */
.pmrow{display:grid;grid-template-columns:54px 1fr auto;gap:10px;align-items:baseline;padding:10px 13px;background:#141414;border:1px solid #222;border-radius:10px;margin:0 0 7px}
.pmrow .pcode{font-size:1rem;font-weight:800}
.pmrow .pname{font-size:.7rem;color:#666;display:block;margin-top:2px}
.pmrow .pmid{font-size:.78rem;color:#aaa}
.pmrow .pmid b{color:#ddd}
.pmrow .pright{text-align:right}
.pmrow .pmove{font-size:.95rem;font-weight:700}
.pmrow .pgap{font-size:.7rem;font-weight:700;letter-spacing:.05em;margin-top:3px}

/* Open range rows */
.orrow{display:grid;grid-template-columns:54px 1fr auto;gap:10px;align-items:baseline;padding:10px 13px;background:#141414;border:1px solid #222;border-radius:10px;margin:0 0 7px}
.orrow .ocode{font-size:1rem;font-weight:800}
.orrow .orng{font-size:.78rem;color:#aaa}
.orrow .opct{font-size:1rem;font-weight:700;text-align:right}
.orrow .overdict{font-size:.7rem;font-weight:700;letter-spacing:.04em;margin-top:3px;text-align:right}
.ormsg{padding:14px;background:#141414;border:1px dashed #333;border-radius:10px;text-align:center;color:#888;font-size:.78rem}

/* Gamma cards */
.gcard{padding:12px 14px;background:#141414;border:1px solid #222;border-radius:10px;margin:0 0 9px}
.gcard .gh{display:flex;justify-content:space-between;align-items:baseline}
.gcard .gt{font-size:1.1rem;font-weight:800}
.gcard .gpx{font-size:.85rem;color:#aaa}
.gcard .gtag{font-size:.78rem;font-weight:800;letter-spacing:.06em}
.gbar{position:relative;height:8px;background:#1c1c1c;border-radius:4px;margin:9px 0 6px}
.gbar .gend{position:absolute;top:-3px;width:2px;height:14px;background:#666}
.gbar .glabL,.gbar .glabR{position:absolute;top:11px;font-size:.6rem;color:#777}
.gbar .glabL{left:0}.gbar .glabR{right:0}
.gbar .gpos{position:absolute;top:-3px;width:3px;height:14px;background:#fff;border-radius:1px;box-shadow:0 0 4px #fff}
.gcard .gw{display:flex;gap:14px;font-size:.82rem;margin-top:18px;flex-wrap:wrap}
.gcard .cw{color:#00cc88;font-weight:700}
.gcard .pw{color:#ff6688;font-weight:700}
.gcard .gd{font-size:.74rem;margin-top:7px;font-weight:600}

/* Weekly range rows */
.wkrow{display:grid;grid-template-columns:54px 1fr auto;gap:10px;align-items:baseline;padding:10px 13px;background:#141414;border:1px solid #222;border-radius:10px;margin:0 0 7px}
.wkrow .wcode{font-size:1rem;font-weight:800}
.wkrow .wname{font-size:.66rem;color:#666;display:block;margin-top:2px}
.wkrow .wmid{font-size:.78rem;color:#aaa}
.wkrow .wmid b{color:#ddd}
.wkrow .wmid .wlo{color:#ff6688;font-weight:700}
.wkrow .wmid .whi{color:#00cc88;font-weight:700}
.wkrow .wright{text-align:right}
.wkrow .wem{font-size:.95rem;font-weight:700;color:#ffcc44}
.wkrow .wexp{font-size:.62rem;color:#666;margin-top:3px}

.note{font-size:.72rem;color:#666;text-align:center;margin:22px 0;line-height:1.65}
</style></head><body>
<h1>Expected Move</h1>
<div class=regime id=regime>...</div>
<div class=asof id=asof></div>

<div class="group group-2">
  <div class=col>
    <div class=section><h2>Pre-market · US</h2><div class=line></div><div class=hint>implied opens</div></div>
    <div id=pmus></div>
  </div>
  <div class=col>
    <div class=section><h2>Pre-market · Global</h2><div class=line></div><div class=hint>FTSE · DAX · Nikkei · HSI</div></div>
    <div id=pmglobal></div>
  </div>
</div>

<div class=section><h2>Intraday EM</h2><div class=line></div><div class=hint>9:30-anchored · frozen</div></div>
<div id=intraday></div>

<div class=section><h2>Daily EM · NQ (next week)</h2><div class=line></div><div class=hint id=dnqhint>Mon-Fri · 1σ from QQQ daily-expiry straddle</div></div>
<div id=dailyNq></div>

<div class="group group-2">
  <div class=col>
    <div class=section><h2>Open range</h2><div class=line></div><div class=hint>9:30-10:00 vs daily EM</div></div>
    <div id=openrange></div>
  </div>
  <div class=col>
    <div class=section><h2>Gamma walls</h2><div class=line></div><div class=hint>SPY · QQQ nearest expiry</div></div>
    <div id=gamma></div>
  </div>
</div>

<div class="group group-3">
  <div class=col>
    <div class=section><h2>Weekly · futures</h2><div class=line></div><div class=hint id=wkhint>next Friday · 1σ from ATM straddle</div></div>
    <div id=weekly></div>
  </div>
  <div class=col>
    <div class=section><h2>Weekly · tech</h2><div class=line></div><div class=hint id=wkstkhint>next Friday · 1σ from each stock's own ATM straddle</div></div>
    <div id=weeklyStocks></div>
  </div>
  <div class=col>
    <div class=section><h2>Weekly · sectors</h2><div class=line></div><div class=hint id=wksechint>next Friday · 1σ from each ETF's own ATM straddle</div></div>
    <div id=weeklySectors></div>
  </div>
</div>

<div class=note>Anchor & EM frozen at the 9:30 ET open and held all day. σ = daily EMs travelled from that open. R/S = daily EM levels. "paste" = numbers for the indicator. Free delayed data (~15 min). Reloads every 60s.</div>

<script>
function fmt(n){return n==null?'—':n.toLocaleString()}
function bar(sd){
  const c=Math.max(-2,Math.min(2,sd)), pct=(c+2)/4*100, col=sd>=0?'#00ff88':'#ff4466';
  const from=sd>=0?50:pct, w=Math.abs(pct-50);
  return '<div class=bar><div class=mark style="left:50%"></div><div class=mark style="left:25%"></div><div class=mark style="left:75%"></div><div class=fill style="margin-left:'+from+'%;width:'+w+'%;background:'+col+'"></div></div>';
}

function renderPremarket(p){
  const usRows = (p.us||[]).map(r=>{
    if(r.error) return '<div class=pmrow><span class=pcode>'+r.code+'</span><span class=pmid>'+r.name+' — '+r.error+'</span><span></span></div>';
    const arrow=r.dir>0?'▲':'▼', col=r.dir>0?'#00ff88':'#ff4466';
    const openLabel = r.isRealized ? 'actual open' : 'implied open';
    return '<div class=pmrow>'
      +'<div><span class=pcode>'+r.code+'</span><span class=pname>'+r.name+'</span></div>'
      +'<div class=pmid>close <b>'+fmt(r.cashClose)+'</b> → '+openLabel+' <b>'+fmt(r.impliedOpen)+'</b></div>'
      +'<div class=pright><div class=pmove style="color:'+col+'">'+arrow+' '+(r.overnight>=0?'+':'')+r.overnight+'</div>'
      +'<div class=pgap style="color:'+r.gapColor+'">'+r.gapClass+' · '+(r.gapEmFrac>=0?'+':'')+r.gapEmFrac+'σ</div></div>'
      +'</div>';
  }).join('');
  document.getElementById('pmus').innerHTML = usRows;

  const gRows = (p.global||[]).map(r=>{
    if(r.error) return '<div class=pmrow><span class=pcode>'+r.code+'</span><span class=pmid>'+r.name+' — '+r.error+'</span><span></span></div>';
    const arrow=r.dir>0?'▲':'▼', col=r.dir>0?'#00ff88':'#ff4466';
    return '<div class=pmrow>'
      +'<div><span class=pcode>'+r.code+'</span><span class=pname>'+r.name+'</span></div>'
      +'<div class=pmid>last <b>'+fmt(r.last)+'</b> · prev <b>'+fmt(r.prev)+'</b></div>'
      +'<div class=pright><div class=pmove style="color:'+col+'">'+arrow+' '+(r.move>=0?'+':'')+r.move+'</div>'
      +'<div class=pgap style="color:#888">'+(r.pct>=0?'+':'')+r.pct+'%</div></div>'
      +'</div>';
  }).join('');
  document.getElementById('pmglobal').innerHTML = gRows;
}

function renderIntraday(rows){
  const h = rows.map((r,i)=>{
    if(r.error) return '<div class=card><span class=code>'+r.code+'</span> <span class=name>'+r.name+' — '+r.error+'</span></div>';
    const cl=r.dir>0?'up':'dn', arrow=r.dir>0?'▲':'▼';
    const staleBadge = r.stale ? '<div class=stale>STALE · '+r.anchorDate+'</div>' : '';
    return '<div class="card'+(i===0?' lead':'')+'">'
      +'<div class=rank>#'+(i+1)+(i===0?' LEADER':'')+'</div>'+staleBadge
      +'<div class=top><div><span class=code>'+r.code+'</span><span class=name>'+r.name+'</span></div>'
      +'<div class="sig '+cl+'">'+arrow+' '+Math.abs(r.sigD).toFixed(2)+'σ</div></div>'
      +bar(r.sigD)
      +'<div class=status>'+r.status+'</div>'
      +'<div class=meta>'
      +'<span><b>'+fmt(r.price)+'</b> px</span>'
      +'<span>9:30 open <b>'+fmt(r.open)+'</b></span>'
      +'<span>wk <b>'+r.sigW.toFixed(2)+'σ</b></span>'
      +'<span>vol <b>'+(r.vol!=null?r.vol:'—')+'</b> ('+r.volSrc+')</span>'
      +'</div>'
      +'<div class=lvls>'
      +'<span class=r>R2 '+fmt(r.lvls.r2)+'</span>'
      +'<span class=r>R1 '+fmt(r.lvls.r1)+'</span>'
      +'<span class=s>S1 '+fmt(r.lvls.s1)+'</span>'
      +'<span class=s>S2 '+fmt(r.lvls.s2)+'</span>'
      +'</div>'
      +'<div class=em>paste → <span>daily <b>'+fmt(r.emD)+'</b></span>'
      +'<span>weekly <b>'+fmt(r.emW)+'</b></span>'
      +'<span>monthly <b>'+fmt(r.emM)+'</b></span></div>'
      +'</div>';
  }).join('');
  document.getElementById('intraday').innerHTML = h;
}

function renderOpenRange(o){
  if(!o.available){
    document.getElementById('openrange').innerHTML = '<div class=ormsg>'+(o.msg||'open range pending')+'</div>';
    return;
  }
  const h = (o.rows||[]).map(r=>{
    if(r.error) return '<div class=orrow><span class=ocode>'+r.code+'</span><span class=orng>'+r.name+' — '+r.error+'</span><span></span></div>';
    return '<div class=orrow>'
      +'<div><span class=ocode>'+r.code+'</span></div>'
      +'<div class=orng>H <b>'+fmt(r.high)+'</b> · L <b>'+fmt(r.low)+'</b> · range <b>'+fmt(r.range)+'</b></div>'
      +'<div><div class=opct>'+(r.emFrac*100).toFixed(0)+'%</div>'
      +'<div class=overdict style="color:'+r.verdictColor+'">'+r.verdict+'</div></div>'
      +'</div>';
  }).join('');
  document.getElementById('openrange').innerHTML = h;
}

function gbar(pct){
  // pct can be negative (below put wall) or >100 (above call wall) — clip the marker.
  const clip = Math.max(0, Math.min(100, pct));
  return '<div class=gbar>'
    +'<div class=gend style="left:0%"></div>'
    +'<div class=gend style="left:100%"></div>'
    +'<div class=gpos style="left:'+clip+'%"></div>'
    +'<span class=glabL>put wall</span><span class=glabR>call wall</span>'
    +'</div>';
}
function renderGamma(g){
  const h = (g||[]).map(r=>{
    if(r.error) return '<div class=gcard><span class=gt>'+r.ticker+'</span> — '+r.error+'</div>';
    const dcw = r.distCallWall;
    const dpw = r.distPutWall;
    const dcwStr = dcw!=null ? (dcw>=0?'+':'')+dcw : '—';
    const dpwStr = dpw!=null ? (dpw>=0?'+':'')+dpw : '—';
    const tag = '<span class=gtag style="color:'+r.positionColor+'">'+r.positionShort+'</span>';
    const ladder = r.pctInRange!=null ? gbar(r.pctInRange) : '';
    return '<div class=gcard>'
      +'<div class=gh><div><span class=gt>'+r.ticker+'</span> <span class=gpx>px '+fmt(r.price)+' · exp '+r.expiry+'</span></div>'+tag+'</div>'
      +ladder
      +'<div class=gw>'
      +'<span>call wall <span class=cw>'+fmt(r.callWall)+'</span> <span class=gpx>('+fmt(r.callOI)+' OI · '+dcwStr+')</span></span>'
      +'<span>put wall <span class=pw>'+fmt(r.putWall)+'</span> <span class=gpx>('+fmt(r.putOI)+' OI · '+dpwStr+')</span></span>'
      +'</div>'
      +'<div class=gd style="color:'+r.positionColor+'">'+r.position+'</div>'
      +'</div>';
  }).join('');
  document.getElementById('gamma').innerHTML = h;
}

function renderWeekly(w){
  const hint = document.getElementById('wkhint');
  if(hint) hint.textContent = (w.targetFriday ? 'expiry '+w.targetFriday+' · 1σ from ATM straddle' : '1σ from ATM straddle');
  const rows = w.rows||[];
  const h = rows.map(r=>{
    if(r.error) return '<div class=wkrow><span class=wcode>'+r.code+'</span><span class=wmid>'+r.name+' — '+r.error+'</span><span></span></div>';
    return '<div class=wkrow>'
      +'<div><span class=wcode>'+r.code+'</span><span class=wname>'+r.name+'</span></div>'
      +'<div class=wmid><span class=wlo>'+fmt(r.low)+'</span> · mid <b>'+fmt(r.anchor)+'</b> · <span class=whi>'+fmt(r.high)+'</span></div>'
      +'<div class=wright><div class=wem>± '+fmt(r.emW)+'</div><div class=wexp>'+r.etf+' '+fmt(r.straddle)+' × √π/2</div></div>'
      +'</div>';
  }).join('');
  document.getElementById('weekly').innerHTML = h;
}

function renderWeeklyUnderlying(w, containerId, hintId, kindLabel){
  const hint = document.getElementById(hintId);
  if(hint) hint.textContent = (w.targetFriday ? 'expiry '+w.targetFriday+' · 1σ from each '+kindLabel+'\'s own ATM straddle' : '1σ from each '+kindLabel+'\'s own ATM straddle');
  const rows = w.rows||[];
  const h = rows.map(r=>{
    if(r.error) return '<div class=wkrow><span class=wcode>'+r.sym+'</span><span class=wmid>'+r.name+' — '+r.error+'</span><span></span></div>';
    return '<div class=wkrow>'
      +'<div><span class=wcode>'+r.sym+'</span><span class=wname>'+r.name+'</span></div>'
      +'<div class=wmid><span class=wlo>'+fmt(r.low)+'</span> · mid <b>'+fmt(r.anchor)+'</b> · <span class=whi>'+fmt(r.high)+'</span></div>'
      +'<div class=wright><div class=wem>± '+r.emW.toFixed(2)+'</div><div class=wexp>straddle '+r.straddle.toFixed(2)+' × √π/2</div></div>'
      +'</div>';
  }).join('');
  document.getElementById(containerId).innerHTML = h;
}
function renderWeeklyStocks(w){ renderWeeklyUnderlying(w, 'weeklyStocks', 'wkstkhint', 'stock'); }

function renderDailyNq(dn){
  const hint = document.getElementById('dnqhint');
  if(hint) hint.textContent = 'NQ anchor '+fmt(dn.anchor)+'  ·  QQQ ratio '+fmt(dn.ratio)+'  ·  Mon-Fri cumulative 1σ';
  const rows = dn.rows||[];
  const h = rows.map(r=>{
    if(r.error) return '<div class=wkrow><span class=wcode>'+(r.day||'?')+'</span><span class=wmid>'+(r.date||'')+' — '+r.error+'</span><span></span></div>';
    return '<div class=wkrow>'
      +'<div><span class=wcode>'+r.day+'</span><span class=wname>'+r.date+'</span></div>'
      +'<div class=wmid><span class=wlo>'+fmt(r.low)+'</span> · mid <b>'+fmt(r.anchor)+'</b> · <span class=whi>'+fmt(r.high)+'</span></div>'
      +'<div class=wright><div class=wem>± '+fmt(r.em)+'</div><div class=wexp>straddle '+r.straddle+' × √π/2</div></div>'
      +'</div>';
  }).join('');
  document.getElementById('dailyNq').innerHTML = h;
}

async function go(){
  let d; try{ d=await (await fetch('/api/four')).json(); }catch(e){ return; }
  if(d.error){document.getElementById('regime').textContent='data error';return;}
  document.getElementById('regime').textContent=d.regime;
  document.getElementById('asof').textContent='as of '+d.asOf;
  renderPremarket(d.premarket||{us:[],global:[]});
  renderIntraday(d.intraday||[]);
  renderOpenRange(d.openRange||{available:false});
  renderGamma(d.gamma||[]);
  renderWeekly(d.weekly||{rows:[]});
  renderWeeklyStocks(d.weeklyStocks||{rows:[]});
  renderWeeklyUnderlying(d.weeklySectors||{rows:[]}, 'weeklySectors', 'wksechint', 'ETF');
  renderDailyNq(d.dailyNq||{rows:[]});
}
go(); setInterval(go,60000);
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE
