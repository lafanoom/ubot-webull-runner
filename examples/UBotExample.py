# Copyright 2026, uBotDesign
# https://ubotdesign.com
"""Example strategy, written by hand: daily trend entry with an ATR stop and target.

Buy when the fast moving average crosses above the slow one and the close is above
the slow one. The stop rests at Webull (stop_atr x ATR below the close); the
target (target_atr x ATR above) is watched by the runner. Sell when the fast
average crosses back below the slow one. Once the price has moved one ATR in
favour, the stop follows at trail_atr x ATR below the close (0 = off).
"""
from ubot_runner.strategy import Strategy, atr, sma

RUNNER = "0.3.0"
NAME = "Example Trend"


class UBotExample(Strategy):
    NAME = NAME
    TAG = "uexample"
    BAR = "1d"
    WARMUP = 60
    INPUTS = {
        "fast": 20,
        "slow": 50,
        "atr_period": 14,
        "stop_atr": 2.0,       # 0 = no stop
        "target_atr": 4.0,     # 0 = no target
        "trail_atr": 2.0,      # 0 = no trailing
        "notional_usd": 1000.0,
    }
    # what the rules read, drawn on the chart (follows the settings)
    PLOT = [
        {"kind": "sma", "period": "fast"},
        {"kind": "sma", "period": "slow"},
        {"kind": "atr", "period": "atr_period"},
    ]

    def on_bar(self, ctx, symbol, bars):
        p = ctx.inputs
        closes = bars.close
        fast, slow = sma(closes, p["fast"]), sma(closes, p["slow"])
        fast_prev, slow_prev = sma(closes[:-1], p["fast"]), sma(closes[:-1], p["slow"])
        a = atr(bars, p["atr_period"])
        if None in (fast, slow, fast_prev, slow_prev, a):
            return
        crossed_up = fast_prev <= slow_prev and fast > slow
        crossed_down = fast_prev >= slow_prev and fast < slow
        last = closes[-1]
        pos = ctx.position(symbol)

        # close
        if pos and crossed_down:
            ctx.sell(symbol, reason="averages crossed down")
            if ctx.is_simulation:
                ctx.accept(f"exit {symbol} fast<slow at {bars.last.time:%Y-%m-%d}")
            return
        # open
        if not pos and crossed_up and last > slow:
            stop = last - p["stop_atr"] * a if p["stop_atr"] > 0 else None
            target = last + p["target_atr"] * a if p["target_atr"] > 0 else None
            ctx.buy(symbol, notional=p["notional_usd"], stop=stop, target=target, reason="averages crossed up")
            if ctx.is_simulation:
                ctx.accept(f"entry {symbol} fast>slow close>slow at {bars.last.time:%Y-%m-%d}")
            return
        # modify
        if pos and p["trail_atr"] > 0 and last - pos.entry >= a:
            ctx.move_stop(symbol, last - p["trail_atr"] * a)

    def levels(self, ctx, symbol, bars, position):
        """New settings saved in the window reach the position held now:
        the same stop/target rule as the buy, from the price it was bought at."""
        p = ctx.inputs
        a = atr(bars, p["atr_period"])
        if a is None:
            return None
        stop = position.entry - p["stop_atr"] * a if p["stop_atr"] > 0 else None
        target = position.entry + p["target_atr"] * a if p["target_atr"] > 0 else None
        return stop, target
