"""
KAVACH-09 — Teacher
====================
Lessons + quizzes for /teach, /quiz, /answer commands.
All lessons are static markdown (deterministic); AI is used only for /explain.
"""
from __future__ import annotations

import logging
import random
from typing import Any

import database as db

log = logging.getLogger("kavach.teacher")


# ────────────────────────────────────────────────────────────────────
# LESSONS — full content
# ────────────────────────────────────────────────────────────────────

LESSONS: dict[str, str] = {
    "cvd": (
        "📚 LESSON: Cumulative Volume Delta (CVD)\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔵 কী জিনিস CVD?\n\n"
        "প্রতিটা trade হয় দুভাবে:\n"
        "• Buyer aggressive → bid-এ কিনলো → BUY volume\n"
        "• Seller aggressive → ask-এ বেচলো → SELL volume\n\n"
        "CVD = Σ(BUY volume) − Σ(SELL volume) — cumulative\n\n"
        "সহজ কথা:\n"
        "CVD বাড়ছে → buyers বেশি aggressive\n"
        "CVD কমছে → sellers বেশি aggressive\n\n"
        "🔴 কেন Divergence গুরুত্বপূর্ণ?\n\n"
        "BEARISH DIVERGENCE:\n"
        "Price: $100 → $102 → $104 (higher highs)\n"
        "CVD:  1000 → 900 → 750 (lower highs)\n\n"
        "মানে: price উপরে যাচ্ছে কিন্তু buyers ক্রমশ\n"
        "কম aggressive হচ্ছে। প্রতিটা push-এ কম লোক কিনছে।\n"
        "Result: momentum শেষ → reversal আসবে\n\n"
        "BULLISH DIVERGENCE: Mirror image (price lower low, CVD higher low)\n\n"
        "📐 MATH:\n"
        "CVD_t = CVD_(t-1) + BuyVol_t − SellVol_t\n\n"
        "Trade tape থেকে:\n"
        "BuyVol  = trades executed at ASK price (taker buy)\n"
        "SellVol = trades executed at BID price (taker sell)\n\n"
        "🎯 কখন valid signal?\n"
        "✅ Divergence ≥ 3 candles ধরে\n"
        "✅ Price move ≥ 0.3% (significant)\n"
        "✅ CVD move opposite ≥ 15% of previous reading\n"
        "❌ Invalid: single candle spike\n"
        "❌ Invalid: low volume period (<50% avg)\n\n"
        "🧪 REAL EXAMPLE:\n"
        "11:05 ETH price: $3,831 | CVD: −1,203\n"
        "11:15 ETH price: $3,842 | CVD: −2,847 (divergence!)\n"
        "Price +0.29% ↑ but CVD −136% ↓\n"
        "→ SHORT signal generated ✅\n\n"
        "/quiz cvd লেখো practice করতে\n"
        "/teach vwap পরের lesson দেখতে"
    ),

    "vwap": (
        "📚 LESSON: VWAP (Volume-Weighted Average Price)\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔵 VWAP কী?\n\n"
        "VWAP = দিনের সব trades-এর average price, volume দিয়ে weighted।\n"
        "Institutional traders VWAP দিয়ে measure করে তাদের execution ভালো হলো কিনা।\n\n"
        "📐 Formula:\n"
        "VWAP = Σ(typical_price × volume) / Σ(volume)\n"
        "where typical_price = (high + low + close) / 3\n\n"
        "🎯 কেন গুরুত্বপূর্ণ?\n\n"
        "1️⃣ Institutional benchmark — বড় লোকেরা VWAP-এর উপরে buy করলে ভালো না, নিচে buy ভালো\n"
        "2️⃣ Mean-reversion magnet — price অনেক দূর গেলে VWAP-এর দিকে ফিরে আসে\n"
        "3️⃣ Trend filter — VWAP-এর উপরে = bullish bias, নিচে = bearish bias\n\n"
        "📊 KAVACH-09-এ session VWAP:\n"
        "• Reset হয় 00:00 UTC (5:30 AM IST)\n"
        "• Daily anchor — দিনের শুরু থেকে accumulate হয়\n\n"
        "🔍 PRICE vs VWAP interpretation:\n\n"
        "Price >> VWAP (+0.5% বা তার বেশি):\n"
        "  • Overextended → mean-reversion likely\n"
        "  • Short bias (fade)\n\n"
        "Price << VWAP (−0.5% বা তার বেশি):\n"
        "  • Oversold → bounce likely\n"
        "  • Long bias (fade)\n\n"
        "Price ≈ VWAP (±0.1%):\n"
        "  • Equilibrium — wait for direction\n"
        "  • Reclaim from this level = strong signal\n\n"
        "🧪 REAL EXAMPLE:\n"
        "BTC price $107,240 | VWAP $106,890\n"
        "Deviation: +0.33% (above VWAP)\n"
        "→ Mild bullish bias — not extreme\n\n"
        "⚠️ Warning: VWAP-এর ফাঁক বড় হলে reversal risk বাড়ে, কিন্তু timing ঠিক করা কঠিন। সবসময় ATR-সহ stop ব্যবহার করো।\n\n"
        "/quiz vwap লেখো practice করতে\n"
        "/teach funding পরের lesson দেখতে"
    ),

    "funding": (
        "📚 LESSON: Funding Rate Mechanics\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔵 Funding Rate কী?\n\n"
        "Perpetual futures-এ কোনো expiry নেই। Funding rate হলো একটা periodic payment\n"
        "যা longs আর shorts-এর মধ্যে হাতবদল হয় — price-কে spot-এর কাছে রাখার জন্য।\n\n"
        "📅 Timing: প্রতি 8 ঘণ্টায় (00:00, 08:00, 16:00 UTC)\n\n"
        "📐 Calculation:\n"
        "Funding = Position_Value × Funding_Rate\n\n"
        "🎯 Interpretation:\n\n"
        "Funding > 0 (positive):\n"
        "  • Longs shorts-কে টাকা দেয়\n"
        "  • মানে longs বেশি crowded\n"
        "  • Higher the rate = more crowded = reversal risk\n\n"
        "Funding < 0 (negative):\n"
        "  • Shorts longs-কে টাকা দেয়\n"
        "  • মানে shorts বেশি crowded\n"
        "  • Short squeeze risk\n\n"
        "📊 KAVACH-09 thresholds (S3 Funding Fade strategy):\n"
        "• Funding ≥ +0.05% → SHORT bias (longs অনেক crowded)\n"
        "• Funding ≤ −0.02% → LONG bias (shorts অনেক crowded)\n"
        "• Between → neutral, no fade trade\n\n"
        "🧪 REAL EXAMPLE (extreme):\n"
        "2021 সালে BTC $20K পুশ করার সময় funding ছিল +0.30% per 8h\n"
        "মানে প্রতি 8 ঘণ্টায় 0.30% longs shorts-কে দিচ্ছিল\n"
        "Annualized: 0.30% × 3 × 365 = 328%!\n"
        "→ Result: সাথে সাথে $5K dump (long squeeze)\n\n"
        "⚠️ Risk warning:\n"
        "• Funding extreme হলেও squeeze দিন-সপ্তাহ চলতে পারে\n"
        "• 0.05% threshold = caution zone, 0.10%+ = danger zone\n"
        "• সবসময় ATR-based stop ব্যবহার করো — funding একা যথেষ্ট না\n\n"
        "/teach cascade পরের lesson দেখতে"
    ),

    "cascade": (
        "📚 LESSON: Liquidation Cascade\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔵 Liquidation কী?\n\n"
        "Futures-ে leverage থাকে। যদি price তোমার কলateral-এর দিকে যায় এবং margin\n"
        "below maintenance-এ চলে যায়, exchange forcefully তোমার position কে বন্ধ করে দেয়\n"
        "(liquidate করে)।\n\n"
        "🔴 Cascade কী?\n\n"
        "যখন অনেক লোকের stop একসাথে hit হয়, তখন তাদের forced sells আরও price drop করে,\n"
        "যেটা আরও liquidations trigger করে — chain reaction।\n\n"
        "📊 Cascade signature:\n"
        "• $300M+ liquidations in 1 hour → cascade territory\n"
        "• ≥70% one-sided (long vs short) → strong directional flush\n"
        "• Volume spike + sharp move + recovery = textbook cascade\n\n"
        "🎯 Trading the cascade (S4 strategy):\n\n"
        "DO NOT trade যখন cascade শুরু হচ্ছে — অপেক্ষা করো প্রথম flush শেষ হতে।\n"
        "Entry: longs flushed 70%+ → LONG (mean reversion)\n"
        "       shorts flushed 70%+ → SHORT (mean reversion)\n\n"
        "Stop:   2.0 × ATR (wider — cascade volatility high)\n"
        "Target: VWAP (mean reversion magnet)\n\n"
        "🧪 REAL EXAMPLE:\n"
        "Aug 2024, BTC একটা নিউজে $65K থেকে $49K drop করল\n"
        "Liquidation spike: $1.2B in 2 hours (mostly longs)\n"
        "After flush: 4 hours-এ $49K → $56K (+14% bounce)\n"
        "→ Cascade bounces often violent\n\n"
        "⚠️ Risk warning:\n"
        "• কখনো cascade-এর মাঝে entry নিও না — wait for volume to peak\n"
        "• Stop wider than normal (2×ATR অন্তত)\n"
        "• Position size 50% কমাও — cascade volatility unpredictable\n\n"
        "/teach risk পরের lesson দেখতে"
    ),

    "risk": (
        "📚 LESSON: Risk Management & Position Sizing\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔵 1R কী?\n\n"
        "1R = তোমার risk per trade\n"
        "তুমি যদি ₹500 risk করো প্রতি trade-এ → 1R = ₹500\n\n"
        "Win = +2R মানে ₹1000 profit\n"
        "Loss = −1R মানে ₹500 loss\n\n"
        "🎯 ATR-Based Position Sizing:\n\n"
        "ATR (Average True Range) = average candle range\n"
        "ATR-14 on 5m BTC = ~$285 (current)\n\n"
        "Stop distance = 1.5 × ATR = $427\n\n"
        "যদি তোমার 1R = ₹500 (capital-এর 1%):\n"
        "Position = Risk ÷ Stop_distance_in_%\n"
        "Stop% = 427 ÷ 107,000 = 0.40%\n"
        "Contracts = ₹500 ÷ 0.40% = ₹1,25,000 notional\n\n"
        "5x leverage হলে: ₹25,000 margin\n\n"
        "📐 FORMULA:\n"
        "contracts = (account × risk_pct) ÷ (atr14 × 1.5 ÷ price)\n\n"
        "🔴 NEVER:\n"
        "• 2%-এর বেশি risk per trade\n"
        "• Day-এ 5%-এর বেশি total loss\n"
        "• একসাথে 2টার বেশি open position\n\n"
        "📊 R:R (Reward:Risk) math:\n\n"
        "R:R = 1 : 2 মানে প্রতি ₹1 risk-এ ₹2 reward\n"
        "Win rate 40% হলেও break-even: (0.40 × 2) − (0.60 × 1) = +0.20R per trade\n\n"
        "🎯 Minimum acceptable R:R:\n"
        "• Scalping (5m-15m): 1 : 1.5+\n"
        "• Intraday (1h): 1 : 2+\n"
        "• Swing (4h-D): 1 : 3+\n\n"
        "⚠️ Common mistakes:\n"
        "1. Stop too tight (chasing small risk) → whipsaw\n"
        "2. Risk too high (FOMO) → 3 bad days = bust\n"
        "3. No daily stop → revenge trading\n"
        "4. Averaging down → classic account killer\n\n"
        "🧪 Daily routine:\n"
        "• Start: Define max daily loss (5% of account)\n"
        "• Each trade: Set stop BEFORE entry, never move it against you\n"
        "• End: Log every trade, review mistakes\n\n"
        "/stats লেখো তোমার lifetime record দেখতে"
    ),
}


# ────────────────────────────────────────────────────────────────────
# QUIZZES
# ────────────────────────────────────────────────────────────────────

QUIZZES: dict[str, list[dict]] = {
    "cvd": [
        {
            "q": "Price lower low করছে কিন্তু CVD higher low করছে। এটা কী signal?",
            "options": ["Bearish", "Bullish", "Neutral"],
            "correct": 1,
            "explain": "Bullish divergence: price lower low → sellers push করছে, "
                       "CVD higher low → কিন্তু selling pressure কমছে. "
                       "Sellers exhausted → long opportunity.",
        },
        {
            "q": "Bearish CVD divergence-এ entry direction কী?",
            "options": ["LONG", "SHORT", "Both"],
            "correct": 1,
            "explain": "Bearish divergence = price up but CVD down = buyers exhausted → SHORT.",
        },
        {
            "q": "CVD signal valid হতে minimum কত candles divergence চাই?",
            "options": ["1", "3", "10", "50"],
            "correct": 1,
            "explain": "≥3 candles needed. Single-candle spikes are noise, not divergence.",
        },
    ],
    "vwap": [
        {
            "q": "Price VWAP-এর অনেক উপরে (+0.8%)। সম্ভাব্য bias?",
            "options": ["Strong LONG", "Mean-revert SHORT", "Neutral"],
            "correct": 1,
            "explain": "Overextended from VWAP → price tends to revert → SHORT bias (fade).",
        },
        {
            "q": "VWAP কখন reset হয় (KAVACH-09 setup)?",
            "options": ["Every candle", "8h", "Daily 00:00 UTC", "Weekly"],
            "correct": 2,
            "explain": "Session VWAP resets at 00:00 UTC = 5:30 AM IST daily.",
        },
        {
            "q": "VWAP reclaim signal কখন valid?",
            "options": ["Price crashes through VWAP", "Price closes back above VWAP after losing it",
                        "Price touches VWAP", "VWAP moves to price"],
            "correct": 1,
            "explain": "Reclaim = lost then took back. Close above VWAP after being below = reclaim.",
        },
    ],
    "funding": [
        {
            "q": "Funding rate +0.10% per 8h চলছে। এটা কী বোঝায়?",
            "options": ["Longs pay shorts, crowded long", "Shorts pay longs, crowded short",
                        "Neutral", "No relation"],
            "correct": 0,
            "explain": "Positive funding = longs pay shorts = too many longs = crowded long position.",
        },
        {
            "q": "KAVACH-09 funding fade SHORT threshold?",
            "options": ["+0.01%", "+0.05%", "+0.10%", "+1.00%"],
            "correct": 1,
            "explain": "≥+0.05% triggers SHORT bias in Funding Fade strategy.",
        },
        {
            "q": "Negative funding (-0.05%) signal?",
            "options": ["Long bias (short squeeze risk)", "Short bias", "Neutral", "Bull trend"],
            "correct": 0,
            "explain": "Negative funding = shorts pay longs = crowded shorts = long bias (short squeeze potential).",
        },
    ],
}


# ────────────────────────────────────────────────────────────────────
# SESSION STATE (per chat) — tracks quiz progress
# ────────────────────────────────────────────────────────────────────

_quiz_state: dict[int, dict] = {}   # chat_id → {topic, q_index, score}


def get_lesson(topic: str) -> str | None:
    topic = topic.lower().strip()
    return LESSONS.get(topic)


def start_quiz(chat_id: int, topic: str) -> dict | None:
    topic = topic.lower().strip()
    if topic not in QUIZZES:
        return None
    _quiz_state[chat_id] = {"topic": topic, "q_index": 0, "score": 0}
    return _next_question(chat_id)


def _next_question(chat_id: int) -> dict | None:
    state = _quiz_state.get(chat_id)
    if not state:
        return None
    qs = QUIZZES[state["topic"]]
    if state["q_index"] >= len(qs):
        # Final score
        total = len(qs)
        score = state["score"]
        db_stat = db.insert_quiz(state["topic"], score == total, f"final:{score}/{total}")
        return {
            "done": True,
            "topic": state["topic"],
            "score": score,
            "total": total,
            "emoji": "🏆" if score == total else ("✅" if score >= total * 0.6 else "📚"),
        }
    q = qs[state["q_index"]]
    return {
        "q_index": state["q_index"] + 1,
        "total": len(qs),
        "question": q["q"],
        "options": q["options"],
    }


def answer_quiz(chat_id: int, choice: str | int) -> dict | None:
    """Process answer. choice can be 'A'/'B'/'C' or 0/1/2."""
    state = _quiz_state.get(chat_id)
    if not state:
        return None
    qs = QUIZZES[state["topic"]]
    if state["q_index"] >= len(qs):
        return None
    q = qs[state["q_index"]]

    # Parse choice
    if isinstance(choice, str):
        choice = choice.strip().upper()
        if choice in ("A", "B", "C"):
            idx = ord(choice) - ord("A")
        elif choice.isdigit():
            idx = int(choice) - 1
        else:
            return {"error": "Reply with A, B, or C"}
    else:
        idx = choice

    if idx < 0 or idx >= len(q["options"]):
        return {"error": "Invalid choice"}

    correct = (idx == q["correct"])
    if correct:
        state["score"] += 1
    # Log to DB
    db.insert_quiz(state["topic"], correct, q["q"])

    state["q_index"] += 1
    next_q = _next_question(chat_id)
    return {
        "was_correct": correct,
        "correct_answer": q["options"][q["correct"]],
        "explanation": q["explain"],
        "next": next_q,
    }


def format_question(q: dict) -> str:
    opts = "\n".join(f"  {chr(65+i)}) {o}" for i, o in enumerate(q["options"]))
    return (
        f"🧠 QUIZ: {q.get('topic', '').upper()}\n\n"
        f"Q{q['q_index']}/{q['total']}: {q['question']}\n\n"
        f"{opts}\n\n"
        f"/answer A বা /answer B বা /answer C"
    )


def format_answer(a: dict) -> str:
    if "error" in a:
        return f"❌ {a['error']}"
    emoji = "✅" if a["was_correct"] else "❌"
    out = (
        f"{emoji} {'CORRECT!' if a['was_correct'] else 'WRONG'}\n\n"
        f"Correct answer: {a['correct_answer']}\n\n"
        f"{a['explanation']}\n"
    )
    nxt = a.get("next")
    if nxt:
        if nxt.get("done"):
            emoji2 = nxt["emoji"]
            out += (
                f"\n━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 QUIZ COMPLETE\n"
                f"Score: {nxt['score']}/{nxt['total']} {emoji2}\n"
            )
            if nxt["score"] == nxt["total"]:
                out += "Perfect! CVD master! 🏆\n"
            elif nxt["score"] >= nxt["total"] * 0.6:
                out += "Good job — keep practicing 📚\n"
            else:
                out += "Review the lesson again /teach <topic>\n"
        else:
            out += "\n" + format_question(nxt)
    return out
