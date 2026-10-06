"""Simulation over past bars - the same engine against FakeBroker.

Per bar, in this order (conservative: the stop is checked before the target):
  1. market orders from the previous bar fill at this bar's open
  2. new fills get their stop
  3. resting stops touched by the bar's low fill at the stop (or the open on a gap)
  4. a target touched by the bar's high sells at the target (or the open on a gap)
  5. the bar closes: the strategy sees it and may queue orders for the next open

It is an estimate from bars: no spread, no slippage beyond gaps, no partial fills.
"""
import json
import math
from datetime import datetime, timezone

from .broker import FakeBroker
from .config import Config
from .engine import Engine
from .state import State
from .strategy import Bar


def load_bars(path):
    """{"SYM": [[iso_time, open, high, low, close, volume], ...]} -> {sym: [Bar]}"""
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    out = {}
    for sym, rows in doc.items():
        bars = []
        for r in rows:
            t = datetime.fromisoformat(r[0])
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            bars.append(Bar(t.astimezone(timezone.utc), *map(float, r[1:6])))
        out[sym.upper()] = sorted(bars, key=lambda b: b.time)
    return out


def dump_bars(bars_by_symbol, path):
    doc = {s: [[b.time.isoformat(), b.open, b.high, b.low, b.close, b.volume] for b in bars]
           for s, bars in bars_by_symbol.items()}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f)


def simulate(strategy, bars_by_symbol, inputs=None, deposit=10_000.0, commission=0.0,
             max_notional=None, max_open=10):
    cfg = Config(app_key="sim", app_secret="sim", dry_run=False, symbols=tuple(bars_by_symbol))
    cfg.inputs = dict(strategy.INPUTS)
    cfg.inputs.update(inputs or {})
    cfg.limits.max_notional_per_order = max_notional or deposit
    cfg.limits.max_open_positions = max_open
    cfg.limits.max_orders_per_day = 1000
    cfg.limits.daily_loss_pct = 0
    fake = FakeBroker(cash=deposit, commission=commission)
    eng = Engine(strategy, cfg, fake, State(), sim=True)
    times = sorted({b.time for bars in bars_by_symbol.values() for b in bars})
    index = {s: {b.time: b for b in bars} for s, bars in bars_by_symbol.items()}
    seen = {s: [] for s in bars_by_symbol}
    curve = []
    peak = deposit
    max_dd = 0.0
    strategy.on_start(eng.ctx)
    for t in times:
        now = t
        for s in bars_by_symbol:
            b = index[s].get(t)
            if not b:
                continue
            fake.open_bar(s, b)
            eng.reconcile(now)
            eng.protect(now)
            fake.touch_stops(s, b)
            eng.reconcile(now)
            p = eng.state.position(s)
            if p and p["target"] is not None and not p["closing"] and b.high >= p["target"]:
                fake.mark[s] = max(b.open, p["target"])
                fake.instant = True
                eng.watch_targets(now)
                fake.instant = False
                eng.reconcile(now)
            fake.mark[s] = b.close
            seen[s].append(b)
            keep = max(strategy.WARMUP * 3, 500)
            if len(seen[s]) > keep * 2:
                del seen[s][:-keep]
            eng.run_bar(s, seen[s], now)
            eng.protect(now)
        cash, mv, _ = fake.usd()
        eq = cash + mv
        curve.append((t, eq))
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak * 100)
    trades = eng.state.trades()
    wins = [r for r in trades if r["pnl"] > 0]
    gross_w = sum(r["pnl"] for r in wins)
    gross_l = -sum(r["pnl"] for r in trades if r["pnl"] < 0)
    end = curve[-1][1] if curve else deposit
    return {
        "bars": len(times),
        "trades": len(trades),
        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else None,
        "profit_factor": round(gross_w / gross_l, 2) if gross_l else None,
        "net_usd": round(end - deposit, 2),
        "net_pct": round((end - deposit) / deposit * 100, 2),
        "max_dd_pct": round(max_dd, 2),
        "open_at_end": len(eng.state.positions()),
        "exits": {k: sum(1 for r in trades if r["reason"] == k) for k in sorted({r["reason"] for r in trades})},
    }
