"""US stock market calendar: regular hours, NYSE holidays, early closes.

Webull's trading-calendar endpoint is not reliable everywhere (it answered 404 in
the test environment), so the rules are computed here. A broker refusal for a
non-trading day is still treated as closed by the engine.
"""
from datetime import date, datetime, time, timedelta, timezone, tzinfo


class _Eastern(tzinfo):
    """US Eastern time by the rule in force since 2007 (DST from the 2nd Sunday of
    March 02:00 to the 1st Sunday of November 02:00). Used when the machine has no
    time-zone database - plain Windows Python has none unless `tzdata` is installed."""

    def _dst_window(self, y):
        mar = date(y, 3, 8) + timedelta(days=(6 - date(y, 3, 8).weekday()) % 7)
        nov = date(y, 11, 1) + timedelta(days=(6 - date(y, 11, 1).weekday()) % 7)
        return datetime(y, mar.month, mar.day, 2), datetime(y, nov.month, nov.day, 2)

    def utcoffset(self, dt):
        return timedelta(hours=-5) + self.dst(dt)

    def dst(self, dt):
        if dt is None:
            return timedelta(0)
        start, end = self._dst_window(dt.year)
        naive = dt.replace(tzinfo=None)
        return timedelta(hours=1) if start <= naive < end else timedelta(0)

    def tzname(self, dt):
        return "EDT" if self.dst(dt) else "EST"

    def fromutc(self, dt):
        start, end = self._dst_window(dt.year)
        naive = dt.replace(tzinfo=None)
        # the DST window in UTC: starts 07:00 UTC, ends 06:00 UTC
        on = start + timedelta(hours=5) <= naive < end + timedelta(hours=4)
        return (naive + timedelta(hours=-4 if on else -5)).replace(tzinfo=self)


try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = _Eastern()
OPEN = time(9, 30)
CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)

SPANS = {"1d": None, "1h": timedelta(hours=1), "30m": timedelta(minutes=30),
         "15m": timedelta(minutes=15), "5m": timedelta(minutes=5)}


def _nth_weekday(y, m, weekday, n):
    d = date(y, m, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(y, m, weekday):
    d = date(y, m + 1, 1) - timedelta(days=1) if m < 12 else date(y, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _easter(y):
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month = (h + l_ - 7 * m + 114) // 31
    day = (h + l_ - 7 * m + 114) % 31 + 1
    return date(y, month, day)


def _observed(d):
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def holidays(y):
    h = {
        _nth_weekday(y, 1, 0, 3),        # Martin Luther King Jr. Day
        _nth_weekday(y, 2, 0, 3),        # Washington's Birthday
        _easter(y) - timedelta(days=2),  # Good Friday
        _last_weekday(y, 5, 0),          # Memorial Day
        _observed(date(y, 7, 4)),
        _nth_weekday(y, 9, 0, 1),        # Labor Day
        _nth_weekday(y, 11, 3, 4),       # Thanksgiving
        _observed(date(y, 12, 25)),
    }
    if y >= 2022:
        h.add(_observed(date(y, 6, 19)))
    ny = date(y, 1, 1)
    if ny.weekday() != 5:                # NYSE does not observe Jan 1 on the Friday before
        h.add(_observed(ny))
    return h


def is_trading_day(d):
    return d.weekday() < 5 and d not in holidays(d.year)


def close_time(d):
    early = set()
    jul3 = date(d.year, 7, 3)
    if jul3.weekday() < 5 and is_trading_day(jul3):
        early.add(jul3)
    early.add(_nth_weekday(d.year, 11, 3, 4) + timedelta(days=1))
    dec24 = date(d.year, 12, 24)
    if dec24.weekday() < 5:
        early.add(dec24)
    return EARLY_CLOSE if d in early else CLOSE


def session(d):
    """(open, close) as aware UTC datetimes, or None on a closed day."""
    if not is_trading_day(d):
        return None
    o = datetime.combine(d, OPEN, ET).astimezone(timezone.utc)
    c = datetime.combine(d, close_time(d), ET).astimezone(timezone.utc)
    return o, c


def et_date(now):
    return now.astimezone(ET).date()


def regular_open(now):
    s = session(et_date(now))
    return bool(s) and s[0] <= now < s[1]


def bar_closed(bar_start, bar, now):
    """Has the bar that started at bar_start finished, as of now?"""
    span = SPANS[bar]
    if span is None:
        d = et_date(bar_start)
        s = session(d)
        end = s[1] if s else datetime.combine(d + timedelta(days=1), time(0), ET)
        return now >= end
    end = bar_start + span
    s = session(et_date(bar_start))
    if s and s[0] <= bar_start < s[1]:
        end = min(end, s[1])         # the last bar of the day ends at the close
    return now >= end


def next_trading_day(d):
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d
