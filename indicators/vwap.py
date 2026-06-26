"""
VWAP — Volume Weighted Average Price (session-anchored)
========================================================
Session = UTC day (resets at 00:00 UTC = 05:30 IST).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence


def _session_key(ts: str | int | float, reset_utc_hour: int = 0) -> str:
    """Return YYYY-MM-DD for the current VWAP session."""
    if isinstance(ts, str):
        # ISO 8601 expected
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    elif isinstance(ts, (int, float)):
        # ms or s epoch
        ts_s = ts / 1000 if ts > 1e12 else ts
        dt = datetime.fromtimestamp(ts_s, tz=timezone.utc)
    else:
        dt = datetime.now(timezone.utc)
    if dt.hour < reset_utc_hour:
        dt = dt.replace(day=dt.day - 1) if dt.day > 1 else dt
    return dt.strftime("%Y-%m-%d")


def calculate_vwap(candles: Sequence[dict]) -> float:
    """Classic VWAP over all supplied candles."""
    cum_pv = 0.0
    cum_v  = 0.0
    for c in candles:
        h = float(c["high"]); l = float(c["low"]); cls = float(c["close"])
        v  = float(c.get("volume", 0))
        typical = (h + l + cls) / 3
        cum_pv += typical * v
        cum_v  += v
    return cum_pv / cum_v if cum_v > 0 else 0.0


def session_vwap(candles: Sequence[dict], reset_utc_hour: int = 0) -> float:
    """VWAP restricted to the current session only."""
    if not candles:
        return 0.0
    today_key = _session_key(candles[-1].get("timestamp", ""), reset_utc_hour)
    session_candles = [
        c for c in candles
        if _session_key(c.get("timestamp", ""), reset_utc_hour) == today_key
    ]
    return calculate_vwap(session_candles) if session_candles else calculate_vwap(candles)


def vwap_deviation_pct(price: float, vwap: float) -> float:
    """+ve = price above VWAP, −ve = below."""
    if vwap == 0:
        return 0.0
    return ((price - vwap) / vwap) * 100.0
