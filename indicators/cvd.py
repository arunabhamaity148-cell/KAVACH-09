"""
CVD — Cumulative Volume Delta
=============================
CVD = Σ(BUY volume) − Σ(SELL volume)

A trade is BUY if taker crossed the ask (aggressive buy),
SELL if taker crossed the bid (aggressive sell).

Trade tape format expected:
    {"price": float, "volume": float, "side": "buy"|"sell"}
"""
from __future__ import annotations

from typing import Sequence


def calculate_cvd(trades: Sequence[dict]) -> float:
    """Sum of signed trade volumes."""
    cvd = 0.0
    for t in trades:
        side = t.get("side", "").lower()
        vol  = float(t.get("volume", 0))
        if side == "buy":
            cvd += vol
        elif side == "sell":
            cvd -= vol
    return cvd


def cvd_series(trades: Sequence[dict]) -> list[float]:
    """Running CVD per trade — useful for charting."""
    out: list[float] = []
    acc = 0.0
    for t in trades:
        side = t.get("side", "").lower()
        vol  = float(t.get("volume", 0))
        if side == "buy":
            acc += vol
        elif side == "sell":
            acc -= vol
        out.append(acc)
    return out


def detect_cvd_divergence(
    prices: Sequence[float],
    cvds: Sequence[float],
    window: int = 3,
    min_price_move_pct: float = 0.30,
    min_div_pct: float = 15.0,
) -> str:
    """
    Returns:
        "BEARISH_DIVERGENCE"  → price higher high, CVD lower high  → SHORT
        "BULLISH_DIVERGENCE"  → price lower low,  CVD higher low   → LONG
        "NO_DIVERGENCE"       → not a setup
    """
    if len(prices) < window + 1 or len(cvds) < window + 1:
        return "NO_DIVERGENCE"

    p_now, p_prev = prices[-1], prices[-window - 1]
    c_now, c_prev = cvds[-1],  cvds[-window - 1]

    price_move_pct = ((p_now - p_prev) / p_prev) * 100 if p_prev else 0
    if abs(price_move_pct) < min_price_move_pct:
        return "NO_DIVERGENCE"

    # CVD move as % of previous magnitude (avoids divide-by-zero)
    c_prev_mag = abs(c_prev) if abs(c_prev) > 1 else 1.0
    cvd_move_pct = ((c_now - c_prev) / c_prev_mag) * 100

    # Bearish divergence: price ↑ but CVD ↓
    if price_move_pct > 0 and cvd_move_pct < 0 and abs(cvd_move_pct) >= min_div_pct:
        return "BEARISH_DIVERGENCE"
    # Bullish divergence: price ↓ but CVD ↑
    if price_move_pct < 0 and cvd_move_pct > 0 and abs(cvd_move_pct) >= min_div_pct:
        return "BULLISH_DIVERGENCE"
    return "NO_DIVERGENCE"


def volume_at_price(trades: Sequence[dict], bins: int = 20) -> dict[str, list]:
    """
    Build a simple volume-at-price histogram. Useful for /verify.
    Returns {"prices": [...], "buy_vol": [...], "sell_vol": [...]}.
    """
    if not trades:
        return {"prices": [], "buy_vol": [], "sell_vol": []}
    prices = [float(t["price"]) for t in trades]
    lo, hi = min(prices), max(prices)
    if hi == lo:
        hi = lo * 1.001
    step = (hi - lo) / bins
    bins_arr = [lo + i * step for i in range(bins)]
    buy_vol  = [0.0] * bins
    sell_vol = [0.0] * bins
    for t in trades:
        idx = min(int((float(t["price"]) - lo) / step), bins - 1)
        if idx < 0:
            idx = 0
        if t.get("side", "").lower() == "buy":
            buy_vol[idx] += float(t.get("volume", 0))
        else:
            sell_vol[idx] += float(t.get("volume", 0))
    return {"prices": bins_arr, "buy_vol": buy_vol, "sell_vol": sell_vol}
