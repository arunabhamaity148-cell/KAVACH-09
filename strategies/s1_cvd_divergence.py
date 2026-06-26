"""
S1 — CVD Divergence Scalp
=========================
Entry: bearish divergence (price ↑, CVD ↓) → SHORT
       bullish divergence (price ↓, CVD ↑) → LONG

ISSUE 6 FIX:
  Old approach bucketed trades into candles, but REST trades only cover
  last 15 min → only 3 candles get CVD data, rest are 0 → divergence
  detector always sees flat CVD → NO_DIVERGENCE.

  New approach: compute total tape CVD and compare against price move
  over the same window. Simpler, reliable, no bucketing needed.

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
from indicators.cvd import calculate_cvd
from indicators.vwap import session_vwap, vwap_deviation_pct
from strategies.base_strategy import BaseStrategy, StrategyResult


class CvdDivergenceStrategy(BaseStrategy):
    name        = "CVD Divergence Scalp"
    key         = "CVD_DIVERGENCE"
    description = "Price makes higher high but CVD is net negative (or mirror). Fade the move."

    async def evaluate(self, pair, candles, trades, context) -> StrategyResult | None:
        if len(candles) < 15:
            return None

        # ─── 1. ISSUE 6 FIX: tape-level CVD vs price move ──────
        # Use total tape CVD (net buy − sell) vs price direction.
        # Works even when trades only cover last 15 min (3 candles).
        last_n = candles[-15:]
        prices = [c["close"] for c in last_n]

        # Price direction over the window
        p_now  = prices[-1]
        p_prev = prices[-CVD_MIN_CANDLES - 1]   # 4 candles back
        if p_prev == 0:
            return None
        price_move_pct = ((p_now - p_prev) / p_prev) * 100

        if abs(price_move_pct) < CVD_MIN_PRICE_MOVE_PCT:
            return None   # price hasn't moved enough to form divergence

        # Net CVD from the full trade tape
        if trades:
            tape_cvd = calculate_cvd(trades)
            tape_buy  = sum(float(t.get("volume", 0)) for t in trades if t.get("side") == "buy")
            tape_sell = sum(float(t.get("volume", 0)) for t in trades if t.get("side") == "sell")
            total_vol = tape_buy + tape_sell
            cvd_bias  = tape_cvd / total_vol if total_vol > 0 else 0   # -1 to +1
        else:
            tape_cvd  = 0.0
            cvd_bias  = 0.0

        # Divergence: price up but CVD net negative → bearish divergence
        # Divergence: price down but CVD net positive → bullish divergence
        if price_move_pct > 0 and cvd_bias < -(CVD_MIN_DIVERGENCE_PCT / 100):
            direction = "SHORT"
        elif price_move_pct < 0 and cvd_bias > (CVD_MIN_DIVERGENCE_PCT / 100):
            direction = "LONG"
        else:
            return None

        # ─── 2. Conditions check ────────────────────────────────
        vols = [c["volume"] for c in last_n]
        avg_vol    = sum(vols[:-3]) / max(len(vols[:-3]), 1)
        recent_vol = sum(vols[-3:]) / 3
        volume_declining = (recent_vol < avg_vol * (CVD_MIN_VOLUME_PCT_AVG / 100)) if avg_vol > 0 else False

        vwap     = session_vwap(candles)
        price    = candles[-1]["close"]
        vwap_dev = vwap_deviation_pct(price, vwap)
        vwap_extended = (
            (direction == "SHORT" and vwap_dev > 0.3) or
            (direction == "LONG"  and vwap_dev < -0.3)
        )

        lookback = 6
        recent_highs = [c["high"] for c in candles[-lookback:]]
        recent_lows  = [c["low"]  for c in candles[-lookback:]]
        if direction == "SHORT":
            price_structure = recent_highs[-1] >= max(recent_highs[:-1])
        else:
            price_structure = recent_lows[-1] <= min(recent_lows[:-1])

        funding  = context.get("funding", {}).get(pair.symbol, {})
        rate_pct = funding.get("rate_pct", 0)
        if direction == "SHORT":
            funding_neutral = rate_pct < FUNDING_NEUTRAL_HIGH
        else:
            funding_neutral = rate_pct > FUNDING_NEUTRAL_LOW

        conditions = {
            "cvd_divergence":   True,              # core signal — always met at this point
            "vwap_extended":    vwap_extended,
            "volume_declining": volume_declining,
            "price_structure":  price_structure,
            "funding_neutral":  funding_neutral,
        }
        score = sum(SCORE_WEIGHTS[k] for k, v in conditions.items() if v)

        # ─── 3. Levels ──────────────────────────────────────────
        atr       = calculate_atr(candles, 14)
        stop_dist = max(atr * ATR_STOP_MULTIPLIER, price * 0.0015)

        if direction == "SHORT":
            entry  = price * 0.9995
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
            warnings.append(f"Funding rate elevated ({rate_pct:+.3f}%) — size 50% কমাও")
        btc_trend = context.get("btc_trend", {})
        if direction == "SHORT" and btc_trend.get("trend") == "BULLISH":
            warnings.append(f"BTC 15m trend: {btc_trend.get('change_pct', 0):+.2f}% bullish — counter-trend risk")
        elif direction == "LONG" and btc_trend.get("trend") == "BEARISH":
            warnings.append(f"BTC 15m trend: {btc_trend.get('change_pct', 0):+.2f}% bearish — counter-trend risk")
        if not volume_declining:
            warnings.append("Volume not declining — momentum still strong")

        return StrategyResult(
            strategy=self.name,
            strategy_key=self.key,
            pair=pair.symbol,
            direction=direction,
            entry_price=round(entry, pair.price_precision),
            stop_price=round(stop,  pair.price_precision),
            target_price=round(target, pair.price_precision),
            score=score,
            confidence=self.confidence_from_score(score),
            conditions=conditions,
            condition_details={
                "price_move":    f"{price_move_pct:+.2f}% ({CVD_MIN_CANDLES} candles)",
                "cvd_bias":      f"{cvd_bias:+.3f} (net {'buy' if cvd_bias > 0 else 'sell'})",
                "volume_vs_avg": f"{recent_vol:.0f} vs avg {avg_vol:.0f}",
                "vwap_dev_pct":  f"{vwap_dev:+.2f}%",
                "funding_rate":  f"{rate_pct:+.3f}%",
            },
            warnings=warnings,
            rr=rr,
            atr=round(atr, 2),
            vwap=round(vwap, 2),
            extra={"divergence_type": f"{'BEARISH' if direction == 'SHORT' else 'BULLISH'}_DIVERGENCE"},
        )
