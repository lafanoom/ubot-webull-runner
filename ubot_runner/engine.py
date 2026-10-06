"""The engine: turns a strategy's intentions into orders, and keeps them safe.

One step does, in order:
  1. reconcile - ask the broker about every order still in flight, and act on fills
  2. protect   - every position with a stop has a resting STOP_LOSS at the broker
  3. targets   - a position whose price reached its target: cancel the stop, sell
  4. evaluate  - a new closed bar: call the strategy, then close -> open -> modify

Rules the strategy cannot switch off (all in this file):
- dry_run sends nothing; a STOP file next to the program sends nothing new;
- only symbols listed in webull.toml are traded (empty list = nothing);
- regular US hours only; long only; whole shares; one position per symbol;
- caps: dollars per buy, orders per day, open positions, daily loss;
- the stop always rests at the broker (it survives this program being off);
  a stop is only ever moved up;
- a refused order is logged and dropped, never fired again; "accepted" is not
  "filled" - every order is read back.
"""
import logging
import math
import re
import uuid
from datetime import datetime, time, timedelta, timezone
from types import MappingProxyType

from . import clock
from .broker import OrderReq, round_price
from .state import iso
from .strategy import Bars, Buy, MoveStop, Position, Sell

log = logging.getLogger("ubot")

CID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def tag_of(strategy):
    t = re.sub(r"[^A-Za-z0-9]", "", str(getattr(strategy, "TAG", "") or "demo"))[:10]
    return t or "demo"


class Ctx:
    """What a strategy sees. Everything it asks for is queued; the engine acts after on_bar returns."""

    def __init__(self, engine):
        self._e = engine
        self.inputs = MappingProxyType(dict(engine.cfg.inputs))
        self.state = engine.state.get("strategy", {}) or {}
        self.is_simulation = engine.sim
        self.now = None
        self._intents = []

    def position(self, symbol):
        r = self._e.state.position(symbol)
        return _pos(r) if r else None

    def positions(self):
        return {s: _pos(r) for s, r in self._e.state.positions().items()}

    def buy(self, symbol, qty=None, notional=None, stop=None, target=None, reason=""):
        self._intents.append(Buy(symbol, qty, notional, stop, target, reason))

    def sell(self, symbol, reason=""):
        self._intents.append(Sell(symbol, reason))

    def move_stop(self, symbol, price):
        self._intents.append(MoveStop(symbol, price))

    def accept(self, text):
        """Evidence line for the factory's checks. Silent on the customer's machine."""
        if self.is_simulation:
            print("[ACCEPT] " + str(text), flush=True)

    def log(self, text):
        log.info("%s", text)


def _pos(r):
    return Position(r["symbol"], r["qty"], r["entry"], r["stop"], r["target"],
                    datetime.fromisoformat(r["opened_at"]))


class Engine:
    def __init__(self, strategy, cfg, broker, state, sim=False, stop_file=None, sleep=None):
        self.strategy = strategy
        self.cfg = cfg
        self.broker = broker
        self.state = state
        self.sim = sim
        self.stop_file = stop_file
        self.sleep = sleep or (lambda s: None)
        self.tag = tag_of(strategy)
        self.ctx = Ctx(self)
        self._next_fetch = {}
        self._last_sync = None
        self._said = set()
        self.status = ""

    # -- helpers -----------------------------------------------------------
    def cid(self, kind):
        c = f"ub{self.tag}{kind}{uuid.uuid4().hex[:12]}"
        assert CID_RE.match(c), c
        return c

    def once(self, key, msg, *a):
        if key not in self._said:
            self._said.add(key)
            log.info(msg, *a)

    def halted(self):
        import os
        return bool(self.stop_file) and os.path.exists(self.stop_file)

    def market_open(self, now):
        return True if self.sim else clock.regular_open(now)

    def day(self, now):
        """Today's bookkeeping: ET date, its start in UTC, the equity it started with."""
        d = clock.et_date(now)
        rec = self.state.get("day")
        if not rec or rec.get("date") != d.isoformat():
            start = datetime.combine(d, time(0), clock.ET).astimezone(timezone.utc)
            usd = self.broker.usd()
            rec = {"date": d.isoformat(), "start": iso(start), "equity": (usd[0] + usd[1]) if usd else None}
            self.state.put("day", rec)
        return rec

    # -- 1. reconcile --------------------------------------------------------
    def recover(self, now):
        """After a restart: orders written but never answered are looked up by their id."""
        for o in self.state.live_orders():
            if o["status"] != "sending":
                continue
            info = self.broker.detail(o["cid"])
            if info is None:
                self.state.set_order(o["cid"], now, status="lost")
                log.warning("order %s for %s was never received by Webull - dropped", o["cid"], o["symbol"])
                p = self.state.position(o["symbol"])
                if p and o["kind"] == "stop" and p["stop_cid"] == o["cid"]:
                    self.state.set_position(o["symbol"], stop_cid=None)
                if p and o["kind"] == "exit" and p["exit_cid"] == o["cid"]:
                    self.state.set_position(o["symbol"], exit_cid=None, closing=None)
            elif info is not False:
                self.state.set_order(o["cid"], now, status="open")
        self.reconcile(now)

    def reconcile(self, now):
        for o in self.state.live_orders():
            if o["status"] == "sending":
                continue
            info = self.broker.detail(o["cid"])
            if not info:
                continue
            final = info.status in ("FILLED", "CANCELED", "FAILED")
            if not final:
                if info.status == "PARTIAL" and o["status"] != "partial":
                    self.state.set_order(o["cid"], now, status="partial", filled_qty=info.filled_qty)
                continue
            self.state.set_order(o["cid"], now, status=info.status.lower(), filled_qty=info.filled_qty,
                                 avg_price=info.avg_price)
            getattr(self, "_done_" + o["kind"])(o, info, now)

    def _done_entry(self, o, info, now):
        import json
        got = int(math.floor(info.filled_qty + 1e-9))
        if got <= 0:
            log.info("buy %s %s ended %s with nothing filled", o["qty"], o["symbol"], info.status)
            return
        extra = json.loads(o["extra"] or "{}")
        price = info.avg_price or extra.get("ref") or 0.0
        self.state.open_position(o["symbol"], got, price, extra.get("stop"), extra.get("target"), now)
        log.info("bought %d %s at %.2f%s", got, o["symbol"], price,
                 f" (asked {o['qty']})" if got != o["qty"] else "")
        self.protect(now)

    def _done_stop(self, o, info, now):
        p = self.state.position(o["symbol"])
        if not p or p["stop_cid"] != o["cid"]:
            return
        if info.status == "FILLED":
            pnl = self.state.close_position(o["symbol"], info.avg_price, "stop", now)
            log.info("stop filled: sold %s %s at %.2f, P/L %.2f USD", p["qty"], o["symbol"], info.avg_price or 0, pnl or 0)
            return
        self.state.set_position(o["symbol"], stop_cid=None)
        if p["closing"] == "cancel_stop":
            self._sell(o["symbol"], p["qty"], p["close_reason"] or "exit", now)
        else:
            log.warning("the stop of %s was cancelled outside this program - placing it again", o["symbol"])

    def _done_exit(self, o, info, now):
        p = self.state.position(o["symbol"])
        if not p or p["exit_cid"] != o["cid"]:
            return
        if info.status == "FILLED":
            pnl = self.state.close_position(o["symbol"], info.avg_price, p["close_reason"] or "exit", now)
            log.info("sold %s %s at %.2f (%s), P/L %.2f USD", p["qty"], o["symbol"], info.avg_price or 0,
                     p["close_reason"], pnl or 0)
        else:
            log.warning("sell of %s ended %s - the stop goes back on", o["symbol"], info.status)
            self.state.set_position(o["symbol"], exit_cid=None, closing=None)

    # -- broker sends --------------------------------------------------------
    def send(self, kind, req, now, stop_price=None, extra=None):
        """Write first, then send. Transient failures retry with the same id (the broker dedupes)."""
        self.state.add_order(req.cid, req.symbol, kind, req.side, req.qty, stop_price, extra, now)
        reply = None
        for attempt in range(3):
            reply = self.broker.place(req)
            if reply.ok or not reply.transient:
                break
            self.sleep(2 * (attempt + 1))
        if reply.ok:
            self.state.set_order(req.cid, now, status="open")
            return True
        self.state.set_order(req.cid, now, status="refused" if not reply.transient else "lost")
        log.warning("Webull refused %s %s %s: %s %s", req.side.lower(), req.qty, req.symbol, reply.code, reply.message)
        return False

    def can_send(self):
        return not self.cfg.dry_run and not self.halted()

    # -- 2. protect ----------------------------------------------------------
    def protect(self, now):
        if not self.can_send():
            return
        for s, p in self.state.positions().items():
            if p["stop"] is None or p["stop_cid"] or p["closing"]:
                continue
            req = OrderReq(self.cid("S"), s, "SELL", "STOP_LOSS", p["qty"], stop_price=round_price(p["stop"]), tif="GTC")
            if self.send("stop", req, now, stop_price=req.stop_price):
                self.state.set_position(s, stop_cid=req.cid)
                log.info("stop for %s %s rests at Webull: %.2f", p["qty"], s, req.stop_price)

    # -- 3. targets / exits --------------------------------------------------
    def watch_targets(self, now):
        if not self.can_send() or not self.market_open(now):
            return
        for s, p in self.state.positions().items():
            if p["target"] is None or p["closing"]:
                continue
            px = self.broker.last_price(s)
            if px is not None and px >= p["target"]:
                log.info("%s reached its target %.2f (last %.2f)", s, p["target"], px)
                self.exit(s, "target", now)

    def exit(self, symbol, reason, now):
        p = self.state.position(symbol)
        if not p or p["closing"]:
            return
        if not self.can_send() or not self.market_open(now):
            log.info("would sell %s (%s) - not sending now", symbol, reason)
            return
        if not p["stop_cid"]:
            self._sell(symbol, p["qty"], reason, now)
            return
        self.state.set_position(symbol, closing="cancel_stop", close_reason=reason)
        r = self.broker.cancel(p["stop_cid"])
        if not r.ok:
            info = self.broker.detail(p["stop_cid"])
            if not info or info.status not in ("CANCELED", "FILLED"):
                log.warning("could not cancel the stop of %s (%s) - keeping it", symbol, r.code)
                self.state.set_position(symbol, closing=None, close_reason=None)
                return
        # one resting sell per held share: the stop must be gone before the sale is accepted
        for i in range(8):
            info = self.broker.detail(p["stop_cid"])
            if info and info.status in ("CANCELED", "FILLED", "FAILED"):
                break
            self.sleep(1)
        self.reconcile(now)

    def _sell(self, symbol, qty, reason, now):
        req = OrderReq(self.cid("X"), symbol, "SELL", "MARKET", qty)
        self.state.set_position(symbol, closing="selling", close_reason=reason, exit_cid=req.cid)
        if not self.send("exit", req, now, extra={"reason": reason}):
            self.state.set_position(symbol, closing=None, exit_cid=None)
            self.protect(now)

    # -- outside changes -------------------------------------------------------
    def sync_positions(self, now):
        """Shares sold by hand in the Webull app are no longer this program's."""
        if self.sim or (self._last_sync and now - self._last_sync < timedelta(seconds=60)):
            return
        self._last_sync = now
        held = self.broker.positions()
        if held is None:
            return
        for s, p in self.state.positions().items():
            if p["closing"]:
                continue
            have = int(math.floor(held.get(s, 0.0) + 1e-9))
            if have >= p["qty"]:
                continue
            log.warning("%s: Webull shows %d shares, this program bought %d - following Webull", s, have, p["qty"])
            if have <= 0:
                if p["stop_cid"]:
                    self.broker.cancel(p["stop_cid"])
                self.state.close_position(s, None, "sold outside the program", now)
            else:
                self.state.set_position(s, qty=have)
                if p["stop_cid"]:
                    self.broker.replace(p["stop_cid"], qty=have)

    # -- 4. evaluate -----------------------------------------------------------
    def due(self, symbol, now):
        if not self.market_open(now):
            return False
        if self.strategy.BAR == "1d":
            s = clock.session(clock.et_date(now))
            return bool(s) and now >= s[0] + timedelta(minutes=self.cfg.daily_eval_delay_min)
        return now >= self._next_fetch.get(symbol, now)

    def evaluate(self, now):
        for symbol in self.cfg.symbols:
            if not self.due(symbol, now):
                continue
            if self.strategy.BAR == "1d" and self.state.get("bar:" + symbol) == iso(self._prev_session_day(now)):
                continue
            raw = self.broker.bars(symbol, self.strategy.BAR, self.strategy.WARMUP + 10)
            if raw is None:
                self._next_fetch[symbol] = now + timedelta(seconds=30)
                continue
            closed = [b for b in raw if clock.bar_closed(b.time, self.strategy.BAR, now)]
            if self.strategy.BAR != "1d" and closed:
                span = clock.SPANS[self.strategy.BAR]
                self._next_fetch[symbol] = max(now + timedelta(seconds=5), closed[-1].time + 2 * span + timedelta(seconds=3))
            self.run_bar(symbol, closed, now)

    def _prev_session_day(self, now):
        d = clock.et_date(now) - timedelta(days=1)
        while not clock.is_trading_day(d):
            d -= timedelta(days=1)
        return datetime.combine(d, time(0), clock.ET)

    def run_bar(self, symbol, closed, now):
        """Call the strategy if `closed` ends with a bar it has not seen. Returns True when called."""
        if len(closed) < self.strategy.WARMUP:
            self.once("warm:" + symbol, "%s: %d closed bars, the program needs %d - waiting", symbol,
                      len(closed), self.strategy.WARMUP)
            return False
        key = "bar:" + symbol
        last = iso(closed[-1].time)
        if self.state.get(key) == last:
            return False
        self.state.put(key, last)
        self.ctx.now = now
        self.ctx._intents = []
        try:
            self.strategy.on_bar(self.ctx, symbol, Bars(closed))
        except Exception:
            log.exception("the program's on_bar failed for %s - no orders from this bar", symbol)
            return True
        self.state.put("strategy", self.ctx.state)
        intents = self.ctx._intents
        order = {Sell: 0, Buy: 1, MoveStop: 2}
        for it in sorted(intents, key=lambda x: order[type(x)]):
            if it.symbol != symbol and it.symbol not in self.cfg.symbols:
                log.warning("ignored a request for %s: not in your symbols list", it.symbol)
                continue
            if isinstance(it, Sell):
                self.exit(it.symbol, it.reason or "program", now)
            elif isinstance(it, Buy):
                self.buy(it, closed[-1].close, now)
            else:
                self.move_stop(it, now)
        return True

    def buy(self, it, ref, now):
        s = it.symbol
        why = self.refuse_buy(it, ref, now)
        if why:
            log.info("not buying %s: %s", s, why)
            return
        qty = it.qty if it.qty is not None else int(math.floor(it.notional / ref))
        desc = f"{qty} {s} ~{qty * ref:.2f} USD" + (f" stop {it.stop:.2f}" if it.stop else "") + \
            (f" target {it.target:.2f}" if it.target else "") + (f" ({it.reason})" if it.reason else "")
        if self.cfg.dry_run:
            log.info("DRY RUN - would buy %s", desc)
            return
        req = OrderReq(self.cid("E"), s, "BUY", "MARKET", qty)
        extra = {"stop": round_price(it.stop) if it.stop else None,
                 "target": round_price(it.target) if it.target else None, "ref": ref, "reason": it.reason}
        if self.send("entry", req, now, extra=extra):
            log.info("buy sent: %s", desc)

    def refuse_buy(self, it, ref, now):
        lim = self.cfg.limits
        if self.halted():
            return "STOP file is present"
        if it.symbol not in self.cfg.symbols:
            return "not in your symbols list"
        if not self.market_open(now):
            return "market is closed"
        if self.state.position(it.symbol):
            return "already holding it"
        if any(o["symbol"] == it.symbol and o["kind"] == "entry" for o in self.state.live_orders()):
            return "a buy is already in flight"
        if (it.qty is None) == (it.notional is None):
            return "the program must give either qty or notional"
        if it.qty is not None and (not isinstance(it.qty, int) or it.qty <= 0):
            return "qty must be a whole number above 0"
        if it.notional is not None and not it.notional > 0:
            return "notional must be above 0"
        if not ref or ref <= 0:
            return "no price"
        qty = it.qty if it.qty is not None else int(math.floor(it.notional / ref))
        if qty <= 0:
            return f"{it.notional} USD is less than one share at {ref:.2f}"
        if qty * ref > lim.max_notional_per_order + 1e-9:
            return f"{qty * ref:.2f} USD is above max_notional_per_order ({lim.max_notional_per_order:g})"
        if it.stop is not None and not 0 < it.stop < ref:
            return f"stop {it.stop} must be below the price {ref:.2f}"
        if it.target is not None and not it.target > ref:
            return f"target {it.target} must be above the price {ref:.2f}"
        open_n = len(self.state.positions()) + sum(1 for o in self.state.live_orders() if o["kind"] == "entry")
        if open_n >= lim.max_open_positions:
            return f"max_open_positions ({lim.max_open_positions}) reached"
        day = self.day(now)
        if self.state.orders_sent_since(day["start"]) >= lim.max_orders_per_day:
            return f"max_orders_per_day ({lim.max_orders_per_day}) reached"
        if day["equity"] and lim.daily_loss_pct > 0:
            lost = -self.state.realized_since(day["start"])
            if lost >= day["equity"] * lim.daily_loss_pct / 100:
                return f"today's loss {lost:.2f} USD reached daily_loss_pct ({lim.daily_loss_pct:g}%)"
        usd = self.broker.usd()
        if usd and qty * ref > usd[2]:
            return f"buying power {usd[2]:.2f} USD is not enough"
        return ""

    def move_stop(self, it, now):
        p = self.state.position(it.symbol)
        if not p or p["closing"]:
            return
        new = round_price(it.price)
        if p["stop"] is not None and new <= p["stop"]:
            return                                    # a stop only moves up
        if self.cfg.dry_run:
            log.info("DRY RUN - would move the stop of %s to %.2f", it.symbol, new)
            return
        if not p["stop_cid"]:
            self.state.set_position(it.symbol, stop=new)
            self.protect(now)
            return
        if not self.can_send():
            return
        r = self.broker.replace(p["stop_cid"], p["qty"], stop_price=new)
        if not r.ok and r.transient:
            # a timeout does not say whether the change happened: read the order back
            self.sleep(2)
            info = self.broker.detail(p["stop_cid"])
            if info and info.stop_price is not None and abs(info.stop_price - new) < 1e-6:
                r = r.__class__(True)
            elif info and info.status == "OPEN":
                r = self.broker.replace(p["stop_cid"], p["qty"], stop_price=new)
        if r.ok:
            self.state.set_position(it.symbol, stop=new)
            self.state.set_order(p["stop_cid"], now, stop_price=new)
            log.info("stop of %s moved up to %.2f", it.symbol, new)
        else:
            log.warning("could not move the stop of %s: %s %s", it.symbol, r.code, r.message)

    # -- one live step ---------------------------------------------------------
    def step(self, now):
        self.reconcile(now)
        self.sync_positions(now)
        self.protect(now)
        self.watch_targets(now)
        self.evaluate(now)
        self.reconcile(now)
        self.status = self.status_line(now)

    def status_line(self, now):
        mode = "STOPPED (STOP file)" if self.halted() else "DRY RUN" if self.cfg.dry_run else "LIVE"
        mkt = "open" if clock.regular_open(now) else "closed"
        pos = ", ".join(f"{s} {p['qty']}" + (f" stop {p['stop']:.2f}" if p["stop"] else "") +
                        (f" target {p['target']:.2f}" if p["target"] else "") +
                        (f" [{p['closing']}]" if p["closing"] else "")
                        for s, p in self.state.positions().items()) or "no positions"
        return f"{mode} | market {mkt} | {pos}"
