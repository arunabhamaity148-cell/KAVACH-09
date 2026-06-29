"""
KAVACH-09 — Global Configuration
================================
All constants, thresholds, pair list, API endpoints live here.
Edit this file (or override via .env) to tune the bot.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


# ────────────────────────────────────────────────────────────────────
# BOT IDENTITY
# ────────────────────────────────────────────────────────────────────
BOT_NAME    = "KAVACH-09"
BOT_VERSION = "1.0.1"
BOT_ROLE    = "Signal Intelligence Engine (manual trade only)"
EXCHANGE    = "CoinDCX USDT Futures"


# ────────────────────────────────────────────────────────────────────
# TRADING PAIRS  (CoinDCX USDT-M futures symbols → Binance fallbacks)
# ────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Pair:
    symbol: str          # display symbol e.g. "BTC-USDT"
    coindcx: str         # CoinDCX futures symbol e.g. "B-USDT-BTC"
    binance: str         # Binance futures symbol e.g. "BTCUSDT"
    price_precision: int
    qty_precision: int

PAIRS: list[Pair] = [
    Pair("BTC-USDT", "B-USDT-BTC",  "BTCUSDT",  2, 3),
    Pair("ETH-USDT", "B-USDT-ETH",  "ETHUSDT",  2, 3),
    Pair("SOL-USDT", "B-USDT-SOL",  "SOLUSDT",  2, 2),
    Pair("BNB-USDT", "B-USDT-BNB",  "BNBUSDT",  2, 2),
    Pair("XRP-USDT", "B-USDT-XRP",  "XRPUSDT",  4, 1),
]

PAIR_LOOKUP: dict[str, Pair] = {p.symbol: p for p in PAIRS}
PAIR_ALIASES: dict[str, str] = {
    "BTC": "BTC-USDT", "ETH": "ETH-USDT", "SOL": "SOL-USDT",
    "BNB": "BNB-USDT", "XRP": "XRP-USDT",
}


def resolve_pair(name: str) -> Pair | None:
    """Resolve user input like 'BTC' or 'BTC-USDT' to a Pair object."""
    name = name.upper().strip()
    if name in PAIR_LOOKUP:
        return PAIR_LOOKUP[name]
    if name in PAIR_ALIASES:
        return PAIR_LOOKUP[PAIR_ALIASES[name]]
    return None


# ────────────────────────────────────────────────────────────────────
# TIMEFRAMES & SCANNING
# ────────────────────────────────────────────────────────────────────
SCAN_INTERVAL_SECONDS  = 30          # signal scan loop interval
CANDLE_TIMEFRAME       = "5m"        # primary chart TF
CANDLE_HISTORY         = 200         # candles to keep in memory
TRADE_TAPE_WINDOW      = 5000        # last N trades per pair for CVD
SESSION_RESET_UTC_HOUR = 0           # VWAP resets at 00:00 UTC = 05:30 IST


# ────────────────────────────────────────────────────────────────────
# STRATEGY THRESHOLDS  (FIXED: relaxed for more frequent signals)
# ────────────────────────────────────────────────────────────────────

# CVD Divergence (S1)
CVD_MIN_CANDLES         = 3
CVD_MIN_PRICE_MOVE_PCT  = 0.15       # FIX: was 0.30 — too strict
CVD_MIN_DIVERGENCE_PCT  = 8.0        # FIX: was 15.0 — too strict
CVD_MIN_VOLUME_PCT_AVG  = 80.0

# VWAP Reclaim (S2)
VWAP_RECLAIM_MAX_DEV_PCT = 0.50      # price must be within 0.5% of VWAP
VWAP_RECLAIM_MIN_DEV_PCT = 0.05      # not exactly on VWAP — must be reclaim

# Funding Fade (S3)
FUNDING_HIGH_THRESHOLD  = 0.030        # FIX: was 0.050 — too extreme
FUNDING_LOW_THRESHOLD   = -0.010     # FIX: was -0.020 — too extreme
FUNDING_NEUTRAL_HIGH    = 0.040        # warn above this for shorts
FUNDING_NEUTRAL_LOW     = -0.015     # warn below this for longs

# Liquidation Cascade (S4)
LIQ_CASCADE_THRESHOLD_USD = 50_000_000   # FIX: was 300M — now 50M
LIQ_CASCADE_MIN_RATIO     = 0.65          # FIX: was 0.70 — slightly relaxed

# ETF Flow (S5)
ETF_FLOW_BULLISH_USD   = 150_000_000     # FIX: was 200M — slightly relaxed
ETF_FLOW_BEARISH_USD   = -150_000_000    # FIX: was -200M
ETF_US_SESSION_IST_HOUR = 19              # 7 PM IST = US session open


# ────────────────────────────────────────────────────────────────────
# SIGNAL SCORING WEIGHTS  (Section 5 of blueprint)
# ────────────────────────────────────────────────────────────────────
SCORE_WEIGHTS: dict[str, int] = {
    "cvd_divergence"   : 30,
    "vwap_extended"    : 20,
    "volume_declining" : 20,
    "price_structure"  : 15,
    "funding_neutral"  : 15,
}

SCORE_HIGH   = 90    # HIGH confidence
SCORE_MEDIUM = 75    # MEDIUM confidence
SCORE_LOW    = 60    # LOW confidence (warn)
SCORE_MIN    = 50    # FIX: was 60 — allow more signals through


# ────────────────────────────────────────────────────────────────────
# RISK MANAGEMENT
# ────────────────────────────────────────────────────────────────────
DEFAULT_RISK_PCT        = 0.01        # 1% per trade
MAX_RISK_PCT            = 0.02        # never exceed 2%
MAX_DAILY_LOSS_PCT      = 0.05        # 5% daily stop
MAX_OPEN_POSITIONS      = 2
DEFAULT_LEVERAGE        = 5
ATR_STOP_MULTIPLIER     = 1.5         # stop = 1.5 × ATR
RR_MIN                  = 1.5         # reject signals with RR < 1.5
HOLD_TIME_MAX_MINUTES   = 45          # scalping timeframe


# ────────────────────────────────────────────────────────────────────
# API ENDPOINTS
# ────────────────────────────────────────────────────────────────────
# CoinDCX public REST
COINDCX_REST     = "https://api.coindcx.com"
COINDCX_TICKER   = f"{COINDCX_REST}/exchange/ticker"
COINDCX_CANDLES  = f"{COINDCX_REST}/market_data/candlestick"

# CoinDCX public WebSocket — futures stream
COINDCX_WS        = "wss://stream.coindcx.com/stream"

# Binance USDT-M futures fallback (CoinDCX shares liquidity here)
BINANCE_FAPI      = "https://fapi.binance.com"
BINANCE_TICKER    = f"{BINANCE_FAPI}/fapi/v1/ticker/24hr"
BINANCE_KLINES    = f"{BINANCE_FAPI}/fapi/v1/klines"
BINANCE_FUNDING   = f"{BINANCE_FAPI}/fapi/v1/premiumIndex"
BINANCE_LIQ       = f"{BINANCE_FAPI}/fapi/v1/allForceOrders"
BINANCE_WS        = "wss://fstream.binance.com/stream"

# CoinGlass (free tier)
COINGLASS_BASE    = "https://open-api-v3.coinglass.com/api/futures"
COINGLASS_API_KEY = os.getenv("COINGLASS_API_KEY", "").strip()

# SoSoValue (free, scrape-friendly)
SOSOVALUE_BASE    = "https://api.sosovalue.com/openapi"

# AI provider (Groq free tier — best free LLM)
GROQ_API_KEY      = os.getenv("GROQ_API_KEY", "").strip()
GROQ_API_URL      = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL        = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# Fallback AI provider (Google Gemini free tier)
GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_API_URL    = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_MODEL      = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")


# ────────────────────────────────────────────────────────────────────
# TELEGRAM
# ────────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "").strip()


# ────────────────────────────────────────────────────────────────────
# DATABASE
# ────────────────────────────────────────────────────────────────────
DB_PATH = os.getenv("KAVACH_DB_PATH", os.path.join(os.path.dirname(__file__), "kavach09.db"))


# ────────────────────────────────────────────────────────────────────
# ALERT COOLDOWN — avoid spamming the same pair/direction
# ────────────────────────────────────────────────────────────────────
ALERT_COOLDOWN_MINUTES = 15           # FIX: was 30 — shorter for more signals


# ────────────────────────────────────────────────────────────────────
# IST TIME HELPER
# ────────────────────────────────────────────────────────────────────
IST_OFFSET_HOURS = 5.5


# ────────────────────────────────────────────────────────────────────
# FEATURE FLAGS
# ────────────────────────────────────────────────────────────────────
FEATURE_AI_EXPLAIN     = True        # /explain uses Groq
FEATURE_AI_POSTMORTEM  = True        # /postmortem enriched by AI
FEATURE_TELEMETRY      = False       # disabled by default


__all__ = [name for name in dir() if not name.startswith("_")]
