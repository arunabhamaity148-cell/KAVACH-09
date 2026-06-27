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

import asyncio
import logging
import time as _time
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


# ════════════════════════════════════════════════════════════════════
# LIQUIDATION DATA  — Binance free public endpoint
# ════════════════════════════════════════════════════════════════════

async def get_liquidations_last_hour() -> dict[str, Any]:
    """
    Returns last-1-hour liquidation summary across all tracked pairs.
    Uses in-memory cache (5 min TTL) to avoid hammering Binance.

    Return schema:
        {
            "total_usd":   float,
            "long_total":  float,   # long positions liquidated
            "short_total": float,   # short positions liquidated
            "bias_ratio":  float,   # long_liq / total  (>0.5 = longs getting wiped)
            "by_pair":     {"BTC-USDT": {"long_usd": .., "short_usd": .., "total_usd": ..}},
            "source":      str,
            "window_min":  int,     # actual window used (minutes)
        }
    """
    global _liq_cache, _liq_cache_ts

    # Serve from cache if fresh
    now = _time.time()
    if _liq_cache and (now - _liq_cache_ts) < _LIQ_CACHE_TTL:
        return _liq_cache

    # CoinGlass first (if key present)
    if COINGLASS_API_KEY:
        try:
            result = await _coinglass_liquidations()
            _liq_cache    = result
            _liq_cache_ts = now
            return result
        except Exception as e:
            log.warning(f"CoinGlass liq failed: {e} — using Binance")

    # ── Binance free endpoints ───────────────────────────────────
    # Strategy:
    #   1. allForceOrders (all symbols, limit 200) — broadest picture
    #   2. per-symbol forceOrders for each tracked pair — more reliable
    # Merge both, deduplicate by (symbol, time).

    try:
        result = await _binance_liquidations_merged()
        _liq_cache    = result
        _liq_cache_ts = now
        return result
    except Exception as e:
        log.error(f"Binance liquidation fetch failed: {e}")
        # Return stale cache if available, else empty
        if _liq_cache:
            stale = dict(_liq_cache)
            stale["source"] = stale.get("source", "") + " (cached — fetch failed)"
            return stale
        return _empty_liq("Binance fetch failed")


async def _binance_liquidations_merged() -> dict[str, Any]:
    """
    Fetches from both allForceOrders and per-symbol forceOrders,
    deduplicates, filters to last 1 hour, aggregates USD value.
    """
    one_hour_ago_ms = int((_time.time() - 3600) * 1000)

    # ── Fetch 1: allForceOrders (no symbol filter, no limit param) ──
    # Binance docs: allForceOrders does NOT support `limit` param.
    # It returns last ~20 min of liquidations across all symbols.
    all_orders: list[dict] = []
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{BINANCE_FAPI}/fapi/v1/allForceOrders",
                timeout=12,
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    all_orders.extend(data)
                    log.debug(f"allForceOrders: {len(data)} records")
                else:
                    body = await r.text()
                    log.warning(f"allForceOrders HTTP {r.status}: {body[:100]}")
    except Exception as e:
        log.warning(f"allForceOrders failed: {e}")

    # ── Fetch 2: per-symbol forceOrders for each tracked pair ────
    pair_lookup = {p.binance: p.symbol for p in PAIRS}

    async def _fetch_symbol(sym: str) -> list[dict]:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"{BINANCE_FAPI}/fapi/v1/forceOrders",
                    params={"symbol": sym, "limit": 50, "startTime": one_hour_ago_ms},
                    timeout=10,
                ) as r:
                    if r.status == 200:
                        return await r.json()
                    return []
        except Exception as e:
            log.debug(f"forceOrders {sym}: {e}")
            return []

    per_sym_results = await asyncio.gather(
        *[_fetch_symbol(p.binance) for p in PAIRS],
        return_exceptions=True,
    )
    for res in per_sym_results:
        if isinstance(res, list):
            all_orders.extend(res)

    # ── Deduplicate by (symbol, time) ───────────────────────────
    seen: set[tuple[str, int]] = set()
    unique: list[dict] = []
    for o in all_orders:
        key = (o.get("symbol", ""), int(o.get("time", 0)))
        if key not in seen:
            seen.add(key)
            unique.append(o)

    # ── Filter to last 1 hour ───────────────────────────────────
    recent = [o for o in unique if int(o.get("time", 0)) >= one_hour_ago_ms]
    log.debug(f"Liquidations: {len(unique)} total, {len(recent)} in last 1h")

    # ── Aggregate ───────────────────────────────────────────────
    by_pair: dict[str, dict[str, float]] = defaultdict(
        lambda: {"long_usd": 0.0, "short_usd": 0.0, "total_usd": 0.0}
    )
    long_total  = 0.0
    short_total = 0.0

    for o in recent:
        sym = o.get("symbol", "")
        if sym not in pair_lookup:
            continue   # only count tracked pairs

        # Binance convention:
        #   side=SELL → forced SELL (long position liquidated) → long_liq
        #   side=BUY  → forced BUY  (short position liquidated) → short_liq
        side  = o.get("side", "").upper()
        price = float(o.get("averagePrice") or o.get("price", 0))
        qty   = float(o.get("executedQty") or o.get("origQty", 0))
        usd   = price * qty
        pname = pair_lookup[sym]

        if side == "SELL":          # long liquidated
            by_pair[pname]["long_usd"]  += usd
            long_total  += usd
        elif side == "BUY":         # short liquidated
            by_pair[pname]["short_usd"] += usd
            short_total += usd
        by_pair[pname]["total_usd"] += usd

    total = long_total + short_total

    # Determine source label
    if all_orders:
        source = "Binance public (allForceOrders + forceOrders)"
    else:
        source = "Binance public (forceOrders per-symbol)"

    return {
        "total_usd":   total,
        "long_total":  long_total,
        "short_total": short_total,
        "bias_ratio":  long_total / total if total > 0 else 0.5,
        "by_pair":     dict(by_pair),
        "source":      source,
        "window_min":  60,
        "count":       len(recent),
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
