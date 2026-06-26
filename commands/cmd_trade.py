"""
KAVACH-09 — Trade Log Commands
===============================
/log ETH SHORT 3840 3852 3808   /win 47   /loss 47   /be 47
/open   /close 47 3815
"""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

import database as db
from trade_logger import log_trade, mark_result, close_trade, list_open_trades
from data import market_feed
from config import resolve_pair


async def cmd_log(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Usage: /log ETH SHORT 3840 3852 3808 [strategy]"""
    if len(ctx.args) < 5:
        await update.message.reply_text(
            "Usage: /log PAIR DIRECTION ENTRY STOP TARGET [STRATEGY]\n"
            "Example: /log ETH SHORT 3840 3852 3808\n"
            "Strategies: CVD_DIVERGENCE, VWAP_RECLAIM, FUNDING_FADE, LIQ_CASCADE, ETF_FLOW, MANUAL"
        )
        return
    pair_str, direction = ctx.args[0], ctx.args[1].upper()
    try:
        entry  = float(ctx.args[2])
        stop   = float(ctx.args[3])
        target = float(ctx.args[4])
    except ValueError:
        await update.message.reply_text("❌ Entry/Stop/Target must be numbers")
        return
    strategy = ctx.args[5] if len(ctx.args) >= 6 else "MANUAL"

    result = log_trade(pair_str, direction, entry, stop, target, strategy)
    if "error" in result:
        await update.message.reply_text(f"❌ {result['error']}")
        return

    msg = (
        f"📝 TRADE #{result['trade_id']} LOGGED\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Pair      : {result['pair']}\n"
        f"Direction : {result['direction']}\n"
        f"Entry     : ${result['entry']:,.2f}\n"
        f"Stop      : ${result['stop']:,.2f} (+{result['risk_pct']:.2f}%)\n"
        f"Target    : ${result['target']:,.2f} (-{result['reward_pct']:.2f}%)\n"
        f"R:R       : 1 : {result['rr']}\n"
        f"Strategy  : {result['strategy']}\n"
        f"Time      : {result['time_ist']}\n\n"
        f"⚠️ Limit order দেওয়ার কথা মনে আছে?\n"
        f"Result জানাতে: /win {result['trade_id']} বা /loss {result['trade_id']} বা /be {result['trade_id']}"
    )
    await update.message.reply_text(msg)


async def cmd_win(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _mark(update, ctx, "WIN")


async def cmd_loss(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _mark(update, ctx, "LOSS")


async def cmd_be(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _mark(update, ctx, "BE")


async def _mark(update: Update, ctx: ContextTypes.DEFAULT_TYPE, result: str):
    if not ctx.args:
        await update.message.reply_text(f"Usage: /{result.lower()} TRADE_ID   e.g. /{result.lower()} 47")
        return
    try:
        trade_id = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("❌ Trade ID must be a number")
        return

    exit_price = float(ctx.args[1]) if len(ctx.args) >= 2 else None
    r = mark_result(trade_id, result, exit_price)
    if "error" in r:
        await update.message.reply_text(f"❌ {r['error']}")
        return

    emoji = {"WIN": "✅", "LOSS": "❌", "BE": "↔️"}[result]
    msg = (
        f"{emoji} TRADE #{r['trade_id']} — {result}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{r['pair']} {r['direction']} ${r['entry']:,.2f} → ${r['exit']:,.2f}\n"
        f"P&L   : {r['pnl_pct']:+.2f}%\n"
        f"Hold  : {r['hold_minutes']} minutes\n"
        f"Strategy: {r['strategy']} {emoji if result != 'LOSS' else '❌'}\n"
    )

    # Today's summary
    bot = ctx.application.bot_data["bot"]
    wins = bot.today_result_count("WIN")
    losses = bot.today_result_count("LOSS")
    total = wins + losses
    wr = (wins / total * 100) if total > 0 else 0
    msg += f"\nআজকের score: {wins}W / {losses}L ({wr:.0f}% win rate)\n"
    if result == "LOSS":
        msg += f"\n/why {r['trade_id']} লেখো — কেন loss হলো analysis দেখতে"
    elif result == "WIN":
        msg += f"\nBot শিখছে — এই trade postmortem-এ যোগ হবে।"
    await update.message.reply_text(msg)


async def cmd_open(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    trades = list_open_trades()
    if not trades:
        await update.message.reply_text("📋 No open trades right now.\n/log দিয়ে নতুন trade যোগ করো")
        return
    # Fetch live prices
    bot = ctx.application.bot_data["bot"]
    lines = ["📋 OPEN TRADES", "━━━━━━━━━━━━━━━━━━━━━"]
    combined_unrealized = 0.0
    for t in trades:
        pair = resolve_pair(t["pair"])
        if pair:
            ticker = await market_feed.get_ticker(pair)
            live = ticker.get("price", t["entry_price"])
        else:
            live = t["entry_price"]
        if t["direction"] == "LONG":
            pnl_pct = ((live - t["entry_price"]) / t["entry_price"]) * 100
        else:
            pnl_pct = ((t["entry_price"] - live) / t["entry_price"]) * 100
        pnl_pct -= 0.05   # fees
        combined_unrealized += pnl_pct * 100   # just a sum of %s as a rough indicator
        direction_emoji = "🟢" if pnl_pct >= 0 else "🔴"
        lines.append(
            f"#{t['id']} {t['pair']} {t['direction']} ${t['entry_price']:,.2f}\n"
            f"    Stop: ${t['stop_price']:,.2f} | Target: ${t['target_price']:,.2f}\n"
            f"    Current: ${live:,.2f} ({pnl_pct:+.2f}%) {direction_emoji}"
        )
    lines.append(f"\n{len(trades)} open trades | Combined unrealized: {combined_unrealized/100:+.2f}%")
    await update.message.reply_text("\n".join(lines))


async def cmd_close(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Usage: /close 47 3815"""
    if len(ctx.args) < 2:
        await update.message.reply_text("Usage: /close TRADE_ID EXIT_PRICE   e.g. /close 47 3815")
        return
    try:
        trade_id = int(ctx.args[0])
        exit_price = float(ctx.args[1])
    except ValueError:
        await update.message.reply_text("❌ Trade ID must be int, exit price must be number")
        return
    r = close_trade(trade_id, exit_price)
    if "error" in r:
        await update.message.reply_text(f"❌ {r['error']}")
        return
    msg = (
        f"📌 TRADE #{r['trade_id']} CLOSED\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Entry : ${r['entry']:,.2f} | Exit: ${r['exit']:,.2f}\n"
        f"P&L   : {r['pnl_pct']:+.2f}%\n"
        f"Hold  : {r['hold_minutes']} minutes\n"
        f"Result: {r['result']}\n"
    )
    if r["result"] == "PARTIAL":
        msg += f"\nTarget ছিল ${r['target']:,.2f} — partial exit noted"
    msg += f"\n/result {r['trade_id']} WIN দিয়ে log করো (যদি profit হয়)"
    await update.message.reply_text(msg)
