"""
KAVACH-09 — Market Data Commands
=================================
/price BTC   /funding   /liquidation   /etf

ISSUE 3 FIX: get_ticker() now uses correct ?symbol= param (not /BTCUSDT path).
ISSUE 4 FIX: VWAP pulled from bus.vwap() when bus ready, else computed from candles.
ISSUE 2 FIX: ATR pulled from bus.atr() when bus ready, else computed from candles.
BUG-004 FIX: /liquidation uses Binance public forceOrders (no CoinGlass key needed).
BUG-005 FIX: /etf uses Farside Investors scraper (no SoSoValue key needed).
"""
from __future__ import annotations

import asyncio

from telegram import Update
from telegram.ext import ContextTypes

from config import (
    LIQ_CASCADE_THRESHOLD_USD,
    LIQ_CASCADE_MIN_RATIO,
    PAIRS,
    resolve_pair,
)
from data import coinglass_feed, etf_feed, market_feed
from data.coinglass_feed import time_to_next_funding_ms
from indicators.atr import calculate_atr, volatility_band
from indicators.vwap import session_vwap, vwap_deviation_pct


# ════════════════════════════════════════════════════════════════════
# /price  BTC
# ════════════════════════════════════════════════════════════════════

async def cmd_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.message.reply_text("Usage: /price BTC   (or ETH, SOL, BNB, XRP)")
        return

    pair = resolve_pair(ctx.args[0])
    if not pair:
        await update.message.reply_text(
            f"❌ Unknown pair: {ctx.args[0]}\nValid: BTC ETH SOL BNB XRP"
        )
        return

    # Parallel fetch
    ticker, candles, funding_map = await asyncio.gather(
        market_feed.get_ticker(pair),
        market_feed.get_candles(pair, "5m", 100),
        coinglass_feed.get_funding_rates(),
        return_exceptions=True,
    )

    if isinstance(ticker, Exception) or not ticker or ticker.get("price", 0) == 0:
        await update.message.reply_text(f"❌ Failed to fetch ticker for {pair.symbol}")
        return
    if isinstance(candles, Exception):
        candles = []
    if isinstance(funding_map, Exception):
        funding_map = {}

    price = ticker["price"]

    # ISSUE 2 & 4 FIX: prefer live bus values, fall back to candle compute
    bot = ctx.application.bot_data.get("kavach")
    bus = getattr(bot, "bus", None)

    if bus and bus.is_connected:
        atr  = bus.atr(pair.binance)  or (calculate_atr(candles, 14) if candles else 0)
        vwap = bus.vwap(pair.binance) or (session_vwap(candles)      if candles else 0)
    else:
        atr  = calculate_atr(candles, 14) if candles else 0
        vwap = session_vwap(candles)      if candles else 0

    vwap_dev = vwap_deviation_pct(price, vwap) if vwap else 0
    vol_band = volatility_band(atr, price)      if atr  else "unknown"

    # Funding rate — bus live value first, then REST result
    if bus and bus.is_connected:
        live_rate = bus.funding_rate(pair.binance) * 100
    else:
        live_rate = 0.0
    funding  = funding_map.get(pair.symbol, {}) if isinstance(funding_map, dict) else {}
    rate_pct = live_rate if live_rate != 0.0 else funding.get("rate_pct", 0)
    next_ms  = funding.get("next_funding_ms")

    # VWAP bias label
    if vwap_dev > 0.3:
        bias = "🔴 SHORT bias (extended above VWAP)"
    elif vwap_dev < -0.3:
        bias = "🟢 LONG bias (extended below VWAP)"
    else:
        bias = "🟡 NEUTRAL"

    msg = (
        f"₿ {pair.symbol}  |  Binance Futures\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Price      : ${price:,.2f}\n"
        f"📈 24h Change : {ticker['change_pct']:+.2f}%  ({ticker['change_abs']:+.2f})\n"
        f"📊 24h Volume : ${ticker['volume']/1e9:.2f}B\n"
        f"🎯 VWAP       : ${vwap:,.2f}  "
        f"({'above' if vwap_dev > 0 else 'below'} {abs(vwap_dev):.2f}%)\n"
        f"💧 Funding    : {rate_pct:+.4f}% / 8h  "
        f"({'neutral' if abs(rate_pct) < 0.04 else '⚠️ elevated'})\n"
        f"📉 ATR-14(5m) : ${atr:.2f}  (volatility: {vol_band})\n"
        f"\nSignal bias: {bias}"
    )
    if next_ms:
        msg += f"\n⏰ Next funding: {time_to_next_funding_ms(next_ms)}"

    await update.message.reply_text(msg)


# ════════════════════════════════════════════════════════════════════
# /funding
# ════════════════════════════════════════════════════════════════════

async def cmd_funding(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    funding_map = await coinglass_feed.get_funding_rates()
    if not funding_map:
        await update.message.reply_text(
            "❌ Funding rate fetch failed\n"
            "Binance FAPI unreachable — VPS network চেক করো।"
        )
        return

    lines       = ["💰 FUNDING RATES — Binance Futures", "━━━━━━━━━━━━━━━━━━━━━"]
    high_short  = []
    low_long    = []

    for p in PAIRS:
        f = funding_map.get(p.symbol)
        if not f:
            lines.append(f"{p.symbol:<10}: n/a")
            continue
        rate = f["rate_pct"]
        bias = f["bias"]
        if bias == "short_bias":
            mark = "⚠️ HIGH → short bias"
            high_short.append(p.symbol)
        elif bias == "long_bias":
            mark = "⚠️ LOW → long bias"
            low_long.append(p.symbol)
        else:
            mark = "✅ neutral"
        lines.append(f"{p.symbol:<10}: {rate:+.4f}%  {mark}")
        if f.get("next_funding_ms"):
            lines.append(f"{'':10}  next: {time_to_next_funding_ms(f['next_funding_ms'])}")

    if high_short:
        lines.append(f"\n🔴 {', '.join(high_short)} — SHORT bias active")
    if low_long:
        lines.append(f"🟢 {', '.join(low_long)} — LONG bias active")
    if high_short or low_long:
        lines.append("Strategy #3 FundingFade scan চলছে...")

    await update.message.reply_text("\n".join(lines))


# ════════════════════════════════════════════════════════════════════
# /liquidation
# BUG-004 FIX: Binance free endpoint — no CoinGlass key needed
# ════════════════════════════════════════════════════════════════════

async def cmd_liquidation(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("⏳ Liquidation data loading...")

    liq = await coinglass_feed.get_liquidations_last_hour()

    total       = liq.get("total_usd", 0)
    long_total  = liq.get("long_total", 0)
    short_total = liq.get("short_total", 0)
    bias        = liq.get("bias_ratio", 0.5)
    source      = liq.get("source", "unknown")
    count       = liq.get("count", 0)

    # Data unavailable check
    if total == 0 and count == 0:
        await update.message.reply_text(
            "⚠️ Liquidation Data Unavailable\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Binance public endpoint থেকে data পাওয়া যাচ্ছে না।\n\n"
            "Possible reasons:\n"
            "• Last 1h তে কোনো significant liquidation হয়নি\n"
            "• Binance IP rate limit hit হয়েছে\n"
            "• VPS network issue\n\n"
            f"Source: {source}\n"
            "কয়েক মিনিট পরে আবার try করো।"
        )
        return

    # Cascade status
    if total >= LIQ_CASCADE_THRESHOLD_USD:
        if bias >= LIQ_CASCADE_MIN_RATIO:
            status = "🔴🔴 EXTREME — LONG CASCADE (shorts এখন enter করো)"
        elif (1 - bias) >= LIQ_CASCADE_MIN_RATIO:
            status = "🟢🟢 EXTREME — SHORT CASCADE (longs এখন enter করো)"
        else:
            status = "🔴 EXTREME — mixed cascade, direction unclear"
    elif total >= LIQ_CASCADE_THRESHOLD_USD * 0.5:
        status = "🟡 MODERATE — cascade territory নজরে রাখো"
    elif total > 0:
        status = "🟢 LOW — normal market conditions"
    else:
        status = "⚪ No significant liquidations"

    long_pct  = bias * 100
    short_pct = (1 - bias) * 100

    lines = [
        "💥 LIQUIDATIONS — Last 60 min",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"📊 Total     : ${total/1e6:.2f}M  ({count} orders)",
        f"🔴 Long liq  : ${long_total/1e6:.2f}M  ({long_pct:.0f}%)",
        f"🟢 Short liq : ${short_total/1e6:.2f}M  ({short_pct:.0f}%)",
        "",
        f"⚠️ Cascade threshold : ${LIQ_CASCADE_THRESHOLD_USD/1e6:.0f}M",
        f"Status: {status}",
    ]

    # Per-pair breakdown
    by_pair = liq.get("by_pair", {})
    active_pairs = {
        k: v for k, v in by_pair.items() if v.get("total_usd", 0) > 0
    }
    if active_pairs:
        lines.append("\nPair breakdown:")
        for sym, d in sorted(
            active_pairs.items(), key=lambda x: -x[1].get("total_usd", 0)
        ):
            l_usd = d.get("long_usd", 0)
            s_usd = d.get("short_usd", 0)
            t_usd = d.get("total_usd", 0)
            lines.append(
                f"  {sym:<12} ${t_usd/1e6:.2f}M  "
                f"(L: ${l_usd/1e6:.2f}M / S: ${s_usd/1e6:.2f}M)"
            )

    lines.append(f"\nSource: {source}")
    await update.message.reply_text("\n".join(lines))


# ════════════════════════════════════════════════════════════════════
# /etf
# BUG-005 FIX: Farside Investors scraper — no SoSoValue key needed
# ════════════════════════════════════════════════════════════════════

async def cmd_etf(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("⏳ ETF flow data loading (Farside scraping)...")

    etf = await etf_feed.get_etf_flow()

    net      = etf.get("net_flow", 0)
    bias     = etf.get("bias", "NEUTRAL")
    by_issuer= etf.get("by_issuer", {})
    cum_7d   = etf.get("cumulative_7d", 0)
    us_h     = etf.get("us_session_in_hours", 0)
    date_str = etf.get("date", "unknown")
    source   = etf.get("source", "unknown")

    # Data unavailable
    if net == 0 and not by_issuer and "unavailable" in source:
        await update.message.reply_text(
            "⚠️ ETF Flow Data Unavailable\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Farside Investors (farside.co.uk) থেকে data পাওয়া যাচ্ছে না।\n\n"
            "Possible reasons:\n"
            "• Farside site temporarily down\n"
            "• VPS থেকে farside.co.uk block হয়েছে\n"
            "• Page structure পরিবর্তন হয়েছে\n\n"
            f"Source: {source}\n\n"
            "Manual check: https://farside.co.uk/bitcoin-etf-flow-all-data/"
        )
        return

    bias_emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "🟡"}.get(bias, "🟡")
    flow_arrow = "📈" if net > 0 else "📉" if net < 0 else "➡️"

    lines = [
        f"🏛️ BTC ETF FLOW — {date_str}",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"{flow_arrow} Net flow   : ${net/1e6:+.1f}M  "
        f"{'✅ INFLOW' if net > 0 else '❌ OUTFLOW' if net < 0 else '➡️ FLAT'}",
        "",
    ]

    # By issuer — show top 5 by abs value
    if by_issuer:
        lines.append("By ETF:")
        top = sorted(by_issuer.items(), key=lambda x: -abs(x[1]))[:8]
        for ticker, flow in top:
            bar = "▲" if flow > 0 else "▼" if flow < 0 else "─"
            lines.append(f"  {bar} {ticker:<6} ${flow/1e6:+.1f}M")

    lines.extend([
        "",
        f"📊 7-day cumulative : ${cum_7d/1e6:+.1f}M",
        f"Signal bias: {bias_emoji} {bias}",
    ])

    if bias == "BULLISH":
        lines.append("💡 ETF inflow → institutional buying → LONG bias")
    elif bias == "BEARISH":
        lines.append("💡 ETF outflow → institutional selling → SHORT bias")

    if us_h < 1:
        lines.append(f"\n⚡ US session খুলছে {us_h*60:.0f} মিনিটে — ETF flow আসছে")
    elif us_h < 4:
        lines.append(f"\n⏰ US session {us_h:.1f}h-এ — fresh data আসতে পারে")

    lines.append(f"\nSource: {source}")
    await update.message.reply_text("\n".join(lines))
