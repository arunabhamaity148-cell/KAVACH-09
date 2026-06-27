"""
KAVACH-09 — Trade Logger
=========================
Handles /log, /win, /loss, /be, /open, /close commands.
Bridges Telegram input ↔ SQLite trades table.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import database as db
from config import IST_OFFSET_HOURS

log = logging.getLogger("kavach.trades")


def log_trade(pair_str: str, direction: str, entry: float, stop: float, target: float,
              strategy: str | None = None, notes: str = "") -> dict[str, Any]:
    """Create a new OPEN trade record."""
    from config import resolve_pair
    pair = resolve_pair(pair_str)
    if not pair:
        return {"error": f"Unknown pair: {pair_str}"}
    direction = direction.upper()
    if direction not in ("LONG", "SHORT"):
        return {"error": "Direction must be LONG or SHORT"}

    trade_id = db.insert_trade({
        "pair":         pair.symbol,
        "direction":    direction,
        "entry_price":  entry,
        "stop_price":   stop,
        "target_price": target,
        "strategy":     strategy or "MANUAL",
        "notes":        notes,
        "result":       "OPEN",
    })

    risk   = abs(entry - stop)
    reward = abs(target - entry)
    rr     = round(reward / risk, 2) if risk > 0 else 0
    risk_pct = round((risk / entry) * 100, 3) if entry > 0 else 0
    reward_pct = round((reward / entry) * 100, 3) if entry > 0 else 0

    return {
        "trade_id":    trade_id,
        "pair":        pair.symbol,
        "direction":   direction,
        "entry":       entry,
        "stop":        stop,
        "target":      target,
        "rr":          rr,
        "risk_pct":    risk_pct,
        "reward_pct":  reward_pct,
        "strategy":    strategy or "MANUAL",
        "time_ist":    _now_ist_str(),
    }


def mark_result(trade_id: int, result: str, exit_price: float | None = None,
                hold_minutes: int | None = None) -> dict[str, Any]:
    """Mark a trade WIN / LOSS / BE."""
    result = result.upper()
    if result not in ("WIN", "LOSS", "BE"):
        return {"error": "Result must be WIN, LOSS, or BE"}
    trade = db.get_trade(trade_id)
    if not trade:
        return {"error": f"Trade #{trade_id} not found"}

    if exit_price is None:
        # If not provided, use target (WIN), stop (LOSS), or entry (BE)
        exit_price = {
            "WIN":  trade["target_price"],
            "LOSS": trade["stop_price"],
            "BE":   trade["entry_price"],
        }[result]

    entry = trade["entry_price"]
    direction = trade["direction"]
    leverage = trade.get("leverage", 5)   # BUG-16 fix: default 5x
    if direction == "LONG":
        raw_pct = ((exit_price - entry) / entry) * 100
    else:
        raw_pct = ((entry - exit_price) / entry) * 100
    # BUG-16 fix: apply leverage, subtract fees (0.05% × 2 sides × leverage)
    pnl_pct = raw_pct * leverage - 0.05 * leverage

    if hold_minutes is None:
        # Estimate from timestamp
        try:
            ts = datetime.fromisoformat(trade["timestamp"])
            now_ist = datetime.utcnow() + timedelta(hours=IST_OFFSET_HOURS)
            hold_minutes = max(1, int((now_ist - ts).total_seconds() / 60))
        except Exception:
            hold_minutes = 0

    db.update_trade_result(trade_id, result, exit_price, round(pnl_pct, 3), hold_minutes)

    # Rule-break detection
    _detect_rule_breaks(trade_id, trade, exit_price, result)

    return {
        "trade_id":     trade_id,
        "pair":         trade["pair"],
        "direction":    direction,
        "entry":        entry,
        "exit":         exit_price,
        "result":       result,
        "pnl_pct":      round(pnl_pct, 3),
        "hold_minutes": hold_minutes,
        "strategy":     trade["strategy"],
    }


def close_trade(trade_id: int, exit_price: float) -> dict[str, Any]:
    """Manually close a trade at an arbitrary price."""
    trade = db.get_trade(trade_id)
    if not trade:
        return {"error": f"Trade #{trade_id} not found"}
    entry = trade["entry_price"]
    direction = trade["direction"]
    leverage = trade.get("leverage", 5)   # BUG-16 fix
    if direction == "LONG":
        raw_pct = ((exit_price - entry) / entry) * 100
    else:
        raw_pct = ((entry - exit_price) / entry) * 100
    pnl_pct = raw_pct * leverage - 0.05 * leverage

    # Determine result based on exit vs target/stop
    if direction == "LONG":
        if exit_price >= trade["target_price"]:
            result = "WIN"
        elif exit_price <= trade["stop_price"]:
            result = "LOSS"
        else:
            result = "PARTIAL"
    else:
        if exit_price <= trade["target_price"]:
            result = "WIN"
        elif exit_price >= trade["stop_price"]:
            result = "LOSS"
        else:
            result = "PARTIAL"

    try:
        ts = datetime.fromisoformat(trade["timestamp"])
        now_ist = datetime.utcnow() + timedelta(hours=IST_OFFSET_HOURS)
        hold_minutes = max(1, int((now_ist - ts).total_seconds() / 60))
    except Exception:
        hold_minutes = 0

    db.update_trade_result(trade_id, result, exit_price, round(pnl_pct, 3), hold_minutes)
    _detect_rule_breaks(trade_id, trade, exit_price, result)

    return {
        "trade_id":     trade_id,
        "pair":         trade["pair"],
        "direction":    direction,
        "entry":        entry,
        "exit":         exit_price,
        "result":       result,
        "pnl_pct":      round(pnl_pct, 3),
        "hold_minutes": hold_minutes,
        "strategy":     trade["strategy"],
        "target":       trade["target_price"],
    }


def list_open_trades() -> list[dict]:
    """Return all open trades with current P&L placeholder (live price fetched by caller)."""
    return db.get_open_trades()


# ────────────────────────────────────────────────────────────────────
# RULE BREAK DETECTION
# ────────────────────────────────────────────────────────────────────

def _detect_rule_breaks(trade_id: int, trade: dict, exit_price: float, result: str) -> None:
    """Inspect a closed trade for rule violations; log them."""
    if result != "LOSS":
        return
    entry  = trade["entry_price"]
    stop   = trade["stop_price"]
    direction = trade["direction"]
    strategy  = trade["strategy"]

    # Rule 1: Stop too tight (< 1.0 × ATR — would need ATR, but we approximate)
    stop_distance = abs(entry - stop)
    stop_pct = (stop_distance / entry) * 100 if entry > 0 else 0
    if stop_pct < 0.15:
        db.insert_rule_break(
            trade_id,
            "STOP_TOO_TIGHT",
            f"Stop distance {stop_pct:.3f}% — likely below 1.5×ATR"
        )

    # Rule 2: Counter-trend CVD short with funding elevated (strategy-specific)
    if strategy == "CVD_DIVERGENCE" and direction == "SHORT":
        # We don't have historical funding here; mark as candidate
        db.insert_rule_break(
            trade_id,
            "CVD_SHORT_FUNDING_CHECK",
            "CVD short — was funding > 0.04% at entry? (check signal log)"
        )

    # Rule 3: Held too long (max 45 min for scalp)
    try:
        ts = datetime.fromisoformat(trade["timestamp"])
        now_ist = datetime.utcnow() + timedelta(hours=IST_OFFSET_HOURS)
        hold_min = (now_ist - ts).total_seconds() / 60
        if hold_min > 60:
            db.insert_rule_break(
                trade_id,
                "HELD_TOO_LONG",
                f"Held {hold_min:.0f} min — scalp should be ≤45 min"
            )
    except Exception:
        pass


def _now_ist_str() -> str:
    now = datetime.utcnow() + timedelta(hours=IST_OFFSET_HOURS)
    return now.strftime("%I:%M %p IST")
