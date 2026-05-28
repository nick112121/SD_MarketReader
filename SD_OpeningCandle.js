'use strict';
// ═══════════════════════════════════════════════════════════════════════
// SD_OpeningCandle  —  Session Open / "Fair Price" candle marker
//
// Built from the "high time-frame reversion, low time-frame continuation"
// prop-firm strategy. The core idea from the strategy:
//
//   "I always mark out the candle pre-open / the opening candle, which we
//    believe is the fair price. Any move away from that is unfair."
//
// At each session open the price right at the open is treated as the fair
// auction price. This indicator finds the FIRST candle at/after each
// session open and:
//   • draws a box anchored on that candle, extended to the right
//     (top = candle high, bottom = candle low) — the fair-value zone
//   • draws a "fair price" line at the candle's OPEN (== the pre-open
//     close, the level the YouTuber reverts back to)
//   • labels it with the session name + fair price
//
// Sessions (times are wall-clock in the configured tz; default -4 = US
// Eastern / EDT). Toggle each on/off in the params panel:
//   NEWS  08:30   NY    09:30   NY PM 14:00
//   EVE   18:00   ASIA  20:00   LON   03:00
//
// tzOffsetHours: shift the bar timestamp to your exchange wall clock.
//   -4 = US Eastern during DST (EDT, the default — matches the strategy's
//   "EST" talk during summer). Use -5 for true winter EST.
// ═══════════════════════════════════════════════════════════════════════

const predef    = require('./tools/predef');
const { du }    = require('./tools/graphics');

function pget(p, key, def) {
    return (p && p[key] != null) ? p[key] : def;
}

class OpeningCandleMarker {

    init(props) {
        if (props) this.props = props;
        this.lastChartIdx = 0;     // d.index() of the most recent bar
        this._bi = 0;              // fallback running counter
        this.opens = [];           // detected opening candles, in time order
        this._seen = {};           // "dayKey|SESS" -> index into this.opens
    }

    // Session table. min = minutes from local midnight for the open.
    _sessions() {
        const p = this.props || {};
        return [
            { key: 'NEWS', label: 'NEWS 8:30',  min:  8 * 60 + 30, on: pget(p, 'sessNewsAM',  1), color: '#FF8C00' },
            { key: 'NY',   label: 'NY 9:30',    min:  9 * 60 + 30, on: pget(p, 'sessNY',      1), color: '#00CCFF', primary: true },
            { key: 'NYPM', label: 'NY PM 2:00', min: 14 * 60,      on: pget(p, 'sessNYPM',    1), color: '#FF55DD' },
            { key: 'EVE',  label: 'EVE 6:00',   min: 18 * 60,      on: pget(p, 'sessEvening', 0), color: '#88AAFF' },
            { key: 'ASIA', label: 'ASIA 8:00',  min: 20 * 60,      on: pget(p, 'sessAsia',    0), color: '#FFD24D' },
            { key: 'LON',  label: 'LON 3:00',   min:  3 * 60,      on: pget(p, 'sessLondon',  0), color: '#A0FF88' },
        ];
    }

    map(d) {
        const _ret = { graphics: { items: [] } };
        try {
            const p  = this.props || {};
            const tz = pget(p, 'tzOffsetHours', -4);

            const o = d.open(), h = d.high(), l = d.low(), c = d.close();

            // Chart bar index — what du() needs to position x.
            const chartIdx = (typeof d.index === 'function') ? d.index() : this._bi;
            this._bi++;
            this.lastChartIdx = chartIdx;

            // ── Local wall-clock time (fixed tz offset, like the prop-firm
            //    strategy's "Eastern" reference). ───────────────────────
            const ts = d.timestamp();
            const lt = new Date(ts.getTime() + tz * 3600000);
            const lth = lt.getUTCHours(), ltm = lt.getUTCMinutes();
            const mins = lth * 60 + ltm;
            const dayKey = lt.getUTCFullYear() + '-' + (lt.getUTCMonth() + 1) + '-' + lt.getUTCDate();

            // ── Detect each session's opening candle ───────────────────
            // Mark the FIRST bar of each day that falls within `tol` minutes
            // of the session open. The tolerance is what makes this robust
            // to gaps / RTH-only data: if the first available bar is far
            // past the open (e.g. an RTH chart whose data starts at 09:30,
            // so there is no real 08:30 candle), that session is skipped
            // instead of being mis-tagged onto a later bar. _seen dedups per
            // day so replays and live ticks never create duplicates.
            const tol = Math.max(0, pget(p, 'openToleranceMin', 15));
            for (const s of this._sessions()) {
                if (!s.on) continue;
                const k = dayKey + '|' + s.key;
                const seenIdx = this._seen[k];

                if (seenIdx != null) {
                    // Already recorded today. If this is still the same
                    // (live) bar, let its high/low/close keep forming.
                    const rec = this.opens[seenIdx];
                    if (rec && rec.chartIdx === chartIdx) {
                        rec.h = Math.max(rec.h, h);
                        rec.l = Math.min(rec.l, l);
                        rec.c = c;
                    }
                    continue;
                }

                const diff = mins - s.min;
                if (diff >= 0 && diff <= tol) {
                    this._seen[k] = this.opens.length;
                    this.opens.push({
                        key: s.key, label: s.label, color: s.color,
                        primary: !!s.primary, chartIdx, o, h, l, c, dayKey
                    });
                }
            }

            // ── Render on the last bar so spanning lines reach "now". ───
            const isLast = (typeof d.isLast === 'function') && d.isLast();
            if (isLast) this._draw(_ret.graphics.items, p);

        } catch (e) { /* never throw out of map */ }
        return _ret;
    }

    _draw(items, p) {
        const showZone  = pget(p, 'showZone',     1);
        const showFair  = pget(p, 'showFairLine', 1);
        const showLabel = pget(p, 'showLabel',    1);
        const extendBars = pget(p, 'extendBars',  0);   // 0 = until next open
        const lw  = Math.max(1, pget(p, 'lineWidth', 1));
        const op  = Math.max(0.1, Math.min(1, pget(p, 'opacity', 0.85)));
        const labelSize = Math.max(7, pget(p, 'labelSize', 11));

        for (let i = 0; i < this.opens.length; i++) {
            const rec = this.opens[i];
            const next = this.opens[i + 1];
            const sx = rec.chartIdx;

            // Right edge: the next open of any session, else the live bar.
            let ex = next ? next.chartIdx : (this.lastChartIdx + 5);
            if (extendBars > 0) ex = Math.min(ex, sx + extendBars);
            if (ex <= sx) ex = sx + 1;

            const isNY = rec.primary;
            const zoneLw = isNY ? lw + 1 : lw;
            const fairLw = isNY ? lw + 1 : lw;

            // High / low fair-value rectangle, extended right.
            if (showZone) {
                items.push({
                    tag: 'LineSegments', key: 'oc_t_' + rec.key + '_' + rec.dayKey,
                    lines: [{ tag: 'Line', a: { x: du(sx), y: du(rec.h) }, b: { x: du(ex), y: du(rec.h) } }],
                    lineStyle: { lineWidth: zoneLw, color: rec.color, opacity: op * 0.6 }
                });
                items.push({
                    tag: 'LineSegments', key: 'oc_b_' + rec.key + '_' + rec.dayKey,
                    lines: [{ tag: 'Line', a: { x: du(sx), y: du(rec.l) }, b: { x: du(ex), y: du(rec.l) } }],
                    lineStyle: { lineWidth: zoneLw, color: rec.color, opacity: op * 0.6 }
                });
                // Left edge — marks WHERE the opening candle is.
                items.push({
                    tag: 'LineSegments', key: 'oc_e_' + rec.key + '_' + rec.dayKey,
                    lines: [{ tag: 'Line', a: { x: du(sx), y: du(rec.l) }, b: { x: du(sx), y: du(rec.h) } }],
                    lineStyle: { lineWidth: zoneLw, color: rec.color, opacity: op }
                });
            }

            // Fair price line (the candle's OPEN == pre-open close).
            if (showFair) {
                items.push({
                    tag: 'LineSegments', key: 'oc_f_' + rec.key + '_' + rec.dayKey,
                    lines: [{ tag: 'Line', a: { x: du(sx), y: du(rec.o) }, b: { x: du(ex), y: du(rec.o) } }],
                    lineStyle: { lineWidth: fairLw, color: rec.color, opacity: op }
                });
            }

            // Label above the candle.
            if (showLabel) {
                const pad = Math.max(rec.h - rec.l, Math.abs(rec.c) * 0.0003);
                items.push({
                    tag: 'Text', key: 'oc_lbl_' + rec.key + '_' + rec.dayKey,
                    text: rec.label + '  fair ' + rec.o.toFixed(2),
                    point: { x: du(sx), y: du(rec.h + pad) },
                    style: { fontSize: labelSize, fontWeight: rec.primary ? 'bold' : 'normal', fill: rec.color },
                    textAlignment: 'leftMiddle'
                });
            }
        }
    }
}

module.exports = {
    name: 'SD_OpeningCandle',
    description: 'Session Open / Fair Price candle marker',
    calculator: OpeningCandleMarker,
    inputType: 'bars',
    tags: ['SD'],
    params: {
        // Shift bar timestamps to your exchange wall clock.
        // -4 = US Eastern (EDT). Use -5 for true winter EST.
        tzOffsetHours: predef.paramSpecs.number(-4, 1, -12),
        // Session toggles (1 = mark its open candle, 0 = ignore).
        sessNewsAM:  predef.paramSpecs.number(1, 1, 0),   // 08:30 news
        sessNY:      predef.paramSpecs.number(1, 1, 0),   // 09:30 NY open
        sessNYPM:    predef.paramSpecs.number(1, 1, 0),   // 14:00 NY PM
        sessEvening: predef.paramSpecs.number(0, 1, 0),   // 18:00 reopen
        sessAsia:    predef.paramSpecs.number(0, 1, 0),   // 20:00 Asia
        sessLondon:  predef.paramSpecs.number(0, 1, 0),   // 03:00 London
        // How many minutes after an open the first bar may be and still
        // count as that session's open candle. Keep below 60 so the 08:30
        // news open never bleeds onto the 09:30 NY bar. 1-5m charts: 15 is
        // plenty; raise only for larger timeframes.
        openToleranceMin: predef.paramSpecs.number(15, 1, 0),
        // What to draw.
        showZone:     predef.paramSpecs.number(1, 1, 0),  // high/low rectangle
        showFairLine: predef.paramSpecs.number(1, 1, 0),  // open / fair price line
        showLabel:    predef.paramSpecs.number(1, 1, 0),
        // 0 = extend each box until the next session open (or the live bar).
        // >0 = cap the box width at this many bars.
        extendBars:  predef.paramSpecs.number(0, 5, 0),
        // Appearance.
        lineWidth:   predef.paramSpecs.number(1, 1, 1),
        opacity:     predef.paramSpecs.number(0.85, 0.05, 0.1),
        labelSize:   predef.paramSpecs.number(11, 1, 7),
    },
    plots: {},
    schemeStyles: { dark: {} },
};
