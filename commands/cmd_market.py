"""
KAVACH-09 — Market Data Commands
=================================
/price BTC   /funding   /liquidation   /etf
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import ContextTypes

from config import resolve_pair, PAIRS, IST_OFFSET_HOURS
from data import market_feed, coinglass_feed, etf_feed
from data.coinglass_feed import time_to_next_funding_ms
from indicators.atr import calculate_atr, volatility_band
from indicators.vwap import session_vwap, vwap_deviation_pct


async def cmd_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /price BTC   (or ETH, SOL, BNB, XRP)")
        return
    pair = resolve_pair(ctx.args[0])
    if not pair:
        await update.message.reply_text(f"❌ Unknown pair: {ctx.args[0]}\nValid: BTC ETH SOL BNB XRP")
        return

    # Run fetches in parallel
    ticker_task = asyncio.create_task(market_feed.get_ticker(pair))
    candle_task = asyncio.create_task(market_feed.get_candles(pair, "5m", 100))
    funding_task = asyncio.create_task(coinglass_feed.get_funding_rates())
    ticker, candles, funding_map = await asyncio.gather(
        ticker_task, candle_task, funding_task, return_exceptions=True
    )
    if isinstance(ticker, Exception) or not ticker:
        await update.message.reply_text(f"❌ Failed to fetch ticker for {pair.symbol}")
        return
    if isinstance(candles, Exception):
        candles = []
    if isinstance(funding_map, Exception):
        funding_map = {}

    price = ticker["price"]
    vwap = session_vwap(candles) if candles else 0
    vwap_dev = vwap_deviation_pct(price, vwap) if vwap else 0
    atr = calculate_atr(candles, 14) if candles else 0
    vol_band = volatility_band(atr, price) if atr else "unknown"

    funding = funding_map.get(pair.symbol, {})
    rate_pct = funding.get("rate_pct", 0)
    next_ms = funding.get("next_funding_ms")

    if vwap_dev > 0.3:
        bias = "🔴 SHORT bias (extended above VWAP)"
    elif vwap_dev < -0.3:
        bias = "🟢 LONG bias (extended below VWAP)"
    else:
        bias = "🟡 NEUTRAL"

    msg = (
        f"₿ {pair.symbol}  |  CoinDCX Futures\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Price     : ${price:,.2f}\n"
        f"📈 24h Change: {ticker['change_pct']:+.2f}% ({ticker['change_abs']:+.2f})\n"
        f"📊 24h Volume: ${ticker['volume']/1e9:.2f}B\n"
        f"🎯 VWAP      : ${vwap:,.2f} ({'above' if vwap_dev > 0 else 'below'} {abs(vwap_dev):.2f}%)\n"
        f"💧 Funding   : {rate_pct:+.3f}% / 8h ({'neutral' if abs(rate_pct) < 0.04 else '⚠️ elevated'})\n"
        f"📉 ATR-14(5m): ${atr:.2f} (volatility: {vol_band})\n\n"
        f"Signal bias: {bias}"
    )
    if next_ms:
        msg += f"\n⏰ Next funding: {time_to_next_funding_ms(next_ms)}"
    await update.message.reply_text(msg)


async def cmd_funding(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    funding_map = await coinglass_feed.get_funding_rates()
    if not funding_map:
        await update.message.reply_text("❌ Failed to fetch funding rates")
        return

    lines = ["💰 FUNDING RATES — CoinDCX Futures", "━━━━━━━━━━━━━━━━━━━━━"]
    high_short = []
    low_long = []
    for p in PAIRS:
        f = funding_map.get(p.symbol)
        if not f:
            lines.append(f"{p.symbol} : n/a")
            continue
        rate = f["rate_pct"]
        bias = f["bias"]
        if bias == "short_bias":
            mark = "⚠️ HIGH (short bias)"
            high_short.append(p.symbol)
        elif bias == "long_bias":
            mark = "⚠️ LOW (long bias)"
            low_long.append(p.symbol)
        else:
            mark = "✅ neutral"
        lines.append(f"{p.symbol} : {rate:+.3f}% {mark}")
        if f.get("next_funding_ms"):
            lines.append(f"          next: {time_to_next_funding_ms(f['next_funding_ms'])}")

    if high_short:
        lines.append(f"\n🔴 {' & '.join(high_short)} SHORT bias active")
    if low_long:
        lines.append(f"🟢 {' & '.join(low_long)} LONG bias active")
    if high_short or low_long:
        lines.append("Strategy #3 scan করছি...")

    await update.message.reply_text("\n".join(lines))


async def cmd_liquidation(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    from config import LIQ_CASCADE_THRESHOLD_USD
    liq = await coinglass_feed.get_liquidations_last_hour()
    total = liq.get("total_usd", 0)
    long_total = liq.get("long_total", 0)
    short_total = liq.get("short_total", 0)
    bias = liq.get("bias_ratio", 0.5)

    # Categorise
    if total >= LIQ_CASCADE_THRESHOLD_USD:
        status = "🔴 EXTREME — cascade territory"
    elif total >= LIQ_CASCADE_THRESHOLD_USD * 0.5:
        status = "🟡 MODERATE — watch closely"
    elif total > 0:
        status = "🟢 LOW — normal market"
    else:
        status = "⚪ No data"

    lines = [
        "💥 LIQUIDATIONS — Last 60 min",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"Total market : ${total/1e6:.1f}M",
        f"Long liq     : ${long_total/1e6:.1f}M ({bias*100:.0f}%)" if total else "Long liq     : n/a",
        f"Short liq    : ${short_total/1e6:.1f}M ({(1-bias)*100:.0f}%)" if total else "Short liq    : n/a",
        "",
        f"⚠️ Threshold: ${LIQ_CASCADE_THRESHOLD_USD/1e6:.0f}M (cascade trigger)",
        f"Status: {status}",
        "",
        f"Source: {liq.get('source', 'unknown')}",
    ]
    # Per-pair breakdown if available
    by_pair = liq.get("by_pair", {})
    if by_pair:
        lines.append("\nBy pair:")
        for sym, d in by_pair.items():
            if d.get("total_usd", 0) > 0:
                lines.append(
                    f"  {sym}: ${d['total_usd']/1e6:.1f}M "
                    f"(L:${d.get('long_usd',0)/1e6:.1f}M / S:${d.get('short_usd',0)/1e6:.1f}M)"
                )
    await update.message.reply_text("\n".join(lines))


async def cmd_etf(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    etf = await etf_feed.get_etf_flow()
    net = etf.get("net_flow", 0)
    bias = etf.get("bias", "NEUTRAL")
    bias_emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "🟡"}.get(bias, "🟡")
    by_issuer = etf.get("by_issuer", {})
    cum_7d = etf.get("cumulative_7d", 0)
    us_h = etf.get("us_session_in_hours", 0)

    lines = [
        "🏛️ BTC ETF FLOW — SoSoValue",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"আজকের flow  : ${net/1e6:+.1f}M {'✅ NET INFLOW' if net > 0 else '❌ NET OUTFLOW'}",
    ]
    if by_issuer:
        for issuer, flow in sorted(by_issuer.items(), key=lambda x: -abs(x[1]))[:5]:
            lines.append(f"{issuer:<14}: ${flow/1e6:+.1f}M")
    lines.append("")
    lines.append(f"📊 Last 7 days: ${cum_7d/1e6:+.1f}M cumulative")
    lines.append(f"Signal bias: {bias_emoji} {bias}")
    if bias == "BULLISH" and us_h < 6:
        lines.append(f"⚡ US session opens in {us_h:.1f}h — ETF Flow strategy watching")
    elif us_h < 1:
        lines.append(f"⚡ US session opens in {us_h:.1f}h")
    lines.append("")
    lines.append(f"⏰ US session opens: {us_h:.1f}h")
    lines.append(f"Source: {etf.get('source', 'unknown')}")
    await update.message.reply_text("\n".join(lines))
