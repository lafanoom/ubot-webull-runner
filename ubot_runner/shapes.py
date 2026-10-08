"""Rounded boxes, buttons, tabs, switches and input fields for tkinter, which only
draws square ones.

A rounded shape is drawn on a canvas that sits behind the content. The content
lives in an inner frame inset far enough that its square corners stay inside
the rounded ones. `Box.inner` (what callers put widgets in) forwards pack/grid
to the box, so code written for a plain Frame keeps working.

Every pixel size here goes through `px()`, which scales with the screen's DPI
once `set_scale()` has been told it (the window does that at start-up).
"""
import math
import tkinter as tk
import tkinter.font as tkfont

K = 1.0


def set_scale(k):
    """Pixels per CSS pixel: 1.0 at 96 dpi, 1.5 at 144 dpi."""
    global K
    K = max(0.5, min(4.0, float(k)))


def px(v):
    return int(round(v * K))


def round_rect(cv, x1, y1, x2, y2, r, **kw):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, splinesteps=16, **kw)


def mix(c1, c2, t):
    """c1 blended towards c2 by t (0..1). Stands in for rgba() over a known background."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    t = max(0.0, min(1.0, t))
    return "#%02X%02X%02X" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _inset(r, d):
    """How far a rounded corner of radius r cuts in, at distance d from the edge."""
    if d >= r:
        return 0
    return r - math.sqrt(max(0.0, r * r - (r - d) ** 2))


def gradient_rect(cv, x1, y1, x2, y2, r, top, bottom, outline=None, vertical=True, step=1, tags=()):
    """A rounded rectangle filled with a gradient (top->bottom, or left->right)."""
    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
    r = max(0, min(r, (x2 - x1) // 2, (y2 - y1) // 2))
    if vertical:
        n = max(1, y2 - y1)
        for y in range(y1, y2, step):
            d = min(y - y1, y2 - 1 - y)
            dx = _inset(r, d)
            cv.create_line(x1 + dx, y, x2 - dx, y, fill=mix(top, bottom, (y - y1) / n), width=step, tags=tags)
    else:
        n = max(1, x2 - x1)
        for x in range(x1, x2, step):
            d = min(x - x1, x2 - 1 - x)
            dy = _inset(r, d)
            cv.create_line(x, y1 + dy, x, y2 - dy, fill=mix(top, bottom, (x - x1) / n), width=step, tags=tags)
    if outline:
        round_rect(cv, x1, y1, x2 - 1, y2 - 1, r, fill="", outline=outline, tags=tags)


def diag_gradient(cv, x1, y1, x2, y2, r, c0, c1, end=0.7, angle=160, step=2, seg=18, outline=None, tags=()):
    """A rounded rectangle with a CSS-like `linear-gradient(<angle>deg, c0 0%, c1 <end>)`:
    drawn as rows of short horizontal segments, each coloured by its distance along the gradient line."""
    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
    w, h = x2 - x1, y2 - y1
    if w < 2 or h < 2:
        return
    r = max(0, min(r, w // 2, h // 2))
    a = math.radians(angle)
    sa, ca = math.sin(a), -math.cos(a)
    length = abs(w * sa) + abs(h * ca) or 1.0
    cx, cy = x1 + w / 2, y1 + h / 2
    for y in range(y1, y2, step):
        d = min(y - y1, y2 - 1 - y)
        dx = _inset(r, d)
        xa, xb = x1 + dx, x2 - dx
        x = xa
        while x < xb:
            xe = min(xb, x + seg)
            t = ((x + xe) / 2 - cx) * sa / length + (y - cy) * ca / length + 0.5
            cv.create_line(x, y, xe, y, fill=mix(c0, c1, t / end), width=step, tags=tags)
            x = xe
    if outline:
        round_rect(cv, x1 + 0.5, y1 + 0.5, x2 - 0.5, y2 - 0.5, r, fill="", outline=outline, tags=tags)


def shadow(cv, x1, y1, x2, y2, r, bg, color="#000000", spread=None, strength=0.55, tags=()):
    """A soft drop shadow: rounded rings fading from `color` into `bg`. Draw before the shape."""
    spread = px(18) if spread is None else spread
    n = max(2, spread // 2)
    for i in range(n, 0, -1):
        d = i * spread / n
        t = strength * (1 - i / (n + 1)) ** 2
        round_rect(cv, x1 - d, y1 - d * 0.5 + spread * 0.35, x2 + d, y2 + d, r + d,
                   fill=mix(bg, color, t), outline="", tags=tags)


def circle(cv, cx, cy, r, **kw):
    return cv.create_oval(cx - r, cy - r, cx + r, cy + r, **kw)


def diag_color(x, y, x1, y1, x2, y2, c0, c1, end=0.7, angle=160):
    """The colour `diag_gradient` paints at (x, y) - so something drawn on top can blend into it."""
    w, h = x2 - x1, y2 - y1
    a = math.radians(angle)
    sa, ca = math.sin(a), -math.cos(a)
    length = abs(w * sa) + abs(h * ca) or 1.0
    t = (x - (x1 + w / 2)) * sa / length + (y - (y1 + h / 2)) * ca / length + 0.5
    return mix(c0, c1, t / end)


def _rgb(c):
    return [int(c[i:i + 2], 16) for i in (1, 3, 5)]


_RING_CACHE = {}
_SS = 4  # samples per pixel along each axis


def ring_image(cv, r, width, frac, color, track, bg_at, ox, oy):
    """A smooth (anti-aliased) progress ring as a PhotoImage - canvas arcs are drawn with hard,
    stepped edges. Each pixel is sampled _SS x _SS times and blended into `bg_at(x, y)`, the
    background colour at canvas point (x, y); (ox, oy) is where the image's top-left lands."""
    half = width / 2
    n = int(math.ceil(2 * (r + half))) + 2
    c = n / 2
    frac = max(0.0, min(1.0, frac or 0.0))
    key = (n, r, width, round(frac, 3), color, track, bg_at(ox, oy), bg_at(ox + n, oy + n))
    img = _RING_CACHE.get(key)
    if img is not None:
        return img
    col, trk = _rgb(color), _rgb(track)
    sweep = 2 * math.pi * frac
    step = 1.0 / _SS
    rows = []
    for py in range(n):
        row = []
        for qx in range(n):
            bg = bg_at(ox + qx, oy + py)
            d0 = math.hypot(qx + 0.5 - c, py + 0.5 - c)
            if abs(d0 - r) > half + 1:
                row.append(bg)
                continue
            hit_c = hit_t = 0
            for sy in range(_SS):
                yy = py + (sy + 0.5) * step - c
                for sx in range(_SS):
                    xx = qx + (sx + 0.5) * step - c
                    if abs(math.hypot(xx, yy) - r) > half:
                        continue
                    ang = math.atan2(xx, -yy) % (2 * math.pi)   # 0 at 12 o'clock, clockwise
                    if ang < sweep:
                        hit_c += 1
                    else:
                        hit_t += 1
            tot = _SS * _SS
            if not hit_c and not hit_t:
                row.append(bg)
                continue
            b = _rgb(bg)
            a_c, a_t = hit_c / tot, hit_t / tot
            px_ = [round(b[i] * (1 - a_c - a_t) + col[i] * a_c + trk[i] * a_t) for i in range(3)]
            row.append("#%02X%02X%02X" % tuple(px_))
        rows.append("{" + " ".join(row) + "}")
    img = tk.PhotoImage(master=cv, width=n, height=n)
    img.put(" ".join(rows))
    if len(_RING_CACHE) > 64:
        _RING_CACHE.clear()
    _RING_CACHE[key] = img
    return img


def ring(cv, cx, cy, r, width, frac, color, track, tags=(), bg_at=None):
    """A progress ring: `frac` of the circle in `color`, the rest in `track`. Starts at 12 o'clock.
    With `bg_at` (canvas x, y -> colour underneath) it is drawn anti-aliased as an image."""
    if bg_at is not None:
        half = width / 2
        n = int(math.ceil(2 * (r + half))) + 2
        ox, oy = int(round(cx - n / 2)), int(round(cy - n / 2))
        img = ring_image(cv, r, width, frac, color, track, bg_at, ox, oy)
        cv.create_image(ox, oy, image=img, anchor="nw", tags=tags)
        return
    cv.create_oval(cx - r, cy - r, cx + r, cy + r, outline=track, width=width, tags=tags)
    if frac and frac > 0:
        ext = -max(1.0, min(359.9, 360 * frac))
        cv.create_arc(cx - r, cy - r, cx + r, cy + r, start=90, extent=ext, style="arc", outline=color,
                      width=width, tags=tags)


def draw_icon(cv, name, cx, cy, size, color, tags=(), width=None):
    """Small line icons the window uses (the mockup's SVGs, redrawn): chart, sliders, pause, play, plus, x."""
    s = size / 2
    w = width or max(1.5, size / 7)
    kw = dict(fill=color, width=w, capstyle="round", joinstyle="round", tags=tags)
    if name == "chart":
        cv.create_line(cx - s, cy + s * 0.6, cx - s * 0.35, cy - s * 0.1, cx + s * 0.1, cy + s * 0.35,
                       cx + s, cy - s * 0.65, **kw)
    elif name == "trend":                   # the logo: chart line with an arrow head
        cv.create_line(cx - s, cy + s * 0.55, cx - s * 0.35, cy - s * 0.1, cx + s * 0.1, cy + s * 0.3,
                       cx + s, cy - s * 0.6, **kw)
        cv.create_line(cx + s * 0.35, cy - s * 0.6, cx + s, cy - s * 0.6, cx + s, cy + s * 0.05, **kw)
    elif name == "sliders":
        for i, kx in enumerate((-0.35, 0.3, -0.55)):
            y = cy + (i - 1) * s * 0.75
            cv.create_line(cx - s, y, cx + s, y, **kw)
            cv.create_oval(cx + kx * s - w * 1.3, y - w * 1.3, cx + kx * s + w * 1.3, y + w * 1.3,
                           fill=color, outline=color, tags=tags)
    elif name == "pause":
        for dx in (-0.55, 0.15):
            cv.create_rectangle(cx + dx * s, cy - s * 0.8, cx + (dx + 0.4) * s, cy + s * 0.8,
                                fill=color, outline=color, tags=tags)
    elif name == "play":
        cv.create_polygon(cx - s * 0.6, cy - s * 0.85, cx + s * 0.85, cy, cx - s * 0.6, cy + s * 0.85,
                          fill=color, outline=color, tags=tags)
    elif name == "plus":
        cv.create_line(cx - s * 0.7, cy, cx + s * 0.7, cy, **kw)
        cv.create_line(cx, cy - s * 0.7, cx, cy + s * 0.7, **kw)
    elif name == "x":
        cv.create_line(cx - s * 0.55, cy - s * 0.55, cx + s * 0.55, cy + s * 0.55, **kw)
        cv.create_line(cx - s * 0.55, cy + s * 0.55, cx + s * 0.55, cy - s * 0.55, **kw)
    elif name == "check":
        cv.create_line(cx - s * 0.6, cy + s * 0.05, cx - s * 0.15, cy + s * 0.5, cx + s * 0.65, cy - s * 0.5, **kw)


class _Forward:
    """Geometry calls on the inner widget go to the outer box."""
    _box = None

    def pack(self, *a, **kw):
        return self._box.pack(*a, **kw)
    pack_configure = pack

    def grid(self, *a, **kw):
        return self._box.grid(*a, **kw)
    grid_configure = grid

    def place(self, *a, **kw):
        return self._box.place(*a, **kw)

    def pack_forget(self):
        return self._box.pack_forget()

    def grid_forget(self):
        return self._box.grid_forget()


class Inner(_Forward, tk.Frame):
    pass


class Box(tk.Frame):
    """A rounded panel. Put children in `.inner`.

    `tint` paints a soft band at the top (the mockup's `linear-gradient(160deg, tint, fill 70%)`),
    fading into `fill` over `band` px, and `title` is drawn on that band so the panel can carry a
    coloured legend without a widget sitting on the gradient."""

    def __init__(self, parent, fill, outline="", radius=14, width=1, inset=None, tint=None, band=None,
                 title=None, title_fg="#E6EAF2", title_font=None, pad=None, **kw):
        super().__init__(parent, bg=parent["bg"], bd=0, highlightthickness=0, **kw)
        self.fill, self.outline, self.radius, self.lw = fill, outline, radius, width
        self.tint, self.band = tint, band if band is not None else px(72)
        self.title, self.title_fg = title, title_fg
        self.title_font = tkfont.Font(font=title_font) if title_font else None
        self.cv = tk.Canvas(self, bg=parent["bg"], highlightthickness=0, bd=0)
        self.cv.place(x=0, y=0, relwidth=1, relheight=1)
        inset = max(px(4), int(radius * 0.32) + 1) if inset is None else inset
        self.inset = inset
        padx = inset if pad is None else pad[0]
        top = inset if pad is None else pad[1]
        bottom = inset if pad is None else (pad[2] if len(pad) > 2 else pad[1])
        if title:
            top = max(top, inset) + (self.title_font.metrics("linespace") if self.title_font else px(18)) + px(8)
        self.inner = Inner(self, bg=fill, bd=0, highlightthickness=0)
        self.inner._box = self
        tk.Frame.pack(self.inner, fill="both", expand=True, padx=padx, pady=(top, bottom))
        self.cv.bind("<Configure>", lambda e: self.draw())

    def draw(self):
        cv = self.cv
        cv.delete("all")
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 4 or h < 4:
            return
        o = self.lw / 2 + 0.5
        if self.tint:
            round_rect(cv, o, o, w - o, h - o, self.radius, fill=self.fill, outline="")
            band = min(self.band, h - 2)
            n = max(1, band)
            for y in range(1, band, 2):
                d = y
                dx = _inset(self.radius, d)
                cv.create_line(1 + dx, y, w - 1 - dx, y, fill=mix(self.tint, self.fill, y / n), width=2)
            if self.outline:
                round_rect(cv, o, o, w - o, h - o, self.radius, fill="", outline=self.outline, width=self.lw)
        else:
            round_rect(cv, o, o, w - o, h - o, self.radius, fill=self.fill,
                       outline=self.outline or self.fill, width=self.lw)
        if self.title:
            padx = self.inner.pack_info().get("padx", self.inset)
            if isinstance(padx, tuple):
                padx = padx[0]
            cv.create_text(int(padx) + px(2), self.inset + px(4), text=self.title, anchor="nw",
                           fill=self.title_fg, font=self.title_font)

    def set(self, fill=None, outline=None, tint=None, title=None, title_fg=None):
        if fill is not None:
            self.fill = fill
            self.inner.config(bg=fill)
        if outline is not None:
            self.outline = outline
        if tint is not None:
            self.tint = tint or None
        if title is not None:
            self.title = title
        if title_fg is not None:
            self.title_fg = title_fg
        self.draw()


class Button(tk.Canvas):
    """A rounded button. config() takes text, bg, fg, state, command like tk.Button.

    Extras: `icon` (a draw_icon name before the text), `dot` (a coloured dot before the text,
    for status pills), `tail` (muted text after the main text, e.g. a dropdown arrow),
    `height` (a fixed pixel height instead of pady), `gradient` ((left, right) colours),
    `outline` (1px border) and `busy` (a spinner before the text)."""

    def __init__(self, parent, text="", command=None, bg="#161D2B", fg="#E6EAF2", font=None,
                 padx=16, pady=7, radius=10, hover=None, cursor="hand2", outline="", gradient=None,
                 icon=None, dot=None, tail="", height=None, disabledforeground="#5E6A80", disabledbackground="#1E293B",
                 bold=False):
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, bd=0, cursor=cursor)
        self._o = dict(text=text, command=command, bg=bg, fg=fg, state="normal", outline=outline,
                       disabledforeground=disabledforeground, disabledbackground=disabledbackground,
                       gradient=gradient, icon=icon, dot=dot, tail=tail, busy=False)
        self.font = tkfont.Font(font=font) if font else tkfont.nametofont("TkDefaultFont")
        if bold:
            self.font.configure(weight="bold")
        self.tail_font = tkfont.Font(font=self.font)
        self.padx, self.pady, self.radius, self.height = padx, pady, radius, height
        self.hover = hover
        self._in = False
        self._spin = 0
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<ButtonRelease-1>", self._click)
        self.bind("<Configure>", lambda e: self.draw())
        self._size()

    def _size(self):
        w = self.font.measure(self._o["text"] or " ") + 2 * self.padx
        if self._o["icon"] or self._o["busy"]:
            w += self.font.metrics("linespace") + px(4)
        if self._o["dot"]:
            w += px(8) + px(8)
        if self._o["tail"]:
            w += self.tail_font.measure(self._o["tail"]) + px(8)
        h = self.height or (self.font.metrics("linespace") + 2 * self.pady)
        super().configure(width=w, height=h)

    def _hover(self, on):
        self._in = on
        self.draw()

    def _click(self, e):
        if self._o["state"] == "normal" and self._o["command"] and 0 <= e.x <= self.winfo_width() \
                and 0 <= e.y <= self.winfo_height():
            self._o["command"]()

    def draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return
        on = self._o["state"] == "normal"
        fill = self._o["bg"] if on else self._o["disabledbackground"]
        hot = on and self._in and self._o["command"]
        if on and self._o["gradient"]:
            a, b = self._o["gradient"]
            if hot:
                a, b = _lighter(a), _lighter(b)
            gradient_rect(self, 0, 0, w, h, self.radius, a, b, vertical=False)
        else:
            if hot:
                fill = self.hover or _lighter(fill)
            outline = self._o["outline"] or fill
            if fill == self["bg"] and not self._o["outline"]:
                outline = ""
            round_rect(self, 0.5, 0.5, w - 0.5, h - 0.5, self.radius, fill=fill, outline=outline)
        fg = self._o["fg"] if on else self._o["disabledforeground"]
        text = self._o["text"]
        tw = self.font.measure(text) if text else 0
        parts = tw
        ls = self.font.metrics("linespace")
        if self._o["icon"] or self._o["busy"]:
            parts += ls + px(4)
        if self._o["dot"]:
            parts += px(16)
        tail_w = self.tail_font.measure(self._o["tail"]) + px(8) if self._o["tail"] else 0
        parts += tail_w
        x = (w - parts) / 2
        if self._o["busy"]:
            r = ls * 0.36
            cx, cy = x + ls / 2, h / 2
            self.create_oval(cx - r, cy - r, cx + r, cy + r, outline=mix(fill, fg, 0.25), width=max(2, px(2)))
            self.create_arc(cx - r, cy - r, cx + r, cy + r, start=self._spin, extent=100, style="arc",
                            outline=fg, width=max(2, px(2)))
            x += ls + px(4)
        elif self._o["icon"]:
            draw_icon(self, self._o["icon"], x + ls / 2, h / 2, ls * 0.62, fg)
            x += ls + px(4)
        if self._o["dot"]:
            r = px(4)
            self.create_oval(x, h / 2 - r, x + 2 * r, h / 2 + r, fill=self._o["dot"], outline="")
            x += px(16)
        if text:
            self.create_text(x, h / 2, text=text, font=self.font, fill=fg, anchor="w")
            x += tw
        if self._o["tail"]:
            self.create_text(x + px(8), h / 2, text=self._o["tail"], font=self.tail_font,
                             fill=mix(fill, fg, 0.55) if on else fg, anchor="w")

    def spin(self):
        """Advance the busy spinner one step (call every ~80 ms while busy)."""
        if self._o["busy"]:
            self._spin = (self._spin - 24) % 360
            self.draw()

    def configure(self, cnf=None, **kw):
        kw = dict(cnf or {}, **kw)
        for k in ("activebackground", "activeforeground", "background"):
            if k in kw:
                v = kw.pop(k)
                if k == "background":
                    super().configure(bg=v)
        mine = {k: kw.pop(k) for k in list(kw) if k in self._o}
        self._o.update(mine)
        if kw:
            super().configure(**kw)
        if any(k in mine for k in ("text", "icon", "dot", "tail", "busy")):
            self._size()
        self.draw()
    config = configure

    def cget(self, key):
        return self._o[key] if key in self._o else super().cget(key)


class Tab(tk.Canvas):
    """An underline-style tab: text, an optional count pill, a 2px accent line when active."""

    def __init__(self, parent, text, command=None, count=None, active=False, accent="#22D3EE",
                 fg_on="#E6EAF2", fg_off="#8B95A7", font=None, pill_bg="#1E293B", pill_fg="#AEB7C7",
                 pill_font=None, height=None, padx=14):
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, bd=0, cursor="hand2")
        self.text, self.count, self.active, self.command = text, count, active, command
        self.accent, self.fg_on, self.fg_off = accent, fg_on, fg_off
        self.pill_bg, self.pill_fg = pill_bg, pill_fg
        self.font = tkfont.Font(font=font) if font else tkfont.nametofont("TkDefaultFont")
        self.pill_font = tkfont.Font(font=pill_font) if pill_font else self.font
        self.h = height or px(38)
        self.padx = padx
        self.bind("<ButtonRelease-1>", lambda e: self.command and self.command())
        self.bind("<Configure>", lambda e: self.draw())
        self._size()

    def _size(self):
        w = self.font.measure(self.text) + 2 * self.padx
        if self.count is not None:
            w += self.pill_font.measure(str(self.count)) + px(14) + px(8)
        super().configure(width=w, height=self.h)

    def set(self, text=None, count=None, active=None):
        if text is not None:
            self.text = text
        if count is not None:
            self.count = count
        if active is not None:
            self.active = active
        self._size()
        self.draw()
    config = set

    def draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        fg = self.fg_on if self.active else self.fg_off
        x = self.padx
        self.create_text(x, h / 2 - px(3), text=self.text, anchor="w", fill=fg, font=self.font)
        x += self.font.measure(self.text)
        if self.count is not None:
            s = str(self.count)
            pw = self.pill_font.measure(s) + px(14)
            ph = self.pill_font.metrics("linespace") + px(2)
            round_rect(self, x + px(8), h / 2 - px(3) - ph / 2, x + px(8) + pw, h / 2 - px(3) + ph / 2, ph / 2,
                       fill=self.pill_bg, outline="")
            self.create_text(x + px(8) + pw / 2, h / 2 - px(3), text=s, fill=self.pill_fg, font=self.pill_font)
        if self.active:
            self.create_rectangle(0, h - px(2), w, h, fill=self.accent, outline="")


class Segmented(tk.Frame):
    """A segmented control: a dark inset track holding one rounded pill per option."""

    def __init__(self, parent, options, value=None, command=None, colors=None, height=None, font=None,
                 track="#0B1019", border="#232C3F", on=("#1E293B", "#E6EAF2"), off="#8B95A7", radius=9, padx=14):
        super().__init__(parent, bg=parent["bg"], bd=0, highlightthickness=0)
        self.options, self.command, self.colors = list(options), command, colors or {}
        self.on, self.off, self.track = on, off, track
        self.value = value
        self.box = Box(self, track, border, radius=radius, inset=px(3))
        self.box.pack()
        self.buttons = {}
        for opt in self.options:
            v, label = opt[0], opt[1]
            icon = opt[2] if len(opt) > 2 else None
            b = Button(self.box.inner, label, lambda v=v: self._pick(v), bg=track, fg=off, font=font, padx=padx,
                       radius=max(4, radius - 2), height=height or px(34), hover=mix(track, "#E6EAF2", 0.06),
                       icon=icon)
            b.pack(side="left")
            self.buttons[v] = b
        self.paint()

    def _pick(self, v):
        if self.command:
            self.command(v)
        else:
            self.set(v)

    def get(self):
        return self.value

    def set(self, value):
        self.value = value
        self.paint()

    def paint(self):
        for v, b in self.buttons.items():
            if v == self.value:
                bg, fg = self.colors.get(v, self.on)
            else:
                bg, fg = self.track, self.off
            b.config(bg=bg, fg=fg)

    def state(self, value, state):
        self.buttons[value].config(state=state)


class Check(tk.Canvas):
    """A flat checkbox (18px box, accent fill with a tick) with a label, bound to a BooleanVar."""

    def __init__(self, parent, text, variable, command=None, fg="#E6EAF2", accent="#22D3EE", font=None,
                 wraplength=0, box="#0B1019", border="#3A4458", size=None):
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, bd=0, cursor="hand2")
        self.text, self.var, self.command = text, variable, command
        self.fg, self.accent, self.boxbg, self.border = fg, accent, box, border
        self.font = tkfont.Font(font=font) if font else tkfont.nametofont("TkDefaultFont")
        self.size = size or px(18)
        self.wrap = wraplength
        self.bind("<ButtonRelease-1>", self._toggle)
        self.bind("<Configure>", lambda e: self.draw())
        self.var.trace_add("write", lambda *a: self.draw())
        self._size()

    def _size(self):
        tw = self.wrap or (self.font.measure(self.text) + px(4))
        tmp = self.create_text(0, 0, text=self.text, font=self.font, anchor="nw", width=tw)
        x1, y1, x2, y2 = self.bbox(tmp)
        self.delete(tmp)
        super().configure(width=self.size + px(10) + (x2 - x1) + px(2), height=max(self.size, y2 - y1) + px(2))

    def _toggle(self, e):
        if 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height():
            self.var.set(not self.var.get())
            if self.command:
                self.command()

    def draw(self):
        self.delete("all")
        s = self.size
        on = bool(self.var.get())
        y = px(1) if self.wrap else max(0, (self.winfo_height() - s) / 2)
        round_rect(self, 0.5, y + 0.5, s - 0.5, y + s - 0.5, px(4), fill=self.accent if on else self.boxbg,
                   outline=self.accent if on else self.border)
        if on:
            draw_icon(self, "check", s / 2, y + s / 2, s * 0.7, "#06101A", width=max(2, px(2)))
        self.create_text(s + px(10), y + (s / 2 if not self.wrap else 0) + (0 if not self.wrap else -px(1)),
                         text=self.text, anchor="w" if not self.wrap else "nw", fill=self.fg, font=self.font,
                         width=self.wrap or 0)


class Entry(_Forward, tk.Entry):
    """A text field inside a rounded box; pack/grid place the box."""
    pass


def entry(parent, fill, outline, focus, radius=9, height=None, prefix=None, suffix=None, affix_fg="#8B95A7",
          affix_font=None, **kw):
    """A rounded text field. `height` is the box's outer height in px; `prefix`/`suffix` are muted
    texts inside the box ($, %, a unit)."""
    inset = px(6)
    box = Box(parent, fill, outline, radius=radius, inset=inset)
    font = kw.get("font")
    e = Entry(box.inner, bg=fill, relief="flat", bd=0, highlightthickness=0, **kw)
    ipady = 0
    if height:
        ls = tkfont.Font(font=font).metrics("linespace") if font else tkfont.nametofont("TkDefaultFont").metrics("linespace")
        ipady = max(0, (height - 2 * inset - ls) // 2)
    if prefix:
        tk.Label(box.inner, text=prefix, bg=fill, fg=affix_fg, font=affix_font or font).pack(side="left", padx=(px(6), 0))
    if suffix:
        tk.Label(box.inner, text=suffix, bg=fill, fg=affix_fg, font=affix_font or font).pack(side="right", padx=(0, px(6)))
    tk.Entry.pack(e, side="left", fill="x", expand=True, padx=px(4), pady=px(2), ipady=ipady)
    e._box = box
    e.bind("<FocusIn>", lambda ev: box.set(outline=focus), add="+")
    e.bind("<FocusOut>", lambda ev: box.set(outline=outline), add="+")
    return e


class ThinBar(tk.Canvas):
    """A 6px scrollbar drawn on a canvas: a rounded thumb, drag to scroll, click to page."""

    def __init__(self, parent, command, bg, thumb="#2A3448", hot="#3A4458"):
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0, width=px(6))
        self.command, self.thumb, self.hot = command, thumb, hot
        self.a, self.b = 0.0, 1.0
        self._drag = None
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))
        self.bind("<Enter>", lambda e: self.draw(True))
        self.bind("<Leave>", lambda e: self.draw(False))

    def set(self, a, b):
        self.a, self.b = float(a), float(b)
        self.draw()

    def _span(self):
        h = self.winfo_height()
        y1 = self.a * h
        y2 = max(y1 + px(24), self.b * h)
        return y1, min(h, y2), h

    def draw(self, hot=None):
        self.delete("all")
        y1, y2, h = self._span()
        if h < 10:
            return
        round_rect(self, 1, y1, px(6) - 1, y2, px(3), fill=self.hot if hot else self.thumb, outline="")

    def _press(self, e):
        y1, y2, h = self._span()
        if y1 <= e.y <= y2:
            self._drag = (e.y, self.a)
        else:
            self.command("scroll", -1 if e.y < y1 else 1, "pages")

    def _move(self, e):
        if self._drag and self.winfo_height():
            y0, a0 = self._drag
            self.command("moveto", max(0.0, min(1.0, a0 + (e.y - y0) / self.winfo_height())))


class Scrollable(tk.Frame):
    """A vertically scrolling area: put widgets in `.inner`. The thin scrollbar shows only when needed,
    the mouse wheel scrolls while the pointer is over it."""

    def __init__(self, parent, bg, style=None):
        super().__init__(parent, bg=bg, bd=0, highlightthickness=0)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0, width=1, height=1)
        self.sb = ThinBar(self, self.canvas.yview, bg)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._fit)
        self.canvas.bind("<Configure>", self._fit)
        self.canvas.configure(yscrollcommand=self._scrolled)
        self.canvas.pack(side="left", fill="both", expand=True)
        self._shown = False
        for w in (self.canvas, self.inner):
            w.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._wheel), add="+")
            w.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"), add="+")

    def _fit(self, e=None):
        self.canvas.itemconfigure(self.win, width=self.canvas.winfo_width())
        self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(), max(self.inner.winfo_reqheight(),
                                                                                 self.canvas.winfo_height())))

    def _scrolled(self, a, b):
        self.sb.set(a, b)
        need = not (float(a) <= 0.0 and float(b) >= 1.0)
        if need != self._shown:
            self._shown = need
            if need:
                self.sb.pack(side="right", fill="y")
            else:
                self.sb.pack_forget()

    def _wheel(self, e):
        if self._shown:
            self.canvas.yview_scroll(-1 * int(e.delta / 120), "units")


def _lighter(hexcode, k=0.12):
    try:
        r, g, b = (int(hexcode[i:i + 2], 16) for i in (1, 3, 5))
    except (ValueError, TypeError):
        return hexcode
    return "#%02X%02X%02X" % tuple(min(255, int(c + (255 - c) * k)) for c in (r, g, b))
