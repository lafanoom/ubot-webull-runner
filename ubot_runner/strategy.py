"""The strategy API - the only module a strategy file may import.

A strategy decides; the runner acts. A strategy never talks to the broker, the
network or the disk. It receives closed bars for one symbol at a time and asks
for things through `ctx`:

    class UBotExample(Strategy):
        NAME = "Example Trend"
        TAG = "uexample"
        BAR = "1d"
        INPUTS = {"fast": 20, "slow": 50, "stop_atr": 2.0, "target_atr": 4.0}
        WARMUP = 60

        def on_bar(self, ctx, symbol, bars):
            ...
            ctx.buy(symbol, notional=1000, stop=..., target=...)

Order of work inside one bar is fixed by the runner whatever order the strategy
asks in: close first, then open, then modify.

Prices are US dollars. Quantities are whole shares. Long only.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

BAR_SIZES = ("1d", "1h", "30m", "15m", "5m")


@dataclass(frozen=True)
class Bar:
    time: datetime          # bar start, timezone-aware UTC
    open: float
    high: float
    low: float
    close: float
    volume: float


class Bars(Sequence):
    """Closed bars of one symbol, oldest first. The forming bar is never here."""

    def __init__(self, items):
        self._b = tuple(items)

    def __getitem__(self, i):
        return self._b[i]

    def __len__(self):
        return len(self._b)

    @property
    def open(self):
        return [b.open for b in self._b]

    @property
    def high(self):
        return [b.high for b in self._b]

    @property
    def low(self):
        return [b.low for b in self._b]

    @property
    def close(self):
        return [b.close for b in self._b]

    @property
    def volume(self):
        return [b.volume for b in self._b]

    @property
    def last(self):
        return self._b[-1]


@dataclass(frozen=True)
class Position:
    """A position this program holds: its own buys, and buys made by hand in its
    window (by = "you"). Shares bought in the Webull app are not here."""
    symbol: str
    qty: int
    entry: float
    stop: Optional[float]
    target: Optional[float]
    opened: datetime
    by: str = "program"


@dataclass(frozen=True)
class Buy:
    symbol: str
    qty: Optional[int] = None
    notional: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    reason: str = ""


@dataclass(frozen=True)
class Sell:
    symbol: str
    reason: str = ""


@dataclass(frozen=True)
class MoveStop:
    symbol: str
    price: float


class Strategy:
    NAME: Optional[str] = None   # None = the program calls itself by its file name
    TAG: str = "demo"            # ASCII tag of the factory job, goes into order ids
    BAR: str = "1d"
    INPUTS: dict = {}
    WARMUP: int = 50             # closed bars needed before on_bar is called
    UI: dict = {}                # changes to the standard window (see ubot_runner.ui.check_ui)

    def on_start(self, ctx):
        pass

    def on_bar(self, ctx, symbol: str, bars: Bars):
        raise NotImplementedError

    def levels(self, ctx, symbol: str, bars: Bars, position: Position):
        """Stop and target of a position held now, worked out from the current inputs.

        The runner calls it when the customer saves new settings, so new values
        reach the positions already held. Return (stop, target); None in either
        place keeps that one as it is. Returning None keeps both."""
        return None

    def ui_values(self, ctx, symbol: str, bars: Bars):
        """Extra figures for the watch list, {label: value}, when UI asks for them."""
        return {}

    def on_button(self, ctx, button: str):
        """A button declared in UI["buttons"] was pressed. Ask through ctx like on_bar."""
        pass


# -- indicators (pure functions over lists, newest value last) ---------------

def sma(values, period):
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema(values, period):
    if period <= 0 or len(values) < period:
        return None
    k = 2.0 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def rsi(values, period=14):
    """Wilder RSI of the last value."""
    if period <= 0 or len(values) <= period:
        return None
    gains = losses = 0.0
    for a, b in zip(values[:period], values[1:period + 1]):
        d = b - a
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    ag, al = gains / period, losses / period
    for a, b in zip(values[period:], values[period + 1:]):
        d = b - a
        ag = (ag * (period - 1) + max(d, 0.0)) / period
        al = (al * (period - 1) + max(-d, 0.0)) / period
    if al == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def atr(bars, period=14):
    """Wilder ATR of the last bar."""
    if period <= 0 or len(bars) <= period:
        return None
    tr = []
    for prev, cur in zip(bars[:-1], bars[1:]):
        tr.append(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close)))
    a = sum(tr[:period]) / period
    for t in tr[period:]:
        a = (a * (period - 1) + t) / period
    return a
