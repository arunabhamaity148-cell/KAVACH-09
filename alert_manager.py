"""
KAVACH-09 — Alert Manager
==========================
Formats signals into Telegram messages + sends them.
Tracks alert cooldowns to prevent spam.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from config import BOT_NAME, IST_OFFSET_HOURS, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from strategies.base_strategy import StrategyResult

log = logging.getLogger("kavach.alert")


# ────────────────────────────────────────────────────────────────────
# FORMATTERS
# ────────────────────────────────────────────────────────────────────

def _now_ist_str() -> str:
    now = datetime.now(timezone.utc) + timedelta(hours=IST_OFFSET_HOURS)
    return now.strftime("%I:%M %p IST")


def format_signal_alert(r: StrategyResult, signal_id: int | None = None) -> str:
    sid = f"#{signal_id:03d}" if signal_id else "#---"
    direction_emoji = "🟢" if r.direction == "LONG" else "🔴"

    # Conditions list
    cond_lines = []
    label_map = {
        "cvd_divergence":   "CVD Divergence",
        "vwap_extended":    "VWAP Extended",
        "volume_declining": "Volume Declining",
        "price_structure":  "Price Structure",
        "funding_neutral":  "Funding Neutral",
    }
    # BUG-09 fix: map condition keys to their matching detail keys explicitly
    DETAIL_KEY_FOR_CONDITION = {
        "cvd_divergence":   "cvd_bias",
        "vwap_extended":    "vwap_dev_pct",
        "volume_declining": "volume_vs_avg",
        "price_structure":  "price_move",
        "funding_neutral":  "funding_rate",
        # S2 VWAP Reclaim
        "volume_rising":    "volume_vs_avg",
        "volume_strong":    "volume_vs_avg",
        "price_reclaim":    "price_move",
        # S3 Funding Fade
        "funding_extreme":  "funding_rate",
        "price_reversal":   "price_move",
        # S4 Liquidation
        "cascade_detected": "total_usd",
        "one_sided":        "bias_ratio",
        # S5 ETF
        "etf_signal":       "net_flow",
        "flow_sustained":   "cumulative_7d",
    }
    for k, met in r.conditions.items():
        mark  = "✅" if met else "❌"
        label = label_map.get(k, k.replace("_", " ").title())
        detail_key = DETAIL_KEY_FOR_CONDITION.get(k, k)
        detail = r.condition_details.get(detail_key, r.condition_details.get(k, ""))
        cond_lines.append(f"{mark} {label:<20} : {detail}")

    # Warnings
    warn_block = ""
    if r.warnings:
        warn_lines = "\n".join(f"• {w}" for w in r.warnings)
        warn_block = f"\n━━━━━━━━━━━━━━━━━━━━━\n⚠️ WARNINGS\n\n{warn_lines}\n"

    confidence_emoji = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🟠"}.get(r.confidence, "🔴")

    return (
        f"⚔️ {BOT_NAME} SIGNAL {sid}\n"
        f"\n━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 {r.strategy}\n"
        f"🪙 {r.pair}  |  {direction_emoji} {r.direction}\n"
        f"⏰ {_now_ist_str()}  |  5m chart\n"
        f"\n━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔍 SIGNAL VERIFICATION\n\n"
        + "\n".join(cond_lines)
        + f"\n\nScore: {r.score}/100  |  Confidence: {confidence_emoji} {r.confidence}\n"
        f"\n━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 TRADE PARAMETERS\n\n"
        f"Entry    : ${r.entry_price:,.{2 if r.entry_price < 100 else 2}f}  (limit)\n"
        f"Stop     : ${r.stop_price:,.{2 if r.stop_price < 100 else 2}f}  ({r.atr:.0f} ATR ×1.5)\n"
        f"Target   : ${r.target_price:,.{2 if r.target_price < 100 else 2}f}  (VWAP / structure)\n"
        f"R:R      : 1 : {r.rr}\n"
        f"\nHold max : 45 minutes\n"
        f"Fee cost : 0.05% (maker ×2) ← already factored"
        f"{warn_block}"
        f"\n━━━━━━━━━━━━━━━━━━━━━\n"
        f"📝 LOG করতে:\n"
        f"/log {r.pair.replace('-USDT','')} {r.direction} {r.entry_price} {r.stop_price} {r.target_price}"
    )


def format_status(uptime_seconds: int, scan_count: int, paused: bool,
                  ws_connected: bool, ws_latency_ms: float,
                  coinglass_ok: bool, sosovalue_ok: bool,
                  today_signals: int, today_trades: int,
                  today_wins: int, today_losses: int) -> str:
    h = int(uptime_seconds // 3600)
    m = int((uptime_seconds % 3600) // 60)
    s = int(uptime_seconds % 60)
    paused_str = "⏸️ PAUSED" if paused else "🟢 LIVE"
    return (
        f"{paused_str} {BOT_NAME}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"WebSocket    : {'✅ Connected' if ws_connected else '❌ Disconnected'}"
        + (f" ({ws_latency_ms:.0f}ms latency)" if ws_connected else "") + "\n"
        f"CoinGlass    : {'✅ Last update OK' if coinglass_ok else '⚠️ Stale / unavailable'}\n"
        f"SoSoValue    : {'✅ Last update OK' if sosovalue_ok else '⚠️ Stale / unavailable'}\n"
        f"Signal scan  : ✅ Running" + (f" (paused)" if paused else "") + f" (loop #{scan_count})\n"
        f"\n📊 আজকের Summary (IST 09:00 reset):\n"
        f"Signals sent : {today_signals}\n"
        f"Trades logged: {today_trades}\n"
        f"Win/Loss     : {today_wins}W / {today_losses}L\n"
        f"\n⏰ Uptime: {h}h {m}m {s}s"
    )


def format_help() -> str:
    return (
        f"📜 {BOT_NAME} — COMMAND HELP\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🤖 SYSTEM\n"
        f"/start          Bot চালু\n"
        f"/status         System health\n"
        f"/pause          Scan pause\n"
        f"/resume         Scan চালু\n"
        f"/help           সব commands\n\n"
        f"📊 MARKET\n"
        f"/price BTC      Real-time price + context\n"
        f"/funding        সব pairs-এর funding rate\n"
        f"/liquidation    Last 1h liquidation data\n"
        f"/etf            ETF flow data\n\n"
        f"🔍 SIGNAL\n"
        f"/scan           Manual scan এখনই\n"
        f"/signal ETH     Specific pair signal\n"
        f"/verify ETH SHORT   তোমার idea verify\n\n"
        f"📝 TRADE LOG\n"
        f"/log ETH SHORT 3840 3852 3808   Trade log\n"
        f"/win 47         Trade win\n"
        f"/loss 47        Trade loss\n"
        f"/be 47          Breakeven\n"
        f"/open           Open trades\n"
        f"/close 47 3815  Manual close\n\n"
        f"📈 ANALYSIS\n"
        f"/analysis today     আজকের performance\n"
        f"/analysis week      Last 7 days\n"
        f"/postmortem 47      Single trade analysis\n"
        f"/postmortem week    Weekly loss analysis\n"
        f"/stats              Lifetime stats\n\n"
        f"🎓 LEARNING\n"
        f"/teach cvd          CVD full lesson\n"
        f"/teach vwap         VWAP full lesson\n"
        f"/teach funding      Funding lesson\n"
        f"/teach cascade      Liquidation lesson\n"
        f"/teach risk         Risk management lesson\n"
        f"/explain why ETH SHORT failed   Natural question\n"
        f"/why 47             Trade-specific reason\n"
        f"/quiz cvd           Quiz শুরু\n"
        f"/answer B           Quiz answer"
    )


# ────────────────────────────────────────────────────────────────────
# TELEGRAM SENDER
# ────────────────────────────────────────────────────────────────────

class AlertManager:
    def __init__(self):
        self.bot_token = TELEGRAM_BOT_TOKEN
        self.chat_id   = TELEGRAM_CHAT_ID
        self._session = None
        # BUG-009 fix: Rate limiting — max 20 messages per minute to avoid Telegram 429
        self._sent_timestamps: list[float] = []
        self._rate_limit = 20       # max msgs per window
        self._rate_window = 60.0    # seconds

    async def _get_session(self):
        if self._session is None:
            import aiohttp
            self._session = aiohttp.ClientSession()
        return self._session

    async def send(self, text: str, chat_id: str | None = None,
                   parse_mode: str | None = None) -> bool:
        if not self.bot_token:
            log.warning("TELEGRAM_BOT_TOKEN not set — alert not sent")
            return False
        chat = chat_id or self.chat_id
        if not chat:
            log.warning("TELEGRAM_CHAT_ID not set — alert not sent")
            return False

        # BUG-009 fix: Rate limit check
        import time as _time
        now = _time.time()
        self._sent_timestamps = [t for t in self._sent_timestamps if now - t < self._rate_window]
        if len(self._sent_timestamps) >= self._rate_limit:
            log.warning(f"Rate limit hit — {len(self._sent_timestamps)} msgs in last {self._rate_window}s, dropping alert")
            return False
        self._sent_timestamps.append(now)

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": chat, "text": text, "disable_web_page_preview": True}
        if parse_mode:
            payload["parse_mode"] = parse_mode

        try:
            session = await self._get_session()
            async with session.post(url, json=payload, timeout=15) as r:
                if r.status != 200:
                    body = await r.text()
                    log.error(f"Telegram send failed {r.status}: {body[:200]}")
                    return False
                return True
        except Exception as e:
            log.error(f"Telegram send error: {e}")
            return False

    async def send_signal(self, result: StrategyResult, signal_id: int | None = None) -> bool:
        text = format_signal_alert(result, signal_id)
        return await self.send(text)

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None
