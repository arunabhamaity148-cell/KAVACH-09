"""
KAVACH-09 — BTC ETF Flow Feed
==============================
NO PAID API KEY REQUIRED.

Sources (tried in order):
  1. Farside Investors — https://farside.co.uk/bitcoin-etf-flow-all-data/
     Free public page, scraped with BeautifulSoup.
     Updates ~daily (after US market close EST).

  2. Stale cache — last successful fetch served if fresh fetch fails.

  3. Neutral placeholder — shown if no data available at all.

Farside table columns (as of 2025):
  Date | IBIT | FBTC | BITB | ARKB | BTCO | EZBC | BRRR | HODL | DEFI | GBTC | BTC | Total
  Values are in $M USD. "-" or empty = 0. Parentheses = negative.

Cache TTL: 60 minutes (ETF data updates once per day, but we refresh
           hourly so fresh days are picked up promptly).
"""
from __future__ import annotations

import logging
import re
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from config import ETF_US_SESSION_IST_HOUR

log = logging.getLogger("kavach.etf")

# ── Cache ────────────────────────────────────────────────────────────
_cache: dict[str, Any] = {}
_cache_ts: float = 0.0
_CACHE_TTL = 60 * 60     # 60 minutes

# ── Farside config ───────────────────────────────────────────────────
_FARSIDE_URL = "https://farside.co.uk/bitcoin-etf-flow-all-data/"
_FARSIDE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://farside.co.uk/",
}

# Known ETF tickers on Farside (column order may vary — we match by header)
_KNOWN_TICKERS = {
    "IBIT", "FBTC", "BITB", "ARKB", "BTCO", "EZBC",
    "BRRR", "HODL", "DEFI", "GBTC", "BTC",
}


# ════════════════════════════════════════════════════════════════════
# PUBLIC API
# ════════════════════════════════════════════════════════════════════

async def get_etf_flow() -> dict[str, Any]:
    """
    Returns:
        {
            "date":               "2026-06-26",
            "net_flow":           float,    # USD (positive = inflow)
            "by_issuer":          {"IBIT": float, "GBTC": float, ...},
            "cumulative_7d":      float,    # sum of last 7 trading days
            "bias":               "BULLISH"|"BEARISH"|"NEUTRAL",
            "us_session_in_hours": float,
            "source":             str,
        }
    """
    global _cache, _cache_ts
    now = _time.time()

    # Serve cache if fresh
    if _cache and (now - _cache_ts) < _CACHE_TTL:
        return _cache

    # Try Farside scrape
    try:
        result = await _scrape_farside()
        if result:
            _cache    = result
            _cache_ts = now
            log.info(
                f"ETF data refreshed from Farside: "
                f"date={result['date']} net={result['net_flow']/1e6:+.1f}M"
            )
            return result
    except Exception as e:
        log.warning(f"Farside scrape failed: {e}")

    # Stale cache fallback
    if _cache:
        stale = dict(_cache)
        stale["source"] = stale.get("source", "Farside") + " (cached — refresh failed)"
        stale["us_session_in_hours"] = _us_session_hours()
        log.info("ETF: serving stale cache")
        return stale

    # Nothing available
    log.warning("ETF: no data available (Farside unreachable, no cache)")
    return _unavailable_placeholder()


# ════════════════════════════════════════════════════════════════════
# FARSIDE SCRAPER
# ════════════════════════════════════════════════════════════════════

async def _scrape_farside() -> dict[str, Any] | None:
    """
    Scrapes farside.co.uk and returns the most recent trading day's data.
    Handles:
      - Values in $M (e.g. "123.4")
      - Parentheses as negative (e.g. "(45.2)" = -45.2M)
      - Dashes / empty cells = 0
      - Multiple tables (picks the one with most ETF ticker columns)
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        log.error("beautifulsoup4 not installed — run: pip install beautifulsoup4")
        return None

    html: str = ""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                _FARSIDE_URL,
                headers=_FARSIDE_HEADERS,
                timeout=20,
            ) as r:
                if r.status != 200:
                    log.warning(f"Farside returned HTTP {r.status}")
                    return None
                html = await r.text(encoding="utf-8", errors="replace")
    except Exception as e:
        log.warning(f"Farside HTTP error: {e}")
        return None

    if not html or len(html) < 500:
        log.warning("Farside returned empty/tiny response")
        return None

    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        log.warning("Farside: no <table> found in page")
        return None

    # Pick table with most ETF ticker columns
    best_table = None
    best_score = 0
    for tbl in tables:
        headers = [th.get_text(strip=True).upper() for th in tbl.find_all("th")]
        score   = sum(1 for h in headers if h in _KNOWN_TICKERS)
        if score > best_score:
            best_score = score
            best_table = tbl

    if not best_table or best_score < 2:
        log.warning(f"Farside: no ETF table found (best score={best_score})")
        return None

    # Parse column headers
    header_row = best_table.find("tr")
    if not header_row:
        return None
    headers = [th.get_text(strip=True).upper() for th in header_row.find_all(["th", "td"])]
    log.debug(f"Farside headers: {headers}")

    # Get all data rows (skip header)
    rows = best_table.find_all("tr")[1:]
    if not rows:
        return None

    # ── Parse last 7+ rows to get recent data ───────────────────
    # We scan from the bottom up to find the most recent non-empty row
    daily_rows: list[dict] = []
    for row in rows:
        cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
        if not cells or len(cells) < 3:
            continue
        row_dict: dict[str, Any] = {}
        for i, cell in enumerate(cells):
            if i < len(headers):
                row_dict[headers[i]] = cell
        daily_rows.append(row_dict)

    if not daily_rows:
        return None

    # Find most recent row with a parseable date and at least one numeric value
    latest_row: dict | None = None
    for row in reversed(daily_rows):
        date_val = row.get("DATE", "")
        if _parse_date(date_val) and _has_numeric(row):
            latest_row = row
            break

    if not latest_row:
        # Last resort: just use the last row
        latest_row = daily_rows[-1]

    # ── Extract by_issuer ────────────────────────────────────────
    by_issuer: dict[str, float] = {}
    for ticker in _KNOWN_TICKERS:
        raw = latest_row.get(ticker, "")
        val = _parse_flow(raw)
        if val != 0.0:
            by_issuer[ticker] = val * 1_000_000   # convert $M → USD

    # Net flow from "Total" column or sum of issuers
    total_raw = latest_row.get("TOTAL", "")
    if total_raw:
        net_flow = _parse_flow(total_raw) * 1_000_000
    else:
        net_flow = sum(by_issuer.values())

    # ── 7-day cumulative ─────────────────────────────────────────
    recent_7 = daily_rows[-7:] if len(daily_rows) >= 7 else daily_rows
    cum_7d   = 0.0
    for row in recent_7:
        t = row.get("TOTAL", "")
        if t:
            cum_7d += _parse_flow(t) * 1_000_000
        else:
            cum_7d += sum(_parse_flow(row.get(tk, "")) for tk in _KNOWN_TICKERS) * 1_000_000

    date_str = _parse_date(latest_row.get("DATE", "")) or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    return {
        "date":                date_str,
        "net_flow":            net_flow,
        "by_issuer":           by_issuer,
        "cumulative_7d":       cum_7d,
        "bias":                _etf_bias(net_flow),
        "us_session_in_hours": _us_session_hours(),
        "source":              "Farside Investors (farside.co.uk)",
    }


# ════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════

def _parse_flow(raw: str) -> float:
    """
    Parse Farside cell value to float ($M).
    Examples:
        "123.4"    → 123.4
        "(45.2)"   → -45.2   (parentheses = negative)
        "-"        → 0.0
        ""         → 0.0
        "1,234.5"  → 1234.5
    """
    raw = raw.strip().replace(",", "")
    if not raw or raw in ("-", "—", "n/a", "N/A", "*"):
        return 0.0
    # Parentheses = negative
    negative = raw.startswith("(") and raw.endswith(")")
    raw = raw.strip("()")
    try:
        val = float(raw)
        return -val if negative else val
    except ValueError:
        return 0.0


def _parse_date(raw: str) -> str | None:
    """Try to parse a date string from Farside. Returns YYYY-MM-DD or None."""
    raw = raw.strip()
    if not raw:
        return None
    # Common formats on Farside: "26 Jun 2026", "Jun 26, 2026", "2026-06-26"
    formats = ["%d %b %Y", "%b %d, %Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"]
    for fmt in formats:
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    # Regex fallback: extract any YYYY pattern
    m = re.search(r"(\d{4})", raw)
    if m:
        return None   # year found but can't parse full date
    return None


def _has_numeric(row: dict) -> bool:
    """Return True if at least one ticker column has a non-zero value."""
    for ticker in _KNOWN_TICKERS:
        if _parse_flow(row.get(ticker, "")) != 0.0:
            return True
    return False


def _etf_bias(net_flow_usd: float) -> str:
    from config import ETF_FLOW_BULLISH_USD, ETF_FLOW_BEARISH_USD
    if net_flow_usd >= ETF_FLOW_BULLISH_USD:
        return "BULLISH"
    if net_flow_usd <= ETF_FLOW_BEARISH_USD:
        return "BEARISH"
    return "NEUTRAL"


def _us_session_hours() -> float:
    """Hours until US market open (7 PM IST = 13:30 UTC)."""
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
        "source":              "unavailable (Farside unreachable — try again later)",
    }
