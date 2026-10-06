"""What the program must remember across a restart, in sqlite next to it.

- orders: every order this program sent (written *before* it is sent, so a crash
  between "sent" and "answered" is found again on the next start);
- positions: shares this program bought, with their stop/target;
- trades: closed round trips (for the daily loss limit and the log);
- kv: strategy state (ctx.state) and the runner's own markers.
"""
import json
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
  cid TEXT PRIMARY KEY, symbol TEXT NOT NULL, kind TEXT NOT NULL, side TEXT NOT NULL,
  qty INTEGER NOT NULL, status TEXT NOT NULL, filled_qty REAL NOT NULL DEFAULT 0,
  avg_price REAL, stop_price REAL, extra TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS positions (
  symbol TEXT PRIMARY KEY, qty INTEGER NOT NULL, entry REAL NOT NULL, stop REAL, target REAL,
  stop_cid TEXT, exit_cid TEXT, closing TEXT, close_reason TEXT, opened_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, qty INTEGER NOT NULL,
  entry REAL NOT NULL, exit REAL NOT NULL, pnl REAL NOT NULL, reason TEXT,
  opened_at TEXT NOT NULL, closed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

LIVE = ("sending", "open", "partial")


def iso(t=None):
    return (t or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")


class State:
    def __init__(self, path=":memory:"):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self):
        self.db.close()

    # -- kv ----------------------------------------------------------------
    def get(self, key, default=None):
        r = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    def put(self, key, value):
        self.db.execute("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, json.dumps(value)))
        self.db.commit()

    # -- orders ------------------------------------------------------------
    def add_order(self, cid, symbol, kind, side, qty, stop_price=None, extra=None, now=None):
        t = iso(now)
        self.db.execute("INSERT INTO orders(cid,symbol,kind,side,qty,status,stop_price,extra,created_at,updated_at)"
                        " VALUES(?,?,?,?,?,'sending',?,?,?,?)",
                        (cid, symbol, kind, side, qty, stop_price, json.dumps(extra or {}), t, t))
        self.db.commit()

    def set_order(self, cid, now=None, **f):
        if not f:
            return
        cols = ", ".join(f"{k}=?" for k in f)
        self.db.execute(f"UPDATE orders SET {cols}, updated_at=? WHERE cid=?", (*f.values(), iso(now), cid))
        self.db.commit()

    def order(self, cid):
        return self.db.execute("SELECT * FROM orders WHERE cid=?", (cid,)).fetchone()

    def live_orders(self):
        return self.db.execute("SELECT * FROM orders WHERE status IN ('sending','open','partial') ORDER BY created_at").fetchall()

    def orders_sent_since(self, t):
        return self.db.execute("SELECT COUNT(*) FROM orders WHERE created_at>=? AND status<>'dry'", (t,)).fetchone()[0]

    # -- positions ---------------------------------------------------------
    def positions(self):
        return {r["symbol"]: r for r in self.db.execute("SELECT * FROM positions ORDER BY opened_at")}

    def position(self, symbol):
        return self.db.execute("SELECT * FROM positions WHERE symbol=?", (symbol,)).fetchone()

    def open_position(self, symbol, qty, entry, stop, target, now=None):
        self.db.execute("INSERT INTO positions(symbol,qty,entry,stop,target,opened_at) VALUES(?,?,?,?,?,?)"
                        " ON CONFLICT(symbol) DO UPDATE SET entry=(entry*qty+excluded.entry*excluded.qty)/(qty+excluded.qty),"
                        " qty=qty+excluded.qty",
                        (symbol, qty, entry, stop, target, iso(now)))
        self.db.commit()

    def set_position(self, symbol, **f):
        cols = ", ".join(f"{k}=?" for k in f)
        self.db.execute(f"UPDATE positions SET {cols} WHERE symbol=?", (*f.values(), symbol))
        self.db.commit()

    def close_position(self, symbol, exit_price, reason, now=None):
        p = self.position(symbol)
        if not p:
            return None
        pnl = (exit_price - p["entry"]) * p["qty"] if exit_price is not None else 0.0
        self.db.execute("INSERT INTO trades(symbol,qty,entry,exit,pnl,reason,opened_at,closed_at) VALUES(?,?,?,?,?,?,?,?)",
                        (symbol, p["qty"], p["entry"], exit_price if exit_price is not None else p["entry"],
                         pnl, reason, p["opened_at"], iso(now)))
        self.db.execute("DELETE FROM positions WHERE symbol=?", (symbol,))
        self.db.commit()
        return pnl

    def realized_since(self, t):
        return self.db.execute("SELECT COALESCE(SUM(pnl),0) FROM trades WHERE closed_at>=?", (t,)).fetchone()[0]

    def trades(self):
        return self.db.execute("SELECT * FROM trades ORDER BY id").fetchall()
