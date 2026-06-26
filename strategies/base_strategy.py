"""
KAVACH-09 — Base Strategy
=========================
All strategies implement evaluate() and return a StrategyResult.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StrategyResult:
    strategy:         str            # display name e.g. "CVD Divergence Scalp"
    strategy_key:     str            # internal key e.g. "CVD_DIVERGENCE"
    pair:             str            # BTC-USDT
    direction:        str            # LONG / SHORT
    entry_price:      float
    stop_price:       float
    target_price:     float
    score:            int            # 0-100
    confidence:       str            # HIGH / MEDIUM / LOW
    conditions:       dict[str, bool]   # each scoring condition's status
    condition_details: dict[str, str] = field(default_factory=dict)  # human-readable details
    warnings:         list[str]      = field(default_factory=list)
    rr:               float = 0.0    # reward:risk ratio
    atr:              float = 0.0
    vwap:             float = 0.0
    extra:            dict[str, Any] = field(default_factory=dict)


class BaseStrategy(ABC):
    """Abstract base — every strategy must implement evaluate()."""

    name:        str = "BASE"
    key:         str = "BASE"
    description: str = ""

    @abstractmethod
    async def evaluate(
        self,
        pair: Any,                      # config.Pair
        candles: list[dict],            # 5m candles, oldest first
        trades:  list[dict],            # recent aggressor trades
        context: dict[str, Any],        # funding, etf, liq, ticker
    ) -> StrategyResult | None:
        """
        Return a StrategyResult if a valid setup is found,
        else None.
        """
        ...

    # ─── helpers shared by all strategies ─────────────────────────
    @staticmethod
    def compute_rr(entry: float, stop: float, target: float) -> float:
        risk   = abs(entry - stop)
        reward = abs(target - entry)
        return round(reward / risk, 2) if risk > 0 else 0.0

    @staticmethod
    def confidence_from_score(score: int) -> str:
        from config import SCORE_HIGH, SCORE_MEDIUM, SCORE_LOW
        if score >= SCORE_HIGH:
            return "HIGH"
        if score >= SCORE_MEDIUM:
            return "MEDIUM"
        if score >= SCORE_LOW:
            return "LOW"
        return "REJECT"
