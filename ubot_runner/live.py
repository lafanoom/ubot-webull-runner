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
from .broker import CHART_SIZES, DAILY_UP
from .plot import plot_of, series, warmup
from .state import iso
from .strategy import Bars
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
        self.sparks = {}                  # symbol -> (loaded at, [closes]) for the watch list's small lines
        self.stop_event = threading.Event()
        self.thread = None
        self.poll = poll_seconds
        self.ui = ui_of(engine.strategy)
        self.plot = plot_of(engine.strategy)
        bar = getattr(engine.strategy, "BAR", "1d")
        self.chart_tf = bar if bar in CHART_SIZES else "1d"

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

    def do_chart_tf(self, now, tf):
        if tf not in CHART_SIZES:
            return "unknown bar size"
        self.chart_tf = tf
        self.load_chart(self.selected, now, force=True)
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
    CHART_BARS = 120                      # bars on screen
    CHART_EVERY = {"1m": 30, "1d": 300, "1w": 600, "1mo": 600}   # seconds between reloads (default 60)

    def load_chart(self, symbol, now, force=False):
        """The chart of one symbol at the chosen bar size, with the strategy's indicator lines
        worked out over extra bars loaded before the shown ones (one call)."""
        if not symbol:
            return
        tf = self.chart_tf
        c = self.chart.get(symbol)
        if c and not force and c["tf"] == tf and                 (now - c["at"]).total_seconds() < self.CHART_EVERY.get(tf, 60):
            return
        inputs = dict(self.eng.cfg.inputs)
        extra = warmup(self.plot, inputs)
        bars = self.eng.broker.bars(symbol, tf, self.CHART_BARS + extra) or []
        shown = bars[-self.CHART_BARS:]
        cut = len(bars) - len(shown)
        lines = []
        if self.plot and shown:
            custom = {}
            if any(p["kind"] == "line" for p in self.plot):
                try:
                    custom = self.eng.strategy.plot(self.eng.ctx, symbol, Bars(bars)) or {}
                except Exception:
                    log.exception("the program's plot() failed for %s", symbol)
            for n, item in enumerate(self.plot):
                try:
                    for label, pane, vals, levels in series(item, bars, inputs, custom if isinstance(custom, dict) else {}):
                        lines.append({"label": label, "pane": pane, "values": vals[cut:], "levels": levels,
                                      "group": n, "kind": item["kind"]})
                except Exception:
                    log.exception("could not work out %s for the chart", item.get("kind"))
        self.chart[symbol] = {"at": now, "tf": tf, "bars": [(iso(b.time), b.close) for b in shown],
                              "plots": lines}

    def chart_marks(self, symbol, now):
        """Where this program bought and sold the symbol, on the shown bars: each mark sits on the
        bar the order filled in (the last bar that started at or before it)."""
        c = self.chart.get(symbol)
        if not c or not c["bars"]:
            return []
        times = [datetime.fromisoformat(t) for t, _ in c["bars"]]
        first = times[0]

        def at(t):
            try:
                t = datetime.fromisoformat(t)
            except (TypeError, ValueError):
                return None
            if t < first:
                return None
            i = len(times) - 1
            while i > 0 and times[i] > t:
                i -= 1
            return i

        out = []
        for t in self.eng.state.trades(iso(first - timedelta(days=40 if c["tf"] in DAILY_UP else 1))):
            if t["symbol"] != symbol:
                continue
            a, b = at(t["opened_at"]), at(t["closed_at"])
            if b is None:
                continue
            out.append({"side": "buy", "i": a, "px": t["entry"], "qty": t["qty"], "t": t["opened_at"],
                        "by": t["opened_by"]})
            out.append({"side": "sell", "i": b, "px": t["exit"], "qty": t["qty"], "t": t["closed_at"],
                        "pnl": t["pnl"], "reason": t["reason"], "from": a, "from_px": t["entry"]})
        p = self.eng.state.position(symbol)
        if p:
            i = at(p["opened_at"])
            if i is not None:
                out.append({"side": "buy", "i": i, "px": p["entry"], "qty": p["qty"], "t": p["opened_at"],
                            "by": p["opened_by"]})
        return [m for m in out if m["i"] is not None]

    SPARK_EVERY = 300                     # seconds between refreshes of one symbol's small line

    def load_spark(self, symbol, now):
        """The last 30 closes of one watched symbol, refreshed a few times an hour (one call each)."""
        got = self.sparks.get(symbol)
        if got and (now - got[0]).total_seconds() < self.SPARK_EVERY:
            return
        try:
            bars = self.eng.broker.bars(symbol, "5m", 30) or []
        except Exception:
            bars = []
        self.sparks[symbol] = (now, [b.close for b in bars[-30:]])

    def refresh(self, now, quick=False):
        eng = self.eng
        st = eng.state
        if not quick:
            for s in set(eng.cfg.symbols) | set(st.positions()):
                q = eng.broker.quote(s)
                if q:
                    self.quotes[s] = q
            self.load_chart(self.selected, now)
            for s in eng.cfg.symbols:
                self.load_spark(s, now)
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
            "sparks": {s: v[1] for s, v in self.sparks.items()},
            "chart": self.chart.get(self.selected, {}).get("bars", []),
            "chart_tf": self.chart.get(self.selected, {}).get("tf", self.chart_tf),
            "plots": self.chart.get(self.selected, {}).get("plots", []),
            "has_plot": bool(self.plot),
            "marks": self.chart_marks(self.selected, now),
            "values": values, "log": list(self.ring.lines)[-120:], "status": eng.status,
        }
        with self.lock:
            self._snap = snap
