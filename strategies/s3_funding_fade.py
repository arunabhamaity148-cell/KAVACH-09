"""
S3 — Funding Rate Extreme Fade
===============================
Entry: funding rate at extreme (+0.05% or -0.02%) → fade the crowd.
       Funding high → too many longs → SHORT.
       Funding low  → too many shorts → LONG.

Stop:   1.5 × ATR
Target: 0.5 × ATR past VWAP toward mean
"""
from __future__ import annotations

from config import (
    ATR_STOP_MULTIPLIER, FUNDING_HIGH_THRESHOLD, FUNDING_LOW_THRESHOLD,
    SCORE_WEIGHTS,
)
from indicators.atr import calculate_atr
from indicators.vwap import session_vwap, vwap_deviation_pct
from strategies.base_strategy import BaseStrategy, StrategyResult


class FundingFadeStrategy(BaseStrategy):
    name        = "Funding Rate Extreme Fade"
    key         = "FUNDING_FADE"
    description = "Fade extreme funding rates — crowded positioning tends to reverse."

    async def evaluate(self, pair, candles, trades, context) -> StrategyResult | None:
        if len(candles) < 15:
            return None

        funding_map = context.get("funding", {})
        f = funding_map.get(pair.symbol)
        if not f:
            return None
        rate_pct = f.get("rate_pct", 0)

        # Only fire on extremes
        if rate_pct >= FUNDING_HIGH_THRESHOLD:
            direction = "SHORT"
        elif rate_pct <= FUNDING_LOW_THRESHOLD:
            direction = "LONG"
        else:
            return None

        price = candles[-1]["close"]
        vwap  = session_vwap(candles)
        atr   = calculate_atr(candles, 14)
        if atr == 0:
            return None

        # ─── Conditions ─────────────────────────────────────────
        vwap_dev = vwap_deviation_pct(price, vwap)
        if direction == "SHORT":
            vwap_extended = vwap_dev > 0.20    # price above VWAP supports short
        else:
            vwap_extended = vwap_dev < -0.20

        # Volume confirmation — declining momentum (we want fade)
        vols = [c["volume"] for c in candles[-6:]]
        avg_vol = sum(vols[:-3]) / max(len(vols[:-3]), 1)
        recent_vol = sum(vols[-3:]) / 3
        volume_declining = recent_vol < avg_vol if avg_vol > 0 else False

        # Price structure — exhausted trend
        if direction == "SHORT":
            price_structure = candles[-1]["high"] <= max(c["high"] for c in candles[-6:-1])
        else:
            price_structure = candles[-1]["low"] >= min(c["low"] for c in candles[-6:-1])

        # CVD divergence check (lightweight)
        funding_neutral = False     # by definition not neutral in this strategy

        conditions = {
            "cvd_divergence":   False,         # not required for this strategy
            "vwap_extended":    vwap_extended,
            "volume_declining": volume_declining,
            "price_structure":  price_structure,
            "funding_neutral":  False,         # extreme = trigger, not neutral
        }
        # Override funding_neutral weight with the actual trigger strength
        # so the strategy still scores high when extreme funding is detected.
        score = sum(SCORE_WEIGHTS[k] for k, v in conditions.items() if v)
        # Bonus: extreme funding itself contributes 30 (same weight as cvd_divergence)
        if direction == "SHORT" and rate_pct >= FUNDING_HIGH_THRESHOLD:
            score += 30
        elif direction == "LONG" and rate_pct <= FUNDING_LOW_THRESHOLD:
            score += 30
        score = min(100, score)

        # ─── Levels ─────────────────────────────────────────────
        stop_dist = atr * ATR_STOP_MULTIPLIER
        if direction == "SHORT":
            entry  = price * 0.9995
            stop   = price + stop_dist
            target = min(vwap, price - stop_dist * 2)
        else:
            entry  = price * 1.0005
            stop   = price - stop_dist
            target = max(vwap, price + stop_dist * 2)

        rr = self.compute_rr(entry, stop, target)
        if rr < 1.5:
            return None

        warnings = []
        if rate_pct > 0.10:
            warnings.append(f"⚠️ Funding extremely high ({rate_pct:+.3f}%) — squeeze risk")
        if rate_pct < -0.05:
            warnings.append(f"⚠️ Funding very negative ({rate_pct:+.3f}%) — short squeeze risk")

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
                "funding_rate":  f"{rate_pct:+.3f}%",
                "threshold":     f"±{FUNDING_HIGH_THRESHOLD}%",
                "vwap":          f"${vwap:.2f}",
                "vwap_dev_pct":  f"{vwap_dev:+.2f}%",
                "atr":           f"${atr:.2f}",
            },
            warnings=warnings,
            rr=rr,
            atr=round(atr, 2),
            vwap=round(vwap, 2),
        )
