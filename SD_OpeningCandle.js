'use strict';
// ═══════════════════════════════════════════════════════════════════════
// SD_OpeningCandle  —  Session "Fair Price" strategy (time charts)
//
// Implements the prop-firm strategy: "high time-frame reversion, low time-
// frame continuation." Runs per session open (default NY 09:30 + EVE 18:00).
//
//  1. MARK the fair-price candle:
//       NY  09:29 pre-open candle (its CLOSE == the 09:30 open / fair price)
//       EVE 18:00 reopen candle   (its OPEN == fair price)
//     Drawn as a box (high/low) extended right + a fair-price line + label.
//
//  2. SIGNALS off the session open candle:
//       • CONTINUATION (first contMinutes, default 10): trade in the
//         direction of the opening candle (overnight order flow).
//       • MEAN REVERSION (contMinutes..revMinutes, default 10..90): once
//         price has moved away from the open, trade back toward it.
//       Entry trigger = DISPLACEMENT candle (bigger range than prior, body-
//       dominant, small wicks) = grade A, or BREAK-OF-STRUCTURE + close
//       beyond recent swing = grade A+. Signals opposing the session bias
//       (B setups) are skipped.
//       Fixed risk: tpPoints / slPoints (default 38 / 25 = ~1:1.5 R:R,
//       NQ points). Each entry is tracked to TP or SL and marked WIN/LOSS.
//       Capped per phase (default 1 continuation + 4 reversion); window
//       ends 90 min after open (== 11:00 for NY, "stop looking after 11").
//       New entries also stop once volume dies out (volDieFrac).
//
// TIMEFRAME: use a 1-minute time chart (the strategy's primary), 5-minute
//   for quiet sessions. The chart timeframe is a Tradovate chart setting,
//   not something an indicator can set — this study detects the bar
//   interval and warns on-chart when it isn't 1m/5m.
//
// tzOffsetHours: shift bar timestamps to your exchange wall clock.
//   -4 = US Eastern during DST (EDT, default). Use -5 for winter EST.
// ═══════════════════════════════════════════════════════════════════════

const predef = require('./tools/predef');
const { du } = require('./tools/graphics');
const plottingTools = require('./tools/plotting');

function pget(p, key, def) { return (p && p[key] != null) ? p[key] : def; }
function sgn(x) { return x > 0 ? 1 : (x < 0 ? -1 : 0); }

const C_BULL = '#26c281', C_BEAR = '#ff5b5b';
const C_WIN  = '#00ff88', C_LOSS = '#ff4444';
const C_TP   = '#00ff66', C_SL   = '#ff4444', C_ENTRY = '#dddddd';
const C_WARN = '#ff5555', C_OK   = '#7fbf7f';

class OpeningCandleStrategy {

    init(props) {
        if (props) this.props = props;
        this.lastChartIdx = 0;
        this._bi = 0;
        // marker state
        this.opens = [];           // marked fair-price candles
        this._seen = {};           // "dayKey|SESS" -> index in opens
        // timeframe detection
        this._prevTs = null;
        this._barMs = null;
        this._lastClose = null;
        this._lastHigh = null;
        // bar store (timestamp-keyed so replays/ticks never corrupt it)
        this.O = []; this.H = []; this.L = []; this.C = []; this.V = [];
        this.M = []; this.X = []; this.DK = [];
        this._tsIndex = new Map();
        this._procUpTo = -1;       // last fully-closed bar index processed
        // strategy state
        this.active = null;        // current session: {key,label,color,fair,openDir,openMin,startK,trades}
        this._stratSeen = {};      // "dayKey|SESS" -> open candle index
        this.pos = 0;              // 0 flat, 1 long, -1 short
        this.entryPx = 0; this.tp = 0; this.sl = 0;
        this.entryIdx = 0; this.entryDir = 0; this.entryGrade = '';
        this.entriesLog = [];      // {x,dir,grade,phase,price,lo,hi,color}
        this.exitsLog = [];        // {x,win,price}
    }

    // Session table. min = minute-of-day of the candle to MARK.
    // fairAt = which price of that candle is the fair price ('close' for
    // pre-open candles, 'open' for reopen candles). The strategy's open
    // candle is at min+1 for pre-open sessions, min for reopen sessions.
    // `on` is computed dynamically via _isSessionActive so toggling a
    // session OR switching the chart timeframe takes effect immediately,
    // even on bars whose oc* flags were set on a previous param state.
    _sessions() {
        const all = [
            { key: 'NY',   label: 'NY 9:29',    min:  9 * 60 + 29, fairAt: 'close', color: '#00CCFF', primary: true },
            { key: 'EVE',  label: 'EVE 6:00',   min: 18 * 60,      fairAt: 'open',  color: '#88AAFF' },
            { key: 'NEWS', label: 'NEWS 8:29',  min:  8 * 60 + 29, fairAt: 'close', color: '#FF8C00' },
            { key: 'NYPM', label: 'NY PM 1:59', min: 13 * 60 + 59, fairAt: 'close', color: '#FF55DD' },
            { key: 'ASIA', label: 'ASIA 8:00',  min: 20 * 60,      fairAt: 'open',  color: '#FFD24D' },
            { key: 'LON',  label: 'LON 3:00',   min:  3 * 60,      fairAt: 'open',  color: '#A0FF88' },
        ];
        for (const s of all) s.on = this._isSessionActive(s.key);
        return all;
    }

    // Detected bar size in minutes (null until two timestamps have been seen).
    _tfMin() { return this._barMs ? this._barMs / 60000 : null; }

    // Is a session currently active? Combines the autoSession mode (1m
    // chart -> NY only, 5m chart -> EVE only) with the manual sessXxx
    // toggles. NEWS/NYPM/ASIA/LON always follow their toggles.
    _isSessionActive(key) {
        const p = this.props || {};
        const toggleKey = {
            NY: 'sessNY', EVE: 'sessEvening', NEWS: 'sessNewsAM',
            NYPM: 'sessNYPM', ASIA: 'sessAsia', LON: 'sessLondon'
        }[key];
        const auto = pget(p, 'autoSession', 1);
        if (auto && (key === 'NY' || key === 'EVE')) {
            const tf = this._tfMin();
            if (tf == null) return key === 'NY';         // default to NY before TF known
            if (tf >= 0.9 && tf <= 1.1) return key === 'NY';
            if (tf >= 4.5 && tf <= 5.5) return key === 'EVE';
            return false;                                 // unsupported TF -> both off
        }
        return !!pget(p, toggleKey, 0);
    }

    map(d) {
        const _ret = { graphics: { items: [] } };
        try {
            const p = this.props || {};
            const tz = pget(p, 'tzOffsetHours', -4);
            const markers = pget(p, 'enableMarkers', 1);
            const strat = pget(p, 'enableStrategy', 1);

            const o = d.open(), h = d.high(), l = d.low(), c = d.close();
            const vol = Math.max(0, (typeof d.volume === 'function' ? d.volume() : 0) || 0);
            const chartIdx = (typeof d.index === 'function') ? d.index() : this._bi;
            this._bi++;
            this.lastChartIdx = chartIdx;
            this._lastClose = c; this._lastHigh = h;

            const ts = d.timestamp();
            const tms = ts.getTime();
            const lt = new Date(tms + tz * 3600000);
            const mins = lt.getUTCHours() * 60 + lt.getUTCMinutes();
            const dayKey = lt.getUTCFullYear() + '-' + (lt.getUTCMonth() + 1) + '-' + lt.getUTCDate();

            // ── timeframe detection (min positive delta == bar size) ──
            if (this._prevTs != null) {
                const dms = tms - this._prevTs;
                if (dms > 0) this._barMs = (this._barMs == null) ? dms : Math.min(this._barMs, dms);
            }
            this._prevTs = tms;

            // ── marker: mark each session's fair-price candle ──
            // Lines are drawn by the custom plotter (Tradovate's default
            // renderer ignores line shapes returned from map), so each
            // opening-candle bar carries its box geometry as fields the
            // plotter reads back off history.
            const tol = Math.max(0, pget(p, 'openToleranceMin', 15));
            if (markers) {
                for (const s of this._sessions()) {
                    if (!s.on) continue;
                    const k = dayKey + '|' + s.key;
                    const seenIdx = this._seen[k];
                    if (seenIdx != null) {
                        const rec = this.opens[seenIdx];
                        if (rec && rec.chartIdx === chartIdx) {
                            rec.h = Math.max(rec.h, h); rec.l = Math.min(rec.l, l); rec.c = c;
                            _ret.ocKey = s.key; _ret.ocColor = s.color; _ret.ocH = rec.h; _ret.ocL = rec.l;
                            _ret.ocFair = (s.fairAt === 'close') ? rec.c : rec.o;
                        }
                        continue;
                    }
                    const diff = mins - s.min;
                    if (diff >= 0 && diff <= tol) {
                        this._seen[k] = this.opens.length;
                        this.opens.push({ key: s.key, label: s.label, color: s.color, fairAt: s.fairAt, primary: !!s.primary, chartIdx, o, h, l, c, dayKey });
                        _ret.ocKey = s.key; _ret.ocColor = s.color; _ret.ocH = h; _ret.ocL = l;
                        _ret.ocFair = (s.fairAt === 'close') ? c : o;
                    }
                }
            }

            // ── strategy: maintain bar store + step closed bars. Skipped
            //    entirely when the strategy is off (markers-only mode). ──
            if (strat) {
                let k = this._tsIndex.get(tms);
                if (k == null) {
                    k = this.O.length;
                    this._tsIndex.set(tms, k);
                    this.O.push(o); this.H.push(h); this.L.push(l); this.C.push(c); this.V.push(vol);
                    this.M.push(mins); this.X.push(chartIdx); this.DK.push(dayKey);
                } else {
                    this.O[k] = o; this.H[k] = h; this.L[k] = l; this.C[k] = c; this.V[k] = vol;
                    this.M[k] = mins; this.X[k] = chartIdx; this.DK[k] = dayKey;
                }
                const lastClosed = this.O.length - 2;   // last bar is still forming
                for (let j = this._procUpTo + 1; j <= lastClosed; j++) this._step(j);
                if (lastClosed > this._procUpTo) this._procUpTo = lastClosed;
            }

            const isLast = (typeof d.isLast === 'function') && d.isLast();
            if (isLast) this._draw(_ret.graphics.items, p);
        } catch (e) { /* never throw out of map */ }
        return _ret;
    }

    // ── strategy stepping for a fully-closed bar at array index k ──
    _step(k) {
        const p = this.props || {};
        const tol = Math.max(0, pget(p, 'openToleranceMin', 15));
        const contMin = pget(p, 'contMinutes', 10);
        const revMin = pget(p, 'revMinutes', 90);
        const volDieFrac = pget(p, 'volDieFrac', 0.4);
        const volWindow = Math.max(2, pget(p, 'volWindow', 5));

        // 1. manage open position
        if (this.pos !== 0) this._checkExit(k);

        // 2. detect session open candle
        for (const s of this._sessions()) {
            if (!s.on) continue;
            const openMin = (s.fairAt === 'close') ? s.min + 1 : s.min;
            const sk = this.DK[k] + '|' + s.key;
            if (this._stratSeen[sk] != null) continue;
            const diff = this.M[k] - openMin;
            if (diff >= 0 && diff <= tol) {
                this._stratSeen[sk] = k;
                this.active = {
                    key: s.key, label: s.label, color: s.color,
                    fair: this.O[k], openDir: sgn(this.C[k] - this.O[k]),
                    openMin, startK: k, contTrades: 0, revTrades: 0,
                    volSum: 0, volN: 0, volDead: false
                };
            }
        }

        // 3. close session window after revMin, or if the session was
        //    just toggled off (manually or by an autoSession TF switch).
        if (this.active && this.M[k] > this.active.openMin + revMin) this.active = null;
        if (this.active && !this._isSessionActive(this.active.key)) this.active = null;

        // 4. volume tracking — flag "volume died" once it fades vs the
        //    session average (only after the continuation window).
        if (this.active) {
            this.active.volSum += this.V[k];
            this.active.volN++;
            if (!this.active.volDead && this.active.volN >= 15 && this.M[k] > this.active.openMin + contMin) {
                const recent = this._volAvg(k, volWindow);
                const avg = this.active.volSum / this.active.volN;
                if (avg > 0 && recent < volDieFrac * avg) this.active.volDead = true;
            }
        }

        // 5. entry
        if (this.pos === 0 && this.active && k > this.active.startK) this._maybeEnter(k, contMin, revMin);
    }

    // Average volume over the `n` bars preceding (and excluding) bar k.
    _volAvg(k, n) {
        let sum = 0, cnt = 0;
        for (let j = Math.max(0, k - n); j <= k - 1; j++) { sum += this.V[j]; cnt++; }
        return cnt ? sum / cnt : 0;
    }

    _maybeEnter(k, contMin, revMin) {
        const p = this.props || {};
        const revMove = pget(p, 'revMinMovePts', 10);
        if (this.active.volDead) return;               // volume died -> stop trading

        const trig = this._trigger(k);
        if (!trig) return;

        // bias by phase
        const m = this.M[k];
        let bias = 0, phase = '';
        if (m <= this.active.openMin + contMin) { phase = 'C'; bias = this.active.openDir; }
        else if (m <= this.active.openMin + revMin) {
            phase = 'R';
            const dToFair = this.active.fair - this.C[k];
            if (Math.abs(dToFair) >= revMove) bias = sgn(dToFair);
        }
        if (bias === 0 || trig.dir !== bias) return;  // no bias / B setup -> skip

        // per-phase trade caps: 1 continuation, then 3-4 reversions
        const maxCont = pget(p, 'maxContTrades', 1), maxRev = pget(p, 'maxRevTrades', 4);
        if (phase === 'C' && this.active.contTrades >= maxCont) return;
        if (phase === 'R' && this.active.revTrades >= maxRev) return;

        // optional: require a volume spike to confirm the trigger
        if (pget(p, 'requireVolSpike', 0)) {
            const ra = this._volAvg(k, Math.max(2, pget(p, 'volWindow', 5)));
            if (ra > 0 && this.V[k] < pget(p, 'volSpikeMult', 1.2) * ra) return;
        }

        const tp = pget(p, 'tpPoints', 38), sl = pget(p, 'slPoints', 25);
        this.pos = trig.dir;
        this.entryPx = this.C[k];
        this.entryDir = trig.dir;
        this.tp = this.entryPx + trig.dir * tp;
        this.sl = this.entryPx - trig.dir * sl;
        this.entryIdx = this.X[k];
        this.entryGrade = trig.grade;
        if (phase === 'C') this.active.contTrades++; else this.active.revTrades++;
        this.entriesLog.push({
            x: this.X[k], dir: trig.dir, grade: trig.grade, phase,
            price: this.entryPx, lo: this.L[k], hi: this.H[k],
            color: trig.dir === 1 ? C_BULL : C_BEAR
        });
    }

    // Trigger on bar k: BOS+close (A+) takes priority over displacement (A).
    _trigger(k) {
        const p = this.props || {};
        const look = pget(p, 'bosLookback', 12);
        const bodyFrac = pget(p, 'dispBodyFrac', 0.6);
        if (k < look + 1) return null;

        const range = this.H[k] - this.L[k];
        const prevRange = this.H[k - 1] - this.L[k - 1];
        const body = Math.abs(this.C[k] - this.O[k]);
        const prevBody = Math.abs(this.C[k - 1] - this.O[k - 1]);
        const bull = this.C[k] > this.O[k], bear = this.C[k] < this.O[k];

        // break of structure: close beyond the prior `look` bars' range
        let hh = -Infinity, ll = Infinity;
        for (let j = k - look; j <= k - 1; j++) { if (this.H[j] > hh) hh = this.H[j]; if (this.L[j] < ll) ll = this.L[j]; }
        if (this.C[k] > hh) return { dir: 1, grade: 'A+' };
        if (this.C[k] < ll) return { dir: -1, grade: 'A+' };

        // displacement: bigger range + body-dominant + bigger body than prior
        const disp = range > prevRange && body >= bodyFrac * range && body > prevBody && (bull || bear);
        if (disp) return { dir: bull ? 1 : -1, grade: 'A' };
        return null;
    }

    _checkExit(k) {
        const dir = this.pos;
        let win = null;
        if (dir === 1) {
            if (this.L[k] <= this.sl) win = false;        // SL first (conservative)
            else if (this.H[k] >= this.tp) win = true;
        } else {
            if (this.H[k] >= this.sl) win = false;
            else if (this.L[k] <= this.tp) win = true;
        }
        if (win != null) {
            const exitPx = win ? this.tp : this.sl;
            const pts = dir * (exitPx - this.entryPx);
            this.exitsLog.push({ x: this.X[k], win, price: exitPx, pts });
            this.pos = 0;
        }
    }

    _draw(items, p) {
        // ── markers ──
        if (pget(p, 'enableMarkers', 1)) this._drawMarkers(items, p);
        // ── strategy signals ──
        if (pget(p, 'enableStrategy', 1)) this._drawStrategy(items, p);
        // ── timeframe notice ──
        if (pget(p, 'tfWarning', 1)) this._drawTF(items, p);
    }

    // Labels only — the box/fair lines are drawn by the custom plotter.
    _drawMarkers(items, p) {
        if (!pget(p, 'showLabel', 1)) return;
        const labelSize = Math.max(7, pget(p, 'labelSize', 11));
        for (const rec of this.opens) {
            if (!this._isSessionActive(rec.key)) continue;  // session toggled off / TF mismatch
            const fair = (rec.fairAt === 'close') ? rec.c : rec.o;
            const pad = Math.max(rec.h - rec.l, Math.abs(rec.c) * 0.0003);
            items.push({ tag: 'Text', key: 'oc_lbl_' + rec.key + '_' + rec.dayKey,
                text: rec.label + '  fair ' + fair.toFixed(2),
                point: { x: du(rec.chartIdx), y: du(rec.h + pad) },
                style: { fontSize: labelSize, fontWeight: rec.primary ? 'bold' : 'normal', fill: rec.color },
                textAlignment: 'leftMiddle' });
        }
    }

    _drawStrategy(items, p) {
        const labelSize = Math.max(7, pget(p, 'labelSize', 11));
        const padOf = (price, lo, hi) => Math.max((hi - lo) || 0, Math.abs(price) * 0.0004);

        // entry arrows
        for (let i = 0; i < this.entriesLog.length; i++) {
            const e = this.entriesLog[i];
            const pad = padOf(e.price, e.lo, e.hi);
            const up = e.dir === 1;
            const phaseTag = e.phase === 'C' ? 'CONT' : 'REV';
            items.push({ tag: 'Text', key: 'st_en_' + i,
                text: (up ? '▲ ' : '▼ ') + e.grade + ' ' + phaseTag,
                point: { x: du(e.x), y: du(up ? e.lo - pad : e.hi + pad) },
                style: { fontSize: labelSize, fontWeight: 'bold', fill: e.color },
                textAlignment: 'centerMiddle' });
        }
        // exit markers
        for (let i = 0; i < this.exitsLog.length; i++) {
            const x = this.exitsLog[i];
            items.push({ tag: 'Text', key: 'st_ex_' + i,
                text: x.win ? '✓' : '✗',
                point: { x: du(x.x), y: du(x.price) },
                style: { fontSize: labelSize + 1, fontWeight: 'bold', fill: x.win ? C_WIN : C_LOSS },
                textAlignment: 'centerMiddle' });
        }
        // open position: entry / SL / TP value labels (the lines are drawn
        // by the custom plotter).
        if (this.pos !== 0 && pget(p, 'showTradeLines', 1)) {
            const ex = this.lastChartIdx + 5;
            const lbl = (key, y, txt, col) => items.push({ tag: 'Text', key,
                text: txt, point: { x: du(ex), y: du(y) },
                style: { fontSize: labelSize, fontWeight: 'bold', fill: col },
                textAlignment: 'leftMiddle', global: true });
            lbl('st_pel', this.entryPx, (this.pos === 1 ? 'LONG ' : 'SHORT ') + this.entryPx.toFixed(2) + ' [' + this.entryGrade + ']', C_ENTRY);
            lbl('st_psl', this.sl, 'SL ' + this.sl.toFixed(2), C_SL);
            lbl('st_ptl', this.tp, 'TP ' + this.tp.toFixed(2), C_TP);
        }

        // status HUD (top of chart, anchored at last bar)
        const baseY = (this._lastHigh || this._lastClose || 0);
        const pad = Math.abs(this._lastClose || 1) * 0.0018;
        const wins = this.exitsLog.filter(e => e.win).length;
        const total = this.exitsLog.length;
        const losses = total - wins;
        const net = this.exitsLog.reduce((a, e) => a + (e.pts || 0), 0);
        const wr = total ? Math.round(100 * wins / total) : 0;
        const perf = 'W' + wins + '/L' + losses + (total ? ' ' + wr + '%' : '') +
                     '  ' + (net >= 0 ? '+' : '') + net.toFixed(0) + 'pt';
        let line;
        if (this.active) {
            const maxCont = pget(p, 'maxContTrades', 1), maxRev = pget(p, 'maxRevTrades', 4);
            line = this.active.label.split(' ')[0] +
                   '  C' + this.active.contTrades + '/' + maxCont +
                   ' R' + this.active.revTrades + '/' + maxRev +
                   (this.active.volDead ? ' [vol died]' : '') +
                   '  fair ' + this.active.fair.toFixed(0) + '  ' + perf;
        } else {
            line = 'no active session  ' + perf;
        }
        items.push({ tag: 'Text', key: 'st_hud',
            text: line, point: { x: du(this.lastChartIdx), y: du(baseY + pad * 2.4) },
            style: { fontSize: labelSize, fontWeight: 'bold', fill: '#cccccc' },
            textAlignment: 'leftMiddle', global: true });
    }

    _drawTF(items, p) {
        const labelSize = Math.max(7, pget(p, 'labelSize', 11));
        const baseY = (this._lastHigh || this._lastClose || 0);
        const pad = Math.abs(this._lastClose || 1) * 0.0018;
        const bm = this._barMs ? this._barMs / 60000 : null;       // bar size in minutes
        let text, col;
        if (bm == null) { return; }
        else if (bm >= 0.9 && bm <= 1.1) { text = '1m ✓ strategy timeframe'; col = C_OK; }
        else if (bm >= 4.5 && bm <= 5.5) { text = '5m — low-volume fallback (use 1m for NY)'; col = C_OK; }
        else {
            const shown = bm >= 1 ? Math.round(bm) + 'm' : Math.round(bm * 60) + 's';
            text = '⚠ chart is ' + shown + ' — switch to 1m (5m for quiet sessions)';
            col = C_WARN;
        }
        items.push({ tag: 'Text', key: 'st_tf',
            text, point: { x: du(this.lastChartIdx), y: du(baseY + pad * 4) },
            style: { fontSize: labelSize, fontWeight: 'bold', fill: col },
            textAlignment: 'leftMiddle', global: true });
    }
}

// Custom plotter — draws all LINES (Tradovate's default renderer only draws
// the Text/marker items returned from map). Reads the per-bar box geometry
// (ocKey/ocH/ocL/ocFair/ocColor) the calculator wrote onto history items.
function linePlotter(canvas, indicator, history) {
    try {
        const pt = plottingTools;
        const p = indicator.props || {};
        const data = history && history.data;
        const n = data ? data.length : 0;
        if (!n) return;

        const lw = Math.max(1, (p.lineWidth != null ? p.lineWidth : 1));
        const op = Math.max(0.1, Math.min(1, (p.opacity != null ? p.opacity : 0.85)));
        const showZone = (p.showZone != null ? p.showZone : 1);
        const showFair = (p.showFairLine != null ? p.showFairLine : 1);
        const markersOn = (p.enableMarkers != null ? p.enableMarkers : 1);
        const stratOn = (p.enableStrategy != null ? p.enableStrategy : 1);

        // collect opening-candle bars + last x
        const opens = [];
        let lastX = null;
        for (let i = 0; i < n; i++) {
            const it = history.get(i);
            if (!it) continue;
            lastX = pt.x.get(it);
            if (it.ocKey) opens.push({ key: it.ocKey, x: lastX, h: it.ocH, l: it.ocL, fair: it.ocFair, col: it.ocColor });
        }
        if (lastX == null) return;

        // fair-value boxes, each extended to the next open (or the live bar).
        // Skip any open whose session is currently inactive (toggled off, or
        // filtered out by autoSession when the chart TF doesn't match).
        if (markersOn) {
            const activeOpens = opens.filter(o => indicator._isSessionActive(o.key));
            for (let j = 0; j < activeOpens.length; j++) {
                const o = activeOpens[j];
                const sx = o.x;
                const ex = (j + 1 < activeOpens.length) ? activeOpens[j + 1].x : lastX;
                if (showZone) {
                    canvas.drawLine(pt.offset(sx, o.h), pt.offset(ex, o.h), { color: o.col, lineWidth: lw, opacity: op * 0.6 });
                    canvas.drawLine(pt.offset(sx, o.l), pt.offset(ex, o.l), { color: o.col, lineWidth: lw, opacity: op * 0.6 });
                    canvas.drawLine(pt.offset(sx, o.l), pt.offset(sx, o.h), { color: o.col, lineWidth: lw, opacity: op });
                }
                if (showFair) {
                    canvas.drawLine(pt.offset(sx, o.fair), pt.offset(ex, o.fair), { color: o.col, lineWidth: lw + 1, opacity: op });
                }
            }
        }

        // open-position entry / SL / TP lines
        if (stratOn && (p.showTradeLines != null ? p.showTradeLines : 1) && indicator.pos) {
            const back = history.get(Math.max(0, n - 60));
            const sx = back ? pt.x.get(back) : lastX;
            canvas.drawLine(pt.offset(sx, indicator.entryPx), pt.offset(lastX, indicator.entryPx), { color: '#dddddd', lineWidth: 1, opacity: 0.95 });
            canvas.drawLine(pt.offset(sx, indicator.sl), pt.offset(lastX, indicator.sl), { color: '#ff4444', lineWidth: 1, opacity: 0.95 });
            canvas.drawLine(pt.offset(sx, indicator.tp), pt.offset(lastX, indicator.tp), { color: '#00ff66', lineWidth: 1, opacity: 0.95 });
        }
    } catch (e) { /* swallow */ }
}

module.exports = {
    name: 'SD_OpeningCandle',
    description: 'Session fair-price candle marker + continuation/reversion strategy',
    calculator: OpeningCandleStrategy,
    inputType: 'bars',
    tags: ['SD'],
    plotter: [predef.plotters.custom(linePlotter)],
    params: {
        // Shift bar timestamps to your exchange wall clock.
        // -4 = US Eastern (EDT, default). Use -5 for winter EST.
        tzOffsetHours: predef.paramSpecs.number(-4, 1, -12),

        // ── master switches ──
        enableMarkers:  predef.paramSpecs.number(1, 1, 0),  // draw fair-price candle boxes
        enableStrategy: predef.paramSpecs.number(1, 1, 0),  // generate signals
        tfWarning:      predef.paramSpecs.number(1, 1, 0),  // on-chart timeframe notice

        // ── sessions ──
        // autoSession: when 1 (default), the chart timeframe picks the
        // active session — 1m chart -> NY only, 5m chart -> EVE only, any
        // other TF -> neither. Set to 0 for full manual control via the
        // sessXxx toggles below. NEWS / NY PM / ASIA / LON always follow
        // their own toggles regardless of autoSession.
        autoSession: predef.paramSpecs.number(1, 1, 0),
        sessNY:      predef.paramSpecs.number(1, 1, 0),
        sessEvening: predef.paramSpecs.number(1, 1, 0),
        sessNewsAM:  predef.paramSpecs.number(0, 1, 0),
        sessNYPM:    predef.paramSpecs.number(0, 1, 0),
        sessAsia:    predef.paramSpecs.number(0, 1, 0),
        sessLondon:  predef.paramSpecs.number(0, 1, 0),
        // first bar may be this many minutes past the target and still count
        // (keep < 60 so adjacent sessions don't merge). On 1m it's exact.
        openToleranceMin: predef.paramSpecs.number(15, 1, 0),

        // ── strategy logic ──
        contMinutes: predef.paramSpecs.number(10, 1, 0),    // continuation window after open
        revMinutes:  predef.paramSpecs.number(90, 5, 0),    // reversion window after open
        maxContTrades: predef.paramSpecs.number(1, 1, 0),   // continuation = "the first trade"
        maxRevTrades:  predef.paramSpecs.number(4, 1, 0),   // reversion = "3-4 trades max"
        bosLookback: predef.paramSpecs.number(12, 1, 2),    // bars for break-of-structure
        dispBodyFrac: predef.paramSpecs.number(0.6, 0.05, 0.1), // displacement body/range
        revMinMovePts: predef.paramSpecs.number(10, 1, 0),  // min move from fair to fade
        // fixed risk in points (NQ). 38/25 ≈ 1:1.5 R:R.
        tpPoints: predef.paramSpecs.number(38, 1, 1),
        slPoints: predef.paramSpecs.number(25, 1, 1),

        // ── volume ("stop looking when volume dies out") ──
        // Once the recent volume (last volWindow bars) drops below volDieFrac
        // of the session average, new entries stop. 0 disables.
        volDieFrac:  predef.paramSpecs.number(0.4, 0.05, 0),
        volWindow:   predef.paramSpecs.number(5, 1, 2),
        // optional: require the trigger bar's volume > volSpikeMult * recent
        // average ("we get the volume spike, close above the box").
        requireVolSpike: predef.paramSpecs.number(0, 1, 0),
        volSpikeMult:    predef.paramSpecs.number(1.2, 0.1, 1),

        // ── appearance ──
        showZone:      predef.paramSpecs.number(1, 1, 0),
        showFairLine:  predef.paramSpecs.number(1, 1, 0),
        showLabel:     predef.paramSpecs.number(1, 1, 0),
        showTradeLines: predef.paramSpecs.number(1, 1, 0),
        extendBars:    predef.paramSpecs.number(0, 5, 0),
        lineWidth:     predef.paramSpecs.number(1, 1, 1),
        opacity:       predef.paramSpecs.number(0.85, 0.05, 0.1),
        labelSize:     predef.paramSpecs.number(11, 1, 7),
    },
    plots: {},
    schemeStyles: { dark: {} },
};
