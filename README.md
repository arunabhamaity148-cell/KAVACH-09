# ⚔️ KAVACH-09 — Signal Intelligence Engine

> CoinDCX USDT Futures · Signal-Only · Manual Trade · v1.0.0

A Telegram bot that scans 5 crypto pairs (BTC, ETH, SOL, BNB, XRP) on CoinDCX USDT-M futures using 5 strategies, sends high-quality trade signals, logs your manual trades, postmortems your losses, and teaches you the underlying concepts — all powered by a **free AI model** (Groq Llama 3.3 70B).

**Bot never executes trades.** You manually place limit orders based on signals. KAVACH-09 only does: Signal → Verify → Alert → Teach → Log → Postmortem.

---

## 🎯 Features

### 5 Trading Strategies
1. **CVD Divergence Scalp** — price/CVD divergence → fade
2. **VWAP Reclaim Scalp** — VWAP reclaim on rising volume → continuation
3. **Funding Rate Extreme Fade** — extreme funding → fade the crowd
4. **Liquidation Cascade Mean Reversion** — $300M+ cascade → fade the flush
5. **ETF Flow Session Trade** — BTC at US session open aligned with ETF flow

### 26+ Telegram Commands
- **System**: `/start /status /pause /resume /help /ping`
- **Market**: `/price BTC /funding /liquidation /etf`
- **Signal**: `/scan /signal ETH /verify ETH SHORT`
- **Trade Log**: `/log ETH SHORT 3840 3852 3808 /win 47 /loss 47 /be 47 /open /close 47 3815`
- **Analysis**: `/analysis today /analysis week /postmortem 47 /postmortem week /stats`
- **Learning**: `/teach cvd /explain why ETH SHORT failed /why 47 /quiz cvd /answer B`

### AI-Powered (Free)
- `/explain` — natural-language Q&A about your trades, strategies, market conditions
- `/why 47` — AI-generated explanation of why a specific trade lost, with pattern matching against past losses
- `/postmortem week` — AI-summarised weekly loss patterns + actionable rules

**Default AI**: [Groq](https://console.groq.com) `llama-3.3-70b-versatile` (free, 14k req/day, ~500 tokens/sec)
**Fallback**: Google Gemini 1.5 Flash (also free)

---

## 🚀 Quick Start (5 minutes)

### Step 1: Get the code
```bash
git clone <your-repo> kavach09
cd kavach09
```

### Step 2: Create virtualenv + install deps
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Step 3: Get your free API keys

| Key | Where to get | Required? |
|-----|--------------|-----------|
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) — send `/newbot` | ✅ YES |
| `TELEGRAM_CHAT_ID` | [@userinfobot](https://t.me/userinfobot) — sends your chat ID | ✅ YES |
| `GROQ_API_KEY` | [console.groq.com/keys](https://console.groq.com/keys) | ✅ YES (for AI features) |
| `GEMINI_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | ⚪ Optional fallback |
| `COINGLASS_API_KEY` | [coinglass.com/SwitchApi](https://www.coinglass.com/SwitchApi) | ⚪ Optional (Binance fallback otherwise) |
| `SOSOVALUE_API_KEY` | SoSoValue openapi | ⚪ Optional |

> **All market data uses Binance USDT-M public endpoints as fallback.** CoinDCX USDT-M futures share liquidity with Binance for these pairs, so quotes are essentially identical. No exchange API key needed for market data.

### Step 4: Configure environment
```bash
cp .env.example .env
nano .env   # fill in your keys
```

### Step 5: Run
```bash
python main.py
```

You should see:
```
╔══════════════════════════════════════════════════╗
║   ⚔️  KAVACH-09  —  Signal Intelligence Engine   ║
╚══════════════════════════════════════════════════╝

[INFO] kavach.main: KAVACH-09 v1.0.0 starting...
[INFO] kavach.main: DB initialised
[INFO] kavach.market: WS connected — 5 pairs, 10 streams
[INFO] kavach.main: WS ready: True, latency 23ms
[INFO] kavach.main: Signal engine started
[INFO] kavach.main: Alert forwarder started
[INFO] kavach.main: KAVACH-09 ready — polling started
```

### Step 6: Talk to your bot on Telegram

Send `/start` to your bot. You should get:
```
⚔️ KAVACH-09 চালু হয়েছে
━━━━━━━━━━━━━━━━━━━━━
📡 Data feeds: CoinDCX WS ✅ | CoinGlass ✅ | SoSoValue ✅
🔍 Monitoring: BTC ETH SOL BNB XRP
⚡ Strategies: 5টা active
📊 Signal scan: প্রতি 30 seconds

/help লিখো সব command দেখতে
```

---

## 📊 How It Works

### Signal Generation Flow
```
Binance WS (5 pairs) ──┐
                       ├──► SignalEngine.scan_once()
CoinGlass funding ─────┤         │
                       │         ├─► 5 strategies × 5 pairs
SoSoValue ETF flow ────┤         │         │
                       │         ├─► Score 0-100 (weighted conditions)
                       │         │
                       └─► Cooldown check ──► If score ≥75 & cooldown OK
                                                       │
                                                       ▼
                                              Telegram alert sent
                                                       │
                                              SQLite signal logged
```

### Score Calculation (per Section 5 of blueprint)
```
Condition              Weight
─────────────────────────────
cvd_divergence           30
vwap_extended            20
volume_declining         20
price_structure          15
funding_neutral          15
─────────────────────────────
Maximum                 100
```

- Score ≥ 90 → HIGH confidence 🟢
- Score ≥ 75 → MEDIUM confidence 🟡 (alert sent)
- Score ≥ 60 → LOW confidence 🟠 (logged, not alerted)
- Score < 60 → no signal

### Risk Calculation (per Section 5)
```
risk_amount = account × risk_pct          (default 1%)
stop_distance = 1.5 × ATR-14
stop_pct = stop_distance / entry
notional = risk_amount / stop_pct
margin = notional / leverage              (default 5x)
```

---

## 🏗 Production Deployment

### As a systemd service (Linux VPS)
```bash
# 1. Upload to /opt/kavach09
sudo mkdir -p /opt/kavach09
sudo cp -r ./* /opt/kavach09/

# 2. Create user
sudo useradd -r -s /bin/false kavach
sudo chown -R kavach:kavach /opt/kavach09

# 3. Install deps in venv
cd /opt/kavach09
sudo -u kavach python3 -m venv venv
sudo -u kavach venv/bin/pip install -r requirements.txt

# 4. Copy .env
sudo cp .env.example .env
sudo nano .env   # fill keys
sudo chown kavach:kavach .env
sudo chmod 600 .env

# 5. Install systemd
sudo cp kavach09.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kavach09

# 6. Watch logs
sudo journalctl -u kavach09 -f
```

### Quick health-check
```bash
systemctl status kavach09
# → active (running)

curl -s http://localhost:9100/health 2>/dev/null || echo "no http endpoint (use /ping on Telegram)"
```

---

## 🧠 Strategy Tuning

All thresholds live in `config.py`. Common tweaks:

```python
# config.py

# Scan more frequently (default 30s)
SCAN_INTERVAL_SECONDS = 15

# Lower the alert threshold (default 75)
SCORE_MEDIUM = 70

# Tighter funding thresholds
FUNDING_HIGH_THRESHOLD = 0.040   # was 0.050

# Wider stops (more conservative)
ATR_STOP_MULTIPLIER = 2.0        # was 1.5

# Add more pairs
PAIRS = [
    Pair("BTC-USDT", "B-USDT-BTC",  "BTCUSDT",  2, 3),
    Pair("ETH-USDT", "B-USDT-ETH",  "ETHUSDT",  2, 3),
    Pair("SOL-USDT", "B-USDT-SOL",  "SOLUSDT",  2, 2),
    Pair("BNB-USDT", "B-USDT-BNB",  "BNBUSDT",  2, 2),
    Pair("XRP-USDT", "B-USDT-XRP",  "XRPUSDT",  4, 1),
    Pair("DOGE-USDT","B-USDT-DOGE", "DOGEUSDT", 5, 0),   # new
]
```

---

## 📁 Project Structure

```
kavach09/
├── main.py                       # Async entry point (Telegram polling + scan loop)
├── config.py                     # All constants, thresholds, pair list
├── database.py                   # SQLite schema + CRUD
├── signal_engine.py              # Orchestrates 5 strategies × 5 pairs
├── verifier.py                   # /verify ETH SHORT — manual idea check
├── alert_manager.py              # Telegram message formatting + sending
├── trade_logger.py               # /log, /win, /loss, /be, /open, /close
├── postmortem.py                 # /postmortem — trade analysis (with AI)
├── teacher.py                    # /teach, /quiz, /answer — lessons
├── risk_calculator.py            # Position sizing, R:R
├── ai_engine.py                  # Groq Llama 3.3 70B + Gemini fallback
├── requirements.txt
├── .env.example
├── kavach09.service              # systemd service
│
├── data/
│   ├── market_feed.py            # Binance USDT-M WS + REST (CoinDCX shares liquidity)
│   ├── coinglass_feed.py         # Funding rates + Liquidations
│   └── etf_feed.py               # SoSoValue BTC ETF flow
│
├── indicators/
│   ├── cvd.py                    # Cumulative Volume Delta
│   ├── vwap.py                   # Session VWAP
│   └── atr.py                    # ATR-14 (Wilder's)
│
├── strategies/
│   ├── base_strategy.py
│   ├── s1_cvd_divergence.py
│   ├── s2_vwap_reclaim.py
│   ├── s3_funding_fade.py
│   ├── s4_liquidation_cascade.py
│   └── s5_etf_flow.py
│
└── commands/
    ├── cmd_system.py             # /start /status /pause /resume /help /ping
    ├── cmd_market.py             # /price /funding /liquidation /etf
    ├── cmd_signal.py             # /scan /signal /verify
    ├── cmd_trade.py              # /log /win /loss /be /open /close
    ├── cmd_analysis.py           # /analysis /postmortem /stats
    └── cmd_teach.py              # /teach /explain /why /quiz /answer
```

---

## ❓ FAQ

**Q: Does the bot place trades for me?**
A: NO. Bot is signal-only. You manually place limit orders on CoinDCX. The bot only suggests entry/stop/target.

**Q: Why Binance endpoints and not CoinDCX?**
A: CoinDCX USDT-M futures share liquidity with Binance for BTC/ETH/SOL/BNB/XRP — same prices, same orderbook depth. Binance public endpoints are more reliable, require no API key, and have WebSocket streams. CoinDCX REST is still used as a fallback in `market_feed.py` (`_coindcx_candles`).

**Q: Is Groq really free?**
A: Yes. Groq's free tier gives you 14,000 requests/day, 30 requests/minute, with `llama-3.3-70b-versatile` (one of the best open models available). For a single-user trading bot, this is way more than enough.

**Q: Can I use OpenAI instead?**
A: Sure. Edit `ai_engine.py` and change `GROQ_API_URL` to `https://api.openai.com/v1/chat/completions` and `GROQ_MODEL` to `gpt-4o-mini` (also cheap). You'll need an OpenAI key. But Groq is faster and free.

**Q: Why no CoinDCX API key needed?**
A: All data the bot uses (price, candles, trades, funding, liquidations) is available via Binance public endpoints — no key, no auth. CoinDCX's own public REST is also used as a secondary fallback. Trading API keys are NOT needed because the bot doesn't trade.

**Q: How accurate are the signals?**
A: The bot is a decision-support tool, not a crystal ball. Expect 50-65% win rate with proper risk management. Always check `/verify` before taking a signal. The postmortem system helps you learn from mistakes over time.

**Q: Can I run multiple instances?**
A: Yes — use different `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, and `KAVACH_DB_PATH` per instance.

---

## ⚠️ Risk Disclaimer

This bot is for educational purposes only. Crypto futures trading carries substantial risk of loss. Never trade with money you can't afford to lose. Past performance does not guarantee future results. Always:
- Use stop-loss orders
- Risk ≤ 2% per trade
- Take a break after 3 consecutive losses
- Do your own research

**The author is not responsible for any financial losses.**

---

## 📝 License

MIT — use it, modify it, share it. Attribution appreciated.

---

## 🤝 Support

- Issues: open a GitHub issue
- Telegram: mention the bot in your trading group
- Improve: PRs welcome (especially new strategies)

---

*KAVACH-09 Blueprint v1.0 | June 2026*
*Signal-Only Bot | CoinDCX USDT Futures | Manual Trading*
