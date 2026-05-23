# SD_MarketReader

Tools for reading the market and finding trades faster.

## Markov Regime Dashboard

A web dashboard that scores a basket of assets by **market regime** so you can
spot setups without scanning every chart in TradingView by hand.

For each asset and timeframe it:

1. Pulls OHLC data from Yahoo Finance (`yfinance`).
2. Labels every bar as **Bear / Sideways / Bull** from its log-return over a
   lookback window.
3. Builds a **Markov transition matrix** of regime → regime probabilities and
   its stationary distribution.
4. Emits a **LONG / SHORT / FLAT** signal from the next-bar bull-vs-bear edge.
5. Backtests that signal with 3×ATR take-profit / stop-loss to show a win rate,
   trade count and modelled P&L.

Assets span Forex, US Indices, US Sectors, Global Indices and Commodities,
across the `15m`, `1h`, `4h` and `1d` timeframes. Data is cached for 60 minutes
and refreshed in the background.

### Run locally

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

Then open http://127.0.0.1:8000. The first load takes ~30s while the cache
fills.

### Deploy

`runtime.txt` pins Python 3.12 and `Procfile` defines the web process, so the
app deploys to any PaaS that supports them (Railway, Render, Heroku, etc.):

```
web: uvicorn main:app --host 0.0.0.0 --port $PORT
```

Data is delayed 15–20 min via Yahoo Finance. **Not financial advice.**

## SD_marketindicator

A Tradovate custom indicator that reads NQ order flow (sigma levels, liquidity
sweeps, price voids, VWAP) and flags absorption / exhaustion / sweep behaviours
at key levels.
