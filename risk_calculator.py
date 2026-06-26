"""
KAVACH-09 — Risk Calculator
============================
Position sizing, R:R, ATR-based stop placement.

Formula:
    risk_amount = account × risk_pct
    stop_distance = 1.5 × ATR14
    stop_pct = stop_distance / entry_price
    notional = risk_amount / stop_pct
    margin = notional / leverage
"""
from __future__ import annotations

from dataclasses import dataclass

from config import (
    ATR_STOP_MULTIPLIER, DEFAULT_LEVERAGE, DEFAULT_RISK_PCT,
    MAX_OPEN_POSITIONS, MAX_RISK_PCT, RR_MIN,
)


@dataclass
class PositionPlan:
    risk_amount:    float
    stop_distance:  float
    stop_pct:       float
    notional:       float
    margin:         float
    contracts:      float    # notional / entry
    rr:             float
    leverage:       int
    risk_pct:       float
    verdict:        str      # 'ok' | 'too_much_risk' | 'rr_too_low' | 'max_open_exceeded'


def calculate_position_size(
    account_balance: float,
    entry_price:     float,
    atr14:           float,
    direction:       str,
    stop_price:      float | None = None,
    target_price:    float | None = None,
    risk_pct:        float = DEFAULT_RISK_PCT,
    leverage:        int   = DEFAULT_LEVERAGE,
    open_positions:  int   = 0,
) -> PositionPlan:
    """
    Returns a fully-formed position plan including a verdict.
    Verdicts:
      'ok'                  → take the trade
      'too_much_risk'       → risk_pct > MAX_RISK_PCT
      'rr_too_low'          → R:R < RR_MIN
      'max_open_exceeded'   → already at MAX_OPEN_POSITIONS
    """
    risk_pct = min(risk_pct, MAX_RISK_PCT)
    risk_amount = account_balance * risk_pct

    # Stop from ATR if not provided
    if stop_price:
        stop_distance = abs(entry_price - stop_price)
    else:
        stop_distance = atr14 * ATR_STOP_MULTIPLIER
        stop_price = (
            entry_price - stop_distance if direction == "LONG"
            else entry_price + stop_distance
        )

    if entry_price == 0:
        return PositionPlan(
            risk_amount=0, stop_distance=0, stop_pct=0, notional=0,
            margin=0, contracts=0, rr=0, leverage=leverage,
            risk_pct=risk_pct, verdict="too_much_risk",
        )

    stop_pct = stop_distance / entry_price
    notional = risk_amount / stop_pct if stop_pct > 0 else 0
    margin   = notional / leverage if leverage > 0 else notional
    contracts = notional / entry_price if entry_price > 0 else 0

    # R:R
    if target_price:
        risk   = abs(entry_price - stop_price)
        reward = abs(target_price - entry_price)
        rr = round(reward / risk, 2) if risk > 0 else 0.0
    else:
        rr = 0.0

    # Verdict
    if open_positions >= MAX_OPEN_POSITIONS:
        verdict = "max_open_exceeded"
    elif risk_pct > MAX_RISK_PCT:
        verdict = "too_much_risk"
    elif rr and rr < RR_MIN:
        verdict = "rr_too_low"
    else:
        verdict = "ok"

    return PositionPlan(
        risk_amount   = round(risk_amount, 2),
        stop_distance = round(stop_distance, 2),
        stop_pct      = round(stop_pct * 100, 3),     # as % value
        notional      = round(notional, 2),
        margin        = round(margin, 2),
        contracts     = round(contracts, 4),
        rr            = rr,
        leverage      = leverage,
        risk_pct      = risk_pct,
        verdict       = verdict,
    )


def format_position_plan(p: PositionPlan, pair_symbol: str, direction: str) -> str:
    """Telegram-friendly string."""
    verdict_emoji = {
        "ok":                  "✅",
        "too_much_risk":       "🛑",
        "rr_too_low":          "⚠️",
        "max_open_exceeded":   "🛑",
    }[p.verdict]
    return (
        f"📐 POSITION PLAN — {pair_symbol} {direction}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Risk amount   : ${p.risk_amount:.2f}  ({p.risk_pct*100:.2f}% of account)\n"
        f"Stop distance : ${p.stop_distance:.2f}  ({p.stop_pct:.3f}%)\n"
        f"Notional size : ${p.notional:.2f}\n"
        f"Margin needed : ${p.margin:.2f}  ({p.leverage}x leverage)\n"
        f"Contracts     : {p.contracts}\n"
        f"R:R           : 1 : {p.rr}\n"
        f"Verdict       : {verdict_emoji} {p.verdict}"
    )
