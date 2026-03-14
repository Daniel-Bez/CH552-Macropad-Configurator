import copy
import ctypes
import json
import os
import keyboard
import pystray
from PIL import Image, ImageDraw
import threading
import tkinter as tk
from tkinter import colorchooser, messagebox, simpledialog


# f13-f15 = 3 keys, f16 = knob click, f17/f18 = knob CCW/CW
# my pad sends these by default, yours might differ
KEY_CODES  = ["f13", "f14", "f15"]
KEY_NAMES  = {"f13": "KEY 1", "f14": "KEY 2", "f15": "KEY 3"}
KNOB_CODES = ["f17", "f18"]
KNOB_NAMES = {"f17": "↺  CCW", "f18": "↻  CW"}
ALL_CODES  = KEY_CODES + KNOB_CODES

CONFIG_FILE = "macropad_config.json"
DEFAULT_CONFIG = {
    "current_layer": 0,
    "layers": [
        {"name": "Desktop", "color": "#4f9eff",
         "f13": "ctrl+c", "f14": "ctrl+v", "f15": "win+d",
         "f17": "volume down", "f18": "volume up"},
        {"name": "Media", "color": "#2ecc71",
         "f13": "prev track", "f14": "play/pause", "f15": "next track",
         "f17": "volume down", "f18": "volume up"},
        {"name": "Gaming", "color": "#e74c3c",
         "f13": "1", "f14": "2", "f15": "3",
         "f17": "scroll down", "f18": "scroll up"},
    ],
}

# what the legend popup shows
LEGEND = [
    ("MEDIA & VOLUME", [
        ("mute",           "Toggle system mute"),
        ("volume up",      "Raise volume"),
        ("volume down",    "Lower volume"),
        ("play/pause",     "Play or pause media"),
        ("next track",     "Skip to next track"),
        ("prev track",     "Go to previous track"),
    ]),
    ("MOUSE SCROLL", [
        ("scroll up",      "Scroll mouse wheel up"),
        ("scroll down",    "Scroll mouse wheel down"),
        ("wheel up",       "Same as scroll up"),
        ("wheel down",     "Same as scroll down"),
    ]),
    ("TYPE LITERAL TEXT", [
        ("type: Hello!",   "Types any text after 'type: '"),
    ]),
    ("KEYBOARD COMBOS  (examples)", [
        ("ctrl+c",         "Copy"),
        ("ctrl+v",         "Paste"),
        ("ctrl+z",         "Undo"),
        ("ctrl+shift+t",   "Reopen closed tab"),
        ("alt+tab",        "Switch window"),
        ("win+d",          "Show desktop"),
        ("win+l",          "Lock screen"),
        ("f5",             "Refresh"),
        ("printscreen",    "Screenshot"),
        ("alt+f4",         "Close window"),
    ]),
]

# colors
C_BG      = "#0f0f13"
C_SURFACE = "#16161f"
C_CARD    = "#1e1e2b"
C_BORDER  = "#2a2a3a"
C_TEXT    = "#e0e0f0"
C_MUTED   = "#8888a8"
C_HINT    = "#4a4a62"
C_LEGEND  = "#141420"
C_WHITE   = "#ffffff"

FN_LABEL = ("Consolas", 10, "bold")
FN_SIDE  = ("Consolas", 11, "bold")
FN_SUB   = ("Consolas", 9)


def _do_scroll(direction):
    delta = 120 if direction == "up" else -120
    ctypes.windll.user32.mouse_event(0x0800, 0, 0, ctypes.c_uint32(delta).value, 0)


def execute_action(action):
    if not action:
        return

    low = action.strip().lower()

    # handle type: prefix first
    if low.startswith("type:"):
        try:
            keyboard.write(action.split(":", 1)[1])
        except Exception as e:
            print("type error:", e)
        return

    # media keys
    if low in ("mute", "volume mute"):
        keyboard.send("volume mute")
    elif low == "volume up":
        keyboard.send("volume up")
    elif low == "volume down":
        keyboard.send("volume down")
    elif low in ("play/pause", "play/pause media"):
        keyboard.send("play/pause media")
    elif low == "next track":
        keyboard.send("next track")
    elif low in ("prev track", "previous track"):
        keyboard.send("previous track")
    # scroll wheel
    elif low in ("scroll up", "wheel up"):
        _do_scroll("up")
    elif low in ("scroll down", "wheel down"):
        _do_scroll("down")
    else:
        # assume it's a hotkey combo like ctrl+c, alt+tab etc
        try:
            keyboard.send(action.strip())
        except Exception as e:
            print("keyboard error:", e)


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                data = json.load(f)
            # basic sanity check
            if "layers" in data and "current_layer" in data:
                return data
        except Exception:
            pass
    return copy.deepcopy(DEFAULT_CONFIG)


def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def _make_tray_img(hex_color):
    # PIL wants an RGB tuple, not a hex string
    h = hex_color.lstrip("#")
    rgb = tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=rgb, outline=(255, 255, 255, 180), width=3)
    return img


class MacroPadApp:

    def __init__(self):
        self.cfg = load_config()
        self.tray = None
        self._kb_stop = threading.Event()
        self._build_window()
        self._build_ui()
        self._refresh()
        self._start_keyboard()
        self._start_tray()
        self.root.mainloop()


    def _idx(self):
        return self.cfg["current_layer"]

    def _layer(self, i=None):
        return self.cfg["layers"][self._idx() if i is None else i]

    def _set_layer(self, i):
        self.cfg["current_layer"] = max(0, min(i, len(self.cfg["layers"]) - 1))
        save_config(self.cfg)
        self._refresh()

    def _cycle_layer(self):
        self._set_layer((self._idx() + 1) % len(self.cfg["layers"]))

    def _build_window(self):
        self.root = tk.Tk()
        self.root.title("CH552-Macropad-Configurator")
        self.root.geometry("")
        self.root.minsize(900, 600)
        self.root.configure(bg=C_BG)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        if self.tray:
            self.root.withdraw()
        else:
            self._quit()

    def _quit(self):
        self._kb_stop.set()
        if self.tray:
            try: self.tray.stop()
            except Exception: pass
        self.root.destroy()

    def _build_ui(self):
        self.sidebar = tk.Frame(self.root, bg=C_SURFACE, width=190)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
        self.sidebar.pack_propagate(False)

        tk.Label(self.sidebar, text="PROFILES", font=FN_LABEL, fg=C_MUTED, bg=C_SURFACE)\
          .pack(pady=(16, 4), padx=14, anchor="w")

        self.sidebar_list = tk.Frame(self.sidebar, bg=C_SURFACE)
        self.sidebar_list.pack(fill=tk.BOTH, expand=True, padx=6)

        tk.Button(self.sidebar, text="＋  Add Profile", font=FN_LABEL, fg=C_MUTED,
                  bg=C_SURFACE, relief="flat", cursor="hand2",
                  activebackground=C_CARD, activeforeground=C_TEXT,
                  bd=0, command=self._add_profile)\
          .pack(side=tk.BOTTOM, fill=tk.X, padx=6, pady=8, ipady=7)

        self.main = tk.Frame(self.root, bg=C_BG)
        self.main.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # header row: title on left, buttons on right
        hrow = tk.Frame(self.main, bg=C_BG)
        hrow.pack(fill=tk.X, padx=22, pady=(18, 0))

        self.lbl_name = tk.Label(hrow, text="", font=("Consolas", 22, "bold"),
                                 fg=C_WHITE, bg=C_BG)
        self.lbl_name.pack(side=tk.LEFT)

        ctrl = tk.Frame(hrow, bg=C_BG)
        ctrl.pack(side=tk.RIGHT)

        tk.Button(ctrl, text="?", font=("Consolas", 12, "bold"), fg=C_MUTED, bg=C_BG,
                  relief="flat", cursor="hand2", activeforeground=C_TEXT,
                  activebackground=C_BG, bd=0, width=2, command=self._show_legend)\
          .pack(side=tk.LEFT, padx=6)

        self.swatch = tk.Button(ctrl, text="⬤", font=("Consolas", 17), fg=C_WHITE,
                                bg=C_BG, relief="flat", cursor="hand2", bd=0,
                                command=self._pick_color)
        self.swatch.pack(side=tk.LEFT, padx=4)

        for lbl, cmd in [("rename", self._rename_profile), ("delete", self._delete_profile)]:
            tk.Button(ctrl, text=lbl, font=FN_LABEL,
                      fg=C_MUTED if lbl == "rename" else "#c0392b",
                      bg=C_BG, relief="flat", cursor="hand2",
                      activeforeground=C_TEXT, activebackground=C_BG,
                      bd=0, command=cmd).pack(side=tk.LEFT, padx=5)

        self.accent = tk.Frame(self.main, height=3, bg=C_BORDER)
        self.accent.pack(fill=tk.X, padx=22, pady=(8, 20))

        self._card_refs  = {}
        self._macro_lbls = {}

        controls_row = tk.Frame(self.main, bg=C_BG)
        controls_row.pack(expand=True)

        keys_frame = tk.Frame(controls_row, bg=C_BG)
        keys_frame.pack(side=tk.LEFT, padx=(0, 28))

        for code in KEY_CODES:
            self._build_key_card(code, keys_frame)

        knob_cluster = tk.Frame(controls_row, bg=C_BG)
        knob_cluster.pack(side=tk.LEFT)

        knob_top = tk.Frame(knob_cluster, bg=C_BG)
        knob_top.pack()
        for code in KNOB_CODES:
            self._build_knob_mini(code, knob_top)

        self._build_knob_circle(knob_cluster)

        tk.Label(self.main,
                 text="F16 Knob Click = Cycle Profile   •   Click any card to remap   •   ? for key reference",
                 font=FN_LABEL, fg=C_MUTED, bg=C_BG)\
          .pack(side=tk.BOTTOM, pady=12)

    def _build_key_card(self, code, parent):
        col = tk.Frame(parent, bg=C_BG)
        col.pack(side=tk.LEFT, padx=10)

        border = tk.Frame(col, bg=C_BORDER, padx=2, pady=2)
        border.pack()

        inner = tk.Frame(border, bg=C_CARD, width=140, height=118)
        inner.pack_propagate(False)
        inner.pack()

        name_lbl = tk.Label(inner, text=KEY_NAMES[code], font=FN_LABEL,
                            fg=C_MUTED, bg=C_CARD)
        name_lbl.pack(pady=(13, 2))

        macro_lbl = tk.Label(inner, text="—", font=("Consolas", 11),
                             fg=C_TEXT, bg=C_CARD, wraplength=124)
        macro_lbl.pack(expand=True)

        hint = tk.Label(inner, text="click to set", font=FN_SUB, fg=C_HINT, bg=C_CARD)
        hint.pack(pady=(0, 9))

        for w in (border, inner, name_lbl, macro_lbl, hint):
            w.bind("<Button-1>", lambda e, c=code: self._configure_key(c))
            w.configure(cursor="hand2")
            w.bind("<Enter>", lambda e, f=inner: f.config(bg="#252535"))
            w.bind("<Leave>", lambda e, f=inner: f.config(bg=C_CARD))

        self._card_refs[code]  = (border, inner)
        self._macro_lbls[code] = macro_lbl

    def _build_knob_mini(self, code, parent):
        col = tk.Frame(parent, bg=C_BG)
        col.pack(side=tk.LEFT, padx=5)

        border = tk.Frame(col, bg=C_BORDER, padx=2, pady=2)
        border.pack()

        inner = tk.Frame(border, bg=C_CARD, width=100, height=82)
        inner.pack_propagate(False)
        inner.pack()

        name_lbl = tk.Label(inner, text=KNOB_NAMES[code], font=FN_LABEL,
                            fg=C_MUTED, bg=C_CARD)
        name_lbl.pack(pady=(10, 2))

        macro_lbl = tk.Label(inner, text="—", font=("Consolas", 9),
                             fg=C_TEXT, bg=C_CARD, wraplength=86)
        macro_lbl.pack(expand=True)

        hint = tk.Label(inner, text="click to set", font=("Consolas", 7),
                        fg=C_HINT, bg=C_CARD)
        hint.pack(pady=(0, 6))

        for w in (border, inner, name_lbl, macro_lbl, hint):
            w.bind("<Button-1>", lambda e, c=code: self._configure_key(c))
            w.configure(cursor="hand2")
            w.bind("<Enter>", lambda e, f=inner: f.config(bg="#252535"))
            w.bind("<Leave>", lambda e, f=inner: f.config(bg=C_CARD))

        self._card_refs[code]  = (border, inner)
        self._macro_lbls[code] = macro_lbl

    def _build_knob_circle(self, parent):
        size = 96
        self._knob_canvas = tk.Canvas(parent, width=size, height=size,
                                      bg=C_BG, highlightthickness=0)
        self._knob_canvas.pack(pady=(8, 0))

        pad = 6
        self._knob_oval = self._knob_canvas.create_oval(
            pad, pad, size - pad, size - pad,
            fill=C_CARD, outline=C_BORDER, width=2
        )
        self._knob_canvas.create_text(size // 2, size // 2 - 7,
                                      text="KNOB", fill=C_MUTED,
                                      font=("Consolas", 9, "bold"))
        self._knob_canvas.create_text(size // 2, size // 2 + 9,
                                      text="click", fill=C_HINT,
                                      font=("Consolas", 7))
        self._knob_canvas.bind("<Button-1>", lambda e: self._cycle_layer())
        self._knob_canvas.configure(cursor="hand2")

    def _flash_knob(self, color):
        self._knob_canvas.itemconfig(self._knob_oval, fill=color, outline=color)
        self.root.after(260, lambda: self._knob_canvas.itemconfig(
            self._knob_oval, fill=C_CARD, outline=C_BORDER))

    def _update_knob_color(self, color):
        self._knob_canvas.itemconfig(self._knob_oval, outline=color, width=2)

    def _refresh(self):
        layer = self._layer()
        color = layer.get("color", C_MUTED)

        self.lbl_name.config(text=layer["name"])
        self.accent.config(bg=color)
        self.swatch.config(fg=color)
        self._update_knob_color(color)

        for code in ALL_CODES:
            val = layer.get(code, "")
            self._macro_lbls[code].config(text=val if val else "—")

        self._rebuild_sidebar()
        self._update_tray(color, layer["name"])

    def _rebuild_sidebar(self):
        for w in self.sidebar_list.winfo_children():
            w.destroy()

        active = self._idx()
        for i, layer in enumerate(self.cfg["layers"]):
            is_active = (i == active)
            color  = layer.get("color", C_MUTED)
            row_bg = C_CARD if is_active else C_SURFACE

            row = tk.Frame(self.sidebar_list, bg=row_bg, cursor="hand2")
            row.pack(fill=tk.X, pady=2)
            row.bind("<Button-1>", lambda e, idx=i: self._set_layer(idx))

            stripe = tk.Frame(row, width=4, bg=color if is_active else row_bg)
            stripe.pack(side=tk.LEFT, fill=tk.Y)
            stripe.bind("<Button-1>", lambda e, idx=i: self._set_layer(idx))

            txt = tk.Frame(row, bg=row_bg)
            txt.pack(side=tk.LEFT, padx=10, pady=9, fill=tk.X, expand=True)
            txt.bind("<Button-1>", lambda e, idx=i: self._set_layer(idx))

            mapped = sum(1 for k in ALL_CODES if layer.get(k, ""))
            for text, font, fg in [
                (layer["name"], FN_SIDE, color if is_active else C_TEXT),
                (f"{mapped}/{len(ALL_CODES)} mapped", FN_SUB, C_MUTED),
            ]:
                lbl = tk.Label(txt, text=text, font=font, fg=fg, bg=row_bg, anchor="w")
                lbl.pack(fill=tk.X)
                lbl.bind("<Button-1>", lambda e, idx=i: self._set_layer(idx))

    def _add_profile(self):
        name = simpledialog.askstring("New Profile", "Profile name:", parent=self.root)
        if not name or not name.strip():
            return
        self.cfg["layers"].append({
            "name": name.strip(), "color": "#9b59b6",
            **{c: "" for c in ALL_CODES},
        })
        self.cfg["current_layer"] = len(self.cfg["layers"]) - 1
        save_config(self.cfg)
        self._refresh()

    def _rename_profile(self):
        old  = self._layer()["name"]
        name = simpledialog.askstring("Rename Profile", "New name:",
                                      initialvalue=old, parent=self.root)
        if name and name.strip():
            self._layer()["name"] = name.strip()
            save_config(self.cfg)
            self._refresh()

    def _delete_profile(self):
        if len(self.cfg["layers"]) <= 1:
            messagebox.showwarning("Can't Delete", "You need at least one profile.",
                                   parent=self.root)
            return
        if not messagebox.askyesno("Delete Profile",
                                   f'Delete "{self._layer()["name"]}"?',
                                   parent=self.root):
            return
        idx = self._idx()
        self.cfg["layers"].pop(idx)
        self.cfg["current_layer"] = max(0, idx - 1)
        save_config(self.cfg)
        self._refresh()

    def _pick_color(self):
        cur = self._layer().get("color", "#4f9eff")
        _, hex_c = colorchooser.askcolor(color=cur, parent=self.root,
                                         title="Pick Profile Color")
        if hex_c:
            self._layer()["color"] = hex_c
            save_config(self.cfg)
            self._refresh()

    def _configure_key(self, code):
        cur   = self._layer().get(code, "")
        label = KNOB_NAMES.get(code) or KEY_NAMES.get(code, code.upper())
        val   = simpledialog.askstring(
            f"Map {label}",
            f'Enter action for {label}:\n'
            f'  Media:   mute | volume up | volume down | play/pause | next track | prev track\n'
            f'  Scroll:  scroll up | scroll down\n'
            f'  Combo:   ctrl+c | alt+tab | win+d | f5 ...\n'
            f'  Text:    type: Hello World\n'
            f'  (leave blank to clear)  —  press ? for full reference',
            initialvalue=cur, parent=self.root,
        )
        if val is not None:
            self._layer()[code] = val.strip()
            save_config(self.cfg)
            self._refresh()

    def _show_legend(self):
        win = tk.Toplevel(self.root)
        win.title("Key Mapping Reference")
        win.configure(bg=C_LEGEND)
        win.resizable(False, False)
        win.grab_set()

        tk.Label(win, text="KEY MAPPING REFERENCE",
                 font=("Consolas", 13, "bold"), fg=C_WHITE, bg=C_LEGEND)\
          .pack(pady=(18, 2), padx=24)
        tk.Label(win, text="Click any blue action to copy it to clipboard",
                 font=FN_SUB, fg=C_MUTED, bg=C_LEGEND)\
          .pack(pady=(0, 10))

        canvas = tk.Canvas(win, bg=C_LEGEND, highlightthickness=0, width=500, height=420)
        sb = tk.Scrollbar(win, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 6))
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(16, 0))

        inner = tk.Frame(canvas, bg=C_LEGEND)
        cw_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        canvas.bind("<Configure>", lambda e: canvas.itemconfig(cw_id, width=e.width))
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        def _wheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _wheel)
        win.protocol("WM_DELETE_WINDOW",
                     lambda: (canvas.unbind_all("<MouseWheel>"), win.destroy()))

        for section_title, rows in LEGEND:
            hdr = tk.Frame(inner, bg=C_CARD)
            hdr.pack(fill=tk.X, pady=(10, 0), padx=8)
            tk.Label(hdr, text=section_title, font=("Consolas", 10, "bold"),
                     fg=C_MUTED, bg=C_CARD, anchor="w")\
              .pack(fill=tk.X, padx=10, pady=5)

            for action, desc in rows:
                row = tk.Frame(inner, bg=C_LEGEND)
                row.pack(fill=tk.X, padx=8, pady=1)

                chip = tk.Label(row, text=action, font=("Consolas", 10),
                                fg="#a8d8ff", bg=C_CARD,
                                padx=8, pady=3, cursor="hand2", width=20, anchor="w")
                chip.pack(side=tk.LEFT)
                chip.bind("<Button-1>", lambda e, a=action: self._copy_to_clipboard(a))

                tk.Label(row, text=desc, font=("Consolas", 10),
                         fg=C_TEXT, bg=C_LEGEND, anchor="w")\
                  .pack(side=tk.LEFT, padx=10)

        tk.Button(win, text="Close", font=FN_LABEL, fg=C_TEXT, bg=C_CARD,
                  relief="flat", cursor="hand2", activebackground=C_BORDER,
                  command=lambda: (canvas.unbind_all("<MouseWheel>"), win.destroy()))\
          .pack(pady=14, ipadx=20, ipady=6)

    def _copy_to_clipboard(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()

    def _flash(self, code):
        if code == "f16":
            self._flash_knob(self._layer().get("color", "#4f9eff"))
            return
        if code not in self._card_refs:
            return
        color = self._layer().get("color", "#4f9eff")
        border, inner = self._card_refs[code]
        border.config(bg=color)
        inner.config(bg=color)
        for w in inner.winfo_children():
            try: w.config(bg=color)
            except Exception: pass
        self.root.after(260, lambda: self._unflash(code))

    def _unflash(self, code):
        if code not in self._card_refs:
            return
        border, inner = self._card_refs[code]
        border.config(bg=C_BORDER)
        inner.config(bg=C_CARD)
        for w in inner.winfo_children():
            try: w.config(bg=C_CARD)
            except Exception: pass

    def _start_keyboard(self):
        # root.after() is one of the few tkinter calls that's safe from other threads
        # so we just schedule directly instead of messing with a queue
        def _exec(code):
            self.root.after(0, lambda: self._flash(code))
            execute_action(self._layer().get(code, ""))

        def _run():
            for code in ALL_CODES:
                keyboard.add_hotkey(code, lambda c=code: _exec(c), suppress=True)
            # knob click cycles profile
            keyboard.add_hotkey("f16", lambda: self.root.after(0, self._cycle_layer),
                                 suppress=True)
            self._kb_stop.wait()
            keyboard.unhook_all()

        threading.Thread(target=_run, daemon=True).start()

    def _show_window(self):
        self.root.after(0, self.root.deiconify)
        self.root.after(0, self.root.lift)
        self.root.after(0, self.root.focus_force)


    # pystray on Windows has no real double click event, it just fires the
    # default menu item on every single click. worked around it with a timer:
    # track the last click time, and if a second click comes in under 500ms,
    # treat it as a double click. the invisible default MenuItem below is what
    # catches every click without showing up in the right click menu.

    def _start_tray(self):
        import time
        self._tray_last_click = 0
        DOUBLE_CLICK_MS = 500
 
        def _on_tray_click(icon, item):
            now = time.time() * 1000
            delta = now - self._tray_last_click
            if delta < DOUBLE_CLICK_MS:
                self._tray_last_click = 0
                self._show_window()
            else:
                self._tray_last_click = now
 
        img  = _make_tray_img(self._layer().get("color", "#4f9eff"))
        menu = pystray.Menu(
            pystray.MenuItem("Show", lambda icon, item: self._show_window()),
            pystray.MenuItem("Quit", lambda icon, item: self.root.after(0, self._quit)),
            pystray.MenuItem("", _on_tray_click, default=True, visible=False),
        )
        self.tray = pystray.Icon("CH552-Macropad-Configurator", img,"CH552-Macropad-Configurator", menu)
        threading.Thread(target=self.tray.run, daemon=True).start()
 
    def _update_tray(self, color, name):
        if not self.tray:
            return
        try:
            self.tray.icon  = _make_tray_img(color)
            self.tray.title = f"CH552-Macropad: {name}"
        except Exception:
            pass
 
 
if __name__ == "__main__":
    MacroPadApp()
