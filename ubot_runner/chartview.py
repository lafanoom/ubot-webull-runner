"""Which bars of the loaded chart are on screen: drag to go back in time, the wheel to zoom.

The window loads more bars than it shows (live.Live.CHART_BARS) and draws a slice of them.
`back` is how many bars the right edge sits before the newest bar (0 = the newest is on
screen) and `span` is how many bars are on screen. Pure: no tkinter, so it can be tested.
"""
SPAN = 120          # bars on screen when the window opens
SPAN_MIN = 20       # zoomed in as far as it goes
ZOOM = 1.2          # one notch of the wheel


def clamp(n, back, span):
    """(back, span) that fit n loaded bars."""
    if n < 2:
        return 0, max(2, n)
    span = max(min(SPAN_MIN, n), min(int(span), n))
    back = max(0, min(int(back), n - span))
    return back, span


def window(n, back, span):
    """The slice [a, b) of the loaded bars that is on screen."""
    back, span = clamp(n, back, span)
    b = n - back
    return max(0, b - span), b


def drag(n, back0, span, dx, width):
    """Where the view is after dragging dx pixels from where it was (back0): dragging right
    pulls older bars in from the left."""
    back0, span = clamp(n, back0, span)
    if width <= 0 or span < 2:
        return back0
    bars = dx * (span - 1) / width
    return clamp(n, back0 + round(bars), span)[0]


def zoom(n, back, span, notches, frac=1.0):
    """The wheel: notches > 0 zooms in (fewer bars). The bar under the pointer (frac of the
    width, 0 = left edge) stays under it."""
    back, span = clamp(n, back, span)
    if not notches:
        return back, span
    new = span / ZOOM ** notches
    new = max(min(SPAN_MIN, n), min(n, int(round(new))))
    if new == span:
        new = max(min(SPAN_MIN, n), min(n, span + (-1 if notches > 0 else 1)))
    a, _ = window(n, back, span)
    under = a + frac * (span - 1)
    a2 = round(under - frac * (new - 1))
    return clamp(n, n - (a2 + new), new)


def follow(old_times, new_times, back):
    """Bars arrived while the view was back in time: keep the same bars on screen."""
    if not back or not old_times or not new_times:
        return back
    last = old_times[-1]
    try:
        i = len(new_times) - 1 - new_times[::-1].index(last)
    except ValueError:
        return back
    return back + (len(new_times) - 1 - i)


def cut(chart, a, b):
    """The chart tuple the window draws, cut to bars [a, b): the indicator values sliced, the
    marks and signals that sit on those bars moved to the new positions. A trade's line can
    start before the cut (its 'from' goes below 0) and is drawn running in from the left edge."""
    bars, p, plots, marks, tf, has_plot, signals, ohlc = chart
    n = len(bars)
    if a <= 0 and b >= n:
        return chart
    plots = [dict(l, values=l["values"][a:b]) for l in plots]
    moved = []
    for m in marks:
        if a <= m["i"] < b:
            m = dict(m, i=m["i"] - a)
            if m.get("from") is not None:
                m["from"] -= a
            moved.append(m)
    sigs = [dict(s, i=s["i"] - a) for s in signals if a <= s["i"] < b]
    return (bars[a:b], p, plots, moved, tf, has_plot, sigs, ohlc[a:b] if len(ohlc) == n else ohlc)
