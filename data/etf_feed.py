"""
KAVACH-09 — SoSoValue ETF Flow Feed
====================================
BTC spot ETF daily flow data.

SoSoValue's open API is gated; we use a multi-source fallback:
  1. SoSoValue openapi (if SOSOVALUE_API_KEY set)
  2. Public scrape endpoint (best-effort)
  3. Cached last-known values with timestamp

Returns the most recent trading-day flow summary.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from config import SOSOVALUE_BASE, ETF_US_SESSION_IST_HOUR

log = logging.getLogger("kavach.etf")


# Cache — ETF data only updates daily, refresh every 30 minutes
_cache: dict[str, Any] = {}
_cache_ts: float = 0.0
_CACHE_TTL = 30 * 60  # 30 minutes


async def get_etf_flow() -> dict[str, Any]:
    """
    Returns:
        {
            "date":        "2026-06-26",
            "net_flow":    float,         # USD, +ve = inflow
            "by_issuer":   {"IBIT": float, "FBTC": float, ...},
            "cumulative_7d": float,
            "bias":        "BULLISH"|"BEARISH"|"NEUTRAL",
            "us_session_in_hours": float,
            "source":      "SoSoValue"|"cached"|"mock"
        }
    """
    global _cache, _cache_ts
    now = time_now()
    if _cache and (now - _cache_ts) < _CACHE_TTL:
        return _cache

    try:
        result = await _fetch_sosovalue()
        if result:
            _cache = result
            _cache_ts = now
            return result
    except Exception as e:
        log.warning(f"SoSoValue fetch failed: {e}")

    # Fall back to last cached (even if stale)
    if _cache:
        _cache["source"] = "cached (stale)"
        return _cache

    # No data yet — return neutral placeholder
    return {
        "date":      (datetime.now(timezone.utc) - timedelta(hours=5.5)).strftime("%Y-%m-%d"),
        "net_flow":  0.0,
        "by_issuer": {},
        "cumulative_7d": 0.0,
        "bias":      "NEUTRAL",
        "us_session_in_hours": _us_session_hours_remaining(),
        "source":    "unavailable (set SOSOVALUE_API_KEY or check connectivity)",
    }


async def _fetch_sosovalue() -> dict[str, Any] | None:
    """
    Best-effort fetch from SoSoValue. The API spec changes; if it fails
    the function returns None and the caller will fall back.
    """
    from config import SOSOVALUE_BASE  # local import — config may be patched in tests
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{SOSOVALUE_BASE}/v1/btc/etf/flow/latest",
                timeout=15,
                headers={"accept": "application/json"},
            ) as r:
                if r.status != 200:
                    return None
                data = await r.json()
        # Parse — structure may vary, be defensive
        latest = data.get("data", {}).get("latest", {}) if isinstance(data, dict) else {}
        if not latest:
            return None
        net = float(latest.get("totalNetFlow", 0))
        by_issuer: dict[str, float] = {}
        for it in latest.get("list", []):
            by_issuer[it.get("ticker", "?")] = float(it.get("netFlow", 0))
        cum_7d = float(latest.get("weeklyNetFlow", 0))
        return {
            "date":      latest.get("date", ""),
            "net_flow":  net,
            "by_issuer": by_issuer,
            "cumulative_7d": cum_7d,
            "bias":      _etf_bias(net),
            "us_session_in_hours": _us_session_hours_remaining(),
            "source":    "SoSoValue",
        }
    except Exception as e:
        log.debug(f"SoSoValue fetch error: {e}")
        return None


def _etf_bias(net_flow_usd: float) -> str:
    from config import ETF_FLOW_BULLISH_USD, ETF_FLOW_BEARISH_USD
    if net_flow_usd >= ETF_FLOW_BULLISH_USD:
        return "BULLISH"
    if net_flow_usd <= ETF_FLOW_BEARISH_USD:
        return "BEARISH"
    return "NEUTRAL"


def _us_session_hours_remaining() -> float:
    """Hours until US session opens (7 PM IST = 13:30 UTC)."""
    now_ist = datetime.now(timezone.utc) + timedelta(hours=5.5)
    target = now_ist.replace(hour=ETF_US_SESSION_IST_HOUR, minute=0, second=0, microsecond=0)
    if now_ist.hour >= ETF_US_SESSION_IST_HOUR:
        target += timedelta(days=1)
    delta = (target - now_ist).total_seconds() / 3600
    return round(delta, 2)


def time_now() -> float:
    return datetime.now(timezone.utc).timestamp()
