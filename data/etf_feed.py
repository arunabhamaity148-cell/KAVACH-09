"""
KAVACH-09 — BTC ETF Flow Feed
==============================
Data source: github.com/arunabhamaity148-cell/btc-etf-data
Updated daily via GitHub Actions (Farside scraper).
Bot reads via raw.githubusercontent.com — works from Oracle Cloud.
"""
from __future__ import annotations

import logging
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from config import ETF_FLOW_BEARISH_USD, ETF_FLOW_BULLISH_USD, ETF_US_SESSION_IST_HOUR

log = logging.getLogger("kavach.etf")

_RAW_URL = (
    "https://raw.githubusercontent.com/"
    "arunabhamaity148-cell/btc-etf-data/main/data/latest.json"
)

_cache: dict[str, Any] = {}
_cache_ts: float = 0.0
_CACHE_TTL = 60 * 60   # 1 hour


async def get_etf_flow() -> dict[str, Any]:
    global _cache, _cache_ts

    now = _time.time()
    if _cache and (now - _cache_ts) < _CACHE_TTL:
        return _cache

    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(_RAW_URL, timeout=10) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    # Add computed fields
                    data["us_session_in_hours"] = _us_session_hours()
                    data["bias"] = _etf_bias(data.get("net_flow", 0))
                    _cache    = data
                    _cache_ts = now
                    log.info(
                        f"ETF data loaded: date={data.get('date')} "
                        f"net={data.get('net_flow', 0)/1e6:+.1f}M"
                    )
                    return data
                else:
                    log.warning(f"ETF GitHub raw: HTTP {r.status}")
    except Exception as e:
        log.warning(f"ETF fetch failed: {e}")

    # Stale cache
    if _cache:
        stale = dict(_cache)
        stale["source"] = stale.get("source", "") + " (cached)"
        stale["us_session_in_hours"] = _us_session_hours()
        return stale

    return _placeholder()


def _etf_bias(net_flow: float) -> str:
    if net_flow >= ETF_FLOW_BULLISH_USD:
        return "BULLISH"
    if net_flow <= ETF_FLOW_BEARISH_USD:
        return "BEARISH"
    return "NEUTRAL"


def _us_session_hours() -> float:
    now_ist = datetime.now(timezone.utc) + timedelta(hours=5.5)
    target  = now_ist.replace(
        hour=ETF_US_SESSION_IST_HOUR, minute=0, second=0, microsecond=0
    )
    if now_ist.hour >= ETF_US_SESSION_IST_HOUR:
        target += timedelta(days=1)
    return round((target - now_ist).total_seconds() / 3600, 2)


def _placeholder() -> dict[str, Any]:
    return {
        "date":                datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "net_flow":            0.0,
        "by_issuer":           {},
        "cumulative_7d":       0.0,
        "bias":                "NEUTRAL",
        "us_session_in_hours": _us_session_hours(),
        "source":              "unavailable",
        "disabled":            True,
    }
