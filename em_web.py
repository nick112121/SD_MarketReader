"""
NQ Expected-Move — a one-page morning site.

Open the URL, read the daily expected move, paste it into the indicator's
expMoveDaily. Pulls VXN (NDX option-implied vol = the option chain's own
forecast) + NQ live each load. Mobile-friendly. Standalone — nothing to do
with the dashboard.

Local:   uvicorn em_web:app --reload    → http://127.0.0.1:8000
Deploy:  Render → New Web Service → this repo →
         Start command:  uvicorn em_web:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import numpy as np
import yfinance as yf
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI(title="NQ Expected Move")


def _em(price_override: float = 0.0) -> dict:
    px = yf.download(["NQ=F", "^VXN"], period="5d", interval="1d",
                     progress=False, auto_adjust=True)["Close"].dropna()
    px.columns = [str(c) for c in px.columns]
    nq_feed = float(px["NQ=F"].iloc[-1])
    vxn = float(px["^VXN"].iloc[-1])
    nq = price_override if price_override > 0 else nq_feed
    pct_d = (vxn / 100) * np.sqrt(1 / 252)
    em_d = nq * pct_d
    em_w = nq * (vxn / 100) * np.sqrt(5 / 252)
    em_m = nq * (vxn / 100) * np.sqrt(21 / 252)
    r = lambda x: int(round(x / 5.0) * 5)
    return {
        "nq": round(nq, 1), "nq_feed": round(nq_feed, 1), "vxn": round(vxn, 2),
        "pct_d": round(pct_d * 100, 2),
        "em_d": r(em_d), "em_w": r(em_w), "em_m": r(em_m),
        "levels": {
            "R2": round(nq + em_d, 1), "R1": round(nq + 0.5 * em_d, 1),
            "S1": round(nq - 0.5 * em_d, 1), "S2": round(nq - em_d, 1),
            "wk_up": round(nq + em_w, 1), "wk_dn": round(nq - em_w, 1),
            "mo_up": round(nq + em_m, 1), "mo_dn": round(nq - em_m, 1),
        },
    }


@app.get("/api/em")
def api_em(price: float = 0.0):
    try:
        return JSONResponse(_em(price))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)


PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>NQ Expected Move</title><style>
body{background:#0d0d0d;color:#eee;font-family:system-ui,Segoe UI,sans-serif;margin:0;padding:24px;text-align:center}
h1{font-size:.8rem;letter-spacing:.2em;color:#00ff88;text-transform:uppercase;margin:0 0 18px}
.big{background:#141414;border:1px solid #222;border-radius:14px;padding:22px;margin:0 auto 16px;max-width:460px}
.paste{font-size:2.6rem;font-weight:800;color:#00ff88;margin:6px 0}
.sub{font-size:.8rem;color:#888}
.row{display:flex;gap:8px;max-width:460px;margin:0 auto 16px}
.cell{flex:1;background:#141414;border:1px solid #222;border-radius:10px;padding:10px}
.cell .l{font-size:.55rem;color:#666;text-transform:uppercase;letter-spacing:.08em}
.cell .v{font-size:1rem;font-weight:700;margin-top:4px}
.up{color:#00ff88}.dn{color:#ff4466}
input{background:#141414;border:1px solid #333;border-radius:8px;color:#eee;padding:8px 10px;width:130px;text-align:right;font-size:1rem}
button{background:#00ff88;border:0;border-radius:8px;color:#000;font-weight:700;padding:9px 16px;margin-left:6px;cursor:pointer}
.note{font-size:.65rem;color:#666;max-width:460px;margin:14px auto;line-height:1.6}
.lvl{max-width:460px;margin:0 auto;text-align:left;font-size:.8rem}
.lvl div{display:flex;justify-content:space-between;padding:5px 12px;border-bottom:1px solid #1a1a1a}
</style></head><body>
<h1>NQ Expected Move</h1>
<div class=big>
  <div class=sub>paste into indicator → <b>expMoveDaily</b></div>
  <div class=paste id=paste>…</div>
  <div class=sub id=ctx></div>
</div>
<div class=row>
  <div class=cell><div class=l>Weekly ±1σ</div><div class=v id=emw>…</div></div>
  <div class=cell><div class=l>Monthly ±1σ</div><div class=v id=emm>…</div></div>
</div>
<div style="max-width:460px;margin:0 auto 8px">
  <input id=price type=number placeholder="your live NQ"><button onclick=go()>Use my price</button>
</div>
<div class=lvl id=lvl></div>
<div class=note>VXN = NDX option-implied vol (the chain's own forecast). If the NQ
price shown differs from your platform, type your live price above. Reload each
morning — it recomputes from live VXN.</div>
<script>
async function go(){
  const p=document.getElementById('price').value||0;
  const r=await fetch('/api/em?price='+p); const d=await r.json();
  if(d.error){document.getElementById('paste').textContent='data error';return;}
  document.getElementById('paste').textContent='expMoveDaily = '+d.em_d;
  document.getElementById('ctx').textContent='NQ '+d.nq.toLocaleString()+'  ·  VXN '+d.vxn+'  ·  '+d.pct_d+'%/day';
  document.getElementById('emw').textContent='± '+d.em_w;
  document.getElementById('emm').textContent='± '+d.em_m;
  const L=d.levels;
  document.getElementById('lvl').innerHTML=
    row('R2  +1.0σ',L.R2,'up')+row('R1  +0.5σ',L.R1,'up')+
    row('S1  −0.5σ',L.S1,'dn')+row('S2  −1.0σ',L.S2,'dn')+
    row('Week +1σ',L.wk_up,'up')+row('Week −1σ',L.wk_dn,'dn')+
    row('Month +1σ',L.mo_up,'up')+row('Month −1σ',L.mo_dn,'dn');
}
function row(n,v,c){return '<div><span>'+n+'</span><span class='+c+'>'+v.toLocaleString()+'</span></div>';}
go();
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE
