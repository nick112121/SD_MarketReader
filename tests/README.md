# Tests

Lightweight test harness for `SD_marketindicator`. The indicator depends on
Tradovate's `tools/predef`, `tools/graphics`, and `tools/plotting` modules,
which are stubbed in `tests/tools/`. The harness intercepts the require()
resolution so the unmodified production file can run under Node.

## Run

```
node tests/run-tests.js
```

Exits 0 on pass, 1 on any failure. Failure messages include expected vs.
actual.

## What's covered

- File loads and exports the expected shape
- `init()` sets the new state defaults (lastLoss*, ibStartBar, volBins, etc.)
- Smoke run through a synthetic NY session — no exceptions
- Volatility bin accumulation per 15-min bucket
- Initial Balance: develops, locks at 60 min, breaks down on a sustained
  3-bar close below IB low
- WIN/LOSS exit-label position math (direction-aware)
- Chase filter range/PIDR math
- Day-context (PIDR) math reproducing today's NY entries
- Regime-flip override gating logic
- H1-COUNTER threshold gating
- LOSS-PAUSE threshold (now 2-of-5)
- End-to-end distribution-day arc — IB breaks, sessHigh/Low captured

## Notes

- Bars use 2030 dates so the indicator's "live wall-clock" fallback (active
  when `|now - bar_ts| < 60 min`) doesn't override bar time.
- Filter logic that requires the full framework decision pipeline (AUCT/STR/
  FLOW agreement at a level) is tested via math-equivalent shadow functions
  rather than driving full decisions, since synthesising bars that satisfy
  all framework conditions is brittle.
