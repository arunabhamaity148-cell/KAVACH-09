"""
KAVACH-09 — SQLite Database Layer
=================================
Schema + CRUD for signals, trades, quiz_results, rule_breaks.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Iterator

from config import DB_PATH, IST_OFFSET_HOURS


_lock = threading.Lock()


def _now_ist() -> str:
    """Return current UTC time + 5:30 offset, formatted as ISO string."""
    return (datetime.utcnow() + timedelta(hours=IST_OFFSET_HOURS)).isoformat(timespec="seconds")


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Create all tables if not exist. Safe to call on every startup."""
    with _lock, get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS signals (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp     TEXT NOT NULL,
                pair          TEXT NOT NULL,
                strategy      TEXT NOT NULL,
                direction     TEXT NOT NULL,
                entry_price   REAL NOT NULL,
                stop_price    REAL NOT NULL,
                target_price  REAL NOT NULL,
                score         INTEGER NOT NULL,
                confidence    TEXT NOT NULL,
                conditions    TEXT NOT NULL,
                warnings      TEXT
            );

            CREATE TABLE IF NOT EXISTS trades (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id     INTEGER REFERENCES signals(id),
                timestamp     TEXT NOT NULL,
                pair          TEXT NOT NULL,
                direction     TEXT NOT NULL,
                entry_price   REAL NOT NULL,
                stop_price    REAL NOT NULL,
                target_price  REAL NOT NULL,
                exit_price    REAL,
                result        TEXT,
                hold_minutes  INTEGER,
                pnl_pct       REAL,
                strategy      TEXT NOT NULL,
                notes         TEXT
            );

            CREATE TABLE IF NOT EXISTS quiz_results (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp     TEXT NOT NULL,
                topic         TEXT NOT NULL,
                correct       INTEGER NOT NULL,
                question      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS rule_breaks (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id      INTEGER REFERENCES trades(id),
                timestamp     TEXT NOT NULL,
                rule          TEXT NOT NULL,
                description   TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_signals_pair_ts ON signals(pair, timestamp);
            CREATE INDEX IF NOT EXISTS idx_trades_result   ON trades(result);
            CREATE INDEX IF NOT EXISTS idx_trades_ts       ON trades(timestamp);
            """
        )


# ────────────────────────────────────────────────────────────────────
# SIGNAL CRUD
# ────────────────────────────────────────────────────────────────────

def insert_signal(sig: dict[str, Any]) -> int:
    with _lock, get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO signals
               (timestamp, pair, strategy, direction, entry_price, stop_price,
                target_price, score, confidence, conditions, warnings)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                _now_ist(), sig["pair"], sig["strategy"], sig["direction"],
                sig["entry_price"], sig["stop_price"], sig["target_price"],
                sig["score"], sig["confidence"],
                json.dumps(sig.get("conditions", {}), default=str),
                json.dumps(sig.get("warnings", []), default=str),
            ),
        )
        return cur.lastrowid


def get_latest_signal(pair: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM signals WHERE pair=? ORDER BY id DESC LIMIT 1", (pair,)
        ).fetchone()
        return dict(row) if row else None


def get_signals_since(pair: str | None, hours: int) -> list[dict]:
    cutoff = (datetime.utcnow() + timedelta(hours=IST_OFFSET_HOURS - hours)).isoformat()
    sql = "SELECT * FROM signals WHERE timestamp >= ?"
    args: list = [cutoff]
    if pair:
        sql += " AND pair=?"
        args.append(pair)
    sql += " ORDER BY id DESC"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


# ────────────────────────────────────────────────────────────────────
# TRADE CRUD
# ────────────────────────────────────────────────────────────────────

def insert_trade(t: dict[str, Any]) -> int:
    with _lock, get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO trades
               (signal_id, timestamp, pair, direction, entry_price, stop_price,
                target_price, exit_price, result, hold_minutes, pnl_pct,
                strategy, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                t.get("signal_id"), _now_ist(), t["pair"], t["direction"],
                t["entry_price"], t["stop_price"], t["target_price"],
                t.get("exit_price"), t.get("result", "OPEN"), t.get("hold_minutes"),
                t.get("pnl_pct"), t["strategy"], t.get("notes", ""),
            ),
        )
        return cur.lastrowid


def update_trade_result(trade_id: int, result: str, exit_price: float | None,
                         pnl_pct: float | None, hold_minutes: int | None) -> bool:
    with _lock, get_conn() as conn:
        cur = conn.execute(
            """UPDATE trades SET result=?, exit_price=?, pnl_pct=?, hold_minutes=?
               WHERE id=?""",
            (result, exit_price, pnl_pct, hold_minutes, trade_id),
        )
        return cur.rowcount > 0


def get_trade(trade_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
        return dict(row) if row else None


def get_open_trades() -> list[dict]:
    with get_conn() as conn:
        return [
            dict(r) for r in conn.execute(
                "SELECT * FROM trades WHERE result='OPEN' ORDER BY id DESC"
            ).fetchall()
        ]


def get_trades_since(hours: int) -> list[dict]:
    cutoff = (datetime.utcnow() + timedelta(hours=IST_OFFSET_HOURS - hours)).isoformat()
    with get_conn() as conn:
        return [
            dict(r) for r in conn.execute(
                "SELECT * FROM trades WHERE timestamp >= ? ORDER BY id DESC", (cutoff,)
            ).fetchall()
        ]


def get_all_trades() -> list[dict]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM trades ORDER BY id ASC").fetchall()]


# ────────────────────────────────────────────────────────────────────
# RULE BREAKS
# ────────────────────────────────────────────────────────────────────

def insert_rule_break(trade_id: int, rule: str, description: str) -> int:
    with _lock, get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO rule_breaks (trade_id, timestamp, rule, description) VALUES (?,?,?,?)",
            (trade_id, _now_ist(), rule, description),
        )
        return cur.lastrowid


def get_rule_breaks(trade_id: int | None = None) -> list[dict]:
    with get_conn() as conn:
        if trade_id:
            rows = conn.execute(
                "SELECT * FROM rule_breaks WHERE trade_id=? ORDER BY id DESC", (trade_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM rule_breaks ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


# ────────────────────────────────────────────────────────────────────
# QUIZ
# ────────────────────────────────────────────────────────────────────

def insert_quiz(topic: str, correct: bool, question: str) -> int:
    with _lock, get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO quiz_results (timestamp, topic, correct, question) VALUES (?,?,?,?)",
            (_now_ist(), topic, 1 if correct else 0, question),
        )
        return cur.lastrowid


def get_quiz_stats() -> dict:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM quiz_results").fetchone()[0]
        correct = conn.execute(
            "SELECT COUNT(*) FROM quiz_results WHERE correct=1"
        ).fetchone()[0]
        topics = conn.execute(
            "SELECT topic, COUNT(*) AS n, SUM(correct) AS c FROM quiz_results GROUP BY topic"
        ).fetchall()
        return {
            "total": total, "correct": correct,
            "by_topic": {r["topic"]: {"n": r["n"], "c": r["c"]} for r in topics},
        }
