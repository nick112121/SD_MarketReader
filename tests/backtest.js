'use strict';
// Backtest harness: drives the indicator through multiple simulated day
// regimes and reports per-day diagnostics so we can see whether the filter
// stack is doing what we expect.
//
// We can't test fired-trade win rate end-to-end without engineering bars
// that satisfy the AUCT+STR+FLOW framework agreement at a level — that's
// brittle. Instead we measure what the indicator THINKS about each day:
//
//   - dayQuality at end of session
//   - blockReason distribution across the day
//   - IB status (broken / held)
//   - sessHigh / sessLow / sessOpen / dayMv
//   - Number of bars where the indicator considered itself "BLOCKED"
//
// A working model = on trend / hostile days, dayQuality stays low and
// blockReasons skew toward MOMENTUM / IB-COUNTER / DAY-QUALITY. On
// balance days, dayQuality stays >= 5 and blockReason is mostly NO-LEVEL
// (the natural "no setup right now" state).

const { MarketReader, runScenario, genSession, makeData } = require('./harness');

// ─── Day arc generators ─────────────────────────────────────────────────

// Balance day: oscillates 27795-27820 throughout
function balanceArc(i, total) {
    const px = 27800 + Math.sin(i / 8) * 12 + Math.cos(i / 13) * 6;
    return { o: px, h: px + 2, l: px - 2, c: px };
}

// Trend-down distribution day: rip up 27860→27940 in 30, then dump to
// 27620 by bar 90, chop near lows.
function distributionArc(i, total) {
    let px;
    if (i < 30) px = 27860 + i * 2.5;
    else if (i < 90) px = 27940 - (i - 30) * 5.3;
    else px = 27620 + ((i - 90) % 8) * 3;
    return { o: px - 0.5, h: px + 2, l: px - 2, c: px };
}

// Trend-up day: open 27800, rip steadily to 27950
function trendUpArc(i, total) {
    const px = 27800 + (i / total) * 150 + Math.sin(i / 5) * 4;
    return { o: px, h: px + 3, l: px - 1, c: px + 2 };
}

// Choppy day: high-frequency oscillation, no trend
function chopArc(i, total) {
    const px = 27810 + Math.sin(i / 3) * 18 + Math.cos(i / 5) * 8;
    return { o: px, h: px + 4, l: px - 4, c: px };
}

// Reversal day: trend down 0-50, then sharp reversal up 50-130
function reversalArc(i, total) {
    let px;
    if (i < 50) px = 27900 - i * 2;
    else px = 27800 + (i - 50) * 1.8;
    return { o: px, h: px + 2, l: px - 2, c: px };
}

const dayTypes = [
    { name: 'BALANCE',       arc: balanceArc,       count: 180 },
    { name: 'DISTRIBUTION',  arc: distributionArc,  count: 180 },
    { name: 'TREND-UP',      arc: trendUpArc,       count: 180 },
    { name: 'CHOP',          arc: chopArc,          count: 180 },
    { name: 'REVERSAL',      arc: reversalArc,      count: 130 },
];

function runDay(name, arc, count) {
    const bars = genSession('2030-05-04', 9, 30, count, arc);
    const mr = new MarketReader();
    mr.props = { tradeNY: 1, simpleMode: 1, hudScale: 1 };
    mr.init(mr.props);

    const blockHist = {};
    const dqHist = [];
    const stateHist = {};
    for (let i = 0; i < bars.length; i++) {
        mr.map(makeData(bars[i]));
        const br = mr.blockReason || 'CAN-FIRE';
        blockHist[br] = (blockHist[br] || 0) + 1;
        if (mr.dayQuality != null) dqHist.push(mr.dayQuality);
        const st = mr.marketState || 'NEUTRAL';
        stateHist[st] = (stateHist[st] || 0) + 1;
    }

    const lastC = mr.C[mr.C.length - 1];
    const dayMv = mr.sessOpen ? lastC - mr.sessOpen : 0;
    const finalDQ = mr.dayQuality != null ? mr.dayQuality : 5;
    const avgDQ = dqHist.length ? (dqHist.reduce((a, b) => a + b, 0) / dqHist.length) : 5;

    return {
        name,
        bars: count,
        sessOpen: mr.sessOpen,
        sessHigh: mr.sessHigh,
        sessLow: mr.sessLow,
        dayMv,
        ibLocked: mr.ibLocked,
        ibBroken: mr.ibBroken,
        finalDQ,
        avgDQ,
        blockHist,
        finalState: mr.marketState,
        stateHist,
    };
}

function pad(s, n) { s = String(s); return s + ' '.repeat(Math.max(0, n - s.length)); }

console.log('Backtest: indicator state across simulated regimes');
console.log('═'.repeat(80));
const results = dayTypes.map(d => runDay(d.name, d.arc, d.count));

console.log(pad('REGIME', 14) + pad('IB', 14) + pad('dayMv', 10) +
            pad('avgDQ', 8) + pad('Q', 4) + pad('STATE', 14) + 'top-block-reasons');
console.log('─'.repeat(95));
for (const r of results) {
    const ib = r.ibBroken === 1 ? 'IB↑broken' :
               r.ibBroken === -1 ? 'IB↓broken' :
               r.ibLocked ? 'IB·held' : 'IB·forming';
    const topReasons = Object.entries(r.blockHist)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 3)
        .map(([k, v]) => `${k}(${v})`)
        .join(' ');
    console.log(
        pad(r.name, 14) +
        pad(ib, 14) +
        pad(r.dayMv.toFixed(0), 10) +
        pad(r.avgDQ.toFixed(1), 8) +
        pad(r.finalDQ.toFixed(0), 4) +
        pad(r.finalState, 14) +
        topReasons
    );
}

console.log('\nExpected behavior:');
console.log('  BALANCE: IB·held, low |dayMv|, avgDQ >= 5, top-block = NO-LEVEL');
console.log('  DISTRIBUTION/TREND: IB↓/↑broken, large |dayMv|, avgDQ < 5,');
console.log('     top-block includes IB-COUNTER / DAY-QUALITY / MOMENTUM');
console.log('  CHOP: IB held or marginal, avgDQ moderate, NO-LEVEL dominant');
console.log('  REVERSAL: mixed — IB likely broken but DQ recovers if PIDR returns');

// ─── Validation: assert the regime detection actually works ────────────
let pass = 0, fail = 0;
const fails = [];
function check(cond, label) {
    if (cond) { pass++; return; }
    fail++; fails.push('  FAIL: ' + label);
}
const byName = Object.fromEntries(results.map(r => [r.name, r]));

console.log('\nAssertions:');
check(byName['BALANCE'].avgDQ >= 5,
    `BALANCE avgDQ >= 5 (got ${byName['BALANCE'].avgDQ.toFixed(2)})`);
check(byName['DISTRIBUTION'].ibBroken === -1,
    `DISTRIBUTION IB broken down (got ${byName['DISTRIBUTION'].ibBroken})`);
check(byName['DISTRIBUTION'].avgDQ < byName['BALANCE'].avgDQ,
    `DISTRIBUTION avgDQ < BALANCE avgDQ (${byName['DISTRIBUTION'].avgDQ.toFixed(2)} < ${byName['BALANCE'].avgDQ.toFixed(2)})`);
check(byName['TREND-UP'].ibBroken === 1,
    `TREND-UP IB broken up (got ${byName['TREND-UP'].ibBroken})`);
check(byName['TREND-UP'].dayMv > 100,
    `TREND-UP dayMv > 100 (got ${byName['TREND-UP'].dayMv.toFixed(0)})`);
check(byName['CHOP'].finalDQ >= 4,
    `CHOP finalDQ >= 4 (got ${byName['CHOP'].finalDQ})`);
// State visits — classifier should have seen the right regime at some point
check(byName['TREND-UP'].stateHist['TREND_UP'] >= 30,
    `TREND-UP visited TREND_UP state (got ${byName['TREND-UP'].stateHist['TREND_UP']||0} bars)`);
check(byName['DISTRIBUTION'].stateHist['TREND_DOWN'] >= 30,
    `DISTRIBUTION visited TREND_DOWN state during the dump (got ${byName['DISTRIBUTION'].stateHist['TREND_DOWN']||0} bars)`);
check((byName['BALANCE'].stateHist['RANGE'] || 0) >= 20,
    `BALANCE visited RANGE state (got ${byName['BALANCE'].stateHist['RANGE']||0} bars)`);

console.log(`\n  pass: ${pass}   fail: ${fail}`);
if (fails.length) {
    console.log(fails.join('\n'));
    process.exit(1);
}
process.exit(0);
