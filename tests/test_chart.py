"""The chart (runner 0.3): bar sizes, the strategy's PLOT lines and the trade marks. Stdlib only."""
import os
import random
import sys
import unittest
from datetime import timedelta

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))

from test_runner import OPEN_NOW, bar, make  # noqa: E402
from ubot_runner import plot as pl  # noqa: E402
from ubot_runner import strategy as sg  # noqa: E402
from ubot_runner.broker import CHART_SIZES, TIMESPAN  # noqa: E402
from ubot_runner.live import Live, Ring  # noqa: E402
from ubot_runner.state import iso  # noqa: E402

INPUTS = {"fast": 20, "slow": 50, "rsi_len": 14, "mult": 2.0, "name": "x"}


def walk(n=300, seed=3):
    rnd = random.Random(seed)
    px, out = 50.0, []
    t0 = OPEN_NOW - timedelta(minutes=5 * n)
    for i in range(n):
        o = px
        px = max(1.0, px * (1 + rnd.gauss(0, 0.01)))
        out.append(bar(t0 + timedelta(minutes=5 * i), o, max(o, px) * 1.002, min(o, px) * 0.998, px))
    return out


class CheckPlot(unittest.TestCase):
    def ok(self, plot):
        self.assertEqual(pl.check_plot(plot, INPUTS), [])

    def bad(self, plot, word):
        got = pl.check_plot(plot, INPUTS)
        self.assertTrue(got and any(word in p for p in got), got)

    def test_empty_and_good(self):
        self.ok(None)
        self.ok([])
        self.ok([{"kind": "ema", "period": "fast"}, {"kind": "sma", "period": 200},
                 {"kind": "bollinger", "period": 20, "mult": "mult"}, {"kind": "donchian", "period": 55},
                 {"kind": "rsi", "period": "rsi_len", "levels": [30, 70]},
                 {"kind": "line", "label": "Stop line", "pane": "price"}])
        self.ok([{"kind": "macd"}, {"kind": "atr"}])          # defaults

    def test_refusals(self):
        self.bad({"kind": "ema"}, "must be a list")
        self.bad([{"kind": "ichimoku"}], "needs a kind")
        self.bad([{"kind": "ema"}], "needs period")
        self.bad([{"kind": "ema", "period": "nope"}], "not one of the INPUTS")
        self.bad([{"kind": "ema", "period": "name"}], "whole number")
        self.bad([{"kind": "ema", "period": 2.5}], "whole number")
        self.bad([{"kind": "ema", "period": 0}], "whole number")
        self.bad([{"kind": "ema", "period": 501}], "whole number")
        self.bad([{"kind": "ema", "period": True}], "whole number")
        self.bad([{"kind": "ema", "period": 9, "colour": "red"}], "unknown key")
        self.bad([{"kind": "ema", "period": 9, "levels": [1]}], "levels")
        self.bad([{"kind": "rsi", "levels": [1, 2, 3, 4]}], "levels")
        self.bad([{"kind": "macd", "fast": 30, "slow": 26}], "shorter")
        self.bad([{"kind": "line", "pane": "price"}], "needs a label")
        self.bad([{"kind": "line", "label": "x", "pane": "side"}], "needs a pane")
        self.bad([{"kind": "rsi"}, {"kind": "atr"}, {"kind": "macd"}], "at most 2")
        self.bad([{"kind": "ema", "period": n} for n in range(1, 8)], "at most 6")
        self.bad([{"kind": "ema", "period": "fast"}, {"kind": "ema", "period": 20}], "different")
        self.bad([{"kind": "ema", "period": 5, "label": "x" * 17}], "1-16")

    def test_plot_of_falls_back_to_nothing(self):
        class Good(sg.Strategy):
            INPUTS = {"fast": 9}
            PLOT = [{"kind": "ema", "period": "fast"}]

        class Bad(sg.Strategy):
            PLOT = [{"kind": "ema", "period": "fast"}]          # no such input

        self.assertEqual(pl.plot_of(Good()), Good.PLOT)
        self.assertEqual(pl.plot_of(Bad()), [])
        self.assertEqual(pl.plot_of(sg.Strategy()), [])

    def test_labels_follow_the_settings(self):
        item = {"kind": "ema", "period": "fast"}
        self.assertEqual(pl.label_of(item, {"fast": 20}), "EMA 20")
        self.assertEqual(pl.label_of(item, {"fast": 30}), "EMA 30")
        self.assertEqual(pl.label_of({"kind": "macd"}, {}), "MACD 12,26,9")
        self.assertEqual(pl.label_of({"kind": "bollinger", "period": 20}, {}), "BB 20,2")
        self.assertEqual(pl.label_of({"kind": "rsi", "label": "Mine"}, {}), "Mine")


class Series(unittest.TestCase):
    """Each line ends at what the strategy reads for the last bar from the same bars."""

    def test_last_values_match_the_strategy_functions(self):
        bars = walk()
        close = [b.close for b in bars]
        for p in (5, 14, 50):
            self.assertAlmostEqual(pl.sma_series(close, p)[-1], sg.sma(close, p), places=9)
            self.assertAlmostEqual(pl.ema_series(close, p)[-1], sg.ema(close, p), places=9)
            self.assertAlmostEqual(pl.rsi_series(close, p)[-1], sg.rsi(close, p), places=9)
            self.assertAlmostEqual(pl.atr_series(bars, p)[-1], sg.atr(bars, p), places=9)
            # and every earlier point is the same function over the bars up to it
            for i in (p + 3, len(close) // 2):
                self.assertAlmostEqual(pl.ema_series(close, p)[i], sg.ema(close[:i + 1], p), places=9)
                self.assertAlmostEqual(pl.atr_series(bars, p)[i], sg.atr(bars[:i + 1], p), places=9)

    def test_not_ready_is_none(self):
        close = [b.close for b in walk(30)]
        s = pl.ema_series(close, 20)
        self.assertEqual(s[:19], [None] * 19)
        self.assertIsNotNone(s[19])
        self.assertEqual(pl.rsi_series(close, 40), [None] * 30)

    def test_shapes(self):
        bars = walk(120)
        got = pl.series({"kind": "bollinger", "period": 20}, bars, {})
        self.assertEqual([g[0] for g in got], ["BB 20,2", "BB 20,2 +", "BB 20,2 -"])
        mid, up, lo = (g[2] for g in got)
        self.assertTrue(all(u > m > l_ for u, m, l_ in zip(up[19:], mid[19:], lo[19:])))
        d = pl.series({"kind": "donchian", "period": 10}, bars, {})
        self.assertEqual(d[0][2][-1], max(b.high for b in bars[-10:]))
        m = pl.series({"kind": "macd"}, bars, {})
        self.assertEqual(m[0][3], [0])                       # zero line by default
        self.assertEqual(m[0][1], "lower")
        self.assertIsNotNone(m[1][2][-1])
        for g in got + d + m:
            self.assertEqual(len(g[2]), len(bars))

    def test_custom_line_is_right_aligned_and_cleaned(self):
        bars = walk(10)
        item = {"kind": "line", "label": "L", "pane": "price"}
        got = pl.series(item, bars, {}, {"L": [1, "x", 3.5, float("nan")]})
        self.assertEqual(got[0][2], [None] * 6 + [1.0, None, 3.5, None])
        self.assertEqual(pl.series(item, bars, {}, {"other": [1]}), [])

    def test_warmup(self):
        self.assertEqual(pl.warmup([], {}), 0)
        self.assertEqual(pl.warmup([{"kind": "ema", "period": "slow"}], INPUTS), 160)
        self.assertEqual(pl.warmup([{"kind": "macd"}], {}), 115)
        self.assertEqual(pl.warmup([{"kind": "sma", "period": 500}], {}), 1000)


class Plotting(sg.Strategy):
    INPUTS = {"fast": 5}
    PLOT = [{"kind": "ema", "period": "fast"}, {"kind": "line", "label": "Mine", "pane": "lower"}]

    def on_bar(self, ctx, symbol, bars):
        pass

    def plot(self, ctx, symbol, bars):
        return {"Mine": [b.close * 2 for b in bars]}


class LiveChart(unittest.TestCase):
    def live(self, strategy=None):
        eng, st, fb = make()
        if strategy:
            eng.strategy = strategy
            eng.cfg.inputs = dict(strategy.INPUTS)
        fb.history["F"] = walk(400)
        sizes = []
        real = fb.bars
        fb.bars = lambda s, b, n: (sizes.append((b, n)), real(s, b, n))[1]
        return Live(eng, "x.toml", lambda c, p: None, Ring()), eng, sizes

    def test_every_chart_size_is_a_webull_size(self):
        self.assertEqual(set(CHART_SIZES) - set(TIMESPAN), set())
        self.assertEqual(set(sg.BAR_SIZES) - set(CHART_SIZES), set())

    def test_starts_at_the_strategys_bar_and_switches(self):
        lv, eng, sizes = self.live()
        self.assertEqual(lv.chart_tf, eng.strategy.BAR)
        lv.load_chart("F", OPEN_NOW)
        self.assertEqual(lv.do_chart_tf(OPEN_NOW, "1w"), "")
        self.assertEqual(sizes[-1][0], "1w")
        self.assertEqual(lv.chart["F"]["tf"], "1w")
        self.assertEqual(lv.do_chart_tf(OPEN_NOW, "3m"), "unknown bar size")
        self.assertEqual(lv.chart_tf, "1w")

    def test_lines_are_cut_to_the_shown_bars_and_follow_inputs(self):
        lv, eng, sizes = self.live(Plotting())
        lv.load_chart("F", OPEN_NOW, force=True)
        c = lv.chart["F"]
        self.assertEqual(sizes[-1][1], lv.CHART_BARS + pl.warmup(Plotting.PLOT, {"fast": 5}))
        self.assertEqual(len(c["bars"]), lv.CHART_BARS)
        labels = [(l["label"], l["pane"]) for l in c["plots"]]
        self.assertEqual(labels, [("EMA 5", "price"), ("Mine", "lower")])
        for l in c["plots"]:
            self.assertEqual(len(l["values"]), lv.CHART_BARS)
        self.assertIsNotNone(c["plots"][0]["values"][0])     # warmed up before the first shown bar
        self.assertAlmostEqual(c["plots"][1]["values"][-1], c["bars"][-1][1] * 2)
        eng.cfg.inputs = {"fast": 9}
        lv.load_chart("F", OPEN_NOW, force=True)
        self.assertEqual(lv.chart["F"]["plots"][0]["label"], "EMA 9")

    def test_a_failing_plot_keeps_the_chart(self):
        class Boom(Plotting):
            def plot(self, ctx, symbol, bars):
                raise RuntimeError("no")
        lv, eng, _ = self.live(Boom())
        lv.load_chart("F", OPEN_NOW, force=True)
        self.assertEqual([l["label"] for l in lv.chart["F"]["plots"]], ["EMA 5"])

    def test_marks_sit_on_the_bar_of_the_fill(self):
        lv, eng, _ = self.live()
        lv.load_chart("F", OPEN_NOW, force=True)
        times = [t for t, _ in lv.chart["F"]["bars"]]
        st = eng.state
        a, b = times[10], times[20]
        st.db.execute("INSERT INTO trades(symbol,qty,entry,exit,pnl,reason,opened_at,closed_at,opened_by)"
                      " VALUES('F',10,50,52,20,'target',?,?,'program')", (iso_plus(a, 60), iso_plus(b, 1)))
        st.db.execute("INSERT INTO trades(symbol,qty,entry,exit,pnl,reason,opened_at,closed_at,opened_by)"
                      " VALUES('AMD',10,50,52,20,'target',?,?,'program')", (a, b))
        st.db.commit()
        st.open_position("F", 5, 51.0, 49.0, 55.0, from_iso(times[-3]) + timedelta(seconds=30))
        m = lv.chart_marks("F", OPEN_NOW)
        self.assertEqual([(x["side"], x["i"]) for x in m], [("buy", 10), ("sell", 20), ("buy", 117)])
        self.assertEqual((m[1]["from"], m[1]["pnl"], m[1]["reason"]), (10, 20, "target"))

    def test_a_trade_opened_before_the_chart_shows_only_its_sale(self):
        lv, eng, _ = self.live()
        lv.load_chart("F", OPEN_NOW, force=True)
        times = [t for t, _ in lv.chart["F"]["bars"]]
        eng.state.db.execute("INSERT INTO trades(symbol,qty,entry,exit,pnl,reason,opened_at,closed_at,opened_by)"
                             " VALUES('F',10,50,49,-10,'stop',?,?,'program')", (iso_plus(times[0], -3600), times[5]))
        eng.state.db.commit()
        m = lv.chart_marks("F", OPEN_NOW)
        self.assertEqual([(x["side"], x["i"], x["from"]) for x in m], [("sell", 5, None)])


def from_iso(t):
    from datetime import datetime
    return datetime.fromisoformat(t)


def iso_plus(t, seconds):
    return iso(from_iso(t) + timedelta(seconds=seconds))


if __name__ == "__main__":
    unittest.main()
