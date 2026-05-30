"""
EM dashboard audit — runs all sections and validates the math.

Exit 0 = clean. Exit 1 = a section failed a structural or math check.

Intended to be run twice daily by the GitHub Action (see
.github/workflows/audit-em.yml) — once shortly after the US open and once
after the close — so data-feed / math regressions surface immediately
instead of being noticed mid-session. Can also be run manually:

    python audit_em.py
"""

from __future__ import annotations

import sys
import warnings

warnings.filterwarnings("ignore")

import four_em as fe


def run_audit() -> tuple[int, list[str], list[str], list[str]]:
    ok: list[str] = []
    warn: list[str] = []
    fail: list[str] = []

    def OK(m: str) -> None:
        ok.append(m)

    def WARN(m: str) -> None:
        warn.append(m)
        print(f"  ! {m}")

    def FAIL(m: str) -> None:
        fail.append(m)
        print(f"  ✗ {m}")

    # ── compute()
    try:
        d = fe.compute()
    except Exception as e:
        FAIL(f"compute() raised: {e}")
        return 1, ok, warn, fail
    OK(f"compute() returned, asOf={d.get('asOf')}")

    # ── top-level keys
    for k in ("asOf", "regime", "intraday", "premarket", "openRange", "gamma"):
        if k not in d:
            FAIL(f"missing top-level key '{k}'")
        else:
            OK(f"top-level '{k}' present")

    # ── intraday
    intr = d.get("intraday", [])
    codes = {r.get("code") for r in intr}
    for need in ("ES", "NQ", "YM", "RTY"):
        if need in codes:
            OK(f"intraday: {need} present")
        else:
            FAIL(f"intraday: {need} missing")

    for r in intr:
        if "error" in r:
            WARN(f"intraday {r.get('code')}: {r['error']}")
            continue
        if r.get("emD", 0) <= 0:
            FAIL(f"{r['code']} emD={r.get('emD')} (must be > 0)")
            continue
        # σD math identity: (price − open) / emD
        expected_sig = (r["price"] - r["open"]) / r["emD"]
        if abs(expected_sig - r["sigD"]) > 0.02:
            FAIL(f"{r['code']} sigD mismatch: stated {r['sigD']} vs computed {expected_sig:.2f}")
        # R/S levels must match open ± emD × k using the same rounded emD shown
        for lvl, mult in [("r1", 0.5), ("r2", 1.0), ("s1", -0.5), ("s2", -1.0)]:
            expected = round(r["open"] + r["emD"] * mult, 2)
            if abs(r["lvls"][lvl] - expected) > 0.01:
                FAIL(f"{r['code']} {lvl} mismatch: {r['lvls'][lvl]} vs {expected}")
        if abs(r["sigD"]) > 5:
            WARN(f"{r['code']} sigD={r['sigD']} (unusually large — feed glitch?)")
        OK(f"intraday {r['code']} math OK · sigD={r['sigD']:+.2f}")

    # ── pre-market
    for r in d.get("premarket", {}).get("us", []):
        if "error" in r:
            WARN(f"pm US {r.get('code')}: {r['error']}")
            continue
        # When isRealized we use the actual 9:30 open, not the formula —
        # only enforce close + gap = impliedOpen on the implied path.
        if not r.get("isRealized"):
            expected_io = round(r["cashClose"] + r["overnight"], 2)
            if abs(r["impliedOpen"] - expected_io) > 0.01:
                FAIL(f"pm {r['code']} impliedOpen mismatch: stated {r['impliedOpen']} vs computed {expected_io}")
        OK(f"pm {r['code']}: {('realized' if r.get('isRealized') else 'implied')} · gap {r['overnight']:+.2f} · {r['gapClass']}")

    for r in d.get("premarket", {}).get("global", []):
        if "error" in r:
            WARN(f"pm global {r.get('code')}: {r['error']}")
            continue
        OK(f"pm global {r['code']}: last {r['last']} ({r['pct']:+.2f}%)")

    # ── open range (post-10 ET only)
    o = d.get("openRange", {})
    if o.get("available"):
        for r in o.get("rows", []):
            if "error" in r:
                WARN(f"OR {r.get('code')}: {r['error']}")
                continue
            if not (0 <= r["emFrac"] <= 5):
                FAIL(f"OR {r['code']} emFrac out of range: {r['emFrac']}")
            OK(f"OR {r['code']}: range {r['range']} = {r['emFrac']*100:.0f}% emD → {r['verdict']}")
    else:
        OK(f"OR: {o.get('msg', 'unavailable (pre-10 ET or closed market)')}")

    # ── gamma
    for r in d.get("gamma", []):
        if "error" in r:
            WARN(f"gamma {r.get('ticker')}: {r['error']}")
            continue
        cw, pw, px = r.get("callWall"), r.get("putWall"), r["price"]
        if cw is not None and cw < px - 0.01:
            FAIL(f"gamma {r['ticker']}: call wall {cw} BELOW spot {px}")
        if pw is not None and pw > px + 0.01:
            FAIL(f"gamma {r['ticker']}: put wall {pw} ABOVE spot {px}")
        if r.get("position") in (None, "—"):
            WARN(f"gamma {r['ticker']}: no position computed")
        OK(f"gamma {r['ticker']}: px {px}  CW {cw}  PW {pw} → {r.get('positionShort')}")

    # ── weekly range (1-σ from ATM straddle)
    wk = d.get("weekly", {})
    if not isinstance(wk, dict) or "rows" not in wk:
        FAIL("weekly: missing rows")
    else:
        for r in wk.get("rows", []):
            if "error" in r:
                WARN(f"weekly {r.get('code')}: {r['error']}")
                continue
            # low + (high - low)/2 should equal anchor (= mid) within rounding
            mid_calc = (r["low"] + r["high"]) / 2
            if abs(mid_calc - r["anchor"]) > 0.5:
                FAIL(f"weekly {r['code']}: midpoint {mid_calc} vs anchor {r['anchor']}")
            # emW = high − anchor (symmetric band)
            em_calc = r["high"] - r["anchor"]
            if abs(em_calc - r["emW"]) > 1:
                FAIL(f"weekly {r['code']}: emW {r['emW']} vs (high − anchor) {em_calc:.0f}")
            OK(f"weekly {r['code']}: anchor {r['anchor']} ± {r['emW']} → [{r['low']}, {r['high']}]  (exp {r['expiry']})")

    # ── regime sanity
    regime = d.get("regime")
    if not isinstance(regime, str) or not regime:
        FAIL("regime: missing/empty")
    else:
        OK(f"regime: {regime}")

    return (0 if not fail else 1), ok, warn, fail


def main() -> int:
    print("EM dashboard audit · " + __doc__.strip().splitlines()[0])
    print()
    exit_code, ok, warn, fail = run_audit()
    print()
    print(f"══ SUMMARY · {len(ok)} passed · {len(warn)} warnings · {len(fail)} failures ══")
    if fail:
        print("\nFAILURES:")
        for f in fail:
            print(f"  ✗ {f}")
    if warn:
        print("\nWARNINGS:")
        for w in warn:
            print(f"  ! {w}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
