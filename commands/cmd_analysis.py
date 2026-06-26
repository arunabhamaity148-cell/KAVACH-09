"""
KAVACH-09 — Analysis Commands
==============================
/analysis today   /analysis week   /stats
/postmortem 47    /postmortem week
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import ContextTypes

import database as db
from config import IST_OFFSET_HOURS
from postmortem import postmortem_single, postmortem_week, format_postmortem_single, format_postmortem_week


async def cmd_analysis(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or ctx.args[0].lower() not in ("today", "week"):
        await update.message.reply_text("Usage: /analysis today   OR   /analysis week")
        return
    period = ctx.args[0].lower()
    hours = 24 if period == "today" else 24 * 7
    trades = db.get_trades_since(hours)
    if not trades:
        await update.message.reply_text(
            f"📊 No trades logged in last {'24 hours' if period == 'today' else '7 days'}.\n"
            f"/log দিয়ে কিছু trade যোগ করো analysis দেখতে।"
        )
        return

    closed = [t for t in trades if t.get("result") in ("WIN", "LOSS", "BE", "PARTIAL")]
    wins   = [t for t in closed if t["result"] == "WIN"]
    losses = [t for t in closed if t["result"] in ("LOSS", "PARTIAL")]
    bes    = [t for t in closed if t["result"] == "BE"]
    total = len(closed)
    wr = (len(wins) / total * 100) if total > 0 else 0

    avg_win  = sum(t.get("pnl_pct", 0) for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t.get("pnl_pct", 0) for t in losses) / len(losses) if losses else 0
    expectancy = ((len(wins)/total * avg_win) + (len(losses)/total * avg_loss)) if total > 0 else 0

    # Best & worst
    best = max(closed, key=lambda t: t.get("pnl_pct", -999)) if closed else None
    worst = min(closed, key=lambda t: t.get("pnl_pct", 999)) if closed else None

    # Strategy breakdown
    strat_stats = defaultdict(lambda: {"n": 0, "w": 0, "l": 0})
    for t in closed:
        s = t.get("strategy", "MANUAL")
        strat_stats[s]["n"] += 1
        if t["result"] == "WIN":
            strat_stats[s]["w"] += 1
        elif t["result"] in ("LOSS", "PARTIAL"):
            strat_stats[s]["l"] += 1

    # Signals received in window
    sigs = db.get_signals_since(None, hours)
    follow_rate = (total / len(sigs) * 100) if sigs else 0

    title = "📊 DAILY ANALYSIS" if period == "today" else "📊 WEEKLY ANALYSIS"
    date_str = (datetime.utcnow() + timedelta(hours=IST_OFFSET_HOURS)).strftime("%d %b %Y")
    title += f" — {date_str}"

    lines = [title, "━━━━━━━━━━━━━━━━━━━━━", ""]
    lines.append("🎯 PERFORMANCE")
    lines.append(f"Signals received : {len(sigs)}")
    lines.append(f"Trades taken     : {total} ({follow_rate:.0f}% signal follow rate)")
    lines.append(f"Win / Loss / BE  : {len(wins)} / {len(losses)} / {len(bes)}")
    lines.append(f"Win rate         : {wr:.1f}%")
    lines.append(f"Avg win          : {avg_win:+.2f}%")
    lines.append(f"Avg loss         : {avg_loss:+.2f}%")
    lines.append(f"Expectancy       : {expectancy:+.3f}%")

    if best:
        lines.append("")
        lines.append("📈 BEST TRADE")
        lines.append(f"#{best['id']} {best['pair']} {best['direction']} | {best.get('pnl_pct', 0):+.2f}%")
        lines.append(f"Strategy: {best.get('strategy', 'MANUAL')}")

    if worst:
        lines.append("")
        lines.append("📉 WORST TRADE")
        lines.append(f"#{worst['id']} {worst['pair']} {worst['direction']} | {worst.get('pnl_pct', 0):+.2f}%")
        lines.append(f"Strategy: {worst.get('strategy', 'MANUAL')}")

    lines.append("")
    lines.append("🔍 STRATEGY BREAKDOWN")
    for s, d in strat_stats.items():
        wr_s = (d["w"] / d["n"] * 100) if d["n"] > 0 else 0
        lines.append(f"{s:<20}: {d['n']} trades | {d['w']}W {d['l']}L | {wr_s:.0f}%")

    # Pattern detection (simple)
    rule_breaks = db.get_rule_breaks()
    if rule_breaks and period == "week":
        from collections import Counter
        rc = Counter(rb["rule"] for rb in rule_breaks)
        top_rule, top_count = rc.most_common(1)[0]
        lines.append("")
        lines.append("⚠️ PATTERN DETECTED")
        lines.append(f"Most common rule break: {top_rule} ({top_count} times)")
        lines.append("Recommendation: /postmortem week দিয়ে deep analysis করো")

    await update.message.reply_text("\n".join(lines))


async def cmd_postmortem(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /postmortem TRADE_ID   OR   /postmortem week")
        return
    arg = ctx.args[0].lower()
    if arg == "week":
        await update.message.reply_text("🔬 Weekly postmortem চলছে... (AI সহ)")
        pm = await postmortem_week()
        await update.message.reply_text(format_postmortem_week(pm))
        return
    try:
        trade_id = int(arg)
    except ValueError:
        await update.message.reply_text("Usage: /postmortem TRADE_ID   OR   /postmortem week")
        return
    await update.message.reply_text(f"🔬 Postmortem চলছে trade #{trade_id}...")
    pm = await postmortem_single(trade_id)
    await update.message.reply_text(format_postmortem_single(pm))


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Lifetime stats."""
    all_trades = db.get_all_trades()
    if not all_trades:
        await update.message.reply_text("📊 No trades logged yet.\n/log দিয়ে প্রথম trade যোগ করো")
        return

    closed = [t for t in all_trades if t.get("result") in ("WIN", "LOSS", "BE", "PARTIAL")]
    wins   = [t for t in closed if t["result"] == "WIN"]
    losses = [t for t in closed if t["result"] in ("LOSS", "PARTIAL")]
    bes    = [t for t in closed if t["result"] == "BE"]
    wr = (len(wins) / len(closed) * 100) if closed else 0

    # Streaks
    cur_streak = 0
    best_streak = 0
    worst_streak = 0
    cur_sign = 0
    for t in closed[::-1]:   # oldest to newest
        if t["result"] == "WIN":
            sign = 1
        elif t["result"] in ("LOSS", "PARTIAL"):
            sign = -1
        else:
            sign = 0
        if sign == cur_sign and sign != 0:
            cur_streak += sign
        else:
            cur_sign = sign
            cur_streak = sign
        if cur_streak > best_streak:
            best_streak = cur_streak
        if cur_streak < worst_streak:
            worst_streak = cur_streak

    # P&L summary
    total_pnl = sum(t.get("pnl_pct", 0) for t in closed)
    if closed:
        first_ts = closed[0].get("timestamp", "")[:10]
        best_day_trade = max(closed, key=lambda t: t.get("pnl_pct", -999))
        worst_day_trade = min(closed, key=lambda t: t.get("pnl_pct", 999))

    # Quiz stats
    quiz = db.get_quiz_stats()

    # Strategy lifetime
    strat_stats = defaultdict(lambda: {"n": 0, "w": 0, "l": 0})
    for t in closed:
        s = t.get("strategy", "MANUAL")
        strat_stats[s]["n"] += 1
        if t["result"] == "WIN":
            strat_stats[s]["w"] += 1
        elif t["result"] in ("LOSS", "PARTIAL"):
            strat_stats[s]["l"] += 1

    rule_breaks = db.get_rule_breaks()

    lines = [
        "📊 YOUR KAVACH-09 STATS",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"📅 Trading since: {first_ts if closed else 'no trades yet'}",
        f"📝 Total trades  : {len(closed)}",
        f"✅ Win rate      : {wr:.1f}% ({len(wins)}W/{len(losses)}L/{len(bes)}BE)",
        f"📈 Best streak   : {best_streak} consecutive wins",
        f"📉 Worst streak  : {worst_streak} consecutive losses",
        "",
        "💰 P&L SUMMARY",
        f"Total return   : {total_pnl:+.2f}% (cumulative)",
        f"Best trade     : {best_day_trade.get('pnl_pct', 0):+.2f}% (#{best_day_trade['id']})" if closed else "",
        f"Worst trade    : {worst_day_trade.get('pnl_pct', 0):+.2f}% (#{worst_day_trade['id']})" if closed else "",
        "",
        "🎓 LEARNING PROGRESS",
        f"Quizzes taken  : {quiz['total']}",
        f"Quizzes passed : {quiz['correct']}",
        f"Rules broken   : {len(rule_breaks)} times",
        "",
        "📊 STRATEGY PERFORMANCE (lifetime)",
    ]
    for s, d in sorted(strat_stats.items(), key=lambda x: -x[1]["n"]):
        wr_s = (d["w"] / d["n"] * 100) if d["n"] > 0 else 0
        lines.append(f"{s:<20}: {wr_s:.0f}% WR ({d['w']}W/{d['l']}L of {d['n']})")
    await update.message.reply_text("\n".join(lines))
