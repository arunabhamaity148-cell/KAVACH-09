"""
S2 — VWAP Reclaim Scalp
========================
Entry: price dipped below VWAP then reclaims it (closes back above)
       on rising volume → LONG. Mirror for SHORT (price above VWAP,
       loses it, reclaims from below on rising volume).

FIXED:
  - Relaxed from exact 3-candle pattern to "recently below/above + reclaim"
  - Allows 1-3 candles below/above VWAP before reclaim

Stop:   just beyond the swing low/high that preceded the reclaim
Target: 1.5 × ATR or 0.5% from entry (whichever is greater)
"""
from __future__ import annotations

from config import ATR_STOP_MULTIPLIER, SCORE_WEIGHTS, VWAP_RECLAIM_MAX_DEV_PCT, VWAP_RECLAIM_MIN_DEV_PCT
from indicators.atr import calculate_atr
from indicators.vwap import session_vwap, vwap_deviation_pct
from strategies.base_strategy import BaseStrategy, StrategyResult


class VwapReclaimStrategy(BaseStrategy):
    name        = "VWAP Reclaim Scalp"
    key         = "VWAP_RECLAIM"
    description = "Price reclaims VWAP on rising volume — momentum continuation."

    async def evaluate(self, pair, candles, trades, context) -> StrategyResult | None:
        if len(candles) < 20:
            return None

        vwap = session_vwap(candles)
        if vwap == 0:
            return None

        # Check last 5 candles for reclaim pattern
        recent = candles[-5:]
        price = recent[-1]["close"]

        # LONG reclaim: at least 1 candle closed below VWAP, then reclaims above
        below_count = sum(1 for c in recent[:-1] if c["close"] < vwap)
        above_count = sum(1 for c in recent[:-1] if c["close"] > vwap)
        
        long_reclaim  = below_count >= 1 and recent[-1]["close"] > vwap and recent[-2]["close"] < vwap
        short_reclaim = above_count >= 1 and recent[-1]["close"] < vwap and recent[-2]["close"] > vwap

        if not (long_reclaim or short_reclaim):
            return None

        direction = "LONG" if long_reclaim else "SHORT"

        # Volume check — use closed candles only
        closed = [c for c in candles[-6:] if c.get("closed", True)]
        if len(closed) >= 3:
            vols = [closed[-3]["volume"], closed[-2]["volume"], closed[-1]["volume"]]
        else:
            vols = [recent[-3]["volume"], recent[-2]["volume"], recent[-1]["volume"]]
        
        volume_rising = vols[-1] > vols[-2]  # FIX: just need latest > previous
        avg_vol = sum(c["volume"] for c in candles[-20:]) / 20
        volume_strong = vols[-1] > avg_vol * 0.8  # FIX: relaxed from > avg to > 0.8*avg

        # Price structure confirmation
        if direction == "LONG":
            swing = min(c["low"] for c in recent[:-1])
            price_structure = recent[-1]["close"] > recent[-2]["close"]
        else:
            swing = max(c["high"] for c in recent[:-1])
            price_structure = recent[-1]["close"] < recent[-2]["close"]

        # Funding neutral check
        funding = context.get("funding", {}).get(pair.symbol, {})
        rate_pct = funding.get("rate_pct", 0)
        funding_neutral = -0.05 < rate_pct < 0.05

        # VWAP extension check
        dev = vwap_deviation_pct(price, vwap)
        vwap_extended = abs(dev) <= VWAP_RECLAIM_MAX_DEV_PCT and abs(dev) >= VWAP_RECLAIM_MIN_DEV_PCT

        conditions = {
            "cvd_divergence":   False,    # N/A for this strategy
            "vwap_extended":    vwap_extended,
            "volume_declining": volume_rising,   # repurposed: rising vol = confirm
            "price_structure":  price_structure,
            "funding_neutral":  funding_neutral,
        }
        score = sum(SCORE_WEIGHTS[k] for k, v in conditions.items() if v)

        atr = calculate_atr(candles, 14)
        stop_dist = max(atr * ATR_STOP_MULTIPLIER, price * 0.0015)

        if direction == "LONG":
            entry  = price
            stop   = min(swing, price - stop_dist)
            target = price + stop_dist * 1.5
        else:
            entry  = price
            stop   = max(swing, price + stop_dist)
            target = price - stop_dist * 1.5

        rr = self.compute_rr(entry, stop, target)
        if rr < 1.5:
            return None

        warnings: list[str] = []
        if not volume_strong:
            warnings.append("Reclaim volume below average — weak confirmation")
        if not funding_neutral:
            warnings.append(f"Funding rate {rate_pct:+.3f}% — elevated, fade risk")

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
                "vwap":           f"${vwap:.2f}",
                "vwap_dev_pct":   f"{dev:+.2f}%",
                "recent_volumes": " → ".join(f"{v:.0f}" for v in vols),
                "avg_volume":     f"{avg_vol:.0f}",
                "funding_rate":   f"{rate_pct:+.3f}%",
            },
            warnings=warnings,
            rr=rr,
            atr=round(atr, 2),
            vwap=round(vwap, 2),
        )
