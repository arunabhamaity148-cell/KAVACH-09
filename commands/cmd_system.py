"""
KAVACH-09 — System Commands
============================
/start  /status  /pause  /resume  /help  /ping
"""
from __future__ import annotations

import time
from telegram import Update
from telegram.ext import ContextTypes

from alert_manager import format_help, format_status
from config import BOT_NAME


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    bot = ctx.application.bot_data["bot"]
    msg = (
        f"⚔️ {BOT_NAME} চালু হয়েছে\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📡 Data feeds: CoinDCX WS {'✅' if bot.bus and bot.bus.is_connected else '🔄'} | "
        f"CoinGlass {'✅' if bot.coinglass_ok else '⚠️'} | SoSoValue {'✅' if bot.sosovalue_ok else '⚠️'}\n"
        f"🔍 Monitoring: BTC ETH SOL BNB XRP\n"
        f"⚡ Strategies: 5টা active\n"
        f"📊 Signal scan: প্রতি 30 seconds\n\n"
        f"/help লিখো সব command দেখতে"
    )
    await update.message.reply_text(msg)


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    bot = ctx.application.bot_data["bot"]
    today_signals = bot.today_signal_count()
    today_trades  = bot.today_trade_count()
    today_wins    = bot.today_result_count("WIN")
    today_losses  = bot.today_result_count("LOSS")
    uptime = time.time() - bot.start_time
    msg = format_status(
        uptime_seconds=uptime,
        scan_count=bot.engine.scan_count,
        paused=bot.engine.is_paused,
        ws_connected=bot.bus.is_connected if bot.bus else False,
        ws_latency_ms=bot.bus.latency_ms if bot.bus else 0,
        coinglass_ok=bot.coinglass_ok,
        sosovalue_ok=bot.sosovalue_ok,
        today_signals=today_signals,
        today_trades=today_trades,
        today_wins=today_wins,
        today_losses=today_losses,
    )
    await update.message.reply_text(msg)


async def cmd_pause(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    bot = ctx.application.bot_data["bot"]
    bot.engine.pause()
    await update.message.reply_text(
        "⏸️ Signal scan paused\n"
        "Resume করতে /resume লেখো"
    )


async def cmd_resume(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    bot = ctx.application.bot_data["bot"]
    bot.engine.resume()
    await update.message.reply_text("▶️ Signal scan resumed")


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(format_help())


async def cmd_ping(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Quick health probe — useful for debugging."""
    bot = ctx.application.bot_data["bot"]
    await update.message.reply_text(
        f"🏓 pong\n"
        f"WS: {'✅' if bot.bus and bot.bus.is_connected else '❌'}\n"
        f"Scans: {bot.engine.scan_count}\n"
        f"Paused: {bot.engine.is_paused}"
    )
