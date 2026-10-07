"""Rounded boxes, buttons and input fields for tkinter, which only draws square ones.

A rounded shape is drawn on a canvas that sits behind the content. The content
lives in an inner frame inset far enough that its square corners stay inside
the rounded ones. `Box.inner` (what callers put widgets in) forwards pack/grid
to the box, so code written for a plain Frame keeps working.
"""
import tkinter as tk
import tkinter.font as tkfont


def round_rect(cv, x1, y1, x2, y2, r, **kw):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, splinesteps=12, **kw)


def mix(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02X%02X%02X" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _inset(r, d):
    """How far a rounded corner of radius r cuts in, at distance d from the edge."""
    import math
    if d >= r:
        return 0
    return r - math.sqrt(max(0.0, r * r - (r - d) ** 2))


def gradient_rect(cv, x1, y1, x2, y2, r, top, bottom, outline=None, vertical=True, step=1):
    """A rounded rectangle filled with a gradient (top->bottom, or left->right)."""
    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
    r = max(0, min(r, (x2 - x1) // 2, (y2 - y1) // 2))
    if vertical:
        n = max(1, y2 - y1)
        for y in range(y1, y2, step):
            d = min(y - y1, y2 - 1 - y)
            dx = _inset(r, d)
            cv.create_line(x1 + dx, y, x2 - dx, y, fill=mix(top, bottom, (y - y1) / n), width=step)
    else:
        n = max(1, x2 - x1)
        for x in range(x1, x2, step):
            d = min(x - x1, x2 - 1 - x)
            dy = _inset(r, d)
            cv.create_line(x, y1 + dy, x, y2 - dy, fill=mix(top, bottom, (x - x1) / n), width=step)
    if outline:
        round_rect(cv, x1, y1, x2 - 1, y2 - 1, r, fill="", outline=outline)


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
    """A rounded panel. Put children in `.inner`."""

    def __init__(self, parent, fill, outline="", radius=14, width=1, inset=None, **kw):
        super().__init__(parent, bg=parent["bg"], bd=0, highlightthickness=0, **kw)
        self.fill, self.outline, self.radius, self.lw = fill, outline, radius, width
        self.cv = tk.Canvas(self, bg=parent["bg"], highlightthickness=0, bd=0)
        self.cv.place(x=0, y=0, relwidth=1, relheight=1)
        inset = max(4, int(radius * 0.32) + 1) if inset is None else inset
        self.inner = Inner(self, bg=fill, bd=0, highlightthickness=0)
        self.inner._box = self
        tk.Frame.pack(self.inner, fill="both", expand=True, padx=inset, pady=inset)
        self.cv.bind("<Configure>", lambda e: self.draw())

    def draw(self):
        cv = self.cv
        cv.delete("all")
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 4 or h < 4:
            return
        o = self.lw / 2 + 0.5
        round_rect(cv, o, o, w - o, h - o, self.radius, fill=self.fill,
                   outline=self.outline or self.fill, width=self.lw)

    def set(self, fill=None, outline=None):
        if fill is not None:
            self.fill = fill
            self.inner.config(bg=fill)
        if outline is not None:
            self.outline = outline
        self.draw()


class Button(tk.Canvas):
    """A rounded button. config() takes text, bg, fg, state, command like tk.Button."""

    def __init__(self, parent, text="", command=None, bg="#161D2B", fg="#E6EAF2", font=None,
                 padx=16, pady=7, radius=10, hover=None, cursor="hand2", outline="", gradient=None):
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, bd=0, cursor=cursor)
        self._o = dict(text=text, command=command, bg=bg, fg=fg, state="normal", outline=outline,
                       disabledforeground="#5E6A80", gradient=gradient)
        self.font = tkfont.Font(font=font) if font else tkfont.nametofont("TkDefaultFont")
        self.padx, self.pady, self.radius = padx, pady, radius
        self.hover = hover
        self._in = False
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<ButtonRelease-1>", self._click)
        self.bind("<Configure>", lambda e: self.draw())
        self._size()

    def _size(self):
        w = self.font.measure(self._o["text"] or " ") + 2 * self.padx
        h = self.font.metrics("linespace") + 2 * self.pady
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
        fill = self._o["bg"] if on else "#1E293B"
        hot = on and self._in and self._o["command"]
        if on and self._o["gradient"]:
            a, b = self._o["gradient"]
            if hot:
                a, b = _lighter(a), _lighter(b)
            gradient_rect(self, 0, 0, w, h, self.radius, a, b, vertical=False)
        else:
            if hot:
                fill = self.hover or _lighter(fill)
            round_rect(self, 1, 1, w - 1, h - 1, self.radius, fill=fill, outline=self._o["outline"] or fill)
        self.create_text(w / 2, h / 2, text=self._o["text"], font=self.font,
                         fill=self._o["fg"] if on else self._o["disabledforeground"])

    def configure(self, cnf=None, **kw):
        kw = dict(cnf or {}, **kw)
        mine = {k: kw.pop(k) for k in list(kw) if k in self._o or k in ("activebackground", "activeforeground")}
        mine.pop("activebackground", None)
        mine.pop("activeforeground", None)
        self._o.update(mine)
        if kw:
            super().configure(**kw)
        if "text" in mine:
            self._size()
        self.draw()
    config = configure

    def cget(self, key):
        return self._o[key] if key in self._o else super().cget(key)


class Entry(_Forward, tk.Entry):
    """A text field inside a rounded box; pack/grid place the box."""
    pass


def entry(parent, fill, outline, focus, radius=9, **kw):
    box = Box(parent, fill, outline, radius=radius, inset=6)
    e = Entry(box.inner, bg=fill, relief="flat", bd=0, highlightthickness=0, **kw)
    tk.Entry.pack(e, fill="x", expand=True, padx=4, pady=2)
    e._box = box
    e.bind("<FocusIn>", lambda ev: box.set(outline=focus), add="+")
    e.bind("<FocusOut>", lambda ev: box.set(outline=outline), add="+")
    return e


def _lighter(hexcode, k=0.12):
    try:
        r, g, b = (int(hexcode[i:i + 2], 16) for i in (1, 3, 5))
    except (ValueError, TypeError):
        return hexcode
    return "#%02X%02X%02X" % tuple(min(255, int(c + (255 - c) * k)) for c in (r, g, b))
