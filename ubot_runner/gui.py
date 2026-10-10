"""The program's window (tkinter, which comes with Python on Windows).

The standard screen every Webull program gets: status and stop/start on top,
four money cards, the chart or the trading settings in the middle, the watched
stocks on the right, positions and what the program did at the bottom. The
first run asks for the keys here; they are written to webull.toml next to the
program and go to Webull only.

Threads: tkinter lives on the main thread. The engine runs on the Live thread;
the window reads Live.snapshot() once a second and queues commands. Slow work
started by the window (connect, test keys, send a buy) runs on short worker
threads that put their answer in `self.inbox`, which the main thread reads.

Looks: the layout follows the approved mockup. tkinter only draws square,
flat widgets, so panels, buttons, pills, tabs, tables and dialogs are drawn on
canvases (see shapes.py). Every size here is in CSS pixels through `P()`, which
scales with the screen's DPI; the process is DPI-aware so text stays sharp.
"""
import copy
import json
import os
import queue
import threading
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from tkinter import ttk

from . import clock
from .broker import CHART_SIZES, DAILY_UP
from .config import ConfigError, parse
from . import shapes as sh
from .shapes import px as P
from .tray import Tray
from .ui import default_lang, text, ui_of, why

BG, PANEL, PANEL2, LINE, ROWLINE, HEAD = "#0A0E16", "#111827", "#0B1019", "#1F2738", "#182033", "#0F1420"
TOPBG = "#0F1522"
INK, INK2, INK3, INK4, SOFT = "#E6EAF2", "#AEB7C7", "#8B95A7", "#5E6A80", "#C9D1DF"
UP, DOWN, UP2, DOWN2, AMBER, VIOLET = "#22C55E", "#F43F5E", "#4ADE80", "#FB7185", "#F59E0B", "#8B5CF6"
ACCENT, ACCENT2 = "#22D3EE", "#67E8F9"
# indicator lines: never green or red (those mean money on this screen)
LINE_COLORS = ("#F59E0B", "#C084FC", "#38BDF8", "#F472B6", "#E2E8F0", "#FDE68A")
TF_LABEL = {"1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30", "1h": "H1", "2h": "H2", "4h": "H4",
            "1d": "D1", "1w": "W1", "1mo": "MN"}
GHOST_LINE, FIELD_LINE = "#3A4458", "#2A3448"
KEY = "#010203"                 # the colour that is see-through on borderless dialogs


def dpi_aware():
    """Tell Windows this process draws for the real DPI (else it stretches the window, blurring text)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        try:
            if not ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):   # per-monitor v2
                raise OSError
        except Exception:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


dpi_aware()


def dark_title(win):
    """Ask Windows 10/11 for a dark title bar (no effect elsewhere)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        on = ctypes.c_int(1)
        for attr in (20, 19):                         # DWMWA_USE_IMMERSIVE_DARK_MODE (new, old builds)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(on), ctypes.sizeof(on)) == 0:
                break
    except Exception:
        pass


OFF = -30000                    # where a hidden view waits (see slide)


def slide(show, hide):
    """Show one of two views placed in the same spot and park the other off-screen. Both keep their
    size, so nothing inside is re-laid out or redrawn: the switch is one move and one paint.
    (Re-packing or un-mapping redrew every panel - a judder; raising one left the other on screen.)"""
    hide.place_configure(x=OFF)
    show.place_configure(x=0)
    show.tkraise()


def money(v, sign=False):
    if v is None:
        return "—"
    s = f"${abs(v):,.2f}"
    if sign:
        return ("+" if v >= 0 else "−") + s
    return ("−" if v < 0 else "") + s


def money0(v):
    """Whole dollars, for the cards' small print."""
    if v is None:
        return "—"
    return ("−" if v < 0 else "") + f"${abs(v):,.0f}"


def num(v, d=2):
    return "—" if v is None else f"{v:,.{d}f}"


def tone(v):
    return INK if v is None or v == 0 else UP if v > 0 else DOWN


def tone2(v):
    """The lighter green/red the mockup uses for small figures."""
    return INK if v is None or v == 0 else UP2 if v > 0 else DOWN2


_FONTS = {}


def F(spec):
    """One tkfont.Font per (family, size, weight) - measuring text every second must not mint fonts."""
    f = _FONTS.get(spec)
    if f is None:
        f = _FONTS[spec] = tkfont.Font(font=spec)
    return f


def first_font(root, *names):
    """The first installed family of `names` (the last one is the fallback)."""
    try:
        have = set(tkfont.families(root))
    except tk.TclError:
        have = set()
    for n in names[:-1]:
        if n in have:
            return n
    return names[-1]


class Table(tk.Frame):
    """A table drawn on canvases: a fixed header band, rows with 1px separators, hover and selection,
    cells that can carry colour, two fonts, a pill or a stop-price-target bar. Scrolls with the wheel.

    cols: [(key, title, weight, min_width_px, anchor)] · rows: [{"iid": str, key: cell}] where a cell is
    ("text", str, colour, font) · ("rich", [(str, colour, font), ...]) · ("pill", str, bg, fg) ·
    ("bar", stop, last, target)."""

    def __init__(self, parent, win, cols, bg=PANEL, on_select=None):
        super().__init__(parent, bg=bg, bd=0, highlightthickness=0)
        self.win, self.cols, self.bg = win, cols, bg
        self.rows, self.sel, self.hover, self.on_select = [], None, None, on_select
        self.head = tk.Canvas(self, bg=HEAD, highlightthickness=0, bd=0, height=P(32))
        self.head.pack(fill="x")
        self.body = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0, height=1, width=1)
        self.body.pack(fill="both", expand=True)
        self.rh = P(44)
        self.head.bind("<Configure>", lambda e: self.draw_head())
        self.body.bind("<Configure>", lambda e: self.draw())
        self.body.bind("<Button-1>", self._click)
        self.body.bind("<Motion>", self._motion)
        self.body.bind("<Leave>", lambda e: self._set_hover(None))
        self.body.bind("<Enter>", lambda e: self.body.bind_all("<MouseWheel>", self._wheel), add="+")
        self.body.bind("<Leave>", lambda e: self.body.unbind_all("<MouseWheel>"), add="+")

    def widths(self):
        w = self.body.winfo_width()
        if w < 10:
            w = self.head.winfo_width()
        mins = sum(c[3] for c in self.cols)
        weights = sum(c[2] for c in self.cols) or 1
        if w < mins:
            return [c[3] * w / mins for c in self.cols]
        extra = w - mins
        return [c[3] + extra * c[2] / weights for c in self.cols]

    def draw_head(self):
        cv = self.head
        cv.delete("all")
        x = 0
        for (key, title, _, _, anchor), w in zip(self.cols, self.widths()):
            tx = x + P(12) if anchor == "w" else x + w - P(12) if anchor == "e" else x + w / 2
            cv.create_text(tx, P(16), text=title, anchor=anchor, fill=INK3, font=self.win.f(11, "bold"))
            x += w
        cv.create_line(0, P(31), cv.winfo_width(), P(31), fill=LINE)

    def set_rows(self, rows):
        self.rows = rows
        if self.sel and not any(r["iid"] == self.sel for r in rows):
            self.sel = None
        self.draw()

    def selection(self):
        return [self.sel] if self.sel else []

    def selection_set(self, iid):
        self.sel = iid
        self.draw()

    def exists(self, iid):
        return any(r["iid"] == iid for r in self.rows)

    def _row_at(self, y):
        i = int((self.body.canvasy(y)) // self.rh)
        return i if 0 <= i < len(self.rows) else None

    def _click(self, e):
        i = self._row_at(e.y)
        if i is not None:
            self.sel = self.rows[i]["iid"] if self.sel != self.rows[i]["iid"] else None
            self.draw()
            if self.on_select:
                self.on_select(self.sel)

    def _motion(self, e):
        self._set_hover(self._row_at(e.y))

    def _set_hover(self, i):
        if i != self.hover:
            self.hover = i
            self.draw()

    def _wheel(self, e):
        if self.rh * len(self.rows) > self.body.winfo_height():
            self.body.yview_scroll(-1 * int(e.delta / 120), "units")

    def draw(self):
        cv = self.body
        cv.delete("all")
        W = cv.winfo_width()
        if W < 10:
            return
        ws = self.widths()
        f = self.win
        for i, r in enumerate(self.rows):
            y = i * self.rh
            selected = r["iid"] == self.sel
            if selected or i == self.hover:
                cv.create_rectangle(0, y, W, y + self.rh, fill="#16203A" if selected else "#141B2A", outline="")
            if selected:
                cv.create_rectangle(0, y, P(3), y + self.rh, fill=f.accent, outline="")
            x = 0
            for (key, _, _, _, anchor), w in zip(self.cols, ws):
                cell = r.get(key)
                if cell:
                    self.cell(cv, cell, x, y, w, anchor)
                x += w
            cv.create_line(0, y + self.rh - 1, W, y + self.rh - 1, fill=ROWLINE)
        cv.configure(scrollregion=(0, 0, W, max(self.rh * len(self.rows), cv.winfo_height())))
        self.draw_head()

    def cell(self, cv, cell, x, y, w, anchor):
        f = self.win
        cy = y + self.rh / 2
        pad = P(12)
        kind = cell[0]
        if kind == "text":
            _, s, color, font = cell
            tx = x + pad if anchor == "w" else x + w - pad if anchor == "e" else x + w / 2
            cv.create_text(tx, cy, text=s, anchor=anchor, fill=color, font=font)
        elif kind == "rich":
            parts = cell[1]
            total = sum(F(fn).measure(s) for s, _, fn in parts) + P(6) * (len(parts) - 1)
            tx = x + pad if anchor == "w" else x + w - pad - total if anchor == "e" else x + (w - total) / 2
            for s, color, fn in parts:
                cv.create_text(tx, cy, text=s, anchor="w", fill=color, font=fn)
                tx += F(fn).measure(s) + P(6)
        elif kind == "pill":
            _, s, bg, fg = cell
            font = f.f(12)
            pw = F(font).measure(s) + P(18)
            ph = P(22)
            px0 = x + pad if anchor == "w" else x + w - pad - pw if anchor == "e" else x + (w - pw) / 2
            sh.round_rect(cv, px0, cy - ph / 2, px0 + pw, cy + ph / 2, ph / 2, fill=bg, outline="")
            cv.create_text(px0 + pw / 2, cy, text=s, fill=fg, font=font)
        elif kind == "bar":
            _, stop, last, target = cell
            x1, x2 = x + pad, x + w - pad
            cv.create_text(x1, cy - P(11), text=num(stop), anchor="w", fill=DOWN2, font=f.mono(11))
            cv.create_text(x2, cy - P(11), text=num(target), anchor="e", fill=UP2, font=f.mono(11))
            by = cy + P(5)
            mid = (x1 + x2) / 2
            sh.gradient_rect(cv, x1, by - P(2), mid + 1, by + P(3), P(2), DOWN, "#334155", vertical=False)
            sh.gradient_rect(cv, mid, by - P(2), x2, by + P(3), P(2), "#334155", UP, vertical=False)
            if stop and target and target > stop and last is not None:
                t = max(0.0, min(1.0, (last - stop) / (target - stop)))
                mx = x1 + (x2 - x1) * t
                sh.round_rect(cv, mx - P(2), by - P(6), mx + P(2), by + P(7), P(2), fill="#FFFFFF", outline="")


class Window:
    def __init__(self, root, name, version, strategy, cfg, cfg_path, connect, save_cfg, make_live, prefs_path=None):
        self.root = root
        self.name = name
        self.version = version
        self.strategy = strategy
        self.cfg = cfg
        self.cfg_path = cfg_path
        self.connect = connect            # cfg -> broker (raises with a sentence on failure)
        self.save_cfg = save_cfg
        self.make_live = make_live        # broker -> Live (started)
        self.ui = ui_of(strategy)
        self.accent = self.ui.get("accent", ACCENT)
        self.lang = default_lang(cfg.lang, self.ui)
        self.live = None
        self.inbox = queue.Queue()
        self.tray = None
        self.snap_at = None
        self.view = "chart"
        # what the window remembers between runs (only its own layout - never keys or settings)
        self.prefs_path = prefs_path or os.path.join(os.path.dirname(os.path.abspath(cfg_path)), "window.json")
        self.prefs = self.load_prefs()
        # the middle panels' share of the height they and the bottom ones split
        self.split_frac = {}
        for side, key in (("left", "split"), ("right", "split_right")):
            v = self.prefs.get(key)
            self.split_frac[side] = min(0.9, max(0.1, v)) if isinstance(v, (int, float)) else self.SPLIT_DEFAULT[side]
        # the chart: bar size (the strategy's own until the customer picks another), lines, trade marks
        bar = getattr(strategy, "BAR", "1d")
        tf = self.prefs.get("chart_tf")
        self.chart_tf = tf if tf in CHART_SIZES else bar if bar in CHART_SIZES else "1d"
        self.chart_ind = self.prefs.get("chart_ind") is not False
        self.chart_marks = self.prefs.get("chart_marks") is not False
        self.tab = "open"
        self.dirty = False
        self.form = {}
        self.watch_rows = {}
        self._pulse = False
        self._dialogs = []
        try:
            sh.set_scale(root.winfo_fpixels("1i") / 96.0)
        except tk.TclError:
            pass
        self.pick_fonts()
        root.title(f"{name} — uBotDesign Webull")
        root.configure(bg=BG)
        sw, shh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.minsize(min(P(1180), sw - P(40)), min(P(700), shh - P(80)))
        try:
            root.state("zoomed")
        except tk.TclError:
            root.geometry(f"{P(1440)}x{P(900)}")
        self.style()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        dark_title(root)
        self.body = tk.Frame(root, bg=BG)
        self.body.pack(fill="both", expand=True)
        if cfg.app_key and cfg.app_secret:
            self.connect_screen(auto=True)
        else:
            self.connect_screen()
        root.after(300, self.tick)
        root.after(150, self.to_front)

    def pick_fonts(self):
        """The mockup's families when installed, else what every Windows has: Leelawadee UI (Thai),
        Segoe UI (English), Cascadia Mono / Consolas for figures."""
        thai = first_font(self.root, "IBM Plex Sans Thai", "Leelawadee UI")
        latin = first_font(self.root, "IBM Plex Sans Thai", "Segoe UI Variable Text", "Segoe UI")
        self.sans = thai if self.lang == "th" else latin
        self.monof = first_font(self.root, "IBM Plex Mono", "Cascadia Mono", "Consolas")
        self.f = lambda size=13, w="normal": (self.sans, -P(size), w)
        self.mono = lambda size=13, w="normal": (self.monof, -P(size), w)

    def to_front(self):
        """Started by a double-click, the window should be the one in front."""
        try:
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(400, lambda: self.root.attributes("-topmost", False))
            self.root.focus_force()
        except tk.TclError:
            pass

    def t(self, key, **kw):
        return text(self.lang, key, **kw)

    def style(self):
        s = ttk.Style(self.root)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        s.layout("Thin.Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"children": [
            ("Vertical.Scrollbar.thumb", {"expand": 1, "sticky": "nswe"})], "sticky": "ns"})])
        s.configure("Thin.Vertical.TScrollbar", background="#2A3448", troughcolor=PANEL, bordercolor=PANEL,
                    lightcolor="#2A3448", darkcolor="#2A3448", gripcount=0, borderwidth=0, relief="flat",
                    width=P(6), arrowsize=0)
        s.map("Thin.Vertical.TScrollbar", background=[("active", "#3A4458")])

    # -- small builders ----------------------------------------------------
    def label(self, parent, txt="", size=13, color=INK, w="normal", mono=False, bg=None, **kw):
        kw.setdefault("padx", 0 if mono else P(3))
        return tk.Label(parent, text=txt, fg=color, bg=bg or parent["bg"],
                        font=(self.mono if mono else self.f)(size, w), **kw)

    def button(self, parent, txt, cmd, kind="ghost", size=13, height=36, padx=14, icon=None, tail="", radius=8,
               bold=None):
        bg = parent["bg"]
        spec = {"main": dict(bg=self.accent, fg="#06101A", gradient=(self.accent, "#3B82F6")),
                "ghost": dict(bg=bg, fg=INK, outline=GHOST_LINE),
                "dark": dict(bg="#161D2B", fg=INK, outline=GHOST_LINE),
                "accent": dict(bg=bg, fg=ACCENT2, outline=self.accent),
                "buy": dict(bg="#16A34A", fg="#FFFFFF"),
                "sell": dict(bg="#E11D48", fg="#FFFFFF"),
                "flat": dict(bg=bg, fg=INK2)}[kind]
        if bold is None:
            bold = kind in ("main", "buy", "sell", "accent")
        return sh.Button(parent, txt, cmd, padx=P(padx), height=P(height), radius=P(radius), icon=icon, tail=tail,
                         font=self.f(size, "bold" if bold else "normal"), **spec)

    def pill(self, parent, txt="", color=INK, dot=None, border="", bg=None, size=13, height=30):
        bg = bg or parent["bg"]
        return sh.Button(parent, txt, None, bg=bg, fg=color, padx=P(12), height=P(height), radius=P(height // 2),
                         font=self.f(size), cursor="arrow", outline=border, dot=dot)

    def card(self, parent, border=LINE, bg=PANEL, radius=12, **kw):
        return sh.Box(parent, bg, border, radius=P(radius), **kw).inner

    def entry(self, parent, value="", width=12, show=None, mono=True, height=36, size=14, radius=8, border=None,
              prefix=None, suffix=None):
        e = sh.entry(parent, PANEL2, border or FIELD_LINE, self.accent, radius=P(radius), height=P(height),
                     prefix=prefix, suffix=suffix, affix_font=self.f(13),
                     fg=INK, insertbackground=INK, width=width, disabledbackground=PANEL2, disabledforeground="#4B5567",
                     font=(self.mono if mono else self.f)(size), show=show or "")
        e.insert(0, value)
        return e

    def clear(self):
        for w in self.body.winfo_children():
            w.destroy()

    def segmented(self, parent, options, value, command, height=34, size=13, colors=None, padx=14):
        return sh.Segmented(parent, options, value=value, command=command, colors=colors, height=P(height),
                            font=self.f(size, "bold"), radius=P(9), padx=P(padx))

    def check(self, parent, txt, var, command=None, size=13, color=INK, accent=None, wraplength=0):
        return sh.Check(parent, txt, var, command=command, fg=color, accent=accent or self.accent, font=self.f(size),
                        wraplength=wraplength)

    # -- first run: the keys -------------------------------------------------
    CARD_TINT, CARD_LINE = "#152038", "#23304A"

    def connect_screen(self, auto=False, error=None):
        self.clear()
        cv = tk.Canvas(self.body, bg=BG, highlightthickness=0, bd=0)
        cv.pack(fill="both", expand=True)
        CW, LW, GAP = P(480), P(520), P(64)
        pad = P(30)
        card_fill = PANEL

        def shade(fx, fy, rect):
            """The card's gradient colour at a point, so a widget's background can match it."""
            x1, y1, x2, y2 = rect
            w, h = x2 - x1, y2 - y1
            import math
            a = math.radians(160)
            sa, ca = math.sin(a), -math.cos(a)
            length = abs(w * sa) + abs(h * ca) or 1.0
            t = (fx - (x1 + w / 2)) * sa / length + (fy - (y1 + h / 2)) * ca / length + 0.5
            return sh.mix(self.CARD_TINT, card_fill, t / 0.7)

        widgets = []                                    # (window item, widget, dy, height) on the card

        def add(widget, dy, h, wide=True):
            kw = {"width": CW - 2 * pad} if wide else {}
            item = cv.create_window(0, 0, anchor="nw", window=widget, **kw)
            widgets.append((item, widget, dy, h))

        k_lbl = self.label(cv, "App Key", 14, INK2)
        key = self.entry(cv, self.cfg.app_key, 30, show="•", height=48, size=16, radius=10)
        s_lbl = self.label(cv, "App Secret", 14, INK2)
        sec = self.entry(cv, self.cfg.app_secret, 30, show="•", height=48, size=16, radius=10)
        go = self.button(cv, self.t("cn_go"), None, "main", 16, height=50, radius=12)
        y = pad
        add(k_lbl, y, P(20), wide=False); y += P(28)
        add(key._box, y, P(48)); y += P(66)
        add(s_lbl, y, P(20), wide=False); y += P(28)
        add(sec._box, y, P(48)); y += P(66)
        add(go, y, P(50)); y += P(50)
        err = None
        if error:
            err = sh.Box(cv, sh.mix(card_fill, DOWN, 0.1), sh.mix(card_fill, DOWN, 0.5), radius=P(12), inset=P(14))
            self.label(err.inner, self.t("cn_bad", e=error), 13, "#FDA4AF", wraplength=CW - 2 * pad - P(32),
                       justify="left").pack(anchor="w")
            y += P(16)
            add(err, y, 0)
            y += P(4)
        later = self.label(cv, self.t("cn_later"), 12, INK3, wraplength=CW - 2 * pad, justify="left", anchor="w")
        y += P(16)
        add(later, y, P(20), wide=False)
        card_h = [y + P(20) + pad]

        def layout(e=None):
            W, H = cv.winfo_width(), cv.winfo_height()
            if W < 100 or H < 100:
                return
            cv.delete("bg")
            # the soft glow top-left (radial-gradient(1200px 600px at 20% 10%, #14213D, BG 60%))
            cx, cy = W * 0.2, H * 0.1
            for i in range(28, 0, -1):
                t = i / 28 * 0.6
                rx, ry = P(1200) * t, P(600) * t
                cv.create_oval(cx - rx, cy - ry, cx + rx, cy + ry, fill=sh.mix("#14213D", BG, t / 0.6),
                               outline="", tags="bg")
            total = LW + GAP + CW
            x0 = max(P(24), (W - total) / 2)
            ch = card_h[0]
            # the card
            cx1 = x0 + LW + GAP
            cy1 = max(P(24), (H - ch) / 2)
            rect = (cx1, cy1, cx1 + CW, cy1 + ch)
            sh.shadow(cv, cx1, cy1, cx1 + CW, cy1 + ch, P(18), BG, spread=P(28), strength=0.6, tags="bg")
            sh.diag_gradient(cv, cx1, cy1, cx1 + CW, cy1 + ch, P(18), self.CARD_TINT, card_fill, end=0.7,
                             outline=self.CARD_LINE, tags="bg")
            for item, w, dy, h in widgets:
                cv.coords(item, cx1 + pad, cy1 + dy)
                wide = cv.itemcget(item, "width") not in ("", "0")
                col = shade(cx1 + (CW / 2 if wide else pad + w.winfo_reqwidth() / 2), cy1 + dy + h / 2, rect)
                try:
                    if isinstance(w, tk.Label):
                        w.configure(bg=col)
                    elif isinstance(w, sh.Button):
                        w.configure(background=col)
                    elif isinstance(w, sh.Box):
                        w.configure(bg=col)
                        w.cv.configure(bg=col)
                    elif hasattr(w, "_box"):
                        w._box.configure(bg=col)
                        w._box.cv.configure(bg=col)
                except tk.TclError:
                    pass
            # the left column: words
            left_h = P(420)
            ly = max(P(24), (H - left_h) / 2)
            lx = x0
            sh.gradient_rect(cv, lx, ly, lx + P(56), ly + P(56), P(14), self.accent, VIOLET, vertical=False, tags="bg")
            sh.draw_icon(cv, "trend", lx + P(28), ly + P(28), P(30), BG, tags="bg", width=P(2.4))
            ly += P(56) + P(22)
            cv.create_text(lx, ly, text=f"{self.name} {self.version}".strip(), anchor="nw", fill=ACCENT2,
                           font=self.f(14, "bold"), tags="bg")
            ly += P(28)
            t1 = cv.create_text(lx, ly, text=self.t("cn_title"), anchor="nw", fill=INK, font=self.f(36, "bold"),
                                width=LW, tags="bg")
            ly = cv.bbox(t1)[3] + P(10)
            t2 = cv.create_text(lx, ly, text=self.t("cn_d"), anchor="nw", fill=INK2, font=self.f(16), width=LW, tags="bg")
            ly = cv.bbox(t2)[3] + P(22)
            for i, k in enumerate(("cn_1", "cn_2", "cn_3"), 1):
                sh.circle(cv, lx + P(14), ly + P(14), P(14), fill=sh.mix(BG, self.accent, 0.14), outline="", tags="bg")
                cv.create_text(lx + P(14), ly + P(14), text=str(i), fill=ACCENT2, font=self.mono(13), tags="bg")
                ti = cv.create_text(lx + P(40), ly + P(2), text=self.t(k), anchor="nw", fill=SOFT, font=self.f(15),
                                    width=LW - P(40), tags="bg")
                ly = max(cv.bbox(ti)[3], ly + P(28)) + P(12)
            ly += P(6)
            cv.create_line(lx, ly, lx + LW, ly, fill=LINE, tags="bg")
            cv.create_text(lx, ly + P(14), text=self.t("cn_safe"), anchor="nw", fill=INK3, font=self.f(13),
                           width=LW, tags="bg")
            cv.tag_lower("bg")

        cv.bind("<Configure>", layout)

        def spin():
            if go.winfo_exists() and go.cget("busy"):
                go.spin()
                self.root.after(80, spin)

        def attempt():
            k, s = key.get().strip(), sec.get().strip()
            if not k or not s:
                return
            go.config(state="disabled", busy=True, text=self.t("cn_busy"), gradient=None, bg="#16203A",
                      outline=self.CARD_LINE, fg=ACCENT2, disabledforeground=ACCENT2, disabledbackground="#16203A")
            go.font.configure(weight="normal")
            spin()
            cfg = copy.copy(self.cfg)
            cfg.app_key, cfg.app_secret = k, s

            def work():
                try:
                    broker = self.connect(cfg)
                    self.inbox.put(("connected", cfg, broker))
                except Exception as e:
                    self.inbox.put(("connect_failed", str(e) or "could not connect"))
            threading.Thread(target=work, daemon=True).start()

        go.config(command=attempt)
        for w in (key, sec):
            w.bind("<Return>", lambda e: attempt())
        self._connect_widgets = (go, err)
        key.focus_set()
        if auto:
            attempt()

    def connected(self, cfg, broker):
        if (cfg.app_key, cfg.app_secret) != (self.cfg.app_key, self.cfg.app_secret):
            self.save_cfg(cfg, self.cfg_path)
        self.cfg = cfg
        self.live = self.make_live(cfg, broker)
        self.live.ask("chart_tf", self.chart_tf)
        self.main_screen()

    # -- the main screen -------------------------------------------------------
    def shown(self, panel):
        return panel not in self.ui.get("hide", [])

    def main_screen(self):
        self.clear()
        b = self.body
        right = self.shown("watch") or self.shown("log")
        b.grid_columnconfigure(0, weight=1)
        b.grid_columnconfigure(1, weight=0, minsize=P(380) if right else 0)
        for r in (3, 4, 5):                 # (rows 4 and 5 were the old shared split)
            b.grid_rowconfigure(r, weight=1 if r == 3 else 0, minsize=0, uniform="")
        self.build_top(b)
        self.notices = tk.Frame(b, bg=BG)
        self.notices.grid(row=1, column=0, columnspan=2, sticky="ew", padx=P(12))
        self.build_kpis(b)
        # two columns, each split on its own: chart over orders on the left, watch list over the
        # log on the right; each column has its own bar to drag
        self.cols = {"left": tk.Frame(b, bg=BG)}
        self.cols["left"].grid(row=3, column=0, sticky="nsew", padx=(P(12), P(10)), pady=(P(10), P(12)))
        if right:
            self.cols["right"] = tk.Frame(b, bg=BG)
            self.cols["right"].grid(row=3, column=1, sticky="nsew", padx=(0, P(12)), pady=(P(10), P(12)))
        self.build_center(self.cols["left"])
        self.build_watch(self.cols.get("right"))
        self.build_bottom(self.cols["left"], self.cols.get("right"))
        for side, col in self.cols.items():
            col.grid_columnconfigure(0, weight=1)
            self.build_split(side, col)
        self.snap_at = None

    SPLIT_DEFAULT = {"left": 0.56, "right": 0.6}
    # the smallest the top / bottom panel of each column may be dragged to (unscaled px)
    SPLIT_MIN = {"left": (250, 150), "right": (150, 110)}
    SPLIT_PREF = {"left": "split", "right": "split_right"}

    def build_split(self, side, col):
        """The bar between a column's top panel and its bottom one: drag it to give either more of
        the height. Rows 0 and 2 share one `uniform` group, so their heights are exactly in
        proportion to their weights; a drag only changes the weights. A column with one panel
        just gives it all the height."""
        kids = {int(w.grid_info()["row"]) for w in col.grid_slaves()}
        if kids != {0, 2}:
            for r in (0, 2):
                col.grid_rowconfigure(r, weight=1 if r in kids else 0, uniform="")
            return
        bar = tk.Canvas(col, bg=BG, highlightthickness=0, bd=0, height=P(10), cursor="sb_v_double_arrow")
        bar.grid(row=1, column=0, sticky="ew")
        col.grid_rowconfigure(1, weight=0, minsize=P(10))

        def grip(hot=False):
            bar.delete("all")
            w = bar.winfo_width()
            if w > 1:
                gw = P(44)
                sh.smooth_rect(bar, (w - gw) / 2, P(3), (w + gw) / 2, P(7), P(2),
                               self.accent if hot else "#334155", BG)
        drag = {}

        def press(e):
            top, bottom = self.split_heights(col)
            drag.update(y=e.y_root, top=top, total=top + bottom, y0=bar.winfo_y() + bar.winfo_height() // 2)
            drag["ghost"] = tk.Frame(col, bg=self.accent, height=max(2, P(2)))
            drag["ghost"].place(x=0, y=drag["y0"], relwidth=1)
            grip(True)

        def move(e):
            if not drag:
                return
            mins = self.SPLIT_MIN[side]
            lo, hi = P(mins[0]), drag["total"] - P(mins[1])
            top = min(max(drag["top"] + e.y_root - drag["y"], lo), max(lo, hi))
            drag["frac"] = top / max(1, drag["total"])
            # while dragging only a guide line moves; the panels are laid out once, on release
            # (re-laying them out per mouse move redraws every panel each time - seconds of lag)
            drag["ghost"].place_configure(y=drag["y0"] + top - drag["top"])

        def release(e):
            if drag.get("ghost"):
                drag["ghost"].destroy()
            if "frac" in drag:
                self.split_frac[side] = drag["frac"]
                self.split_rows(side, col)
                self.save_prefs(**{self.SPLIT_PREF[side]: round(drag["frac"], 4)})
            drag.clear()
            grip(bar.winfo_containing(e.x_root, e.y_root) is bar)

        def reset(e):
            self.split_frac[side] = self.SPLIT_DEFAULT[side]
            self.split_rows(side, col)
            self.save_prefs(**{self.SPLIT_PREF[side]: None})
        bar.bind("<Configure>", lambda e: grip())
        bar.bind("<Enter>", lambda e: grip(True))
        bar.bind("<Leave>", lambda e: drag or grip())
        bar.bind("<ButtonPress-1>", press)
        bar.bind("<B1-Motion>", move)
        bar.bind("<ButtonRelease-1>", release)
        bar.bind("<Double-Button-1>", reset)
        self.split_rows(side, col)

    def load_prefs(self):
        try:
            with open(self.prefs_path, encoding="utf-8") as f:
                doc = json.load(f)
            return doc if isinstance(doc, dict) else {}
        except (OSError, ValueError):
            return {}                       # missing or unreadable: the default layout

    def save_prefs(self, **kw):
        """Remember layout choices in window.json beside webull.toml. A failed write only means the
        next start uses the default layout."""
        for k, v in kw.items():
            if v is None:
                self.prefs.pop(k, None)
            else:
                self.prefs[k] = v
        tmp = self.prefs_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.prefs, f)
            os.replace(tmp, self.prefs_path)
        except OSError:
            pass

    def split_heights(self, col):
        return col.grid_bbox(0, 0)[3], col.grid_bbox(0, 2)[3]

    def split_rows(self, side, col):
        """Share a column's height between its top (row 0) and bottom (row 2) panels."""
        top = max(1, int(round(self.split_frac[side] * 1000)))
        col.grid_rowconfigure(0, weight=top, minsize=0, uniform="split")
        col.grid_rowconfigure(2, weight=max(1, 1000 - top), minsize=0, uniform="split")

    def build_top(self, b):
        wrap = tk.Frame(b, bg=TOPBG)
        wrap.grid(row=0, column=0, columnspan=2, sticky="ew")
        top = tk.Frame(wrap, bg=TOPBG)
        top.pack(fill="x")
        tk.Frame(wrap, bg=LINE, height=1).pack(fill="x")
        self.logo(top, P(36)).pack(side="left", padx=(P(20), P(12)), pady=P(10))
        self.label(top, self.name, 18, INK, "bold").pack(side="left")
        self.sub = self.label(top, "", 13, INK3)
        self.sub.pack(side="left", padx=P(12))
        self.run_btn = self.button(top, "", self.on_run_button, "dark", 14, height=40, padx=16, radius=9,
                                   icon="pause", tail="▾", bold=False)
        self.run_btn.pack(side="right", padx=(P(10), P(20)), pady=P(8))
        self.clock = self.label(top, "", 13, INK3, mono=True)
        self.clock.pack(side="right", padx=P(10))
        self.mkt = self.pill(top, "", INK2, dot=INK4, bg=TOPBG)
        self.mkt.pack(side="right", padx=P(4))
        self.mode_pill = self.pill(top)
        self.mode_pill.pack(side="right", padx=P(6))
        self.run_pill = self.pill(top)
        self.run_pill.pack(side="right", padx=P(6))

    def logo(self, parent, size):
        cv = tk.Canvas(parent, width=size, height=size, bg=parent["bg"], highlightthickness=0)
        sh.gradient_rect(cv, 0, 0, size, size, size // 4, self.accent, VIOLET, vertical=False)
        sh.draw_icon(cv, "trend", size / 2, size / 2, size * 0.56, BG, width=max(2, size / 15))
        return cv

    # The money cards are drawn whole on a canvas: a widget cannot sit on a gradient.
    KPI_TINT = {"kpi_equity": ("#152038", "#23304A"), "kpi_today": ("#0F2A20", "#1D3B2E"),
                "kpi_stats": ("#1F1838", "#2E2650"), "kpi_used": ("#2A1E10", "#3B2C17")}

    def build_kpis(self, b):
        row = tk.Frame(b, bg=BG)
        row.grid(row=2, column=0, columnspan=2, sticky="ew", padx=P(12), pady=(P(10), 0))
        self.k = {}
        keys = [k for k in ("kpi_equity", "kpi_today", "kpi_stats", "kpi_used") if self.shown(k)]
        for i, key in enumerate(keys):
            row.grid_columnconfigure(i, weight=1, uniform="kpi")
            cv = tk.Canvas(row, height=P(100), width=P(200), bg=BG, highlightthickness=0)
            cv.grid(row=0, column=i, sticky="nsew", padx=(0, 0 if i == len(keys) - 1 else P(10)))
            cv.bind("<Configure>", lambda e, k=key: self.draw_kpi(k, bg=True))
            self.k[key] = {"cv": cv, "data": None}

    def draw_kpi(self, key, bg=False):
        d = self.k.get(key)
        if not d:
            return
        cv = d["cv"]
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 40:
            return
        if bg:
            cv.delete("bg")
            tint, line = self.KPI_TINT[key]
            sh.diag_gradient(cv, 0, 0, w, h, P(12), tint, PANEL, end=0.7, outline=line, tags="bg")
            cv.tag_lower("bg")
        cv.delete("fg")
        if d["data"]:
            getattr(self, "kpi_" + key[4:])(cv, w, h, d["data"])

    def set_kpi(self, key, **data):
        d = self.k.get(key)
        if d is None:
            return
        d["data"] = data
        self.draw_kpi(key)

    def kpi_text(self, cv, x, y, title, big, small, big_fg=INK):
        cv.create_text(x, y + P(14), text=title, anchor="w", fill=INK3, font=self.f(11), tags="fg")
        cv.create_text(x, y + P(44), text=big, anchor="w", fill=big_fg, font=self.mono(24, "bold"), tags="fg")
        cv.create_text(x, y + P(74), text=small, anchor="w", fill=INK3, font=self.f(12), tags="fg")

    def kpi_equity(self, cv, w, h, d):
        self.kpi_text(cv, P(14), 0, d["title"], d["big"], d["small"])
        tw = max(F(self.mono(24, "bold")).measure(d["big"]),
                 F(self.f(12)).measure(d["small"])) + P(28)
        x1, x2 = max(tw, w * 0.5), w - P(14)
        if x2 - x1 > P(60):
            self.spark(cv, d["eq"], self.accent, x1, P(14), x2, P(60))
            if d["pct"] is not None:
                s = f"{d['pct']:+.2f}%"
                cv.create_text(x2, P(76), text=s, anchor="e", fill=tone2(d["pct"]), font=self.mono(11), tags="fg")
                cv.create_text(x2 - F(self.mono(11)).measure(s) - P(6), P(76), text=d["label30"],
                               anchor="e", fill=INK3, font=self.f(11), tags="fg")

    def kpi_today(self, cv, w, h, d):
        self.kpi_text(cv, P(14), 0, d["title"], d["big"], d["small"], d["big_fg"])
        tw = max(F(self.mono(24, "bold")).measure(d["big"]),
                 F(self.f(12)).measure(d["small"])) + P(28)
        x1, x2 = max(tw, w * 0.55), w - P(14)
        if x2 - x1 > P(60):
            self.bars(cv, d["days"], x1, P(16), x2, P(60))
            cv.create_text(x2, P(76), text=d["label"], anchor="e", fill=INK3, font=self.f(11), tags="fg")

    def kpi_stats(self, cv, w, h, d):
        r = P(30)
        cx, cy = P(14) + r + P(4), h / 2
        tint = self.KPI_TINT["kpi_stats"][0]
        sh.ring(cv, cx, cy, r, P(4), d["share"], VIOLET, "#2A2F45", tags="fg",
                bg_at=lambda x, y: sh.diag_color(x, y, 0, 0, w, h, tint, PANEL, end=0.7))
        cv.create_text(cx, cy, text=d["ring"], fill=INK, font=self.mono(12, "bold"), tags="fg")
        x = cx + r + P(14)
        cv.create_text(x, P(22), text=d["title"], anchor="w", fill=INK3, font=self.f(11), tags="fg")
        cv.create_text(x, P(48), text=d["line"], anchor="w", fill=INK, font=self.f(14), tags="fg")
        cv.create_text(x, P(74), text=d["total_label"], anchor="w", fill=INK3, font=self.f(14), tags="fg")
        tw = F(self.f(14)).measure(d["total_label"])
        cv.create_text(x + tw + P(6), P(74), text=d["total"], anchor="w", fill=tone2(d["total_v"]),
                       font=self.mono(14), tags="fg")

    def kpi_used(self, cv, w, h, d):
        x1, x2 = P(14), w - P(14)
        cv.create_text(x1, P(18), text=d["title"], anchor="w", fill=INK3, font=self.f(11), tags="fg")
        cv.create_text(x2, P(18), text=d["held"], anchor="e", fill=INK3, font=self.f(12), tags="fg")
        cv.create_text(x1, P(48), text=d["big"], anchor="w", fill=INK, font=self.mono(24, "bold"), tags="fg")
        bw = F(self.mono(24, "bold")).measure(d["big"])
        cv.create_text(x1 + bw + P(8), P(51), text=d["cap"], anchor="w", fill=INK3, font=self.mono(14), tags="fg")
        y = P(80)
        sh.round_rect(cv, x1, y - P(3), x2, y + P(4), P(3), fill="#232B3D", outline="", tags="fg")
        if d["frac"]:
            end = x1 + (x2 - x1) * min(1.0, d["frac"])
            sh.gradient_rect(cv, x1, y - P(3), max(end, x1 + P(8)), y + P(4), P(3), AMBER, "#F97316",
                             vertical=False, tags="fg")

    def build_center(self, b):
        box = sh.Box(b, PANEL, LINE, radius=P(12), inset=P(6))
        frame = box.inner
        frame.grid(row=0, column=0, sticky="nsew")
        bar = tk.Frame(frame, bg=PANEL)
        bar.pack(fill="x", padx=P(10), pady=(P(4), P(6)))
        tabs = [("chart", self.t("chart"), "chart"), ("settings", self.t("settings"), "sliders")]
        if not self.shown("chart"):
            tabs = tabs[1:]
            self.view = "settings"
        self.seg = self.segmented(bar, tabs, self.view, self.set_view)
        self.seg.pack(side="left")
        self.dirty_lbl = self.pill(bar, "", "#FCD34D", dot=AMBER, size=12)
        stack = tk.Frame(frame, bg=PANEL)
        stack.pack(fill="both", expand=True)
        self.chart_frame = tk.Frame(stack, bg=PANEL)
        self.chart_frame.place(x=0, y=0, relwidth=1, relheight=1)
        self.chart_head = tk.Frame(self.chart_frame, bg=PANEL)
        self.chart_head.pack(fill="x", padx=P(10), pady=(P(2), 0))
        self.chart_title = self.label(self.chart_head, "", 20, INK, "bold")
        self.chart_title.pack(side="left")
        self.chart_px = self.label(self.chart_head, "", 22, INK, "bold", mono=True)
        self.chart_px.pack(side="left", padx=(P(12), P(8)))
        self.chart_chg = self.label(self.chart_head, "", 14, INK3, mono=True)
        self.chart_chg.pack(side="left")
        self.legend = tk.Canvas(self.chart_head, bg=PANEL, highlightthickness=0, bd=0, height=P(26), width=P(10))
        self.legend.pack(side="right")
        tools = tk.Frame(self.chart_frame, bg=PANEL)
        tools.pack(fill="x", padx=P(10), pady=(P(6), 0))
        self.tf_seg = self.segmented(tools, [(tf, TF_LABEL[tf]) for tf in CHART_SIZES], self.chart_tf,
                                     self.set_chart_tf, height=28, size=12, padx=7)
        self.tf_seg.pack(side="left")
        self.marks_var = tk.BooleanVar(value=self.chart_marks)
        self.ind_var = tk.BooleanVar(value=self.chart_ind)
        self.check(tools, self.t("c_marks"), self.marks_var, self.toggle_chart, size=12).pack(side="right")
        self.check(tools, self.t("c_ind"), self.ind_var, self.toggle_chart, size=12).pack(side="right", padx=(0, P(14)))
        bar = getattr(self.strategy, "BAR", "")
        if bar in TF_LABEL:
            self.label(tools, self.t("c_sys_tf", tf=TF_LABEL[bar]), 11, INK4).pack(side="left", padx=(P(10), 0))
        self.chart = tk.Canvas(self.chart_frame, bg=PANEL, highlightthickness=0, height=P(120))
        self.chart.pack(fill="both", expand=True, padx=P(10), pady=(P(6), P(8)))
        self.chart.bind("<Configure>", lambda e: self.draw_chart())
        self.settings_frame = tk.Frame(stack, bg=PANEL)
        self.settings_frame.place(x=0, y=0, relwidth=1, relheight=1)
        self.build_settings(self.settings_frame)
        self.set_view(self.view)

    def set_chart_tf(self, tf):
        self.chart_tf = tf
        self.tf_seg.set(tf)
        self.save_prefs(chart_tf=tf)
        if self.live:
            self.live.ask("chart_tf", tf)

    def toggle_chart(self):
        self.chart_ind, self.chart_marks = bool(self.ind_var.get()), bool(self.marks_var.get())
        self.save_prefs(chart_ind=self.chart_ind, chart_marks=self.chart_marks)
        self.draw_chart()

    def set_view(self, view):
        # both views are built once and share one grid cell of fixed size: switching hides one and
        # shows the other at the size it already had, so no panel is re-laid out or redrawn
        # (re-packing them made every panel redraw - a judder)
        self.view = view
        self.seg.set(view)
        show, hide = (self.chart_frame, self.settings_frame) if view == "chart" else \
            (self.settings_frame, self.chart_frame)
        slide(show, hide)

    def build_watch(self, b):
        if not self.shown("watch"):
            self.watch_list = None
            return
        w = self.card(b)
        w.grid(row=0, column=0, sticky="nsew")
        head = tk.Frame(w, bg=PANEL)
        head.pack(fill="x", padx=P(10), pady=(P(6), P(6)))
        self.label(head, self.t("watch"), 15, INK, "bold").pack(side="left")
        self.label(head, self.t("watch_tip"), 12, INK3).pack(side="right")
        if self.ui.get("buttons"):
            bb = tk.Frame(w, bg=PANEL)
            bb.pack(fill="x", padx=P(10), pady=(0, P(6)))
            for x in self.ui["buttons"]:
                self.button(bb, x["label"], lambda i=x["id"]: self.press(i), "ghost", 12, height=30).pack(
                    side="left", padx=(0, P(6)))
        sc = sh.Scrollable(w, PANEL)
        sc.pack(fill="both", expand=True)
        self.watch_list = sc.inner
        self.watch_rows = {}

    def build_bottom(self, b, side=None):
        box = sh.Box(b, PANEL, LINE, radius=P(12), inset=P(6))
        left = box.inner
        left.grid(row=2, column=0, sticky="nsew")
        bar = tk.Frame(left, bg=PANEL)
        bar.pack(fill="x", padx=P(4), pady=(P(2), 0))
        self.tab_btn = {}
        tabs = [("open", "open_tab")] + ([("closed", "closed_tab")] if self.shown("closed") else [])
        for key, label in tabs:
            tb = sh.Tab(bar, self.t(label), lambda k=key: self.set_tab(k), count=0, active=key == self.tab,
                        accent=self.accent, font=self.f(14, "bold"), pill_font=self.mono(11), height=P(38), padx=P(12))
            tb.pack(side="left")
            self.tab_btn[key] = (tb, self.t(label))
        self.sell_btn = self.button(bar, self.t("sell_sel"), self.ask_sell, "sell", 13, height=32, padx=14)
        self.sell_btn.pack(side="right", padx=(P(10), P(6)), pady=(0, P(4)))
        self.won_cv = tk.Canvas(bar, bg=PANEL, highlightthickness=0, bd=0, height=P(20), width=P(10))
        self.won_cv.pack(side="right", padx=P(6), pady=(0, P(4)))
        tk.Frame(left, bg=LINE, height=1).pack(fill="x")
        holder = tk.Frame(left, bg=PANEL)
        holder.pack(fill="both", expand=True)
        cols_open = [("sym", self.t("h_sym"), 1, P(96), "w"), ("qty", self.t("h_qty"), 1, P(58), "e"),
                     ("avg", self.t("h_avg"), 1, P(70), "e"), ("last", self.t("h_last"), 1, P(70), "e"),
                     ("pl", self.t("h_pl"), 2, P(132), "e"),
                     ("bar", f"{self.t('c_stop')} ← {self.t('h_last')} → {self.t('c_target')}", 3, P(150), "w"),
                     ("by", self.t("h_by"), 1, P(84), "center")]
        self.tree_open = Table(holder, self, cols_open)
        cols_closed = [("when", self.t("h_when"), 1, P(96), "w"), ("sym", self.t("h_sym"), 1, P(66), "w"),
                       ("qty", self.t("h_qty"), 1, P(58), "e"), ("buy", self.t("h_avg"), 1, P(70), "e"),
                       ("sell", self.t("h_sell"), 1, P(70), "e"), ("pl", self.t("h_pl"), 2, P(100), "e"),
                       ("why", self.t("h_why"), 2, P(120), "w"), ("by", self.t("h_by"), 1, P(80), "w")]
        self.tree_closed = Table(holder, self, cols_closed)
        for tbl in (self.tree_open, self.tree_closed):
            tbl.place(x=0, y=0, relwidth=1, relheight=1)
        self.set_tab("open")
        if self.shown("log"):
            right = self.card(side)
            right.grid(row=2, column=0, sticky="nsew")
            self.label(right, self.t("log"), 15, INK, "bold").pack(anchor="w", padx=P(10), pady=(P(6), P(4)))
            self.log_txt = tk.Text(right, bg=PANEL, fg=SOFT, relief="flat", wrap="word", height=4, width=10, bd=0,
                                   font=self.f(13), highlightthickness=0, cursor="arrow", spacing1=P(3), spacing3=P(3),
                                   padx=P(10), selectbackground="#16203A")
            self.log_txt.pack(fill="both", expand=True)
            tf = F(self.mono(13))
            indent = tf.measure("● ") + tf.measure("00:00") + tf.measure("  ")
            self.log_txt.tag_configure("d", foreground=self.accent, font=self.f(9))
            self.log_txt.tag_configure("dw", foreground=AMBER, font=self.f(9))
            self.log_txt.tag_configure("t", foreground=INK3, font=self.mono(13))
            self.log_txt.tag_configure("m", foreground=SOFT, lmargin2=indent)
            self.log_txt.tag_configure("w", foreground="#FDE68A", lmargin2=indent)
            self.label(right, self.t("foot"), 11, INK4, wraplength=P(330), justify="left").pack(
                anchor="w", padx=P(10), pady=(P(4), P(6)))
        else:
            self.log_txt = None

    def set_tab(self, tab):
        self.tab = tab
        for k, (tb, label) in self.tab_btn.items():
            tb.set(active=k == tab)
        show, hide = (self.tree_open, self.tree_closed) if tab == "open" else (self.tree_closed, self.tree_open)
        slide(show, hide)
        self.sell_btn.config(state="normal" if tab == "open" else "disabled")

    # -- trading settings ---------------------------------------------------
    def build_settings(self, parent):
        foot = tk.Frame(parent, bg=PANEL)
        foot.pack(side="bottom", fill="x", padx=P(10), pady=(P(6), P(6)))
        tk.Frame(foot, bg=LINE, height=1).pack(fill="x", pady=(0, P(8)))
        row = tk.Frame(foot, bg=PANEL)
        row.pack(fill="x")
        self.save_msg = self.label(row, self.t("saved_to"), 12, INK3)
        self.save_msg.pack(side="left")
        self.button(row, self.t("save"), self.save_settings, "main", 14, height=38, padx=18, radius=9).pack(
            side="right", padx=(P(8), 0))
        self.button(row, self.t("revert"), self.fill_settings, "ghost", 14, height=38, padx=16, radius=9).pack(side="right")
        sc = sh.Scrollable(parent, PANEL)
        sc.pack(fill="both", expand=True, padx=(P(10), P(4)))
        self.settings_inner = sc.inner
        self.fill_settings()

    def section(self, parent, title, color=INK, tint=None, border="#232C3F", pack=True):
        box = sh.Box(parent, PANEL, border, radius=P(10), inset=P(14), tint=tint, title=title, title_fg=color,
                     title_font=self.f(13, "bold"), pad=(P(14), P(12), P(12)))
        if pack:
            box.pack(fill="x", pady=(P(12), 0))
        return box

    def field(self, parent, label, value, row, col, width=14, prefix=None, suffix=None, size=14, height=36,
              border=None, label_color=INK2, stretch=True):
        cell = tk.Frame(parent, bg=parent["bg"])
        cell.grid(row=row, column=col, sticky="ew", padx=(0, P(14)), pady=(P(4), P(4)))
        self.label(cell, label, 12, label_color, wraplength=P(190), justify="left").pack(anchor="w", pady=(0, P(5)))
        e = self.entry(cell, value, width, size=size, height=height, prefix=prefix, suffix=suffix, border=border)
        e.pack(anchor="w", fill="x" if stretch else None)
        e.bind("<KeyRelease>", lambda ev: self.touch())
        return e

    def touch(self, *_):
        self.dirty = True
        self.dirty_lbl.config(text=self.t("unsaved"))
        if not self.dirty_lbl.winfo_ismapped():
            self.dirty_lbl.pack(side="left", padx=P(10))

    def fill_settings(self):
        for w in self.settings_inner.winfo_children():
            w.destroy()
        cfg = self.live.eng.cfg if self.live else self.cfg
        self.form = {}
        inner = self.settings_inner
        pair = tk.Frame(inner, bg=PANEL)
        pair.pack(fill="x")
        pair.grid_columnconfigure(0, weight=1, uniform="s")
        pair.grid_columnconfigure(1, weight=1, uniform="s")
        # stocks to trade: chips + an add box
        sb = self.section(pair, self.t("s_syms"), ACCENT2, tint="#152038", pack=False)
        sb.grid(row=0, column=0, sticky="nsew", padx=(0, P(7)), pady=(P(12), 0))
        s = sb.inner
        self.form["symbols"] = SymbolChips(s, self, list(cfg.symbols))
        self.form["symbols"].pack(fill="x")
        self.label(s, self.t("s_syms_d"), 11, INK3, wraplength=P(330), justify="left").pack(anchor="w", pady=(P(8), 0))
        # money and risk
        lb = self.section(pair, self.t("s_limits"), "#FCD34D", tint="#2A1E10", border="#3B2C17", pack=False)
        lb.grid(row=0, column=1, sticky="nsew", padx=(P(7), 0), pady=(P(12), 0))
        s = lb.inner
        s.grid_columnconfigure(0, weight=1, uniform="l")
        s.grid_columnconfigure(1, weight=1, uniform="l")
        L = cfg.limits
        for i, (k, label, v, pre, suf) in enumerate((("max_total_notional", "l_total", L.max_total_notional, "$", None),
                                                     ("max_notional_per_order", "l_per", L.max_notional_per_order, "$", None),
                                                     ("max_open_positions", "l_pos", L.max_open_positions, None, None),
                                                     ("max_orders_per_day", "l_orders", L.max_orders_per_day, None, None),
                                                     ("daily_loss_pct", "l_loss", L.daily_loss_pct, None, "%"))):
            self.form["lim:" + k] = self.field(s, self.t(label), f"{v:g}", i // 2, i % 2, prefix=pre, suffix=suf)
        # the strategy's inputs
        if self.strategy.INPUTS:
            ib = self.section(inner, self.t("s_inputs") + f" · {self.name} {self.version}".rstrip(), "#C4B5FD",
                              tint="#1F1838", border="#2E2650")
            s = ib.inner
            for c in range(3):
                s.grid_columnconfigure(c, weight=1, uniform="i")
            for i, (k, d) in enumerate(self.strategy.INPUTS.items()):
                v = cfg.inputs.get(k, d)
                if isinstance(d, bool):
                    var = tk.BooleanVar(value=bool(v))
                    cell = tk.Frame(s, bg=PANEL)
                    cell.grid(row=i // 3, column=i % 3, sticky="w", padx=(0, P(14)), pady=(P(22), P(4)))
                    self.check(cell, k, var, command=self.touch).pack(anchor="w")
                    self.form["in:" + k] = var
                elif isinstance(d, str) and self._choices(k):
                    # a text input with a fixed list of values (strategy.CHOICES): pick, never type
                    cell = tk.Frame(s, bg=PANEL)
                    cell.grid(row=i // 3, column=i % 3, sticky="w", padx=(0, P(14)), pady=(P(4), P(4)))
                    self.label(cell, k, 12, INK2).pack(anchor="w", pady=(0, P(5)))
                    opts = self._choices(k)
                    seg = self.segmented(cell, [(x, x) for x in opts], str(v) if str(v) in opts else None, None,
                                         height=36, size=13, padx=16)
                    seg.command = lambda x, seg=seg: (seg.set(x), self.touch())
                    seg.pack(anchor="w")
                    self.form["in:" + k] = seg
                else:
                    self.form["in:" + k] = self.field(s, k, f"{v:g}" if isinstance(v, float) else str(v), i // 3, i % 3)
            self.label(s, self.t("s_inputs_d"), 11, INK3, wraplength=P(700), justify="left").grid(
                row=99, column=0, columnspan=3, sticky="w", pady=(P(6), 0))
        pair2 = tk.Frame(inner, bg=PANEL)
        pair2.pack(fill="x", pady=(0, P(6)))
        pair2.grid_columnconfigure(0, weight=1, uniform="m")
        pair2.grid_columnconfigure(1, weight=1, uniform="m")
        # mode
        mb = self.section(pair2, self.t("s_mode"), INK, pack=False)
        mb.grid(row=0, column=0, sticky="nsew", padx=(0, P(7)), pady=(P(12), 0))
        s = mb.inner
        self.mode_seg = self.segmented(s, [("live", self.t("m_live")), ("dry", self.t("m_dry"))], None, self.ask_mode,
                                       colors={"live": (sh.mix(PANEL2, UP, 0.18), "#86EFAC"),
                                               "dry": (sh.mix(PANEL2, VIOLET, 0.22), "#DDD6FE")}, padx=16)
        self.mode_seg.pack(anchor="w")
        self.paint_mode(cfg.dry_run)
        self.form_seg = {}
        for key, opts, cur in (("on_close", (("ask", "c_ask"), ("tray", "c_tray"), ("quit", "c_quit")), cfg.on_close),
                               ("lang", (("th", None), ("en", None)), self.lang)):
            row = tk.Frame(s, bg=PANEL)
            row.pack(anchor="w", pady=(P(10), 0))
            self.label(row, self.t("s_close" if key == "on_close" else "s_lang"), 12, INK2).pack(side="left", padx=(0, P(10)))
            var = tk.StringVar(value=cur)
            seg = self.segmented(row, [(v, self.t(label) if label else {"th": "ไทย", "en": "English"}[v]) for v, label in opts],
                                 cur, lambda v, var=var, key=key: (var.set(v), self.form_seg[key].set(v), self.touch()),
                                 height=28, size=12, padx=10)
            seg.pack(side="left")
            self.form_seg[key] = seg
            self.form[key] = var
        self.label(s, self.t("m_live_d"), 11, INK3, wraplength=P(330), justify="left").pack(anchor="w", pady=(P(10), 0))
        # the Webull account
        ab = self.section(pair2, self.t("s_acct"), INK, pack=False)
        ab.grid(row=0, column=1, sticky="nsew", padx=(P(7), 0), pady=(P(12), 0))
        s = ab.inner
        acct = self.live.snapshot().get("account", "") if self.live else ""
        for k, v, dot in ((self.t("a_status"), self.t("a_ok"), True), (self.t("a_acct"), "Webull Thailand ••••" + acct, False),
                          ("App Key", "••••••••" + cfg.app_key[-4:], False), ("App Secret", "••••••••••••", False)):
            r = tk.Frame(s, bg=PANEL)
            r.pack(fill="x", pady=P(2))
            self.label(r, k, 13, INK3).pack(side="left")
            if dot:
                self.pill(r, v, ACCENT2, dot=self.accent, size=13, height=22).pack(side="right")
            else:
                self.label(r, v, 13, INK, mono=True).pack(side="right")
        keybox = tk.Frame(s, bg=PANEL)
        keybox.pack(anchor="w", fill="x", pady=(P(8), 0))
        self.button(keybox, self.t("a_change"), lambda: self.key_form(keybox), "ghost", 13, height=34).pack(anchor="w")
        if self.live:
            self.dirty = False
            self.dirty_lbl.config(text="")
            self.dirty_lbl.pack_forget()

    def paint_mode(self, dry):
        self.mode_seg.set("dry" if dry else "live")

    def key_form(self, box):
        for w in box.winfo_children():
            w.destroy()
        self.label(box, self.t("a_new_key"), 12, INK2).pack(anchor="w", pady=(0, P(4)))
        k = self.entry(box, "", 30, show="•", height=34)
        k.pack(anchor="w", fill="x")
        self.label(box, self.t("a_new_secret"), 12, INK2).pack(anchor="w", pady=(P(8), P(4)))
        s = self.entry(box, "", 30, show="•", height=34)
        s.pack(anchor="w", fill="x")
        msg = self.label(box, self.t("a_test_d"), 11, INK3, wraplength=P(330), justify="left")
        row = tk.Frame(box, bg=PANEL)
        row.pack(anchor="w", pady=P(8))

        def test():
            if not k.get().strip() or not s.get().strip():
                return
            msg.config(text=self.t("cn_busy"), fg=self.accent)
            kk, ss = k.get().strip(), s.get().strip()

            def work():
                out = self.live.ask("keys", kk, ss, self.connect, wait=True, timeout=600)
                self.inbox.put(("keys_done", out, msg))
            threading.Thread(target=work, daemon=True).start()

        self.button(row, self.t("a_test"), test, "main", 13, height=34).pack(side="left")
        self.button(row, self.t("cancel"), self.fill_settings, "ghost", 13, height=34).pack(side="left", padx=P(8))
        msg.pack(anchor="w")

    def read_form(self):
        """The form as a webull.toml document, checked by the same parser as the file."""
        cfg = self.live.eng.cfg
        doc = {"app_key": cfg.app_key, "app_secret": cfg.app_secret, "account_id": cfg.account_id, "host": cfg.host,
               "dry_run": cfg.dry_run, "poll_seconds": cfg.poll_seconds,
               "daily_eval_delay_min": cfg.daily_eval_delay_min,
               "symbols": [x.strip() for x in self.form["symbols"].get().replace(" ", ",").split(",") if x.strip()],
               "limits": {}, "inputs": {},
               "window": {"lang": self.form["lang"].get(), "on_close": self.form["on_close"].get()}}
        for k, w in self.form.items():
            if k.startswith("lim:"):
                doc["limits"][k[4:]] = self._number(w.get(), k[4:])
            elif k.startswith("in:"):
                d = self.strategy.INPUTS[k[3:]]
                if isinstance(d, bool):
                    doc["inputs"][k[3:]] = bool(w.get())
                elif isinstance(d, (int, float)):
                    doc["inputs"][k[3:]] = self._number(w.get(), k[3:])
                else:
                    doc["inputs"][k[3:]] = w.get()
        return parse(doc, self.strategy.INPUTS)

    def _choices(self, name):
        """The values an input may take when the strategy lists them (2-8 short texts), else None."""
        ch = (getattr(self.strategy, "CHOICES", None) or {}).get(name)
        if isinstance(ch, (list, tuple)) and 2 <= len(ch) <= 8 and len(set(ch)) == len(ch) \
                and all(isinstance(x, str) and 0 < len(x) <= 12 for x in ch):
            return list(ch)
        return None

    @staticmethod
    def _number(s, name):
        try:
            v = float(s.replace(",", "").strip())
        except ValueError:
            raise ConfigError(f"{name}: {s!r} is not a number") from None
        return int(v) if v == int(v) else v

    def save_settings(self):
        if not self.live:
            return
        try:
            cfg = self.read_form()
        except ConfigError as e:
            self.save_msg.config(text=self.t("bad", e=e), fg=DOWN)
            return
        relang = cfg.lang != self.cfg.lang and cfg.lang
        self.save_msg.config(text="…", fg=INK3)

        def work():
            out = self.live.ask("save", cfg, wait=True, timeout=300)
            self.inbox.put(("saved", out, cfg, relang))
        threading.Thread(target=work, daemon=True).start()

    def ask_mode(self, mode):
        if not self.live:
            return
        cur = self.live.eng.cfg
        if (mode == "dry") == cur.dry_run:
            return
        if mode == "dry":
            self.save_mode(True)
            return
        d = self.dialog(self.t("lv_title"), border=UP, tint="#0F2A20")
        acct = "••••" + self.live.snapshot().get("account", "")
        self.label(d.inner, self.t("lv_body", a=acct), 14, SOFT, wraplength=P(470), justify="left").pack(anchor="w")
        L = cur.limits
        limits = tk.Frame(d.inner, bg=PANEL)
        limits.pack(anchor="w", pady=(P(6), 0))
        for i, (label, v) in enumerate(((self.t("k_used"), L.max_total_notional), (self.t("l_per"), L.max_notional_per_order))):
            if i:
                self.label(limits, "·", 13, INK3).pack(side="left")
            self.label(limits, label, 13, INK3).pack(side="left")
            self.label(limits, money0(v) if v else self.t("k_nocap"), 13, INK, mono=True).pack(side="left", padx=(0, P(8)))
        var = tk.BooleanVar(value=False)
        go = self.button(d.btns, self.t("lv_go"), lambda: (d.close(), self.save_mode(False)), "buy", 15, height=44,
                         padx=22, radius=10)

        def arm():
            go.config(state="normal" if var.get() else "disabled")
        arm()
        self.check(d.inner, self.t("lv_tick"), var, command=arm, size=14, accent=UP, wraplength=P(440)).pack(
            anchor="w", pady=(P(14), P(4)))
        go.pack(side="right")
        self.button(d.btns, self.t("lv_no"), d.close, "ghost", 15, height=44, padx=20, radius=10).pack(
            side="right", padx=(0, P(10)))

    def save_mode(self, dry):
        cfg = copy.copy(self.live.eng.cfg)
        cfg.dry_run = dry

        def work():
            out = self.live.ask("save", cfg, wait=True, timeout=300)
            self.inbox.put(("mode_saved", out, dry))
        threading.Thread(target=work, daemon=True).start()

    # -- dialogs ---------------------------------------------------------------
    def dialog(self, title, border="#2A3448", width=520, tint=None, anchor=None, radius=14, aside=None):
        """A borderless rounded card over a dimmed screen (or, with `anchor`, a dropdown under that widget).
        Put content in `.inner`, buttons in `.btns`; `.close()` takes it down."""
        W = P(width)
        pad = P(22) if anchor is None else P(12)
        scrim = None
        if anchor is None:
            scrim = tk.Toplevel(self.root, bg="#05080E")
            scrim.overrideredirect(True)
            try:
                scrim.attributes("-alpha", 0.72)
            except tk.TclError:
                pass
            scrim.transient(self.root)
        top = tk.Toplevel(self.root, bg=KEY)
        top.withdraw()
        top.overrideredirect(True)
        top.transient(self.root)
        try:
            top.attributes("-transparentcolor", KEY)
            seen = True
        except tk.TclError:
            top.configure(bg=PANEL)
            seen = False
        # what shows through around the card: the dimmed screen (mostly the dark page under the scrim)
        # or, for a dropdown, the page itself. Corner edges blend into it, so they look smooth even
        # though a see-through colour is all-or-nothing per pixel.
        behind = sh.mix(BG, "#05080E", 0.72) if anchor is None else BG
        top.title(title)
        cv = tk.Canvas(top, bg=top["bg"], highlightthickness=0, bd=0)
        cv.pack()
        top.inner = tk.Frame(cv, bg=PANEL)
        top.btns = tk.Frame(cv, bg=PANEL)
        tf = F(self.f(18, "bold"))
        y0 = pad + (tf.metrics("linespace") + P(14) if title and anchor is None else 0)
        iw = cv.create_window(pad, y0, anchor="nw", window=top.inner, width=W - 2 * pad)
        bw = cv.create_window(pad, y0, anchor="nw", window=top.btns, width=W - 2 * pad)
        size = [0, 0]

        def place(H):
            if anchor is not None and anchor.winfo_exists():
                x = anchor.winfo_rootx() + anchor.winfo_width() - W
                y = anchor.winfo_rooty() + anchor.winfo_height() + P(6)
            else:
                x = self.root.winfo_rootx() + (self.root.winfo_width() - W) // 2
                y = self.root.winfo_rooty() + max(P(40), (self.root.winfo_height() - H) // 2 - P(20))
            top.geometry(f"{W}x{H}+{max(0, x)}+{max(0, y)}")
            if scrim is not None and scrim.winfo_exists():
                scrim.geometry(f"{self.root.winfo_width()}x{self.root.winfo_height()}+{self.root.winfo_rootx()}+{self.root.winfo_rooty()}")

        def relayout(e=None):
            if not top.winfo_exists():
                return
            ih = top.inner.winfo_reqheight() if top.inner.winfo_children() else 0
            bh = top.btns.winfo_reqheight() if top.btns.winfo_children() else 0
            H = y0 + ih + (P(16) + bh if bh else 0) + pad
            cv.coords(bw, pad, y0 + ih + (P(16) if ih else 0))
            if size != [W, H]:
                size[:] = [W, H]
                cv.configure(width=W, height=H)
                cv.delete("card")
                r = P(radius)
                band = min(P(96), H - 2)

                def at(x, y):
                    return sh.mix(tint, PANEL, (y - (y % 2) + 1) / band) if tint and y < band else PANEL
                sh.smooth_rect(cv, 0, 0, W, H, r, PANEL, behind, outline=border, fill_at=at, vary="y",
                               tags="card", outside=KEY if seen else None)
                if title and anchor is None:
                    cv.create_text(pad, pad, text=title, anchor="nw", fill=INK, font=tf, tags="card")
                if aside:
                    head, val = aside
                    vf = F(self.mono(15, "bold"))
                    cv.create_text(W - pad, pad + P(4), text=val, anchor="ne", fill=INK, font=vf, tags="card")
                    cv.create_text(W - pad - vf.measure(val) - P(8), pad + P(5), text=head, anchor="ne", fill=INK2,
                                   font=self.f(14), tags="card")
                cv.tag_lower("card")
            place(H)

        def raise_all(e=None):
            if e is not None and e.widget is not self.root:
                return
            relayout()
            for w in (scrim, top):
                if w is not None and w.winfo_exists():
                    w.lift()

        top.inner.bind("<Configure>", relayout)
        top.btns.bind("<Configure>", relayout)
        root_bind = self.root.bind("<Configure>", raise_all, add="+")
        focus_bind = self.root.bind("<FocusIn>", raise_all, add="+")

        def close():
            for seq, fid in (("<Configure>", root_bind), ("<FocusIn>", focus_bind)):
                try:
                    self.root.unbind(seq, fid)
                except tk.TclError:
                    pass
            for w in (top, scrim):
                if w is not None and w.winfo_exists():
                    try:
                        w.grab_release()
                    except tk.TclError:
                        pass
                    w.destroy()
            if top in self._dialogs:
                self._dialogs.remove(top)
        top.close = close
        top.protocol("WM_DELETE_WINDOW", close)
        top.bind("<Escape>", lambda e: close())
        if anchor is not None:
            def outside(e):
                if not (0 <= e.x_root - top.winfo_rootx() < top.winfo_width()
                        and 0 <= e.y_root - top.winfo_rooty() < top.winfo_height()):
                    close()
            top.bind("<Button-1>", outside)
        self._dialogs.append(top)
        top.after_idle(relayout)
        def arrive():
            if not top.winfo_exists():
                return
            relayout()
            top.deiconify()
            if scrim is not None and scrim.winfo_exists():
                scrim.lift()
            top.lift()
            top.grab_set()
            top.focus_force()
        top.after(60, arrive)
        return top

    def menu_item(self, parent, title, desc, cmd, color=INK):
        """A row of a dropdown menu: bold title, muted description, highlighted on hover."""
        box = sh.Box(parent, PANEL, "", radius=P(8), inset=P(2))
        box.pack(fill="x", pady=P(2))
        b = box.inner
        b.config(cursor="hand2")
        h = self.label(b, title, 14, color, "bold")
        h.pack(anchor="w", padx=P(10), pady=(P(8), P(1)))
        dd = self.label(b, desc, 12, INK3, wraplength=P(340), justify="left")
        dd.pack(anchor="w", padx=P(10), pady=(0, P(8)))

        def hover(on):
            bg = "#1E293B" if on else PANEL
            box.set(fill=bg)
            for w in (h, dd):
                w.config(bg=bg)
        for w in (b, h, dd):
            w.bind("<Button-1>", lambda e: cmd())
            w.bind("<Enter>", lambda e: hover(True))
            w.bind("<Leave>", lambda e: hover(False))
        return box

    def on_run_button(self):
        snap = self.live.snapshot() if self.live else {}
        if snap.get("run", "on") != "on":
            self.live.ask("run", "on")
            return
        d = self.dialog(self.t("stop"), width=380, anchor=self.run_btn, radius=12)
        self.menu_item(d.inner, self.t("stop_new"), self.t("stop_new_d"),
                       lambda: (d.close(), self.live.ask("run", "paused")))
        self.menu_item(d.inner, self.t("stop_all"), self.t("stop_all_d"), lambda: self.confirm_stop_all(d), DOWN2)

    def confirm_stop_all(self, d):
        for w in list(d.inner.winfo_children()) + list(d.btns.winfo_children()):
            w.destroy()
        snap = self.live.snapshot()
        self.label(d.inner, self.t("stop_all_q", n=len(snap.get("positions", []))), 14, INK, wraplength=P(340),
                   justify="left").pack(anchor="w", padx=P(10), pady=(P(6), 0))
        if snap.get("market", ("",))[0] != "open":
            self.label(d.inner, self.t("stop_all_closed"), 12, INK3, wraplength=P(340), justify="left").pack(
                anchor="w", padx=P(10), pady=(P(2), 0))
        self.button(d.btns, self.t("stop_all_go"), lambda: (d.close(), self.live.ask("run", "off")), "sell", 13,
                    height=36).pack(side="left", padx=(P(10), P(8)))
        self.button(d.btns, self.t("cancel"), d.close, "ghost", 13, height=36, padx=12).pack(side="left")

    def ask_buy(self, symbol):
        if not self.live:
            return
        snap = self.live.snapshot()
        px = (snap.get("quotes", {}).get(symbol) or (None, None))[0]
        d = self.dialog(self.t("b_title", s=symbol), border=UP, width=560, tint="#0F2A20",
                        aside=(self.t("b_last", p="").strip(), num(px)))
        grid = tk.Frame(d.inner, bg=PANEL)
        grid.pack(fill="x")
        grid.grid_columnconfigure(0, weight=1, uniform="b")
        grid.grid_columnconfigure(1, weight=1, uniform="b")
        qty = self.field(grid, self.t("b_qty"), "1", 0, 0, size=16, height=44, label_color=INK3)
        tf = tk.Frame(grid, bg=PANEL)
        tf.grid(row=0, column=1, sticky="ew", pady=(P(4), P(4)))
        self.label(tf, self.t("b_type"), 12, INK3).pack(anchor="w", pady=(0, P(5)))
        kind = tk.StringVar(value="market")
        seg = self.segmented(tf, [("market", self.t("b_market")), ("limit", self.t("b_limit"))], "market",
                             lambda v: (kind.set(v), seg.set(v)), height=36, padx=18)
        seg.pack(anchor="w")
        lim = self.field(grid, self.t("b_limit_p"), num(px) if px else "", 1, 1, size=16, height=44, label_color=INK3)
        stop = self.field(grid, self.t("b_stop"), num(px * 0.95) if px else "", 2, 0, size=16, height=44,
                          label_color="#FDA4AF", border="#5B2333")
        target = self.field(grid, self.t("b_target"), "", 2, 1, size=16, height=44, label_color="#86EFAC", border="#1E4D33")
        cost = self.label(d.inner, "", 13, INK3, mono=True)
        cost.pack(anchor="w", pady=(P(10), 0))
        self.label(d.inner, self.t("b_note"), 13, INK3, wraplength=P(500), justify="left").pack(anchor="w", pady=(P(4), 0))
        msg = self.label(d.inner, "", 13, DOWN2, wraplength=P(500), justify="left")
        msg.pack(anchor="w")

        def values():
            q = int(float(qty.get()))
            lp = float(lim.get()) if kind.get() == "limit" else None
            sp = float(stop.get()) if stop.get().strip() else None
            tp = float(target.get()) if target.get().strip() else None
            return q, sp, tp, lp

        def update(*_):
            try:
                q, _, _, lp = values()
                cost.config(text=self.t("b_cost", c=money(q * (lp or px or 0))))
            except ValueError:
                cost.config(text="")
            lim.config(state="normal" if kind.get() == "limit" else "disabled")
        for w in (qty, lim):
            w.bind("<KeyRelease>", update)
        kind.trace_add("write", update)
        update()

        def send():
            try:
                q, sp, tp, lp = values()
            except ValueError:
                msg.config(text=self.t("b_bad", e="?"))
                return
            go.config(state="disabled")

            def work():
                out = self.live.ask("buy", symbol, q, sp, tp, lp, wait=True, timeout=120)
                self.inbox.put(("bought", out, d, msg, go))
            threading.Thread(target=work, daemon=True).start()

        go = self.button(d.btns, self.t("b_send"), send, "buy", 15, height=44, padx=22, radius=10)
        go.pack(side="right")
        self.button(d.btns, self.t("cancel"), d.close, "ghost", 15, height=44, padx=20, radius=10).pack(
            side="right", padx=(0, P(10)))
        d.after(120, qty.focus_set)

    def ask_sell(self):
        sel = self.tree_open.selection()
        if not sel or not self.live:
            return
        sym = sel[0]
        p = next((x for x in self.live.snapshot().get("positions", []) if x["symbol"] == sym), None)
        if not p:
            return
        d = self.dialog(self.t("sell_go") + " " + sym, border=DOWN, tint="#2A1118")
        self.label(d.inner, self.t("sell_q", s=sym, q=p["qty"]), 14, INK, wraplength=P(470), justify="left").pack(anchor="w")
        self.button(d.btns, self.t("sell_go"), lambda: (d.close(), self.live.ask("sell", sym)), "sell", 15, height=44,
                    padx=22, radius=10).pack(side="right")
        self.button(d.btns, self.t("cancel"), d.close, "ghost", 15, height=44, padx=20, radius=10).pack(
            side="right", padx=(0, P(10)))

    def press(self, button):
        if self.live:
            self.live.ask("press", button)

    # -- closing ---------------------------------------------------------------
    def on_close(self):
        if not self.live:
            self.quit()
            return
        choice = self.live.eng.cfg.on_close
        if choice == "tray":
            self.to_tray()
            return
        if choice == "quit":
            self.quit()
            return
        d = self.dialog(self.t("cl_title"))
        keep = tk.BooleanVar(value=False)
        for key, desc, act in (("cl_tray", "cl_tray_d", "tray"), ("cl_quit", "cl_quit_d", "quit")):
            tray = act == "tray"
            bg = sh.mix(PANEL, self.accent, 0.08) if tray else PANEL
            box = sh.Box(d.inner, bg, self.accent if tray else GHOST_LINE, radius=P(10), inset=P(3))
            box.pack(fill="x", pady=(0, P(10)))
            b = box.inner
            b.config(cursor="hand2")
            h = self.label(b, self.t(key), 15, INK, "bold")
            h.pack(anchor="w", padx=P(12), pady=(P(10), P(1)))
            dd = self.label(b, self.t(desc), 13, INK2, wraplength=P(440), justify="left")
            dd.pack(anchor="w", padx=P(12), pady=(0, P(10)))

            def hover(on, box=box, bg=bg, ws=(h, dd)):
                c = sh._lighter(bg, 0.05) if on else bg
                box.set(fill=c)
                for w in ws:
                    w.config(bg=c)
            for w in (b, h, dd):
                w.bind("<Button-1>", lambda e, a=act: self.closing(d, a, keep.get()))
                w.bind("<Enter>", lambda e, hv=hover: hv(True))
                w.bind("<Leave>", lambda e, hv=hover: hv(False))
        self.check(d.inner, self.t("cl_keep"), keep, size=13, color=INK2).pack(anchor="w", pady=(0, P(2)))
        self.button(d.btns, self.t("cancel"), d.close, "ghost", 14, height=40, padx=18, radius=9).pack(side="right")

    def closing(self, d, act, remember):
        d.close()
        if remember:
            cfg = copy.copy(self.live.eng.cfg)
            cfg.on_close = act
            self.live.ask("save", cfg, wait=True, timeout=60)
        self.to_tray() if act == "tray" else self.quit()

    def to_tray(self):
        if Tray.available():
            self.tray = Tray(self.t("tray_tip", n=self.name), lambda: self.inbox.put(("restore",)))
            if self.tray.show((self.name, self.t("tray_tip", n=self.name))):
                self.root.withdraw()
                return
        self.root.iconify()

    def restore(self):
        if self.tray:
            self.tray.hide()
            self.tray = None
        self.root.deiconify()
        self.root.lift()

    def quit(self):
        if self.tray:
            self.tray.hide()
        if self.live:
            self.live.stop()
        self.root.destroy()

    # -- once a second ---------------------------------------------------------
    def tick(self):
        try:
            while True:
                self.handle(*self.inbox.get_nowait())
        except queue.Empty:
            pass
        if self.live:
            snap = self.live.snapshot()
            if snap and snap.get("at") is not self.snap_at:
                self.snap_at = snap.get("at")
                try:
                    self.paint(snap)
                except tk.TclError:
                    pass
            if snap:
                try:
                    self.clock.config(text=datetime.now(clock.ET).strftime("%H:%M:%S") + " ET")
                    self._pulse = not self._pulse
                    if snap.get("run") == "on":
                        self.run_pill.config(dot=self.accent if self._pulse else sh.mix(TOPBG, self.accent, 0.45))
                except tk.TclError:
                    pass
        self.root.after(1000, self.tick)

    def handle(self, kind, *a):
        if kind == "connected":
            self.connected(*a)
        elif kind == "connect_failed":
            self.connect_screen(error=a[0])
        elif kind == "restore":
            self.restore()
        elif kind == "keys_done":
            out, msg = a
            if out:
                msg.config(text=self.t("cn_bad", e=out), fg=DOWN2)
            else:
                self.fill_settings()
        elif kind == "saved":
            out, cfg, relang = a
            if out:
                self.save_msg.config(text=self.t("bad", e=out), fg=DOWN2)
                return
            self.dirty = False
            self.dirty_lbl.config(text="")
            self.dirty_lbl.pack_forget()
            self.save_msg.config(text=self.t("saved"), fg=ACCENT2)
            self.cfg = cfg
            if relang and relang != self.lang:
                self.lang = relang
                self.pick_fonts()
                self.main_screen()
            self.watch_rows = {}
        elif kind == "mode_saved":
            out, dry = a
            if not out:
                self.paint_mode(dry)
        elif kind == "bought":
            out, d, msg, go = a
            if out:
                try:
                    msg.config(text=self.t("b_bad", e=out))
                    go.config(state="normal")
                except tk.TclError:
                    pass
            else:
                d.close()

    def paint(self, s):
        cfg = s["cfg"]
        sub = [self.version, "Webull Thailand ••••" + s["account"], self.ui.get("title", "")]
        self.sub.config(text=" · ".join(x for x in sub if x))
        run = s["run"]
        pill = {"on": (self.t("running"), self.accent, ACCENT2), "paused": (self.t("paused"), AMBER, "#FCD34D"),
                "off": (self.t("off"), AMBER, "#FCD34D")}[run]
        self.run_pill.config(text=pill[0], bg=sh.mix(TOPBG, pill[1], 0.1), outline=sh.mix(TOPBG, pill[1], 0.35),
                             fg=pill[2], dot=pill[1])
        mp = (self.t("dry"), VIOLET, "#DDD6FE", 0.18, 0.5) if s["dry"] else (self.t("live"), UP, "#86EFAC", 0.14, 0.45)
        self.mode_pill.config(text=mp[0], bg=sh.mix(TOPBG, mp[1], mp[3]), outline=sh.mix(TOPBG, mp[1], mp[4]), fg=mp[2])
        m = s["market"]
        self.mkt.config(text=self.t("mkt_" + m[0], t=m[1]), dot=UP if m[0] == "open" else "#64748B")
        if run == "on":
            self.run_btn.config(text=self.t("stop"), bg="#161D2B", fg=INK, outline=GHOST_LINE, gradient=None,
                                icon="pause", tail="▾")
        else:
            self.run_btn.config(text=self.t("start"), bg=self.accent, fg="#06101A", outline="",
                                gradient=(self.accent, "#3B82F6"), icon="play", tail="")
        self.run_btn.font.configure(weight="normal" if run == "on" else "bold")
        self.run_btn._size()
        shown = [x for x in ((s["halted"], "n_stopfile", AMBER, "#FCD34D", "#FDE68A"),
                             (run == "paused", "n_paused", AMBER, "#FCD34D", "#FDE68A"),
                             (run == "off", "n_off", AMBER, "#FCD34D", "#FDE68A"),
                             (s["dry"], "n_dry", VIOLET, "#C4B5FD", "#DDD6FE")) if x[0]]
        if [x[1] for x in shown] != getattr(self, "_notices", None):
            self._notices = [x[1] for x in shown]
            for w in self.notices.winfo_children():
                w.destroy()
            for _, key, hue, head, body in shown:
                bg = sh.mix(BG, hue, 0.09)
                box = sh.Box(self.notices, bg, sh.mix(BG, hue, 0.4), radius=P(10), inset=P(4))
                box.pack(fill="x", pady=(P(10), 0))
                row = tk.Frame(box.inner, bg=bg)
                row.pack(fill="x", padx=P(10), pady=P(5))
                words = self.t(key)
                first, sep, rest = words.partition(" · ")
                self.label(row, first, 14, head, "bold").pack(side="left")
                if rest:
                    self.label(row, " · " + rest, 14, body, anchor="w", justify="left", padx=P(8),
                               wraplength=max(P(400), self.root.winfo_width() - P(360))).pack(side="left", fill="x")
        self.paint_kpis(s, cfg)
        self.paint_watch(s)
        self.paint_chart_head(s)
        self.draw_chart()
        self.paint_tables(s)
        self.paint_log(s)

    def paint_kpis(self, s, cfg):
        usd = s["usd"]
        eq = [v for _, v in s["equity_days"][-30:]]
        pct = (eq[-1] / eq[0] - 1) * 100 if len(eq) >= 2 and eq[0] else None
        self.set_kpi("kpi_equity", title=self.t("k_equity"), big=money(usd[0] + usd[1]) if usd else "—",
                     small=self.t("k_cash", c=money0(usd[0]), s=money0(usd[1])) if usd else "",
                     eq=eq, pct=pct, label30=self.t("k_30d"))
        tot = s["realized_today"] + s["unrealized"]
        days = [v for _, v in s["daily"]]
        self.set_kpi("kpi_today", title=self.t("k_today"), big=money(tot, True),
                     small=self.t("k_today_d", c=money0(s["realized_today"]) if s["realized_today"] < 0 else "+" + money0(s["realized_today"]),
                                  o=money0(s["unrealized"]) if s["unrealized"] < 0 else "+" + money0(s["unrealized"])),
                     big_fg=tone(tot), days=days, label=self.t("k_days", n=len(days)) if days else "")
        pnl = [p for (p,) in s["trades30"]]
        won = [p for p in pnl if p > 0]
        lost = [-p for p in pnl if p < 0]
        pf = (sum(won) / sum(lost)) if lost else None
        share = len(won) / len(pnl) if pnl else None
        self.set_kpi("kpi_stats", title=self.t("k_stats"), share=share, ring=f"{share * 100:.0f}%" if pnl else "—",
                     line=self.t("k_stats_d", w=len(won), n=len(pnl), pf=num(pf) if pf else ("∞" if won else "—")),
                     total_label=self.t("k_total", v="").strip(), total=money(sum(pnl), True), total_v=sum(pnl))
        cap = cfg.limits.max_total_notional
        used = s["in_use"]
        self.set_kpi("kpi_used", title=self.t("k_used"), big=money0(used),
                     cap=("/ " + money0(cap)) if cap else self.t("k_nocap"),
                     held=self.t("k_held", n=len(s["positions"]), m=cfg.limits.max_open_positions),
                     frac=(used / cap) if cap and used > 0 else 0)

    def spark(self, cv, vals, color, x1, y1, x2, y2, width=2, fill=True, tags="fg"):
        if len(vals) < 2:
            return
        lo, hi = min(vals), max(vals)
        span = (hi - lo) or 1
        pts = []
        for i, v in enumerate(vals):
            pts.append((x1 + i * (x2 - x1) / (len(vals) - 1), y2 - (v - lo) / span * (y2 - y1)))
        if fill:
            self.fill_under(cv, pts, y2, color, PANEL, tags=tags)
        sh.smooth_line(cv, pts, width, color, tags=tags)

    def fill_under(self, cv, pts, base, color, bg, step=2, strength=0.4, tags="fg"):
        """The fading fill under a line, from the line's colour at its highest point to the panel at
        `base`. Drawn as horizontal bands, each one polygon clipped to the line - a few dozen canvas
        items instead of thousands of 1px columns, which Tk had to repaint one by one every time the
        chart came back on screen (the judder when switching views)."""
        if len(pts) < 2:
            return
        x0, xn = pts[0][0], pts[-1][0]
        top = min(p[1] for p in pts)
        span = max(1.0, base - top)
        n = max(8, min(40, int(span / P(4))))
        for k in range(n):
            ya = top + span * k / n
            yb = top + span * (k + 1) / n
            t = strength * (1 - k / n)
            poly = []
            for i, (x, y) in enumerate(pts):
                if i:                       # where the line crosses this band's edges, add the crossing
                    px_, py_ = pts[i - 1]
                    cross = [(px_ + (x - px_) * (edge - py_) / (y - py_), edge) for edge in (ya, yb)
                             if (py_ - edge) * (y - edge) < 0]
                    poly += sorted(cross)
                poly.append((x, min(max(y, ya), yb)))
            poly += [(xn, yb), (x0, yb)]
            cv.create_polygon([c for p in poly for c in p], fill=sh.mix(bg, color, max(0.0, t)), outline="",
                              tags=tags)

    def bars(self, cv, vals, x1, y1, x2, y2):
        if not vals:
            return
        mx = max(abs(v) for v in vals) or 1
        gap = P(3)
        bw = min(P(14), ((x2 - x1) - gap * (len(vals) - 1)) / len(vals))
        x0 = x2 - len(vals) * (bw + gap) + gap
        for i, v in enumerate(vals):
            bh = max(P(4), abs(v) / mx * (y2 - y1))
            x = x0 + i * (bw + gap)
            sh.round_rect(cv, x, y2 - bh, x + bw, y2 + P(2), P(2), fill=UP if v >= 0 else DOWN, outline="", tags="fg")

    def paint_watch(self, s):
        if not self.watch_list:
            return
        held = {p["symbol"] for p in s["positions"]}
        syms = list(s["cfg"].symbols)
        if list(self.watch_rows) != syms:
            for w in self.watch_list.winfo_children():
                w.destroy()
            self.watch_rows = {}
            for sym in syms:
                tk.Frame(self.watch_list, bg=ROWLINE, height=1).pack(fill="x")
                row = tk.Frame(self.watch_list, bg=PANEL, cursor="hand2")
                row.pack(fill="x")
                bar = tk.Frame(row, bg=PANEL, width=P(3))
                bar.pack(side="left", fill="y")
                body = tk.Frame(row, bg=PANEL)
                body.pack(side="left", fill="both", expand=True, padx=(P(9), P(12)), pady=P(4))
                left = tk.Frame(body, bg=PANEL, width=P(64))
                left.pack(side="left")
                name = self.label(left, sym, 14, INK, "bold")
                name.pack(anchor="w")
                state = self.label(left, "", 11, INK3)
                state.pack(anchor="w")
                spark = tk.Canvas(body, bg=PANEL, highlightthickness=0, bd=0, width=P(80), height=P(28))
                spark.pack(side="left", padx=(P(10), 0))
                extra = self.label(body, "", 11, INK3)
                extra.pack(side="left", padx=P(8))
                buy = self.button(body, self.t("buy"), lambda x=sym: self.ask_buy(x), "buy", 13, height=34, padx=13)
                buy.pack(side="right")
                right = tk.Frame(body, bg=PANEL)
                right.pack(side="right", padx=(0, P(10)))
                px = self.label(right, "", 14, INK, mono=True)
                px.pack(anchor="e")
                chg = sh.Button(right, "", None, bg=PANEL, fg=INK3, padx=P(6), height=P(18), radius=P(4),
                                font=self.mono(11), cursor="arrow")
                chg.pack(anchor="e", pady=(P(2), 0))
                for w in (row, body, left, name, state, spark, right, px, extra):
                    w.bind("<Button-1>", lambda e, x=sym: self.live.ask("select", x))
                self.watch_rows[sym] = dict(row=row, bar=bar, body=body, left=left, name=name, state=state, spark=spark,
                                            px=px, chg=chg, extra=extra, buy=buy, right=right, drawn=None)
        for sym, r in self.watch_rows.items():
            q = s["quotes"].get(sym) or (None, None)
            sel = sym == s["selected"]
            bg = "#16203A" if sel else PANEL
            for k in ("row", "body", "left", "name", "state", "px", "extra", "right", "spark"):
                r[k].config(bg=bg)
            r["bar"].config(bg=self.accent if sel else bg)
            r["buy"].config(background=bg)
            r["state"].config(text=self.t("w_held") if sym in held else self.t("w_wait"))
            r["px"].config(text=num(q[0]))
            up = q[1] is not None and q[1] >= 0
            r["chg"].config(text=("" if q[1] is None else f"{q[1]:+.2f}%"), background=bg,
                            fg=UP2 if up else DOWN2 if q[1] is not None else INK3,
                            bg=sh.mix(bg, UP if up else DOWN, 0.15) if q[1] is not None else bg)
            vals = s["values"].get(sym) or {}
            r["extra"].config(text="  ".join(f"{k} {v:g}" if isinstance(v, (int, float)) else f"{k} {v}"
                                             for k, v in vals.items()))
            r["buy"].config(state="disabled" if s["run"] == "off" else "normal")
            line = s.get("sparks", {}).get(sym) or []
            key = (tuple(line[-30:]), bg, up if q[1] is not None else None)
            if r["drawn"] != key:
                r["drawn"] = key
                cv = r["spark"]
                cv.delete("all")
                color = UP if (q[1] is None and len(line) > 1 and line[-1] >= line[0]) or (q[1] is not None and up) else DOWN
                self.spark(cv, line[-30:], color, P(2), P(4), P(78), P(24), width=max(1.5, P(1.6)), fill=False, tags="")

    def paint_chart_head(self, s):
        sym = s["selected"]
        q = s["quotes"].get(sym) or (None, None)
        self.chart_title.config(text=sym or "—")
        self.chart_px.config(text=num(q[0]))
        self.chart_chg.config(text="" if q[1] is None else f"{q[1]:+.2f}%", fg=tone2(q[1]))
        p = next((x for x in s["positions"] if x["symbol"] == sym), None)
        cv = self.legend
        cv.delete("all")
        items = []
        if p:
            items = [(UP, (5, 3), f"{self.t('c_target')} {num(p['target'])}"), ("#94A3B8", (2, 3), f"{self.t('c_buy')} {num(p['entry'])}"),
                     (DOWN, (5, 3), f"{self.t('c_stop')} {num(p['stop'])}")]
        f12 = F(self.f(12))
        total = sum(P(18) + P(6) + f12.measure(t) + P(14) for _, _, t in items) + P(6)
        cv.configure(width=total)
        x = 0
        cy = P(13)
        for color, dash, t in items:
            if dash[0] > 2:
                for seg in (0, P(10)):
                    cv.create_line(x + seg, cy, x + seg + P(7), cy, fill=color, width=2)
            else:
                for seg in (0, P(6), P(12)):
                    cv.create_oval(x + seg, cy - 1, x + seg + 2, cy + 1, fill=color, outline=color)
            x += P(18) + P(6)
            cv.create_text(x, cy, text=t, anchor="w", fill=INK2, font=self.f(12))
            x += f12.measure(t) + P(14)
        self._chart = (s["chart"], p, s.get("plots", []), s.get("marks", []), s.get("chart_tf"),
                       s.get("has_plot", False), s.get("signals", []))

    def draw_chart(self):
        if not hasattr(self, "_chart") or not self.chart.winfo_exists():
            return
        cv = self.chart
        bars, p, plots, marks, tf, has_plot, signals = self._chart
        w, h = cv.winfo_width(), cv.winfo_height()
        key = (repr(self._chart), w, h, self.lang, self.chart_ind, self.chart_marks)
        if getattr(self, "_chart_drawn", None) == key:  # the smooth lines are images: only redraw on change
            return
        self._chart_drawn = key
        cv.delete("all")
        if w < 50 or h < 50:
            return
        if len(bars) < 2:
            cv.create_text(w / 2, h / 2, text=self.t("no_bars"), fill=INK3, font=self.f(13))
            return
        n = len(bars)
        X = lambda i: i * w / (n - 1)
        vals = [c for _, c in bars]
        lines = plots if self.chart_ind else []
        on_price = [l for l in lines if l["pane"] == "price"]
        groups = []                                      # the panes under the price, one per PLOT item
        for l in lines:
            if l["pane"] == "lower":
                if not groups or groups[-1][0] != l["group"]:
                    groups.append((l["group"], []))
                groups[-1][1].append(l)
        axis = P(24)
        pane_h = max(P(56), (h - axis) * 0.22) if groups else 0
        if (h - axis) - pane_h * len(groups) < P(90):    # too short: the price comes first
            groups, pane_h = [], 0
        base = h - axis - pane_h * len(groups)
        lo, hi = min(vals), max(vals)
        for l in on_price:
            got = [v for v in l["values"] if v is not None]
            if got:
                lo, hi = min(lo, min(got)), max(hi, max(got))
        if p:
            lo = min([lo] + [x for x in (p["stop"], p["entry"]) if x])
            hi = max([hi] + [x for x in (p["target"], p["entry"]) if x])
        pad = (hi - lo) * 0.06 or 1
        lo, hi = lo - pad, hi + pad
        top = P(8)
        y = lambda v: top + (hi - v) / (hi - lo) * (base - top)
        for i in range(1, 4):
            cv.create_line(0, top + i * (base - top) / 4, w, top + i * (base - top) / 4, fill="#1C2435")
        if self.chart_ind:
            self.draw_signals(cv, signals, X, top, base, w)
        color = UP if vals[-1] >= vals[0] else DOWN
        pts = [(X(i), y(v)) for i, v in enumerate(vals)]
        self.fill_under(cv, pts, base, color, PANEL, step=3, strength=0.35 if not on_price else 0.2, tags="")
        sh.smooth_line(cv, pts, max(2, P(2.2)), color)
        colors = {}
        for l in lines:
            colors.setdefault(l["group"], LINE_COLORS[len(colors) % len(LINE_COLORS)])
        for l in on_price:
            self.plot_line(cv, l["values"], X, y, colors[l["group"]], thin=l["label"].endswith((" +", " -")))
        if p:
            for v, c, dash in ((p["target"], UP, (6, 5)), (p["entry"], "#94A3B8", (2, 4)), (p["stop"], DOWN, (6, 5))):
                if v:
                    cv.create_line(0, y(v), w, y(v), fill=c, dash=dash, width=1)
        # the names of the lines on the price, top left, with their last value
        x = P(4)
        if self.chart_ind and not has_plot:
            cv.create_text(x, P(10), text=self.t("c_no_ind"), anchor="w", fill=INK4, font=self.f(11))
        for l in on_price:
            if l["label"].endswith((" +", " -")):
                continue
            last = next((v for v in reversed(l["values"]) if v is not None), None)
            t = l["label"] + ("" if last is None else f" {num(last)}")
            item = cv.create_text(x, P(10), text=t, anchor="w", fill=colors[l["group"]], font=self.mono(11))
            x1, y1, x2, y2 = cv.bbox(item)
            back = cv.create_rectangle(x1 - P(3), y1 - 1, x2 + P(3), y2 + 1, fill=PANEL, outline="")
            cv.tag_lower(back, item)
            x += F(self.mono(11)).measure(t) + P(14)
        if self.chart_marks:
            self.draw_marks(cv, marks, X, y, w)
        # panes under the price
        for k, (g, ls) in enumerate(groups):
            y1 = base + k * pane_h + P(6)
            y2 = base + (k + 1) * pane_h - P(2)
            cv.create_line(0, y1 - P(3), w, y1 - P(3), fill=LINE)
            got = [v for l in ls for v in l["values"] if v is not None] + list(ls[0]["levels"])
            if ls[0]["kind"] == "rsi":
                plo, phi = 0.0, 100.0
            elif got:
                plo, phi = min(got), max(got)
                pp = (phi - plo) * 0.08 or 1
                plo, phi = plo - pp, phi + pp
            else:
                continue
            yy = lambda v, a=y1 + P(14), b=y2: a + (phi - v) / (phi - plo) * (b - a)
            for lv in ls[0]["levels"]:
                cv.create_line(0, yy(lv), w, yy(lv), fill="#2A3448", dash=(3, 4))
                cv.create_text(w - P(2), yy(lv), text=num(lv), anchor="se", fill=INK4, font=self.mono(10))
            for j, l in enumerate(ls):
                c = colors[g] if j == 0 else INK3
                self.plot_line(cv, l["values"], X, yy, c, thin=j > 0)
            last = next((v for v in reversed(ls[0]["values"]) if v is not None), None)
            cv.create_text(P(4), y1 + P(4), text=ls[0]["label"] + ("" if last is None else f" {num(last)}"),
                           anchor="w", fill=colors[g], font=self.mono(11))
        # the time under it all
        times = [datetime.fromisoformat(t).astimezone(clock.ET) for t, _ in bars]
        if tf == "1mo":
            fmt = "%m/%Y"
        elif tf == "1w":
            fmt = "%d/%m/%y"
        elif tf in DAILY_UP:
            fmt = "%d/%m"
        else:
            fmt = "%H:%M" if times[0].date() == times[-1].date() else "%d/%m %H:%M"
        for frac in (0, 0.25, 0.5, 0.75, 1):
            i = int(round((n - 1) * frac))
            anchor = "w" if frac == 0 else "e" if frac == 1 else "center"
            cv.create_text(X(i), h - P(8), text=times[i].strftime(fmt), anchor=anchor, fill=INK4, font=self.mono(11))

    def plot_line(self, cv, values, X, Y, color, thin=False):
        """One indicator line, in pieces where it has values (it starts once the indicator is ready)."""
        run = []
        for i, v in enumerate(list(values) + [None]):
            if v is None:
                if len(run) > 1:
                    sh.smooth_line(cv, run, max(1, P(1.1 if thin else 1.6)), sh.mix(PANEL, color, 0.6) if thin else color)
                run = []
            else:
                run.append((X(i), Y(v)))

    def draw_marks(self, cv, marks, X, Y, w):
        """Arrows where the program bought (under the price, pointing up) and sold (over it, pointing
        down), a dotted line between the two ends of a closed trade, and a note on hover."""
        s = P(7)
        for m in marks:
            if m["side"] == "sell" and m.get("from") is not None:
                c = UP if (m.get("pnl") or 0) > 0 else DOWN if (m.get("pnl") or 0) < 0 else INK3
                cv.create_line(X(m["from"]), Y(m["from_px"]), X(m["i"]), Y(m["px"]), fill=sh.mix(PANEL, c, 0.7),
                               dash=(2, 3))
        for k, m in enumerate(marks):
            x, yv = X(m["i"]), Y(m["px"])
            tag = f"mark{k}"
            if m["side"] == "buy":
                c = self.accent
                pts = (x, yv + P(3), x - s, yv + P(3) + s * 1.4, x + s, yv + P(3) + s * 1.4)
                note = self.t("c_mark_buy", q=m["qty"], p=num(m["px"]))
            else:
                pnl = m.get("pnl") or 0
                c = UP if pnl > 0 else DOWN if pnl < 0 else INK3
                pts = (x, yv - P(3), x - s, yv - P(3) - s * 1.4, x + s, yv - P(3) - s * 1.4)
                note = self.t("c_mark_sell", q=m["qty"], p=num(m["px"]), pl=money(pnl, True),
                              why=why(self.lang, m.get("reason", "")))
            try:
                when = datetime.fromisoformat(m["t"]).astimezone().strftime("%d/%m %H:%M")
            except (TypeError, ValueError):
                when = ""
            cv.create_polygon(pts, fill=c, outline=PANEL, width=1, tags=(tag,))
            cv.tag_bind(tag, "<Enter>", lambda e, t=f"{note} · {when}", x=x, y=yv: self.chart_tip(t, x, y, w))
            cv.tag_bind(tag, "<Leave>", lambda e: self.chart.delete("tip"))

    def draw_signals(self, cv, signals, X, top, base, w):
        """The program's signals: a faint upright line through the price and a small triangle at the
        top (pointing up for buy, down for sell), with its note on hover. Drawn under the price."""
        s = P(5)
        for k, m in enumerate(signals):
            x = X(m["i"])
            c = UP if m["side"] == "buy" else DOWN
            cv.create_line(x, top + s * 2 + P(4), x, base, fill=sh.mix(PANEL, c, 0.35), dash=(3, 4))
            if m["side"] == "buy":
                pts = (x, top, x - s, top + s * 1.6, x + s, top + s * 1.6)
            else:
                pts = (x - s, top, x + s, top, x, top + s * 1.6)
            tag = f"sig{k}"
            cv.create_polygon(pts, fill=sh.mix(PANEL, c, 0.85), outline="", tags=(tag,))
            try:
                when = datetime.fromisoformat(m["t"]).astimezone().strftime("%d/%m %H:%M")
            except (TypeError, ValueError):
                when = ""
            txt = " · ".join(v for v in (self.t("c_sig_" + m["side"]), m.get("note") or "", when) if v)
            cv.tag_bind(tag, "<Enter>", lambda e, t=txt, x=x: self.chart_tip(t, x, top + s * 2, w))
            cv.tag_bind(tag, "<Leave>", lambda e: self.chart.delete("tip"))

    def chart_tip(self, txt, x, y, w):
        cv = self.chart
        cv.delete("tip")
        f = F(self.f(12))
        tw, th = f.measure(txt) + P(16), f.metrics("linespace") + P(10)
        x1 = min(max(P(2), x - tw / 2), w - tw - P(2))
        y1 = y - th - P(18) if y - th - P(18) > P(2) else y + P(18)
        sh.round_rect(cv, x1, y1, x1 + tw, y1 + th, P(6), fill="#1E293B", outline=LINE, tags="tip")
        cv.create_text(x1 + P(8), y1 + th / 2, text=txt, anchor="w", fill=INK, font=self.f(12), tags="tip")

    def paint_tables(self, s):
        rows = []
        for p in s["positions"]:
            pct = (p["last"] / p["entry"] - 1) * 100 if p["last"] and p["entry"] else None
            c = tone2(p["pl"])
            when = ""
            try:
                when = datetime.fromisoformat(p["opened_at"]).astimezone().strftime("%H:%M")
            except (TypeError, ValueError):
                pass
            you = p["by"] == "you"
            rows.append({"iid": p["symbol"],
                         "sym": ("rich", [(p["symbol"], INK, self.f(14, "bold")), (when, INK3, self.mono(12))]),
                         "qty": ("text", str(p["qty"]), INK, self.mono(14)),
                         "avg": ("text", num(p["entry"]), INK2, self.mono(14)),
                         "last": ("text", num(p["last"]), INK, self.mono(14)),
                         "pl": ("rich", [(money(p["pl"], True), c, self.mono(14, "bold"))]
                                + ([(f"{pct:+.2f}%", c, self.mono(12))] if pct is not None else [])),
                         "bar": ("bar", p["stop"], p["last"], p["target"]),
                         "by": ("pill", self.t("by_you") if you else self.t("by_prog"),
                                sh.mix(PANEL, VIOLET, 0.18) if you else sh.mix(PANEL, self.accent, 0.12),
                                "#C4B5FD" if you else ACCENT2)})
        self.tree_open.set_rows(rows)
        why_style = {"target": (sh.mix(PANEL, UP, 0.14), UP2), "stop": (sh.mix(PANEL, DOWN, 0.14), DOWN2),
                     "sold by you": (sh.mix(PANEL, VIOLET, 0.18), "#C4B5FD"), "stop all": (sh.mix(PANEL, DOWN, 0.14), DOWN2)}
        rows = []
        for i, c in enumerate(s["closed"]):
            when = datetime.fromisoformat(c["closed_at"]).astimezone().strftime("%d/%m %H:%M")
            bg, fg = why_style.get(c["reason"], ("#1E293B", "#CBD5E1"))
            rows.append({"iid": str(i),
                         "when": ("text", when, INK3, self.mono(13)),
                         "sym": ("text", c["symbol"], INK, self.f(14, "bold")),
                         "qty": ("text", str(c["qty"]), INK, self.mono(14)),
                         "buy": ("text", num(c["entry"]), INK2, self.mono(14)),
                         "sell": ("text", num(c["exit"]), INK2, self.mono(14)),
                         "pl": ("text", money(c["pnl"], True), tone2(c["pnl"]), self.mono(14, "bold")),
                         "why": ("pill", why(self.lang, c["reason"]), bg, fg),
                         "by": ("text", self.t("by_you") if c["by"] == "you" else self.t("by_prog"), INK3, self.f(13))})
        self.tree_closed.set_rows(rows)
        self.tab_btn["open"][0].set(count=len(s["positions"]))
        if "closed" in self.tab_btn:
            self.tab_btn["closed"][0].set(count=len(s["closed"]))
        pnl = [p for (p,) in s["trades30"]]
        w, l = sum(1 for p in pnl if p > 0), sum(1 for p in pnl if p < 0)
        cv = self.won_cv
        cv.delete("all")
        f12 = F(self.f(12))
        label = self.t("k_30d")
        tail = self.t("won_lost", w=w, l=l)
        barw = P(100)
        cv.configure(width=f12.measure(label) + P(10) + barw + P(10) + f12.measure(tail) + P(20))
        x, cy = 0, P(10)
        cv.create_text(x, cy, text=label, anchor="w", fill=INK3, font=self.f(12))
        x += f12.measure(label) + P(10)
        sh.round_rect(cv, x, cy - P(3), x + barw, cy + P(4), P(3), fill="#2A2F45", outline="")
        if w + l:
            mid = x + barw * w / (w + l)
            if w:
                sh.round_rect(cv, x, cy - P(3), mid, cy + P(4), P(3), fill=UP, outline="")
            if l:
                sh.round_rect(cv, mid, cy - P(3), x + barw, cy + P(4), P(3), fill=DOWN, outline="")
        x += barw + P(10)
        cv.create_text(x, cy, text=tail, anchor="w", fill=INK3, font=self.f(12))

    def paint_log(self, s):
        if not self.log_txt:
            return
        at_end = self.log_txt.yview()[1] > 0.98
        self.log_txt.config(state="normal")
        self.log_txt.delete("1.0", "end")
        for t, lvl, msg in s["log"]:
            warn = lvl in ("WARNING", "ERROR")
            self.log_txt.insert("end", "● ", "dw" if warn else "d")
            self.log_txt.insert("end", t + "  ", "t")
            self.log_txt.insert("end", msg + "\n", "w" if warn else "m")
        self.log_txt.config(state="disabled")
        if at_end:
            self.log_txt.see("end")


class SymbolChips(tk.Frame):
    """The tickers as chips with a × each, plus a box and a button to add one. `.get()` is the
    comma-separated list the rest of the form expects."""

    def __init__(self, parent, win, symbols):
        super().__init__(parent, bg=parent["bg"], bd=0, highlightthickness=0)
        self.win, self.symbols = win, list(symbols)
        self.chips = tk.Frame(self, bg=parent["bg"])
        self.chips.pack(fill="x", anchor="w")
        self._width = 0
        self.chips.bind("<Configure>", self._reflow)
        row = tk.Frame(self, bg=parent["bg"])
        row.pack(fill="x", pady=(P(8), 0))
        self.box = win.entry(row, "", 12, mono=False, height=36)
        self.box.pack(side="left", fill="x", expand=True)
        self.box.bind("<Return>", lambda e: self.add())
        win.button(row, win.t("s_add"), self.add, "accent", 13, height=36, padx=12).pack(side="left", padx=(P(8), 0))
        self.draw()

    def get(self):
        return ", ".join(self.symbols)

    def _reflow(self, e):
        if abs(e.width - self._width) > 4:
            self._width = e.width
            self.draw()

    def draw(self):
        for w in self.chips.winfo_children():
            w.destroy()
        line = None
        used = 0
        limit = max(P(120), self._width or P(300))
        for s in self.symbols:
            b = sh.Button(self.chips, s, lambda s=s: self.remove(s), bg="#1E293B", fg=INK, padx=P(10), height=P(30),
                          radius=P(15), font=self.win.f(13, "bold"), tail="×", hover="#2A3448")
            w = int(b["width"])
            if line is None or used + w > limit:
                line = tk.Frame(self.chips, bg=self.chips["bg"])
                line.pack(fill="x", anchor="w", pady=(0, P(6)))
                used = 0
            b.pack(in_=line, side="left", padx=(0, P(6)))
            tk.Misc.lift(b)
            used += w + P(6)
        if not self.symbols:
            tk.Frame(self.chips, bg=self.chips["bg"], height=1).pack()

    def remove(self, s):
        self.symbols = [x for x in self.symbols if x != s]
        self.draw()
        self.win.touch()

    def add(self):
        new = [x.strip().upper() for x in self.box.get().replace(" ", ",").split(",") if x.strip()]
        if not new:
            return
        for s in new:
            if s not in self.symbols:
                self.symbols.append(s)
        self.box.delete(0, "end")
        self.draw()
        self.win.touch()


def available():
    """Can this computer show a window?"""
    try:
        r = tk.Tk()
        r.withdraw()
        r.destroy()
        return True
    except tk.TclError:
        return False
