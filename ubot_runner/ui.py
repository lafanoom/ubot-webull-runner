"""The window's text and what a strategy may change in it.

Every program gets the same standard window. A strategy may change it a little
through its `UI` class attribute (a plain dict literal) - nothing here can
reach the keys, the network or an order except through ctx, which the runner
checks like every other order:

    UI = {
        "title": "Gap and go, small caps",      # line under the name, <= 40 chars
        "lang": "en",                            # default window language: "th" | "en"
        "accent": "#22D3EE",                     # accent colour (never green/red: those mean money)
        "hide": ["chart", "kpi_stats"],          # panels to leave out (see PANELS)
        "values": ["RSI", "To target %"],        # extra columns in the watch list, from ui_values()
        "buttons": [{"id": "sell_all", "label": "Sell all"}],   # up to 4, call on_button()
    }

`check_ui` is the only judge. The runner shows the standard window when it says
no, and the factory refuses the strategy file for the same reasons.
"""
import re

PANELS = ("kpi_equity", "kpi_today", "kpi_stats", "kpi_used", "chart", "watch", "closed", "log")
UI_KEYS = ("title", "lang", "accent", "hide", "values", "buttons")
MAX_BUTTONS = 4
MAX_VALUES = 3
ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,19}$")
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _label(v, n):
    return isinstance(v, str) and 0 < len(v.strip()) <= n and not any(ord(c) < 32 for c in v)


def _money_colour(hexcode):
    """True for colours a reader takes for profit (green) or loss (red)."""
    r, g, b = (int(hexcode[i:i + 2], 16) for i in (1, 3, 5))
    hi, lo = max(r, g, b), min(r, g, b)
    if hi - lo < 60:
        return False                                  # greys
    return (g == hi and g - max(r, b) > 30) or (r == hi and r - max(g, b) > 60 and b < 150)


def check_ui(ui):
    """Problems with a strategy's UI dict, [] when it is fine."""
    if ui is None or ui == {}:
        return []
    if not isinstance(ui, dict):
        return ["UI must be a dict"]
    out = [f"UI has an unknown key {k!r}" for k in ui if k not in UI_KEYS]
    if "title" in ui and not _label(ui["title"], 40):
        out.append("UI title must be text of 1-40 characters")
    if "lang" in ui and ui["lang"] not in ("th", "en"):
        out.append('UI lang must be "th" or "en"')
    if "accent" in ui:
        a = ui["accent"]
        if not isinstance(a, str) or not HEX_RE.match(a):
            out.append('UI accent must be a colour like "#22D3EE"')
        elif _money_colour(a):
            out.append("UI accent cannot be green or red - those colours mean profit and loss")
    if "hide" in ui:
        h = ui["hide"]
        if not isinstance(h, list) or not all(x in PANELS for x in h) or len(set(h)) != len(h):
            out.append(f"UI hide must list panels from {', '.join(PANELS)}")
    if "values" in ui:
        v = ui["values"]
        if not isinstance(v, list) or not 1 <= len(v) <= MAX_VALUES or not all(_label(x, 16) for x in v) \
                or len(set(v)) != len(v):
            out.append(f"UI values must be 1-{MAX_VALUES} different labels of up to 16 characters")
    if "buttons" in ui:
        b = ui["buttons"]
        ok = isinstance(b, list) and 1 <= len(b) <= MAX_BUTTONS
        ids = []
        for x in b if ok else []:
            if not isinstance(x, dict) or set(x) != {"id", "label"} or not isinstance(x["id"], str) \
                    or not ID_RE.match(x["id"]) or not _label(x["label"], 24):
                ok = False
            else:
                ids.append(x["id"])
        if not ok or len(set(ids)) != len(ids):
            out.append(f'UI buttons must be 1-{MAX_BUTTONS} items like {{"id": "sell_all", "label": "Sell all"}}')
    return out


def ui_of(strategy):
    """The strategy's UI when it passes, else the standard window ({})."""
    ui = getattr(strategy, "UI", None) or {}
    return dict(ui) if not check_ui(ui) else {}


# -- the window's own words --------------------------------------------------
# Thai and English. Numbers are put in by the window, never written here.

T = {
    "en": {
        "running": "Running", "paused": "No new buys", "off": "Stopped",
        "live": "Live trading", "dry": "Test mode",
        "mkt_open": "Market open · closes {t} ET", "mkt_closed": "Market closed · opens {t} ET",
        "stop": "Stop program", "start": "Start program",
        "stop_new": "Stop new buys only", "stop_new_d": "No new buys. Positions held now are still looked after until they close.",
        "stop_all": "Stop everything", "stop_all_d": "Sell every position at market, cancel the stops at Webull, then do nothing.",
        "stop_all_q": "Sell all {n} positions at market now?", "stop_all_closed": "The market is closed: the sale waits for the open.",
        "stop_all_go": "Sell all and stop", "cancel": "Cancel",
        "n_paused": "No new buys · positions held now are still looked after · buys by hand still work and are looked after too",
        "n_off": "Stopped · every position sold · the program does nothing until you start it",
        "n_dry": "Test mode · the program writes what it would do but sends no order to Webull · switch in Trading settings → Mode",
        "n_stopfile": "A STOP file is next to the program: nothing new is sent until you delete it",
        "k_equity": "Account value", "k_cash": "Cash {c} · Stocks {s}", "k_30d": "30 days",
        "k_today": "Today's P/L", "k_today_d": "Closed {c} · Open {o}", "k_days": "last {n} days",
        "k_stats": "30-day results", "k_stats_d": "Won {w} / {n} · PF {pf}", "k_total": "Total {v}",
        "k_used": "Money the program uses", "k_held": "Holding {n} / {m}", "k_nocap": "no cap",
        "chart": "Chart", "settings": "Trading settings", "unsaved": "Unsaved changes",
        "c_ind": "Indicators", "c_marks": "Trades", "c_sys_tf": "The program decides on {tf} bars",
        "c_no_ind": "This program draws no indicator lines", "c_mark_buy": "Bought {q} @ {p}",
        "c_mark_sell": "Sold {q} @ {p} · {pl} · {why}", "c_buy": "Bought", "c_stop": "Stop", "c_target": "Target", "no_bars": "No chart yet",
        "watch": "Stocks the program watches", "watch_tip": "Click for the chart", "buy": "Buy",
        "w_held": "Held", "w_wait": "Waiting",
        "open_tab": "Open positions", "closed_tab": "Closed",
        "h_sym": "Stock", "h_qty": "Shares", "h_avg": "Bought", "h_last": "Last", "h_pl": "P/L",
        "h_stop": "Stop (at Webull)", "h_target": "Target (watched)", "h_by": "Opened by", "h_when": "Closed",
        "h_sell": "Sold", "h_why": "Closed by",
        "by_you": "You", "by_prog": "Program", "sell_sel": "Sell selected", "sell_q": "Sell all {q} {s} at market?",
        "sell_go": "Sell", "won_lost": "Won {w} · Lost {l}",
        "log": "What the program did", "foot": "Your keys stay on this computer and go to Webull only · ✕ asks whether to quit or keep running in the corner",
        "s_syms": "Stocks to trade", "s_add": "+ Add", "s_syms_d": "US stocks and ETFs · checked with Webull when you save",
        "s_limits": "Money and risk", "l_total": "Most money in use (0 = no cap)", "l_per": "Money per buy",
        "l_pos": "Most stocks held at once", "l_orders": "Most orders a day", "l_loss": "Daily loss limit (% of account)",
        "s_inputs": "The trading system's values", "s_inputs_d": "Defaults come from the conditions agreed when you ordered · saving applies them at once, to positions held now as well",
        "s_mode": "Mode", "m_live": "Live", "m_dry": "Test (no orders)", "m_live_d": "Switching to live asks you on screen first",
        "s_close": "Close button ✕", "c_ask": "Ask each time", "c_tray": "Keep running in the corner", "c_quit": "Quit",
        "s_lang": "Language",
        "s_acct": "Webull account", "a_status": "Status", "a_ok": "Connected", "a_acct": "Account",
        "a_change": "Change keys", "a_new_key": "New App Key", "a_new_secret": "New App Secret", "a_test": "Test and use these keys",
        "a_test_d": "The program connects with the new keys first · only then does it replace the old ones",
        "save": "Save", "revert": "Undo changes", "saved_to": "Saved to webull.toml on this computer · takes effect at once",
        "saved": "Saved", "bad": "Not saved: {e}",
        "b_title": "Buy {s} by hand", "b_last": "Last {p}", "b_qty": "Shares", "b_type": "Order type",
        "b_market": "Market", "b_limit": "Limit", "b_limit_p": "Limit price", "b_stop": "Stop (rests at Webull)",
        "b_target": "Target (watched by the program)", "b_cost": "About {c}",
        "b_note": "Regular hours only · whole shares · the stop goes to Webull right after the fill · this position is marked \"You\" and is looked after like the program's own, inside the same money limits",
        "b_send": "Send buy order", "b_bad": "Not sent: {e}",
        "lv_title": "Switch to live trading?",
        "lv_body": "From now on the program sends real orders to Webull account {a} with real money, within the conditions and money limits you set.",
        "lv_tick": "I understand the program will trade with real money and any loss is mine", "lv_no": "Not now", "lv_go": "Start live trading",
        "cl_title": "Close this window", "cl_tray": "Keep running in the corner", "cl_tray_d": "The program keeps buying, selling and watching targets · click its icon by the clock to bring the window back",
        "cl_quit": "Quit the program", "cl_quit_d": "No new buys and nobody watches the targets · the stops at Webull keep working · nothing is sold",
        "cl_keep": "Remember this (change it in Trading settings)",
        "tray_tip": "{n} is running",
        "cn_title": "Connect your Webull account", "cn_d": "Enter the App Key and App Secret Webull approved for your account. Once is enough; this computer keeps them.",
        "cn_1": "In the Webull Thailand app, open OpenAPI and apply for an App Key (Webull approves it within 1-2 business days)",
        "cn_2": "Copy the App Key and the App Secret into the boxes", "cn_3": "Press Connect. The first time, Webull asks you to approve it in the app on your phone",
        "cn_safe": "Your keys stay on this computer and go to Webull only · uBotDesign never sees them · never give them to anyone, including in our chat",
        "cn_go": "Connect", "cn_busy": "Connecting · approve it in the Webull app on your phone", "cn_bad": "Webull did not accept these keys: {e}",
        "cn_later": "You can change the keys later in Trading settings → Webull account",
    },
    "th": {
        "running": "กำลังทำงาน", "paused": "หยุดเปิดใหม่", "off": "หยุดทั้งหมด",
        "live": "เทรดจริง", "dry": "โหมดทดลอง",
        "mkt_open": "ตลาดเปิด · ปิด {t} ET", "mkt_closed": "ตลาดปิด · เปิด {t} ET",
        "stop": "หยุดโปรแกรม", "start": "เริ่มโปรแกรม",
        "stop_new": "หยุดเปิดใหม่อย่างเดียว", "stop_new_d": "ไม่ซื้อใหม่ · ไม้ที่ถืออยู่โปรแกรมยังดูแล Stop/Target ต่อจนปิด",
        "stop_all": "หยุดทั้งหมด", "stop_all_d": "ขายทุกไม้ที่ราคาตลาด ยกเลิก Stop ที่ Webull แล้วหยุดทำงาน",
        "stop_all_q": "ขายทุกไม้ที่ถืออยู่ {n} ตัวที่ราคาตลาดตอนนี้?", "stop_all_closed": "ตลาดปิดอยู่ คำสั่งขายรอเปิดตลาด",
        "stop_all_go": "ขายทั้งหมดแล้วหยุด", "cancel": "ยกเลิก",
        "n_paused": "หยุดเปิดใหม่ · ไม่ซื้อใหม่ · ไม้ที่ถืออยู่โปรแกรมยังดูแล Stop/Target ต่อ · ซื้อด้วยมือยังทำได้ และโปรแกรมดูแลให้เหมือนกัน",
        "n_off": "หยุดทั้งหมด · ขายทุกไม้แล้ว · โปรแกรมไม่ทำอะไรจนกว่าจะกดเริ่มโปรแกรม",
        "n_dry": "โหมดทดลอง · โปรแกรมเขียนว่าจะทำอะไรแต่ไม่ส่งคำสั่งไป Webull · เปลี่ยนเป็นเทรดจริงได้ที่ ตั้งค่าระบบเทรด → โหมด",
        "n_stopfile": "มีไฟล์ STOP อยู่ข้างโปรแกรม · ไม่ส่งคำสั่งใหม่จนกว่าจะลบไฟล์นั้น",
        "k_equity": "มูลค่าบัญชี", "k_cash": "เงินสด {c} · หุ้น {s}", "k_30d": "30 วัน",
        "k_today": "กำไร/ขาดทุนวันนี้", "k_today_d": "ปิดแล้ว {c} · ยังไม่ปิด {o}", "k_days": "{n} วันล่าสุด",
        "k_stats": "ผล 30 วัน", "k_stats_d": "ชนะ {w} / {n} ไม้ · PF {pf}", "k_total": "รวม {v}",
        "k_used": "เงินที่ให้โปรแกรมใช้", "k_held": "ถือ {n} / {m} ตัว", "k_nocap": "ไม่จำกัด",
        "chart": "กราฟ", "settings": "ตั้งค่าระบบเทรด", "unsaved": "มีค่าที่ยังไม่ได้บันทึก",
        "c_ind": "Indicator", "c_marks": "จุดซื้อขาย", "c_sys_tf": "ระบบตัดสินใจจากแท่ง {tf}",
        "c_no_ind": "โปรแกรมนี้ไม่มีเส้น indicator", "c_mark_buy": "ซื้อ {q} หุ้น @ {p}",
        "c_mark_sell": "ขาย {q} หุ้น @ {p} · {pl} · {why}", "c_buy": "ซื้อ", "c_stop": "Stop", "c_target": "Target", "no_bars": "ยังไม่มีกราฟ",
        "watch": "หุ้นที่โปรแกรมดูอยู่", "watch_tip": "กดเพื่อดูกราฟ", "buy": "ซื้อ",
        "w_held": "ถืออยู่", "w_wait": "รอเงื่อนไข",
        "open_tab": "ไม้ที่เปิดอยู่", "closed_tab": "ไม้ที่ปิดแล้ว",
        "h_sym": "หุ้น", "h_qty": "จำนวน", "h_avg": "ราคาซื้อ", "h_last": "ล่าสุด", "h_pl": "กำไร/ขาดทุน",
        "h_stop": "Stop (ที่ Webull)", "h_target": "Target (โปรแกรมเฝ้า)", "h_by": "ใครเปิด", "h_when": "ปิดเมื่อ",
        "h_sell": "ขาย", "h_why": "ปิดด้วย",
        "by_you": "คุณ", "by_prog": "โปรแกรม", "sell_sel": "ขายไม้ที่เลือก", "sell_q": "ขาย {s} ทั้งหมด {q} หุ้นที่ราคาตลาด?",
        "sell_go": "ขาย", "won_lost": "ชนะ {w} · แพ้ {l}",
        "log": "สิ่งที่โปรแกรมทำ", "foot": "กุญแจเก็บบนเครื่องนี้เท่านั้น ส่งไปที่ Webull ที่เดียว · ปุ่ม ✕ ถามว่าจะปิดโปรแกรม หรือย่อไว้มุมจอแล้วทำงานต่อ",
        "s_syms": "หุ้นที่ให้เทรด", "s_add": "+ เพิ่ม", "s_syms_d": "หุ้นสหรัฐและ ETF · ตรวจกับ Webull ตอนบันทึก",
        "s_limits": "เงินและความเสี่ยง", "l_total": "เงินสูงสุดที่ให้ใช้ (0 = ไม่จำกัด)", "l_per": "เงินต่อไม้",
        "l_pos": "ถือพร้อมกันสูงสุด (ตัว)", "l_orders": "คำสั่งต่อวันสูงสุด", "l_loss": "ขาดทุนต่อวันสูงสุด (% ของบัญชี)",
        "s_inputs": "ค่าของระบบเทรด", "s_inputs_d": "ค่าเริ่มต้นมาจากเงื่อนไขที่ตกลงกันตอนสั่งสร้าง · บันทึกแล้วมีผลทันที รวมไม้ที่ถืออยู่: Stop ที่ Webull และ Target ถูกคิดใหม่ตามค่าใหม่",
        "s_mode": "โหมด", "m_live": "เทรดจริง", "m_dry": "ทดลอง (ไม่ส่งคำสั่ง)", "m_live_d": "กดเทรดจริงแล้วโปรแกรมถามยืนยันบนหน้าจอก่อนเปลี่ยน",
        "s_close": "เมื่อกด ✕", "c_ask": "ถามทุกครั้ง", "c_tray": "ย่อไว้มุมจอ", "c_quit": "ปิดโปรแกรม",
        "s_lang": "ภาษา",
        "s_acct": "บัญชี Webull", "a_status": "สถานะ", "a_ok": "เชื่อมต่อแล้ว", "a_acct": "บัญชี",
        "a_change": "เปลี่ยนกุญแจ", "a_new_key": "App Key ใหม่", "a_new_secret": "App Secret ใหม่", "a_test": "ทดสอบแล้วใช้กุญแจนี้",
        "a_test_d": "โปรแกรมลองต่อ Webull ด้วยกุญแจใหม่ก่อน · ผ่านแล้วค่อยแทนกุญแจเดิม · ไม่ผ่านกุญแจเดิมยังใช้อยู่",
        "save": "บันทึก", "revert": "ยกเลิกการแก้", "saved_to": "บันทึกลง webull.toml บนเครื่องนี้ · มีผลทันทีที่กดบันทึก",
        "saved": "บันทึกแล้ว", "bad": "ยังไม่ได้บันทึก: {e}",
        "b_title": "ซื้อ {s} ด้วยมือ", "b_last": "ล่าสุด {p}", "b_qty": "จำนวนหุ้น", "b_type": "ชนิดคำสั่ง",
        "b_market": "Market", "b_limit": "Limit", "b_limit_p": "ราคา Limit", "b_stop": "Stop (วางที่ Webull)",
        "b_target": "Target (โปรแกรมเฝ้า)", "b_cost": "ใช้เงินประมาณ {c}",
        "b_note": "ซื้อได้เฉพาะเวลาตลาดปกติ · หุ้นเต็มจำนวน · Stop วางที่ Webull ทันทีหลังซื้อได้ · ไม้นี้ติดป้าย \"คุณ\" และรวมอยู่ในระบบเดียวกัน: โปรแกรมดูแล Stop/Target และนับในเพดานเงิน",
        "b_send": "ส่งคำสั่งซื้อ", "b_bad": "ยังไม่ได้ส่ง: {e}",
        "lv_title": "เปลี่ยนเป็นเทรดจริง?",
        "lv_body": "ตั้งแต่นี้โปรแกรมส่งคำสั่งซื้อขายจริงไปที่บัญชี Webull {a} ด้วยเงินจริง ตามเงื่อนไขและเพดานเงินที่ตั้งไว้",
        "lv_tick": "ฉันเข้าใจว่าโปรแกรมจะเทรดด้วยเงินจริง และผลขาดทุนเป็นของฉัน", "lv_no": "ยังไม่เปลี่ยน", "lv_go": "เริ่มเทรดจริง",
        "cl_title": "ปิดหน้าต่างนี้", "cl_tray": "ย่อไว้มุมจอ ทำงานต่อ", "cl_tray_d": "โปรแกรมยังซื้อขายและเฝ้า Target ต่อ · กดไอคอนข้างนาฬิกาเพื่อเปิดหน้าต่างกลับมา",
        "cl_quit": "ปิดโปรแกรม", "cl_quit_d": "ไม่ซื้อใหม่ และไม่มีใครเฝ้า Target · Stop ที่วางไว้ที่ Webull ยังทำงาน · ไม้ที่ถืออยู่ไม่ถูกขาย",
        "cl_keep": "จำคำตอบนี้ ไม่ต้องถามอีก (เปลี่ยนได้ที่ ตั้งค่าระบบเทรด)",
        "tray_tip": "{n} กำลังทำงาน",
        "cn_title": "เชื่อมบัญชี Webull ของคุณ", "cn_d": "ใส่ App Key กับ App Secret ที่ Webull อนุมัติให้บัญชีของคุณ ครั้งเดียวพอ โปรแกรมจำไว้บนเครื่องนี้",
        "cn_1": "เข้าแอป Webull Thailand ไปที่ OpenAPI แล้วขอ App Key (Webull อนุมัติภายใน 1–2 วันทำการ)",
        "cn_2": "คัดลอก App Key และ App Secret มาวางในช่อง", "cn_3": "กดเชื่อมต่อ ครั้งแรก Webull จะขอให้ยืนยันในแอปบนมือถือ",
        "cn_safe": "กุญแจอยู่บนเครื่องนี้และส่งไปที่ Webull ที่เดียว · uBotDesign ไม่เห็นกุญแจของคุณ · ห้ามส่งกุญแจให้ใคร รวมถึงในแชทกับเรา",
        "cn_go": "เชื่อมต่อ", "cn_busy": "กำลังเชื่อมต่อ · ยืนยันในแอป Webull บนมือถือ", "cn_bad": "Webull ไม่รับกุญแจนี้: {e}",
        "cn_later": "เปลี่ยนกุญแจทีหลังได้ที่ ตั้งค่าระบบเทรด → บัญชี Webull",
    },
}

WHY = {
    "en": {"target": "Target", "stop": "Stop at Webull", "sold by you": "Sold by you", "stop all": "Stop all",
           "sold outside the program": "Sold in Webull", "program": "Program's rule"},
    "th": {"target": "ถึง Target", "stop": "Stop ที่ Webull", "sold by you": "ขายด้วยมือ", "stop all": "หยุดทั้งหมด",
           "sold outside the program": "ขายในแอป Webull", "program": "หมดเงื่อนไข"},
}


def text(lang, key, **kw):
    s = T.get(lang, T["en"]).get(key) or T["en"][key]
    return s.format(**kw) if kw else s


def why(lang, reason):
    return WHY.get(lang, WHY["en"]).get(reason or "", reason or "")


def default_lang(cfg_lang, ui):
    """The customer's own choice wins, then the program's UI lang, then the computer's."""
    if cfg_lang in ("th", "en"):
        return cfg_lang
    if ui.get("lang") in ("th", "en"):
        return ui["lang"]
    try:
        import locale
        loc = (locale.getlocale()[0] or "").lower()
        if loc.startswith("th") or "thai" in loc:
            return "th"
    except Exception:
        pass
    if __import__("os").name == "nt":
        try:
            import ctypes
            if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x1E:   # LANG_THAI
                return "th"
        except Exception:
            pass
    return "en"
