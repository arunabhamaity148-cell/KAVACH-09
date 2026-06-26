"""
KAVACH-09 — CoinGlass Feed
==========================
Funding rates + Liquidation data.

If COINGLASS_API_KEY is empty, falls back to Binance public endpoints
(Binance funding rates + Binance force-order stream). No key needed.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import aiohttp

from config import (
    BINANCE_FAPI, COINGLASS_API_KEY, COINGLASS_BASE,
    FUNDING_HIGH_THRESHOLD, FUNDING_LOW_THRESHOLD, PAIRS, Pair,
)

log = logging.getLogger("kavach.coinglass")


# ────────────────────────────────────────────────────────────────────
# FUNDING RATES
# ────────────────────────────────────────────────────────────────────

async def get_funding_rates() -> dict[str, dict[str, Any]]:
    """
    Returns: { "BTC-USDT": {"rate": 0.00018, "rate_pct": 0.018, "next_funding_ms": ..., "bias": "neutral"} }
    """
    # CoinGlass first if API key set
    if COINGLASS_API_KEY:
        try:
            return await _coinglass_funding()
        except Exception as e:
            log.warning(f"CoinGlass funding failed: {e}; using Binance")

    # Binance fallback (no key)
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
        rate = float(d.get("lastFundingRate", 0))
        rate_pct = rate * 100
        bias = _funding_bias(rate_pct)
        out[p.symbol] = {
            "pair":             p.symbol,
            "rate":             rate,
            "rate_pct":         rate_pct,
            "next_funding_ms":  d.get("nextFundingTime"),
            "mark_price":       float(d.get("markPrice", 0)),
            "index_price":      float(d.get("indexPrice", 0)),
            "bias":             bias,
        }
    return out


async def _coinglass_funding() -> dict[str, dict[str, Any]]:
    headers = {"accept": "application/json", "CGAPI-KEY": COINGLASS_API_KEY}
    url = f"{COINGLASS_BASE}/funding/current"
    out: dict[str, dict[str, Any]] = {}
    async with aiohttp.ClientSession() as s:
        for p in PAIRS:
            params = {"symbol": p.symbol.split("-")[0]}
            try:
                async with s.get(url, headers=headers, params=params, timeout=10) as r:
                    data = await r.json()
                items = data.get("data", [])
                if not items:
                    continue
                # Find Binance entry (most liquid)
                binance = next((x for x in items if x.get("exchangeName") == "Binance"), items[0])
                rate = float(binance.get("rate", 0))
                rate_pct = rate * 100
                out[p.symbol] = {
                    "pair":            p.symbol,
                    "rate":            rate,
                    "rate_pct":        rate_pct,
                    "next_funding_ms": binance.get("nextFundingTime"),
                    "bias":            _funding_bias(rate_pct),
                }
            except Exception as e:
                log.debug(f"CoinGlass funding {p.symbol}: {e}")
    return out


def _funding_bias(rate_pct: float) -> str:
    """Returns 'long_bias', 'short_bias', or 'neutral'."""
    if rate_pct >= FUNDING_HIGH_THRESHOLD:
        return "short_bias"   # longs pay → too many longs → fade long
    if rate_pct <= FUNDING_LOW_THRESHOLD:
        return "long_bias"    # shorts pay → too many shorts → fade short
    return "neutral"


def time_to_next_funding_ms(next_ms: int | None) -> str:
    if not next_ms:
        return "unknown"
    delta = (next_ms / 1000) - datetime.now(timezone.utc).timestamp()
    if delta < 0:
        return "settling…"
    h = int(delta // 3600); m = int((delta % 3600) // 60)
    return f"{h}h {m}m"


# ────────────────────────────────────────────────────────────────────
# LIQUIDATION DATA
# ────────────────────────────────────────────────────────────────────

async def get_liquidations_last_hour() -> dict[str, Any]:
    """
    Returns: {
        "total_usd": float,
        "by_pair":   {"BTC-USDT": {"long_usd": float, "short_usd": float, "total_usd": float}},
        "long_total": float, "short_total": float,
        "bias_ratio": float,        # long / (long+short)
    }
    """
    if COINGLASS_API_KEY:
        try:
            return await _coinglass_liquidations()
        except Exception as e:
            log.warning(f"CoinGlass liq failed: {e}; using Binance")

    return await _binance_liquidations()


async def _binance_liquidations() -> dict[str, Any]:
    """
    Binance returns forceOrders for last hour (max 1000).
    Aggregate by pair + side.
    """
    url = f"{BINANCE_FAPI}/fapi/v1/allForceOrders"
    # Note: public endpoint requires no key, but rate-limited and may be empty
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=10) as r:
                if r.status != 200:
                    return _empty_liq()
                data = await r.json()
    except Exception as e:
        log.warning(f"Binance liquidations fetch failed: {e}")
        return _empty_liq()

    by_pair: dict[str, dict[str, float]] = defaultdict(
        lambda: {"long_usd": 0.0, "short_usd": 0.0, "total_usd": 0.0}
    )
    pair_lookup = {p.binance: p.symbol for p in PAIRS}
    long_total = 0.0
    short_total = 0.0

    for liq in data:
        sym = liq.get("symbol")
        if sym not in pair_lookup:
            continue
        side = liq.get("side", "").upper()   # BUY = short liquidation, SELL = long liquidation
        price = float(liq.get("price", 0))
        qty   = float(liq.get("origQty", 0))
        usd   = price * qty
        pair_name = pair_lookup[sym]
        if side == "SELL":
            by_pair[pair_name]["long_usd"]  += usd
            long_total  += usd
        else:
            by_pair[pair_name]["short_usd"] += usd
            short_total += usd
        by_pair[pair_name]["total_usd"] += usd

    total = long_total + short_total
    bias = long_total / total if total > 0 else 0.5

    return {
        "total_usd":   total,
        "by_pair":     dict(by_pair),
        "long_total":  long_total,
        "short_total": short_total,
        "bias_ratio":  bias,
        "source":      "Binance public",
    }


async def _coinglass_liquidations() -> dict[str, Any]:
    headers = {"accept": "application/json", "CGAPI-KEY": COINGLASS_API_KEY}
    url = f"{COINGLASS_BASE}/liquidation/agg-chart"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=headers, params={"interval": "1h"}, timeout=10) as r:
                data = await r.json()
        items = data.get("data", {}).get("dataList", [])
        long_total = 0.0
        short_total = 0.0
        for it in items:
            long_total  += float(it.get("longVolUsd", 0))
            short_total += float(it.get("shortVolUsd", 0))
        total = long_total + short_total
        return {
            "total_usd":   total,
            "by_pair":     {},
            "long_total":  long_total,
            "short_total": short_total,
            "bias_ratio":  long_total / total if total > 0 else 0.5,
            "source":      "CoinGlass",
        }
    except Exception as e:
        log.warning(f"CoinGlass liq fetch failed: {e}")
        return _empty_liq()


def _empty_liq() -> dict[str, Any]:
    return {
        "total_usd": 0, "by_pair": {},
        "long_total": 0, "short_total": 0, "bias_ratio": 0.5,
        "source": "Binance public (empty)",
    }
