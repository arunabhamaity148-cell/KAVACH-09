"""
S4 — Liquidation Cascade Mean Reversion
========================================
Entry: total market liquidations > $300M in last hour AND
       one-sided bias (≥70% on one side) → fade the cascade.

If longs are getting liquidated (bias > 0.70), price has likely
overshot down → LONG. If shorts are getting liquidated (bias < 0.30),
price overshot up → SHORT.

Stop:   2.0 × ATR (cascade volatility is high)
Target: VWAP (mean reversion)
"""
from __future__ import annotations

from config import (
    ATR_STOP_MULTIPLIER, LIQ_CASCADE_MIN_RATIO, LIQ_CASCADE_THRESHOLD_USD,
    SCORE_WEIGHTS,
)
from indicators.atr import calculate_atr
from indicators.vwap import session_vwap
from strategies.base_strategy import BaseStrategy, StrategyResult


class LiquidationCascadeStrategy(BaseStrategy):
    name        = "Liquidation Cascade Mean Reversion"
    key         = "LIQ_CASCADE"
    description = "Fade one-sided liquidation cascade — market tends to reverse after flush."

    async def evaluate(self, pair, candles, trades, context) -> StrategyResult | None:
        if len(candles) < 15:
            return None

        liq = context.get("liquidations")
        if not liq:
            return None

        total_usd = liq.get("total_usd", 0)
        if total_usd < LIQ_CASCADE_THRESHOLD_USD:
            return None

        bias = liq.get("bias_ratio", 0.5)
        # bias > 0.70 → longs being liquidated → fade down → LONG
        # bias < 0.30 → shorts being liquidated → fade up → SHORT
        if bias >= LIQ_CASCADE_MIN_RATIO:
            direction = "LONG"
        elif bias <= (1.0 - LIQ_CASCADE_MIN_RATIO):
            direction = "SHORT"
        else:
            return None

        price = candles[-1]["close"]
        vwap  = session_vwap(candles)
        atr   = calculate_atr(candles, 14)
        if atr == 0:
            return None

        # ─── Conditions ─────────────────────────────────────────
        vols = [c["volume"] for c in candles[-6:]]
        avg_vol = sum(vols[:-3]) / max(len(vols[:-3]), 1)
        recent_vol = sum(vols[-3:]) / 3
        # Cascade → volume SPIKING not declining — but for fade we want exhaustion
        volume_declining = recent_vol < avg_vol * 1.5 if avg_vol > 0 else False

        vwap_dev = ((price - vwap) / vwap * 100) if vwap > 0 else 0
        vwap_extended = abs(vwap_dev) > 0.30

        if direction == "LONG":
            price_structure = candles[-1]["close"] > candles[-2]["close"]  # reversal candle
        else:
            price_structure = candles[-1]["close"] < candles[-2]["close"]

        funding = context.get("funding", {}).get(pair.symbol, {})
        rate_pct = funding.get("rate_pct", 0)
        funding_neutral = -0.04 < rate_pct < 0.04

        conditions = {
            "cvd_divergence":   False,
            "vwap_extended":    vwap_extended,
            "volume_declining": volume_declining,
            "price_structure":  price_structure,
            "funding_neutral":  funding_neutral,
        }
        score = sum(SCORE_WEIGHTS[k] for k, v in conditions.items() if v)
        # Cascade trigger itself contributes core weight
        score += 30
        score = min(100, score)

        # ─── Levels — wider stop for cascade volatility ─────────
        stop_dist = atr * 2.0     # wider than other strategies
        if direction == "LONG":
            entry  = price
            stop   = price - stop_dist
            target = vwap if vwap > price else price + stop_dist * 1.5
        else:
            entry  = price
            stop   = price + stop_dist
            target = vwap if vwap < price else price - stop_dist * 1.5

        rr = self.compute_rr(entry, stop, target)
        if rr < 1.2:              # cascades have lower RR bar
            return None

        warnings = []
        warnings.append(
            f"💥 Cascade mode: ${total_usd/1e6:.0f}M liquidated "
            f"(long {bias*100:.0f}% / short {(1-bias)*100:.0f}%)"
        )
        if not funding_neutral:
            warnings.append(f"Funding {rate_pct:+.3f}% — extreme, may extend cascade")

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
                "total_liq_usd":    f"${total_usd:,.0f}",
                "long_liq_pct":     f"{bias*100:.1f}%",
                "short_liq_pct":    f"{(1-bias)*100:.1f}%",
                "threshold":        f"${LIQ_CASCADE_THRESHOLD_USD/1e6:.0f}M",
                "vwap":             f"${vwap:.2f}",
                "atr":              f"${atr:.2f}",
            },
            warnings=warnings,
            rr=rr,
            atr=round(atr, 2),
            vwap=round(vwap, 2),
        )
