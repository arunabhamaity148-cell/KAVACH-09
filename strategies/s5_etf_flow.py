"""
S5 — ETF Flow Session Trade
============================
Entry: US session opens (7 PM IST) + strong ETF inflow/outflow day.
       Inflow  > +$200M → LONG BTC at US open
       Outflow < -$200M → SHORT BTC at US open

Only applies to BTC-USDT. Other pairs are skipped.
Stop:   1.5 × ATR
Target: 2 × ATR (session momentum)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from config import (
    ATR_STOP_MULTIPLIER, ETF_FLOW_BEARISH_USD, ETF_FLOW_BULLISH_USD,
    ETF_US_SESSION_IST_HOUR, SCORE_WEIGHTS,
)
from indicators.atr import calculate_atr
from indicators.vwap import session_vwap
from strategies.base_strategy import BaseStrategy, StrategyResult


class EtfFlowStrategy(BaseStrategy):
    name        = "ETF Flow Session Trade"
    key         = "ETF_FLOW"
    description = "Trade BTC at US session open aligned with ETF flow direction."

    async def evaluate(self, pair, candles, trades, context) -> StrategyResult | None:
        # Only BTC
        if pair.symbol != "BTC-USDT":
            return None
        if len(candles) < 15:
            return None

        etf = context.get("etf")
        if not etf:
            return None

        net_flow = etf.get("net_flow", 0)
        if net_flow >= ETF_FLOW_BULLISH_USD:
            direction = "LONG"
        elif net_flow <= ETF_FLOW_BEARISH_USD:
            direction = "SHORT"
        else:
            return None

        # ─── Session window check (BUG-05 fix: proper wrap-around) ──
        now_ist   = datetime.now(timezone.utc) + timedelta(hours=5.5)
        now_min   = now_ist.hour * 60 + now_ist.minute
        target_min = ETF_US_SESSION_IST_HOUR * 60
        diff = (now_min - target_min) % (24 * 60)
        diff = min(diff, 24 * 60 - diff)   # shortest angular distance
        in_session_window = diff <= 60
        if not in_session_window:
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
        volume_declining = recent_vol < avg_vol if avg_vol > 0 else False

        vwap_dev = ((price - vwap) / vwap * 100) if vwap > 0 else 0
        if direction == "LONG":
            vwap_extended    = vwap_dev > 0.10
            price_structure  = candles[-1]["close"] > candles[-2]["close"]
        else:
            vwap_extended    = vwap_dev < -0.10
            price_structure  = candles[-1]["close"] < candles[-2]["close"]

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
        score += 30   # ETF flow trigger contributes core weight
        score = min(100, score)

        # ─── Levels ─────────────────────────────────────────────
        stop_dist = atr * ATR_STOP_MULTIPLIER
        if direction == "LONG":
            entry  = price
            stop   = price - stop_dist
            target = price + stop_dist * 2
        else:
            entry  = price
            stop   = price + stop_dist
            target = price - stop_dist * 2

        rr = self.compute_rr(entry, stop, target)
        if rr < 1.5:
            return None

        warnings = []
        if etf.get("cumulative_7d", 0) < 0 and direction == "LONG":
            warnings.append("⚠️ 7-day ETF cumulative flow is negative — counter-trend")
        us_h = etf.get("us_session_in_hours", 0)
        if us_h < 0.5:
            warnings.append("⚡ US session opening — high volatility expected")

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
                "etf_net_flow":   f"${net_flow/1e6:+.1f}M",
                "etf_7d_cum":     f"${etf.get('cumulative_7d', 0)/1e6:+.1f}M",
                "us_session_in":  f"{us_h:.1f}h",
                "bias":           etf.get("bias", "NEUTRAL"),
                "vwap":           f"${vwap:.2f}",
                "atr":            f"${atr:.2f}",
                "funding_rate":   f"{rate_pct:+.3f}%",
            },
            warnings=warnings,
            rr=rr,
            atr=round(atr, 2),
            vwap=round(vwap, 2),
        )
