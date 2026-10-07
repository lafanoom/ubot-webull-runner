"""Command line: run / check / simulate.

    python "My Program v1.00.pyz"            # the program's window (dry_run until you switch to live)
    python "My Program v1.00.pyz" --console  # run without the window, as text
    python "My Program v1.00.pyz" check      # read-only: keys, account, symbols
    python "My Program v1.00.pyz" simulate --bars bars.json
    python "My Program v1.00.pyz" simulate --fetch 1000    # bars from Webull, read-only

Files next to the program: webull.toml (settings + keys), <name>.db (memory),
<name>.log, webull-token/ (Webull's login token), STOP (create it to stop sending).
"""
import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import re

from . import RUNNER, clock
from .config import ConfigError, load


class Mask(logging.Filter):
    def __init__(self, secrets):
        super().__init__()
        self.secrets = [s for s in secrets if s and len(s) >= 6]

    def filter(self, record):
        msg = record.getMessage()
        for s in self.secrets:
            msg = msg.replace(s, "<hidden>")
        record.msg, record.args = msg, ()
        return True


def setup_logging(path, secrets, extra=()):
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, logging.FileHandler):
            h.close()
    root.handlers.clear()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    outs = [logging.FileHandler(path, encoding="utf-8")]
    if sys.stdout is not None:                        # pythonw has no console
        outs.append(logging.StreamHandler(sys.stdout))
    for h in outs + list(extra):
        h.setFormatter(fmt)
        h.addFilter(Mask(secrets))
        root.addHandler(h)
    for name in ("webull", "urllib3", "requests"):
        logging.getLogger(name).setLevel(logging.WARNING)


class Lock:
    """One copy per folder: two copies would send every order twice."""

    def __init__(self, path):
        self.path = path
        self.f = None

    def __enter__(self):
        self.f = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SystemExit("This program is already running from this folder. Close the other copy first.")
        return self

    def __exit__(self, *a):
        self.f.close()


def program_name(strategy, program_path):
    if strategy.NAME:
        return strategy.NAME
    return os.path.splitext(os.path.basename(program_path))[0]


def main(strategy_cls, program_path, argv=None):
    ap = argparse.ArgumentParser(prog=os.path.basename(program_path))
    ap.add_argument("command", nargs="?", default="run", choices=("run", "check", "simulate"))
    ap.add_argument("--config", help="settings file (default: webull.toml next to the program)")
    ap.add_argument("--bars", help="simulate: bars file (JSON)")
    ap.add_argument("--fetch", type=int, help="simulate: fetch this many bars per symbol from Webull")
    ap.add_argument("--save-bars", help="simulate --fetch: also save the bars to this file")
    ap.add_argument("--deposit", type=float, default=10_000.0)
    ap.add_argument("--inputs", help='simulate --bars: input values as JSON, e.g. {"fast": 10}')
    ap.add_argument("--console", action="store_true", help="run without the window")
    args = ap.parse_args(argv)

    strategy = strategy_cls()
    base = os.path.dirname(os.path.abspath(program_path))
    stem = os.path.splitext(os.path.basename(program_path))[0]
    name = program_name(strategy, program_path)
    if sys.stdout is not None:
        print(f"{name} | runner {RUNNER} | Python {sys.version.split()[0]}", flush=True)

    if args.command == "run" and not args.console and not os.environ.get("UBOT_FACTORY"):
        from . import gui
        if gui.available():
            return run_window(strategy, args.config or os.path.join(base, "webull.toml"), base, stem, name)

    if args.command == "simulate" and args.bars:
        from .sim import load_bars, simulate
        import json
        from .config import merge_inputs
        try:
            inputs = merge_inputs(strategy.INPUTS, json.loads(args.inputs) if args.inputs else {})
        except (ConfigError, ValueError) as e:
            print("Inputs:", e, flush=True)
            return 2
        bars = load_bars(args.bars)
        res = simulate(strategy, bars, inputs=inputs, deposit=args.deposit)
        print("[RESULT] " + json.dumps(res), flush=True)
        return 0

    cfg_path = args.config or os.path.join(base, "webull.toml")
    try:
        cfg = load(cfg_path, strategy.INPUTS)
    except ConfigError as e:
        print("Settings:", e, flush=True)
        return 2
    setup_logging(os.path.join(base, stem + ".log"), cfg.secrets())
    log = logging.getLogger("ubot")
    log.info("start %s (runner %s) host %s %s", name, RUNNER, cfg.host, "DRY RUN" if cfg.dry_run else "LIVE")

    from .broker import WebullBroker
    token_dir = os.path.join(base, "webull-token")
    os.makedirs(token_dir, exist_ok=True)
    if not cfg.is_uat:
        log.info("If Webull sends you an SMS code now, approve it in the Webull app within 5 minutes.")
    broker = WebullBroker(cfg, token_dir)
    try:
        kind = broker.connect()
    except RuntimeError as e:
        log.error("%s", e)
        return 3
    log.info("account type %s", kind)
    if kind != "CASH":
        log.warning("this program is written for a cash account; it never shorts or borrows")

    if args.command == "check":
        return check(cfg, broker, strategy, log)
    if args.command == "simulate":
        from .sim import dump_bars, simulate
        n = args.fetch or 500
        bars = {}
        for s in cfg.symbols:
            got = broker.bars(s, strategy.BAR, n)
            if got:
                bars[s] = [b for b in got if clock.bar_closed(b.time, strategy.BAR, datetime.now(timezone.utc))]
        if not bars:
            log.error("no bars - is the symbols list empty?")
            return 4
        if args.save_bars:
            dump_bars(bars, args.save_bars)
        import json
        print("[RESULT] " + json.dumps(simulate(strategy, bars, inputs=cfg.inputs, deposit=args.deposit)), flush=True)
        return 0

    with Lock(os.path.join(base, stem + ".lock")):
        return run(cfg, broker, strategy, base, stem, log)


def version_of(stem):
    m = re.search(r"\bv\.?(\d+\.\d+)\s*$", stem)
    return "v" + m.group(1) if m else ""


def run_window(strategy, cfg_path, base, stem, name):
    """The window: asks for the keys the first time, then runs the program behind it."""
    import tkinter as tk
    from . import gui
    from .broker import WebullBroker
    from .config import dump
    from .engine import Engine
    from .live import Live, Ring
    from .state import State

    with Lock(os.path.join(base, stem + ".lock")):
        try:
            cfg = load(cfg_path, strategy.INPUTS, need_keys=False)
        except ConfigError as e:
            root = tk.Tk()
            root.title(name)
            tk.Label(root, text=f"{os.path.basename(cfg_path)}: {e}", padx=24, pady=24, wraplength=520,
                     justify="left").pack()
            root.mainloop()
            return 2
        ring = Ring()
        log_path = os.path.join(base, stem + ".log")
        setup_logging(log_path, cfg.secrets(), extra=[ring])
        log = logging.getLogger("ubot")
        log.info("start %s (runner %s) window", name, RUNNER)
        token_dir = os.path.join(base, "webull-token")
        os.makedirs(token_dir, exist_ok=True)

        def connect(c):
            setup_logging(log_path, cfg.secrets() + c.secrets(), extra=[ring])
            broker = WebullBroker(c, token_dir)
            kind = broker.connect()
            log.info("connected, account type %s", kind)
            return broker

        def make_live(c, broker):
            state = State(os.path.join(base, stem + ".db"))
            eng = Engine(strategy, c, broker, state, stop_file=os.path.join(base, "STOP"), sleep=time.sleep)
            live = Live(eng, cfg_path, dump, ring)
            live.refresh(datetime.now(timezone.utc))
            live.start()
            return live

        root = tk.Tk()
        win = gui.Window(root, name, version_of(stem), strategy, cfg, cfg_path, connect, dump, make_live)
        try:
            root.mainloop()
        finally:
            if win.live and win.live.thread and win.live.thread.is_alive():
                win.live.stop()
            if win.live:
                win.live.eng.state.close()
        log.info("window closed - orders resting at Webull stay there")
    return 0


def check(cfg, broker, strategy, log):
    usd = broker.usd()
    log.info("USD buying power: %s", f"{usd[2]:.2f}" if usd else "could not read")
    if not cfg.symbols:
        log.warning("symbols is empty: this program will not trade anything until you list tickers")
    for s in cfg.symbols:
        bars = broker.bars(s, strategy.BAR, 5)
        px = broker.last_price(s)
        log.info("%s: %s bars, last price %s", s, len(bars) if bars is not None else "no", px)
    log.info("dry_run is %s", "ON - no order will be sent" if cfg.dry_run else "OFF - orders will be sent")
    return 0


def run(cfg, broker, strategy, base, stem, log):
    from .engine import Engine
    from .state import State
    state = State(os.path.join(base, stem + ".db"))
    eng = Engine(strategy, cfg, broker, state, stop_file=os.path.join(base, "STOP"), sleep=time.sleep)
    now = datetime.now(timezone.utc)
    eng.recover(now)
    strategy.on_start(eng.ctx)
    last_status = None
    last_beat = None
    fails = 0
    log.info("running - press Ctrl+C to quit (orders resting at Webull stay there)")
    try:
        while True:
            now = datetime.now(timezone.utc)
            try:
                eng.step(now)
                fails = 0
            except Exception:
                fails += 1
                log.exception("step failed (%d in a row)", fails)
            if last_beat is None or now - last_beat > timedelta(hours=12):
                r = broker.heartbeat()
                if not r.ok:
                    log.warning("Webull did not answer the daily check: %s %s - if the token expired, restart "
                                "the program and approve the SMS in the Webull app", r.code, r.message)
                last_beat = now
            if eng.status != last_status:
                print("[status] " + eng.status, flush=True)
                last_status = eng.status
            wait = cfg.poll_seconds if clock.regular_open(now) else 60
            time.sleep(min(300, wait * (2 ** min(fails, 4))))
    except KeyboardInterrupt:
        log.info("stopped by you - orders resting at Webull stay there")
    finally:
        state.close()
    return 0
