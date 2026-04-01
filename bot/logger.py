"""SQLite logging for trades, events, and PnL tracking."""

import json
import time
import sqlite3
import logging
import datetime
from pathlib import Path
from .config import DB_PATH, STATE_PATH

logger = logging.getLogger("mm.logger")


class TradeLogger:
    def __init__(self, db_path: str = DB_PATH):
        self.conn = sqlite3.connect(db_path, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self._create_tables()
        self._events: list[dict] = []
        self._max_events = 100

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS fills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                size REAL NOT NULL,
                fee REAL NOT NULL,
                oid INTEGER,
                pnl REAL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pnl_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                realized_pnl REAL NOT NULL,
                unrealized_pnl REAL NOT NULL,
                total_value REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_fills_ts ON fills(timestamp);
            CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp);
        """)
        self.conn.commit()

    def log_fill(self, side: str, price: float, size: float, fee: float, oid: int, pnl: float = 0):
        ts = int(time.time() * 1000)
        self.conn.execute(
            "INSERT INTO fills (timestamp, side, price, size, fee, oid, pnl) VALUES (?,?,?,?,?,?,?)",
            (ts, side, price, size, fee, oid, pnl)
        )
        self.conn.commit()
        self.add_event("success", f"Fill: {'BUY' if side == 'B' else 'SELL'} {size:.2f} XMR1 @ {price:.2f}")

    def add_event(self, level: str, message: str):
        ts = int(time.time() * 1000)
        self.conn.execute(
            "INSERT INTO events (timestamp, level, message) VALUES (?,?,?)",
            (ts, level, message)
        )
        self.conn.commit()
        now = datetime.datetime.now().strftime("%H:%M:%S")
        self._events.insert(0, {"time": now, "level": level, "message": message})
        self._events = self._events[:self._max_events]

    def get_recent_fills(self, limit: int = 20) -> list[dict]:
        cursor = self.conn.execute(
            "SELECT timestamp, side, price, size, fee, pnl FROM fills ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        )
        fills = []
        for row in cursor.fetchall():
            ts = datetime.datetime.fromtimestamp(row[0] / 1000).strftime("%H:%M:%S")
            fills.append({
                "time": ts,
                "side": "buy" if row[1] == "B" else "sell",
                "price": row[2],
                "size": row[3],
                "fee": row[4],
                "pnl": row[5],
            })
        return fills

    def get_recent_events(self) -> list[dict]:
        return self._events[:50]

    def get_pnl_24h(self) -> float:
        cutoff = int((time.time() - 86400) * 1000)
        cursor = self.conn.execute(
            "SELECT COALESCE(SUM(pnl), 0) FROM fills WHERE timestamp > ?",
            (cutoff,)
        )
        return cursor.fetchone()[0]

    def get_fill_count_24h(self) -> int:
        cutoff = int((time.time() - 86400) * 1000)
        cursor = self.conn.execute(
            "SELECT COUNT(*) FROM fills WHERE timestamp > ?",
            (cutoff,)
        )
        return cursor.fetchone()[0]

    def get_volume_24h(self) -> float:
        cutoff = int((time.time() - 86400) * 1000)
        cursor = self.conn.execute(
            "SELECT COALESCE(SUM(price * size), 0) FROM fills WHERE timestamp > ?",
            (cutoff,)
        )
        return cursor.fetchone()[0]

    def write_state(self, state: dict):
        """Write current state to state.json for dashboard polling."""
        try:
            with open(STATE_PATH, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to write state.json: {e}")
