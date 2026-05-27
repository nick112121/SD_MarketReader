"""
4-Market Expected Move — a one-page "who's leading" monitor.

Tracks the four major index futures (ES, NQ, YM, RTY) and shows, for each,
how far it has travelled today relative to its own daily expected move, plus
where that sits against the weekly expected move. Ranked so the market that
has moved furthest relative to its EM (the leader) is on top.

Uses FREE delayed data (yfinance, ~15 min lag). That's fine here — this is a
positioning monitor, not an execution feed. Implied vol indices drive the EM
where Yahoo has them (ES=VIX, NQ=VXN, YM=VXD); RTY falls back to realized
vol because ^RVX is not available on Yahoo. Any market with a missing vol
index also falls back to realized vol automatically.

Local:   uvicorn four_em:app --reload   ->  http://127.0.0.1:8000
Deploy:  Render -> New Web Service -> this repo ->
         Start command:  uvicorn four_em:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import yfinance as yf
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI(title="4-Market Expected Move")

# code, futures symbol, implied-vol index, friendly name
MARKETS = [
    ("ES",  "ES=F",  "^VIX", "S&P 500"),
    ("NQ",  "NQ=F",  "^VXN", "Nasdaq 100"),
    ("YM",  "YM=F",  "^VXD", "Dow 30"),
    ("RTY", "RTY=F", "^RVX", "Russell 2000"),
]


def _daily_sigma_pct(close_series: pd.Series, vol_close):
    """Daily 1-sigma move as a fraction of price.

    Prefers the option-implied vol index (annual % -> daily); falls back to
    realized vol (stdev of recent daily log returns) when the index is
    missing (e.g. ^RVX).
    """
    if vol_close is not None and np.isfinite(vol_close) and vol_close > 0:
        return (vol_close / 100.0) / np.sqrt(252), "implied"
    rets = np.log(close_series).diff().dropna()
    if len(rets) >= 5:
        return float(rets.tail(20).std()), "realized"
    return 0.0, "n/a"


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
    fut = [m[1] for m in MARKETS]
    vix = [m[2] for m in MARKETS]
    df = yf.download(fut + vix, period="2mo", interval="1d",
                     group_by="ticker", progress=False, auto_adjust=True)
    rows = []
    for code, fsym, vsym, name in MARKETS:
        try:
            sub = df[fsym].dropna(subset=["Close"])
            cur = float(sub["Close"].iloc[-1])
            open_today = float(sub["Open"].iloc[-1])
            last_date = sub.index[-1]
            wk_start = (last_date - pd.Timedelta(days=int(last_date.weekday()))).normalize()
            wk = sub[sub.index >= wk_start]
            week_open = float(wk["Open"].iloc[0]) if len(wk) else open_today
            try:
                vc = float(df[vsym]["Close"].dropna().iloc[-1])
            except Exception:
                vc = None
            sig_pct, src = _daily_sigma_pct(sub["Close"], vc)
            em_d = cur * sig_pct
            em_w = em_d * np.sqrt(5)
            move = cur - open_today
            wk_move = cur - week_open
            sd = move / em_d if em_d else 0.0
            sw = wk_move / em_w if em_w else 0.0
            rows.append({
                "code": code, "name": name,
                "price": round(cur, 2), "open": round(open_today, 2),
                "weekOpen": round(week_open, 2),
                "emD": int(round(em_d)), "emW": int(round(em_w)),
                "moveD": round(move, 1),
                "sigD": round(sd, 2), "sigW": round(sw, 2),
                "pctD": int(round(abs(sd) * 100)),
                "dir": 1 if move >= 0 else -1,
                "vol": round(vc, 2) if vc else None, "volSrc": src,
                "status": _status(sd, sw),
            })
        except Exception as e:
            rows.append({"code": code, "name": name, "error": str(e)})

    ranked = sorted([r for r in rows if "sigD" in r],
                    key=lambda r: abs(r["sigD"]), reverse=True)
    errs = [r for r in rows if "sigD" not in r]

    # market-wide read: are the movers pulling the same way?
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
    return {"rows": ranked + errs, "regime": regime}


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
.regime{text-align:center;font-size:.85rem;font-weight:700;margin:0 0 16px;color:#ffcc44}
.card{background:#141414;border:1px solid #222;border-radius:12px;padding:12px 14px;margin:0 0 10px;position:relative}
.lead{border-color:#00ff88;box-shadow:0 0 0 1px #00ff8855}
.rank{position:absolute;top:10px;right:12px;font-size:.6rem;color:#666;letter-spacing:.1em}
.top{display:flex;justify-content:space-between;align-items:baseline}
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
.note{font-size:.62rem;color:#666;text-align:center;margin:14px 0;line-height:1.6}
</style></head><body>
<h1>4-Market Expected Move</h1>
<div class=regime id=regime>...</div>
<div id=cards></div>
<div class=note>Daily EM = today's 1σ move from the open. σ = how many daily EMs
price has travelled (1.0 = exactly at the daily expected move). Ranked by who's
moved furthest — the leader is on top. Free delayed data (~15 min). Reloads every 60s.</div>
<script>
function bar(sd){
  // map -2..+2 sigma to 0..100%, center at 50
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
  let h='';
  d.rows.forEach((r,i)=>{
    if(r.error){ h+='<div class=card><span class=code>'+r.code+'</span> <span class=name>'+r.name+' — '+r.error+'</span></div>'; return; }
    const cl=r.dir>0?'up':'dn';
    const arrow=r.dir>0?'▲':'▼';
    h+='<div class="card'+(i===0?' lead':'')+'">'
      +'<div class=rank>#'+(i+1)+(i===0?' LEADER':'')+'</div>'
      +'<div class=top><div><span class=code>'+r.code+'</span><span class=name>'+r.name+'</span></div>'
      +'<div class="sig '+cl+'">'+arrow+' '+Math.abs(r.sigD).toFixed(2)+'σ</div></div>'
      +bar(r.sigD)
      +'<div class=status>'+r.status+'</div>'
      +'<div class=meta>'
      +'<span><b>'+r.price.toLocaleString()+'</b> px</span>'
      +'<span>open <b>'+r.open.toLocaleString()+'</b></span>'
      +'<span>day EM <b>'+r.emD.toLocaleString()+'</b></span>'
      +'<span>wk EM <b>'+r.emW.toLocaleString()+'</b></span>'
      +'<span>wk <b>'+r.sigW.toFixed(2)+'σ</b></span>'
      +'<span>vol <b>'+(r.vol!=null?r.vol:'—')+'</b> ('+r.volSrc+')</span>'
      +'</div></div>';
  });
  document.getElementById('cards').innerHTML=h;
}
go(); setInterval(go,60000);
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE
