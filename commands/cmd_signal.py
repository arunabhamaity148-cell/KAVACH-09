"""
KAVACH-09 — Signal Commands
============================
/scan   /signal ETH   /verify ETH SHORT
"""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from alert_manager import format_signal_alert
from config import resolve_pair
from verifier import verify_manual_idea


async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    bot = ctx.application.bot_data["bot"]
    await update.message.reply_text("🔍 Manual scan চলছে... (5 pairs × 5 strategies)")
    signals = await bot.engine.scan_once()
    if not signals:
        await update.message.reply_text(
            "🟢 No signals right now.\n"
            "All 5 pairs scanned, no setup meets minimum score (60).\n"
            "Next auto-scan in 30s, or /scan আবার।"
        )
        return
    # Show top results (max 3)
    lines = [f"✅ {len(signals)}টা signal পাওয়া গেছে:\n"]
    for i, r in enumerate(signals[:3], 1):
        direction_emoji = "🟢 LONG" if r.direction == "LONG" else "🔴 SHORT"
        confidence_emoji = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🟠"}.get(r.confidence, "🔴")
        lines.append(
            f"{'①②③'[i-1]} {r.pair} — {r.strategy} {direction_emoji}\n"
            f"   Score: {r.score}/100 | Confidence: {confidence_emoji} {r.confidence}\n"
        )
    lines.append(
        f"/signal {signals[0].pair.replace('-USDT','')} লেখো full detail দেখতে"
    )
    await update.message.reply_text("\n".join(lines))


async def cmd_signal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /signal ETH   (or BTC, SOL, BNB, XRP)")
        return
    pair = resolve_pair(ctx.args[0])
    if not pair:
        await update.message.reply_text(f"❌ Unknown pair: {ctx.args[0]}")
        return

    bot = ctx.application.bot_data["bot"]
    r = bot.engine.latest_for(pair.symbol)
    if not r:
        # Try a fresh scan for this pair
        signals = await bot.engine.scan_once(only_pair=pair.symbol)
        r = signals[0] if signals else None
    if not r:
        await update.message.reply_text(
            f"🟢 {pair.symbol} - এই মুহূর্তে কোনো signal নেই।\n"
            f"Auto-scan প্রতি 30s-এ চলছে, অথবা /scan দিয়ে এখনই চালাও।"
        )
        return

    signal_id = r.extra.get("signal_id")
    await update.message.reply_text(format_signal_alert(r, signal_id))


async def cmd_verify(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Usage: /verify ETH SHORT"""
    if len(ctx.args) < 2:
        await update.message.reply_text("Usage: /verify ETH SHORT   (or LONG)")
        return
    pair_str = ctx.args[0]
    direction = ctx.args[1].upper()
    if direction not in ("LONG", "SHORT"):
        await update.message.reply_text("Direction must be LONG or SHORT")
        return

    await update.message.reply_text(f"🔍 MANUAL VERIFY — {pair_str.upper()} {direction}\n━━━━━━━━━━━━━━━━━━━━━\nChecking conditions...")

    result = await verify_manual_idea(pair_str, direction)
    if "error" in result:
        await update.message.reply_text(f"❌ {result['error']}")
        return

    # Build response
    cond_lines = []
    label_map = {
        "price_higher_high":   "Price higher high",
        "cvd_lower_high":      "CVD lower high",
        "volume_declining":    "Volume declining",
        "vwap_extended":       "VWAP extended",
        "funding_neutral":     "Funding neutral",
    }
    for k, v in result["conditions"].items():
        mark = "✅" if v["met"] else "❌"
        label = label_map.get(k, k.replace("_", " ").title())
        cond_lines.append(f"{mark} {label:<22} : {v['detail']}")

    msg = (
        f"🔍 MANUAL VERIFY — {result['pair']} {result['direction']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Current price: ${result['current_price']:,.2f}\n"
        f"VWAP         : ${result['vwap']:,.2f}\n"
        f"ATR-14 (5m)  : ${result['atr']:.2f}\n\n"
        + "\n".join(cond_lines)
        + f"\n\nVERDICT: {result['verdict']}\n"
    )
    if result.get("suggestion"):
        msg += f"{result['suggestion']}\n"
    msg += (
        f"\nSuggested entry: ${result['entry']:,.2f} (limit)\n"
        f"Stop          : ${result['stop']:,.2f}\n"
        f"Target        : ${result['target']:,.2f}\n"
        f"R:R           : 1 : {result['rr']}\n"
    )
    await update.message.reply_text(msg)
