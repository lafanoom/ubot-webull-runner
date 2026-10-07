"""webull.toml - the customer's settings file, next to the program.

The keys live in this file on the customer's machine and nowhere else. They are
never printed, never logged and never sent anywhere except to Webull.

Every safety setting starts at its most careful value: dry_run is on, the
symbol list is empty (empty = no order is ever sent), regular hours only.
"""
import os
import tomllib
from dataclasses import dataclass, field

PROD_HOST = "api.webull.co.th"
UAT_SUFFIX = ".uat.webullbroker.com"

TEMPLATE = """\
# Settings for your trading program. Keep this file private: it holds your keys.
app_key = ""
app_secret = ""
account_id = ""          # leave empty to use your first cash account
host = "api.webull.co.th"

dry_run = true           # true = only write what it would do; no order is sent
symbols = []             # tickers this program may trade, e.g. ["KO", "MSFT"]; empty = trades nothing

[limits]
max_notional_per_order = 1000   # US dollars per buy
max_orders_per_day = 10
max_open_positions = 3
daily_loss_pct = 3              # stop opening new positions today after this loss (% of account)

[inputs]
# values here replace the program's own defaults
"""


class ConfigError(Exception):
    pass


@dataclass
class Limits:
    max_notional_per_order: float = 1000.0
    max_orders_per_day: int = 10
    max_open_positions: int = 3
    daily_loss_pct: float = 3.0


@dataclass
class Config:
    app_key: str = field(default="", repr=False)
    app_secret: str = field(default="", repr=False)
    account_id: str = field(default="", repr=False)
    host: str = PROD_HOST
    dry_run: bool = True
    symbols: tuple = ()
    poll_seconds: int = 15
    daily_eval_delay_min: int = 1
    limits: Limits = field(default_factory=Limits)
    inputs: dict = field(default_factory=dict)

    def secrets(self):
        return [s for s in (self.app_key, self.app_secret, self.account_id) if s]

    @property
    def is_uat(self):
        return self.host.endswith(UAT_SUFFIX)


def _num(d, k, default, kind, lo, hi):
    v = d.get(k, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ConfigError(f"{k} must be a number")
    if kind is int and v != int(v):
        raise ConfigError(f"{k} must be a whole number")
    if not lo <= v <= hi:
        raise ConfigError(f"{k} must be between {lo} and {hi}")
    return kind(v)


def parse(doc, strategy_inputs=None):
    c = Config()
    for k in ("app_key", "app_secret", "account_id", "host"):
        v = doc.get(k, getattr(c, k))
        if not isinstance(v, str):
            raise ConfigError(f"{k} must be text in quotes")
        setattr(c, k, v.strip())
    if not c.app_key or not c.app_secret:
        raise ConfigError("app_key and app_secret are empty - copy them from your Webull App Key page")
    if not c.host or "/" in c.host or ":" in c.host:
        raise ConfigError("host must be a host name only, e.g. api.webull.co.th")
    if os.environ.get("UBOT_FACTORY") == "1" and not c.is_uat:
        raise ConfigError("factory machine: only *.uat.webullbroker.com is allowed")
    dr = doc.get("dry_run", True)
    if not isinstance(dr, bool):
        raise ConfigError("dry_run must be true or false")
    c.dry_run = dr
    syms = doc.get("symbols", [])
    if not isinstance(syms, list) or not all(isinstance(s, str) for s in syms):
        raise ConfigError('symbols must be a list like ["KO", "MSFT"]')
    clean = []
    for s in syms:
        s = s.strip().upper()
        if not s or len(s) > 12 or not all(ch.isalnum() or ch in ".-" for ch in s):
            raise ConfigError(f"symbol {s!r} does not look like a US ticker")
        if s not in clean:
            clean.append(s)
    c.symbols = tuple(clean)
    c.poll_seconds = _num(doc, "poll_seconds", 15, int, 5, 300)
    c.daily_eval_delay_min = _num(doc, "daily_eval_delay_min", 1, int, 0, 120)
    lim = doc.get("limits", {})
    if not isinstance(lim, dict):
        raise ConfigError("[limits] must be a table")
    c.limits = Limits(
        max_notional_per_order=_num(lim, "max_notional_per_order", 1000, float, 1, 10_000_000),
        max_orders_per_day=_num(lim, "max_orders_per_day", 10, int, 0, 1000),
        max_open_positions=_num(lim, "max_open_positions", 3, int, 0, 100),
        daily_loss_pct=_num(lim, "daily_loss_pct", 3, float, 0, 100),
    )
    c.inputs = merge_inputs(strategy_inputs, doc.get("inputs", {}))
    return c


def merge_inputs(defaults, given):
    """The program's own input defaults with the customer's values over them.
    Only names the program declares, each the same kind of value as its default."""
    if not isinstance(given, dict):
        raise ConfigError("[inputs] must be a table")
    merged = dict(defaults or {})
    for k, v in given.items():
        if k not in merged:
            raise ConfigError(f"input {k!r} is not one of this program's inputs: {', '.join(sorted(merged)) or 'none'}")
        d = merged[k]
        if isinstance(d, bool) != isinstance(v, bool) or (
                not isinstance(d, bool) and isinstance(d, (int, float)) and not isinstance(v, (int, float))) or (
                isinstance(d, str) and not isinstance(v, str)):
            raise ConfigError(f"input {k!r} must be the same kind of value as its default ({d!r})")
        if isinstance(d, int) and not isinstance(d, bool) and isinstance(v, float) and v != int(v):
            raise ConfigError(f"input {k!r} must be a whole number")
        merged[k] = type(d)(v) if not isinstance(d, bool) else v
    return merged


def load(path, strategy_inputs=None):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(TEMPLATE)
        raise ConfigError(f"created {path} - put your App Key and App Secret in it, then run again")
    try:
        with open(path, "rb") as f:
            doc = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{os.path.basename(path)} is not valid: {e}") from None
    return parse(doc, strategy_inputs)
