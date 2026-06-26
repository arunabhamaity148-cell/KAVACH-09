"""
KAVACH-09 — Signal Verifier
============================
Two jobs:
  1. Score a signal (already done by strategy, this is double-check + recompute)
  2. /verify ETH SHORT  — manual trade idea check
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from config import SCORE_WEIGHTS
from data import market_feed, coinglass_feed
from indicators.atr import calculate_atr
from indicators.cvd import calculate_cvd, detect_cvd_divergence
from indicators.vwap import session_vwap, vwap_deviation_pct
from strategies.base_strategy import StrategyResult

log = logging.getLogger("kavach.verifier")


async def verify_manual_idea(pair_str: str, direction: str) -> dict[str, Any]:
    """
    User-driven verify: /verify ETH SHORT
    Returns a dict with all 5 condition checks + suggested levels.
    """
    from config import resolve_pair
    pair = resolve_pair(pair_str)
    if not pair:
        return {"error": f"Unknown pair: {pair_str}"}
    direction = direction.upper()
    if direction not in ("LONG", "SHORT"):
        return {"error": "Direction must be LONG or SHORT"}

    candles, trades = await asyncio.gather(
        market_feed.get_candles(pair, "5m", 200),
        market_feed.get_agg_trades_window(pair, 15 * 60_000),
    )
    funding_map = await coinglass_feed.get_funding_rates()
    funding = funding_map.get(pair.symbol, {})

    if not candles:
        return {"error": "Failed to fetch candles"}

    price = candles[-1]["close"]
    vwap  = session_vwap(candles)
    atr   = calculate_atr(candles, 14)
    vwap_dev = vwap_deviation_pct(price, vwap)

    # ─── 5 condition checks (CVD-style manual verify) ─────────
    # 1. Price higher high / lower low (last 6 candles)
    last6 = candles[-6:]
    if direction == "SHORT":
        price_hh = last6[-1]["high"] >= max(c["high"] for c in last6[:-1])
        price_str = f"${last6[-1]['high']:.2f} vs ${max(c['high'] for c in last6[:-1]):.2f}"
    else:
        price_hh = last6[-1]["low"] <= min(c["low"] for c in last6[:-1])
        price_str = f"${last6[-1]['low']:.2f} vs ${min(c['low'] for c in last6[:-1]):.2f}"

    # 2. CVD divergence (split trades into 3 buckets by time)
    trades_sorted = sorted(trades, key=lambda t: t.get("time", 0))
    if len(trades_sorted) >= 30:
        n = len(trades_sorted) // 3
        b1, b2, b3 = trades_sorted[:n], trades_sorted[n:2*n], trades_sorted[2*n:]
        cvd1, cvd2, cvd3 = calculate_cvd(b1), calculate_cvd(b2), calculate_cvd(b3)
        if direction == "SHORT":
            cvd_lh = cvd3 < cvd2    # cvd lower high while price higher high
        else:
            cvd_lh = cvd3 > cvd2
        cvd_str = f"{cvd3:.0f} vs {cvd2:.0f}"
    else:
        cvd_lh = False
        cvd_str = "insufficient trade data"

    # 3. Volume declining
    vols = [c["volume"] for c in last6]
    avg_vol = sum(vols[:-3]) / max(len(vols[:-3]), 1)
    recent_vol = sum(vols[-3:]) / 3
    vol_declining = recent_vol < avg_vol if avg_vol > 0 else False
    vol_str = f"{recent_vol:.0f} vs avg {avg_vol:.0f}"

    # 4. VWAP extended
    if direction == "SHORT":
        vwap_ext = vwap_dev > 0.20
    else:
        vwap_ext = vwap_dev < -0.20
    vwap_str = f"{vwap_dev:+.2f}%"

    # 5. Funding neutral
    rate_pct = funding.get("rate_pct", 0)
    if direction == "SHORT":
        funding_neutral = rate_pct < 0.040
    else:
        funding_neutral = rate_pct > -0.015
    funding_str = f"{rate_pct:+.3f}%"

    conditions = {
        "price_higher_high":   {"met": price_hh,    "detail": price_str},
        "cvd_lower_high":      {"met": cvd_lh,      "detail": cvd_str},
        "volume_declining":    {"met": vol_declining,"detail": vol_str},
        "vwap_extended":       {"met": vwap_ext,    "detail": vwap_str},
        "funding_neutral":     {"met": funding_neutral, "detail": funding_str},
    }
    met_count = sum(1 for c in conditions.values() if c["met"])

    # ─── Suggested levels ─────────────────────────────────────
    stop_dist = max(atr * 1.5, price * 0.0015)
    if direction == "SHORT":
        entry  = price * 0.9995
        stop   = price + stop_dist
        target = vwap if vwap < price else price - stop_dist * 2
    else:
        entry  = price * 1.0005
        stop   = price - stop_dist
        target = vwap if vwap > price else price + stop_dist * 2

    rr = round(abs(target - entry) / abs(entry - stop), 2) if abs(entry - stop) > 0 else 0

    # Verdict logic
    if met_count >= 5:
        verdict = "✅ All conditions met — clean setup"
    elif met_count == 4:
        verdict = "⚠️ 4/5 conditions met — proceed with caution"
    elif met_count == 3:
        verdict = "🟡 3/5 conditions met — risky, reduce size"
    else:
        verdict = "❌ Too few conditions — skip this trade"

    # Suggestion based on which conditions failed
    suggestion = ""
    if not funding_neutral:
        suggestion = "Funding elevated — size 50% কমাও"
    elif not vwap_ext:
        suggestion = "Price not extended from VWAP — wait for better entry"
    elif not vol_declining:
        suggestion = "Volume still strong — momentum may continue"

    return {
        "pair":            pair.symbol,
        "direction":       direction,
        "current_price":   price,
        "vwap":            round(vwap, 2),
        "atr":             round(atr, 2),
        "conditions":      conditions,
        "met_count":       met_count,
        "verdict":         verdict,
        "suggestion":      suggestion,
        "entry":           round(entry, pair.price_precision),
        "stop":            round(stop, pair.price_precision),
        "target":          round(target, pair.price_precision),
        "rr":              rr,
    }


def score_signal(result: StrategyResult) -> int:
    """Recompute score from conditions (sanity check)."""
    return sum(SCORE_WEIGHTS[k] for k, v in result.conditions.items() if v)
