"""Engine rules behind the window (runner 0.2): run modes, buys by hand, settings
that reach held positions, the strategy's own buttons. Stdlib only."""
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import timedelta

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))

from test_runner import CLOSED_NOW, OPEN_NOW, Scripted, bar, feed, fill_market, make  # noqa: E402
from ubot_runner.state import State  # noqa: E402
from ubot_runner.strategy import Strategy  # noqa: E402


def hold(eng, st, fb, qty=10, stop=9.0, target=12.0, price=10.0):
    feed(eng, st, [("buy", dict(symbol="F", qty=qty, stop=stop, target=target))], price=price)
    fill_market(fb, price)
    eng.reconcile(OPEN_NOW)
    return eng.state.position("F")


class RunModes(unittest.TestCase):
    def test_paused_refuses_the_programs_buys_but_keeps_watching(self):
        eng, st, fb = make()
        hold(eng, st, fb)
        eng.set_run("paused", OPEN_NOW)
        eng.state.close_position("F", 10.0, "test", OPEN_NOW)
        fb.held = {}
        n = len(fb.calls)
        feed(eng, st, [("buy", dict(symbol="F", qty=5, stop=9.0))])
        self.assertEqual(len(fb.calls), n)                       # nothing sent
        self.assertEqual(eng.run_mode, "paused")

    def test_paused_still_takes_the_target(self):
        eng, st, fb = make()
        hold(eng, st, fb)
        eng.set_run("paused", OPEN_NOW)
        fb.mark["F"] = 12.5
        fb.instant = True
        eng.watch_targets(OPEN_NOW)
        eng.reconcile(OPEN_NOW)
        self.assertIsNone(eng.state.position("F"))
        self.assertEqual(eng.state.trades()[-1]["reason"], "target")

    def test_paused_allows_a_buy_by_hand(self):
        eng, st, fb = make()
        eng.set_run("paused", OPEN_NOW)
        self.assertEqual(eng.manual_buy("F", 5, 9.0, 12.0, OPEN_NOW), "")
        self.assertEqual(fb.calls[-1][2:], ("MARKET", "BUY", 5))

    def test_stop_all_sells_everything_then_does_nothing(self):
        eng, st, fb = make()
        hold(eng, st, fb)
        fb.instant = True
        eng.set_run("off", OPEN_NOW)
        eng.reconcile(OPEN_NOW)
        self.assertIsNone(eng.state.position("F"))
        self.assertEqual(eng.state.trades()[-1]["reason"], "stop all")
        self.assertEqual(eng.manual_buy("F", 5, 9.0, None, OPEN_NOW), "the program is stopped")
        n = len(fb.calls)
        feed(eng, st, [("buy", dict(symbol="F", qty=5, stop=9.0))])
        self.assertEqual(len(fb.calls), n)

    def test_stop_all_when_closed_waits_for_the_open(self):
        eng, st, fb = make()
        hold(eng, st, fb)
        eng.set_run("off", CLOSED_NOW)
        self.assertTrue(eng.state.position("F"))                 # market closed: still held, stop still rests
        fb.instant = True
        eng.step(OPEN_NOW)
        self.assertIsNone(eng.state.position("F"))

    def test_mode_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.db")
            eng, st, fb = make()
            eng.state = State(path)
            eng.set_run("paused", OPEN_NOW)
            eng.state.close()
            eng2, _, _ = make()
            eng2.state = State(path)
            self.assertEqual(eng2.run_mode, "paused")
            eng2.state.close()


class ByHand(unittest.TestCase):
    def test_buy_by_hand_is_looked_after_like_the_programs(self):
        eng, st, fb = make()
        eng.manual_buy("F", 10, 9.0, 12.0, OPEN_NOW)
        fill_market(fb, 10.0)
        eng.reconcile(OPEN_NOW)
        p = eng.state.position("F")
        self.assertEqual((p["opened_by"], p["stop"], p["target"]), ("you", 9.0, 12.0))
        self.assertTrue([o for o in fb.orders.values() if o["type"] == "STOP_LOSS"])
        fb.mark["F"] = 12.2
        fb.instant = True
        eng.watch_targets(OPEN_NOW)
        eng.reconcile(OPEN_NOW)
        t = eng.state.trades()[-1]
        self.assertEqual((t["reason"], t["opened_by"]), ("target", "you"))

    def test_buy_by_hand_counts_in_the_caps(self):
        eng, st, fb = make(max_open_positions=1)
        hold(eng, st, fb)
        fb.mark["G"] = 5.0
        eng.cfg.symbols = ("F", "G")
        self.assertIn("max_open_positions", eng.manual_buy("G", 1, 4.0, None, OPEN_NOW))

    def test_buy_by_hand_obeys_market_hours_and_symbols(self):
        eng, st, fb = make()
        self.assertEqual(eng.manual_buy("F", 1, 9.0, None, CLOSED_NOW), "market is closed")
        fb.mark["ZZZ"] = 3.0
        self.assertEqual(eng.manual_buy("ZZZ", 1, 2.0, None, OPEN_NOW), "not in your symbols list")

    def test_limit_buy(self):
        eng, st, fb = make()
        eng.manual_buy("F", 3, 9.0, None, OPEN_NOW, limit=9.8)
        o = list(fb.orders.values())[-1]
        self.assertEqual((o["type"], o["limit"]), ("LIMIT", 9.8))

    def test_sell_by_hand(self):
        eng, st, fb = make()
        hold(eng, st, fb)
        fb.instant = True
        self.assertEqual(eng.manual_sell("F", OPEN_NOW), "")
        eng.reconcile(OPEN_NOW)
        self.assertEqual(eng.state.trades()[-1]["reason"], "sold by you")

    def test_max_total_notional(self):
        eng, st, fb = make(max_total_notional=150)
        hold(eng, st, fb)                                         # 100 USD in use
        eng.cfg.symbols = ("F", "G")
        fb.mark["G"] = 10.0
        self.assertIn("max_total_notional", eng.manual_buy("G", 6, 9.0, None, OPEN_NOW))
        self.assertEqual(eng.manual_buy("G", 5, 9.0, None, OPEN_NOW), "")


class Leveled(Scripted):
    INPUTS = {"x": 1, "stop_off": 1.0, "target_off": 2.0}

    def levels(self, ctx, symbol, bars, position):
        return position.entry - ctx.inputs["stop_off"], position.entry + ctx.inputs["target_off"]


class NewSettings(unittest.TestCase):
    def setUp(self):
        self.eng, _, self.fb = make()
        self.st = Leveled()
        self.eng.strategy = self.st
        self.fb.history["F"] = [bar(OPEN_NOW - timedelta(minutes=30), 10, 10, 10, 10)]
        self.eng.cfg.inputs = dict(Leveled.INPUTS)

    def save(self, **inputs):
        cfg = self.eng.cfg
        cfg.inputs = {**cfg.inputs, **inputs}
        self.eng.apply_config(cfg, OPEN_NOW)

    def test_new_inputs_move_the_held_stop_and_target(self):
        hold(self.eng, self.st, self.fb, stop=9.0, target=12.0)
        self.save(stop_off=0.5, target_off=3.0)
        p = self.eng.state.position("F")
        self.assertEqual((p["stop"], p["target"]), (9.5, 13.0))
        stop = self.fb.orders[p["stop_cid"]]
        self.assertEqual(stop["stop"], 9.5)

    def test_saved_settings_may_move_the_stop_down(self):
        hold(self.eng, self.st, self.fb, stop=9.0, target=12.0)
        self.save(stop_off=2.0)
        self.assertEqual(self.eng.state.position("F")["stop"], 8.0)

    def test_the_strategy_still_only_moves_it_up(self):
        hold(self.eng, self.st, self.fb, stop=9.0, target=12.0)
        feed(self.eng, self.st, [("move_stop", dict(symbol="F", price=8.0))])
        self.assertEqual(self.eng.state.position("F")["stop"], 9.0)

    def test_buys_by_hand_keep_what_the_customer_typed(self):
        self.eng.manual_buy("F", 10, 9.0, 12.0, OPEN_NOW)
        fill_market(self.fb, 10.0)
        self.eng.reconcile(OPEN_NOW)
        self.save(stop_off=0.5, target_off=3.0)
        p = self.eng.state.position("F")
        self.assertEqual((p["stop"], p["target"]), (9.0, 12.0))

    def test_without_levels_nothing_moves(self):
        eng, st, fb = make()
        hold(eng, st, fb, stop=9.0, target=12.0)
        n = len(fb.calls)
        eng.apply_config(eng.cfg, OPEN_NOW)
        self.assertEqual(len(fb.calls), n)


class Buttons(unittest.TestCase):
    def test_a_button_orders_through_the_runner(self):
        class WithButton(Scripted):
            UI = {"buttons": [{"id": "sell_all", "label": "Sell all"}]}

            def on_button(self, ctx, button):
                for s in ctx.positions():
                    ctx.sell(s, reason="button")

        eng, _, fb = make()
        st = WithButton()
        eng.strategy = st
        hold(eng, st, fb)
        fb.instant = True
        eng.press("sell_all", OPEN_NOW)
        eng.reconcile(OPEN_NOW)
        self.assertEqual(eng.state.trades()[-1]["reason"], "button")

    def test_a_button_buy_still_meets_the_rules(self):
        class Greedy(Scripted):
            def on_button(self, ctx, button):
                ctx.buy("F", qty=10_000, stop=9.0)

        eng, _, fb = make(max_notional_per_order=500)
        eng.strategy = Greedy()
        n = len(fb.calls)
        eng.press("x", OPEN_NOW)
        self.assertEqual(len(fb.calls), n)


class OldStateFile(unittest.TestCase):
    def test_a_0_1_database_gains_opened_by(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "old.db")
            db = sqlite3.connect(path)
            db.executescript("""
CREATE TABLE positions (symbol TEXT PRIMARY KEY, qty INTEGER NOT NULL, entry REAL NOT NULL, stop REAL, target REAL,
  stop_cid TEXT, exit_cid TEXT, closing TEXT, close_reason TEXT, opened_at TEXT NOT NULL);
CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, qty INTEGER NOT NULL,
  entry REAL NOT NULL, exit REAL NOT NULL, pnl REAL NOT NULL, reason TEXT, opened_at TEXT NOT NULL, closed_at TEXT NOT NULL);
INSERT INTO positions VALUES ('F',10,10.0,9.0,12.0,NULL,NULL,NULL,NULL,'2026-10-06T14:00:00+00:00');
""")
            db.commit()
            db.close()
            s = State(path)
            self.assertEqual(s.position("F")["opened_by"], "program")
            s.close()


class SettingsFile(unittest.TestCase):
    def test_dump_reads_back_the_same(self):
        from ubot_runner.config import Config, dump, load
        cfg = Config(app_key='k"ey\\1234567890', app_secret="secret-1234567890", dry_run=False,
                     symbols=("KO", "BRK.B"), lang="th", on_close="tray",
                     inputs={"fast": 15, "stop_atr": 2.5, "on": True, "label": "a b"})
        cfg.limits.max_total_notional = 12000
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "webull.toml")
            dump(cfg, p)
            back = load(p, {"fast": 20, "stop_atr": 2.0, "on": False, "label": "x"})
            self.assertFalse(os.path.exists(p + ".tmp"))
        self.assertEqual((back.app_key, back.app_secret, back.dry_run, back.symbols, back.lang, back.on_close),
                         (cfg.app_key, cfg.app_secret, False, ("KO", "BRK.B"), "th", "tray"))
        self.assertEqual(back.inputs, cfg.inputs)
        self.assertEqual(back.limits.max_total_notional, 12000)

    def test_window_can_start_without_keys(self):
        from ubot_runner.config import NoKeys, load
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "webull.toml")
            cfg = load(p, {}, need_keys=False)          # writes the template, then reads it
            self.assertEqual((cfg.app_key, cfg.dry_run, cfg.symbols), ("", True, ()))
            with self.assertRaises(NoKeys):
                load(p, {})

    def test_window_values_are_checked(self):
        from ubot_runner.config import ConfigError, parse
        base = {"app_key": "k" * 20, "app_secret": "s" * 20}
        with self.assertRaises(ConfigError):
            parse({**base, "window": {"on_close": "hide"}})
        with self.assertRaises(ConfigError):
            parse({**base, "window": {"lang": "jp"}})


class UiSpec(unittest.TestCase):
    def test_good(self):
        from ubot_runner.ui import check_ui
        self.assertEqual(check_ui({"title": "Gap and go", "lang": "en", "accent": "#22D3EE", "hide": ["chart"],
                                   "values": ["RSI"], "buttons": [{"id": "sell_all", "label": "Sell all"}]}), [])
        self.assertEqual(check_ui({}), [])

    def test_bad(self):
        from ubot_runner.ui import check_ui, ui_of
        for bad in ({"script": "x"}, {"accent": "#22C55E"}, {"accent": "#F43F5E"}, {"accent": "red"},
                    {"hide": ["keys"]}, {"hide": ["chart", "chart"]}, {"values": []},
                    {"values": ["a", "b", "c", "d"]}, {"buttons": [{"id": "Bad Id", "label": "x"}]},
                    {"buttons": [{"id": "a", "label": "x", "url": "http://x"}]},
                    {"buttons": [{"id": "a", "label": str(i)} for i in range(5)]}, {"title": "x" * 41}, "nope"):
            self.assertTrue(check_ui(bad), bad)

        class S(Strategy):
            UI = {"accent": "#00FF00"}
        self.assertEqual(ui_of(S()), {})                          # refused = the standard window

    def test_both_languages_have_every_word(self):
        from ubot_runner.ui import T
        self.assertEqual(set(T["en"]), set(T["th"]))


class Controller(unittest.TestCase):
    """The window's commands, without a window."""

    def live(self):
        from ubot_runner.config import dump
        from ubot_runner.live import Live, Ring
        eng, st, fb = make()
        d = tempfile.mkdtemp()
        lv = Live(eng, os.path.join(d, "webull.toml"), dump, Ring(), poll_seconds=1)
        return lv, eng, fb

    def test_commands_run_on_the_engine_thread(self):
        import threading
        lv, eng, fb = self.live()
        seen = []
        eng.set_run = lambda mode, now: seen.append((mode, threading.current_thread().name))
        lv.start()
        try:
            lv.ask("run", "paused", wait=True, timeout=10)
        finally:
            lv.stop()
        self.assertEqual(seen, [("paused", "ubot-engine")])

    def test_save_refuses_unknown_tickers(self):
        lv, eng, fb = self.live()
        cfg = eng.cfg
        new = type(cfg)(**{**cfg.__dict__, "symbols": ("F", "NOPE")})
        self.assertIn("NOPE", lv.do_save(OPEN_NOW, new))
        self.assertEqual(eng.cfg.symbols, ("F",))
        fb.mark["NOPE"] = 3.0
        self.assertEqual(lv.do_save(OPEN_NOW, new), "")
        self.assertEqual(eng.cfg.symbols, ("F", "NOPE"))
        self.assertTrue(os.path.exists(lv.cfg_path))

    def test_snapshot(self):
        lv, eng, fb = self.live()
        hold(eng, eng.strategy, fb)
        fb.mark["F"] = 10.5
        lv.refresh(OPEN_NOW)
        s = lv.snapshot()
        p = s["positions"][0]
        self.assertEqual((p["symbol"], p["by"], round(p["pl"], 2)), ("F", "program", 5.0))
        self.assertEqual(s["run"], "on")
        self.assertIn(s["market"][0], ("open", "closed"))

    def test_new_keys_replace_old_only_when_they_work(self):
        lv, eng, fb = self.live()
        old = eng.cfg.app_key

        def bad(cfg):
            raise RuntimeError("Webull says no")
        self.assertEqual(lv.do_keys(OPEN_NOW, "x" * 20, "y" * 20, bad), "Webull says no")
        self.assertEqual(eng.cfg.app_key, old)
        self.assertEqual(lv.do_keys(OPEN_NOW, "x" * 20, "y" * 20, lambda c: fb), "")
        self.assertEqual(eng.cfg.app_key, "x" * 20)


if __name__ == "__main__":
    unittest.main()
