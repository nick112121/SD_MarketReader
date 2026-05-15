'use strict';
// Trap Level backtest — measures the OHLC-proxy version of "trap levels":
// a swing level that price WICKED through but CLOSED back inside (failed
// breakout = trapped breakout traders). When price later RETURNS to that
// level, does the trapped side's forced covering produce a reversal?
//
// Measures: return rate, reversal rate on return, avg reaction size.

const fs = require('fs');

// ── LOAD ───────────────────────────────────────────────────────────────────
const file = process.argv[2] || '/tmp/qqq_merged.json';
const data = JSON.parse(fs.readFileSync(file, 'utf8'));
const bars = [];
for (const b of data) {
    if (b.open == null || b.close == null) continue;
    bars.push({ t: new Date(b.date).getTime()/1000, o:b.open, h:b.high, l:b.low, c:b.close, v:b.volume||0 });
}
console.log(`Loaded ${bars.length} bars (${new Date(bars[0].t*1000).toISOString().slice(0,10)} → ${new Date(bars[bars.length-1].t*1000).toISOString().slice(0,10)})`);

// ── HELPERS ────────────────────────────────────────────────────────────────
function atrAt(i, n=14) {
    if (i < n) return 0;
    let s = 0;
    for (let j = i-n+1; j <= i; j++) {
        s += Math.max(bars[j].h-bars[j].l,
            Math.abs(bars[j].h-bars[j-1].c), Math.abs(bars[j].l-bars[j-1].c));
    }
    return s/n;
}

// Swing high: bar i's high is the highest of i-2..i+2
function isSwingHigh(i) {
    if (i < 2 || i > bars.length-3) return false;
    const h = bars[i].h;
    return h > bars[i-1].h && h > bars[i-2].h && h > bars[i+1].h && h > bars[i+2].h;
}
function isSwingLow(i) {
    if (i < 2 || i > bars.length-3) return false;
    const l = bars[i].l;
    return l < bars[i-1].l && l < bars[i-2].l && l < bars[i+1].l && l < bars[i+2].l;
}

// ── BUILD TRAP LEVELS ──────────────────────────────────────────────────────
// Walk forward. Maintain recent swing highs/lows. When a later bar WICKS
// through a swing level but CLOSES back inside → that swing price is a trap.
const swingHighs = [];   // {price, bar}
const swingLows  = [];
const trapLevels = [];   // {price, bar, dir}  dir=+1 trapped-longs (above), -1 trapped-shorts (below)
                         // dir = which side is trapped: -1 = breakout-up failed = trapped longs → resistance
                         //                              +1 = breakout-dn failed = trapped shorts → support

for (let i = 3; i < bars.length - 3; i++) {
    // Register confirmed swings (need +2 bars ahead, so confirmed at i-? — use i-2 logic)
    if (isSwingHigh(i-2)) swingHighs.push({ price: bars[i-2].h, bar: i-2 });
    if (isSwingLow(i-2))  swingLows.push({ price: bars[i-2].l, bar: i-2 });
    if (swingHighs.length > 40) swingHighs.shift();
    if (swingLows.length > 40)  swingLows.shift();

    const atr = atrAt(i, 14);
    if (atr <= 0) continue;
    const b = bars[i];

    // Failed UP-break: bar high pierced a swing high, but close came back below it
    for (let s = swingHighs.length-1; s >= 0; s--) {
        const sw = swingHighs[s];
        if (i - sw.bar < 3 || i - sw.bar > 60) continue;
        if (b.h > sw.price + atr*0.05 && b.c < sw.price - atr*0.05) {
            trapLevels.push({ price: sw.price, bar: i, dir: -1 });  // trapped longs → resistance
            swingHighs.splice(s, 1);   // consume it
            break;
        }
    }
    // Failed DOWN-break: bar low pierced a swing low, but close came back above
    for (let s = swingLows.length-1; s >= 0; s--) {
        const sw = swingLows[s];
        if (i - sw.bar < 3 || i - sw.bar > 60) continue;
        if (b.l < sw.price - atr*0.05 && b.c > sw.price + atr*0.05) {
            trapLevels.push({ price: sw.price, bar: i, dir: 1 });   // trapped shorts → support
            swingLows.splice(s, 1);
            break;
        }
    }
}
console.log(`Detected ${trapLevels.length} trap levels`);

// ── MEASURE RETURNS ────────────────────────────────────────────────────────
// For each trap level, find the FIRST time price returns to it (within a
// zone) after it formed. Measure: did it reverse? how far?
function measure(zoneAtrMult) {
    let returns = 0, reversals = 0, sumReaction = 0, sumAdverse = 0;
    let breaks = 0;
    for (const tl of trapLevels) {
        const formBar = tl.bar;
        // ATR at formation for zone width
        const atr = atrAt(formBar, 14);
        if (atr <= 0) continue;
        const zone = atr * zoneAtrMult;
        // Search forward for first touch of the zone
        for (let j = formBar + 2; j < Math.min(formBar + 200, bars.length - 20); j++) {
            const touched = bars[j].h >= tl.price - zone && bars[j].l <= tl.price + zone;
            if (!touched) continue;
            returns++;
            // Reaction: trapped-longs level (dir=-1) → expect price to reject DOWN
            //           trapped-shorts level (dir=1) → expect price to reject UP
            const expectDir = tl.dir;  // +1 = reject up, -1 = reject down
            let mfe = 0, mae = 0;
            const entry = tl.price;
            for (let k = j; k <= Math.min(j + 15, bars.length - 1); k++) {
                const up = bars[k].h - entry, dn = entry - bars[k].l;
                const fav = expectDir === 1 ? up : dn;
                const adv = expectDir === 1 ? dn : up;
                if (fav > mfe) mfe = fav;
                if (adv > mae) mae = adv;
            }
            sumReaction += mfe;
            sumAdverse += mae;
            // Reversal "hit" = favorable move >= 1 ATR before adverse >= 1 ATR
            if (mfe >= atr && mfe > mae) reversals++;
            else if (mae >= atr) breaks++;
            break;  // only first return
        }
    }
    return { returns, reversals, breaks, sumReaction, sumAdverse };
}

console.log('\n══════════════════════════════════════════════════════════════');
console.log('TRAP LEVEL ACCURACY — OHLC proxy (failed-breakout version)');
console.log('══════════════════════════════════════════════════════════════');
function pad(s,n){s=String(s);return s+' '.repeat(Math.max(0,n-s.length));}
console.log(pad('Zone',12)+pad('Returns',10)+pad('Reversals',12)+pad('Rev%',8)+pad('AvgReact',10)+'AvgAdverse');
console.log('─'.repeat(64));
for (const zm of [0.15, 0.25, 0.50, 1.0]) {
    const r = measure(zm);
    const sampleAtr = atrAt(Math.floor(bars.length/2), 14);
    const revPct = r.returns > 0 ? (r.reversals/r.returns*100) : 0;
    console.log(
        pad('±'+zm+' ATR', 12) +
        pad(r.returns, 10) +
        pad(r.reversals, 12) +
        pad(revPct.toFixed(1)+'%', 8) +
        pad((r.sumReaction/Math.max(r.returns,1)/sampleAtr).toFixed(2)+' ATR', 10) +
        (r.sumAdverse/Math.max(r.returns,1)/sampleAtr).toFixed(2)+' ATR'
    );
}
console.log('\nReversal = favorable move ≥ 1 ATR before adverse ≥ 1 ATR, on first return to zone.');
console.log('AvgReact = avg max favorable excursion in 15 bars after return.');
console.log('AvgAdverse = avg max adverse excursion (how much it goes against you first).');
