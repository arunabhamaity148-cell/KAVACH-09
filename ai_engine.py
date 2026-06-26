"""
KAVACH-09 — AI Engine (Groq Llama 3.3 70B + Gemini fallback)
==============================================================
Powers /explain and /why commands with a free, fast, capable LLM.

PRIMARY:  Groq (https://console.groq.com — free, generous limits)
          Model: llama-3.3-70b-versatile (best free open model)
FALLBACK: Google Gemini (https://aistudio.google.com — free tier)
          Model: gemini-1.5-flash

Both providers have generous free tiers and OpenAI-compatible APIs.

Setup:
  1. Get a free GROQ_API_KEY from https://console.groq.com/keys
  2. (Optional) Get a free GEMINI_API_KEY from https://aistudio.google.com/apikey
  3. Put them in .env

The AI is used ONLY for natural-language explanation of trades.
It does NOT generate trade signals. All signal logic is deterministic.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp

from config import (
    BOT_NAME, GEMINI_API_KEY, GEMINI_API_URL, GEMINI_MODEL,
    GROQ_API_KEY, GROQ_API_URL, GROQ_MODEL,
)

log = logging.getLogger("kavach.ai")


SYSTEM_PROMPT = f"""You are {BOT_NAME}, a trading mentor bot for CoinDCX USDT Futures (Indian trader audience).
You explain crypto-futures trading concepts in simple Bengali + English mix (Banglish) — exactly like a senior trader teaching a junior.

Rules:
- Use Bengali script + English technical terms (e.g., "CVD divergence মানে price উপরে যাচ্ছে কিন্তু buyers কমছে")
- Be CONCISE — max 8-12 lines per response unless asked for detail
- Always ground answers in the trade data provided; never invent numbers
- If asked "why did trade X fail" — give 2-3 concrete reasons from the data
- Suggest ONE specific actionable lesson per response
- Use emojis sparingly (1-2 per response) — 🎯 ⚠️ ✅ ❌ only
- Never recommend increasing position size or leverage
- Never promise profits — always mention risk
"""


# ────────────────────────────────────────────────────────────────────
# PUBLIC API
# ────────────────────────────────────────────────────────────────────

async def explain(question: str, context: dict[str, Any] | None = None) -> str:
    """
    Natural-language Q&A.
    `context` can include trade data, recent losses, strategy stats etc.
    """
    context = context or {}
    user_msg = _build_user_msg(question, context)
    return await _call_llm(user_msg)


async def why_trade_lost(trade: dict, similar_losses: list[dict] | None = None) -> str:
    """Generate a /why explanation for a losing trade."""
    similar_losses = similar_losses or []
    prompt = (
        f"Explain why this trade lost. Be specific and concise (max 10 lines).\n\n"
        f"TRADE:\n{json.dumps(trade, indent=2, default=str)}\n\n"
    )
    if similar_losses:
        prompt += f"SIMILAR PAST LOSSES (pattern matching):\n{json.dumps(similar_losses, indent=2, default=str)}\n\n"
    prompt += "Give 3 concrete reasons. End with ONE actionable rule to avoid this in future."
    return await _call_llm(prompt)


async def weekly_postmortem_summary(losses: list[dict], rule_breaks: list[dict]) -> str:
    """AI-enriched summary of weekly postmortem."""
    prompt = (
        f"Summarise this week's losses and rule breaks. Find the TOP 3 recurring patterns. "
        f"Be concise (max 12 lines). End with 3 specific actionable rules.\n\n"
        f"LOSSES:\n{json.dumps(losses[:20], indent=2, default=str)}\n\n"
        f"RULE_BREAKS:\n{json.dumps(rule_breaks[:20], indent=2, default=str)}"
    )
    return await _call_llm(prompt)


# ────────────────────────────────────────────────────────────────────
# LLM CALLER (Groq primary, Gemini fallback)
# ────────────────────────────────────────────────────────────────────

async def _call_llm(user_msg: str, max_tokens: int = 800) -> str:
    """Try Groq first, fall back to Gemini, then to a static fallback."""
    if GROQ_API_KEY:
        try:
            return await _call_groq(user_msg, max_tokens)
        except Exception as e:
            log.warning(f"Groq call failed: {e}; trying Gemini")

    if GEMINI_API_KEY:
        try:
            return await _call_gemini(user_msg, max_tokens)
        except Exception as e:
            log.warning(f"Gemini call failed: {e}")

    return _static_fallback(user_msg)


async def _call_groq(user_msg: str, max_tokens: int) -> str:
    """Groq OpenAI-compatible endpoint."""
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type":  "application/json",
    }
    payload = {
        "model":       GROQ_MODEL,
        "messages":    [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_msg},
        ],
        "temperature": 0.4,
        "max_tokens":  max_tokens,
    }
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.post(GROQ_API_URL, headers=headers, json=payload) as r:
            if r.status != 200:
                body = await r.text()
                raise RuntimeError(f"Groq {r.status}: {body[:300]}")
            data = await r.json()
    return data["choices"][0]["message"]["content"].strip()


async def _call_gemini(user_msg: str, max_tokens: int) -> str:
    """Google Gemini API (different schema)."""
    url = f"{GEMINI_API_URL}/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_msg}]}],
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": max_tokens,
        },
    }
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.post(url, json=payload, headers={"Content-Type": "application/json"}) as r:
            if r.status != 200:
                body = await r.text()
                raise RuntimeError(f"Gemini {r.status}: {body[:300]}")
            data = await r.json()
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts).strip()


def _static_fallback(user_msg: str) -> str:
    """Used when no AI provider is configured — generic but useful answer."""
    return (
        "⚠️ AI চালু না (GROQ_API_KEY সেট করো .env-এ)\n\n"
        "Quick answer:\n"
        f"Question: {user_msg[:200]}\n\n"
        "Setup:\n"
        "1. https://console.groq.com/keys থেকে free API key নাও\n"
        "2. .env file-এ লেখো: GROQ_API_KEY=gsk_xxx\n"
        "3. Bot restart করো\n\n"
        "Groq free tier-এ প্রতিদিন 14,000 requests — অনেক বেশি।"
    )


def _build_user_msg(question: str, context: dict) -> str:
    """Pack the user question + structured context into a single prompt."""
    if not context:
        return question
    parts = [question, "", "CONTEXT (use this data, do not invent):"]
    for k, v in context.items():
        parts.append(f"\n{k}:")
        parts.append(json.dumps(v, indent=2, default=str) if not isinstance(v, str) else v)
    return "\n".join(parts)


# ────────────────────────────────────────────────────────────────────
# HEALTH CHECK
# ────────────────────────────────────────────────────────────────────

async def health_check() -> dict[str, Any]:
    """Return which AI providers are configured and reachable."""
    out: dict[str, Any] = {"groq": {"configured": bool(GROQ_API_KEY), "model": GROQ_MODEL},
                           "gemini": {"configured": bool(GEMINI_API_KEY), "model": GEMINI_MODEL}}
    if GROQ_API_KEY:
        try:
            r = await _call_groq("ping", max_tokens=5)
            out["groq"]["reachable"] = True
            out["groq"]["sample"] = r[:60]
        except Exception as e:
            out["groq"]["reachable"] = False
            out["groq"]["error"] = str(e)[:120]
    if GEMINI_API_KEY:
        try:
            r = await _call_gemini("ping", max_tokens=5)
            out["gemini"]["reachable"] = True
            out["gemini"]["sample"] = r[:60]
        except Exception as e:
            out["gemini"]["reachable"] = False
            out["gemini"]["error"] = str(e)[:120]
    return out
