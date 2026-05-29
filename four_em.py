"""
4-Market Expected Move — anchored at the 9:30 ET RTH open, frozen all day.

For each market (ES/NQ/YM/RTY):
  ANCHOR  = today's 9:30 ET RTH open (from 5m intraday futures bars).
  DAILY EM= that day's open VXN-style vol index × √(1/252) × anchor.
  σD      = (current price − 9:30 anchor) / EM.

The anchor and EM are frozen at 9:30 ET and held all day — the same way
sites like impliedopen.com do it. Only the current price (and σD) update
through the session.

Before 9:30 ET each day the page falls back to the most recent prior 9:30
anchor and marks it "stale until open" — the new day's values latch at the
first 9:30 ET 5m bar.

Free delayed data (yfinance, ~15 min). Implied vol drives EM where Yahoo
has the index (VIX/VXN/VXD); RTY falls back to realized vol since ^RVX is
not available.

Local:   uvicorn four_em:app --reload   ->  http://127.0.0.1:8000
Deploy:  Render web service starting `uvicorn em_web:app` (em_web
         re-exports this module's app) — see render.yaml.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI(title="4-Market Expected Move")

ET = ZoneInfo("America/New_York")

# code, futures symbol, implied-vol index, friendly name
MARKETS = [
    ("ES",  "ES=F",  "^VIX", "S&P 500"),
    ("NQ",  "NQ=F",  "^VXN", "Nasdaq 100"),
    ("YM",  "YM=F",  "^VXD", "Dow 30"),
    ("RTY", "RTY=F", "^RVX", "Russell 2000"),
]


def _to_et(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    if idx.tz is None:
        return idx.tz_localize("UTC").tz_convert(ET)
    return idx.tz_convert(ET)


def _find_930_bar(fut_intra: pd.DataFrame, target_date):
    """Return the 5m bar whose start is 9:30 ET on target_date, or None."""
    if fut_intra is None or fut_intra.empty:
        return None
    idx_et = _to_et(fut_intra.index)
    mask = (idx_et.hour == 9) & (idx_et.minute == 30) & (idx_et.date == target_date)
    matched = fut_intra[mask]
    if matched.empty:
        return None
    # iterate to find the first non-NaN Open
    for _, row in matched.iterrows():
        if pd.notna(row.get("Open", None)):
            return row
    return None


def _most_recent_930(fut_intra: pd.DataFrame):
    """Most recent 9:30 ET bar with a valid Open."""
    if fut_intra is None or fut_intra.empty:
        return None, None
    idx_et = _to_et(fut_intra.index)
    mask = (idx_et.hour == 9) & (idx_et.minute == 30)
    matched = fut_intra[mask]
    if matched.empty:
        return None, None
    matched_et_index = _to_et(matched.index)
    for i in range(len(matched) - 1, -1, -1):
        row = matched.iloc[i]
        if pd.notna(row.get("Open", None)):
            return row, matched_et_index[i].date()
    return None, None


def _status(sd: float, sw: float) -> str:
    a = abs(sd)
    if a < 0.5:
        base = "inside — building"
    elif a < 0.9:
        base = "approaching daily EM"
    elif a < 1.15:
        base = "AT daily EM (1σ)"
    elif a < 1.6:
        base = "PAST daily EM"
    else:
        base = "well past daily EM"
    aw = abs(sw)
    if a >= 1.0:
        if aw >= 1.0:
            base += " · AT weekly EM"
        elif aw >= 0.7:
            base += " · nearing weekly EM"
        else:
            base += " · heading to weekly"
    return base


def compute() -> dict:
    fut_syms = [m[1] for m in MARKETS]
    vol_syms = [m[2] for m in MARKETS]

    # 5m intraday for futures — needed to find today's 9:30 ET bar (yfinance's
    # daily Open for futures is the 6 PM session open, not 9:30 RTH).
    fut_intra = yf.download(
        fut_syms, period="5d", interval="5m",
        group_by="ticker", progress=False, auto_adjust=True,
    )

    # Daily bars for vol indices (Open ~= 9:30 ET value, since VXN/VIX only
    # compute during RTH) and for futures (monthly anchor + realized fallback).
    daily = yf.download(
        fut_syms + vol_syms, period="2mo", interval="1d",
        group_by="ticker", progress=False, auto_adjust=True,
    )

    today_et = pd.Timestamp.now(tz=ET).date()
    monday_et = today_et - timedelta(days=today_et.weekday())
    month_start_et = today_et.replace(day=1)

    rows = []
    for code, fsym, vsym, name in MARKETS:
        try:
            fi = fut_intra[fsym].copy()
            fd = daily[fsym].dropna(subset=["Close"])
        except Exception:
            rows.append({"code": code, "name": name, "error": "futures data unavailable"})
            continue

        # ── ANCHOR — today's 9:30 ET RTH open ─────────────────────────────
        stale = False
        anchor_date = today_et
        bar = _find_930_bar(fi, today_et)
        if bar is None:
            # Before today's 9:30 (pre-market) or data gap — fall back to the
            # most recent prior 9:30, and flag it stale until the new open.
            bar, anchor_date = _most_recent_930(fi)
            if bar is not None:
                stale = (anchor_date != today_et)
        if bar is None or pd.isna(bar.get("Open")):
            rows.append({"code": code, "name": name, "error": "no 9:30 ET bar found"})
            continue
        open_930 = float(bar["Open"])

        # ── VOL @ 9:30 (frozen) — daily-bar Open of the vol index ─────────
        vol_930 = None
        try:
            vd = daily[vsym].dropna(subset=["Close"])
            today_vd = vd[vd.index.date == anchor_date]
            if not today_vd.empty and pd.notna(today_vd["Open"].iloc[0]) and today_vd["Open"].iloc[0] > 0:
                vol_930 = float(today_vd["Open"].iloc[0])
            elif not today_vd.empty and pd.notna(today_vd["Close"].iloc[0]) and today_vd["Close"].iloc[0] > 0:
                # daily Open sometimes missing for indices; use Close as the
                # next-best frozen value for that session.
                vol_930 = float(today_vd["Close"].iloc[0])
            else:
                vol_930 = float(vd["Close"].iloc[-1])  # last close fallback
        except Exception:
            vol_930 = None

        if vol_930 is not None and vol_930 > 0:
            sig_pct = (vol_930 / 100.0) / np.sqrt(252)
            vol_src = "implied"
        else:
            rets = np.log(fd["Close"]).diff().dropna()
            sig_pct = float(rets.tail(20).std()) if len(rets) >= 5 else 0.0
            vol_src = "realized"

        em_d = open_930 * sig_pct
        em_w = em_d * np.sqrt(5)
        em_m = em_d * np.sqrt(21)

        # ── Current price (latest available 5m close) ─────────────────────
        cur_series = fi["Close"].dropna()
        cur = float(cur_series.iloc[-1]) if len(cur_series) else open_930

        # ── Weekly anchor: Monday's 9:30 bar (this week) ──────────────────
        wk_bar = _find_930_bar(fi, monday_et)
        wk_open = float(wk_bar["Open"]) if (wk_bar is not None and pd.notna(wk_bar.get("Open"))) else open_930

        # ── Monthly anchor: first trading day of month (futures daily) ────
        month_bars = fd[fd.index.date >= month_start_et]
        if len(month_bars) and pd.notna(month_bars["Open"].iloc[0]):
            mo_open = float(month_bars["Open"].iloc[0])
        else:
            mo_open = open_930

        move = cur - open_930
        wk_move = cur - wk_open
        sd = move / em_d if em_d > 0 else 0.0
        sw = wk_move / em_w if em_w > 0 else 0.0

        rows.append({
            "code": code, "name": name,
            "price": round(cur, 2), "open": round(open_930, 2),
            "weekOpen": round(wk_open, 2), "monthOpen": round(mo_open, 2),
            "emD": int(round(em_d)), "emW": int(round(em_w)), "emM": int(round(em_m)),
            "moveD": round(move, 1),
            "sigD": round(sd, 2), "sigW": round(sw, 2),
            "pctD": int(round(abs(sd) * 100)),
            "dir": 1 if move >= 0 else -1,
            "vol": round(vol_930, 2) if vol_930 else None, "volSrc": vol_src,
            "anchorDate": str(anchor_date), "stale": stale,
            "status": _status(sd, sw),
            "lvls": {
                "r2": round(open_930 + em_d, 2),
                "r1": round(open_930 + em_d * 0.5, 2),
                "s1": round(open_930 - em_d * 0.5, 2),
                "s2": round(open_930 - em_d, 2),
            },
        })

    ranked = sorted([r for r in rows if "sigD" in r],
                    key=lambda r: abs(r["sigD"]), reverse=True)
    errs = [r for r in rows if "sigD" not in r]

    movers = [r for r in ranked if abs(r["sigD"]) >= 0.5]
    if not movers:
        regime = "quiet — all inside daily EM"
    else:
        ups = sum(1 for r in movers if r["dir"] > 0)
        dns = len(movers) - ups
        if ups and not dns:
            regime = "risk-ON — movers aligned UP"
        elif dns and not ups:
            regime = "risk-OFF — movers aligned DOWN"
        else:
            regime = "MIXED — markets diverging"

    return {"rows": ranked + errs, "regime": regime,
            "asOf": pd.Timestamp.now(tz=ET).strftime("%Y-%m-%d %H:%M ET")}


@app.get("/api/four")
def api_four():
    try:
        return JSONResponse(compute())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)


PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>4-Market Expected Move</title><style>
body{background:#0d0d0d;color:#eee;font-family:system-ui,Segoe UI,sans-serif;margin:0;padding:18px;max-width:560px;margin:0 auto}
h1{font-size:.8rem;letter-spacing:.2em;color:#00ff88;text-transform:uppercase;margin:0 0 4px;text-align:center}
.regime{text-align:center;font-size:.85rem;font-weight:700;margin:0 0 4px;color:#ffcc44}
.asof{text-align:center;font-size:.6rem;color:#666;margin:0 0 14px;letter-spacing:.05em}
.card{background:#141414;border:1px solid #222;border-radius:12px;padding:12px 14px;margin:0 0 10px;position:relative}
.lead{border-color:#00ff88;box-shadow:0 0 0 1px #00ff8855}
.rank{position:absolute;top:10px;right:12px;font-size:.6rem;color:#666;letter-spacing:.1em}
.stale{position:absolute;top:10px;left:12px;font-size:.55rem;color:#ff8c44;font-weight:700;letter-spacing:.06em}
.top{display:flex;justify-content:space-between;align-items:baseline;margin-top:8px}
.code{font-size:1.15rem;font-weight:800}
.name{font-size:.65rem;color:#777;margin-left:6px}
.sig{font-size:1.7rem;font-weight:800}
.up{color:#00ff88}.dn{color:#ff4466}
.bar{height:7px;background:#1c1c1c;border-radius:4px;margin:9px 0 7px;position:relative;overflow:hidden}
.fill{height:100%;border-radius:4px}
.mark{position:absolute;top:-2px;width:2px;height:11px;background:#888}
.status{font-size:.72rem;color:#bbb}
.meta{display:flex;gap:12px;font-size:.62rem;color:#666;margin-top:7px;flex-wrap:wrap}
.meta b{color:#aaa;font-weight:600}
.lvls{display:flex;gap:10px;font-size:.6rem;margin-top:6px;flex-wrap:wrap}
.lvls span{color:#777}
.lvls .r{color:#00cc88}.lvls .s{color:#ff6688}
.em{display:flex;gap:10px;font-size:.62rem;margin-top:6px;flex-wrap:wrap;border-top:1px solid #1c1c1c;padding-top:6px}
.em b{color:#00ff88;font-weight:700}.em span{color:#888}
.note{font-size:.62rem;color:#666;text-align:center;margin:14px 0;line-height:1.6}
</style></head><body>
<h1>4-Market Expected Move</h1>
<div class=regime id=regime>...</div>
<div class=asof id=asof></div>
<div id=cards></div>
<div class=note>Anchor and EM are <b>frozen at the 9:30 ET open</b> and held all day — the page only refreshes the current price and σD. σ = how many daily EMs price has travelled from that 9:30 open. Ranked by who's moved furthest. R/S = daily EM levels. "paste" = daily/weekly/monthly numbers for the indicator. Free delayed data (~15 min). Reloads every 60s.</div>
<script>
function bar(sd){
  const clamped=Math.max(-2,Math.min(2,sd));
  const pct=(clamped+2)/4*100;
  const col=sd>=0?'#00ff88':'#ff4466';
  const from=sd>=0?50:pct, w=Math.abs(pct-50);
  return '<div class=bar>'
    +'<div class=mark style="left:50%"></div>'
    +'<div class=mark style="left:25%"></div>'
    +'<div class=mark style="left:75%"></div>'
    +'<div class=fill style="margin-left:'+from+'%;width:'+w+'%;background:'+col+'"></div>'
    +'</div>';
}
async function go(){
  let d; try{ d=await (await fetch('/api/four')).json(); }catch(e){ return; }
  if(d.error){document.getElementById('regime').textContent='data error';return;}
  document.getElementById('regime').textContent=d.regime;
  document.getElementById('asof').textContent='as of '+d.asOf;
  let h='';
  d.rows.forEach((r,i)=>{
    if(r.error){ h+='<div class=card><span class=code>'+r.code+'</span> <span class=name>'+r.name+' — '+r.error+'</span></div>'; return; }
    const cl=r.dir>0?'up':'dn';
    const arrow=r.dir>0?'▲':'▼';
    const staleBadge = r.stale ? '<div class=stale>STALE · '+r.anchorDate+'</div>' : '';
    h+='<div class="card'+(i===0?' lead':'')+'">'
      +'<div class=rank>#'+(i+1)+(i===0?' LEADER':'')+'</div>'
      +staleBadge
      +'<div class=top><div><span class=code>'+r.code+'</span><span class=name>'+r.name+'</span></div>'
      +'<div class="sig '+cl+'">'+arrow+' '+Math.abs(r.sigD).toFixed(2)+'σ</div></div>'
      +bar(r.sigD)
      +'<div class=status>'+r.status+'</div>'
      +'<div class=meta>'
      +'<span><b>'+r.price.toLocaleString()+'</b> px</span>'
      +'<span>9:30 open <b>'+r.open.toLocaleString()+'</b></span>'
      +'<span>wk <b>'+r.sigW.toFixed(2)+'σ</b></span>'
      +'<span>vol <b>'+(r.vol!=null?r.vol:'—')+'</b> ('+r.volSrc+')</span>'
      +'</div>'
      +'<div class=lvls>'
      +'<span class=r>R2 '+r.lvls.r2.toLocaleString()+'</span>'
      +'<span class=r>R1 '+r.lvls.r1.toLocaleString()+'</span>'
      +'<span class=s>S1 '+r.lvls.s1.toLocaleString()+'</span>'
      +'<span class=s>S2 '+r.lvls.s2.toLocaleString()+'</span>'
      +'</div>'
      +'<div class=em>paste → <span>daily <b>'+r.emD.toLocaleString()+'</b></span>'
      +'<span>weekly <b>'+r.emW.toLocaleString()+'</b></span>'
      +'<span>monthly <b>'+r.emM.toLocaleString()+'</b></span></div>'
      +'</div>';
  });
  document.getElementById('cards').innerHTML=h;
}
go(); setInterval(go,60000);
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE
