"""
KAVACH-09 — Market Feed
========================
Binance USDT-M futures REST (primary) + CoinDCX fallback.

Provides:
  - get_candles(pair, interval, limit)        → list of candle dicts
  - get_ticker(pair)                          → 24h ticker dict
  - get_recent_trades(pair, limit)            → trade tape for CVD
  - get_agg_trades_window(pair, lookback_ms)  → windowed aggTrades
  - MarketFeedBus                             → async live WS subscription

No API key required — all public endpoints.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

import aiohttp
import websockets

from config import (
    BINANCE_FAPI, BINANCE_KLINES, BINANCE_TICKER, BINANCE_WS,
    COINDCX_CANDLES, COINDCX_TICKER, COINDCX_WS, CANDLE_HISTORY,
    PAIRS, Pair, TRADE_TAPE_WINDOW,
)
from indicators.atr import calculate_atr
from indicators.vwap import session_vwap

log = logging.getLogger("kavach.market")


# ────────────────────────────────────────────────────────────────────
# CANDLE FETCH  — Binance USDT-M futures (reliable, no key)
# ────────────────────────────────────────────────────────────────────

async def get_candles(pair: Pair, interval: str = "5m", limit: int = CANDLE_HISTORY) -> list[dict]:
    """Fetch historical candles from Binance USDT-M futures."""
    params = {"symbol": pair.binance, "interval": interval, "limit": limit}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(BINANCE_KLINES, params=params, timeout=10) as r:
                r.raise_for_status()
                data = await r.json()
        return [
            {
                "timestamp": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).isoformat(),
                "open":   float(k[1]),
                "high":   float(k[2]),
                "low":    float(k[3]),
                "close":  float(k[4]),
                "volume": float(k[5]),
            }
            for k in data
        ]
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
        rows = data.get("data", data) if isinstance(data, dict) else data
        return [
            {
                "timestamp": c.get("time") or c.get("timestamp"),
                "open":   float(c["open"]),
                "high":   float(c["high"]),
                "low":    float(c["low"]),
                "close":  float(c["close"]),
                "volume": float(c.get("volume", 0)),
            }
            for c in rows
        ]
    except Exception as e:
        log.error(f"CoinDCX candle fetch failed: {e}")
        return []


# ────────────────────────────────────────────────────────────────────
# TICKER (24h stats) — ISSUE 3 FIX: correct ?symbol= query param
# ────────────────────────────────────────────────────────────────────

async def get_ticker(pair: Pair) -> dict[str, Any]:
    """24h ticker — price, change, volume.
    FIXED: Binance FAPI requires ?symbol=BTCUSDT (not /BTCUSDT path).
    Falls back to CoinDCX if Binance fails.
    """
    # ── Primary: Binance FAPI ──────────────────────────────────
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                BINANCE_TICKER,
                params={"symbol": pair.binance},
                timeout=10,
            ) as r:
                r.raise_for_status()
                d = await r.json()
        return {
            "pair":       pair.symbol,
            "price":      float(d["lastPrice"]),
            "change_pct": float(d["priceChangePercent"]),
            "change_abs": float(d["priceChange"]),
            "volume":     float(d["quoteVolume"]),
            "high":       float(d["highPrice"]),
            "low":        float(d["lowPrice"]),
        }
    except Exception as e:
        log.warning(f"Binance ticker failed for {pair.binance}: {e} — trying CoinDCX")

    # ── Fallback: CoinDCX REST ticker ──────────────────────────
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(COINDCX_TICKER, timeout=10) as r:
                data = await r.json()
        for t in data:
            if t.get("market") == pair.coindcx:
                last  = float(t.get("last_price", 0) or 0)
                prev  = float(t.get("open", last) or last)
                chg   = last - prev
                chg_p = (chg / prev * 100) if prev else 0
                vol   = float(t.get("base_volume", 0) or 0) * last
                return {
                    "pair":       pair.symbol,
                    "price":      last,
                    "change_pct": chg_p,
                    "change_abs": chg,
                    "volume":     vol,
                    "high":       float(t.get("high", last) or last),
                    "low":        float(t.get("low", last) or last),
                }
    except Exception as e:
        log.error(f"CoinDCX ticker also failed for {pair.symbol}: {e}")

    return {
        "pair": pair.symbol, "price": 0, "change_pct": 0,
        "change_abs": 0, "volume": 0, "high": 0, "low": 0,
    }


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
    url = f"{BINANCE_FAPI}/fapi/v1/trades"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, params={"symbol": pair.binance, "limit": limit}, timeout=10) as r:
                r.raise_for_status()
                data = await r.json()
        return [
            {
                "price":  float(t["price"]),
                "volume": float(t["qty"]),
                "side":   "sell" if t.get("isBuyerMaker") else "buy",
                "time":   t.get("time"),
            }
            for t in data
        ]
    except Exception as e:
        log.warning(f"Binance recent trades failed: {e}")
        return []


async def get_agg_trades_window(pair: Pair, lookback_ms: int = 15 * 60_000) -> list[dict]:
    """Aggressor trades from the last N ms — used for CVD scan."""
    url = f"{BINANCE_FAPI}/fapi/v1/aggTrades"
    start = int(time.time() * 1000) - lookback_ms
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                url,
                params={"symbol": pair.binance, "startTime": start, "limit": 1000},
                timeout=10,
            ) as r:
                r.raise_for_status()
                data = await r.json()
        return [
            {
                "price":  float(t["p"]),
                "volume": float(t["q"]),
                "side":   "sell" if t.get("m") else "buy",
                "time":   t.get("T"),
            }
            for t in data
        ]
    except Exception as e:
        log.warning(f"Binance aggTrades failed: {e}")
        return []


# ────────────────────────────────────────────────────────────────────
# LIVE WEBSOCKET BUS
# ISSUE 1 FIX: populate _funding_rates from markPrice stream
# ISSUE 2 FIX: populate _atrs after each closed kline
# ISSUE 4 FIX: populate _vwaps properly from session_vwap()
# ────────────────────────────────────────────────────────────────────

class MarketFeedBus:
    """
    Async WebSocket subscriber for Binance USDT-M futures combined stream.
    Streams: aggTrade + kline_5m + markPrice (for funding rate).

    Public read API:
        bus.tape(binance_sym)           → list[dict]  (recent aggTrades)
        bus.candles(binance_sym)        → list[dict]  (kline history)
        bus.last_price(binance_sym)     → float
        bus.funding_rate(binance_sym)   → float  (ISSUE 1 FIX)
        bus.atr(binance_sym)            → float  (ISSUE 2 FIX)
        bus.vwap(binance_sym)           → float  (ISSUE 4 FIX)
        bus.is_connected                → bool
        bus.latency_ms                  → float
    """

    def __init__(self, pairs: list[Pair] | None = None, interval: str = "5m"):
        self.pairs    = pairs or list(PAIRS)
        self.interval = interval
        self._task: asyncio.Task | None = None
        self._ws   = None
        self._running = False
        self._ready   = asyncio.Event()

        self._tapes: dict[str, deque] = {
            p.binance: deque(maxlen=TRADE_TAPE_WINDOW) for p in self.pairs
        }
        self._candles: dict[str, list[dict]] = {p.binance: [] for p in self.pairs}
        self._last_prices: dict[str, float]  = {p.binance: 0.0 for p in self.pairs}

        # ISSUE 1 FIX — funding rate per symbol (from markPrice stream)
        self._funding_rates: dict[str, float] = {p.binance: 0.0 for p in self.pairs}

        # ISSUE 2 FIX — ATR(14) per symbol, recomputed on each closed kline
        self._atrs: dict[str, float] = {p.binance: 0.0 for p in self.pairs}

        # ISSUE 4 FIX — session VWAP per symbol, recomputed on each closed kline
        self._vwaps: dict[str, float] = {p.binance: 0.0 for p in self.pairs}

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
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def wait_ready(self, timeout: float = 15.0) -> bool:
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    # ─── reconnect loop ─────────────────────────────────────────
    async def _run_forever(self) -> None:
        backoff = 3
        while self._running:
            try:
                await self._connect_and_listen()
                backoff = 3   # reset on clean exit
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning(f"WS disconnected: {e}; reconnecting in {backoff}s")
                self._ready.clear()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)   # exponential backoff, max 60s

    async def _connect_and_listen(self) -> None:
        streams: list[str] = []
        for p in self.pairs:
            sym = p.binance.lower()
            streams.append(f"{sym}@aggTrade")
            streams.append(f"{sym}@kline_{self.interval}")
            # ISSUE 1 FIX: markPrice stream carries funding rate
            streams.append(f"{sym}@markPrice")

        url = f"{BINANCE_WS}?streams=" + "/".join(streams)
        async with websockets.connect(
            url, ping_interval=15, ping_timeout=10, close_timeout=5, max_size=2**22
        ) as ws:
            self._ws = ws
            log.info(f"WS connected — {len(self.pairs)} pairs, {len(streams)} streams")
            self._ready.set()
            async for raw in ws:
                if not self._running:
                    break
                try:
                    self._handle(json.loads(raw))
                except Exception as e:
                    log.debug(f"WS msg parse err: {e}")

    # ─── message dispatcher ─────────────────────────────────────
    def _handle(self, msg: dict) -> None:
        stream = msg.get("stream", "")
        data   = msg.get("data", {})
        if not stream or not data:
            return

        if "@aggTrade" in stream:
            self._handle_trade(stream, data)
        elif "@kline_" in stream:
            self._handle_kline(stream, data)
        elif "@markPrice" in stream:
            self._handle_mark_price(stream, data)    # ISSUE 1 FIX

    def _handle_trade(self, stream: str, data: dict) -> None:
        sym = stream.split("@")[0].upper()
        if sym not in self._tapes:
            return
        trade = {
            "price":  float(data["p"]),
            "volume": float(data["q"]),
            "side":   "sell" if data.get("m") else "buy",
            "time":   data.get("T"),
        }
        self._tapes[sym].append(trade)
        self._last_prices[sym] = trade["price"]
        if data.get("T"):
            self._latency_ms = max(0, time.time() * 1000 - data["T"])

    def _handle_kline(self, stream: str, data: dict) -> None:
        sym = stream.split("@")[0].upper()
        if sym not in self._candles:
            return
        k = data.get("k", {})
        candle = {
            "timestamp": datetime.fromtimestamp(k.get("t", 0) / 1000, tz=timezone.utc).isoformat(),
            "open":   float(k.get("o", 0)),
            "high":   float(k.get("h", 0)),
            "low":    float(k.get("l", 0)),
            "close":  float(k.get("c", 0)),
            "volume": float(k.get("v", 0)),
            "closed": bool(k.get("x", False)),
        }

        # Upsert: replace last candle if same timestamp, else append
        arr = self._candles[sym]
        if arr and arr[-1]["timestamp"] == candle["timestamp"]:
            arr[-1] = candle
        else:
            arr.append(candle)
            if len(arr) > CANDLE_HISTORY:
                arr.pop(0)

        # ISSUE 2 & 4 FIX: recompute ATR + VWAP on every closed kline
        if candle["closed"] and len(arr) >= 14:
            try:
                self._atrs[sym]  = calculate_atr(arr, 14)       # ISSUE 2
                self._vwaps[sym] = session_vwap(arr)             # ISSUE 4
            except Exception as e:
                log.debug(f"ATR/VWAP compute error for {sym}: {e}")

    def _handle_mark_price(self, stream: str, data: dict) -> None:
        """ISSUE 1 FIX: extract funding rate from markPrice stream."""
        sym = stream.split("@")[0].upper()
        if sym not in self._funding_rates:
            return
        raw_rate = data.get("r")   # "r" = lastFundingRate in markPrice stream
        if raw_rate is not None:
            try:
                self._funding_rates[sym] = float(raw_rate)
            except (ValueError, TypeError):
                pass

    # ─── public read API ────────────────────────────────────────
    def tape(self, binance_sym: str) -> list[dict]:
        return list(self._tapes.get(binance_sym, []))

    def candles(self, binance_sym: str) -> list[dict]:
        return list(self._candles.get(binance_sym, []))

    def last_price(self, binance_sym: str) -> float:
        return self._last_prices.get(binance_sym, 0.0)

    def funding_rate(self, binance_sym: str) -> float:
        """ISSUE 1 FIX: live funding rate from markPrice stream."""
        return self._funding_rates.get(binance_sym, 0.0)

    def atr(self, binance_sym: str) -> float:
        """ISSUE 2 FIX: latest ATR(14) computed from closed klines."""
        return self._atrs.get(binance_sym, 0.0)

    def vwap(self, binance_sym: str) -> float:
        """ISSUE 4 FIX: session VWAP computed from closed klines."""
        return self._vwaps.get(binance_sym, 0.0)

    @property
    def latency_ms(self) -> float:
        return self._latency_ms

    @property
    def is_connected(self) -> bool:
        return self._ready.is_set()


# ────────────────────────────────────────────────────────────────────
# Warm-start helper — backfill candle buffers + compute initial ATR/VWAP
# ────────────────────────────────────────────────────────────────────

async def warm_start(bus: MarketFeedBus) -> None:
    """Pre-fill bus candle buffers with history, then compute ATR + VWAP."""
    async def _fill(p: Pair):
        c = await get_candles(p, bus.interval, CANDLE_HISTORY)
        if c:
            bus._candles[p.binance]     = c
            bus._last_prices[p.binance] = c[-1]["close"]
            if len(c) >= 14:
                bus._atrs[p.binance]  = calculate_atr(c, 14)   # ISSUE 2 FIX
                bus._vwaps[p.binance] = session_vwap(c)         # ISSUE 4 FIX
                log.debug(
                    f"{p.symbol} warm-start: ATR={bus._atrs[p.binance]:.2f}"
                    f" VWAP={bus._vwaps[p.binance]:.2f}"
                )

    await asyncio.gather(*[_fill(p) for p in PAIRS], return_exceptions=True)
    log.info(f"Warm-start complete — {len(PAIRS)} pairs (ATR+VWAP pre-computed)")
