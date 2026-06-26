"""
ATR — Average True Range (Wilder's smoothing)
=============================================
Used for stop placement:  stop = 1.5 × ATR-14
"""
from __future__ import annotations

from typing import Sequence


def true_range(candles: Sequence[dict]) -> list[float]:
    out: list[float] = []
    for i in range(len(candles)):
        if i == 0:
            out.append(float(candles[i]["high"]) - float(candles[i]["low"]))
            continue
        h  = float(candles[i]["high"])
        l  = float(candles[i]["low"])
        pc = float(candles[i - 1]["close"])
        out.append(max(h - l, abs(h - pc), abs(l - pc)))
    return out


def calculate_atr(candles: Sequence[dict], period: int = 14) -> float:
    """Wilder's ATR. Returns 0 if insufficient data."""
    if len(candles) < 2:
        return 0.0
    tr_list = true_range(candles)
    if len(tr_list) < period:
        return sum(tr_list) / len(tr_list)

    atr = sum(tr_list[:period]) / period
    for tr in tr_list[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def atr_stop_distance(candles: Sequence[dict], multiplier: float = 1.5, period: int = 14) -> float:
    """Absolute price-distance for stop placement."""
    return calculate_atr(candles, period) * multiplier


def volatility_band(atr: float, price: float) -> str:
    """Categorise volatility for UI display."""
    if price == 0:
        return "unknown"
    atr_pct = (atr / price) * 100
    if atr_pct < 0.3:
        return "low"
    if atr_pct < 0.7:
        return "normal"
    if atr_pct < 1.2:
        return "high"
    return "extreme"
