"""
KAVACH-09 — Signal Engine
=========================
Orchestrates all strategies. Runs a scan loop, calls each strategy
per pair, dedupes by cooldown, returns top signals.

Usage:
    engine = SignalEngine(bus)
    await engine.start()                       # background scan loop
    signals = await engine.scan_once()         # manual one-shot
    signals = engine.latest_signals()
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from config import PAIRS, PAIR_ALIASES, ALERT_COOLDOWN_MINUTES, IST_OFFSET_HOURS, SCAN_INTERVAL_SECONDS
from data import market_feed, coinglass_feed, etf_feed
from strategies.base_strategy import BaseStrategy, StrategyResult
from strategies.s1_cvd_divergence import CvdDivergenceStrategy
from strategies.s2_vwap_reclaim  import VwapReclaimStrategy
from strategies.s3_funding_fade  import FundingFadeStrategy
from strategies.s4_liquidation_cascade import LiquidationCascadeStrategy
from strategies.s5_etf_flow      import EtfFlowStrategy
from data.market_feed import MarketFeedBus
import database as db

log = logging.getLogger("kavach.engine")


class SignalEngine:
    def __init__(self, bus: MarketFeedBus | None = None):
        self.bus = bus
        self.strategies: list[BaseStrategy] = [
            CvdDivergenceStrategy(),
            VwapReclaimStrategy(),
            FundingFadeStrategy(),
            LiquidationCascadeStrategy(),
            EtfFlowStrategy(),
        ]
        self._latest: dict[str, StrategyResult] = {}   # pair → latest signal
        self._last_alert_ts: dict[tuple[str, str], float] = {}  # (pair, dir) → ts
        self._scan_count = 0
        self._paused = False
        self._task: asyncio.Task | None = None

    # ─── lifecycle ──────────────────────────────────────────────
    async def start(self) -> None:
        if self._task:
            return
        self._task = asyncio.create_task(self._scan_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def scan_count(self) -> int:
        return self._scan_count

    # ─── one-shot scan ──────────────────────────────────────────
    async def scan_once(self, only_pair: str | None = None) -> list[StrategyResult]:
        """
        Run all strategies across all pairs (or one pair).
        Returns list of StrategyResult that pass minimum score threshold.
        """
        from config import SCORE_MIN
        pairs = [p for p in PAIRS if not only_pair or p.symbol == only_pair]
        if not pairs and only_pair:
            return []

        # ─── Gather context once ────────────────────────────────
        funding_task  = asyncio.create_task(coinglass_feed.get_funding_rates())
        liq_task      = asyncio.create_task(coinglass_feed.get_liquidations_last_hour())
        etf_task      = asyncio.create_task(etf_feed.get_etf_flow())
        funding, liq, etf = await asyncio.gather(funding_task, liq_task, etf_task, return_exceptions=True)
        if isinstance(funding, Exception):
            funding = {}
        if isinstance(liq, Exception):
            liq = None
        if isinstance(etf, Exception):
            etf = None

        # ─── BTC trend for counter-trend warnings ───────────────
        btc_trend = await self._btc_trend()

        context = {
            "funding":   funding,
            "liquidations": liq,
            "etf":       etf,
            "btc_trend": btc_trend,
        }

        results: list[StrategyResult] = []
        for pair in pairs:
            candles, trades = await self._gather_pair_data(pair)
            if not candles:
                continue
            for strat in self.strategies:
                try:
                    r = await strat.evaluate(pair, candles, trades, context)
                except Exception as e:
                    log.debug(f"{strat.key} on {pair.symbol}: {e}")
                    continue
                if r and r.score >= SCORE_MIN:
                    results.append(r)

        # ─── Dedupe: keep highest-scoring signal per (pair, direction) ──
        results.sort(key=lambda r: r.score, reverse=True)
        seen: set[tuple[str, str]] = set()
        deduped: list[StrategyResult] = []
        for r in results:
            key = (r.pair, r.direction)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(r)

        # ─── Stash latest per pair ──────────────────────────────
        for r in deduped:
            self._latest[r.pair] = r

        # ─── Persist every signal to DB ─────────────────────────
        for r in deduped:
            try:
                sig_id = db.insert_signal({
                    "pair":         r.pair,
                    "strategy":     r.strategy_key,
                    "direction":    r.direction,
                    "entry_price":  r.entry_price,
                    "stop_price":   r.stop_price,
                    "target_price": r.target_price,
                    "score":        r.score,
                    "confidence":   r.confidence,
                    "conditions":   r.conditions,
                    "warnings":     r.warnings,
                })
                r.extra["signal_id"] = sig_id
            except Exception as e:
                log.warning(f"DB insert signal failed: {e}")

        self._scan_count += 1
        return deduped

    # ─── internal: gather candles + trades for one pair ────────
    async def _gather_pair_data(self, pair) -> tuple[list[dict], list[dict]]:
        # Prefer WS bus if available and warm
        if self.bus and self.bus.is_connected:
            candles = self.bus.candles(pair.binance)
            trades  = self.bus.tape(pair.binance)
            if len(candles) >= 15:
                return candles, trades
        # Fallback: REST
        candles = await market_feed.get_candles(pair, "5m", 200)
        trades  = await market_feed.get_agg_trades_window(pair, 15 * 60_000)
        return candles, trades

    async def _btc_trend(self) -> dict:
        """15-minute BTC trend for counter-trend warnings."""
        btc = next((p for p in PAIRS if p.symbol == "BTC-USDT"), None)
        if not btc:
            return {"trend": "NEUTRAL", "change_pct": 0}
        try:
            candles = await market_feed.get_candles(btc, "15m", 4)
            if len(candles) < 2:
                return {"trend": "NEUTRAL", "change_pct": 0}
            change = ((candles[-1]["close"] - candles[0]["open"]) / candles[0]["open"]) * 100
            if change > 0.5:
                trend = "BULLISH"
            elif change < -0.5:
                trend = "BEARISH"
            else:
                trend = "NEUTRAL"
            return {"trend": trend, "change_pct": change}
        except Exception:
            return {"trend": "NEUTRAL", "change_pct": 0}

    # ─── scan loop ──────────────────────────────────────────────
    async def _scan_loop(self) -> None:
        log.info(f"Signal scan loop started — interval={SCAN_INTERVAL_SECONDS}s")
        while True:
            try:
                if not self._paused:
                    signals = await self.scan_once()
                    if signals:
                        log.info(f"Scan #{self._scan_count}: {len(signals)} new signal(s)")
                else:
                    log.debug("Scan paused — skipping")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error(f"Scan loop error: {e}", exc_info=True)
            await asyncio.sleep(SCAN_INTERVAL_SECONDS)

    # ─── public read API ───────────────────────────────────────
    def latest_signals(self) -> list[StrategyResult]:
        return list(self._latest.values())

    def latest_for(self, pair: str) -> StrategyResult | None:
        return self._latest.get(pair)

    def should_alert(self, pair: str, direction: str) -> bool:
        """Cooldown check — don't spam same pair/direction."""
        key = (pair, direction)
        last = self._last_alert_ts.get(key, 0)
        now = time.time()
        if now - last < ALERT_COOLDOWN_MINUTES * 60:
            return False
        self._last_alert_ts[key] = now
        return True
