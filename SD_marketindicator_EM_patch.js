// ═══════════════════════════════════════════════════════════════════════
// SD_MarketReader — EXPECTED-MOVE LEVELS PATCH
// Adds options-implied daily/weekly/monthly expected-move bands.
// Apply these 6 edits to your current SD_marketindicator. Backward-compatible:
// leave the params at 0 and nothing changes. Test on a Tradovate REPLAY/SIM
// session before trading live.
// ═══════════════════════════════════════════════════════════════════════


// ─── PATCH 1 — PARAMS ───────────────────────────────────────────────────
// In the `params: { ... }` block, FIND this line:
//     tzOffsetHours: predef.paramSpecs.number(-4,1,-12),
// ADD immediately after it:

        // Expected-move levels (options-implied). Paste the 1-sigma ATM
        // straddle in points per horizon (from morning_em.py or the NQ chain).
        // 0 = off. Daily drives the sigma bands; weekly/monthly draw their own.
        expMoveDaily:   predef.paramSpecs.number(0, 25, 0),
        expMoveWeekly:  predef.paramSpecs.number(0, 50, 0),
        expMoveMonthly: predef.paramSpecs.number(0, 100, 0),


// ─── PATCH 2 — CONSTRUCTOR STATE ────────────────────────────────────────
// In init(), FIND the line:  this.expMid=0;
// (the one just before  this.calDayKey='' ...)
// REPLACE it with:

        this.expMid=0;
        // Weekly/monthly expected-move bands (anchored at the week/month open;
        // tracked by timestamp so tick charts behave like time charts).
        this.weekOpen=0; this.wk1H=0; this.wk1L=0;
        this.monthKey=''; this.monthOpen=0; this.mo1H=0; this.mo1L=0;


// ─── PATCH 3 — SIGMA ENGINE (_computeEXP) ───────────────────────────────
// In _computeEXP(open), FIND the block that starts with:
//     const calRange = this.calDayH>0&&this.calDayL<999999 ? ...
// ...through the line:
//     this.exp4H=open+base*1.618; this.exp4L=open-base*1.618;
// REPLACE that whole block with:

        const calRange = this.calDayH>0&&this.calDayL<999999 ? this.calDayH-this.calDayL : 0;
        const emDaily = (this.props && this.props.expMoveDaily) || 0;
        this.expMid = open;
        if(emDaily > 0){
            // TRUE options expected-move levels: 0.5σ/1σ/1.5σ/2σ of the implied
            // daily move (the ATM straddle you pasted). No range floor.
            this.exp1H=open+emDaily*0.5; this.exp1L=open-emDaily*0.5;
            this.exp2H=open+emDaily;     this.exp2L=open-emDaily;
            this.exp3H=open+emDaily*1.5; this.exp3L=open-emDaily*1.5;
            this.exp4H=open+emDaily*2.0; this.exp4L=open-emDaily*2.0;
        } else {
            let base = this.avgSessRange>0 ? this.avgSessRange
                     : calRange>150        ? calRange
                     : floor;
            base = Math.max(base, floor);
            this.exp1H=open+base*0.618; this.exp1L=open-base*0.618;
            this.exp2H=open+base;       this.exp2L=open-base;
            this.exp3H=open+base*1.382; this.exp3L=open-base*1.382;
            this.exp4H=open+base*1.618; this.exp4L=open-base*1.618;
        }


// ─── PATCH 4 — CAPTURE WEEK/MONTH OPEN + BUILD BANDS ────────────────────
// In map(), in the "Weekly key" section, FIND:
//         this.weekKey=weekKey; this.weekHigh=h; this.weekLow=l;
//     } else if(isNewBar) {
//         if(h>this.weekHigh) this.weekHigh=h;
//         if(l<this.weekLow)  this.weekLow=l;
//     }
// REPLACE it with:

                this.weekKey=weekKey; this.weekHigh=h; this.weekLow=l; this.weekOpen=o;
            } else if(isNewBar) {
                if(h>this.weekHigh) this.weekHigh=h;
                if(l<this.weekLow)  this.weekLow=l;
            }
            // Monthly anchor + weekly/monthly expected-move bands
            const _monthKey = wkBase.y+'-'+wkBase.m;
            if(isNewBar && _monthKey!==this.monthKey){ this.monthKey=_monthKey; this.monthOpen=o; }
            const _emW=(p.expMoveWeekly!=null?p.expMoveWeekly:0)||0;
            const _emM=(p.expMoveMonthly!=null?p.expMoveMonthly:0)||0;
            this.wk1H=(this.weekOpen>0&&_emW>0)?this.weekOpen+_emW:0;
            this.wk1L=(this.weekOpen>0&&_emW>0)?this.weekOpen-_emW:0;
            this.mo1H=(this.monthOpen>0&&_emM>0)?this.monthOpen+_emM:0;
            this.mo1L=(this.monthOpen>0&&_emM>0)?this.monthOpen-_emM:0;


// ─── PATCH 5 — PASS BANDS TO THE PLOTTER (_ret) ─────────────────────────
// In map(), FIND the block:
//     if(this.exp1H>0){
//         _ret.sessId   = this.sessKey;
//         ... _ret.expM=this.expMid;
//     }
// ADD immediately after its closing } :

            _ret.weekId=this.weekKey; _ret.wkH=this.wk1H; _ret.wkL=this.wk1L;
            _ret.monthId=this.monthKey; _ret.moH=this.mo1H; _ret.moL=this.mo1L;


// ─── PATCH 6 — DRAW THE BANDS (expPlotter) ──────────────────────────────
// In function expPlotter(...), FIND the LAST daily-band draw line:
//     if(e4L) canvas.drawLine(pt.offset(sx,e4L), pt.offset(ex,e4L), {color:'#FF2244',lineWidth:2,opacity:0.9});
// ADD immediately after it (still inside the try):

        // Weekly (purple) / monthly (pink) expected-move bands at week/month open
        let wkH=null,wkL=null,wkSx=null, moH=null,moL=null,moSx=null;
        const curWk=indicator.weekKey, curMo=indicator.monthKey;
        for(let i=0;i<history.data.length;i++){
            const it=history.get(i); if(!it) continue;
            const xx=pt.x.get(it);
            if(it.weekId===curWk && it.wkH>0){ if(wkSx===null) wkSx=xx; wkH=it.wkH; wkL=it.wkL; }
            if(it.monthId===curMo && it.moH>0){ if(moSx===null) moSx=xx; moH=it.moH; moL=it.moL; }
        }
        if(wkH){ canvas.drawLine(pt.offset(wkSx,wkH),pt.offset(ex,wkH),{color:'#B388FF',lineWidth:2,opacity:0.8});
                 canvas.drawLine(pt.offset(wkSx,wkL),pt.offset(ex,wkL),{color:'#B388FF',lineWidth:2,opacity:0.8}); }
        if(moH){ canvas.drawLine(pt.offset(moSx,moH),pt.offset(ex,moH),{color:'#FF88DD',lineWidth:2,opacity:0.7});
                 canvas.drawLine(pt.offset(moSx,moL),pt.offset(ex,moL),{color:'#FF88DD',lineWidth:2,opacity:0.7}); }


// ═══════════════════════════════════════════════════════════════════════
// DONE. Then in the indicator settings set:
//   expMoveDaily   = today's daily ATM straddle (e.g. 310)
//   expMoveWeekly  = the weekly straddle  (optional)
//   expMoveMonthly = the monthly straddle (optional)
// Daily → cyan/orange/yellow/red bands become true 0.5/1/1.5/2σ levels.
// Weekly → purple band.  Monthly → pink band.  All anchor by timestamp.
// ═══════════════════════════════════════════════════════════════════════
