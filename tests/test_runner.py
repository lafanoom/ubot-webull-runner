"""Runner rules, against FakeBroker. Stdlib only: python -m unittest discover tests"""
import io
import json
import logging
import os
import sys
import tempfile
import unittest
import zipfile
import subprocess
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from ubot_runner import clock, config  # noqa: E402
from ubot_runner.app import Mask  # noqa: E402
from ubot_runner.broker import FakeBroker, price_text  # noqa: E402
from ubot_runner.config import Config, ConfigError, parse  # noqa: E402
from ubot_runner.engine import CID_RE, Engine  # noqa: E402
from ubot_runner.sim import simulate  # noqa: E402
from ubot_runner.state import State  # noqa: E402
from ubot_runner.strategy import Bar, Strategy, atr, ema, rsi, sma  # noqa: E402

# a regular-hours moment: Tue 06/10/2026 10:00 ET
OPEN_NOW = datetime(2026, 10, 6, 14, 0, tzinfo=timezone.utc)
CLOSED_NOW = datetime(2026, 10, 6, 22, 0, tzinfo=timezone.utc)


class Scripted(Strategy):
    """Does whatever the test put in `plan` for the next bar."""
    TAG = "utest"
    BAR = "5m"
    WARMUP = 1
    INPUTS = {"x": 1}

    def __init__(self):
        self.plan = []

    def on_bar(self, ctx, symbol, bars):
        for kind, kw in self.plan:
            getattr(ctx, kind)(**kw)
        self.plan = []


def bar(t, o, h, l_, c):
    return Bar(t, o, h, l_, c, 1000)


def make(dry=False, symbols=("F",), cash=100_000.0, **lim):
    cfg = Config(app_key="k" * 32, app_secret="s" * 32, dry_run=dry, symbols=tuple(symbols))
    for k, v in lim.items():
        setattr(cfg.limits, k, v)
    st = Scripted()
    fb = FakeBroker(cash=cash)
    fb.mark["F"] = 10.0
    fb.held = {}
    eng = Engine(st, cfg, fb, State())
    return eng, st, fb


def feed(eng, st, plan, now=OPEN_NOW, price=10.0, n=[0]):
    """Hand the strategy one new closed 5m bar with this plan."""
    n[0] += 1
    st.plan = plan
    b = bar(now - timedelta(minutes=10) + timedelta(seconds=n[0]), price, price, price, price)
    eng.run_bar("F", [b], now)


def fill_market(fb, price):
    for o in fb.orders.values():
        if o["status"] == "OPEN" and o["type"] == "MARKET":
            fb._fill(o, price)


class EntryAndStop(unittest.TestCase):
    def test_buy_fills_then_stop_rests_at_broker(self):
        eng, st, fb = make()
        feed(eng, st, [("buy", dict(symbol="F", qty=10, stop=9.0, target=12.0))])
        self.assertEqual([c[2:] for c in fb.calls], [("MARKET", "BUY", 10)])
        fill_market(fb, 10.05)
        eng.reconcile(OPEN_NOW)
        p = eng.state.position("F")
        self.assertEqual((p["qty"], p["entry"], p["stop"], p["target"]), (10, 10.05, 9.0, 12.0))
        stops = [o for o in fb.orders.values() if o["type"] == "STOP_LOSS"]
        self.assertEqual(len(stops), 1)
        self.assertEqual((stops[0]["side"], stops[0]["qty"], stops[0]["stop"]), ("SELL", 10, 9.0))
        self.assertTrue(p["stop_cid"])

    def test_target_is_watched_not_rested(self):
        eng, st, fb = make()
        feed(eng, st, [("buy", dict(symbol="F", qty=10, stop=9.0, target=12.0))])
        fill_market(fb, 10.0)
        eng.reconcile(OPEN_NOW)
        self.assertFalse([o for o in fb.orders.values() if o["type"] == "LIMIT"])
        fb.mark["F"] = 11.5
        eng.watch_targets(OPEN_NOW)
        self.assertTrue(eng.state.position("F"))
        fb.mark["F"] = 12.1
        fb.instant = True
        eng.watch_targets(OPEN_NOW)
        eng.reconcile(OPEN_NOW)
        kinds = [c[0] for c in fb.calls]
        self.assertLess(kinds.index("cancel"), len(kinds) - 1)       # the stop goes before the sale
        self.assertEqual(fb.calls[-1][2:], ("MARKET", "SELL", 10))
        self.assertIsNone(eng.state.position("F"))
        t = eng.state.trades()[-1]
        self.assertEqual((t["reason"], t["exit"]), ("target", 12.1))

    def test_stop_fill_closes_position(self):
        eng, st, fb = make()
        feed(eng, st, [("buy", dict(symbol="F", qty=5, stop=9.0))])
        fill_market(fb, 10.0)
        eng.reconcile(OPEN_NOW)
        fb.touch_stops("F", bar(OPEN_NOW, 9.5, 9.6, 8.8, 9.0))
        eng.reconcile(OPEN_NOW)
        self.assertIsNone(eng.state.position("F"))
        t = eng.state.trades()[-1]
        self.assertEqual((t["reason"], t["exit"], t["pnl"]), ("stop", 9.0, -5.0))

    def test_failed_sale_puts_stop_back(self):
        eng, st, fb = make()
        feed(eng, st, [("buy", dict(symbol="F", qty=5, stop=9.0))])
        fill_market(fb, 10.0)
        eng.reconcile(OPEN_NOW)
        fb.refuse["ubutestX"] = "OPENAPI_TICKER_IS_DENY"
        feed(eng, st, [("sell", dict(symbol="F"))])
        p = eng.state.position("F")
        self.assertIsNone(p["closing"])
        live = [o for o in fb.orders.values() if o["type"] == "STOP_LOSS" and o["status"] == "OPEN"]
        self.assertEqual(len(live), 1)

    def test_close_before_open_before_modify(self):
        eng, st, fb = make(symbols=("F",))
        feed(eng, st, [("buy", dict(symbol="F", qty=5, stop=9.0))])
        fill_market(fb, 10.0)
        eng.reconcile(OPEN_NOW)
        fb.instant = True
        feed(eng, st, [("move_stop", dict(symbol="F", price=9.5)), ("buy", dict(symbol="F", qty=1)),
                       ("sell", dict(symbol="F"))])
        order = [c[0] + ":" + (c[3] if c[0] == "place" else "") for c in fb.calls[2:]]
        self.assertEqual(order[0], "cancel:")          # sell first: cancel the stop
        self.assertIn("place:SELL", order)

    def test_stop_only_moves_up(self):
        eng, st, fb = make()
        feed(eng, st, [("buy", dict(symbol="F", qty=5, stop=9.0))])
        fill_market(fb, 10.0)
        eng.reconcile(OPEN_NOW)
        feed(eng, st, [("move_stop", dict(symbol="F", price=8.0))])
        self.assertEqual(eng.state.position("F")["stop"], 9.0)
        feed(eng, st, [("move_stop", dict(symbol="F", price=9.4))])
        self.assertEqual(eng.state.position("F")["stop"], 9.4)
        self.assertEqual([o["stop"] for o in fb.orders.values() if o["type"] == "STOP_LOSS"], [9.4])


class Timeouts(unittest.TestCase):
    def setUp(self):
        self.eng, self.st, self.fb = make()
        feed(self.eng, self.st, [("buy", dict(symbol="F", qty=5, stop=9.0))])
        fill_market(self.fb, 10.0)
        self.eng.reconcile(OPEN_NOW)
        self.real = self.fb.replace

    def test_timeout_that_landed_is_not_sent_twice(self):
        from ubot_runner.broker import Reply

        def landed(cid, qty, stop_price=None, limit_price=None):
            self.real(cid, qty, stop_price=stop_price)
            return Reply(False, "ClientException", "Read timed out", transient=True)
        self.fb.replace = landed
        feed(self.eng, self.st, [("move_stop", dict(symbol="F", price=9.5))])
        self.assertEqual(self.eng.state.position("F")["stop"], 9.5)
        self.assertEqual(len([c for c in self.fb.calls if c[0] == "replace"]), 1)

    def test_timeout_that_was_lost_is_retried_once(self):
        from ubot_runner.broker import Reply
        tries = []

        def lost_then_ok(cid, qty, stop_price=None, limit_price=None):
            tries.append(stop_price)
            if len(tries) == 1:
                return Reply(False, "ClientException", "Read timed out", transient=True)
            return self.real(cid, qty, stop_price=stop_price)
        self.fb.replace = lost_then_ok
        feed(self.eng, self.st, [("move_stop", dict(symbol="F", price=9.5))])
        self.assertEqual(tries, [9.5, 9.5])
        self.assertEqual(self.eng.state.position("F")["stop"], 9.5)


class Guards(unittest.TestCase):
    def test_dry_run_sends_nothing(self):
        eng, st, fb = make(dry=True)
        feed(eng, st, [("buy", dict(symbol="F", qty=5, stop=9.0))])
        self.assertEqual(fb.calls, [])

    def test_empty_symbols_sends_nothing(self):
        eng, st, fb = make(symbols=())
        feed(eng, st, [("buy", dict(symbol="F", qty=5))])
        self.assertEqual(fb.calls, [])

    def test_stop_file(self):
        eng, st, fb = make()
        with tempfile.TemporaryDirectory() as d:
            eng.stop_file = os.path.join(d, "STOP")
            open(eng.stop_file, "w").close()
            feed(eng, st, [("buy", dict(symbol="F", qty=5))])
        self.assertEqual(fb.calls, [])

    def test_closed_market(self):
        eng, st, fb = make()
        feed(eng, st, [("buy", dict(symbol="F", qty=5))], now=CLOSED_NOW)
        self.assertEqual(fb.calls, [])

    def test_caps(self):
        eng, st, fb = make(max_notional_per_order=100)
        feed(eng, st, [("buy", dict(symbol="F", qty=11))])               # 110 USD
        self.assertEqual(fb.calls, [])
        feed(eng, st, [("buy", dict(symbol="F", notional=100))])         # 10 shares
        self.assertEqual(fb.calls[-1][2:], ("MARKET", "BUY", 10))
        feed(eng, st, [("buy", dict(symbol="F", notional=5))])           # in flight already
        self.assertEqual(len(fb.calls), 1)

    def test_bad_stop_and_target(self):
        eng, st, fb = make()
        feed(eng, st, [("buy", dict(symbol="F", qty=1, stop=10.5))])
        feed(eng, st, [("buy", dict(symbol="F", qty=1, target=9.0))])
        feed(eng, st, [("buy", dict(symbol="F", qty=1, notional=10))])
        self.assertEqual(fb.calls, [])

    def test_max_open_and_orders_per_day(self):
        eng, st, fb = make(symbols=("F", "KO"), max_open_positions=1)
        fb.mark["KO"] = 10.0
        feed(eng, st, [("buy", dict(symbol="F", qty=1)), ("buy", dict(symbol="KO", qty=1))])
        self.assertEqual(len(fb.calls), 1)
        eng2, st2, fb2 = make(max_orders_per_day=0)
        feed(eng2, st2, [("buy", dict(symbol="F", qty=1))])
        self.assertEqual(fb2.calls, [])

    def test_daily_loss(self):
        eng, st, fb = make(cash=1000.0, daily_loss_pct=3)
        eng.day(OPEN_NOW)
        eng.state.open_position("KO", 10, 10.0, None, None, OPEN_NOW)
        eng.state.close_position("KO", 6.0, "stop", OPEN_NOW)              # -40 USD = 4 %
        feed(eng, st, [("buy", dict(symbol="F", qty=1))])
        self.assertEqual(fb.calls, [])

    def test_refusal_is_not_retried(self):
        eng, st, fb = make()
        fb.refuse["ubutestE"] = "OPENAPI_ORDER_PRICE_ILLEGAL"
        feed(eng, st, [("buy", dict(symbol="F", qty=1))])
        self.assertEqual(len(fb.calls), 1)
        o = eng.state.db.execute("SELECT status FROM orders").fetchone()
        self.assertEqual(o["status"], "refused")


class Restart(unittest.TestCase):
    def test_state_survives_and_unsent_order_is_found(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.db")
            eng, st, fb = make()
            eng.state = State(path)
            feed(eng, st, [("buy", dict(symbol="F", qty=5, stop=9.0))])
            fill_market(fb, 10.0)
            eng.reconcile(OPEN_NOW)
            eng.state.add_order("ubutestSdeadbeef0001", "F", "stop", "SELL", 5)   # written, never sent
            eng.state.close()
            eng2 = Engine(st, eng.cfg, fb, State(path))
            eng2.recover(OPEN_NOW)
            self.assertEqual(eng2.state.position("F")["qty"], 5)
            self.assertEqual(eng2.state.order("ubutestSdeadbeef0001")["status"], "lost")
            eng2.state.close()

    def test_lost_stop_is_placed_again(self):
        eng, st, fb = make()
        fb.held["F"] = 5
        eng.state.open_position("F", 5, 10.0, 9.0, None, OPEN_NOW)
        eng.state.set_position("F", stop_cid="ubutestSdeadbeef0002")
        eng.state.add_order("ubutestSdeadbeef0002", "F", "stop", "SELL", 5)
        eng.recover(OPEN_NOW)
        eng.protect(OPEN_NOW)
        self.assertEqual(len([o for o in fb.orders.values() if o["type"] == "STOP_LOSS"]), 1)

    def test_shares_sold_by_hand(self):
        eng, st, fb = make()
        eng.sim = False
        feed(eng, st, [("buy", dict(symbol="F", qty=5, stop=9.0))])
        fill_market(fb, 10.0)
        eng.reconcile(OPEN_NOW)
        stop = eng.state.position("F")["stop_cid"]
        fb.held["F"] = 0
        eng.sync_positions(OPEN_NOW)
        self.assertIsNone(eng.state.position("F"))
        self.assertEqual(fb.orders[stop]["status"], "CANCELED")


class Clock(unittest.TestCase):
    def test_holidays_2026(self):
        h = clock.holidays(2026)
        for d in (date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25),
                  date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25)):
            self.assertIn(d, h, d)
        self.assertEqual(len(h), 10)

    def test_new_year_on_saturday_not_observed(self):
        self.assertNotIn(date(2021, 12, 31), clock.holidays(2022))
        self.assertTrue(clock.is_trading_day(date(2021, 12, 31)))

    def test_dst_and_early_close(self):
        self.assertEqual(clock.session(date(2026, 7, 6))[0].hour, 13)     # EDT
        self.assertEqual(clock.session(date(2026, 12, 7))[0].hour, 14)    # EST
        self.assertEqual(clock.close_time(date(2026, 11, 27)).hour, 13)
        self.assertTrue(clock.regular_open(OPEN_NOW))
        self.assertFalse(clock.regular_open(CLOSED_NOW))
        self.assertFalse(clock.regular_open(datetime(2026, 10, 10, 15, tzinfo=timezone.utc)))  # Saturday

    def test_builtin_eastern_matches_tz_database(self):
        try:
            from zoneinfo import ZoneInfo
            real = ZoneInfo("America/New_York")
        except Exception:
            self.skipTest("no tz database here")
        mine = clock._Eastern()
        t = datetime(2024, 1, 1, tzinfo=timezone.utc)
        while t < datetime(2029, 1, 1, tzinfo=timezone.utc):
            a, b = t.astimezone(real), t.astimezone(mine)
            self.assertEqual(a.replace(tzinfo=None), b.replace(tzinfo=None), t)
            for local in (a.replace(tzinfo=None),):
                if local.fold == 0:
                    self.assertEqual(datetime.combine(local.date(), local.time(), real).utcoffset(),
                                     datetime.combine(local.date(), local.time(), mine).utcoffset(), local)
            t += timedelta(minutes=37)

    def test_bar_closed(self):
        t = datetime(2026, 10, 6, 13, 30, tzinfo=timezone.utc)
        self.assertFalse(clock.bar_closed(t, "5m", t + timedelta(minutes=4)))
        self.assertTrue(clock.bar_closed(t, "5m", t + timedelta(minutes=5)))
        d = datetime(2026, 10, 6, 4, tzinfo=timezone.utc)              # Webull daily bar time
        self.assertFalse(clock.bar_closed(d, "1d", OPEN_NOW))
        self.assertTrue(clock.bar_closed(d, "1d", CLOSED_NOW))
        last_hour = datetime(2026, 10, 6, 19, 30, tzinfo=timezone.utc)  # 15:30 ET
        self.assertTrue(clock.bar_closed(last_hour, "1h", datetime(2026, 10, 6, 20, 0, tzinfo=timezone.utc)))


class ConfigRules(unittest.TestCase):
    def doc(self, **kw):
        d = {"app_key": "k" * 32, "app_secret": "s" * 32}
        d.update(kw)
        return d

    def test_safe_defaults(self):
        c = parse(self.doc())
        self.assertTrue(c.dry_run)
        self.assertEqual(c.symbols, ())
        self.assertNotIn("k" * 32, repr(c))

    def test_factory_refuses_production(self):
        os.environ["UBOT_FACTORY"] = "1"
        try:
            with self.assertRaises(ConfigError):
                parse(self.doc())
            parse(self.doc(host="th-api.uat.webullbroker.com"))
        finally:
            del os.environ["UBOT_FACTORY"]

    def test_inputs(self):
        c = parse(self.doc(inputs={"fast": 10}), {"fast": 20, "on": True})
        self.assertEqual(c.inputs, {"fast": 10, "on": True})
        for bad in ({"nope": 1}, {"fast": "x"}, {"fast": 1.5}, {"on": 1}):
            with self.assertRaises(ConfigError):
                parse(self.doc(inputs=bad), {"fast": 20, "on": True})

    def test_symbols_and_keys(self):
        self.assertEqual(parse(self.doc(symbols=["ko", "KO", "brk.b"])).symbols, ("KO", "BRK.B"))
        for bad in (dict(symbols=["A B"]), dict(app_key=""), dict(dry_run="no"), dict(host="https://x")):
            with self.assertRaises(ConfigError):
                parse(self.doc(**bad))

    def test_template_is_valid_toml_and_safe(self):
        import tomllib
        d = tomllib.loads(config.TEMPLATE)
        self.assertIs(d["dry_run"], True)
        self.assertEqual(d["symbols"], [])


class Misc(unittest.TestCase):
    def test_client_order_ids(self):
        eng, _, _ = make()
        eng.tag = "u3733abcd12"[:10]
        for k in "ESX":
            c = eng.cid(k)
            self.assertTrue(CID_RE.match(c) and len(c) <= 32, c)

    def test_prices(self):
        self.assertEqual(price_text(12.345), "12.35")   # never sub-penny above $1
        self.assertEqual(price_text(0.12345), "0.1235")

    def test_mask(self):
        rec = logging.LogRecord("ubot", logging.INFO, "", 0, "key %s ok", ("ABCDEF123456",), None)
        Mask(["ABCDEF123456"]).filter(rec)
        self.assertEqual(rec.getMessage(), "key <hidden> ok")

    def test_indicators(self):
        xs = [float(i) for i in range(1, 31)]
        self.assertEqual(sma(xs, 5), 28.0)
        self.assertAlmostEqual(ema([1.0] * 20, 10), 1.0)
        self.assertEqual(rsi(xs, 14), 100.0)
        t = datetime(2026, 1, 1, tzinfo=timezone.utc)
        bs = [Bar(t, 10, 11, 9, 10, 0) for _ in range(20)]
        self.assertAlmostEqual(atr(bs, 14), 2.0)


class Simulation(unittest.TestCase):
    def test_example_strategy_runs(self):
        sys.path.insert(0, os.path.join(HERE, "examples"))
        from UBotExample import UBotExample
        t0 = datetime(2024, 1, 2, 5, tzinfo=timezone.utc)
        bars, px = [], 50.0
        import math
        for i in range(400):
            px = 50 + 10 * math.sin(i / 25) + i * 0.02
            bars.append(Bar(t0 + timedelta(days=i), px - 0.2, px + 1.0, px - 1.0, px, 1e6))
        res = simulate(UBotExample(), {"KO": bars})
        self.assertGreater(res["trades"], 0)
        self.assertEqual(res["bars"], 400)


class Build(unittest.TestCase):
    def test_pyz_runs_simulation(self):
        sys.path.insert(0, os.path.join(HERE, "tools"))
        from build_pyz import build
        from ubot_runner.sim import dump_bars
        with tempfile.TemporaryDirectory() as d:
            out = build(os.path.join(HERE, "examples", "UBotExample.py"), os.path.join(d, "Example Trend v1.00.pyz"))
            names = zipfile.ZipFile(out).namelist()
            self.assertIn("UBotExample.pyc", names)
            self.assertFalse([n for n in names if n.endswith(".py") and n != "__main__.py"])
            t0 = datetime(2024, 1, 2, 5, tzinfo=timezone.utc)
            import math
            bars = [Bar(t0 + timedelta(days=i), *(4 * [50 + 10 * math.sin(i / 25)]), 1) for i in range(200)]
            dump_bars({"KO": bars}, os.path.join(d, "b.json"))
            r = subprocess.run([sys.executable, out, "simulate", "--bars", os.path.join(d, "b.json")],
                               capture_output=True, text=True, cwd=d, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("[ACCEPT] entry KO", r.stdout)
            self.assertIn("runner 0.3.0", r.stdout)
            res = json.loads([ln for ln in r.stdout.splitlines() if ln.startswith("[RESULT] ")][0][9:])
            self.assertGreater(res["trades"], 0)
            r0 = subprocess.run([sys.executable, out, "simulate", "--bars", os.path.join(d, "b.json"),
                                 "--inputs", '{"stop_atr": 0, "target_atr": 0}'],
                                capture_output=True, text=True, cwd=d, timeout=60)
            self.assertEqual(r0.returncode, 0, r0.stderr)
            self.assertNotIn('"target": ', r0.stdout)
            bad = subprocess.run([sys.executable, out, "simulate", "--bars", os.path.join(d, "b.json"),
                                  "--inputs", '{"nope": 1}'], capture_output=True, text=True, cwd=d, timeout=60)
            self.assertEqual(bad.returncode, 2)


if __name__ == "__main__":
    unittest.main()
