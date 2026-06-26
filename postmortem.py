"""
KAVACH-09 — Postmortem Engine
==============================
Surgical analysis of losing trades.
- /postmortem 47   → single trade deep-dive
- /postmortem week → weekly loss patterns + AI summary
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import database as db
from config import IST_OFFSET_HOURS
from ai_engine import weekly_postmortem_summary

log = logging.getLogger("kavach.postmortem")


async def postmortem_single(trade_id: int) -> dict[str, Any]:
    """Analyse one trade in detail."""
    trade = db.get_trade(trade_id)
    if not trade:
        return {"error": f"Trade #{trade_id} not found"}

    rule_breaks = db.get_rule_breaks(trade_id)

    # Get the original signal (if linked)
    signal = None
    if trade.get("signal_id"):
        # We don't have a direct get_signal function; query inline
        import sqlite3
        from config import DB_PATH
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM signals WHERE id=?", (trade["signal_id"],)).fetchone()
            signal = dict(row) if row else None

    # Compute hold time
    try:
        ts = datetime.fromisoformat(trade["timestamp"])
        now_ist = datetime.utcnow() + timedelta(hours=IST_OFFSET_HOURS)
        hold_min = (now_ist - ts).total_seconds() / 60
    except Exception:
        hold_min = trade.get("hold_minutes", 0) or 0

    # Stop tightness analysis
    entry = trade["entry_price"]
    stop  = trade["stop_price"]
    target = trade["target_price"]
    stop_distance = abs(entry - stop)
    stop_pct = (stop_distance / entry) * 100 if entry > 0 else 0

    # Verdict
    reasons: list[str] = []
    if stop_pct < 0.30:
        reasons.append(f"Stop too tight ({stop_pct:.3f}%) — likely below 1.5×ATR")
    if hold_min > 60 and trade.get("result") == "LOSS":
        reasons.append(f"Held too long ({hold_min:.0f} min) — momentum shifted")
    if trade.get("strategy") == "CVD_DIVERGENCE" and trade["direction"] == "SHORT":
        reasons.append("CVD short — funding rate may have been elevated at entry (check signal log)")
    if not reasons:
        reasons.append("No obvious rule break — market may have moved against setup")

    # Mistake score
    mistake_score = min(10, len(reasons) * 3 + len(rule_breaks) * 2)

    return {
        "trade":           trade,
        "signal":          signal,
        "rule_breaks":     rule_breaks,
        "hold_minutes":    round(hold_min, 1),
        "stop_pct":        round(stop_pct, 3),
        "reasons":         reasons,
        "mistake_score":   mistake_score,
    }


async def postmortem_week() -> dict[str, Any]:
    """Aggregate postmortem of last 7 days' losses + AI summary."""
    week_ago = datetime.utcnow() + timedelta(hours=IST_OFFSET_HOURS - 24 * 7)
    all_trades = db.get_trades_since(hours=24 * 7)
    losses = [t for t in all_trades if t.get("result") in ("LOSS", "PARTIAL")]
    rule_breaks = db.get_rule_breaks()

    # Aggregate rule-break patterns
    rule_counter: Counter = Counter(rb["rule"] for rb in rule_breaks)
    top_rules = rule_counter.most_common(5)

    # Aggregate by strategy
    strat_stats: dict[str, dict] = defaultdict(lambda: {"n": 0, "loss": 0, "win": 0, "pnl": 0.0})
    for t in all_trades:
        s = t.get("strategy", "UNKNOWN")
        strat_stats[s]["n"] += 1
        if t.get("result") == "WIN":
            strat_stats[s]["win"] += 1
        elif t.get("result") in ("LOSS", "PARTIAL"):
            strat_stats[s]["loss"] += 1
            strat_stats[s]["pnl"] += float(t.get("pnl_pct") or 0)

    # Total loss this week
    total_loss = sum(float(t.get("pnl_pct") or 0) for t in losses)

    # AI summary (best-effort)
    ai_summary = ""
    try:
        ai_summary = await weekly_postmortem_summary(losses, rule_breaks)
    except Exception as e:
        log.warning(f"AI weekly summary failed: {e}")
        ai_summary = "(AI summary unavailable — check GROQ_API_KEY)"

    return {
        "losses":          losses,
        "rule_breaks":     rule_breaks,
        "top_rules":       top_rules,
        "strategy_stats":  dict(strat_stats),
        "total_loss_pct":  round(total_loss, 3),
        "ai_summary":      ai_summary,
    }


def format_postmortem_single(pm: dict) -> str:
    if "error" in pm:
        return f"❌ {pm['error']}"
    t = pm["trade"]
    sym_emoji = "🔴" if t["direction"] == "SHORT" else "🟢"
    return (
        f"🔬 POSTMORTEM — TRADE #{t['id']}\n"
        f"{t['pair']} {sym_emoji} {t['direction']} | {t.get('result', '?')} "
        f"({t.get('pnl_pct', 0):+.2f}%)\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📋 WHAT HAPPENED\n"
        f"Entry: ${t['entry_price']:.2f}  →  Exit: ${t.get('exit_price', 0):.2f}\n"
        f"Held: {pm['hold_minutes']:.0f} min\n"
        f"Stop distance: {pm['stop_pct']:.3f}%\n\n"
        f"🔍 REASONS\n"
        + "\n".join(f"• {r}" for r in pm["reasons"])
        + f"\n\n🚨 RULE BREAKS LOGGED: {len(pm['rule_breaks'])}\n"
        + ("\n".join(f"  • {rb['rule']}: {rb['description']}" for rb in pm["rule_breaks"]) if pm["rule_breaks"] else "  (none)")
        + f"\n\n⭐ Mistake score: {pm['mistake_score']}/10"
        + (f"\n\n🎯 LESSON\n" + pm.get("ai_summary", "") if pm.get("ai_summary") else "")
    )


def format_postmortem_week(pm: dict) -> str:
    losses = pm["losses"]
    return (
        f"🔬 WEEKLY POSTMORTEM\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Losses analysed: {len(losses)}\n"
        f"Total loss (this week): {pm['total_loss_pct']:.2f}%\n\n"
        f"TOP RULE BREAKS:\n"
        + ("\n".join(f"  {i+1}. {rule} × {n}" for i, (rule, n) in enumerate(pm["top_rules"]))
           if pm["top_rules"] else "  (none logged)")
        + f"\n\nSTRATEGY STATS:\n"
        + ("\n".join(
            f"  {s}: {d['n']} trades, {d['win']}W/{d['loss']}L, net {d['pnl']:+.2f}%"
            for s, d in pm["strategy_stats"].items()
        ) if pm["strategy_stats"] else "  (no trades yet)")
        + f"\n\n🤖 AI SUMMARY:\n{pm['ai_summary']}"
    )
