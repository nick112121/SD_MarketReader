'use strict';
// Pattern scanner — runs documented chart patterns against historical NQ 5-min
// bars and measures real edge (win rate + average favorable/adverse excursion
// over the next 20 bars).
//
// Output: ranked list of patterns by hit-rate × avg-MFE, with sample sizes.

const fs = require('fs');

// ── LOAD DATA ──────────────────────────────────────────────────────────────
const fileArg = process.argv[2] || '/tmp/qqq_merged.json';
const data = JSON.parse(fs.readFileSync(fileArg, 'utf8'));
const bars = [];
if (data.chart) {
    // Yahoo Finance format
    const r = data.chart.result[0];
    const q = r.indicators.quote[0];
    const ts = r.timestamp;
    for (let i = 0; i < ts.length; i++) {
        if (q.open[i] == null || q.close[i] == null) continue;
        bars.push({ t:ts[i], o:q.open[i], h:q.high[i], l:q.low[i], c:q.close[i], v:q.volume[i]||0 });
    }
} else {
    // Tiingo format — array of {date, open, high, low, close, volume}
    for (const b of data) {
        if (b.open == null || b.close == null) continue;
        bars.push({ t: new Date(b.date).getTime()/1000, o:b.open, h:b.high, l:b.low, c:b.close, v:b.volume||0 });
    }
}
console.log(`Loaded ${bars.length} bars (${new Date(bars[0].t*1000).toISOString().slice(0,10)} → ${new Date(bars[bars.length-1].t*1000).toISOString().slice(0,10)})`);

// ── HELPERS ────────────────────────────────────────────────────────────────
function atrAt(i, n = 14) {
    if (i < n) return 0;
    let sum = 0;
    for (let j = i - n + 1; j <= i; j++) {
        const tr = Math.max(
            bars[j].h - bars[j].l,
            j > 0 ? Math.abs(bars[j].h - bars[j-1].c) : 0,
            j > 0 ? Math.abs(bars[j].l - bars[j-1].c) : 0
        );
        sum += tr;
    }
    return sum / n;
}

function body(b)  { return Math.abs(b.c - b.o); }
function range(b) { return b.h - b.l; }
function isBull(b){ return b.c > b.o; }
function isBear(b){ return b.c < b.o; }

// Forward-return metrics from bar i, looking ahead `n` bars
// Returns { mfeBull, mfeBear, hitDir, exit }
// hitDir = +1 if bull target hit first, -1 if bear, 0 if neither
function forwardMetrics(i, n, atr) {
    const entry = bars[i].c;
    const target = atr * 2;      // 2 ATR move = hit
    let mfeBull = 0, mfeBear = 0, hitDir = 0;
    for (let j = i + 1; j <= Math.min(i + n, bars.length - 1); j++) {
        const upMove   = bars[j].h - entry;
        const downMove = entry - bars[j].l;
        if (upMove   > mfeBull) mfeBull = upMove;
        if (downMove > mfeBear) mfeBear = downMove;
        if (hitDir === 0) {
            if (upMove   >= target && downMove < target) { hitDir = 1; }
            else if (downMove >= target && upMove < target) { hitDir = -1; }
            else if (upMove >= target && downMove >= target) {
                // Both hit in same bar — count by which is bigger
                hitDir = upMove >= downMove ? 1 : -1;
            }
        }
    }
    return { mfeBull, mfeBear, hitDir };
}

// ── PATTERN DETECTORS ──────────────────────────────────────────────────────

// 1) NR7 — bar i has the narrowest range of the last 7 (inclusive)
function isNR7(i) {
    if (i < 6) return false;
    const r0 = range(bars[i]);
    for (let j = i - 6; j < i; j++) if (range(bars[j]) <= r0) return false;
    return true;
}

// 2) NR4
function isNR4(i) {
    if (i < 3) return false;
    const r0 = range(bars[i]);
    for (let j = i - 3; j < i; j++) if (range(bars[j]) <= r0) return false;
    return true;
}

// 3) Inside bar
function isInside(i) {
    if (i < 1) return false;
    return bars[i].h <= bars[i-1].h && bars[i].l >= bars[i-1].l;
}

// 4) Bullish engulfing — bar i bull, bar i-1 bear, body of i covers body of i-1
function isBullEngulf(i) {
    if (i < 1) return false;
    const a = bars[i-1], b = bars[i];
    return isBear(a) && isBull(b) && b.c >= a.o && b.o <= a.c;
}

// 5) Bearish engulfing
function isBearEngulf(i) {
    if (i < 1) return false;
    const a = bars[i-1], b = bars[i];
    return isBull(a) && isBear(b) && b.c <= a.o && b.o >= a.c;
}

// 6) Bullish wick rejection — body ≤ 30% of range, lower wick ≥ 60% of range
function isBullWick(i) {
    const b = bars[i];
    const r = range(b);
    if (r <= 0) return false;
    const bd = body(b);
    const lowerWick = Math.min(b.o, b.c) - b.l;
    return bd / r <= 0.30 && lowerWick / r >= 0.60;
}

// 7) Bearish wick rejection
function isBearWick(i) {
    const b = bars[i];
    const r = range(b);
    if (r <= 0) return false;
    const bd = body(b);
    const upperWick = b.h - Math.max(b.o, b.c);
    return bd / r <= 0.30 && upperWick / r >= 0.60;
}

// 8) Three white soldiers
function isThreeWhite(i) {
    if (i < 2) return false;
    return isBull(bars[i]) && isBull(bars[i-1]) && isBull(bars[i-2]) &&
           bars[i].c > bars[i-1].c && bars[i-1].c > bars[i-2].c &&
           bars[i].o > bars[i-1].o && bars[i-1].o > bars[i-2].o;
}

// 9) Three black crows
function isThreeBlack(i) {
    if (i < 2) return false;
    return isBear(bars[i]) && isBear(bars[i-1]) && isBear(bars[i-2]) &&
           bars[i].c < bars[i-1].c && bars[i-1].c < bars[i-2].c &&
           bars[i].o < bars[i-1].o && bars[i-1].o < bars[i-2].o;
}

// 10) Coil breakout — last 6 bars have avg range < 60% of prior 14-bar ATR,
//     and closes clustered within 1.5 ATR. Trigger on the breakout bar with
//     body > 1.5 ATR.
function isCoilBreakout(i, atr) {
    if (i < 20 || atr <= 0) return null;
    const N = 6;
    const coilStart = i - N, coilEnd = i - 1;
    let rngSum = 0, hi = -Infinity, lo = Infinity;
    for (let j = coilStart; j <= coilEnd; j++) {
        rngSum += range(bars[j]);
        if (bars[j].c > hi) hi = bars[j].c;
        if (bars[j].c < lo) lo = bars[j].c;
    }
    const avgRng = rngSum / N;
    if (avgRng > atr * 0.60) return null;
    if ((hi - lo) > atr * 1.5) return null;
    const breakBody = body(bars[i]);
    if (breakBody < atr * 1.5) return null;
    return isBull(bars[i]) ? 1 : -1;
}

// 11) Double bottom — current bar makes a low close within 0.5 ATR of a prior
//     local low (within last 30 bars), with a higher high between them.
//     Confirmation: current close > the intervening high.
function isDoubleBottom(i, atr) {
    if (i < 30 || atr <= 0) return false;
    const lo0 = bars[i].l;
    for (let j = i - 30; j <= i - 5; j++) {
        if (Math.abs(bars[j].l - lo0) <= atr * 0.5) {
            // Check there's a higher high between j and i
            let interHi = -Infinity;
            for (let k = j + 1; k < i; k++) if (bars[k].h > interHi) interHi = bars[k].h;
            if (interHi > lo0 + atr * 1.5 && bars[i].c > lo0 + atr * 0.5) return true;
        }
    }
    return false;
}

// 12) Double top — mirror
function isDoubleTop(i, atr) {
    if (i < 30 || atr <= 0) return false;
    const hi0 = bars[i].h;
    for (let j = i - 30; j <= i - 5; j++) {
        if (Math.abs(bars[j].h - hi0) <= atr * 0.5) {
            let interLo = Infinity;
            for (let k = j + 1; k < i; k++) if (bars[k].l < interLo) interLo = bars[k].l;
            if (interLo < hi0 - atr * 1.5 && bars[i].c < hi0 - atr * 0.5) return true;
        }
    }
    return false;
}

// ── REGISTER ───────────────────────────────────────────────────────────────
// Each pattern returns: 'bull' | 'bear' | null when fires.
const patterns = {
    'NR7':            (i, atr) => isNR7(i) ? 'breakout' : null,        // direction-neutral
    'NR4':            (i, atr) => isNR4(i) ? 'breakout' : null,
    'InsideBar':      (i, atr) => isInside(i) ? 'breakout' : null,
    'BullEngulfing':  (i, atr) => isBullEngulf(i) ? 'bull' : null,
    'BearEngulfing':  (i, atr) => isBearEngulf(i) ? 'bear' : null,
    'BullWickRej':    (i, atr) => isBullWick(i) ? 'bull' : null,
    'BearWickRej':    (i, atr) => isBearWick(i) ? 'bear' : null,
    'ThreeWhite':     (i, atr) => isThreeWhite(i) ? 'bull' : null,
    'ThreeBlack':     (i, atr) => isThreeBlack(i) ? 'bear' : null,
    'CoilBreakUp':    (i, atr) => isCoilBreakout(i, atr) === 1 ? 'bull' : null,
    'CoilBreakDown':  (i, atr) => isCoilBreakout(i, atr) === -1 ? 'bear' : null,
    'DoubleBottom':   (i, atr) => isDoubleBottom(i, atr) ? 'bull' : null,
    'DoubleTop':      (i, atr) => isDoubleTop(i, atr) ? 'bear' : null,
};

// ── RUN ────────────────────────────────────────────────────────────────────
const FORWARD = 20;
const stats = {};
for (const name of Object.keys(patterns)) {
    stats[name] = { n:0, wins:0, losses:0, neutral:0, sumMFE:0, sumMAE:0 };
}

for (let i = 30; i < bars.length - FORWARD; i++) {
    const atr = atrAt(i, 14);
    if (atr <= 0) continue;
    for (const name of Object.keys(patterns)) {
        const sig = patterns[name](i, atr);
        if (!sig) continue;
        const m = forwardMetrics(i, FORWARD, atr);
        const s = stats[name];
        s.n++;
        if (sig === 'breakout') {
            // Direction-neutral — count whichever direction wins
            if (m.hitDir !== 0) s.wins++;
            else                s.neutral++;
            s.sumMFE += Math.max(m.mfeBull, m.mfeBear);
            s.sumMAE += Math.min(m.mfeBull, m.mfeBear);
        } else {
            const dir = sig === 'bull' ? 1 : -1;
            if      (m.hitDir === dir)  s.wins++;
            else if (m.hitDir === -dir) s.losses++;
            else                        s.neutral++;
            s.sumMFE += (dir === 1) ? m.mfeBull : m.mfeBear;
            s.sumMAE += (dir === 1) ? m.mfeBear : m.mfeBull;
        }
    }
}

// ── REPORT ─────────────────────────────────────────────────────────────────
console.log('\n══════════════════════════════════════════════════════════════════════');
console.log('PATTERN SCAN — NQ 5-min bars,', bars.length, 'bars, 20-bar forward window, 2 ATR target');
console.log('══════════════════════════════════════════════════════════════════════');
function pad(s, n) { s = String(s); return s + ' '.repeat(Math.max(0, n - s.length)); }
console.log(pad('Pattern',16) + pad('N',6) + pad('Win%',7) + pad('Loss%',7) +
            pad('Neut%',7) + pad('MFE/ATR',9) + pad('MAE/ATR',9) + 'Edge');
console.log('─'.repeat(70));

const rows = Object.entries(stats)
    .filter(([, s]) => s.n >= 10)
    .map(([name, s]) => {
        const resolved = s.wins + s.losses;
        const wr = resolved > 0 ? s.wins / resolved * 100 : 0;
        const lr = resolved > 0 ? s.losses / resolved * 100 : 0;
        const nr = s.n > 0 ? s.neutral / s.n * 100 : 0;
        const avgMFE = s.n > 0 ? s.sumMFE / s.n : 0;
        const avgMAE = s.n > 0 ? s.sumMAE / s.n : 0;
        // Edge = MFE/ATR × (winRate - 50%) — captures both quality and direction bias
        const edge = avgMFE * (wr - 50);
        return { name, s, wr, lr, nr, avgMFE, avgMAE, edge };
    })
    .sort((a, b) => b.edge - a.edge);

for (const r of rows) {
    // Need ATR scale to normalize MFE/MAE — use median ATR
    // Compute on the fly: take a sample bar's ATR
    const sampleAtr = atrAt(Math.floor(bars.length / 2), 14);
    console.log(
        pad(r.name, 16) +
        pad(r.s.n, 6) +
        pad(r.wr.toFixed(1), 7) +
        pad(r.lr.toFixed(1), 7) +
        pad(r.nr.toFixed(1), 7) +
        pad((r.avgMFE / sampleAtr).toFixed(2), 9) +
        pad((r.avgMAE / sampleAtr).toFixed(2), 9) +
        r.edge.toFixed(1)
    );
}

console.log('\nLegend:');
console.log('  N        = number of pattern occurrences in the dataset');
console.log('  Win%     = % of resolved trades where 2-ATR target hit in expected direction first');
console.log('  Loss%    = % where 2-ATR target hit in OPPOSITE direction first');
console.log('  Neut%    = % where neither target hit within 20 bars');
console.log('  MFE/ATR  = avg max favorable excursion (in ATRs)');
console.log('  MAE/ATR  = avg max adverse excursion (in ATRs)');
console.log('  Edge     = MFE × (Win% - 50)   — higher = more tradable');
