"""
S2 — VWAP Reclaim Scalp
========================
Entry: price dipped below VWAP then reclaims it (closes back above)
       on rising volume → LONG. Mirror for SHORT (price above VWAP,
       loses it, reclaims from below on rising volume).

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

        # Last 3 candles — detect reclaim
        c0, c1, c2 = candles[-3], candles[-2], candles[-1]
        price = c2["close"]

        # LONG reclaim: c0 closed below VWAP, c2 closes back above
        long_reclaim  = c0["close"] < vwap and c1["close"] < vwap and c2["close"] > vwap
        short_reclaim = c0["close"] > vwap and c1["close"] > vwap and c2["close"] < vwap

        if not (long_reclaim or short_reclaim):
            return None

        direction = "LONG" if long_reclaim else "SHORT"

        # Volume rising across the 3 candles
        vols = [c0["volume"], c1["volume"], c2["volume"]]
        volume_rising = vols[2] > vols[1] > vols[0] and vols[2] > sum(vols) / 3
        avg_vol = sum(c["volume"] for c in candles[-20:]) / 20
        volume_strong = vols[2] > avg_vol

        # Price structure confirmation
        if direction == "LONG":
            swing = min(c0["low"], c1["low"])
            price_structure = c2["close"] > c1["close"]
        else:
            swing = max(c0["high"], c1["high"])
            price_structure = c2["close"] < c1["close"]

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
            warnings.append("Reclaim volume below 20-candle average — weak confirmation")
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
