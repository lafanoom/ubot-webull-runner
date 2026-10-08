"""Indicator lines on the program's chart.

A strategy says which indicators its rules read through its `PLOT` class
attribute (a plain list literal). The window draws them on the chart - on the
price, or in a small pane under it - at whatever bar size the customer picks:

    PLOT = [
        {"kind": "ema", "period": "fast"},                 # an input name, or a number
        {"kind": "ema", "period": "slow"},
        {"kind": "bollinger", "period": 20, "mult": 2},
        {"kind": "rsi", "period": "rsi_len", "levels": [30, 70]},
        {"kind": "line", "label": "Stop line", "pane": "price"},   # values from plot()
    ]

A value that names an input follows the customer's settings: change "fast"
from 20 to 30 and the line moves with it. `kind: "line"` draws what the
strategy's own `plot(ctx, symbol, bars)` returns under that label - for an
indicator that is not in the list below.

`check_plot` is the only judge. The runner draws no indicator when it says no,
and the factory refuses the strategy file for the same reasons.
"""
import math

# kind -> (pane, arguments in order, defaults)
KINDS = {
    "sma": ("price", ("period",), {}),
    "ema": ("price", ("period",), {}),
    "bollinger": ("price", ("period", "mult"), {"mult": 2}),
    "donchian": ("price", ("period",), {}),
    "rsi": ("lower", ("period",), {"period": 14}),
    "macd": ("lower", ("fast", "slow", "signal"), {"fast": 12, "slow": 26, "signal": 9}),
    "atr": ("lower", ("period",), {"period": 14}),
    "line": (None, (), {}),
}
PERIOD_ARGS = ("period", "fast", "slow", "signal")
MAX_LINES = 6
MAX_LOWER = 2
MAX_PERIOD = 500
MAX_LEVELS = 3


def _label(v, n=16):
    return isinstance(v, str) and 0 < len(v.strip()) <= n and not any(ord(c) < 32 for c in v)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _value(v, inputs, arg):
    """The number an argument stands for: a number, or the current value of an input."""
    if isinstance(v, str):
        v = (inputs or {}).get(v)
    if not _num(v) or v <= 0:
        return None
    if arg in PERIOD_ARGS:
        if v != int(v) or v > MAX_PERIOD:
            return None
        return int(v)
    return float(v)


def check_plot(plot, inputs=None):
    """Problems with a strategy's PLOT list, [] when it is fine. `inputs` are the
    strategy's INPUTS: names used as values must be there and hold a fitting number."""
    if plot is None or plot == []:
        return []
    if not isinstance(plot, list):
        return ["PLOT must be a list"]
    if len(plot) > MAX_LINES:
        return [f"PLOT may have at most {MAX_LINES} items"]
    out, lower, labels = [], 0, []
    for n, item in enumerate(plot, 1):
        where = f"PLOT item {n}"
        if not isinstance(item, dict) or item.get("kind") not in KINDS:
            out.append(f"{where} needs a kind from {', '.join(KINDS)}")
            continue
        kind = item["kind"]
        pane, args, _ = KINDS[kind]
        allowed = {"kind", "label", "levels", *args} | ({"pane"} if kind == "line" else set())
        out += [f"{where} has an unknown key {k!r}" for k in item if k not in allowed]
        if "label" in item and not _label(item["label"]):
            out.append(f"{where} label must be text of 1-16 characters")
        if kind == "line":
            if not _label(item.get("label")):
                out.append(f"{where} (line) needs a label: the name plot() returns its values under")
            if item.get("pane") not in ("price", "lower"):
                out.append(f'{where} (line) needs a pane: "price" or "lower"')
            pane = item.get("pane")
        for a in args:
            if a in item and isinstance(item[a], str) and item[a] not in (inputs or {}):
                out.append(f"{where} {a} names {item[a]!r}, which is not one of the INPUTS")
            elif a in item and _value(item[a], inputs, a) is None:
                kind_of = f"a whole number 1-{MAX_PERIOD}" if a in PERIOD_ARGS else "a number above 0"
                out.append(f"{where} {a} must be {kind_of}, or an input holding one")
            elif a not in item and a not in KINDS[kind][2]:
                out.append(f"{where} ({kind}) needs {a}")
        if kind == "macd":
            f, s = (_value(item.get(a, KINDS[kind][2].get(a)), inputs, a) for a in ("fast", "slow"))
            if f and s and f >= s:
                out.append(f"{where} (macd) fast must be shorter than slow")
        if "levels" in item:
            lv = item["levels"]
            if pane != "lower" or not isinstance(lv, list) or not 1 <= len(lv) <= MAX_LEVELS \
                    or not all(_num(x) for x in lv):
                out.append(f"{where} levels must be 1-{MAX_LEVELS} numbers, on a lower-pane indicator")
        if pane == "lower":
            lower += 1
        labels.append(label_of(item, inputs))
    if lower > MAX_LOWER:
        out.append(f"PLOT may have at most {MAX_LOWER} indicators under the price (rsi, macd, atr, lower lines)")
    if len(set(labels)) != len(labels):
        out.append("PLOT labels must be different from each other")
    return out


def plot_of(strategy):
    """The strategy's PLOT when it passes, else [] (no indicator lines)."""
    plot = getattr(strategy, "PLOT", None) or []
    inputs = getattr(strategy, "INPUTS", None) or {}
    return list(plot) if not check_plot(plot, inputs) else []


def _args(item, inputs):
    kind = item["kind"]
    _, args, defaults = KINDS[kind]
    return [_value(item.get(a, defaults.get(a)), inputs, a) for a in args]


def label_of(item, inputs):
    if item.get("label"):
        return item["label"]
    kind = item.get("kind")
    try:
        vals = _args(item, inputs)
    except Exception:
        vals = []
    nums = ",".join("?" if v is None else (str(int(v)) if v == int(v) else f"{v:g}") for v in vals)
    name = {"sma": "SMA", "ema": "EMA", "bollinger": "BB", "donchian": "Donchian", "rsi": "RSI",
            "macd": "MACD", "atr": "ATR"}.get(kind, str(kind))
    return f"{name} {nums}".strip()


def warmup(plot, inputs):
    """Extra bars to load before the shown ones so the lines are settled when they come on screen."""
    longest = 0
    for item in plot:
        vals = [v for v in _args(item, inputs) if v]
        if item["kind"] == "macd" and len(vals) == 3:
            vals = [vals[1] + vals[2]]
        longest = max([longest] + [int(v) for v in vals])
    return min(1000, longest * 3 + 10) if longest else 0


# -- whole series, oldest first, None where the indicator is not ready yet ----
# Each one's last value is the value of the same-named function in
# ubot_runner.strategy over the same list, so the line ends where the
# strategy's own reading of the last bar is.

def sma_series(values, period):
    out, s = [], 0.0
    for i, v in enumerate(values):
        s += v
        if i >= period:
            s -= values[i - period]
        out.append(s / period if i >= period - 1 else None)
    return out


def ema_series(values, period):
    out = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    e = sum(values[:period]) / period
    out[period - 1] = e
    for i in range(period, len(values)):
        e = values[i] * k + e * (1 - k)
        out[i] = e
    return out


def rsi_series(values, period):
    out = [None] * len(values)
    if len(values) <= period:
        return out
    g = l = 0.0
    for a, b in zip(values[:period], values[1:period + 1]):
        g += max(b - a, 0.0)
        l += max(a - b, 0.0)
    ag, al = g / period, l / period

    def r():
        return 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    out[period] = r()
    for i in range(period + 1, len(values)):
        d = values[i] - values[i - 1]
        ag = (ag * (period - 1) + max(d, 0.0)) / period
        al = (al * (period - 1) + max(-d, 0.0)) / period
        out[i] = r()
    return out


def atr_series(bars, period):
    out = [None] * len(bars)
    if len(bars) <= period:
        return out
    tr = [max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)) for p, c in zip(bars[:-1], bars[1:])]
    a = sum(tr[:period]) / period
    out[period] = a
    for i in range(period, len(tr)):
        a = (a * (period - 1) + tr[i]) / period
        out[i + 1] = a
    return out


def _sub(a, b):
    return [None if x is None or y is None else x - y for x, y in zip(a, b)]


def series(item, bars, inputs, custom=None):
    """[(label, pane, values, levels)] for one PLOT item over `bars` - one line, or three for
    bollinger, two for donchian and macd. `custom` is what the strategy's plot() returned."""
    kind = item["kind"]
    pane = item.get("pane") if kind == "line" else KINDS[kind][0]
    label = label_of(item, inputs)
    levels = list(item.get("levels") or [])
    a = _args(item, inputs)
    close = [b.close for b in bars]
    if kind == "sma":
        return [(label, pane, sma_series(close, a[0]), levels)]
    if kind == "ema":
        return [(label, pane, ema_series(close, a[0]), levels)]
    if kind == "bollinger":
        p, m = a
        mid = sma_series(close, p)
        dev = [None] * len(close)
        for i in range(p - 1, len(close)):
            w = close[i - p + 1:i + 1]
            mu = mid[i]
            dev[i] = math.sqrt(sum((x - mu) ** 2 for x in w) / p)
        up = [None if d is None else mu + m * d for mu, d in zip(mid, dev)]
        lo = [None if d is None else mu - m * d for mu, d in zip(mid, dev)]
        return [(label, pane, mid, levels), (label + " +", pane, up, []), (label + " -", pane, lo, [])]
    if kind == "donchian":
        p = a[0]
        hi = [max(b.high for b in bars[i - p + 1:i + 1]) if i >= p - 1 else None for i in range(len(bars))]
        lo = [min(b.low for b in bars[i - p + 1:i + 1]) if i >= p - 1 else None for i in range(len(bars))]
        return [(label + " +", pane, hi, levels), (label + " -", pane, lo, [])]
    if kind == "rsi":
        return [(label, pane, rsi_series(close, a[0]), levels)]
    if kind == "atr":
        return [(label, pane, atr_series(bars, a[0]), levels)]
    if kind == "macd":
        f, s, g = a
        m = _sub(ema_series(close, f), ema_series(close, s))
        start = next((i for i, v in enumerate(m) if v is not None), len(m))
        sig = [None] * start + ema_series(m[start:], g)
        return [(label, pane, m, levels or [0]), (label + " sig", pane, sig, [])]
    # kind == "line": the strategy's own numbers, right-aligned with the bars
    vals = (custom or {}).get(item["label"])
    if not isinstance(vals, (list, tuple)):
        return []
    vals = [float(v) if _num(v) else None for v in list(vals)[-len(bars):]]
    return [(label, pane, [None] * (len(bars) - len(vals)) + vals, levels)]
