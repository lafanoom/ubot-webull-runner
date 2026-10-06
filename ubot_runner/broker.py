"""Talking to the broker.

`WebullBroker` wraps the official SDK (webull-openapi-python-sdk). `FakeBroker`
is the same interface over bars in memory, for simulation and tests.

Facts from the Webull Thailand test environment (06/10/2026) that shape this:
- cash accounts only; order types MARKET, LIMIT, STOP_LOSS, STOP_LOSS_LIMIT;
- no bracket / OCO; one resting sell per held share (a second is refused with
  OPENAPI_SELL_QTY_EXCEED_AVAILABLE_QTY) - so the stop rests at the broker and
  the runner watches the target itself;
- a repeated client_order_id is refused (OPENAPI_REPEAT_REQUEST) - safe retries;
- preview_order says yes to things place_order refuses; only place tells the truth.
"""
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .strategy import Bar

log = logging.getLogger("ubot")

TIMESPAN = {"1d": "D", "1h": "M60", "30m": "M30", "15m": "M15", "5m": "M5"}


@dataclass
class OrderReq:
    cid: str
    symbol: str
    side: str                 # BUY | SELL
    order_type: str           # MARKET | LIMIT | STOP_LOSS
    qty: int
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    tif: str = "DAY"          # DAY | GTC


@dataclass
class Reply:
    ok: bool
    code: str = ""
    message: str = ""
    transient: bool = False   # worth retrying with the same order id


@dataclass
class OrderInfo:
    cid: str
    status: str               # OPEN | PARTIAL | FILLED | CANCELED | FAILED
    filled_qty: float = 0.0
    avg_price: Optional[float] = None
    total_qty: float = 0.0
    stop_price: Optional[float] = None


STATUS = {"PENDING": "OPEN", "SUBMITTED": "OPEN", "PARTIAL_FILLED": "PARTIAL", "FILLED": "FILLED",
          "CANCELED": "CANCELED", "CANCELLED": "CANCELED", "FAILED": "FAILED", "REJECTED": "FAILED"}

_ERR = re.compile(r"HTTP Status:\s*(\d+),\s*Code:\s*([A-Za-z0-9_]+),\s*Msg:\s*(.*?)(?:,\s*RequestID|$)", re.S)


def price_text(p):
    """Two decimals from $1 up, four below (no sub-penny prices above $1)."""
    return f"{p:.2f}" if p >= 1 else f"{p:.4f}"


def round_price(p):
    return round(p, 2) if p >= 1 else round(p, 4)


def _f(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


class WebullBroker:
    def __init__(self, cfg, token_dir):
        try:
            from webull.core.client import ApiClient
            from webull.data.data_client import DataClient
            from webull.trade.trade_client import TradeClient
        except ImportError:
            raise SystemExit("The Webull SDK is not installed. Run:  pip install webull-openapi-python-sdk==3.0.2")
        self.cfg = cfg
        api = ApiClient(cfg.app_key, cfg.app_secret, "th", timeout=20)
        api.add_endpoint("th", cfg.host)
        api.set_token_dir(token_dir)
        # The SDK's own error log dumps whole requests - headers included, and on a
        # production key that means the access token. Silence it; _call() logs the
        # broker's code and message instead.
        sdk = logging.getLogger("webull")
        sdk.handlers.clear()
        sdk.addHandler(logging.NullHandler())
        sdk.propagate = False
        sdk.setLevel(logging.CRITICAL + 1)
        api._stream_logger_set = True
        api._file_logger_set = True
        # On a production key the first run creates a token and Webull sends an SMS;
        # the SDK waits up to 5 minutes for it to be approved in the Webull app.
        self.trade = TradeClient(api)
        self.data = DataClient(api)
        self.account_id = cfg.account_id or None
        self.account_type = None

    # -- low level ---------------------------------------------------------
    def _call(self, fn, *a, **kw):
        """(Reply, body). Never raises for broker answers."""
        try:
            res = fn(*a, **kw)
        except Exception as e:
            m = _ERR.search(str(e))
            if m:
                status = int(m.group(1))
                return Reply(False, m.group(2), m.group(3).strip(), transient=status == 429 or status >= 500), None
            return Reply(False, type(e).__name__, str(e)[:300], transient=True), None
        try:
            body = res.json()
        except Exception:
            body = None
        if getattr(res, "status_code", 200) >= 400:
            return Reply(False, str(res.status_code), str(body)[:300], transient=res.status_code >= 500), body
        return Reply(True), body

    # -- account -----------------------------------------------------------
    def connect(self):
        r, body = self._call(self.trade.account_v2.get_account_list)
        if not r.ok:
            raise RuntimeError(f"could not read your accounts: {r.code} {r.message}")
        rows = body if isinstance(body, list) else (body or {}).get("data", [])
        pick = None
        for a in rows:
            if self.account_id and str(a.get("account_id")) == self.account_id:
                pick = a
            elif not self.account_id and a.get("account_type") == "CASH" and pick is None:
                pick = a
        if not pick:
            raise RuntimeError("the account in account_id was not found" if self.account_id
                               else "no cash account found on this key")
        self.account_id = str(pick["account_id"])
        self.account_type = pick.get("account_type")
        return self.account_type

    def usd(self):
        """(cash_balance, market_value, buying_power) in USD, or None."""
        r, body = self._call(self.trade.account_v2.get_account_balance, self.account_id)
        if not r.ok or not isinstance(body, dict):
            return None
        for a in body.get("account_currency_assets", []):
            if a.get("currency") == "USD":
                return _f(a.get("cash_balance"), 0.0), _f(a.get("market_value"), 0.0), _f(a.get("buying_power"), 0.0)
        return 0.0, 0.0, 0.0

    def positions(self):
        """{symbol: shares} for US stocks, or None when it could not be read."""
        r, body = self._call(self.trade.account_v2.get_account_position, self.account_id)
        if not r.ok:
            return None
        rows = body if isinstance(body, list) else (body or {}).get("data", [])
        out = {}
        for p in rows:
            if p.get("currency") == "USD" and p.get("instrument_type") == "EQUITY":
                out[p["symbol"]] = out.get(p["symbol"], 0.0) + _f(p.get("quantity"), 0.0)
        return out

    def heartbeat(self):
        """Any call keeps the token alive (it dies after 15 days without calls)."""
        r, _ = self._call(self.trade.account_v2.get_account_list)
        return r

    # -- market data -------------------------------------------------------
    def bars(self, symbol, bar, count):
        kw = {"count": str(min(max(count, 1), 1200))}
        if bar != "1d":
            kw["trading_sessions"] = "RTH"
        r, body = self._call(self.data.market_data.get_history_bar, symbol, "US_STOCK", TIMESPAN[bar], **kw)
        if not r.ok:
            log.warning("bars %s: %s %s", symbol, r.code, r.message)
            return None
        rows = body.get("result", []) if isinstance(body, dict) else body if isinstance(body, list) else []
        if rows and isinstance(rows[0], dict) and isinstance(rows[0].get("result"), list):
            rows = rows[0]["result"]
        out = []
        for b in rows or []:
            try:
                t = datetime.strptime(str(b["time"]), "%Y-%m-%dT%H:%M:%S.%f%z").astimezone(timezone.utc)
                out.append(Bar(t, float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"]),
                               _f(b.get("volume"), 0.0)))
            except (KeyError, ValueError, TypeError):
                continue
        out.sort(key=lambda x: x.time)
        return out

    def last_price(self, symbol):
        r, body = self._call(self.data.market_data.get_snapshot, symbol, "US_STOCK")
        if not r.ok:
            return None
        rows = body if isinstance(body, list) else (body or {}).get("result", [body]) if isinstance(body, dict) else []
        for s in rows or []:
            if isinstance(s, dict) and s.get("symbol") == symbol:
                return _f(s.get("price")) or _f(s.get("close"))
        return None

    # -- orders ------------------------------------------------------------
    def _order(self, o: OrderReq):
        d = {"combo_type": "NORMAL", "client_order_id": o.cid, "symbol": o.symbol, "instrument_type": "EQUITY",
             "market": "US", "side": o.side, "order_type": o.order_type, "time_in_force": o.tif,
             "entrust_type": "QTY", "support_trading_session": "CORE", "quantity": str(int(o.qty))}
        if o.limit_price is not None:
            d["limit_price"] = price_text(o.limit_price)
        if o.stop_price is not None:
            d["stop_price"] = price_text(o.stop_price)
        return d

    def place(self, o: OrderReq):
        r, _ = self._call(self.trade.order_v3.place_order, self.account_id, [self._order(o)])
        if not r.ok and r.code == "OPENAPI_REPEAT_REQUEST":
            return Reply(True, r.code, "already received")   # an earlier try got through
        return r

    def replace(self, cid, qty, stop_price=None, limit_price=None):
        # Webull wants the quantity on every change (OPENAPI_PARAM_ERR "quantity is empty")
        ch = {"client_order_id": cid, "quantity": str(int(qty))}
        if stop_price is not None:
            ch["stop_price"] = price_text(stop_price)
        if limit_price is not None:
            ch["limit_price"] = price_text(limit_price)
        r, _ = self._call(self.trade.order_v3.replace_order, self.account_id, [ch])
        return r

    def cancel(self, cid):
        r, _ = self._call(self.trade.order_v3.cancel_order, self.account_id, cid)
        return r

    def detail(self, cid):
        """OrderInfo, None when the broker does not know the id, or False when it could not ask."""
        r, body = self._call(self.trade.order_v3.get_order_detail, self.account_id, cid)
        if not r.ok:
            if r.code in ("404",) or "NOT_FOUND" in r.code or "NOT_EXIST" in r.code:
                return None
            return False
        legs = (body or {}).get("orders") or []
        leg = next((x for x in legs if x.get("client_order_id") == cid), legs[0] if legs else None)
        if not leg:
            return None
        fp = _f(leg.get("filled_price"))
        return OrderInfo(cid, STATUS.get(str(leg.get("status")), "OPEN"), _f(leg.get("filled_quantity"), 0.0),
                         fp if fp else None, _f(leg.get("total_quantity"), 0.0), _f(leg.get("stop_price")))


class FakeBroker:
    """In-memory broker for simulation and tests. Prices come from the driver."""

    def __init__(self, cash=10_000.0, commission=0.0):
        self.cash = cash
        self.commission = commission
        self.mark = {}            # symbol -> price market orders fill at now
        self.held = {}            # symbol -> shares
        self.orders = {}          # cid -> dict
        self.history = {}         # symbol -> [Bar]
        self.refuse = {}          # cid prefix -> code, for tests
        self.account_id = "fake"
        self.account_type = "CASH"
        self.instant = False      # tests/target phase: market orders fill at once at the mark
        self.calls = []

    def connect(self):
        return "CASH"

    def heartbeat(self):
        return Reply(True)

    def usd(self):
        mv = sum(q * self.mark.get(s, 0.0) for s, q in self.held.items())
        return self.cash, mv, self.cash

    def positions(self):
        return {s: float(q) for s, q in self.held.items() if q}

    def bars(self, symbol, bar, count):
        return list(self.history.get(symbol, []))[-count:]

    def last_price(self, symbol):
        return self.mark.get(symbol)

    def _fill(self, o, price):
        q = o["qty"]
        if o["side"] == "BUY":
            cost = q * price + self.commission
            if cost > self.cash + 1e-9:
                o["status"] = "FAILED"
                return
            self.cash -= cost
            self.held[o["symbol"]] = self.held.get(o["symbol"], 0) + q
        else:
            if self.held.get(o["symbol"], 0) < q:
                o["status"] = "FAILED"
                return
            self.cash += q * price - self.commission
            self.held[o["symbol"]] -= q
        o.update(status="FILLED", filled=q, avg=price)

    def _resting_sell(self, symbol, except_cid=None):
        return sum(o["qty"] for c, o in self.orders.items() if c != except_cid and o["symbol"] == symbol
                   and o["side"] == "SELL" and o["status"] == "OPEN")

    def place(self, o: OrderReq):
        self.calls.append(("place", o.cid, o.order_type, o.side, o.qty))
        if o.cid in self.orders:
            return Reply(True, "OPENAPI_REPEAT_REQUEST", "already received")
        for pre, code in self.refuse.items():
            if o.cid.startswith(pre):
                return Reply(False, code, "refused by test")
        if o.side == "SELL" and self._resting_sell(o.symbol) + o.qty > self.held.get(o.symbol, 0):
            return Reply(False, "OPENAPI_SELL_QTY_EXCEED_AVAILABLE_QTY", "exceeds available position")
        self.orders[o.cid] = dict(symbol=o.symbol, side=o.side, type=o.order_type, qty=o.qty,
                                  stop=o.stop_price, limit=o.limit_price, status="OPEN", filled=0, avg=None)
        if o.order_type == "MARKET" and o.symbol in self.mark and self.instant:
            self._fill(self.orders[o.cid], self.mark[o.symbol])
        return Reply(True)

    def replace(self, cid, qty, stop_price=None, limit_price=None):
        self.calls.append(("replace", cid, stop_price))
        o = self.orders.get(cid)
        if not o or o["status"] != "OPEN":
            return Reply(False, "OPENAPI_ORDER_STATUS_ILLEGAL", "not open")
        if not qty:
            return Reply(False, "OPENAPI_PARAM_ERR", "quantity is empty")
        o["qty"] = qty
        if stop_price is not None:
            o["stop"] = stop_price
        if limit_price is not None:
            o["limit"] = limit_price
        return Reply(True)

    def cancel(self, cid):
        self.calls.append(("cancel", cid))
        o = self.orders.get(cid)
        if not o or o["status"] != "OPEN":
            return Reply(False, "OPENAPI_ORDER_STATUS_ILLEGAL", "not open")
        o["status"] = "CANCELED"
        return Reply(True)

    def detail(self, cid):
        o = self.orders.get(cid)
        if not o:
            return None
        return OrderInfo(cid, o["status"], float(o["filled"]), o["avg"], float(o["qty"]), o["stop"])

    # -- driven by the simulator ------------------------------------------
    def open_bar(self, symbol, bar):
        """Market orders waiting since the last bar fill at this bar's open."""
        self.mark[symbol] = bar.open
        for o in self.orders.values():
            if o["symbol"] == symbol and o["status"] == "OPEN" and o["type"] == "MARKET":
                self._fill(o, bar.open)

    def touch_stops(self, symbol, bar):
        """Resting sell stops hit inside the bar fill at the stop, or the open on a gap."""
        for o in self.orders.values():
            if (o["symbol"] == symbol and o["status"] == "OPEN" and o["type"] == "STOP_LOSS"
                    and o["side"] == "SELL" and bar.low <= o["stop"]):
                self._fill(o, min(bar.open, o["stop"]))
