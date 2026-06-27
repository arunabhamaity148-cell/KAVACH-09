"""
KAVACH-09 — Liquidation & Funding Feed
========================================
NO PAID API KEY REQUIRED.

Funding rates  → Binance FAPI /fapi/v1/premiumIndex  (free, public)
Liquidations   → Binance FAPI /fapi/v1/allForceOrders (free, public)
               → per-symbol  /fapi/v1/forceOrders     (fallback)

If COINGLASS_API_KEY is set in .env, CoinGlass is tried first.
Otherwise falls back to Binance public endpoints — no key needed.

Liquidation logic:
  Binance "forceOrders" side field:
    side=SELL  → buyer was liquidated  → LONG position wiped  → long_liq
    side=BUY   → seller was liquidated → SHORT position wiped → short_liq
  We filter to last 1 hour using the "time" field (Unix ms).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import aiohttp

from config import (
    BINANCE_FAPI,
    COINGLASS_API_KEY,
    COINGLASS_BASE,
    FUNDING_HIGH_THRESHOLD,
    FUNDING_LOW_THRESHOLD,
    PAIRS,
)

log = logging.getLogger("kavach.liq")

# ─── simple in-memory cache for liquidations (refresh every 5 min) ──
_liq_cache: dict[str, Any] = {}
_liq_cache_ts: float = 0.0
_LIQ_CACHE_TTL = 5 * 60   # 5 minutes


# ════════════════════════════════════════════════════════════════════
# FUNDING RATES  (unchanged — Binance free endpoint works fine)
# ════════════════════════════════════════════════════════════════════

async def get_funding_rates() -> dict[str, dict[str, Any]]:
    """
    Returns:
        { "BTC-USDT": {"rate": 0.00018, "rate_pct": 0.018,
                       "next_funding_ms": int, "bias": "neutral"} }
    """
    if COINGLASS_API_KEY:
        try:
            return await _coinglass_funding()
        except Exception as e:
            log.warning(f"CoinGlass funding failed: {e} — using Binance")
    return await _binance_funding()


async def _binance_funding() -> dict[str, dict[str, Any]]:
    url = f"{BINANCE_FAPI}/fapi/v1/premiumIndex"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=10) as r:
                r.raise_for_status()
                data = await r.json()
        rate_map = {d["symbol"]: d for d in data}
    except Exception as e:
        log.error(f"Binance funding fetch failed: {e}")
        return {}

    out: dict[str, dict[str, Any]] = {}
    for p in PAIRS:
        d = rate_map.get(p.binance)
        if not d:
            continue
        rate     = float(d.get("lastFundingRate", 0))
        rate_pct = rate * 100
        out[p.symbol] = {
            "pair":            p.symbol,
            "rate":            rate,
            "rate_pct":        rate_pct,
            "next_funding_ms": d.get("nextFundingTime"),
            "mark_price":      float(d.get("markPrice", 0)),
            "index_price":     float(d.get("indexPrice", 0)),
            "bias":            _funding_bias(rate_pct),
        }
    return out


async def _coinglass_funding() -> dict[str, dict[str, Any]]:
    headers = {"accept": "application/json", "CGAPI-KEY": COINGLASS_API_KEY}
    url     = f"{COINGLASS_BASE}/funding/current"
    out: dict[str, dict[str, Any]] = {}
    async with aiohttp.ClientSession() as s:
        for p in PAIRS:
            try:
                async with s.get(
                    url,
                    headers=headers,
                    params={"symbol": p.symbol.split("-")[0]},
                    timeout=10,
                ) as r:
                    data = await r.json()
                items = data.get("data", [])
                if not items:
                    continue
                entry    = next((x for x in items if x.get("exchangeName") == "Binance"), items[0])
                rate     = float(entry.get("rate", 0))
                rate_pct = rate * 100
                out[p.symbol] = {
                    "pair":            p.symbol,
                    "rate":            rate,
                    "rate_pct":        rate_pct,
                    "next_funding_ms": entry.get("nextFundingTime"),
                    "bias":            _funding_bias(rate_pct),
                }
            except Exception as e:
                log.debug(f"CoinGlass funding {p.symbol}: {e}")
    return out


def _funding_bias(rate_pct: float) -> str:
    if rate_pct >= FUNDING_HIGH_THRESHOLD:
        return "short_bias"
    if rate_pct <= FUNDING_LOW_THRESHOLD:
        return "long_bias"
    return "neutral"


def time_to_next_funding_ms(next_ms: int | None) -> str:
    if not next_ms:
        return "unknown"
    delta = (next_ms / 1000) - datetime.now(timezone.utc).timestamp()
    if delta < 0:
        return "settling…"
    h = int(delta // 3600)
    m = int((delta % 3600) // 60)
    return f"{h}h {m}m"


# LIQUIDATION DATA
# ════════════════════════════════════════════════════════════════════
# Source: Binance !forceOrder@arr WebSocket stream (public, no API key)
# This stream is subscribed in MarketFeedBus (market_feed.py).
# get_liquidations_last_hour() reads from the bus's _liq_events buffer.
# No REST call needed — data accumulates in memory as events arrive.
# ════════════════════════════════════════════════════════════════════

# Module-level bus reference — set by KavachBot.start() in main.py
_bus = None

def set_bus(bus) -> None:
    """Called from main.py after bus is created so liquidation feed can read it."""
    global _bus
    _bus = bus


async def get_liquidations_last_hour() -> dict[str, Any]:
    """
    Returns last-1-hour liquidation summary from the WS bus buffer.
    No API key required — data comes from !forceOrder@arr WS stream.

    Return schema:
        {
            "total_usd":   float,
            "long_total":  float,   # long positions liquidated (side=SELL)
            "short_total": float,   # short positions liquidated (side=BUY)
            "bias_ratio":  float,   # long_liq / total (>0.5 = longs wiped)
            "by_pair":     {"BTC-USDT": {"long_usd":.., "short_usd":.., "total_usd":..}},
            "source":      str,
            "window_min":  int,
            "count":       int,
        }
    """
    # CoinGlass if key present
    if COINGLASS_API_KEY:
        try:
            return await _coinglass_liquidations()
        except Exception as e:
            log.warning(f"CoinGlass liq failed: {e} — using WS bus")

    # Read from WebSocket bus buffer
    if _bus is None:
        return _empty_liq("bus not initialised yet")

    events = _bus.liquidations(window_seconds=3600)

    if not events:
        return _empty_liq(
            "WS buffer empty — bot recently started. "
            "Liquidation events accumulate over time. Retry in a few minutes."
        )

    pair_lookup = {p.binance: p.symbol for p in PAIRS}
    by_pair: dict[str, dict[str, float]] = defaultdict(
        lambda: {"long_usd": 0.0, "short_usd": 0.0, "total_usd": 0.0}
    )
    long_total  = 0.0
    short_total = 0.0

    for e in events:
        sym   = e.get("symbol", "")
        pname = pair_lookup.get(sym)
        if not pname:
            continue
        side = e.get("side", "").upper()
        usd  = float(e.get("usd", 0))

        if side == "SELL":          # long position liquidated
            by_pair[pname]["long_usd"]  += usd
            long_total  += usd
        elif side == "BUY":         # short position liquidated
            by_pair[pname]["short_usd"] += usd
            short_total += usd
        by_pair[pname]["total_usd"] += usd

    total = long_total + short_total
    return {
        "total_usd":   total,
        "long_total":  long_total,
        "short_total": short_total,
        "bias_ratio":  long_total / total if total > 0 else 0.5,
        "by_pair":     dict(by_pair),
        "source":      "Binance !forceOrder@arr WebSocket (real-time, no API key)",
        "window_min":  60,
        "count":       len(events),
    }


async def _coinglass_liquidations() -> dict[str, Any]:
    headers = {"accept": "application/json", "CGAPI-KEY": COINGLASS_API_KEY}
    url     = f"{COINGLASS_BASE}/liquidation/agg-chart"
    async with aiohttp.ClientSession() as s:
        async with s.get(url, headers=headers, params={"interval": "1h"}, timeout=10) as r:
            data = await r.json()
    items       = data.get("data", {}).get("dataList", [])
    long_total  = sum(float(it.get("longVolUsd", 0))  for it in items)
    short_total = sum(float(it.get("shortVolUsd", 0)) for it in items)
    total       = long_total + short_total
    return {
        "total_usd":   total,
        "long_total":  long_total,
        "short_total": short_total,
        "bias_ratio":  long_total / total if total > 0 else 0.5,
        "by_pair":     {},
        "source":      "CoinGlass API",
        "window_min":  60,
        "count":       len(items),
    }


def _empty_liq(reason: str = "no data") -> dict[str, Any]:
    return {
        "total_usd": 0.0, "long_total": 0.0, "short_total": 0.0,
        "bias_ratio": 0.5, "by_pair": {}, "window_min": 60, "count": 0,
        "source": f"unavailable ({reason})",
    }

