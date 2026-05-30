'use strict';
// Test harness: loads the indicator, mocks Tradovate's `d` (data) interface,
// drives the calculator with synthetic OHLC bars.

// The indicator file requires './tools/predef' etc. relative to its OWN
// location (the symlink resolves to the repo root). Intercept those resolves
// so they hit our stubs in tests/tools/.
const Module = require('module');
const path   = require('path');
const _origResolve = Module._resolveFilename;
const stubsDir = path.join(__dirname, 'tools');
Module._resolveFilename = function(request, parent, ...rest) {
    if (request === './tools/predef')   return path.join(stubsDir, 'predef.js');
    if (request === './tools/graphics') return path.join(stubsDir, 'graphics.js');
    if (request === './tools/plotting') return path.join(stubsDir, 'plotting.js');
    return _origResolve.call(this, request, parent, ...rest);
};

const indicator = require('./SD_marketindicator.js');
const { calculator: MarketReader } = indicator;

// Simulated bar data feeder. The real Tradovate `d` is per-bar; we instantiate
// once per bar update and supply current OHLCV/timestamp/index.
function makeData(bar) {
    return {
        open:        () => bar.o,
        high:        () => bar.h,
        low:         () => bar.l,
        close:       () => bar.c,
        volume:      () => bar.v != null ? bar.v : 100,
        offerVolume: () => bar.ov != null ? bar.ov : 50,
        bidVolume:   () => bar.bv != null ? bar.bv : 50,
        timestamp:   () => bar.ts,
        index:       () => bar.idx,
        isLast:      () => !!bar.isLast,
    };
}

// Build a UTC timestamp for a given (date string YYYY-MM-DD) + hh + mm.
// The indicator applies a tz offset of -4 (default) — feed it UTC.
function ts(dateStr, hh, mm) {
    const [y, m, d] = dateStr.split('-').map(Number);
    // We want local NY time hh:mm. Indicator default tz=-4 means it does
    // utc + (-4 * 3600000) to derive local. So if we want local 09:30, feed
    // UTC 13:30 (09:30 + 4).
    return new Date(Date.UTC(y, m - 1, d, hh + 4, mm, 0, 0));
}

// A scenario is a list of bars with derived timestamps. genSession generates
// a 1-min bar series for a given local-time window with a price arc.
//
// arc(barIdx, total) -> {o,h,l,c,v?,ov?,bv?}
function genSession(dateStr, startH, startM, count, arc) {
    const out = [];
    for (let i = 0; i < count; i++) {
        const totalMins = startM + i;
        const hh = startH + Math.floor(totalMins / 60);
        const mm = totalMins % 60;
        const ohlc = arc(i, count);
        out.push({
            ts: ts(dateStr, hh, mm),
            idx: i,
            ...ohlc,
            isLast: i === count - 1,
        });
    }
    return out;
}

// Drive the indicator through a sequence of bars, returning the instance
// for inspection.
function runScenario(bars, props) {
    const mr = new MarketReader();
    mr.props = props || {};
    mr.init(props || {});
    for (const bar of bars) {
        mr.map(makeData(bar));
    }
    return mr;
}

module.exports = { MarketReader, makeData, ts, genSession, runScenario, indicator };
