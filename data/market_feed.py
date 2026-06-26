"""
KAVACH-09 — Market Feed
========================
CoinDCX public WebSocket (primary) + Binance USDT-M futures REST fallback.

Provides:
  - get_candles(pair, interval, limit)   → list of candle dicts
  - get_ticker(pair)                     → 24h ticker dict
  - get_recent_trades(pair, limit)       → trade tape for CVD
  - MarketFeedBus                        → async live WS subscription (optional)

No API key required for any of these — all public endpoints.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

import aiohttp
import websockets

from config import (
    BINANCE_FAPI, BINANCE_KLINES, BINANCE_TICKER, BINANCE_WS,
    COINDCX_CANDLES, COINDCX_TICKER, COINDCX_WS, CANDLE_HISTORY,
    PAIRS, Pair, TRADE_TAPE_WINDOW,
)

log = logging.getLogger("kavach.market")


# ────────────────────────────────────────────────────────────────────
# CANDLE FETCH  — Binance (reliable, no key) with CoinDCX as alt
# ────────────────────────────────────────────────────────────────────

async def get_candles(pair: Pair, interval: str = "5m", limit: int = CANDLE_HISTORY) -> list[dict]:
    """Fetch historical candles from Binance USDT-M futures."""
    url = BINANCE_KLINES
    params = {"symbol": pair.binance, "interval": interval, "limit": limit}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, params=params, timeout=10) as r:
                r.raise_for_status()
                data = await r.json()
        candles = []
        for k in data:
            candles.append({
                "timestamp": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).isoformat(),
                "open":  float(k[1]),
                "high":  float(k[2]),
                "low":   float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })
        return candles
    except Exception as e:
        log.warning(f"Binance klines failed for {pair.binance}: {e}")
        return await _coindcx_candles(pair, interval, limit)


async def _coindcx_candles(pair: Pair, interval: str, limit: int) -> list[dict]:
    """CoinDCX candlestick fallback."""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                COINDCX_CANDLES,
                params={"pair": pair.coindcx, "interval": interval, "limit": limit},
                timeout=10,
            ) as r:
                data = await r.json()
        out = []
        for c in data.get("data", data) if isinstance(data, dict) else data:
            out.append({
                "timestamp": c.get("time") or c.get("timestamp"),
                "open":  float(c.get("open")),
                "high":  float(c.get("high")),
                "low":   float(c["low"]),
                "close": float(c["close"]),
                "volume": float(c.get("volume", 0)),
            })
        return out
    except Exception as e:
        log.error(f"CoinDCX candle fetch failed: {e}")
        return []


# ────────────────────────────────────────────────────────────────────
# TICKER (24h stats)
# ────────────────────────────────────────────────────────────────────

async def get_ticker(pair: Pair) -> dict[str, Any]:
    """24h ticker — price, change, volume."""
    url = f"{BINANCE_TICKER}/{pair.binance}"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=10) as r:
                r.raise_for_status()
                d = await r.json()
        return {
            "pair":      pair.symbol,
            "price":     float(d["lastPrice"]),
            "change_pct": float(d["priceChangePercent"]),
            "change_abs": float(d["priceChange"]),
            "volume":    float(d["quoteVolume"]),     # in USDT
            "high":      float(d["highPrice"]),
            "low":       float(d["lowPrice"]),
        }
    except Exception as e:
        log.warning(f"Binance ticker failed: {e}")
        return {"pair": pair.symbol, "price": 0, "change_pct": 0, "change_abs": 0,
                "volume": 0, "high": 0, "low": 0}


async def get_all_tickers() -> dict[str, dict]:
    """Bulk fetch tickers for all configured pairs in one request."""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(BINANCE_TICKER, timeout=10) as r:
                data = await r.json()
        tickers = {d["symbol"]: d for d in data}
        out = {}
        for p in PAIRS:
            if p.binance in tickers:
                d = tickers[p.binance]
                out[p.symbol] = {
                    "pair":       p.symbol,
                    "price":      float(d["lastPrice"]),
                    "change_pct": float(d["priceChangePercent"]),
                    "change_abs": float(d["priceChange"]),
                    "volume":     float(d["quoteVolume"]),
                    "high":       float(d["highPrice"]),
                    "low":        float(d["lowPrice"]),
                }
        return out
    except Exception as e:
        log.error(f"Binance bulk ticker failed: {e}")
        return {}


# ────────────────────────────────────────────────────────────────────
# TRADE TAPE — recent aggressor trades (for CVD)
# ────────────────────────────────────────────────────────────────────

async def get_recent_trades(pair: Pair, limit: int = 1000) -> list[dict]:
    """
    Fetch recent aggressor trades from Binance futures.
    Each trade dict: {price, volume, side: 'buy'|'sell'}
    """
    url = f"{BINANCE_FAPI}/fapi/v1/trades"
    params = {"symbol": pair.binance, "limit": limit}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, params=params, timeout=10) as r:
                r.raise_for_status()
                data = await r.json()
        # Binance returns isBuyerMaker; if True → taker SELL (hit bid)
        return [{
            "price":  float(t["price"]),
            "volume": float(t["qty"]),
            "side":   "sell" if t.get("isBuyerMaker") else "buy",
            "time":   t.get("time"),
        } for t in data]
    except Exception as e:
        log.warning(f"Binance recent trades failed: {e}")
        return []


async def get_agg_trades_window(pair: Pair, lookback_ms: int = 15 * 60_000) -> list[dict]:
    """Aggressor trades from the last N milliseconds — used for CVD divergence scan."""
    url = f"{BINANCE_FAPI}/fapi/v1/aggTrades"
    start = int(time.time() * 1000) - lookback_ms
    params = {"symbol": pair.binance, "startTime": start, "limit": 1000}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, params=params, timeout=10) as r:
                r.raise_for_status()
                data = await r.json()
        return [{
            "price":  float(t["p"]),
            "volume": float(t["q"]),
            "side":   "sell" if t.get("m") else "buy",   # m = isBuyerMaker
            "time":   t.get("T"),
        } for t in data]
    except Exception as e:
        log.warning(f"Binance aggTrades failed: {e}")
        return []


# ────────────────────────────────────────────────────────────────────
# LIVE WEBSOCKET FEED (optional, for low-latency CVD)
# ────────────────────────────────────────────────────────────────────

class MarketFeedBus:
    """
    Async WebSocket subscriber for Binance USDT-M futures combined stream.
    Receives live trades + kline updates, updates in-memory buffers.

    Usage:
        bus = MarketFeedBus()
        await bus.start()       # non-blocking, spawns background task
        await bus.wait_ready()
        trades = bus.tape("BTCUSDT")
        candles = bus.candles("BTCUSDT")
        await bus.stop()
    """
    def __init__(self, pairs: list[Pair] | None = None, interval: str = "5m"):
        self.pairs = pairs or list(PAIRS)
        self.interval = interval
        self._task: asyncio.Task | None = None
        self._ws = None
        self._running = False
        self._ready = asyncio.Event()
        self._tapes: dict[str, deque] = {
            p.binance: deque(maxlen=TRADE_TAPE_WINDOW) for p in self.pairs
        }
        self._candles: dict[str, list[dict]] = {p.binance: [] for p in self.pairs}
        self._last_prices: dict[str, float] = {p.binance: 0.0 for p in self.pairs}
        self._latency_ms = 0.0

    # ─── lifecycle ──────────────────────────────────────────────
    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def wait_ready(self, timeout: float = 15.0) -> bool:
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    # ─── internal ───────────────────────────────────────────────
    async def _run_forever(self) -> None:
        while self._running:
            try:
                await self._connect_and_listen()
            except Exception as e:
                log.warning(f"WS disconnected: {e}; reconnecting in 3s")
                self._ready.clear()
                await asyncio.sleep(3)

    async def _connect_and_listen(self) -> None:
        streams = []
        for p in self.pairs:
            streams.append(f"{p.binance.lower()}@aggTrade")
            streams.append(f"{p.binance.lower()}@kline_{self.interval}")
        url = f"{BINANCE_WS}?streams=" + "/".join(streams)

        async with websockets.connect(url, ping_interval=15, ping_timeout=10,
                                       close_timeout=5, max_size=2**22) as ws:
            self._ws = ws
            log.info(f"WS connected — {len(self.pairs)} pairs, {len(streams)} streams")
            self._ready.set()

            async for raw in ws:
                if not self._running:
                    break
                try:
                    self._handle(json.loads(raw))
                except Exception as e:
                    log.debug(f"msg parse err: {e}")

    def _handle(self, msg: dict) -> None:
        stream = msg.get("stream", "")
        data = msg.get("data", {})
        if not stream or not data:
            return
        if "@aggTrade" in stream:
            sym = stream.split("@")[0].upper()
            if sym in self._tapes:
                trade = {
                    "price":  float(data["p"]),
                    "volume": float(data["q"]),
                    "side":   "sell" if data.get("m") else "buy",
                    "time":   data.get("T"),
                }
                self._tapes[sym].append(trade)
                self._last_prices[sym] = trade["price"]
                recv = data.get("T", 0)
                if recv:
                    self._latency_ms = max(0, time.time() * 1000 - recv)
        elif "@kline_" in stream:
            sym = stream.split("@")[0].upper()
            k = data.get("k", {})
            candle = {
                "timestamp": datetime.fromtimestamp(
                    k.get("t", 0) / 1000, tz=timezone.utc
                ).isoformat(),
                "open":   float(k.get("o", 0)),
                "high":   float(k.get("h", 0)),
                "low":    float(k.get("l", 0)),
                "close":  float(k.get("c", 0)),
                "volume": float(k.get("v", 0)),
                "closed": bool(k.get("x", False)),
            }
            # Replace last candle if same ts, else append
            arr = self._candles[sym]
            if arr and arr[-1]["timestamp"] == candle["timestamp"]:
                arr[-1] = candle
            else:
                arr.append(candle)
                if len(arr) > CANDLE_HISTORY:
                    arr.pop(0)

    # ─── public read API ────────────────────────────────────────
    def tape(self, binance_sym: str) -> list[dict]:
        return list(self._tapes.get(binance_sym, []))

    def candles(self, binance_sym: str) -> list[dict]:
        return list(self._candles.get(binance_sym, []))

    def last_price(self, binance_sym: str) -> float:
        return self._last_prices.get(binance_sym, 0.0)

    @property
    def latency_ms(self) -> float:
        return self._latency_ms

    @property
    def is_connected(self) -> bool:
        return self._ready.is_set()


# ────────────────────────────────────────────────────────────────────
# Bootstrap helper — backfill candles for warm-start
# ────────────────────────────────────────────────────────────────────

async def warm_start(bus: MarketFeedBus) -> None:
    """Pre-fill bus candle buffers with history."""
    async def _fill(p: Pair):
        c = await get_candles(p, bus.interval, CANDLE_HISTORY)
        if c:
            bus._candles[p.binance] = c
            bus._last_prices[p.binance] = c[-1]["close"]
    await asyncio.gather(*[_fill(p) for p in PAIRS], return_exceptions=True)
    log.info(f"Warm-start complete — {len(PAIRS)} pairs")
