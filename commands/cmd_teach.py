"""
KAVACH-09 — Teaching Commands
==============================
/teach cvd   /teach vwap   /teach funding   /teach cascade   /teach risk
/explain why ETH SHORT failed   /why 47
/quiz cvd   /answer B
"""
from __future__ import annotations

import logging
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

import database as db
from teacher import (
    LESSONS, QUIZZES, get_lesson, start_quiz, answer_quiz,
    format_question, format_answer,
)
from postmortem import postmortem_single
from ai_engine import explain, why_trade_lost

log = logging.getLogger("kavach.teach_cmd")


async def cmd_teach(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        topics = " | ".join(f"/teach {t}" for t in LESSONS.keys())
        await update.message.reply_text(
            f"📚 Available lessons:\n{topics}\n\nExample: /teach cvd"
        )
        return
    topic = ctx.args[0].lower()
    lesson = get_lesson(topic)
    if not lesson:
        await update.message.reply_text(
            f"❌ Unknown topic: {topic}\nAvailable: {', '.join(LESSONS.keys())}"
        )
        return
    await update.message.reply_text(lesson)


async def cmd_quiz(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        topics = " | ".join(f"/quiz {t}" for t in QUIZZES.keys())
        await update.message.reply_text(
            f"🧠 Available quizzes:\n{topics}\n\nExample: /quiz cvd"
        )
        return
    topic = ctx.args[0].lower()
    chat_id = update.effective_chat.id
    q = start_quiz(chat_id, topic)
    if q is None:
        await update.message.reply_text(
            f"❌ No quiz for topic: {topic}\nAvailable: {', '.join(QUIZZES.keys())}"
        )
        return
    q["topic"] = topic
    await update.message.reply_text(format_question(q))


async def cmd_answer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /answer A   (or B, C)")
        return
    chat_id = update.effective_chat.id
    result = answer_quiz(chat_id, ctx.args[0])
    if result is None:
        await update.message.reply_text(
            "❌ No active quiz. /quiz <topic> দিয়ে শুরু করো।"
        )
        return
    await update.message.reply_text(format_answer(result))


async def cmd_explain(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Natural-language question. /explain why ETH SHORT failed"""
    if not ctx.args:
        await update.message.reply_text(
            "Usage: /explain <question>\n\n"
            "Examples:\n"
            "  /explain why ETH SHORT failed\n"
            "  /explain what is CVD divergence\n"
            "  /explain should I trade funding > 0.05%\n"
            "  /explain best time to use VWAP reclaim"
        )
        return
    question = " ".join(ctx.args)

    # Build context — recent trades + signals
    context: dict[str, Any] = {}
    try:
        context["recent_trades"] = db.get_trades_since(hours=48)[:10]
        context["recent_signals"] = db.get_signals_since(None, hours=24)[:10]
        # If question mentions a pair, focus on that
        q_upper = question.upper()
        for alias, pair_name in [("BTC", "BTC-USDT"), ("ETH", "ETH-USDT"),
                                   ("SOL", "SOL-USDT"), ("BNB", "BNB-USDT"),
                                   ("XRP", "XRP-USDT")]:
            if alias in q_upper:
                context["pair_focus"] = pair_name
                context["pair_trades"] = [t for t in context["recent_trades"]
                                          if t.get("pair") == pair_name]
                break
    except Exception as e:
        log.warning(f"Context build failed: {e}")

    await update.message.reply_text(f"🤔 তুমি জিজ্ঞেস করেছো: \"{question}\"\n\nBot analyze করছে... (AI)")

    try:
        answer = await explain(question, context)
    except Exception as e:
        log.error(f"AI explain failed: {e}")
        answer = f"⚠️ AI এখন unavailable: {e}\n\nGROQ_API_KEY সেট আছে কিনা .env file এ চেক করো।"

    await update.message.reply_text(answer)


async def cmd_why(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Why did a specific trade lose? /why 47"""
    if not ctx.args:
        await update.message.reply_text("Usage: /why TRADE_ID   e.g. /why 47")
        return
    try:
        trade_id = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("❌ Trade ID must be a number")
        return

    trade = db.get_trade(trade_id)
    if not trade:
        await update.message.reply_text(f"❌ Trade #{trade_id} not found")
        return
    if trade.get("result") not in ("LOSS", "PARTIAL"):
        await update.message.reply_text(
            f"ℹ️ Trade #{trade_id} result is {trade.get('result', 'OPEN')} — "
            f"/why শুধু loss/partial trades-এর জন্য।"
        )
        return

    await update.message.reply_text(f"❓ Analyzing trade #{trade_id}...")

    # Find similar past losses (same pair, same strategy)
    all_trades = db.get_all_trades()
    similar = [
        t for t in all_trades
        if t.get("result") in ("LOSS", "PARTIAL")
        and t.get("pair") == trade["pair"]
        and t.get("strategy") == trade.get("strategy")
        and t["id"] != trade_id
    ][:5]

    rule_breaks = db.get_rule_breaks(trade_id)

    try:
        # Use postmortem for deterministic analysis + AI for natural language
        pm = await postmortem_single(trade_id)
        ai_answer = await why_trade_lost(trade, similar)
    except Exception as e:
        log.error(f"AI why_trade_lost failed: {e}")
        ai_answer = "(AI unavailable — check GROQ_API_KEY)"

    # Combine deterministic reasons + AI
    msg = (
        f"❓ WHY TRADE #{trade_id} LOST\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{ai_answer}\n\n"
    )
    if pm.get("reasons"):
        msg += "🔍 DETERMINISTIC ANALYSIS:\n"
        msg += "\n".join(f"• {r}" for r in pm["reasons"])
        msg += "\n"
    if similar:
        msg += f"\n📌 SIMILAR PAST LOSSES: {len(similar)} (same pair+strategy)\n"
        msg += "Pattern matching suggests this is a recurring mistake.\n"
    if rule_breaks:
        msg += f"\n🚨 RULE BREAKS LOGGED: {len(rule_breaks)}\n"
        for rb in rule_breaks:
            msg += f"  • {rb['rule']}: {rb['description']}\n"
    msg += f"\n⭐ Mistake score: {pm.get('mistake_score', '?')}/10"
    msg += f"\n\n/teach {trade.get('strategy', '').lower().split('_')[0] if trade.get('strategy') else 'risk'} লেখো related lesson দেখতে"
    await update.message.reply_text(msg)
