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
"""
import copy
import queue
import threading
import tkinter as tk
from datetime import datetime
from tkinter import ttk

from . import clock
from .config import ConfigError, parse
from .tray import Tray
from .ui import default_lang, text, ui_of, why

BG, PANEL, PANEL2, LINE = "#0A0E16", "#111827", "#0B1019", "#1F2738"
INK, INK2, INK3 = "#E6EAF2", "#AEB7C7", "#8B95A7"
UP, DOWN, AMBER, VIOLET = "#22C55E", "#F43F5E", "#F59E0B", "#8B5CF6"
ACCENT = "#22D3EE"


def dark_title(win):
    """Ask Windows 10/11 for a dark title bar (no effect elsewhere)."""
    if __import__("os").name != "nt":
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


def money(v, sign=False):
    if v is None:
        return "—"
    s = f"${abs(v):,.2f}"
    if sign:
        return ("+" if v >= 0 else "−") + s
    return ("−" if v < 0 else "") + s


def num(v, d=2):
    return "—" if v is None else f"{v:,.{d}f}"


def tone(v):
    return INK if v is None or v == 0 else UP if v > 0 else DOWN


class Window:
    def __init__(self, root, name, version, strategy, cfg, cfg_path, connect, save_cfg, make_live):
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
        self.tab = "open"
        self.dirty = False
        self.form = {}
        self.watch_rows = {}
        family = "Leelawadee UI" if self.lang == "th" else "Segoe UI"
        self.f = lambda size=10, w="normal": (family, size, w)
        self.mono = lambda size=10, w="normal": ("Consolas", size, w)
        root.title(f"{name} — uBotDesign Webull")
        root.configure(bg=BG)
        root.minsize(1180, 720)
        try:
            root.state("zoomed")
        except tk.TclError:
            root.geometry("1440x900")
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
        s.configure("D.Treeview", background=PANEL, fieldbackground=PANEL, foreground=INK, rowheight=30,
                    borderwidth=0, font=self.f(10))
        s.configure("D.Treeview.Heading", background="#0F1420", foreground=INK3, borderwidth=0,
                    font=self.f(9), relief="flat")
        s.map("D.Treeview", background=[("selected", "#16203A")], foreground=[("selected", INK)])
        s.layout("D.Treeview", [("D.Treeview.treearea", {"sticky": "nswe"})])
        s.configure("D.Treeview", bordercolor=PANEL, lightcolor=PANEL, darkcolor=PANEL)
        s.configure("D.Vertical.TScrollbar", background=PANEL, troughcolor=PANEL2, borderwidth=0, arrowcolor=INK3)

    # -- small builders ----------------------------------------------------
    def label(self, parent, txt="", size=10, color=INK, w="normal", mono=False, bg=None, **kw):
        return tk.Label(parent, text=txt, fg=color, bg=bg or parent["bg"],
                        font=(self.mono if mono else self.f)(size, w), **kw)

    def button(self, parent, txt, cmd, kind="ghost", size=10, **kw):
        colors = {"main": (self.accent, "#06101A"), "ghost": ("#161D2B", INK), "buy": ("#16A34A", "#FFFFFF"),
                  "sell": ("#E11D48", "#FFFFFF"), "flat": (parent["bg"], INK2)}[kind]
        b = tk.Button(parent, text=txt, command=cmd, bg=colors[0], fg=colors[1], activebackground=colors[0],
                      activeforeground=colors[1], relief="flat", bd=0, padx=14, pady=6, cursor="hand2",
                      font=self.f(size, "bold" if kind in ("main", "buy", "sell") else "normal"), **kw)
        return b

    def card(self, parent, border=LINE, bg=PANEL):
        return tk.Frame(parent, bg=bg, highlightbackground=border, highlightthickness=1, bd=0)

    def entry(self, parent, value="", width=12, show=None, mono=True):
        e = tk.Entry(parent, bg=PANEL2, fg=INK, insertbackground=INK, relief="flat", width=width,
                     highlightthickness=1, highlightbackground="#2A3448", highlightcolor=self.accent,
                     disabledbackground="#0F141D", disabledforeground="#4B5567",
                     font=(self.mono if mono else self.f)(11), show=show or "")
        e.insert(0, value)
        return e

    def clear(self):
        for w in self.body.winfo_children():
            w.destroy()

    # -- first run: the keys -------------------------------------------------
    def connect_screen(self, auto=False, error=None):
        self.clear()
        wrap = tk.Frame(self.body, bg=BG)
        wrap.place(relx=0.5, rely=0.5, anchor="center")
        left = tk.Frame(wrap, bg=BG)
        left.grid(row=0, column=0, sticky="n", padx=(0, 48))
        self.label(left, f"{self.name} {self.version}".strip(),
                   11, self.accent, "bold").pack(anchor="w")
        self.label(left, self.t("cn_title"), 22, INK, "bold").pack(anchor="w", pady=(4, 6))
        self.label(left, self.t("cn_d"), 11, INK2, wraplength=480, justify="left").pack(anchor="w")
        for i, k in enumerate(("cn_1", "cn_2", "cn_3"), 1):
            row = tk.Frame(left, bg=BG)
            row.pack(anchor="w", pady=(10, 0))
            self.label(row, str(i), 10, self.accent, "bold", mono=True).pack(side="left", padx=(0, 10))
            self.label(row, self.t(k), 10, INK2, wraplength=440, justify="left").pack(side="left")
        self.label(left, self.t("cn_safe"), 9, INK3, wraplength=480, justify="left").pack(anchor="w", pady=(18, 0))
        box = self.card(wrap, "#23304A", "#131C2F")
        box.grid(row=0, column=1, sticky="n")
        inner = tk.Frame(box, bg="#131C2F")
        inner.pack(padx=28, pady=26)
        self.label(inner, "App Key", 10, INK2).pack(anchor="w")
        key = self.entry(inner, self.cfg.app_key, 38, show="•")
        key.pack(fill="x", ipady=7, pady=(4, 12))
        self.label(inner, "App Secret", 10, INK2).pack(anchor="w")
        sec = self.entry(inner, self.cfg.app_secret, 38, show="•")
        sec.pack(fill="x", ipady=7, pady=(4, 14))
        msg = self.label(inner, "", 10, DOWN, wraplength=360, justify="left")
        msg.pack(anchor="w")
        go = self.button(inner, self.t("cn_go"), None, "main", 11)
        go.pack(fill="x", pady=(8, 8), ipady=4)
        self.label(inner, self.t("cn_later"), 9, INK3, wraplength=360, justify="left").pack(anchor="w")
        if error:
            msg.config(text=self.t("cn_bad", e=error), fg=DOWN)

        def attempt():
            k, s = key.get().strip(), sec.get().strip()
            if not k or not s:
                return
            go.config(state="disabled")
            msg.config(text=self.t("cn_busy"), fg=self.accent)
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
        self._connect_widgets = (go, msg)
        if auto:
            attempt()

    def connected(self, cfg, broker):
        if (cfg.app_key, cfg.app_secret) != (self.cfg.app_key, self.cfg.app_secret):
            self.save_cfg(cfg, self.cfg_path)
        self.cfg = cfg
        self.live = self.make_live(cfg, broker)
        self.main_screen()

    # -- the main screen -------------------------------------------------------
    def shown(self, panel):
        return panel not in self.ui.get("hide", [])

    def main_screen(self):
        self.clear()
        b = self.body
        b.grid_columnconfigure(0, weight=1)
        b.grid_columnconfigure(1, weight=0, minsize=380 if self.shown("watch") else 0)
        self.build_top(b)
        self.notices = tk.Frame(b, bg=BG)
        self.notices.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12)
        self.build_kpis(b)
        self.build_center(b)
        self.build_watch(b)
        self.build_bottom(b)
        b.grid_rowconfigure(3, weight=3, minsize=260)
        b.grid_rowconfigure(4, weight=2, minsize=180)
        self.snap_at = None

    def build_top(self, b):
        top = tk.Frame(b, bg="#0F1520", highlightbackground=LINE, highlightthickness=1)
        top.grid(row=0, column=0, columnspan=2, sticky="ew")
        logo = tk.Canvas(top, width=36, height=36, bg="#0F1520", highlightthickness=0)
        logo.create_rectangle(0, 0, 36, 36, fill=self.accent, outline="")
        logo.create_line(7, 25, 15, 17, 20, 22, 29, 12, fill="#0A0E16", width=3)
        logo.pack(side="left", padx=(16, 10), pady=8)
        self.label(top, self.name, 15, INK, "bold", bg="#0F1520").pack(side="left")
        self.sub = self.label(top, "", 10, INK3, bg="#0F1520")
        self.sub.pack(side="left", padx=12)
        self.run_btn = self.button(top, "", self.on_run_button, "ghost", 10)
        self.run_btn.pack(side="right", padx=(8, 16), pady=8)
        self.clock = self.label(top, "", 10, INK3, mono=True, bg="#0F1520")
        self.clock.pack(side="right", padx=8)
        self.mkt = self.label(top, "", 10, INK2, bg="#0F1520")
        self.mkt.pack(side="right", padx=8)
        self.mode_pill = self.label(top, "", 10, INK, bg="#0F1520", padx=10, pady=3)
        self.mode_pill.pack(side="right", padx=4)
        self.run_pill = self.label(top, "", 10, INK, bg="#0F1520", padx=10, pady=3)
        self.run_pill.pack(side="right", padx=4)

    def build_kpis(self, b):
        row = tk.Frame(b, bg=BG)
        row.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(8, 0))
        self.k = {}
        keys = [k for k in ("kpi_equity", "kpi_today", "kpi_stats", "kpi_used") if self.shown(k)]
        tints = {"kpi_equity": "#152038", "kpi_today": "#0F2A20", "kpi_stats": "#1F1838", "kpi_used": "#2A1E10"}
        for i, key in enumerate(keys):
            tint = tints[key]
            row.grid_columnconfigure(i, weight=1, uniform="kpi")
            c = self.card(row, "#23304A", tint)
            c.grid(row=0, column=i, sticky="nsew", padx=(0, 0 if i == len(keys) - 1 else 10))
            title = self.label(c, "", 9, INK3, bg=tint)
            title.pack(anchor="w", padx=12, pady=(8, 0))
            big = self.label(c, "", 18, INK, "bold", mono=True, bg=tint)
            big.pack(anchor="w", padx=12)
            small = self.label(c, "", 9, INK3, bg=tint)
            small.pack(anchor="w", padx=12)
            cv = tk.Canvas(c, height=34, width=60, bg=tint, highlightthickness=0)
            cv.pack(fill="x", padx=12, pady=(2, 8))
            self.k[key] = (title, big, small, cv, tint)

    def build_center(self, b):
        frame = self.card(b)
        frame.grid(row=3, column=0, sticky="nsew", padx=(12, 10), pady=10)
        bar = tk.Frame(frame, bg=PANEL)
        bar.pack(fill="x", padx=12, pady=(8, 4))
        self.seg = {}
        tabs = (("chart", self.t("chart")), ("settings", self.t("settings")))
        if not self.shown("chart"):
            tabs = tabs[1:]
            self.view = "settings"
        for key, label in tabs:
            btn = tk.Button(bar, text=label, relief="flat", bd=0, padx=14, pady=5, cursor="hand2",
                            font=self.f(10, "bold"), command=lambda k=key: self.set_view(k))
            btn.pack(side="left", padx=(0, 4))
            self.seg[key] = btn
        self.dirty_lbl = self.label(bar, "", 9, AMBER)
        self.dirty_lbl.pack(side="left", padx=10)
        self.chart_frame = tk.Frame(frame, bg=PANEL)
        self.chart_head = tk.Frame(self.chart_frame, bg=PANEL)
        self.chart_head.pack(fill="x", padx=12)
        self.chart_title = self.label(self.chart_head, "", 15, INK, "bold")
        self.chart_title.pack(side="left")
        self.chart_px = self.label(self.chart_head, "", 15, INK, "bold", mono=True)
        self.chart_px.pack(side="left", padx=12)
        self.chart_legend = self.label(self.chart_head, "", 9, INK3)
        self.chart_legend.pack(side="right")
        self.chart = tk.Canvas(self.chart_frame, bg=PANEL, highlightthickness=0, height=120)
        self.chart.pack(fill="both", expand=True, padx=12, pady=(4, 10))
        self.chart.bind("<Configure>", lambda e: self.draw_chart())
        self.settings_frame = tk.Frame(frame, bg=PANEL)
        self.build_settings(self.settings_frame)
        self.set_view(self.view)

    def set_view(self, view):
        self.view = view
        for k, btn in self.seg.items():
            on = k == view
            btn.config(bg="#1E293B" if on else PANEL, fg=INK if on else INK3, activebackground="#1E293B")
        self.chart_frame.pack_forget()
        self.settings_frame.pack_forget()
        (self.chart_frame if view == "chart" else self.settings_frame).pack(fill="both", expand=True)

    def build_watch(self, b):
        if not self.shown("watch"):
            self.watch_list = None
            return
        w = self.card(b)
        w.grid(row=3, column=1, sticky="nsew", padx=(0, 12), pady=10)
        head = tk.Frame(w, bg=PANEL)
        head.pack(fill="x", padx=14, pady=(10, 4))
        self.label(head, self.t("watch"), 11, INK, "bold").pack(side="left")
        self.label(head, self.t("watch_tip"), 9, INK3).pack(side="right")
        if self.ui.get("buttons"):
            bb = tk.Frame(w, bg=PANEL)
            bb.pack(fill="x", padx=12, pady=(0, 6))
            for x in self.ui["buttons"]:
                self.button(bb, x["label"], lambda i=x["id"]: self.press(i), "ghost", 9).pack(side="left", padx=(0, 6))
        cv = tk.Canvas(w, bg=PANEL, highlightthickness=0, width=360, height=120)
        sb = ttk.Scrollbar(w, orient="vertical", command=cv.yview, style="D.Vertical.TScrollbar")
        self.watch_list = tk.Frame(cv, bg=PANEL)
        win = cv.create_window(0, 0, window=self.watch_list, anchor="nw")
        self.watch_list.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.bind("<Configure>", lambda e: cv.itemconfigure(win, width=e.width))
        cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        cv.pack(fill="both", expand=True)
        self.watch_rows = {}

    def build_bottom(self, b):
        left = self.card(b)
        left.grid(row=4, column=0, sticky="nsew", padx=(12, 10), pady=(0, 12),
                  columnspan=1 if self.shown("log") else 2)
        bar = tk.Frame(left, bg=PANEL)
        bar.pack(fill="x", padx=10, pady=(6, 0))
        self.tab_btn = {}
        tabs = [("open", "open_tab")] + ([("closed", "closed_tab")] if self.shown("closed") else [])
        for key, label in tabs:
            btn = tk.Button(bar, relief="flat", bd=0, padx=12, pady=6, cursor="hand2", font=self.f(10, "bold"),
                            bg=PANEL, activebackground=PANEL, command=lambda k=key: self.set_tab(k))
            btn.pack(side="left")
            self.tab_btn[key] = (btn, self.t(label))
        self.sell_btn = self.button(bar, self.t("sell_sel"), self.ask_sell, "sell", 9)
        self.sell_btn.pack(side="right")
        self.won_lbl = self.label(bar, "", 9, INK3)
        self.won_lbl.pack(side="right", padx=12)
        holder = tk.Frame(left, bg=PANEL)
        holder.pack(fill="both", expand=True, padx=6, pady=6)
        cols_open = ("sym", "qty", "avg", "last", "pl", "stop", "target", "by")
        self.tree_open = ttk.Treeview(holder, columns=cols_open, show="headings", style="D.Treeview", height=3)
        for c, key, wdt, anchor in (("sym", "h_sym", 70, "w"), ("qty", "h_qty", 60, "e"), ("avg", "h_avg", 75, "e"),
                                     ("last", "h_last", 75, "e"), ("pl", "h_pl", 140, "e"),
                                     ("stop", "h_stop", 100, "e"), ("target", "h_target", 120, "e"),
                                     ("by", "h_by", 70, "center")):
            self.tree_open.heading(c, text=self.t(key), anchor=anchor)
            self.tree_open.column(c, width=wdt, minwidth=40, anchor=anchor, stretch=True)
        cols_closed = ("when", "sym", "qty", "buy", "sell", "pl", "why", "by")
        self.tree_closed = ttk.Treeview(holder, columns=cols_closed, show="headings", style="D.Treeview", height=3)
        for c, key, wdt, anchor in (("when", "h_when", 100, "w"), ("sym", "h_sym", 65, "w"), ("qty", "h_qty", 60, "e"),
                                     ("buy", "h_avg", 75, "e"), ("sell", "h_sell", 75, "e"), ("pl", "h_pl", 100, "e"),
                                     ("why", "h_why", 120, "w"), ("by", "h_by", 70, "center")):
            self.tree_closed.heading(c, text=self.t(key), anchor=anchor)
            self.tree_closed.column(c, width=wdt, minwidth=40, anchor=anchor, stretch=True)
        self.set_tab("open")
        if self.shown("log"):
            right = self.card(b)
            right.grid(row=4, column=1, sticky="nsew", padx=(0, 12), pady=(0, 12))
            self.label(right, self.t("log"), 11, INK, "bold").pack(anchor="w", padx=14, pady=(10, 4))
            self.log_txt = tk.Text(right, bg=PANEL, fg=INK2, relief="flat", wrap="word", height=4, bd=0,
                                   font=self.f(9), highlightthickness=0)
            self.log_txt.pack(fill="both", expand=True, padx=12)
            self.log_txt.tag_configure("t", foreground=INK3, font=self.mono(9))
            self.log_txt.tag_configure("w", foreground=AMBER)
            self.label(right, self.t("foot"), 8, "#5E6A80", wraplength=340, justify="left").pack(
                anchor="w", padx=14, pady=(4, 10))
        else:
            self.log_txt = None

    def set_tab(self, tab):
        self.tab = tab
        for k, (btn, label) in self.tab_btn.items():
            btn.config(text=label, fg=INK if k == tab else INK3)
        self.tree_open.pack_forget()
        self.tree_closed.pack_forget()
        (self.tree_open if tab == "open" else self.tree_closed).pack(fill="both", expand=True)
        self.sell_btn.config(state="normal" if tab == "open" else "disabled")

    # -- trading settings ---------------------------------------------------
    def build_settings(self, parent):
        foot = tk.Frame(parent, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
        foot.pack(side="bottom", fill="x", padx=12, pady=(6, 8))
        outer = tk.Frame(parent, bg=PANEL)
        outer.pack(fill="both", expand=True, padx=12)
        cv = tk.Canvas(outer, bg=PANEL, highlightthickness=0, height=120)
        sb = ttk.Scrollbar(outer, orient="vertical", command=cv.yview, style="D.Vertical.TScrollbar")
        inner = tk.Frame(cv, bg=PANEL)
        win = cv.create_window(0, 0, window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.bind("<Configure>", lambda e: cv.itemconfigure(win, width=e.width))
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        cv.bind_all("<MouseWheel>", lambda e: cv.yview_scroll(-1 * (e.delta // 120), "units")
                    if self.view == "settings" else None)
        self.settings_inner = inner
        self.save_msg = self.label(foot, self.t("saved_to"), 9, INK3)
        self.save_msg.pack(side="left", padx=8, pady=6)
        self.button(foot, self.t("save"), self.save_settings, "main").pack(side="right", padx=4, pady=6)
        self.button(foot, self.t("revert"), self.fill_settings, "ghost").pack(side="right", padx=4, pady=6)
        self.fill_settings()

    def section(self, parent, title, color=INK):
        f = tk.Frame(parent, bg=PANEL, highlightbackground="#232C3F", highlightthickness=1)
        f.pack(fill="x", pady=(10, 0))
        self.label(f, title, 10, color, "bold").pack(anchor="w", padx=12, pady=(8, 4))
        body = tk.Frame(f, bg=PANEL)
        body.pack(fill="x", padx=12, pady=(0, 10))
        return body

    def field(self, parent, label, value, row, col, width=14):
        cell = tk.Frame(parent, bg=PANEL)
        cell.grid(row=row, column=col, sticky="w", padx=(0, 18), pady=4)
        self.label(cell, label, 9, INK2).pack(anchor="w")
        e = self.entry(cell, value, width)
        e.pack(anchor="w", ipady=4)
        e.bind("<KeyRelease>", lambda ev: self.touch())
        return e

    def touch(self, *_):
        self.dirty = True
        self.dirty_lbl.config(text=self.t("unsaved"))

    def fill_settings(self):
        for w in self.settings_inner.winfo_children():
            w.destroy()
        cfg = self.live.eng.cfg if self.live else self.cfg
        self.form = {}
        s = self.section(self.settings_inner, self.t("s_syms"), self.accent)
        e = self.entry(s, ", ".join(cfg.symbols), 60)
        e.pack(anchor="w", ipady=4)
        e.bind("<KeyRelease>", lambda ev: self.touch())
        self.form["symbols"] = e
        self.label(s, self.t("s_syms_d"), 9, INK3).pack(anchor="w", pady=(4, 0))
        s = self.section(self.settings_inner, self.t("s_limits"), "#FCD34D")
        L = cfg.limits
        for i, (k, label, v) in enumerate((("max_total_notional", "l_total", L.max_total_notional),
                                           ("max_notional_per_order", "l_per", L.max_notional_per_order),
                                           ("max_open_positions", "l_pos", L.max_open_positions),
                                           ("max_orders_per_day", "l_orders", L.max_orders_per_day),
                                           ("daily_loss_pct", "l_loss", L.daily_loss_pct))):
            self.form["lim:" + k] = self.field(s, self.t(label), f"{v:g}", i // 3, i % 3)
        if self.strategy.INPUTS:
            s = self.section(self.settings_inner, self.t("s_inputs") + f" · {self.name}", "#C4B5FD")
            for i, (k, d) in enumerate(self.strategy.INPUTS.items()):
                v = cfg.inputs.get(k, d)
                if isinstance(d, bool):
                    var = tk.BooleanVar(value=bool(v))
                    cb = tk.Checkbutton(s, text=k, variable=var, bg=PANEL, fg=INK, selectcolor=PANEL2,
                                        activebackground=PANEL, font=self.mono(10), command=self.touch)
                    cb.grid(row=i // 3, column=i % 3, sticky="w", padx=(0, 18), pady=4)
                    self.form["in:" + k] = var
                else:
                    self.form["in:" + k] = self.field(s, k, f"{v:g}" if isinstance(v, float) else str(v), i // 3, i % 3)
            self.label(s, self.t("s_inputs_d"), 9, INK3, wraplength=820, justify="left").grid(
                row=99, column=0, columnspan=3, sticky="w", pady=(6, 0))
        s = self.section(self.settings_inner, self.t("s_mode"))
        row = tk.Frame(s, bg=PANEL)
        row.pack(anchor="w")
        self.mode_btns = {}
        for k, label in (("live", "m_live"), ("dry", "m_dry")):
            b = tk.Button(row, text=self.t(label), relief="flat", bd=0, padx=14, pady=5, cursor="hand2",
                          font=self.f(10, "bold"), command=lambda k=k: self.ask_mode(k))
            b.pack(side="left", padx=(0, 4))
            self.mode_btns[k] = b
        self.paint_mode(cfg.dry_run)
        self.label(s, self.t("m_live_d"), 9, INK3).pack(anchor="w", pady=(4, 8))
        for key, opts, cur in (("on_close", (("ask", "c_ask"), ("tray", "c_tray"), ("quit", "c_quit")), cfg.on_close),
                               ("lang", (("th", None), ("en", None)), self.lang)):
            row = tk.Frame(s, bg=PANEL)
            row.pack(anchor="w", pady=2)
            self.label(row, self.t("s_close" if key == "on_close" else "s_lang"), 9, INK2).pack(side="left", padx=(0, 10))
            var = tk.StringVar(value=cur)
            for v, label in opts:
                tk.Radiobutton(row, text=self.t(label) if label else {"th": "ไทย", "en": "English"}[v], value=v,
                               variable=var, bg=PANEL, fg=INK, selectcolor=PANEL2, activebackground=PANEL,
                               font=self.f(9), command=self.touch).pack(side="left", padx=(0, 10))
            self.form[key] = var
        s = self.section(self.settings_inner, self.t("s_acct"))
        acct = self.live.snapshot().get("account", "") if self.live else ""
        for k, v in ((self.t("a_status"), self.t("a_ok")), (self.t("a_acct"), "Webull Thailand ••••" + acct),
                     ("App Key", "••••••••" + cfg.app_key[-4:]), ("App Secret", "••••••••••••")):
            r = tk.Frame(s, bg=PANEL)
            r.pack(fill="x")
            self.label(r, k, 9, INK3, width=14, anchor="w").pack(side="left")
            self.label(r, v, 9, INK, mono=True).pack(side="left")
        keybox = tk.Frame(s, bg=PANEL)
        keybox.pack(anchor="w", pady=(6, 0))
        self.button(keybox, self.t("a_change"), lambda: self.key_form(keybox), "ghost", 9).pack(anchor="w")
        if self.live:
            self.dirty = False
            self.dirty_lbl.config(text="")

    def paint_mode(self, dry):
        for k, b in self.mode_btns.items():
            on = (k == "dry") == dry
            bg = ("#3B2A66" if k == "dry" else "#123524") if on else PANEL2
            b.config(bg=bg, fg=INK if on else INK3, activebackground=bg)

    def key_form(self, box):
        for w in box.winfo_children():
            w.destroy()
        self.label(box, self.t("a_new_key"), 9, INK2).pack(anchor="w")
        k = self.entry(box, "", 40, show="•")
        k.pack(anchor="w", ipady=4)
        self.label(box, self.t("a_new_secret"), 9, INK2).pack(anchor="w", pady=(6, 0))
        s = self.entry(box, "", 40, show="•")
        s.pack(anchor="w", ipady=4)
        msg = self.label(box, self.t("a_test_d"), 9, INK3, wraplength=600, justify="left")
        row = tk.Frame(box, bg=PANEL)
        row.pack(anchor="w", pady=6)

        def test():
            if not k.get().strip() or not s.get().strip():
                return
            msg.config(text=self.t("cn_busy"), fg=self.accent)
            kk, ss = k.get().strip(), s.get().strip()

            def work():
                out = self.live.ask("keys", kk, ss, self.connect, wait=True, timeout=600)
                self.inbox.put(("keys_done", out, msg))
            threading.Thread(target=work, daemon=True).start()

        self.button(row, self.t("a_test"), test, "main", 9).pack(side="left")
        self.button(row, self.t("cancel"), self.fill_settings, "ghost", 9).pack(side="left", padx=6)
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
        d = self.dialog(self.t("lv_title"), border=UP)
        acct = "••••" + self.live.snapshot().get("account", "")
        self.label(d.inner, self.t("lv_body", a=acct), 10, INK2, wraplength=460, justify="left").pack(anchor="w")
        var = tk.BooleanVar(value=False)
        go = self.button(d.btns, self.t("lv_go"), lambda: (d.close(), self.save_mode(False)), "buy")
        def arm():
            go.config(state="normal" if var.get() else "disabled", bg="#16A34A" if var.get() else "#1E293B",
                      disabledforeground="#5E6A80")
        arm()
        tk.Checkbutton(d.inner, text=self.t("lv_tick"), variable=var, bg=d.inner["bg"], fg=INK, selectcolor=PANEL2,
                       activebackground=d.inner["bg"], wraplength=440, justify="left", font=self.f(10),
                       command=arm).pack(anchor="w", pady=12)
        self.button(d.btns, self.t("lv_no"), d.close, "ghost").pack(side="right", padx=4)
        go.pack(side="right", padx=4)

    def save_mode(self, dry):
        cfg = copy.copy(self.live.eng.cfg)
        cfg.dry_run = dry

        def work():
            out = self.live.ask("save", cfg, wait=True, timeout=300)
            self.inbox.put(("mode_saved", out, dry))
        threading.Thread(target=work, daemon=True).start()

    # -- dialogs ---------------------------------------------------------------
    def dialog(self, title, border="#2A3448", width=520):
        top = tk.Toplevel(self.root, bg=PANEL, highlightbackground=border, highlightthickness=1)
        top.transient(self.root)
        top.title(title)
        top.resizable(False, False)
        self.label(top, title, 14, INK, "bold").pack(anchor="w", padx=22, pady=(18, 8))
        top.inner = tk.Frame(top, bg=PANEL, width=width)
        top.inner.pack(fill="x", padx=22)
        top.btns = tk.Frame(top, bg=PANEL)
        top.btns.pack(fill="x", padx=22, pady=(10, 18))
        top.close = lambda: (top.grab_release(), top.destroy())
        top.protocol("WM_DELETE_WINDOW", top.close)
        dark_title(top)
        top.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - max(width, top.winfo_reqwidth())) // 2
        y = self.root.winfo_rooty() + self.root.winfo_height() // 4
        top.geometry(f"+{max(0, x)}+{max(0, y)}")
        top.grab_set()
        return top

    def on_run_button(self):
        snap = self.live.snapshot() if self.live else {}
        if snap.get("run", "on") != "on":
            self.live.ask("run", "on")
            return
        d = self.dialog(self.t("stop"))
        for key, desc, cmd, color in (("stop_new", "stop_new_d", lambda: (d.close(), self.live.ask("run", "paused")), INK),
                                      ("stop_all", "stop_all_d", lambda: self.confirm_stop_all(d), "#FB7185")):
            b = tk.Frame(d.inner, bg=PANEL2, highlightbackground="#2A3448", highlightthickness=1, cursor="hand2")
            b.pack(fill="x", pady=4)
            h = self.label(b, self.t(key), 11, color, "bold", bg=PANEL2)
            h.pack(anchor="w", padx=12, pady=(8, 0))
            dd = self.label(b, self.t(desc), 9, INK3, bg=PANEL2, wraplength=440, justify="left")
            dd.pack(anchor="w", padx=12, pady=(0, 8))
            for w in (b, h, dd):
                w.bind("<Button-1>", lambda e, c=cmd: c())
        self.button(d.btns, self.t("cancel"), d.close, "ghost").pack(side="right")

    def confirm_stop_all(self, d):
        for w in list(d.inner.winfo_children()) + list(d.btns.winfo_children()):
            w.destroy()
        snap = self.live.snapshot()
        self.label(d.inner, self.t("stop_all_q", n=len(snap.get("positions", []))), 11, INK).pack(anchor="w")
        if snap.get("market", ("",))[0] != "open":
            self.label(d.inner, self.t("stop_all_closed"), 9, INK3).pack(anchor="w", pady=(4, 0))
        self.button(d.btns, self.t("cancel"), d.close, "ghost").pack(side="right", padx=4)
        self.button(d.btns, self.t("stop_all_go"), lambda: (d.close(), self.live.ask("run", "off")), "sell").pack(
            side="right", padx=4)

    def ask_buy(self, symbol):
        if not self.live:
            return
        snap = self.live.snapshot()
        px = (snap.get("quotes", {}).get(symbol) or (None, None))[0]
        d = self.dialog(self.t("b_title", s=symbol), border=UP)
        self.label(d.inner, self.t("b_last", p=num(px)), 10, INK2, mono=True).pack(anchor="e")
        grid = tk.Frame(d.inner, bg=PANEL)
        grid.pack(fill="x", pady=6)
        qty = self.field(grid, self.t("b_qty"), "1", 0, 0)
        tf = tk.Frame(grid, bg=PANEL)
        tf.grid(row=0, column=1, sticky="w", pady=4)
        self.label(tf, self.t("b_type"), 9, INK2).pack(anchor="w")
        kind = tk.StringVar(value="market")
        rr = tk.Frame(tf, bg=PANEL)
        rr.pack(anchor="w")
        lim = self.field(grid, self.t("b_limit_p"), num(px) if px else "", 1, 1)
        for v in ("market", "limit"):
            tk.Radiobutton(rr, text=self.t("b_" + v), value=v, variable=kind, bg=PANEL, fg=INK, selectcolor=PANEL2,
                           activebackground=PANEL, font=self.f(10)).pack(side="left", padx=(0, 8))
        stop = self.field(grid, self.t("b_stop"), num(px * 0.95) if px else "", 2, 0)
        target = self.field(grid, self.t("b_target"), "", 2, 1)
        cost = self.label(d.inner, "", 10, INK2, mono=True)
        cost.pack(anchor="w")
        self.label(d.inner, self.t("b_note"), 9, INK3, wraplength=480, justify="left").pack(anchor="w", pady=(6, 0))
        msg = self.label(d.inner, "", 10, DOWN, wraplength=480, justify="left")
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

        self.button(d.btns, self.t("cancel"), d.close, "ghost").pack(side="right", padx=4)
        go = self.button(d.btns, self.t("b_send"), send, "buy")
        go.pack(side="right", padx=4)

    def ask_sell(self):
        sel = self.tree_open.selection()
        if not sel or not self.live:
            return
        sym = sel[0]
        p = next((x for x in self.live.snapshot().get("positions", []) if x["symbol"] == sym), None)
        if not p:
            return
        d = self.dialog(self.t("sell_go") + " " + sym, border=DOWN)
        self.label(d.inner, self.t("sell_q", s=sym, q=p["qty"]), 11, INK).pack(anchor="w")
        self.button(d.btns, self.t("cancel"), d.close, "ghost").pack(side="right", padx=4)
        self.button(d.btns, self.t("sell_go"), lambda: (d.close(), self.live.ask("sell", sym)), "sell").pack(
            side="right", padx=4)

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
            b = tk.Frame(d.inner, bg=PANEL2, highlightbackground=self.accent if act == "tray" else "#3A4458",
                         highlightthickness=1, cursor="hand2")
            b.pack(fill="x", pady=4)
            h = self.label(b, self.t(key), 11, INK, "bold", bg=PANEL2)
            h.pack(anchor="w", padx=12, pady=(8, 0))
            dd = self.label(b, self.t(desc), 9, INK3, bg=PANEL2, wraplength=440, justify="left")
            dd.pack(anchor="w", padx=12, pady=(0, 8))
            for w in (b, h, dd):
                w.bind("<Button-1>", lambda e, a=act: self.closing(d, a, keep.get()))
        tk.Checkbutton(d.inner, text=self.t("cl_keep"), variable=keep, bg=PANEL, fg=INK2, selectcolor=PANEL2,
                       activebackground=PANEL, font=self.f(9)).pack(anchor="w", pady=(6, 0))
        self.button(d.btns, self.t("cancel"), d.close, "ghost").pack(side="right")

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
                self.clock.config(text=datetime.now(clock.ET).strftime("%H:%M:%S") + " ET")
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
                msg.config(text=self.t("cn_bad", e=out), fg=DOWN)
            else:
                self.fill_settings()
        elif kind == "saved":
            out, cfg, relang = a
            if out:
                self.save_msg.config(text=self.t("bad", e=out), fg=DOWN)
                return
            self.dirty = False
            self.dirty_lbl.config(text="")
            self.save_msg.config(text=self.t("saved"), fg=self.accent)
            self.cfg = cfg
            if relang and relang != self.lang:
                self.lang = relang
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
        sub = [self.version, "Webull Thailand ••••" + s["account"],
               self.ui.get("title", "")]
        self.sub.config(text=" · ".join(x for x in sub if x))
        run = s["run"]
        pill = {"on": (self.t("running"), "#0E2A33", "#67E8F9"), "paused": (self.t("paused"), "#2D2210", "#FCD34D"),
                "off": (self.t("off"), "#2D2210", "#FCD34D")}[run]
        self.run_pill.config(text="● " + pill[0], bg=pill[1], fg=pill[2])
        mp = (self.t("dry"), "#24193F", "#DDD6FE") if s["dry"] else (self.t("live"), "#10291C", "#86EFAC")
        self.mode_pill.config(text=mp[0], bg=mp[1], fg=mp[2])
        m = s["market"]
        self.mkt.config(text=("● " if m[0] == "open" else "○ ") + self.t("mkt_" + m[0], t=m[1]),
                        fg=UP if m[0] == "open" else INK3)
        self.run_btn.config(text=("❚❚  " + self.t("stop") + "  ▾") if run == "on" else ("▶  " + self.t("start")),
                            bg="#161D2B" if run == "on" else self.accent, fg=INK if run == "on" else "#06101A",
                            activebackground="#161D2B" if run == "on" else self.accent)
        for w in self.notices.winfo_children():
            w.destroy()
        for on, key, color, bg in ((s["halted"], "n_stopfile", "#FDE68A", "#221A0B"),
                                   (run == "paused", "n_paused", "#FDE68A", "#221A0B"),
                                   (run == "off", "n_off", "#FDE68A", "#221A0B"),
                                   (s["dry"], "n_dry", "#DDD6FE", "#1B1530")):
            if on:
                n = tk.Label(self.notices, text=self.t(key), bg=bg, fg=color, font=self.f(10), anchor="w",
                             padx=12, pady=6, highlightbackground="#4A3A16" if bg == "#221A0B" else "#3D2F6B",
                             highlightthickness=1)
                n.pack(fill="x", pady=(8, 0))
        self.paint_kpis(s, cfg)
        self.paint_watch(s)
        self.paint_chart_head(s)
        self.draw_chart()
        self.paint_tables(s)
        self.paint_log(s)

    def paint_kpis(self, s, cfg):
        usd = s["usd"]
        if "kpi_equity" in self.k:
            title, big, small, cv, tint = self.k["kpi_equity"]
            title.config(text=self.t("k_equity"))
            big.config(text=money(usd[0] + usd[1]) if usd else "—")
            small.config(text=self.t("k_cash", c=money(usd[0]), s=money(usd[1])) if usd else "")
            self.spark(cv, [v for _, v in s["equity_days"][-30:]], self.accent)
        if "kpi_today" in self.k:
            title, big, small, cv, tint = self.k["kpi_today"]
            tot = s["realized_today"] + s["unrealized"]
            title.config(text=self.t("k_today"))
            big.config(text=money(tot, True), fg=tone(tot))
            small.config(text=self.t("k_today_d", c=money(s["realized_today"], True), o=money(s["unrealized"], True)))
            self.bars(cv, [v for _, v in s["daily"]])
        if "kpi_stats" in self.k:
            title, big, small, cv, tint = self.k["kpi_stats"]
            pnl = [p for (p,) in s["trades30"]]
            won = [p for p in pnl if p > 0]
            lost = [-p for p in pnl if p < 0]
            pf = (sum(won) / sum(lost)) if lost else None
            title.config(text=self.t("k_stats"))
            big.config(text=f"{len(won) / len(pnl) * 100:.0f}%" if pnl else "—", fg=INK)
            small.config(text=self.t("k_stats_d", w=len(won), n=len(pnl), pf=num(pf) if pf else ("∞" if won else "—"))
                         + " · " + self.t("k_total", v=money(sum(pnl), True)))
            cv.delete("all")
            if pnl:
                w = max(10, cv.winfo_width())
                cv.create_rectangle(0, 12, w * len(won) / len(pnl), 22, fill=UP, outline="")
                cv.create_rectangle(w * len(won) / len(pnl), 12, w, 22, fill=DOWN, outline="")
        if "kpi_used" in self.k:
            title, big, small, cv, tint = self.k["kpi_used"]
            cap = cfg.limits.max_total_notional
            title.config(text=self.t("k_used"))
            big.config(text=money(s["in_use"]))
            small.config(text=("/ " + money(cap) if cap else self.t("k_nocap")) + " · "
                         + self.t("k_held", n=len(s["positions"]), m=cfg.limits.max_open_positions))
            cv.delete("all")
            w = max(10, cv.winfo_width())
            cv.create_rectangle(0, 12, w, 20, fill="#232B3D", outline="")
            if cap:
                cv.create_rectangle(0, 12, w * min(1, s["in_use"] / cap), 20, fill=AMBER, outline="")

    def spark(self, cv, vals, color):
        cv.delete("all")
        if len(vals) < 2:
            return
        w, h = max(10, cv.winfo_width()), 34
        lo, hi = min(vals), max(vals)
        span = (hi - lo) or 1
        pts = []
        for i, v in enumerate(vals):
            pts += [i * w / (len(vals) - 1), h - 3 - (v - lo) / span * (h - 6)]
        cv.create_line(*pts, fill=color, width=2, smooth=True)

    def bars(self, cv, vals):
        cv.delete("all")
        if not vals:
            return
        w, h = max(10, cv.winfo_width()), 34
        mx = max(abs(v) for v in vals) or 1
        bw = w / len(vals)
        for i, v in enumerate(vals):
            bh = max(3, abs(v) / mx * (h - 4))
            cv.create_rectangle(i * bw + 1, h - bh, (i + 1) * bw - 2, h, fill=UP if v >= 0 else DOWN, outline="")

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
                row = tk.Frame(self.watch_list, bg=PANEL, highlightbackground="#182033", highlightthickness=1,
                               cursor="hand2")
                row.pack(fill="x", padx=8, pady=1)
                left = tk.Frame(row, bg=PANEL)
                left.pack(side="left", padx=8, pady=3)
                name = self.label(left, sym, 11, INK, "bold")
                name.pack(anchor="w")
                state = self.label(left, "", 8, INK3)
                state.pack(anchor="w")
                extra = self.label(row, "", 8, INK3)
                extra.pack(side="left", padx=6)
                buy = self.button(row, self.t("buy"), lambda x=sym: self.ask_buy(x), "buy", 9)
                buy.config(pady=3)
                buy.pack(side="right", padx=8)
                right = tk.Frame(row, bg=PANEL)
                right.pack(side="right", padx=4)
                px = self.label(right, "", 11, INK, mono=True)
                px.pack(anchor="e")
                chg = self.label(right, "", 9, INK3, mono=True)
                chg.pack(anchor="e")
                for w in (row, left, name, state, right, px, chg, extra):
                    w.bind("<Button-1>", lambda e, x=sym: self.live.ask("select", x))
                self.watch_rows[sym] = (row, left, name, state, px, chg, extra, buy, right)
        for sym, (row, left, name, state, px, chg, extra, buy, right) in self.watch_rows.items():
            q = s["quotes"].get(sym) or (None, None)
            sel = sym == s["selected"]
            bg = "#16203A" if sel else PANEL
            for w in (row, left, name, state, px, chg, extra, right):
                w.config(bg=bg)
            row.config(highlightbackground=self.accent if sel else "#182033")
            state.config(text=self.t("w_held") if sym in held else self.t("w_wait"))
            px.config(text=num(q[0]))
            chg.config(text=("" if q[1] is None else f"{q[1]:+.2f}%"), fg=tone(q[1]))
            vals = s["values"].get(sym) or {}
            extra.config(text="  ".join(f"{k} {v:g}" if isinstance(v, (int, float)) else f"{k} {v}"
                                        for k, v in vals.items()))
            buy.config(state="disabled" if s["run"] == "off" else "normal")

    def paint_chart_head(self, s):
        sym = s["selected"]
        q = s["quotes"].get(sym) or (None, None)
        self.chart_title.config(text=sym or "—")
        self.chart_px.config(text=num(q[0]))
        p = next((x for x in s["positions"] if x["symbol"] == sym), None)
        legend = self.t("today_5m") if not s.get("chart_day") else self.t("day_5m", d=s["chart_day"])
        if p:
            legend = (f"{self.t('c_target')} {num(p['target'])}   {self.t('c_buy')} {num(p['entry'])}   "
                      f"{self.t('c_stop')} {num(p['stop'])}   ·   " + legend)
        self.chart_legend.config(text=legend)
        self._chart = (s["chart"], p)

    def draw_chart(self):
        if not hasattr(self, "_chart") or not self.chart.winfo_exists():
            return
        cv = self.chart
        cv.delete("all")
        bars, p = self._chart
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 50 or h < 50:
            return
        if len(bars) < 2:
            cv.create_text(w / 2, h / 2, text=self.t("no_bars"), fill=INK3, font=self.f(11))
            return
        vals = [c for _, c in bars]
        lo, hi = min(vals), max(vals)
        if p:
            lo = min([lo] + [x for x in (p["stop"], p["entry"]) if x])
            hi = max([hi] + [x for x in (p["target"], p["entry"]) if x])
        pad = (hi - lo) * 0.08 or 1
        lo, hi = lo - pad, hi + pad
        y = lambda v: 8 + (hi - v) / (hi - lo) * (h - 30)
        for i in range(1, 4):
            cv.create_line(0, i * (h - 22) / 4, w, i * (h - 22) / 4, fill="#1C2435")
        color = UP if vals[-1] >= vals[0] else DOWN
        pts = []
        for i, v in enumerate(vals):
            pts += [i * w / (len(vals) - 1), y(v)]
        cv.create_polygon(*pts, w, h - 22, 0, h - 22, fill="#0F2A20" if color == UP else "#2A1018", outline="")
        cv.create_line(*pts, fill=color, width=2)
        if p:
            for v, c, dash in ((p["target"], UP, (6, 5)), (p["entry"], "#94A3B8", (2, 4)), (p["stop"], DOWN, (6, 5))):
                if v:
                    cv.create_line(0, y(v), w, y(v), fill=c, dash=dash)
                    cv.create_text(w - 4, y(v) - 8, text=num(v), fill=c, anchor="e", font=self.mono(9))
        for frac in (0, 0.5, 1):
            i = int((len(bars) - 1) * frac)
            t = datetime.fromisoformat(bars[i][0]).astimezone(clock.ET)
            cv.create_text(min(max(i * w / (len(bars) - 1), 20), w - 20), h - 10, text=t.strftime("%H:%M"),
                           fill="#5E6A80", font=self.mono(8))

    def paint_tables(self, s):
        sel = self.tree_open.selection()
        self.tree_open.delete(*self.tree_open.get_children())
        for p in s["positions"]:
            pct = (p["last"] / p["entry"] - 1) * 100 if p["last"] and p["entry"] else None
            pl = money(p["pl"], True) + (f"  {pct:+.2f}%" if pct is not None else "")
            self.tree_open.insert("", "end", iid=p["symbol"], values=(
                p["symbol"], p["qty"], num(p["entry"]), num(p["last"]), pl, num(p["stop"]), num(p["target"]),
                self.t("by_you") if p["by"] == "you" else self.t("by_prog")))
        if sel and self.tree_open.exists(sel[0]):
            self.tree_open.selection_set(sel[0])
        self.tree_closed.delete(*self.tree_closed.get_children())
        for i, c in enumerate(s["closed"]):
            when = datetime.fromisoformat(c["closed_at"]).astimezone().strftime("%d/%m %H:%M")
            self.tree_closed.insert("", "end", iid=str(i), values=(
                when, c["symbol"], c["qty"], num(c["entry"]), num(c["exit"]), money(c["pnl"], True),
                why(self.lang, c["reason"]), self.t("by_you") if c["by"] == "you" else self.t("by_prog")))
        self.tab_btn["open"][0].config(text=f"{self.tab_btn['open'][1]}  {len(s['positions'])}")
        if "closed" in self.tab_btn:
            self.tab_btn["closed"][0].config(text=f"{self.tab_btn['closed'][1]}  {len(s['closed'])}")
        pnl = [p for (p,) in s["trades30"]]
        self.won_lbl.config(text=self.t("k_30d") + " · " + self.t("won_lost", w=sum(1 for p in pnl if p > 0),
                                                                  l=sum(1 for p in pnl if p < 0)))

    def paint_log(self, s):
        if not self.log_txt:
            return
        at_end = self.log_txt.yview()[1] > 0.98
        self.log_txt.config(state="normal")
        self.log_txt.delete("1.0", "end")
        for t, lvl, msg in s["log"]:
            self.log_txt.insert("end", t + "  ", "t")
            self.log_txt.insert("end", msg + "\n", "w" if lvl in ("WARNING", "ERROR") else "")
        self.log_txt.config(state="disabled")
        if at_end:
            self.log_txt.see("end")


def available():
    """Can this computer show a window?"""
    try:
        r = tk.Tk()
        r.withdraw()
        r.destroy()
        return True
    except tk.TclError:
        return False
