"""
KAVACH-09 — BTC ETF Flow Feed
==============================
NO PAID API KEY REQUIRED.

Sources (tried in order):
  1. Farside Investors — https://farside.co.uk/bitcoin-etf-flow-all-data/
     Free public page scraped with BeautifulSoup.
     Oracle Cloud IP may get 403 — we retry with different User-Agents.

  2. Alternative Farside mirror — https://www.farside.co.uk/ (www subdomain)

  3. Stale cache — last successful fetch if all fetches fail.

  4. Neutral placeholder with clear error message.

Cache TTL: 60 minutes (ETF data updates once per day).

403 workaround: Farside blocks some cloud provider IPs.
  We rotate User-Agents and add realistic browser headers.
  If still blocked, the cached value from the last successful
  fetch is used (which persists across restarts via _cache).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from config import ETF_FLOW_BEARISH_USD, ETF_FLOW_BULLISH_USD, ETF_US_SESSION_IST_HOUR

log = logging.getLogger("kavach.etf")

# ── Cache ────────────────────────────────────────────────────────────
_cache: dict[str, Any] = {}
_cache_ts: float = 0.0
_CACHE_TTL = 60 * 60   # 60 minutes

# ── Farside URLs to try in order ─────────────────────────────────────
_FARSIDE_URLS = [
    "https://farside.co.uk/bitcoin-etf-flow-all-data/",
    "https://www.farside.co.uk/bitcoin-etf-flow-all-data/",
]

# Rotate user agents to avoid IP-based blocking
_USER_AGENTS = [
    # Chrome Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Firefox Linux
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    # Chrome Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Safari Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

_KNOWN_TICKERS = {
    "IBIT", "FBTC", "BITB", "ARKB", "BTCO", "EZBC",
    "BRRR", "HODL", "DEFI", "GBTC", "BTC",
}

_ua_index = 0  # rotates on each call


# ════════════════════════════════════════════════════════════════════
# PUBLIC API
# ════════════════════════════════════════════════════════════════════

async def get_etf_flow() -> dict[str, Any]:
    """
    Returns:
        {
            "date":                "2026-06-27",
            "net_flow":            float,    # USD (positive = inflow)
            "by_issuer":           {"IBIT": float, "GBTC": float, ...},
            "cumulative_7d":       float,
            "bias":                "BULLISH"|"BEARISH"|"NEUTRAL",
            "us_session_in_hours": float,
            "source":              str,
        }
    """
    global _cache, _cache_ts

    now = _time.time()
    if _cache and (now - _cache_ts) < _CACHE_TTL:
        return _cache

    # Try all Farside URLs with rotating User-Agents
    result = await _try_all_farside_urls()

    if result:
        _cache    = result
        _cache_ts = now
        log.info(
            f"ETF data refreshed: date={result['date']} "
            f"net={result['net_flow']/1e6:+.1f}M  source={result['source']}"
        )
        return result

    # Stale cache fallback
    if _cache:
        stale = dict(_cache)
        stale["source"]              = stale.get("source", "Farside") + " ⚠️ (cached — refresh failed)"
        stale["us_session_in_hours"] = _us_session_hours()
        log.info("ETF: serving stale cache (fresh fetch failed)")
        return stale

    # Nothing available
    log.warning("ETF: no data (Farside unreachable, no cache)")
    return _unavailable_placeholder()


# ════════════════════════════════════════════════════════════════════
# FARSIDE SCRAPER
# ════════════════════════════════════════════════════════════════════

async def _try_all_farside_urls() -> dict[str, Any] | None:
    """Try each Farside URL with a rotating User-Agent until one works."""
    global _ua_index

    for url in _FARSIDE_URLS:
        ua = _USER_AGENTS[_ua_index % len(_USER_AGENTS)]
        _ua_index += 1

        headers = {
            "User-Agent":      ua,
            "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection":      "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control":   "no-cache",
            "Referer":         "https://www.google.com/",
        }

        try:
            html = await _fetch_url(url, headers)
            if html:
                result = _parse_farside_html(html, url)
                if result:
                    return result
        except Exception as e:
            log.debug(f"Farside URL {url} failed: {e}")
            continue

    return None


async def _fetch_url(url: str, headers: dict) -> str | None:
    """Fetch a URL and return HTML text, or None on failure."""
    try:
        connector = aiohttp.TCPConnector(ssl=True)
        async with aiohttp.ClientSession(connector=connector) as s:
            async with s.get(url, headers=headers, timeout=25, allow_redirects=True) as r:
                if r.status == 200:
                    return await r.text(encoding="utf-8", errors="replace")
                elif r.status == 403:
                    log.warning(
                        f"Farside returned HTTP 403 for {url}\n"
                        "Oracle Cloud IP may be blocked by Farside.\n"
                        "Serving cached/neutral data."
                    )
                    return None
                else:
                    log.warning(f"Farside {url} returned HTTP {r.status}")
                    return None
    except asyncio.TimeoutError:
        log.warning(f"Farside fetch timed out: {url}")
        return None
    except Exception as e:
        log.warning(f"Farside fetch error ({url}): {e}")
        return None


def _parse_farside_html(html: str, source_url: str) -> dict[str, Any] | None:
    """Parse Farside HTML and extract ETF flow data."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        log.error("beautifulsoup4 not installed — pip install beautifulsoup4")
        return None

    if not html or len(html) < 500:
        log.warning("Farside: empty/tiny response")
        return None

    soup   = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")

    if not tables:
        log.warning("Farside: no <table> found in HTML")
        return None

    # Pick table with most ETF ticker columns
    best_table = None
    best_score = 0
    for tbl in tables:
        ths   = [th.get_text(strip=True).upper() for th in tbl.find_all("th")]
        score = sum(1 for h in ths if h in _KNOWN_TICKERS)
        if score > best_score:
            best_score = score
            best_table = tbl

    if not best_table or best_score < 2:
        log.warning(f"Farside: no valid ETF table (best ticker score={best_score})")
        return None

    # Parse header row
    header_row = best_table.find("tr")
    if not header_row:
        return None
    headers = [
        th.get_text(strip=True).upper()
        for th in header_row.find_all(["th", "td"])
    ]
    log.debug(f"Farside headers ({len(headers)}): {headers}")

    # Parse all data rows
    rows: list[dict] = []
    for tr in best_table.find_all("tr")[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if len(cells) < 3:
            continue
        row = {headers[i]: cells[i] for i in range(min(len(headers), len(cells)))}
        rows.append(row)

    if not rows:
        log.warning("Farside: parsed 0 data rows")
        return None

    # Find most recent row with date + numeric data
    latest: dict | None = None
    for row in reversed(rows):
        date_val = row.get("DATE", "")
        if _parse_date(date_val) and _has_numeric(row):
            latest = row
            break

    if not latest:
        latest = rows[-1]

    # Extract by_issuer
    by_issuer: dict[str, float] = {}
    for ticker in _KNOWN_TICKERS:
        val = _parse_flow(latest.get(ticker, ""))
        if val != 0.0:
            by_issuer[ticker] = val * 1_000_000   # $M → USD

    # Net flow from Total column or sum
    total_raw = latest.get("TOTAL", "")
    net_flow  = (_parse_flow(total_raw) * 1_000_000) if total_raw else sum(by_issuer.values())

    # 7-day cumulative
    recent_7 = rows[-7:] if len(rows) >= 7 else rows
    cum_7d   = 0.0
    for row in recent_7:
        t_raw = row.get("TOTAL", "")
        if t_raw:
            cum_7d += _parse_flow(t_raw) * 1_000_000
        else:
            cum_7d += sum(
                _parse_flow(row.get(tk, "")) * 1_000_000
                for tk in _KNOWN_TICKERS
            )

    date_str = _parse_date(latest.get("DATE", "")) or \
               datetime.now(timezone.utc).strftime("%Y-%m-%d")

    return {
        "date":                date_str,
        "net_flow":            net_flow,
        "by_issuer":           by_issuer,
        "cumulative_7d":       cum_7d,
        "bias":                _etf_bias(net_flow),
        "us_session_in_hours": _us_session_hours(),
        "source":              f"Farside Investors ({source_url})",
    }


# ════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════

def _parse_flow(raw: str) -> float:
    """
    Farside cell → float in $M.
    "123.4" → 123.4 | "(45.2)" → -45.2 | "-" → 0.0 | "" → 0.0
    """
    raw = raw.strip().replace(",", "")
    if not raw or raw in ("-", "—", "n/a", "N/A", "*", "–"):
        return 0.0
    negative = raw.startswith("(") and raw.endswith(")")
    raw = raw.strip("()")
    try:
        val = float(raw)
        return -val if negative else val
    except ValueError:
        return 0.0


def _parse_date(raw: str) -> str | None:
    """Parse Farside date cell → YYYY-MM-DD or None."""
    raw = raw.strip()
    if not raw:
        return None
    for fmt in ("%d %b %Y", "%b %d, %Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _has_numeric(row: dict) -> bool:
    return any(_parse_flow(row.get(tk, "")) != 0.0 for tk in _KNOWN_TICKERS)


def _etf_bias(net_flow_usd: float) -> str:
    if net_flow_usd >= ETF_FLOW_BULLISH_USD:
        return "BULLISH"
    if net_flow_usd <= ETF_FLOW_BEARISH_USD:
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


def _unavailable_placeholder() -> dict[str, Any]:
    return {
        "date":                datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "net_flow":            0.0,
        "by_issuer":           {},
        "cumulative_7d":       0.0,
        "bias":                "NEUTRAL",
        "us_session_in_hours": _us_session_hours(),
        "source":              (
            "unavailable — Farside (farside.co.uk) blocked this IP.\n"
            "Manual: https://farside.co.uk/bitcoin-etf-flow-all-data/"
        ),
    }
