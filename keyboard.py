# -*- coding: utf-8 -*-
"""
Pico Virtual Keyboard — экранная клавиатура для Windows.

* Окно поверх всех и НЕ забирает фокус: текст печатается туда, где стоит курсор.
* Русская и английская раскладки (кнопка RU/EN), не зависит от раскладки Windows.
* Залипающие Shift / Ctrl / Alt / Win.
* Буфер обмена: Копировать / Вырезать / Вставить / Выделить всё / Отменить
  и история последних скопированных фрагментов (клик — вставить).

Только стандартная библиотека Python, без pyautogui и без браузера.
"""

import ctypes
import json
import os
import sys
import tkinter as tk

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATE_PATH = os.path.join(BASE_DIR, "state.json")
IS_WIN = sys.platform == "win32"

# ----------------------------------------------------------------------------
# Коды клавиш Windows
# ----------------------------------------------------------------------------
VK = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "esc": 0x1B, "space": 0x20,
    "home": 0x24, "end": 0x23, "left": 0x25, "up": 0x26, "right": 0x27,
    "down": 0x28, "delete": 0x2E,
    "shift": 0xA0, "ctrl": 0xA2, "alt": 0xA4, "win": 0x5B,
}
EXTENDED_VK = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B}


# ----------------------------------------------------------------------------
# Ввод и буфер обмена через WinAPI
# ----------------------------------------------------------------------------
class WinBackend:
    def __init__(self):
        from ctypes import wintypes as wt
        self.wt = wt
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ULONG_PTR = ctypes.c_size_t

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                        ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                        ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]

        class _U(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", wt.DWORD), ("u", _U)]

        self.KEYBDINPUT, self.INPUT = KEYBDINPUT, INPUT
        u, k = self.user32, self.kernel32
        u.SendInput.argtypes = (wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
        u.SendInput.restype = wt.UINT
        u.MapVirtualKeyW.argtypes = (wt.UINT, wt.UINT)
        u.MapVirtualKeyW.restype = wt.UINT
        u.GetParent.argtypes = (wt.HWND,)
        u.GetParent.restype = wt.HWND
        u.GetWindowLongW.argtypes = (wt.HWND, ctypes.c_int)
        u.GetWindowLongW.restype = wt.LONG
        u.SetWindowLongW.argtypes = (wt.HWND, ctypes.c_int, wt.LONG)
        u.SetWindowLongW.restype = wt.LONG
        u.SetWindowPos.argtypes = (wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wt.UINT)
        u.OpenClipboard.argtypes = (wt.HWND,)
        u.OpenClipboard.restype = wt.BOOL
        u.CloseClipboard.restype = wt.BOOL
        u.EmptyClipboard.restype = wt.BOOL
        u.GetClipboardData.argtypes = (wt.UINT,)
        u.GetClipboardData.restype = wt.HANDLE
        u.SetClipboardData.argtypes = (wt.UINT, wt.HANDLE)
        u.SetClipboardData.restype = wt.HANDLE
        u.IsClipboardFormatAvailable.argtypes = (wt.UINT,)
        u.IsClipboardFormatAvailable.restype = wt.BOOL
        u.GetClipboardSequenceNumber.restype = wt.DWORD
        k.GlobalAlloc.argtypes = (wt.UINT, ctypes.c_size_t)
        k.GlobalAlloc.restype = wt.HGLOBAL
        k.GlobalLock.argtypes = (wt.HGLOBAL,)
        k.GlobalLock.restype = ctypes.c_void_p
        k.GlobalUnlock.argtypes = (wt.HGLOBAL,)
        k.GlobalFree.argtypes = (wt.HGLOBAL,)
        self.hwnd = None

    # --- окно -----------------------------------------------------------
    def make_no_activate(self, root):
        root.update_idletasks()
        hwnd = self.user32.GetParent(root.winfo_id()) or root.winfo_id()
        self.hwnd = hwnd
        GWL_EXSTYLE = -20
        WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x8, 0x80, 0x08000000
        WS_EX_APPWINDOW = 0x40000
        style = self.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style = (style | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE) & ~WS_EX_APPWINDOW
        self.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ctypes.c_long(style).value)
        SWP = 0x0001 | 0x0002 | 0x0010 | 0x0020  # NOSIZE|NOMOVE|NOACTIVATE|FRAMECHANGED
        self.user32.SetWindowPos(hwnd, self.wt.HWND(-1), 0, 0, 0, 0, SWP)

    # --- клавиатура -----------------------------------------------------
    def _vk(self, vk, up=False):
        flags = (0x0002 if up else 0) | (0x0001 if vk in EXTENDED_VK else 0)
        scan = self.user32.MapVirtualKeyW(vk, 0)
        return self.INPUT(type=1, ki=self.KEYBDINPUT(vk, scan, flags, 0, 0))

    def _uni(self, code, up=False):
        flags = 0x0004 | (0x0002 if up else 0)  # KEYEVENTF_UNICODE
        return self.INPUT(type=1, ki=self.KEYBDINPUT(0, code, flags, 0, 0))

    def _send(self, items):
        if items:
            arr = (self.INPUT * len(items))(*items)
            self.user32.SendInput(len(items), arr, ctypes.sizeof(self.INPUT))

    def text(self, s):
        items = []
        for ch in s:
            data = ch.encode("utf-16-le")
            for i in range(0, len(data), 2):
                code = int.from_bytes(data[i:i + 2], "little")
                items += [self._uni(code), self._uni(code, True)]
        self._send(items)

    def combo(self, mods, vk):
        items = [self._vk(m) for m in mods]
        items += [self._vk(vk), self._vk(vk, True)]
        items += [self._vk(m, True) for m in reversed(mods)]
        self._send(items)

    # --- буфер обмена ---------------------------------------------------
    def _open_clip(self):
        import time
        for _ in range(10):
            if self.user32.OpenClipboard(self.hwnd):
                return True
            time.sleep(0.02)
        return False

    def clip_seq(self):
        return self.user32.GetClipboardSequenceNumber()

    def clip_get(self):
        CF_UNICODETEXT = 13
        if not self.user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        if not self._open_clip():
            return None
        try:
            h = self.user32.GetClipboardData(CF_UNICODETEXT)
            if not h:
                return None
            p = self.kernel32.GlobalLock(h)
            if not p:
                return None
            try:
                return ctypes.wstring_at(p)
            finally:
                self.kernel32.GlobalUnlock(h)
        finally:
            self.user32.CloseClipboard()

    def clip_set(self, text):
        CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
        data = (text + "\0").encode("utf-16-le")
        if not self._open_clip():
            return False
        try:
            self.user32.EmptyClipboard()
            h = self.kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not h:
                return False
            p = self.kernel32.GlobalLock(h)
            ctypes.memmove(p, data, len(data))
            self.kernel32.GlobalUnlock(h)
            if not self.user32.SetClipboardData(CF_UNICODETEXT, h):
                self.kernel32.GlobalFree(h)
                return False
            return True
        finally:
            self.user32.CloseClipboard()


class PreviewBackend:
    """Заглушка для запуска не в Windows (только показывает окно)."""

    def __init__(self):
        self.root = None

    def make_no_activate(self, root):
        self.root = root
        root.attributes("-topmost", True)

    def text(self, s):
        print("text:", repr(s))

    def combo(self, mods, vk):
        print("combo:", [hex(m) for m in mods], hex(vk))

    def clip_seq(self):
        return None

    def clip_get(self):
        try:
            return self.root.clipboard_get()
        except Exception:
            return None

    def clip_set(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        return True


# ----------------------------------------------------------------------------
# Раскладка
# ----------------------------------------------------------------------------
def letters(en, ru):
    return [("char", (e, e.upper(), r, r.upper(), ord(e.upper())), 1) for e, r in zip(en, ru)]


def ch(en, en_s, ru, ru_s, vk, w=1):
    return ("char", (en, en_s, ru, ru_s, vk), w)


LAYOUT = [
    [("special", "esc", "Esc", 1), ("special", "home", "Home", 1.2), ("special", "end", "End", 1.2),
     ("action", "copy", "Копировать", 2.2), ("action", "cut", "Вырезать", 2),
     ("action", "paste", "Вставить", 2), ("action", "selall", "Выделить всё", 2.5),
     ("action", "undo", "↶", 1), ("action", "history", "История", 1.9)],
    [ch("`", "~", "ё", "Ё", 0xC0), ch("1", "!", "1", "!", 0x31), ch("2", "@", "2", '"', 0x32),
     ch("3", "#", "3", "№", 0x33), ch("4", "$", "4", ";", 0x34), ch("5", "%", "5", "%", 0x35),
     ch("6", "^", "6", ":", 0x36), ch("7", "&", "7", "?", 0x37), ch("8", "*", "8", "*", 0x38),
     ch("9", "(", "9", "(", 0x39), ch("0", ")", "0", ")", 0x30), ch("-", "_", "-", "_", 0xBD),
     ch("=", "+", "=", "+", 0xBB), ("special", "backspace", "⌫ Backspace", 2)],
    [("special", "tab", "Tab", 1.5)] + letters("qwertyuiop", "йцукенгшщз") +
    [ch("[", "{", "х", "Х", 0xDB), ch("]", "}", "ъ", "Ъ", 0xDD), ch("\\", "|", "\\", "/", 0xDC, 1.5)],
    [("action", "caps", "Caps Lock", 1.8)] + letters("asdfghjkl", "фывапролд") +
    [ch(";", ":", "ж", "Ж", 0xBA), ch("'", '"', "э", "Э", 0xDE), ("special", "enter", "Enter", 2.2)],
    [("mod", "shift", "Shift", 2.4)] + letters("zxcvbnm", "ячсмить") +
    [ch(",", "<", "б", "Б", 0xBC), ch(".", ">", "ю", "Ю", 0xBE), ch("/", "?", ".", ",", 0xBF),
     ("mod", "shift", "Shift", 2.6)],
    [("mod", "ctrl", "Ctrl", 1.3), ("mod", "win", "Win", 1.2), ("mod", "alt", "Alt", 1.2),
     ("action", "lang", "RU", 1.3), ("special", "space", "", 5),
     ("special", "left", "←", 1), ("special", "up", "↑", 1), ("special", "down", "↓", 1),
     ("special", "right", "→", 1), ("special", "delete", "Del", 1)],
]

ACTION_SHORTCUTS = {"copy": "C", "cut": "X", "paste": "V", "selall": "A", "undo": "Z"}


def resolve_char(spec, lang, shift, caps):
    en, en_s, ru, ru_s, _vk = spec
    base, shifted = (ru, ru_s) if lang == "ru" else (en, en_s)
    if caps and base.isalpha():
        shift = not shift
    return shifted if shift else base


# ----------------------------------------------------------------------------
# Настройки
# ----------------------------------------------------------------------------
def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def blend_hex(c1, c2, ratio=0.5):
    try:
        a = [int(c1.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
        b = [int(c2.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
        rgb = [int(x + (y - x) * ratio) for x, y in zip(a, b)]
        return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
    except Exception:
        return c2


def load_settings():
    cfg = load_json(CONFIG_PATH, {})
    c = cfg.get("цвета") or cfg.get("theme") or {}
    s = cfg.get("размеры") or cfg.get("layout") or {}
    k = cfg.get("клавиатура") or {}
    b = cfg.get("буфер_обмена") or {}

    def g(d, ru, en, default):
        return d.get(ru, d.get(en, default))

    hist_font = g(s, "размер_шрифта_буфера", "history_font_size", None)
    if hist_font is None:
        hist_font = g(b, "размер_шрифта_буфера", "font_size", 17)

    return {
        "bg": g(c, "фон_страницы", "bg", "#0d1b2a"),
        "key_bg": g(c, "фон_кнопки", "key_bg", "#1b2d45"),
        "key_text": g(c, "текст_кнопки", "key_text", "#e8f1ff"),
        "key_border": g(c, "рамка_кнопки", "key_border", "#2c4669"),
        "key_shadow": g(c, "тень_кнопки", "key_shadow", "#091320"),
        "accent": g(c, "цвет_акцента_энтер", "accent", "#3b82f6"),
        "active": g(c, "фон_при_нажатии", "active", "#28456c"),
        "key_w": g(s, "ширина_кнопки_пиксели", "key_size", 52),
        "key_h": g(s, "высота_кнопки_пиксели", "key_height", 50),
        "gap": g(s, "расстояние_между_кнопками", "gap", 6),
        "font": g(s, "размер_шрифта", "font_size", 20),
        "hist_font": hist_font,
        "font_bold": bool(g(s, "жирный_шрифт", "font_bold", True)),
        "lang": str(k.get("раскладка_при_запуске", "ru")).lower(),
        "alpha": float(k.get("прозрачность", 1.0)),
        "history_size": int(b.get("размер_истории", 15)),
        "history_open": bool(b.get("показывать_историю_при_запуске", False)),
    }


# ----------------------------------------------------------------------------
# Приложение
# ----------------------------------------------------------------------------
class App:
    MIN_ZOOM = 0.6
    MAX_ZOOM = 2.2

    def __init__(self):
        self.cfg = load_settings()
        self.backend = WinBackend() if IS_WIN else PreviewBackend()
        if IS_WIN:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                try:
                    ctypes.windll.user32.SetProcessDPIAware()
                except Exception:
                    pass

        self.root = tk.Tk()
        self.root.title("Pico Keyboard")
        self.root.overrideredirect(True)
        self.root.configure(bg=self.cfg["key_border"])
        try:
            self.root.attributes("-alpha", max(0.3, min(1.0, self.cfg["alpha"])))
        except tk.TclError:
            pass

        st = load_json(STATE_PATH, {})
        self.saved_state = st
        try:
            z = float(st.get("zoom", 1.0))
        except (TypeError, ValueError):
            z = 1.0
        self.zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, z))
        self.dpi_scale = self.root.winfo_fpixels("1i") / 96.0
        self.calc_metrics()

        self.lang = "en" if self.cfg["lang"].startswith("en") else "ru"
        self.caps = False
        self.mods = {"shift": 0, "ctrl": 0, "alt": 0, "win": 0}  # 0 выкл, 1 разово, 2 закреплён
        self.keys = []
        self.row_frames = []
        self.bar_btns = []
        self.repeat_job = None
        self.pinned = [x for x in st.get("pinned", []) if isinstance(x, str) and x.strip()]
        self.history = []
        self.last_seq = None
        self.last_clip = None

        self.build()
        self.refresh()
        self.place_window()
        self.backend.make_no_activate(self.root)
        if self.cfg["history_open"]:
            self.toggle_history()
        self.root.after(300, self.poll_clipboard)

    def calc_metrics(self):
        s = self.dpi_scale * self.zoom
        self.kw = max(24, int(round(self.cfg["key_w"] * s)))
        self.kh = max(24, int(round(self.cfg["key_h"] * s)))
        self.gap = max(2, int(round(self.cfg["gap"] * s)))
        self.shadow_pad = max(2, int(round(3 * s)))
        fs = max(11, int(round(self.cfg["font"] * s)))
        fs_small = max(10, int(round(fs * 0.74)))
        fs_compact = max(9, int(round(fs * 0.64)))
        fs_hist = max(12, int(round(self.cfg["hist_font"] * s)))
        fs_hist_head = max(10, int(round(fs_hist * 0.82)))
        weight = "bold" if self.cfg["font_bold"] else "normal"
        self.font = ("Segoe UI", -fs, weight)
        self.font_small = ("Segoe UI", -fs_small, weight)
        self.font_compact = ("Segoe UI", -fs_compact, weight)
        self.font_hist = ("Segoe UI", -fs_hist, weight)
        self.font_hist_head = ("Segoe UI", -fs_hist_head, weight)

    def _key_width(self, width):
        return int(round(self.kw * width + self.gap * (width - 1))) if width > 1 else int(round(self.kw * width))

    def _key_font(self, font_kind):
        if font_kind == "compact":
            return self.font_compact
        if font_kind == "small":
            return self.font_small
        return self.font

    # --- построение интерфейса -----------------------------------------
    def build(self):
        c = self.cfg
        self.outer = tk.Frame(self.root, bg=c["bg"], padx=self.gap * 2, pady=self.gap)
        self.outer.pack(fill="both", expand=True, padx=1, pady=1)

        self.bar = tk.Frame(self.outer, bg=c["bg"])
        self.bar.pack(side="top", fill="x", pady=(0, self.gap))
        self.title = tk.Label(self.bar, text="⠿  Pico Keyboard", bg=c["bg"], fg=c["accent"],
                              font=self.font_small, cursor="fleur")
        self.title.pack(side="left")
        self.status = tk.Label(self.bar, text="", bg=c["bg"], fg=c["key_text"], font=self.font_small)
        self.status.pack(side="left", padx=12)
        for txt, cmd in (("✕", self.quit), ("—", self.toggle_collapse)):
            b = tk.Label(self.bar, text=txt, bg=c["bg"], fg=c["key_text"], font=self.font_small,
                         padx=8, cursor="hand2")
            b.pack(side="right")
            b.bind("<ButtonPress-1>", lambda e, f=cmd: f())
            self.bar_btns.append(b)
        for w in (self.bar, self.title, self.status):
            w.bind("<ButtonPress-1>", self.drag_start)
            w.bind("<B1-Motion>", self.drag_move)
            w.bind("<ButtonRelease-1>", self.drag_end)

        # Нижняя панель с уголком для изменения размера окна
        self.foot = tk.Frame(self.outer, bg=c["bg"])
        self.foot.pack(side="bottom", fill="x", pady=(2, 0))
        resize_cursor = "size_nw_se" if IS_WIN else "bottom_right_corner"
        self.grip = tk.Canvas(self.foot, width=26, height=20, bg=c["bg"],
                              highlightthickness=0, bd=0)
        try:
            self.grip.configure(cursor=resize_cursor)
        except tk.TclError:
            self.grip.configure(cursor="fleur")
        self.grip.pack(side="right")
        self.draw_grip(False)
        self.zoom_hint = tk.Label(self.foot, text="", bg=c["bg"],
                                  fg=blend_hex(c["bg"], c["key_text"], 0.62),
                                  font=self.font_hist_head)
        self.zoom_hint.pack(side="right", padx=(0, 6))
        self.grip.bind("<Enter>", lambda e: self.draw_grip(True))
        self.grip.bind("<Leave>", lambda e: self.draw_grip(False))
        self.grip.bind("<ButtonPress-1>", self.resize_start)
        self.grip.bind("<B1-Motion>", self.resize_move)
        self.grip.bind("<ButtonRelease-1>", self.resize_end)
        self.grip.bind("<Double-Button-1>", self.resize_reset)

        self.body = tk.Frame(self.outer, bg=c["bg"])
        self.body.pack(side="top")
        for r, row in enumerate(LAYOUT):
            fr = tk.Frame(self.body, bg=c["bg"])
            fr.pack(pady=(0, self.gap * (2 if r == 0 else 1)))
            self.row_frames.append(fr)
            for item in row:
                self.make_key(fr, item)

        self.hist_frame = tk.Frame(self.outer, bg=c["bg"])
        self.hist_visible = False
        self.collapsed = False

    def draw_grip(self, hot=False):
        c = self.cfg
        col = c["accent"] if hot else blend_hex(c["bg"], c["key_text"], 0.6)
        self.grip.delete("all")
        for offset in (4, 10, 16):
            self.grip.create_line(offset, 18, 24, 18 - (24 - offset) * 0.78, fill=col, width=2)

    def make_key(self, parent, item):
        kind, payload = item[0], item[1]
        width = item[-1]
        label = item[2] if kind != "char" else ""
        c = self.cfg
        w = self._key_width(width)
        frame = tk.Frame(parent, width=w, height=self.kh, bg=c["key_shadow"],
                         highlightthickness=1, highlightbackground=c["key_border"])
        frame.pack_propagate(False)
        frame.pack(side="left", padx=max(1, self.gap // 2))
        if kind in ("action", "mod", "special") and len(label) > width * 4.8:
            font_kind = "compact"
        elif kind in ("action", "mod", "special") and len(label) >= 3:
            font_kind = "small"
        else:
            font_kind = "main"
        lab = tk.Label(frame, text=label, bg=c["key_bg"], fg=c["key_text"],
                       font=self._key_font(font_kind), bd=0, cursor="hand2")
        lab.pack(fill="both", expand=True, pady=(0, self.shadow_pad))
        key = {"kind": kind, "payload": payload, "label": lab, "frame": frame,
               "width": width, "font_kind": font_kind}
        self.keys.append(key)
        for wdg in (frame, lab):
            wdg.bind("<ButtonPress-1>", lambda e, k=key: self.on_press(k))
            wdg.bind("<ButtonRelease-1>", lambda e: self.stop_repeat())

    def apply_zoom(self, new_zoom, redraw_history=True):
        self.zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, new_zoom))
        self.calc_metrics()
        self.outer.configure(padx=self.gap * 2, pady=self.gap)
        self.bar.pack_configure(pady=(0, self.gap))
        self.title.configure(font=self.font_small)
        self.status.configure(font=self.font_small)
        self.zoom_hint.configure(font=self.font_hist_head)
        for b in self.bar_btns:
            b.configure(font=self.font_small)
        for r, fr in enumerate(self.row_frames):
            fr.pack_configure(pady=(0, self.gap * (2 if r == 0 else 1)))
        pad_x = max(1, self.gap // 2)
        for k in self.keys:
            k["frame"].configure(width=self._key_width(k["width"]), height=self.kh)
            k["frame"].pack_configure(padx=pad_x)
            k["label"].configure(font=self._key_font(k["font_kind"]))
            k["label"].pack_configure(pady=(0, self.shadow_pad))
        if redraw_history and self.hist_visible:
            self.render_history()

    # --- состояние и подписи --------------------------------------------
    def refresh(self):
        c = self.cfg
        shift = self.mods["shift"] > 0
        for k in self.keys:
            kind, p, lab = k["kind"], k["payload"], k["label"]
            bg, fg = c["key_bg"], c["key_text"]
            if kind == "char":
                lab.configure(text=resolve_char(p, self.lang, shift, self.caps))
            elif kind == "mod":
                st = self.mods[p]
                if st == 1:
                    bg, fg = c["accent"], "white"
                elif st == 2:
                    bg, fg = c["key_text"], c["bg"]
            elif kind == "action" and p == "caps" and self.caps:
                bg, fg = c["accent"], "white"
            elif kind == "action" and p == "lang":
                lab.configure(text=self.lang.upper())
            elif kind == "action" and p == "history" and self.hist_visible:
                bg, fg = c["active"], c["key_text"]
            elif kind == "special" and p == "enter":
                bg, fg = c["accent"], "white"
            lab.configure(bg=bg, fg=fg)
            k["base_bg"] = bg
        parts = [self.lang.upper()]
        if self.caps:
            parts.append("Caps")
        names = {"shift": "Shift", "ctrl": "Ctrl", "alt": "Alt", "win": "Win"}
        for m, st in self.mods.items():
            if st:
                parts.append(names[m] + (" 🔒" if st == 2 else ""))
        self.status.configure(text=" · ".join(parts))

    def flash(self, key):
        key["label"].configure(bg=self.cfg["active"])
        self.root.after(120, lambda: key["label"].configure(bg=key.get("base_bg", self.cfg["key_bg"])))

    # --- нажатия ----------------------------------------------------------
    def on_press(self, key):
        kind, p = key["kind"], key["payload"]
        if kind == "mod":
            self.click_mod(p)
            return
        if kind == "action":
            self.flash(key)
            self.do_action(p)
            return
        self.flash(key)
        self.type_key(key)
        self.release_once_mods()
        self.stop_repeat()
        if not (kind == "special" and p in ("esc", "enter", "tab")):
            self.repeat_job = self.root.after(450, lambda: self.repeat(key))

    def repeat(self, key):
        self.type_key(key)
        self.repeat_job = self.root.after(45, lambda: self.repeat(key))

    def stop_repeat(self):
        if self.repeat_job:
            self.root.after_cancel(self.repeat_job)
            self.repeat_job = None

    def active_mod_vks(self, include_shift=True):
        order = ("ctrl", "alt", "win", "shift") if include_shift else ("ctrl", "alt", "win")
        return [VK[m] for m in order if self.mods[m]]

    def type_key(self, key):
        kind, p = key["kind"], key["payload"]
        if kind == "char":
            if any(self.mods[m] for m in ("ctrl", "alt", "win")):
                self.backend.combo(self.active_mod_vks(), p[4])
            else:
                self.backend.text(resolve_char(p, self.lang, self.mods["shift"] > 0, self.caps))
        elif kind == "special":
            self.backend.combo(self.active_mod_vks(), VK[p])

    def click_mod(self, name):
        st = self.mods[name]
        if name == "win":
            # Win: 1-й клик — ждёт следующую клавишу; 2-й клик — просто Win (меню Пуск)
            if st == 0:
                self.mods[name] = 1
            else:
                self.mods[name] = 0
                self.backend.combo([], VK["win"])
        else:
            # 1-й клик — на одну клавишу, 2-й — закрепить, 3-й — выключить
            self.mods[name] = (st + 1) % 3
        self.refresh()

    def release_once_mods(self):
        changed = False
        for m, st in self.mods.items():
            if st == 1:
                self.mods[m] = 0
                changed = True
        if changed:
            self.refresh()

    def do_action(self, name):
        if name == "caps":
            self.caps = not self.caps
        elif name == "lang":
            self.lang = "en" if self.lang == "ru" else "ru"
        elif name == "history":
            self.toggle_history()
        elif name in ACTION_SHORTCUTS:
            self.backend.combo([VK["ctrl"]], ord(ACTION_SHORTCUTS[name]))
        self.refresh()

    # --- буфер обмена -----------------------------------------------------
    def poll_clipboard(self):
        try:
            seq = self.backend.clip_seq()
            if seq is None or seq != self.last_seq:
                self.last_seq = seq
                txt = self.backend.clip_get()
                if txt and txt.strip() and txt != self.last_clip:
                    self.last_clip = txt
                    self.add_history(txt)
        except Exception:
            pass
        self.root.after(400, self.poll_clipboard)

    def add_history(self, txt):
        txt = txt[:100000]
        if txt in self.pinned:
            return
        if txt in self.history:
            self.history.remove(txt)
        self.history.insert(0, txt)
        del self.history[max(1, self.cfg["history_size"]):]
        if self.hist_visible:
            self.render_history()

    def toggle_pin(self, txt):
        if txt in self.pinned:
            self.pinned.remove(txt)
            if txt in self.history:
                self.history.remove(txt)
            self.history.insert(0, txt)
            del self.history[max(1, self.cfg["history_size"]):]
        else:
            if txt in self.history:
                self.history.remove(txt)
            self.pinned.insert(0, txt)
        self.save_state()
        self.render_history()

    def paste_from_history(self, txt):
        if self.backend.clip_set(txt):
            self.last_clip = txt
            self.add_history(txt)
            self.root.after(60, lambda: self.backend.combo([VK["ctrl"]], ord("V")))

    def remove_history(self, txt):
        if txt in self.pinned:
            self.pinned.remove(txt)
            self.save_state()
        if txt in self.history:
            self.history.remove(txt)
        self.render_history()

    def clear_history(self):
        self.history.clear()
        self.render_history()

    def toggle_history(self):
        self.hist_visible = not self.hist_visible
        if self.hist_visible:
            self.hist_frame.pack(side="top", fill="x", pady=(self.gap, 0))
            self.render_history()
        else:
            self.hist_frame.pack_forget()
        self.refresh()

    def _make_pin_label(self, parent, bg, fg, pad_x, pad_y):
        for glyph in ("📌", "★"):
            try:
                return tk.Label(parent, text=glyph, bg=bg, fg=fg,
                                font=self.font_hist, padx=pad_x, pady=pad_y, cursor="hand2")
            except tk.TclError:
                continue
        return tk.Label(parent, text="*", bg=bg, fg=fg,
                        font=self.font_hist, padx=pad_x, pady=pad_y, cursor="hand2")

    def render_history(self):
        c = self.cfg
        muted = blend_hex(c["bg"], c["key_text"], 0.58)
        pinned_bg = blend_hex(c["key_bg"], c["accent"], 0.16)
        for w in self.hist_frame.winfo_children():
            w.destroy()

        head = tk.Frame(self.hist_frame, bg=c["bg"])
        head.pack(fill="x", pady=(0, 4))
        tk.Label(head, text="История буфера · клик — вставить · 📌 — закрепить · ПКМ — удалить",
                 bg=c["bg"], fg=c["accent"], font=self.font_hist_head).pack(side="left")
        clr = tk.Label(head, text="Очистить", bg=c["key_bg"], fg=c["key_text"],
                       font=self.font_hist_head, cursor="hand2", padx=10, pady=2,
                       highlightthickness=1, highlightbackground=c["key_border"])
        clr.pack(side="right")
        clr.bind("<ButtonPress-1>", lambda e: self.clear_history())
        clr.bind("<Enter>", lambda e: clr.configure(bg=c["active"]))
        clr.bind("<Leave>", lambda e: clr.configure(bg=c["key_bg"]))

        items = [(t, True) for t in self.pinned] + [(t, False) for t in self.history]
        if not items:
            tk.Label(self.hist_frame, text="Пока пусто — скопируйте что-нибудь",
                     bg=c["bg"], fg=muted, font=self.font_hist).pack(anchor="w", pady=6)
            return

        pad_x = max(8, int(round(10 * self.dpi_scale * self.zoom)))
        pad_y = max(4, int(round(6 * self.dpi_scale * self.zoom)))
        est_row_h = max(34, int(round((self.cfg["hist_font"] + 18) * self.dpi_scale * self.zoom)))
        max_visible = 6

        canvas = None
        if len(items) > max_visible:
            wrap = tk.Frame(self.hist_frame, bg=c["bg"])
            wrap.pack(fill="x")
            canvas = tk.Canvas(wrap, bg=c["bg"], height=est_row_h * max_visible,
                               highlightthickness=0, bd=0)
            sb = tk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
            inner = tk.Frame(canvas, bg=c["bg"])
            win_id = canvas.create_window((0, 0), window=inner, anchor="nw")
            canvas.configure(yscrollcommand=sb.set)
            canvas.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
            canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win_id, width=e.width))
        else:
            inner = tk.Frame(self.hist_frame, bg=c["bg"])
            inner.pack(fill="x")

        def on_wheel(e):
            if canvas is not None:
                delta = -1 if e.delta > 0 else 1
                canvas.yview_scroll(delta, "units")

        body_w = self.body.winfo_reqwidth() or self._key_width(15)
        hist_px = max(12, abs(self.font_hist[1]))
        max_chars = max(24, int((body_w - 130) / (hist_px * 0.68)))

        for i, (txt, is_pinned) in enumerate(items):
            one_line = " ".join(txt.split())
            if len(one_line) > max_chars:
                one_line = one_line[:max_chars - 1].rstrip() + "…"
            row_bg = pinned_bg if is_pinned else c["key_bg"]
            border_col = c["accent"] if is_pinned else c["key_border"]

            row = tk.Frame(inner, bg=row_bg, highlightthickness=1, highlightbackground=border_col)
            row.pack(fill="x", pady=2)

            item = tk.Label(row, text=f"{i + 1}.  {one_line}", anchor="w", width=1,
                            bg=row_bg, fg=c["key_text"], font=self.font_hist,
                            padx=pad_x, pady=pad_y, cursor="hand2")
            item.pack(side="left", fill="both", expand=True)

            pin_bg = c["accent"] if is_pinned else row_bg
            pin_fg = "white" if is_pinned else muted
            pin = self._make_pin_label(row, pin_bg, pin_fg, pad_x, pad_y)
            pin.pack(side="right", fill="y")

            item.bind("<ButtonPress-1>", lambda e, t=txt: self.paste_from_history(t))
            item.bind("<ButtonPress-3>", lambda e, t=txt: self.remove_history(t))
            item.bind("<Enter>", lambda e, w=item: w.configure(bg=c["active"]))
            item.bind("<Leave>", lambda e, w=item, b=row_bg: w.configure(bg=b))

            pin.bind("<ButtonPress-1>", lambda e, t=txt: self.toggle_pin(t))
            if not is_pinned:
                pin.bind("<Enter>", lambda e, w=pin: w.configure(bg=c["active"], fg=c["key_text"]))
                pin.bind("<Leave>", lambda e, w=pin, b=row_bg: w.configure(bg=b, fg=muted))
            else:
                pin.bind("<Enter>", lambda e, w=pin: w.configure(bg=blend_hex(c["accent"], "#ffffff", 0.15)))
                pin.bind("<Leave>", lambda e, w=pin: w.configure(bg=c["accent"]))

            if canvas is not None:
                for wdg in (row, item, pin):
                    wdg.bind("<MouseWheel>", on_wheel)
                    wdg.bind("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))
                    wdg.bind("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))

    # --- окно и изменение размера ----------------------------------------
    def toggle_collapse(self):
        self.collapsed = not self.collapsed
        if self.collapsed:
            self.body.pack_forget()
            self.hist_frame.pack_forget()
            self.foot.pack_forget()
        else:
            self.foot.pack(side="bottom", fill="x", pady=(2, 0))
            self.body.pack(side="top")
            if self.hist_visible:
                self.hist_frame.pack(side="top", fill="x", pady=(self.gap, 0))

    def place_window(self):
        self.root.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w, h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        st = self.saved_state
        x, y = st.get("x"), st.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            x, y = (sw - w) // 2, sh - h - 60
        x = max(0, min(x, sw - 100))
        y = max(0, min(y, sh - 40))
        self.root.geometry(f"+{x}+{y}")

    def save_state(self):
        try:
            data = {
                "x": self.root.winfo_x(),
                "y": self.root.winfo_y(),
                "zoom": round(self.zoom, 3),
                "pinned": list(self.pinned),
            }
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass

    def drag_start(self, e):
        self._dx = e.x_root - self.root.winfo_x()
        self._dy = e.y_root - self.root.winfo_y()

    def drag_move(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def drag_end(self, e):
        self.save_state()

    def resize_start(self, e):
        self._rx = e.x_root
        self._ry = e.y_root
        self._r_zoom = self.zoom
        self._r_w = max(200, self.body.winfo_width())
        self._r_h = max(100, self.body.winfo_height())
        self.zoom_hint.configure(text=f"{int(round(self.zoom * 100))}%")

    def resize_move(self, e):
        dx = e.x_root - self._rx
        dy = e.y_root - self._ry
        sx = (self._r_w + dx) / self._r_w
        sy = (self._r_h + dy) / self._r_h
        ratio = sx if abs(sx - 1.0) >= abs(sy - 1.0) else sy
        new_zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, self._r_zoom * ratio))
        if abs(new_zoom - self.zoom) >= 0.025:
            self.apply_zoom(new_zoom, redraw_history=False)
            self.zoom_hint.configure(text=f"{int(round(self.zoom * 100))}%")

    def resize_end(self, e):
        self.zoom_hint.configure(text="")
        if self.hist_visible:
            self.render_history()
        self.save_state()

    def resize_reset(self, e):
        self.apply_zoom(1.0, redraw_history=True)
        self.zoom_hint.configure(text="")
        self.save_state()

    def quit(self):
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
