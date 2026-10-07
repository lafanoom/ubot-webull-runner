"""The program's window over a fake broker and moving prices - for looking at it
and for screenshots. Nothing here talks to Webull.

    python tools/window_demo.py [--lang th|en] [--dry] [--shot out.png] [--strategy examples/UBotExample.py]
"""
import argparse
import os
import random
import sys
import tempfile
import threading
import time
import tkinter as tk
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from ubot_runner import clock, gui  # noqa: E402
from ubot_runner.__main__ import load_strategy  # noqa: E402
from ubot_runner.broker import FakeBroker  # noqa: E402
from ubot_runner.config import Config, dump  # noqa: E402
from ubot_runner.engine import Engine  # noqa: E402
from ubot_runner.live import Live, Ring  # noqa: E402
from ubot_runner.app import setup_logging  # noqa: E402
from ubot_runner.state import State, iso  # noqa: E402
from ubot_runner.strategy import Bar  # noqa: E402

PRICES = {"NVDA": 182.4, "F": 11.82, "AMD": 164.1, "PLTR": 41.05, "TSLA": 251.3}


def fake_history(symbol, p, now, n=78):
    rnd = random.Random(symbol)
    start = now - timedelta(minutes=5 * n)
    out, px = [], p * (1 - rnd.uniform(-0.02, 0.02))
    for i in range(n):
        o = px
        px = max(0.5, px * (1 + rnd.gauss(0, 0.003)))
        out.append(Bar(start + timedelta(minutes=5 * i), o, max(o, px) * 1.001, min(o, px) * 0.999, px, 1e5))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", default="th")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--shot")
    ap.add_argument("--strategy", default=os.path.join(HERE, "examples", "UBotExample.py"))
    ap.add_argument("--view", default="chart")
    ap.add_argument("--walk", help="click through the dialogs and save screenshots in this folder")
    ap.add_argument("--no-keys", action="store_true", help="start on the connect screen (no keys yet)")
    ap.add_argument("--size", help="window size WxH instead of maximised, e.g. 1280x720")
    args = ap.parse_args()

    strategy = load_strategy(args.strategy)()
    d = tempfile.mkdtemp(prefix="ubot-demo-")
    cfg = Config(app_key="" if args.no_keys else "demo-key-0000a91f",
                 app_secret="" if args.no_keys else "demo-secret-000000", dry_run=args.dry,
                 symbols=tuple(PRICES), lang=args.lang, inputs=dict(strategy.INPUTS))
    cfg.limits.max_total_notional = 12000
    cfg.limits.max_notional_per_order = 6000
    cfg_path = os.path.join(d, "webull.toml")
    dump(cfg, cfg_path)
    now = datetime.now(timezone.utc)
    fb = FakeBroker(cash=22680.0)
    fb.account_id = "84214821"
    fb.instant = True
    for s, p in PRICES.items():
        fb.mark[s] = p
        fb.history[s] = fake_history(s, p, now)
    st = State(os.path.join(d, "demo.db"))
    # a little history: two held positions (one by hand), closed trades, equity days
    st.open_position("NVDA", 30, 179.20, 174.50, 190.00, now - timedelta(hours=3))
    st.open_position("F", 400, 11.93, 11.40, 12.60, now - timedelta(hours=1), by="you")
    fb.held = {"NVDA": 30, "F": 400}
    for i, (sym, q, a, b, why, by) in enumerate((("AMD", 20, 160.2, 165.8, "target", "program"),
                                                 ("PLTR", 50, 40.3, 39.7, "stop", "program"),
                                                 ("F", 300, 11.5, 11.88, "sold by you", "you"),
                                                 ("NVDA", 25, 176.1, 178.9, "program", "program"),
                                                 ("TSLA", 8, 248.4, 242.1, "stop", "program"))):
        t = now - timedelta(days=i, hours=2)
        st.db.execute("INSERT INTO trades(symbol,qty,entry,exit,pnl,reason,opened_at,closed_at,opened_by)"
                      " VALUES(?,?,?,?,?,?,?,?,?)", (sym, q, a, b, (b - a) * q, why, iso(t - timedelta(hours=3)), iso(t), by))
    st.db.commit()
    st.put("equity_days", [[(now - timedelta(days=29 - i)).date().isoformat(), 22265 + i * 14 + random.Random(i).uniform(-80, 80)]
                           for i in range(30)])

    ring = Ring()
    setup_logging(os.path.join(d, "demo.log"), cfg.secrets(), extra=[ring])

    def connect(c):
        time.sleep(0.5)
        return fb

    def make_live(c, broker):
        eng = Engine(strategy, c, broker, st, sim=True)
        live = Live(eng, cfg_path, dump, ring, poll_seconds=3)
        live.refresh(datetime.now(timezone.utc))
        live.start()
        return live

    def wiggle():
        rnd = random.Random(7)
        while True:
            time.sleep(2)
            for s in PRICES:
                fb.mark[s] = round(fb.mark[s] * (1 + rnd.gauss(0, 0.0015)), 2)
    threading.Thread(target=wiggle, daemon=True).start()

    root = tk.Tk()
    win = gui.Window(root, strategy.NAME or "Demo", "v1.00", strategy, cfg, cfg_path, connect, dump, make_live)
    if args.size:
        root.state("normal")
        root.geometry(args.size + "+0+0")
    if args.view == "settings":
        root.after(4000, lambda: win.set_view("settings"))
    if args.walk:
        from PIL import ImageGrab
        os.makedirs(args.walk, exist_ok=True)
        steps = []

        def grab(name):
            end = time.monotonic() + 1.5
            while time.monotonic() < end:
                root.update()
                time.sleep(0.03)
            ImageGrab.grab().save(os.path.join(args.walk, name + ".png"))
            d = top_dialog()
            print("[WALK]", name, "dialog:", d and (d.winfo_exists() and (d.state(), d.winfo_viewable(), d.winfo_rootx(),
                  d.winfo_rooty(), d.winfo_width(), d.winfo_height())), flush=True)

        def top_dialog():
            kids = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
            return kids[-1] if kids else None

        def close_dialog():
            d = top_dialog()
            if d:
                d.close()

        def check_save():
            win.form["in:fast"].delete(0, "end")
            win.form["in:fast"].insert(0, "15")
            win.touch()
            win.save_settings()

        def report():
            eng = win.live.eng
            print("[WALK] fast =", eng.cfg.inputs.get("fast"), "| run =", eng.run_mode, "| dry =", eng.cfg.dry_run,
                  "| on disk:", "fast = 15" in open(cfg_path, encoding="utf-8").read())

        steps = [(4000, lambda: (root.attributes("-topmost", True), grab("1-main"), root.attributes("-topmost", False))),
                 (500, win.on_run_button), (1500, lambda: grab("2-stop-menu")),
                 (2000, lambda: win.confirm_stop_all(top_dialog())), (1500, lambda: grab("3-stop-all-confirm")),
                 (200, close_dialog), (1500, lambda: win.ask_buy("NVDA")), (1500, lambda: grab("4-buy")),
                 (200, close_dialog), (1500, lambda: win.ask_mode("live")), (1500, lambda: grab("5-live-confirm")),
                 (200, close_dialog), (1500, win.on_close), (1500, lambda: grab("6-close-ask")),
                 (200, close_dialog), (200, lambda: win.live.ask("run", "paused")), (1500, lambda: grab("7-paused")),
                 (200, lambda: win.set_view("settings")), (300, check_save), (3000, report),
                 (200, lambda: grab("8-saved")),
                 (200, lambda: win.settings_inner.master.yview_moveto(0.42)), (800, lambda: grab("8b-settings-mid")),
                 (200, lambda: win.settings_inner.master.yview_moveto(1.0)), (800, lambda: grab("8c-settings-end")),
                 (200, lambda: (win.set_view("chart"), win.set_tab("closed"))), (1200, lambda: grab("9-closed")),
                 (500, win.quit)]
        def run_step(i):
            if i < len(steps):
                delay, fn = steps[i]
                root.after(delay, lambda: (fn(), run_step(i + 1)))
        run_step(0)
    if args.shot:
        def shot():
            from PIL import ImageGrab
            root.attributes("-topmost", True)
            root.lift()
            root.update()
            time.sleep(0.4)
            x, y = root.winfo_rootx(), root.winfo_rooty()
            ImageGrab.grab((x, y, x + root.winfo_width(), y + root.winfo_height())).save(args.shot)
            win.quit()
        root.after(7000, shot)
    root.mainloop()


if __name__ == "__main__":
    main()
