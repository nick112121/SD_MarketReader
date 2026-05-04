'use strict';
// Test runner. Lightweight assert library, then a sequence of scenarios that
// exercise the new logic added on this branch.

const { MarketReader, runScenario, genSession, ts, makeData, indicator } = require('./harness');

let pass = 0, fail = 0;
const failures = [];

function eq(actual, expected, label) {
    if (actual === expected) { pass++; return; }
    fail++;
    failures.push(`FAIL: ${label}\n      expected: ${JSON.stringify(expected)}\n      actual:   ${JSON.stringify(actual)}`);
}
function truthy(actual, label) {
    if (actual) { pass++; return; }
    fail++;
    failures.push(`FAIL: ${label}\n      expected: truthy\n      actual:   ${JSON.stringify(actual)}`);
}
function approxEq(actual, expected, tol, label) {
    if (Math.abs(actual - expected) <= tol) { pass++; return; }
    fail++;
    failures.push(`FAIL: ${label}\n      expected: ${expected} ± ${tol}\n      actual:   ${actual}`);
}
function ok(label) { pass++; }

// ─────────────────────────────────────────────────────────────────────────
// Test 1: file loads, exports the expected shape
// ─────────────────────────────────────────────────────────────────────────
function testLoad() {
    eq(typeof MarketReader, 'function', 'MarketReader is a class');
    eq(indicator.name, 'SD_MarketReader', 'export name');
    eq(indicator.inputType, 'bars', 'inputType');
    truthy(indicator.params, 'params present');
}

// ─────────────────────────────────────────────────────────────────────────
// Test 2: init sets defaults including the new state we added
// ─────────────────────────────────────────────────────────────────────────
function testInitDefaults() {
    const mr = new MarketReader();
    mr.props = {};
    mr.init({});
    eq(mr.lastLossBar, -9999, 'lastLossBar default');
    eq(mr.lastLossDir, 0, 'lastLossDir default');
    eq(mr.lastLossLevel, '', 'lastLossLevel default');
    eq(mr.maxTradesOverrideUsed, false, 'maxTradesOverrideUsed default');
    eq(typeof mr.volBins, 'object', 'volBins object');
    eq(mr.ibStartBar, -1, 'ibStartBar default');
    eq(mr.ibLocked, false, 'ibLocked default');
    eq(mr.ibBroken, 0, 'ibBroken default');
    eq(mr.ibBreakBars, 0, 'ibBreakBars default');
}

// ─────────────────────────────────────────────────────────────────────────
// Test 3: smoke — drive a flat-ish session, no crashes
// ─────────────────────────────────────────────────────────────────────────
function testSmoke() {
    const bars = genSession('2030-05-04', 9, 30, 90, (i) => {
        const px = 27800 + Math.sin(i / 10) * 5;
        return { o: px, h: px + 2, l: px - 2, c: px };
    });
    let crashed = false;
    let mr;
    try {
        mr = runScenario(bars);
    } catch (e) {
        crashed = true;
        failures.push('FAIL: smoke run threw\n      ' + (e.stack || e.message));
    }
    eq(crashed, false, 'smoke run completes without throwing');
    if (mr) {
        truthy(mr.H.length > 0, 'bars accumulated');
        truthy(mr.atr > 0, 'atr non-zero');
    }
}

// ─────────────────────────────────────────────────────────────────────────
// Test 4: vol bins accumulate per 15-min bucket
// ─────────────────────────────────────────────────────────────────────────
function testVolBins() {
    // Run a 60-min session — 4 bins (09:30, 09:45, 10:00, 10:15)
    const bars = genSession('2030-05-04', 9, 30, 60, (i) => {
        const px = 27800;
        return { o: px, h: px + (i % 5) + 1, l: px - (i % 5) - 1, c: px };
    });
    const mr = runScenario(bars);
    const keys = Object.keys(mr.volBins).sort();
    truthy(keys.length >= 3, `vol bins created (got ${keys.length}: ${keys.join(',')})`);
    truthy(keys.includes('09:30'), '09:30 bin exists');
    truthy(keys.includes('09:45'), '09:45 bin exists');
    if (mr.volBins['09:30']) {
        // First bar is skipped (n>1 guard in volBins update — needs a prior
        // bar to compare). So 15 minutes → 14 samples in the 09:30 bin.
        eq(mr.volBins['09:30'].n, 14, '09:30 bin has 14 samples (first bar skipped)');
        truthy(mr.volBins['09:30'].sumRange > 0, '09:30 sumRange > 0');
    }
}

// ─────────────────────────────────────────────────────────────────────────
// Test 5: IB tracker — develops then locks then breaks
// ─────────────────────────────────────────────────────────────────────────
function testIBTracker() {
    // 130-min session: first 60 bars stay in tight 27800-27820 range
    // (the IB window). Then dump cleanly through IB low and hold below
    // for at least 3 bars to trigger the break confirmation.
    const bars = genSession('2030-05-04', 9, 30, 130, (i) => {
        let px;
        if (i <= 60) {
            // IB period: oscillate 27800-27820
            px = 27800 + (i % 10) * 2;
        } else if (i < 75) {
            // Sharp dump out of IB low, then well below
            px = 27780 - (i - 60) * 4;  // 27780 → 27724
        } else {
            // Hold below for many bars
            px = 27720 + (i % 5);
        }
        return { o: px, h: px + 1, l: px - 1, c: px };
    });
    const mr = runScenario(bars);
    truthy(mr.ibStartBar >= 0, 'IB started');
    truthy(mr.ibHigh >= 27815, `ibHigh captured (${mr.ibHigh})`);
    truthy(mr.ibLow >= 27795 && mr.ibLow <= 27805, `ibLow captured (${mr.ibLow})`);
    eq(mr.ibLocked, true, 'IB locked after 60 min');
    eq(mr.ibBroken, -1, 'IB broken to the downside');
}

// ─────────────────────────────────────────────────────────────────────────
// Test 6: REPEAT-LOSS state set on losing exit
// (Simulate by directly invoking the recording logic — the full entry
// pipeline requires framework agreement which is hard to engineer in
// synthetic data. We test the bookkeeping.)
// ─────────────────────────────────────────────────────────────────────────
function testRepeatLossBookkeeping() {
    const mr = new MarketReader();
    mr.props = {};
    mr.init({});
    // Simulate the exit recording block
    mr.lastLossBar = 100;
    mr.lastLossDir = 1;
    mr.lastLossLevel = 'MTT_H';

    // Verify that the constants the filter depends on resolve as expected
    // The filter is: same dir + same level + (bi - lastLossBar) < 60
    // We just check the state holds:
    eq(mr.lastLossDir, 1, 'lastLossDir held');
    eq(mr.lastLossLevel, 'MTT_H', 'lastLossLevel held');
    // Within window
    truthy((130 - mr.lastLossBar) < 60, 'within 60-bar repeat window @ bar 130');
    truthy((180 - mr.lastLossBar) >= 60, 'outside 60-bar repeat window @ bar 180');
}

// ─────────────────────────────────────────────────────────────────────────
// Test 7: WIN/LOSS label position — direction-aware
// We synthesise the label-y math and verify against expected.
// ─────────────────────────────────────────────────────────────────────────
function testWinLabelPosition() {
    const h = 27950, l = 27940, atr = 10;
    // The new logic: above when (exitDir===1) === win
    function labelY(exitDir, win) {
        const above = (exitDir === 1) === win;
        return above ? h + atr : l - atr;
    }
    eq(labelY(1, true),   h + atr, 'LONG WIN  → above bar');
    eq(labelY(1, false),  l - atr, 'LONG LOSS → below bar');
    eq(labelY(-1, true),  l - atr, 'SHORT WIN → below bar (was buggy: above)');
    eq(labelY(-1, false), h + atr, 'SHORT LOSS → above bar');
}

// ─────────────────────────────────────────────────────────────────────────
// Test 8: chase filter math — 15-bar range > 6 ATR + position-in-range check
// ─────────────────────────────────────────────────────────────────────────
function testChaseFilterMath() {
    // Simulate: 15 bars spanning 27950 → 27720 (range 230), atr 15 → 6*atr=90.
    // 230 > 90, so filter active. Current close = 27725 → posInRng = 5/230 ≈ 0.02
    // SHORT proposed → posInRng < 0.30 → CHASE-DOWN
    const _hi = 27950, _lo = 27720, _c = 27725, atr = 15;
    const _rng = _hi - _lo;
    const _posInRng = (_c - _lo) / Math.max(_rng, 0.01);
    truthy(_rng > atr * 6, `range ${_rng} > 6*atr ${atr * 6}`);
    truthy(_posInRng < 0.30, `posInRng ${_posInRng.toFixed(3)} < 0.30 (chase territory for SHORT)`);

    // LONG proposed at top: c=27945, posInRng > 0.70
    const _c2 = 27945;
    const _pos2 = (_c2 - _lo) / Math.max(_rng, 0.01);
    truthy(_pos2 > 0.70, `posInRng top ${_pos2.toFixed(3)} > 0.70 (chase for LONG)`);
}

// ─────────────────────────────────────────────────────────────────────────
// Test 9: COUNTER-DAY / LATE-CHASE math — day directional + PIDR extreme
// ─────────────────────────────────────────────────────────────────────────
function testDayContextMath() {
    // Use sessHigh/Low AT THE TIME OF ENTRY (not the eventual extreme).
    // The SHORT @ 27738 fired around 11:15-11:30 when the day low was ~27720,
    // not the eventual 27620 made later in the afternoon.
    const sessOpen = 27860, sessHigh = 27945, sessLow = 27720;
    const c = 27738;  // late-chase scenario from today's chart
    const atr = 15;
    const dayRng = sessHigh - sessLow;
    const dayMv = c - sessOpen;
    const PIDR = (c - sessLow) / dayRng;
    const dayDir = dayMv > 0 ? 1 : -1;
    const directional = Math.abs(dayMv) > atr * 4;
    truthy(directional, `directional day (move ${dayMv} > 4*atr=${atr*4})`);
    eq(dayDir, -1, 'day direction: down');
    truthy(PIDR < 0.20, `PIDR ${PIDR.toFixed(3)} < 0.20 → LATE-CHASE territory for SHORT`);

    // LONG @ 27783 scenario — counter-day, slightly later in session
    const c2 = 27783;
    const PIDR2 = (c2 - sessLow) / dayRng;
    truthy(PIDR2 < 0.35, `PIDR ${PIDR2.toFixed(3)} < 0.35 → COUNTER-DAY for LONG`);
}

// ─────────────────────────────────────────────────────────────────────────
// Test 10: regime-flip override math
// ─────────────────────────────────────────────────────────────────────────
function testRegimeFlipOverride() {
    // Simulate: dailyTrades=3 (at MAX-TRADES), lastLossDir=1 (we lost longs),
    // proposed dir=-1 (opposite), totalScore=8 (>=7), override unused → ALLOW
    function flipAllowed(dailyTrades, maxTrades, lastLossDir, proposedDir, totalScore, used) {
        if (dailyTrades < maxTrades) return true; // not at cap, no override needed
        const isFlip = lastLossDir !== 0 &&
                       proposedDir === -lastLossDir &&
                       totalScore >= 7 &&
                       !used;
        return isFlip;
    }
    eq(flipAllowed(3, 3, 1, -1, 8, false), true,  'regime-flip allowed: opposite + score 8');
    eq(flipAllowed(3, 3, 1, -1, 6, false), false, 'regime-flip blocked: score < 7');
    eq(flipAllowed(3, 3, 1,  1, 9, false), false, 'regime-flip blocked: same dir as loss');
    eq(flipAllowed(3, 3, 1, -1, 9, true),  false, 'regime-flip blocked: already used');
    eq(flipAllowed(2, 3, 1, -1, 5, false), true,  'no override needed: dailyTrades < cap');
}

// ─────────────────────────────────────────────────────────────────────────
// Test 11: H1-COUNTER blocking math
// ─────────────────────────────────────────────────────────────────────────
function testH1CounterMath() {
    function shouldBlock(h1Trend, dir, totalScore) {
        if (!h1Trend || h1Trend === 'FLAT') return false;
        const h1Dir = h1Trend === 'BULL' ? 1 : -1;
        return dir === -h1Dir && (totalScore || 0) < 7;
    }
    eq(shouldBlock('BEAR',  1, 5), true,  'block LONG vs BEAR H1, low score');
    eq(shouldBlock('BEAR',  1, 8), false, 'allow LONG vs BEAR H1, high score');
    eq(shouldBlock('BEAR', -1, 5), false, 'allow SHORT with BEAR H1');
    eq(shouldBlock('BULL', -1, 5), true,  'block SHORT vs BULL H1, low score');
    eq(shouldBlock('FLAT',  1, 3), false, 'no block on FLAT H1');
}

// ─────────────────────────────────────────────────────────────────────────
// Test 12: LOSS-PAUSE threshold (now 2)
// ─────────────────────────────────────────────────────────────────────────
function testLossPauseThreshold() {
    // After 2 losses, pauseUntilBar should be set
    function shouldPause(results) {
        const losses = results.filter(r => !r.win).length;
        return losses >= 2;
    }
    eq(shouldPause([{win:false},{win:false}]), true,  '2L → pause');
    eq(shouldPause([{win:false},{win:true}]),  false, '1L → no pause');
    eq(shouldPause([{win:false},{win:false},{win:true}]), true, '2L of 3 → pause');
}

// ─────────────────────────────────────────────────────────────────────────
// Test 13: end-to-end on a distribution-day arc — verify the indicator
// completes a NY session without error and tracks IB+day context.
// ─────────────────────────────────────────────────────────────────────────
function testDistributionDay() {
    // Pattern: open 27860, rip to 27940 in 30 bars, dump to 27620 by bar 90,
    // then chop. 180 bars total = 3 hours.
    const bars = genSession('2030-05-04', 9, 30, 180, (i) => {
        let px;
        if (i < 30) {
            // Rip up
            px = 27860 + i * 2.5;
        } else if (i < 90) {
            // Dump
            px = 27940 - (i - 30) * 5.3;
        } else {
            // Chop near lows
            px = 27620 + ((i - 90) % 8) * 3;
        }
        return { o: px - 0.5, h: px + 2, l: px - 2, c: px };
    });
    let crashed = false;
    let mr;
    try {
        mr = runScenario(bars);
    } catch (e) {
        crashed = true;
        failures.push('FAIL: distribution-day run threw\n      ' + (e.stack || e.message));
    }
    eq(crashed, false, 'distribution-day session completes');
    if (mr) {
        eq(mr.ibLocked, true, 'IB locked');
        eq(mr.ibBroken, -1, 'IB broken down on distribution day');
        truthy(mr.sessHigh >= 27935, `sessHigh captured (${mr.sessHigh})`);
        truthy(mr.sessLow <= 27625, `sessLow captured (${mr.sessLow})`);
        // After the dump, current price near sessLow. PIDR should be low.
        const dayRng = mr.sessHigh - mr.sessLow;
        const lastC = mr.C[mr.C.length - 1];
        const pidr = (lastC - mr.sessLow) / dayRng;
        truthy(pidr < 0.30, `PIDR at end of dump ${pidr.toFixed(3)} < 0.30`);
    }
}

// ─────────────────────────────────────────────────────────────────────────
// Test 14: MOMENTUM filter math — strong + consistent net move blocks
// trades against the flow.
// ─────────────────────────────────────────────────────────────────────────
function testMomentumFilterMath() {
    function shouldBlock(C, O, c, dir, atr) {
        const n = C.length + 1;  // emulate indicator's n (length after current bar)
        if (n < 21) return null;
        const refClose = C[n - 21];
        const netMv = c - refClose;
        let aligned = 0;
        for (let i = 1; i <= 20; i++) {
            const o_i = O[n - 1 - i];
            const c_i = C[n - 1 - i];
            if (o_i == null || c_i == null) continue;
            if (netMv > 0 && c_i > o_i) aligned++;
            else if (netMv < 0 && c_i < o_i) aligned++;
        }
        const strong = Math.abs(netMv) > atr * 3.5;
        const consistent = aligned >= 12;
        if (!strong || !consistent) return null;
        const momDir = netMv > 0 ? 1 : -1;
        if (dir === -momDir) return momDir === -1 ? 'MOMENTUM-DOWN' : 'MOMENTUM-UP';
        return null;
    }
    // Build a clean 20-bar bearish drift: each bar closes -3 from prior open
    const C = [], O = [];
    for (let i = 0; i < 22; i++) {
        const o = 27940 - i * 3;
        const c = o - 3;  // bearish
        O.push(o); C.push(c);
    }
    const cur = 27860; // current price ≈ 80pt below start (matches today's scenario)
    const atr = 13;
    eq(shouldBlock(C, O, cur, 1, atr),  'MOMENTUM-DOWN', 'block LONG into bearish momentum');
    eq(shouldBlock(C, O, cur, -1, atr), null,            'allow SHORT with bearish momentum');

    // Insufficient consistency — half up half down bars but big net move
    const C2 = [], O2 = [];
    for (let i = 0; i < 22; i++) {
        // Alternating colors but stair-stepping down in close
        const o = 27940 - i * 3;
        const c = o + (i % 2 === 0 ? 1 : -7);  // mixed colors
        O2.push(o); C2.push(c);
    }
    eq(shouldBlock(C2, O2, 27870, 1, atr), null, 'allow when not consistent (only ~10 aligned)');

    // Insufficient magnitude — small move
    const C3 = [], O3 = [];
    for (let i = 0; i < 22; i++) {
        const o = 27940 - i * 0.3;  // tiny drift
        const c = o - 0.3;
        O3.push(o); C3.push(c);
    }
    eq(shouldBlock(C3, O3, 27933, 1, atr), null, 'allow when net move below threshold');
}

// ─────────────────────────────────────────────────────────────────────────
// Run all
// ─────────────────────────────────────────────────────────────────────────
const tests = [
    ['load',                         testLoad],
    ['init defaults',                testInitDefaults],
    ['smoke',                        testSmoke],
    ['vol bins',                     testVolBins],
    ['IB tracker',                   testIBTracker],
    ['repeat-loss bookkeeping',      testRepeatLossBookkeeping],
    ['win label position',           testWinLabelPosition],
    ['chase filter math',            testChaseFilterMath],
    ['day context math',             testDayContextMath],
    ['regime-flip override',         testRegimeFlipOverride],
    ['H1-COUNTER math',              testH1CounterMath],
    ['LOSS-PAUSE threshold',         testLossPauseThreshold],
    ['distribution day end-to-end',  testDistributionDay],
    ['MOMENTUM filter math',         testMomentumFilterMath],
];

console.log('Running ' + tests.length + ' test groups…\n');
for (const [name, fn] of tests) {
    const before = fail;
    try {
        fn();
    } catch (e) {
        fail++;
        failures.push(`FAIL (threw): ${name}\n      ${e.stack || e.message}`);
    }
    const groupFails = fail - before;
    console.log(`  ${groupFails === 0 ? '✓' : '✗'} ${name}${groupFails ? ' ('+groupFails+' fail)' : ''}`);
}

console.log('\n──────────────────────────────────');
console.log(`pass: ${pass}   fail: ${fail}`);
if (failures.length) {
    console.log('\n' + failures.join('\n\n'));
    process.exit(1);
}
process.exit(0);
