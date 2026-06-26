"""
S1 — CVD Divergence Scalp
=========================
Entry: bearish divergence (price ↑, CVD ↓) → SHORT
       bullish divergence (price ↓, CVD ↑) → LONG

Stop:   1.5 × ATR(14) on 5m
Target: VWAP (mean-reversion scalp)
"""
from __future__ import annotations

from typing import Any

from config import (
    ATR_STOP_MULTIPLIER, CVD_MIN_CANDLES, CVD_MIN_DIVERGENCE_PCT,
    CVD_MIN_PRICE_MOVE_PCT, CVD_MIN_VOLUME_PCT_AVG, FUNDING_NEUTRAL_HIGH,
    FUNDING_NEUTRAL_LOW, SCORE_WEIGHTS,
)
from indicators.atr import calculate_atr, volatility_band
from indicators.cvd import calculate_cvd, detect_cvd_divergence
from indicators.vwap import session_vwap, vwap_deviation_pct
from strategies.base_strategy import BaseStrategy, StrategyResult


class CvdDivergenceStrategy(BaseStrategy):
    name        = "CVD Divergence Scalp"
    key         = "CVD_DIVERGENCE"
    description = "Price makes higher high but CVD makes lower high (or mirror). Fade the move."

    async def evaluate(self, pair, candles, trades, context) -> StrategyResult | None:
        if len(candles) < 15 or len(trades) < 50:
            return None

        # ─── 1. CVD per candle bucket (last 10 candles) ─────────
        last_n = candles[-15:]
        cvd_per_candle: list[float] = []
        for c in last_n:
            # Approximate: trades that happened during this candle's range
            ts = c["timestamp"]
            bucket = [t for t in trades if _within(t, c)]
            cvd_per_candle.append(calculate_cvd(bucket))

        # Running cumulative CVD
        cum_cvd: list[float] = []
        acc = 0.0
        for v in cvd_per_candle:
            acc += v
            cum_cvd.append(acc)

        prices = [c["close"] for c in last_n]
        div = detect_cvd_divergence(
            prices, cum_cvd, window=CVD_MIN_CANDLES,
            min_price_move_pct=CVD_MIN_PRICE_MOVE_PCT,
            min_div_pct=CVD_MIN_DIVERGENCE_PCT,
        )
        if div == "NO_DIVERGENCE":
            return None

        direction = "SHORT" if div == "BEARISH_DIVERGENCE" else "LONG"

        # ─── 2. Conditions check ────────────────────────────────
        # Volume declining (current vs avg of previous 10)
        vols = [c["volume"] for c in last_n]
        avg_vol = sum(vols[:-3]) / max(len(vols[:-3]), 1)
        recent_vol = sum(vols[-3:]) / 3
        volume_declining = recent_vol < avg_vol * (CVD_MIN_VOLUME_PCT_AVG / 100) \
                           if avg_vol > 0 else False

        # VWAP extended
        vwap = session_vwap(candles)
        price = candles[-1]["close"]
        vwap_dev = vwap_deviation_pct(price, vwap)
        vwap_extended = (
            (direction == "SHORT" and vwap_dev > 0.3) or
            (direction == "LONG"  and vwap_dev < -0.3)
        )

        # Price structure (higher high for short, lower low for long)
        lookback = 6
        recent_highs = [c["high"] for c in candles[-lookback:]]
        recent_lows  = [c["low"]  for c in candles[-lookback:]]
        if direction == "SHORT":
            price_structure = recent_highs[-1] >= max(recent_highs[:-1])
        else:
            price_structure = recent_lows[-1] <= min(recent_lows[:-1])

        # Funding filter
        funding = context.get("funding", {}).get(pair.symbol, {})
        rate_pct = funding.get("rate_pct", 0)
        if direction == "SHORT":
            funding_neutral = rate_pct < FUNDING_NEUTRAL_HIGH
        else:
            funding_neutral = rate_pct > FUNDING_NEUTRAL_LOW

        conditions = {
            "cvd_divergence":    True,                # core signal always met here
            "vwap_extended":     vwap_extended,
            "volume_declining":  volume_declining,
            "price_structure":   price_structure,
            "funding_neutral":   funding_neutral,
        }
        score = sum(SCORE_WEIGHTS[k] for k, v in conditions.items() if v)

        # ─── 3. Levels ──────────────────────────────────────────
        atr = calculate_atr(candles, 14)
        stop_dist = max(atr * ATR_STOP_MULTIPLIER, price * 0.0015)

        if direction == "SHORT":
            entry  = price * 0.9995         # limit sell slightly below current
            stop   = price + stop_dist
            target = vwap if vwap < price else price - stop_dist * 2
        else:
            entry  = price * 1.0005
            stop   = price - stop_dist
            target = vwap if vwap > price else price + stop_dist * 2

        rr = self.compute_rr(entry, stop, target)
        if rr < 1.5:
            return None

        warnings: list[str] = []
        if not funding_neutral:
            warnings.append(
                f"Funding rate elevated ({rate_pct:+.3f}%) — size 50% কমাও"
            )
        btc_trend = context.get("btc_trend", {})
        if direction == "SHORT" and btc_trend.get("trend") == "BULLISH":
            warnings.append(
                f"BTC 15m trend: {btc_trend.get('change_pct', 0):+.2f}% bullish — counter-trend risk"
            )
        elif direction == "LONG" and btc_trend.get("trend") == "BEARISH":
            warnings.append(
                f"BTC 15m trend: {btc_trend.get('change_pct', 0):+.2f}% bearish — counter-trend risk"
            )
        if not volume_declining:
            warnings.append("Volume not declining — momentum still strong")

        return StrategyResult(
            strategy=self.name,
            strategy_key=self.key,
            pair=pair.symbol,
            direction=direction,
            entry_price=round(entry, pair.price_precision),
            stop_price=round(stop, pair.price_precision),
            target_price=round(target, pair.price_precision),
            score=score,
            confidence=self.confidence_from_score(score),
            conditions=conditions,
            condition_details={
                "price_higher_high": f"${prices[-1]:.2f} vs ${prices[-4]:.2f}",
                "cvd_lower_high":    f"{cum_cvd[-1]:.0f} vs {cum_cvd[-4]:.0f}",
                "volume_vs_avg":     f"{recent_vol:.0f} vs avg {avg_vol:.0f}",
                "vwap_dev_pct":      f"{vwap_dev:+.2f}%",
                "funding_rate":      f"{rate_pct:+.3f}%",
            },
            warnings=warnings,
            rr=rr,
            atr=round(atr, 2),
            vwap=round(vwap, 2),
            extra={"divergence_type": div},
        )


def _within(trade: dict, candle: dict) -> bool:
    """Cheap time-bucket check; falls back to price-range check."""
    # If trade has time & candle has timestamp, compare numerically
    t_time = trade.get("time")
    if t_time and candle.get("timestamp"):
        try:
            from datetime import datetime, timezone
            ct = datetime.fromisoformat(candle["timestamp"].replace("Z", "+00:00"))
            candle_ms = int(ct.timestamp() * 1000)
            # 5m candle window
            return candle_ms <= t_time < candle_ms + 5 * 60 * 1000
        except Exception:
            pass
    # Fallback: price within candle range
    return candle["low"] <= trade["price"] <= candle["high"]
