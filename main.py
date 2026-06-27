"""
KAVACH-09 — Main Entry Point
=============================
Async Telegram bot + signal scan loop.

Run:
    python main.py

Bot will:
  1. Init DB
  2. Connect to Binance USDT-M futures WebSocket (CoinDCX shares liquidity)
  3. Warm-start candle buffers
  4. Start signal scan loop (every 30s)
  5. Start Telegram polling
  6. Auto-send alerts when signals fire
"""
from __future__ import annotations

import asyncio
import logging
import signal as sig
import sys
import time
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler, ContextTypes,
)

from config import (
    BOT_NAME, BOT_VERSION, IST_OFFSET_HOURS, TELEGRAM_BOT_TOKEN, SCORE_MIN,
)
from database import init_db, get_trades_since, get_signals_since
from data.market_feed import MarketFeedBus, warm_start
from data import coinglass_feed, etf_feed
from signal_engine import SignalEngine
from alert_manager import AlertManager

from commands.cmd_system  import cmd_start, cmd_status, cmd_pause, cmd_resume, cmd_help, cmd_ping
from commands.cmd_market  import cmd_price, cmd_funding, cmd_liquidation, cmd_etf
from commands.cmd_signal  import cmd_scan, cmd_signal, cmd_verify
from commands.cmd_trade   import cmd_log, cmd_win, cmd_loss, cmd_be, cmd_open, cmd_close
from commands.cmd_analysis import cmd_analysis, cmd_postmortem, cmd_stats
from commands.cmd_teach   import cmd_teach, cmd_explain, cmd_why, cmd_quiz, cmd_answer


# ────────────────────────────────────────────────────────────────────
# LOGGING
# ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# Quiet noisy libs
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("kavach.main")


# ────────────────────────────────────────────────────────────────────
# BOT CONTAINER — passed via application.bot_data
# ────────────────────────────────────────────────────────────────────

class KavachBot:
    """Holds shared state — bus, engine, alert manager, counters."""
    def __init__(self):
        self.bus = MarketFeedBus()
        self.engine = SignalEngine(self.bus)
        self.alerts = AlertManager()
        self.start_time = time.time()
        self.coinglass_ok = False
        self.sosovalue_ok = False
        self._last_signal_check = 0.0

    async def start(self) -> None:
        log.info(f"{BOT_NAME} v{BOT_VERSION} starting...")
        init_db()
        log.info("DB initialised")

        # Start WS bus + warm-start
        await self.bus.start()
        await asyncio.sleep(2)   # let WS connect
        await warm_start(self.bus)
        await self.bus.wait_ready(timeout=15)
        log.info(f"WS ready: {self.bus.is_connected}, latency {self.bus.latency_ms:.0f}ms")

        # Probe aux feeds
        try:
            await coinglass_feed.get_funding_rates()
            self.coinglass_ok = True
        except Exception as e:
            log.warning(f"CoinGlass probe failed: {e}")
            self.coinglass_ok = False
        try:
            await etf_feed.get_etf_flow()
            self.sosovalue_ok = True
        except Exception as e:
            log.warning(f"SoSoValue probe failed: {e}")
            self.sosovalue_ok = False

        # Start scan loop
        await self.engine.start()
        log.info("Signal engine started")

        # Start alert forwarder — ISSUE 5 FIX: keep reference for cancellation
        self._forwarder_task = asyncio.create_task(self._alert_forwarder())

    async def stop(self) -> None:
        log.info("Stopping...")
        # ISSUE 5 FIX: cancel alert forwarder task before stopping subsystems
        if hasattr(self, "_forwarder_task") and self._forwarder_task:
            self._forwarder_task.cancel()
            try:
                await self._forwarder_task
            except asyncio.CancelledError:
                pass
        await self.engine.stop()
        await self.bus.stop()
        await self.alerts.close()
        log.info("Stopped.")

    # ─── forwards new signals from engine → Telegram ─────────────
    async def _alert_forwarder(self) -> None:
        from collections import deque
        seen_ids: deque = deque(maxlen=500)   # BUG-08 fix: bounded
        log.info("Alert forwarder started")
        while True:
            try:
                for r in self.engine.latest_signals():
                    sig_id = r.extra.get("signal_id")
                    if sig_id and sig_id in seen_ids:
                        continue
                    if r.score < SCORE_MIN:
                        continue
                    if not self.engine.should_alert(r.pair, r.direction):
                        continue
                    ok = await self.alerts.send_signal(r, sig_id)
                    if ok:
                        self.engine.mark_alerted(r.pair, r.direction)  # BUG-06 fix
                        if sig_id:
                            seen_ids.append(sig_id)
                        log.info(f"Alert sent: {r.pair} {r.direction} score={r.score}")
                    else:
                        log.warning(f"Alert send failed: {r.pair} {r.direction} — will retry")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error(f"Alert forwarder error: {e}", exc_info=True)
            await asyncio.sleep(5)

    # ─── daily counters (IST 09:00 reset) — BUG-15 fix: consistent IST ──
    def _ist_today_start(self) -> datetime:
        """9 AM IST today as a naive IST datetime."""
        now_ist = datetime.utcnow() + timedelta(hours=IST_OFFSET_HOURS)
        return now_ist.replace(hour=9, minute=0, second=0, microsecond=0)

    def _ist_cutoff_iso(self) -> str:
        """Return IST 9 AM cutoff as ISO string for DB comparison."""
        return self._ist_today_start().isoformat()

    def today_signal_count(self) -> int:
        cutoff_iso = self._ist_cutoff_iso()
        sigs = get_signals_since(None, hours=24)
        return len([s for s in sigs if s["timestamp"] >= cutoff_iso])

    def today_trade_count(self) -> int:
        cutoff_iso = self._ist_cutoff_iso()
        trades = get_trades_since(hours=24)
        return len([t for t in trades if t["timestamp"] >= cutoff_iso])

    def today_result_count(self, result: str) -> int:
        cutoff_iso = self._ist_cutoff_iso()
        trades = get_trades_since(hours=24)
        return len([t for t in trades
                    if t.get("result") == result
                    and t["timestamp"] >= cutoff_iso])


# ────────────────────────────────────────────────────────────────────
# MAIN
# ────────────────────────────────────────────────────────────────────

def build_application() -> Application:
    if not TELEGRAM_BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN not set in .env — bot cannot start")
        sys.exit(1)

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    # Register all command handlers
    handlers = [
        # System
        CommandHandler("start",  cmd_start),
        CommandHandler("status", cmd_status),
        CommandHandler("pause",  cmd_pause),
        CommandHandler("resume", cmd_resume),
        CommandHandler("help",   cmd_help),
        CommandHandler("ping",   cmd_ping),
        # Market
        CommandHandler("price",       cmd_price),
        CommandHandler("funding",     cmd_funding),
        CommandHandler("liquidation", cmd_liquidation),
        CommandHandler("etf",         cmd_etf),
        # Signal
        CommandHandler("scan",   cmd_scan),
        CommandHandler("signal", cmd_signal),
        CommandHandler("verify", cmd_verify),
        # Trade log
        CommandHandler("log",   cmd_log),
        CommandHandler("win",   cmd_win),
        CommandHandler("loss",  cmd_loss),
        CommandHandler("be",    cmd_be),
        CommandHandler("open",  cmd_open),
        CommandHandler("close", cmd_close),
        # Analysis
        CommandHandler("analysis",   cmd_analysis),
        CommandHandler("postmortem", cmd_postmortem),
        CommandHandler("stats",      cmd_stats),
        # Teaching
        CommandHandler("teach",  cmd_teach),
        CommandHandler("explain", cmd_explain),
        CommandHandler("why",    cmd_why),
        CommandHandler("quiz",   cmd_quiz),
        CommandHandler("answer", cmd_answer),
        # Aliases
        CommandHandler("result", cmd_win),    # /result 47 WIN alias
        CommandHandler("weekly", lambda u, c: cmd_analysis(u, c)),  # /weekly alias
    ]
    for h in handlers:
        app.add_handler(h)

    # BUG-002 fix: Global error handler
    app.add_error_handler(_error_handler)

    return app


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler — logs exceptions and notifies user if possible."""
    import traceback
    err = context.error
    tb = "".join(traceback.format_exception(type(err), err, err.__traceback__))
    log.error(f"Exception while handling update:\n{tb}")
    # Try to notify the user
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                f"⚠️ একটি error হয়েছে। Admin-কে জানানো হয়েছে।\n"
                f"Error: {type(err).__name__}: {str(err)[:200]}"
            )
        except Exception:
            pass


async def _post_init(app: Application) -> None:
    """Called after Application is built, before polling starts."""
    bot = KavachBot()
    await bot.start()
    app.bot_data["kavach"] = bot
    # Give coinglass_feed access to the WS bus for liquidation data
    from data import coinglass_feed as _cf
    _cf.set_bus(bot.bus)
    log.info(f"{BOT_NAME} ready — polling started")


async def _post_shutdown(app: Application) -> None:
    bot: KavachBot | None = app.bot_data.get("kavach")   # ISSUE 5 FIX: was "bot"
    if bot:
        await bot.stop()


def main() -> None:
    print(r"""
╔══════════════════════════════════════════════════╗
║                                                  ║
║   ⚔️  KAVACH-09  —  Signal Intelligence Engine   ║
║                                                  ║
║   CoinDCX USDT Futures · Signal-Only · v1.0.0    ║
║                                                  ║
╚══════════════════════════════════════════════════╝
""")
    app = build_application()
    app.post_init = _post_init
    app.post_shutdown = _post_shutdown

    log.info("Starting Telegram polling (Ctrl+C to stop)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
