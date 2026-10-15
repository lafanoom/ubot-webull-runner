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
        fb.history["F"] = walk(1000)
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

    def test_a_failed_bar_load_keeps_the_chart_and_tries_again_soon(self):
        lv, eng, sizes = self.live()
        lv.load_chart("F", OPEN_NOW, force=True)
        shown = list(lv.chart["F"]["bars"])
        fb = eng.broker
        fb.bars = lambda s, b, n: None                          # Webull answered with an error
        later = OPEN_NOW + timedelta(seconds=120)
        lv.load_chart("F", later)
        self.assertEqual(lv.chart["F"]["bars"], shown)          # still on screen
        wait = lv.CHART_EVERY.get(lv.chart_tf, 60)
        self.assertAlmostEqual((later - lv.chart["F"]["at"]).total_seconds(), wait - lv.CHART_RETRY)
        lv.load_chart("G", later, force=True)                   # nothing to keep: blank, soon again
        self.assertEqual(lv.chart["G"]["bars"], [])

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
        self.assertEqual([(x["side"], x["i"]) for x in m], [("buy", 10), ("sell", 20), ("buy", len(times) - 3)])
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


class SignalPlacement(unittest.TestCase):
    """Strategy.signals() points land on the bar they fall in; bad ones are dropped."""

    def test_place(self):
        from ubot_runner.live import place_signals
        times = [OPEN_NOW - timedelta(hours=4 - h) for h in range(4)]      # 4 hourly bars
        now = OPEN_NOW
        raw = [
            {"time": times[1] + timedelta(minutes=20), "side": "buy", "note": "a"},
            {"time": iso(times[2]), "side": "sell"},
            {"time": times[2] + timedelta(minutes=5), "side": "buy", "note": "later on bar 2"},
            {"time": times[0] - timedelta(hours=1), "side": "buy"},           # before the chart
            {"time": now + timedelta(hours=1), "side": "buy"},                # in the future
            {"time": times[3], "side": "hold"},                               # not a side
            {"time": "not a time", "side": "buy"},
            "junk",
            {"time": times[3], "side": "sell", "note": "bad\nnote"},
        ]
        got = place_signals(raw, times, now)
        self.assertEqual([(g["i"], g["side"]) for g in got], [(1, "buy"), (2, "buy"), (3, "sell")])
        self.assertEqual(got[1]["note"], "later on bar 2")
        self.assertEqual(got[2]["note"], "")
        self.assertEqual(place_signals(None, times, now), [])
        self.assertEqual(place_signals([], [], now), [])

    def test_default_has_none(self):
        self.assertEqual(sg.Strategy().signals(None, "X", None), [])


class Banding(sg.Strategy):
    """A band from plot(): a colour per bar and an arrow now and then (runner 0.3.2)."""
    PLOT = [{"kind": "band", "label": "Trend band"}]

    def on_bar(self, ctx, symbol, bars):
        pass

    def plot(self, ctx, symbol, bars):
        cols = ["green" if b.close >= b.open else "red" for b in bars]
        return {"Trend band": {"state": cols, "div": ["up" if i % 50 == 0 else None for i in range(len(bars))]}}


class Band(unittest.TestCase):
    def test_check(self):
        self.assertEqual(pl.check_plot([{"kind": "band", "label": "Trend band"}], {}), [])
        self.assertTrue(pl.check_plot([{"kind": "band"}], {}))                       # needs a label
        self.assertTrue(pl.check_plot([{"kind": "band", "label": "a"}, {"kind": "band", "label": "b"}], {}))
        self.assertTrue(pl.check_plot([{"kind": "band", "label": "a", "pane": "price"}], {}))

    def test_values_are_cleaned_and_right_aligned(self):
        got = pl.band_values({"state": ["green", "purple", None, "blue"], "div": ["up", "sideways"]}, 6)
        self.assertEqual(got, [[None, None], [None, None], ["green", None], [None, None], [None, "up"],
                               ["blue", None]])
        self.assertEqual(pl.band_values(["red", "even"], 3), [[None, None], ["red", None], ["even", None]])
        self.assertEqual(pl.band_values(None, 2), [[None, None], [None, None]])
        self.assertEqual(pl.band_values(["red"] * 9, 3), [["red", None]] * 3)

    def test_warmup_reaches_back_for_the_anchors(self):
        self.assertEqual(pl.warmup([{"kind": "band", "label": "x"}], {}), pl.BAND_WARMUP)
        self.assertEqual(pl.warmup([], {}), 0)

    def test_live_chart_carries_the_band_and_the_candles(self):
        eng, st, fb = make()
        eng.strategy = Banding()
        eng.cfg.inputs = {}
        fb.history["F"] = walk(500)
        lv = Live(eng, "x.toml", lambda c, p: None, Ring())
        lv.load_chart("F", OPEN_NOW, force=True)
        c = lv.chart["F"]
        self.assertEqual(len(c["ohlc"]), len(c["bars"]))
        for (t, close), (o, h, l_, cl) in zip(c["bars"], c["ohlc"]):
            self.assertEqual(close, cl)
            self.assertTrue(l_ <= min(o, cl) and h >= max(o, cl))
        band = [l for l in c["plots"] if l["pane"] == "band"]
        self.assertEqual(len(band), 1)
        self.assertEqual(len(band[0]["values"]), len(c["bars"]))
        for (o, h, l_, cl), (colour, _) in zip(c["ohlc"], band[0]["values"]):
            self.assertEqual(colour, "green" if cl >= o else "red")
        lv.selected = "F"
        lv.refresh(OPEN_NOW, quick=True)
        self.assertEqual(lv.snapshot()["chart_ohlc"], lv.chart["F"]["ohlc"])


class UIChart(unittest.TestCase):
    def test_chart_and_manual(self):
        from ubot_runner.ui import check_ui, T
        self.assertEqual(check_ui({"chart": "candles", "manual": True}), [])
        self.assertEqual(check_ui({"chart": "line", "manual": False}), [])
        self.assertTrue(check_ui({"chart": "bars"}))
        self.assertTrue(check_ui({"manual": "yes"}))
        for lang in ("en", "th"):
            for k in ("c_candles", "c_band_none", "w_watch"):
                self.assertIn(k, T[lang])


class ChartView(unittest.TestCase):
    """Drag back in time, wheel zoom, the way home (chartview)."""

    def setUp(self):
        from ubot_runner import chartview
        self.cv = chartview

    def test_window_starts_at_the_newest_bars(self):
        self.assertEqual(self.cv.window(600, 0, 120), (480, 600))
        self.assertEqual(self.cv.window(600, 30, 120), (450, 570))
        self.assertEqual(self.cv.window(50, 0, 120), (0, 50))          # fewer bars than the span
        self.assertEqual(self.cv.window(600, 10_000, 120), (0, 120))   # cannot go before the first bar
        self.assertEqual(self.cv.clamp(600, -5, 1), (0, self.cv.SPAN_MIN))

    def test_drag_right_goes_back_and_stops_at_both_ends(self):
        # 120 bars over 1190 px = 10 px a bar
        self.assertEqual(self.cv.drag(600, 0, 120, 100, 1190), 10)
        self.assertEqual(self.cv.drag(600, 10, 120, -50, 1190), 5)
        self.assertEqual(self.cv.drag(600, 0, 120, -500, 1190), 0)
        self.assertEqual(self.cv.drag(600, 0, 120, 99_999, 1190), 480)
        self.assertEqual(self.cv.drag(600, 7, 120, 0, 0), 7)

    def test_zoom_keeps_the_bar_under_the_pointer(self):
        back, span = self.cv.zoom(600, 0, 120, 1, 1.0)                 # at the right edge
        self.assertEqual(back, 0)
        self.assertLess(span, 120)
        a, b = self.cv.window(600, 100, 120)
        mid = a + 0.5 * 119
        back2, span2 = self.cv.zoom(600, 100, 120, 1, 0.5)
        a2, _ = self.cv.window(600, back2, span2)
        self.assertLessEqual(abs(a2 + 0.5 * (span2 - 1) - mid), 1)
        back3, span3 = self.cv.zoom(600, 0, 120, -50, 0.5)             # all the way out
        self.assertEqual((back3, span3), (0, 600))
        self.assertEqual(self.cv.zoom(600, 0, 20, 5, 0.5)[1], self.cv.SPAN_MIN)
        self.assertEqual(self.cv.zoom(600, 0, 120, 0.01, 1.0)[1], 119)  # a touchpad nudge still moves

    def test_new_bars_keep_the_same_bars_on_screen(self):
        old = ["t1", "t2", "t3"]
        self.assertEqual(self.cv.follow(old, ["t1", "t2", "t3", "t4", "t5"], 10), 12)
        self.assertEqual(self.cv.follow(old, ["t2", "t3", "t4"], 10), 11)
        self.assertEqual(self.cv.follow(old, old, 10), 10)
        self.assertEqual(self.cv.follow(old, ["x", "y"], 10), 10)      # not found: leave it
        self.assertEqual(self.cv.follow(old, ["t1", "t2", "t3", "t4"], 0), 0)   # at the newest: stays there

    def test_cut_moves_marks_signals_and_lines(self):
        n = 10
        bars = [(f"t{i}", float(i)) for i in range(n)]
        ohlc = [(i, i, i, i) for i in range(n)]
        plots = [{"label": "x", "pane": "price", "values": list(range(n)), "levels": [], "group": 0, "kind": "ma"}]
        marks = [{"side": "buy", "i": 2}, {"side": "sell", "i": 6, "from": 2}, {"side": "buy", "i": 9}]
        sigs = [{"side": "buy", "i": 1}, {"side": "sell", "i": 5}]
        chart = (bars, None, plots, marks, "5m", True, sigs, ohlc)
        self.assertIs(self.cv.cut(chart, 0, n), chart)
        b2, p, pl2, m2, tf, hp, s2, o2 = self.cv.cut(chart, 4, 8)
        self.assertEqual([t for t, _ in b2], ["t4", "t5", "t6", "t7"])
        self.assertEqual(o2, ohlc[4:8])
        self.assertEqual(pl2[0]["values"], [4, 5, 6, 7])
        self.assertEqual(plots[0]["values"], list(range(n)))              # the loaded chart is untouched
        self.assertEqual(m2, [{"side": "sell", "i": 2, "from": -2}])       # its line runs in from the left
        self.assertEqual(s2, [{"side": "sell", "i": 1}])

    def test_live_loads_more_than_one_screen(self):
        from ubot_runner import chartview
        self.assertGreater(Live.CHART_BARS, chartview.SPAN * 2)
        self.assertLessEqual(Live.CHART_BARS + pl.BAND_WARMUP, 1200)      # one Webull call, band warmed up
        from ubot_runner.ui import T
        for lang in ("en", "th"):
            self.assertIn("c_latest", T[lang])


class RuleMarks(unittest.TestCase):
    """signals() with a mark: entries and exits on the price, a stats() table under the chart."""

    def test_an_exit_and_an_entry_share_a_bar(self):
        from ubot_runner.live import place_signals
        times = [OPEN_NOW - timedelta(minutes=5 * (9 - i)) for i in range(10)]
        raw = [{"time": times[3], "side": "sell", "mark": "exit", "leg": "long", "price": 51.5, "note": "out"},
               {"time": times[3], "side": "sell", "mark": "entry", "leg": "short", "note": "in"},
               {"time": times[3], "side": "buy", "note": "plain"},
               {"time": times[5], "side": "buy", "mark": "wild", "leg": "up", "price": float("nan")}]
        got = place_signals(raw, times, OPEN_NOW)
        self.assertEqual([(g["i"], g.get("mark"), g.get("leg")) for g in got],
                         [(3, None, None), (3, "entry", "short"), (3, "exit", "long"), (5, None, None)])
        self.assertEqual(got[2]["price"], 51.5)
        self.assertNotIn("price", got[3])                 # an unknown mark is a plain signal

    def test_stats_are_text_cut_to_size(self):
        from ubot_runner.live import STATS_ROWS, clean_stats
        self.assertIsNone(clean_stats(None))
        self.assertIsNone(clean_stats({"rows": []}))
        t = clean_stats({"title": "T\x07", "cols": ["", "A", "B", "C", "D", "E"], "tones": ["long", "x"],
                         "rows": [["n", 1, 2.5, True]] * 40, "note": "n" * 500})
        self.assertEqual(t["title"], "T")
        self.assertEqual(len(t["cols"]), 4)
        self.assertEqual(t["tones"], ["long", None])
        self.assertEqual(len(t["rows"]), STATS_ROWS)
        self.assertEqual(t["rows"][0], ["n", "1", "2.5", ""])
        self.assertEqual(len(t["note"]), 200)

    def test_live_keeps_the_table_with_the_chart(self):
        class Table(Plotting):
            def stats(self, ctx, symbol, bars):
                return {"title": "x", "rows": [["bars", len(bars)]]}
        eng, st, fb = make()
        eng.strategy = Table()
        eng.cfg.inputs = dict(Table.INPUTS)
        fb.history["F"] = walk(1000)
        lv = Live(eng, "x.toml", lambda c, p: None, Ring())
        lv.selected = "F"
        lv.load_chart("F", OPEN_NOW, force=True)
        self.assertEqual(lv.chart["F"]["stats"]["rows"], [["bars", str(lv.CHART_BARS)]])
        lv.refresh(OPEN_NOW)
        snap = lv.snapshot()
        self.assertTrue(snap["has_stats"])
        self.assertEqual(snap["stats"]["title"], "x")
        from ubot_runner.ui import T
        for lang in ("en", "th"):
            self.assertIn("c_stats", T[lang])
