"""The live loop behind the window.

The engine is single-threaded and stays that way: one background thread runs
it, and everything the window asks for (stop, start, buy by hand, save
settings...) is queued and done by that same thread between steps. The window
only reads `snapshot()`, a plain dict rebuilt after every step and command.
"""
import collections
import copy
import logging
import os
import queue
import threading
import time
from datetime import datetime, timedelta, timezone

from . import clock
from .state import iso
from .ui import ui_of

log = logging.getLogger("ubot")


class Ring(logging.Handler):
    """The last lines of the log, for the window."""

    def __init__(self, n=200):
        super().__init__(logging.INFO)
        self.lines = collections.deque(maxlen=n)

    def emit(self, record):
        try:
            t = datetime.fromtimestamp(record.created).strftime("%H:%M")
            self.lines.append((t, record.levelname, record.getMessage()))
        except Exception:
            pass


class Live:
    def __init__(self, engine, cfg_path, save_cfg, ring, poll_seconds=None):
        self.eng = engine
        self.cfg_path = cfg_path
        self.save_cfg = save_cfg          # (cfg, path) -> None, writes webull.toml
        self.ring = ring
        self.q = queue.Queue()
        self.lock = threading.Lock()
        self._snap = {}
        self.selected = (engine.cfg.symbols or ("",))[0]
        self.quotes = {}
        self.chart = {}
        self.stop_event = threading.Event()
        self.thread = None
        self.poll = poll_seconds
        self.ui = ui_of(engine.strategy)

    # -- the window's side -------------------------------------------------
    def ask(self, name, *args, wait=False, timeout=60):
        """Queue a command. With wait=True, block until it is done and return its result."""
        box = {"done": threading.Event(), "out": None}
        self.q.put((name, args, box))
        if wait:
            box["done"].wait(timeout)
            return box["out"]
        return None

    def snapshot(self):
        with self.lock:
            return self._snap

    def start(self):
        self.thread = threading.Thread(target=self.loop, name="ubot-engine", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.q.put(("_wake", (), {"done": threading.Event(), "out": None}))
        if self.thread:
            self.thread.join(timeout=30)

    # -- the engine's side -------------------------------------------------
    def loop(self):
        eng = self.eng
        now = datetime.now(timezone.utc)
        try:
            eng.recover(now)
            eng.strategy.on_start(eng.ctx)
        except Exception:
            log.exception("start-up check failed")
        last_beat = None
        fails = 0
        next_step = 0.0
        while not self.stop_event.is_set():
            now = datetime.now(timezone.utc)
            if time.monotonic() >= next_step:
                try:
                    eng.step(now)
                    self.refresh(now)
                    fails = 0
                except Exception:
                    fails += 1
                    log.exception("step failed (%d in a row)", fails)
                if last_beat is None or now - last_beat > timedelta(hours=12):
                    r = eng.broker.heartbeat()
                    if not r.ok:
                        log.warning("Webull did not answer the daily check: %s %s - if the token expired, "
                                    "restart the program and approve it in the Webull app", r.code, r.message)
                    last_beat = now
                wait = (self.poll or eng.cfg.poll_seconds) if clock.regular_open(now) else 60
                next_step = time.monotonic() + min(300, wait * (2 ** min(fails, 4)))
            try:
                name, args, box = self.q.get(timeout=max(0.2, min(1.0, next_step - time.monotonic())))
            except queue.Empty:
                continue
            try:
                box["out"] = getattr(self, "do_" + name)(datetime.now(timezone.utc), *args)
            except Exception as e:
                log.exception("%s failed", name)
                box["out"] = str(e) or "failed"
            finally:
                box["done"].set()
            try:
                self.refresh(datetime.now(timezone.utc), quick=True)
            except Exception:
                log.exception("could not refresh the window")

    # commands (run on the engine thread) ------------------------------------
    def do__wake(self, now):
        return None

    def do_run(self, now, mode):
        self.eng.set_run(mode, now)
        return ""

    def do_buy(self, now, symbol, qty, stop, target, limit):
        return self.eng.manual_buy(symbol, qty, stop, target, now, limit=limit)

    def do_sell(self, now, symbol):
        return self.eng.manual_sell(symbol, now)

    def do_press(self, now, button):
        self.eng.press(button, now)
        return ""

    def do_select(self, now, symbol):
        self.selected = symbol
        self.load_chart(symbol, now, force=True)
        return ""

    def do_save(self, now, cfg):
        """New settings: write the file first, then use them - at once, held positions included."""
        old = set(self.eng.cfg.symbols)
        unknown = [s for s in cfg.symbols if s not in old and not self.eng.broker.quote(s)]
        if unknown:
            return "Webull does not know " + ", ".join(unknown)
        self.save_cfg(cfg, self.cfg_path)
        self.eng.apply_config(cfg, now)
        if self.selected not in cfg.symbols and cfg.symbols:
            self.selected = cfg.symbols[0]
        log.info("settings saved")
        return ""

    def do_keys(self, now, key, secret, connect):
        """Try the new keys on a fresh connection; only a working pair replaces the old one."""
        cfg = copy.copy(self.eng.cfg)
        cfg.app_key, cfg.app_secret = key, secret
        try:
            broker = connect(cfg)
        except Exception as e:
            return str(e) or "could not connect"
        self.save_cfg(cfg, self.cfg_path)
        self.eng.cfg = cfg
        self.eng.broker = broker
        log.info("new keys tested and in use")
        return ""

    # -- what the window reads -------------------------------------------------
    def load_chart(self, symbol, now, force=False):
        if not symbol:
            return
        c = self.chart.get(symbol)
        if c and not force and (now - c["at"]).total_seconds() < 60:
            return
        bars = self.eng.broker.bars(symbol, "5m", 90) or []
        day = clock.et_date(now) if clock.is_trading_day(clock.et_date(now)) else None
        today = [b for b in bars if day and clock.et_date(b.time) == day]
        shown = today or bars[-78:]
        self.chart[symbol] = {"at": now, "bars": [(iso(b.time), b.close) for b in shown],
                              "today": bool(today), "day": clock.et_date(shown[-1].time).isoformat() if shown else ""}

    def refresh(self, now, quick=False):
        eng = self.eng
        st = eng.state
        if not quick:
            for s in set(eng.cfg.symbols) | set(st.positions()):
                q = eng.broker.quote(s)
                if q:
                    self.quotes[s] = q
            self.load_chart(self.selected, now)
            usd = eng.broker.usd()
            if usd:
                self.usd = usd
                hist = st.get("equity_days", []) or []
                d = clock.et_date(now).isoformat()
                total = usd[0] + usd[1]
                if hist and hist[-1][0] == d:
                    hist[-1] = [d, total]
                else:
                    hist.append([d, total])
                st.put("equity_days", hist[-60:])
        positions = []
        for s, p in st.positions().items():
            px = (self.quotes.get(s) or (None, None))[0]
            positions.append({"symbol": s, "qty": p["qty"], "entry": p["entry"], "last": px,
                              "pl": (px - p["entry"]) * p["qty"] if px is not None else None,
                              "stop": p["stop"], "target": p["target"], "by": p["opened_by"],
                              "opened_at": p["opened_at"], "closing": p["closing"]})
        day_start = iso(datetime.combine(clock.et_date(now), datetime.min.time(), clock.ET).astimezone(timezone.utc))
        since30 = iso(now - timedelta(days=30))
        trades30 = st.trades(since30)
        daily = {}
        for t in st.trades(iso(now - timedelta(days=20))):
            k = clock.et_date(datetime.fromisoformat(t["closed_at"])).isoformat()
            daily[k] = daily.get(k, 0.0) + t["pnl"]
        closed = [{"symbol": t["symbol"], "qty": t["qty"], "entry": t["entry"], "exit": t["exit"], "pnl": t["pnl"],
                   "reason": t["reason"], "by": t["opened_by"], "closed_at": t["closed_at"]}
                  for t in reversed(st.trades(iso(now - timedelta(days=90))))][:200]
        values = {}
        if self.ui.get("values"):
            for s, bars in list(eng.last_bars.items()):
                try:
                    got = eng.strategy.ui_values(eng.ctx, s, bars) or {}
                    values[s] = {k: got[k] for k in self.ui["values"] if k in got}
                except Exception:
                    log.exception("the program's ui_values failed for %s", s)
        sess = clock.session(clock.et_date(now))
        if clock.regular_open(now):
            mkt = ("open", sess[1].astimezone(clock.ET).strftime("%H:%M"))
        else:
            d = clock.et_date(now)
            nxt = d if sess and now < sess[0] else clock.next_trading_day(d)
            mkt = ("closed", clock.session(nxt)[0].astimezone(clock.ET).strftime("%a %H:%M"))
        snap = {
            "at": now, "et": now.astimezone(clock.ET).strftime("%H:%M:%S"),
            "run": eng.run_mode, "dry": eng.cfg.dry_run, "halted": eng.halted(), "market": mkt,
            "cfg": eng.cfg, "account": (getattr(eng.broker, "account_id", "") or "")[-4:],
            "usd": getattr(self, "usd", None), "equity_days": st.get("equity_days", []) or [],
            "realized_today": st.realized_since(day_start),
            "unrealized": sum(p["pl"] for p in positions if p["pl"] is not None),
            "daily": sorted(daily.items())[-12:],
            "trades30": [(t["pnl"],) for t in trades30], "in_use": eng.in_use(),
            "positions": positions, "closed": closed,
            "quotes": dict(self.quotes), "selected": self.selected,
            "chart": self.chart.get(self.selected, {}).get("bars", []),
            "chart_day": "" if self.chart.get(self.selected, {}).get("today") else self.chart.get(self.selected, {}).get("day", ""),
            "values": values, "log": list(self.ring.lines)[-120:], "status": eng.status,
        }
        with self.lock:
            self._snap = snap
