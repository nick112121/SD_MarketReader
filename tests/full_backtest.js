'use strict';
// Full indicator backtest on real historical data.
// Loads QQQ 5-min bars, runs the entire MarketReader through them,
// captures every fired trade + exit from the journal, reports stats.

const fs = require('fs');
const { MarketReader, makeData } = require('./harness');

const data = JSON.parse(fs.readFileSync('/tmp/qqq_merged.json', 'utf8'));
const bars = data.map((b, i) => ({
    t: new Date(b.date),
    idx: i,
    o: b.open, h: b.high, l: b.low, c: b.close, v: b.volume || 100,
}));
console.log(`Loaded ${bars.length} bars  (${bars[0].t.toISOString().slice(0,10)} → ${bars[bars.length-1].t.toISOString().slice(0,10)})`);

const mr = new MarketReader();
mr.props = { tradeNY:1, tradeEvening:1, tradeAsia:0, tradeLondon:0, simpleMode:1, hudScale:1 };
mr.init(mr.props);

const t0 = Date.now();
let lastPct = -1;
for (let i = 0; i < bars.length; i++) {
    const b = bars[i];
    mr.map(makeData({
        ts: b.t, idx: i,
        o: b.o, h: b.h, l: b.l, c: b.c, v: b.v,
        ov: b.v * 0.5, bv: b.v * 0.5,   // synthetic ask/bid split (no bid/ask in feed)
        isLast: i === bars.length - 1,
    }));
    const pct = Math.floor((i / bars.length) * 10) * 10;
    if (pct !== lastPct) {
        process.stdout.write(`  ${pct}%…\r`);
        lastPct = pct;
    }
}
const ms = Date.now() - t0;
console.log(`Processed in ${(ms/1000).toFixed(1)}s\n`);

// ── ANALYSE STATESTATS (the persistent source of truth) ───────────────────
// The journal is capped at 500 events, so its EXIT count understates the
// real trade count. stateStats survives across the full run and aggregates
// every trade closed. Reconstruct W/L from there.
const stateStats = mr.stateStats || {};
let _totW = 0, _totL = 0, _totPnl = 0;
for (const st in stateStats) {
    for (const tag in stateStats[st]) {
        _totW += stateStats[st][tag].w;
        _totL += stateStats[st][tag].l;
        _totPnl += stateStats[st][tag].pnl;
    }
}
const exitsCount = _totW + _totL;
const exits = mr.journal.filter(e => e.type === 'EXIT'); // for per-day/per-setup tail view
const fires = mr.journal.filter(e => e.type === 'FIRED');
const blocks = mr.journal.filter(e => e.type === 'BLOCKED');
const misses = mr.journal.filter(e => e.type === 'MISSED');

console.log('═══════════════════════════════════════════════════════════════════');
console.log('FULL INDICATOR BACKTEST — QQQ 5-min, 3.5 years');
console.log('═══════════════════════════════════════════════════════════════════');
console.log(`Bars:    ${bars.length}`);
console.log(`Total exits (stateStats): ${exitsCount}`);
console.log(`Journal-visible fired:    ${fires.length} (capped at 500)`);
console.log(`Journal-visible exits:    ${exits.length}`);
console.log(`Journal-visible blocked:  ${blocks.length}`);
console.log(`Journal-visible missed:   ${misses.length}`);
console.log('───────────────────────────────────────────────────────────────────');

if (exits.length === 0) {
    console.log('⚠  No trades fired — indicator never found a setup matching all filters.');
    console.log('   Common causes: framework agreement bar too high, regime filters always blocking,');
    console.log('   or data type mismatch (5-min bars vs the tick-based defaults the indicator tunes for).');
    process.exit(0);
}

// ── PERFORMANCE METRICS (from stateStats — full dataset) ──────────────────
// We have aggregate per-(state,setup) but no per-trade breakdown for the
// full set. Reconstruct totals; use journal exits for tail metrics (maxW/L).
let w = _totW, l = _totL;
let sumWPnl = 0, sumLPnl = 0, maxW = 0, maxL = 0;
for (const st in stateStats) {
    for (const tag in stateStats[st]) {
        const s = stateStats[st][tag];
        // Approximate gross win/loss split from win-rate of bucket
        // (pnl is net; we don't have per-trade granularity here)
        // Better: estimate from journal where we have per-trade.
    }
}
// Approximate avgW/avgL using journal exits where available
let jW = 0, jL = 0, jWp = 0, jLp = 0;
for (const e of exits) {
    if (e.win) { jW++; jWp += e.pnl; if (e.pnl > maxW) maxW = e.pnl; }
    else       { jL++; jLp += e.pnl; if (e.pnl < maxL) maxL = e.pnl; }
}
const avgW = jW > 0 ? (jWp / jW) : 0;
const avgL = jL > 0 ? (jLp / jL) : 0;
// Project totals using avgW/avgL × full counts (best estimate without per-trade pnl)
sumWPnl = avgW * w;
sumLPnl = avgL * l;
const tot = w + l;
const wr = tot > 0 ? (w/tot*100) : 0;
const grossW = sumWPnl, grossL = Math.abs(sumLPnl);
const pf = grossL > 0 ? (grossW/grossL) : Infinity;
const expectancy = tot > 0 ? ((sumWPnl + sumLPnl) / tot) : 0;
const netPts = _totPnl;  // use direct sum from stateStats
const netDollars = netPts * (mr.autoContractValue || 2);

console.log('PERFORMANCE');
console.log('───────────────────────────────────────────────────────────────────');
console.log(`Total trades:      ${tot}`);
console.log(`Wins / Losses:     ${w} / ${l}`);
console.log(`Win rate:          ${wr.toFixed(1)}%`);
console.log(`Avg winner:        ${avgW.toFixed(2)} pts`);
console.log(`Avg loser:         ${avgL.toFixed(2)} pts`);
console.log(`Expectancy:        ${expectancy.toFixed(2)} pts/trade`);
console.log(`Profit factor:     ${pf.toFixed(2)}`);
console.log(`Largest winner:    ${maxW.toFixed(2)} pts`);
console.log(`Largest loser:     ${maxL.toFixed(2)} pts`);
console.log(`Net (pts):         ${netPts >= 0 ? '+' : ''}${netPts.toFixed(1)}`);
console.log(`Net ($ @ MNQ $2):  ${netDollars >= 0 ? '+$' : '-$'}${Math.abs(Math.round(netDollars))}`);

// ── BLOCK REASON HISTOGRAM ─────────────────────────────────────────────────
const blockHist = {};
for (const b of blocks) blockHist[b.reason || 'UNKNOWN'] = (blockHist[b.reason || 'UNKNOWN'] || 0) + 1;
const sortedBlocks = Object.entries(blockHist).sort((a,b) => b[1] - a[1]);
console.log('\nBLOCK REASONS (top 10)');
console.log('───────────────────────────────────────────────────────────────────');
for (const [k, v] of sortedBlocks.slice(0, 10)) {
    console.log(`  ${k.padEnd(20)} ${v}`);
}

// ── PER-SETUP (LEVEL TYPE) ─────────────────────────────────────────────────
const perSetup = {};
for (const e of exits) {
    const tag = e.setupTag || e.level || 'OTHER';
    if (!perSetup[tag]) perSetup[tag] = { w:0, l:0, pnl:0 };
    perSetup[tag][e.win ? 'w' : 'l']++;
    perSetup[tag].pnl += e.pnl;
}
const sortedSetups = Object.entries(perSetup).sort((a,b) => b[1].pnl - a[1].pnl);
if (sortedSetups.length > 0) {
    console.log('\nPER SETUP / LEVEL TYPE');
    console.log('───────────────────────────────────────────────────────────────────');
    for (const [tag, s] of sortedSetups) {
        const ttot = s.w + s.l;
        const tWr = ttot > 0 ? (s.w/ttot*100).toFixed(0) : '--';
        console.log(`  ${String(tag).padEnd(24)} ${s.w}W/${s.l}L (${tWr}%)  ${s.pnl>=0?'+':''}${s.pnl.toFixed(1)}pt`);
    }
}

// ── PER MARKET STATE ───────────────────────────────────────────────────────
const stKeys = Object.keys(stateStats);
if (stKeys.length > 0) {
    console.log('\nPER STATE × SETUP');
    console.log('───────────────────────────────────────────────────────────────────');
    for (const st of stKeys) {
        for (const tag in stateStats[st]) {
            const s = stateStats[st][tag];
            const ttot = s.w + s.l;
            if (ttot < 3) continue;
            const tWr = (s.w/ttot*100).toFixed(0);
            console.log(`  ${st.padEnd(14)} ${tag.padEnd(10)} ${s.w}W/${s.l}L (${tWr}%)  ${s.pnl>=0?'+':''}${s.pnl.toFixed(1)}pt`);
        }
    }
}

// ── DAY-BY-DAY ROLLUP ──────────────────────────────────────────────────────
const perDay = {};
for (const e of exits) {
    const day = (e.session || '').slice(0, 10);
    if (!perDay[day]) perDay[day] = { trades:0, w:0, l:0, pnl:0 };
    perDay[day].trades++;
    perDay[day][e.win ? 'w' : 'l']++;
    perDay[day].pnl += e.pnl;
}
const dayKeys = Object.keys(perDay).sort();
const profitableDays = dayKeys.filter(d => perDay[d].pnl > 0).length;
const losingDays = dayKeys.filter(d => perDay[d].pnl < 0).length;
const flatDays = dayKeys.filter(d => perDay[d].pnl === 0).length;
console.log('\nDAY ROLLUP');
console.log('───────────────────────────────────────────────────────────────────');
console.log(`Active days:       ${dayKeys.length}`);
console.log(`Profitable days:   ${profitableDays} (${(profitableDays/dayKeys.length*100).toFixed(0)}%)`);
console.log(`Losing days:       ${losingDays}`);
console.log(`Flat days:         ${flatDays}`);
const dayPnls = dayKeys.map(d => perDay[d].pnl);
if (dayPnls.length > 0) {
    const maxDayWin = Math.max(...dayPnls);
    const maxDayLoss = Math.min(...dayPnls);
    console.log(`Best day:          ${maxDayWin>=0?'+':''}${maxDayWin.toFixed(1)}pt`);
    console.log(`Worst day:         ${maxDayLoss.toFixed(1)}pt`);
}

// ── EQUITY CURVE & DRAWDOWN ────────────────────────────────────────────────
let eq = 0, peak = 0, maxDD = 0;
const equity = [];
for (const e of exits) {
    eq += e.pnl;
    if (eq > peak) peak = eq;
    const dd = peak - eq;
    if (dd > maxDD) maxDD = dd;
    equity.push(eq);
}
console.log(`Max drawdown:      ${maxDD.toFixed(1)} pts`);

console.log('\n═══════════════════════════════════════════════════════════════════');
console.log('VERDICT');
console.log('═══════════════════════════════════════════════════════════════════');
// Use actual P&L from stateStats (truth) not avgW×count estimate
const realExpectancy = tot > 0 ? (netPts / tot) : 0;
if (tot < 30) {
    console.log('Sample size too small (<30 trades) for confidence.');
} else if (realExpectancy > 0 && wr > 50) {
    console.log('✓ POSITIVE EXPECTANCY — system shows edge across full dataset.');
    console.log('  Per-trade: +'+realExpectancy.toFixed(3)+' pts');
    console.log('  Across '+tot+' trades over '+(bars.length/78/250).toFixed(1)+' years equivalent');
} else if (netPts > 0) {
    console.log('~ Net positive but flat per-trade — small edge.');
} else {
    console.log('✗ Net negative. Filters or strategy need adjustment.');
}
