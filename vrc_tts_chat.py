"""
VRChat Chat + AivisSpeech TTS
-----------------------------
Press Y while VRChat is the focused window -> a small chat box pops up.
Enter sends the text to the VRChat chatbox (OSC) AND speaks it with AivisSpeech.
Esc cancels.

Requires: VRChat OSC enabled (Action Menu > Options > OSC > Enabled),
          AivisSpeech running (local engine, default http://127.0.0.1:10101).
"""
import ctypes
import ctypes.wintypes as wt
import io
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import numpy as np
import requests
import sounddevice as sd
import soundfile as sf
from pythonosc.udp_client import SimpleUDPClient

APP_NAME = "VRChatTTSChat"
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
DEFAULTS = {
    "engine_url": "http://127.0.0.1:10101",
    "speaker_label": "",
    "output_device": "",
    "mic_device": "Off",
    "volume": 100,
    "osc_port": 9000,
    "send_chatbox": True,
    "speak": True,
    "chatbox_sound": False,
    "hotkey_vk": 0x59,  # Y
    "process_name": "VRChat.exe",
}
CHATBOX_LIMIT = 144


# --------------------------------------------------------------------------- config
def load_cfg():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def save_cfg(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# --------------------------------------------------------------------------- win32
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wt.DWORD),
        ("scanCode", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetParent.argtypes = [wt.HWND]
user32.GetParent.restype = wt.HWND
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HMODULE


def foreground_exe():
    """Return (lowercase exe name, hwnd) of the foreground window."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "", None
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return "", hwnd
    buf = ctypes.create_unicode_buffer(1040)
    size = wt.DWORD(1040)
    ok = kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
    kernel32.CloseHandle(h)
    return (os.path.basename(buf.value).lower() if ok else ""), hwnd


def force_focus(hwnd):
    # Tapping Alt lets SetForegroundWindow succeed from a background process.
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 2, 0)
    user32.SetForegroundWindow(hwnd)


HOOK_STATE = {"open": False, "held": False}
_hook_refs = []


def start_hook(vk, process_name, q):
    """Low-level keyboard hook: swallows the hotkey only while VRChat is focused."""
    target_exe = process_name.lower()

    def proc(n, w, l):
        if n == 0:
            k = ctypes.cast(l, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if k.vkCode == vk and not (k.flags & 0x10):  # ignore injected keys
                down = w in (0x100, 0x104)
                if down and not HOOK_STATE["held"]:
                    exe, hwnd = foreground_exe()
                    if not HOOK_STATE["open"] and exe == target_exe:
                        HOOK_STATE["held"] = True
                        q.put(("open", hwnd))
                        return 1
                elif HOOK_STATE["held"]:
                    if not down:
                        HOOK_STATE["held"] = False
                    return 1  # swallow auto-repeat and key-up
        return user32.CallNextHookEx(None, n, w, l)

    cb = HOOKPROC(proc)
    _hook_refs.append(cb)

    def run():
        hm = kernel32.GetModuleHandleW(None)
        user32.SetWindowsHookExW(13, cb, hm, 0)  # WH_KEYBOARD_LL
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass

    threading.Thread(target=run, daemon=True).start()


# --------------------------------------------------------------------------- TTS
class Aivis:
    def __init__(self, url):
        self.url = url.rstrip("/")

    def speakers(self):
        r = requests.get(f"{self.url}/speakers", timeout=5)
        r.raise_for_status()
        out = []
        for sp in r.json():
            for st in sp.get("styles", []):
                out.append((f"{sp['name']} / {st['name']}", st["id"]))
        return out

    def synth(self, text, speaker_id):
        q = requests.post(f"{self.url}/audio_query",
                          params={"text": text, "speaker": speaker_id}, timeout=30)
        q.raise_for_status()
        s = requests.post(f"{self.url}/synthesis", params={"speaker": speaker_id},
                          json=q.json(), timeout=120)
        s.raise_for_status()
        return s.content

    # ---- voice model management (engine's /aivm_models API)
    @staticmethod
    def _check(r):
        if r.ok:
            return
        try:
            d = r.json().get("detail")
            msg = d if isinstance(d, str) else json.dumps(d, ensure_ascii=False)
        except Exception:
            msg = r.text[:200]
        raise RuntimeError(f"{r.status_code}: {msg}")

    def models(self):
        """{uuid: info} of every installed voice model."""
        r = requests.get(f"{self.url}/aivm_models", timeout=15)
        self._check(r)
        return r.json()

    def install_file(self, path):
        with open(path, "rb") as fh:
            r = requests.post(f"{self.url}/aivm_models/install",
                              files={"file": (os.path.basename(path), fh, "application/octet-stream")},
                              timeout=(10, 600))
        self._check(r)

    def install_url(self, url):
        r = requests.post(f"{self.url}/aivm_models/install", data={"url": url}, timeout=(10, 600))
        self._check(r)

    def update_model(self, uuid):
        r = requests.post(f"{self.url}/aivm_models/{uuid}/update", timeout=(10, 600))
        self._check(r)

    def uninstall(self, uuid):
        r = requests.delete(f"{self.url}/aivm_models/{uuid}/uninstall", timeout=60)
        self._check(r)


class Player:
    """Plays queued WAV bytes one after another on the chosen output device."""

    def __init__(self):
        self.q = queue.Queue()
        self.targets = [None]  # device indices to play on at the same time (None = system default)
        self.on_error = None
        self.volume = 1.0  # 0.0 - 1.0, applied to every output
        threading.Thread(target=self._run, daemon=True).start()

    def play(self, wav_bytes):
        self.q.put(wav_bytes)

    def _play_on(self, data, sr, device, errors):
        attempts = []
        if device is not None:
            try:
                api = sd.query_hostapis(sd.query_devices(device)["hostapi"])["name"]
                if "WASAPI" in api:
                    # let Windows convert sample rate / channels to the device format
                    attempts.append((device, sd.WasapiSettings(auto_convert=True)))
            except Exception:
                pass
        attempts.append((device, None))
        last = None
        for dev, extra in attempts:
            try:
                with sd.OutputStream(samplerate=sr, device=dev, channels=data.shape[1],
                                     dtype="float32", extra_settings=extra) as s:
                    s.write(data)
                return
            except Exception as e:
                last = e
        errors.append(last)

    def _run(self):
        while True:
            wav = self.q.get()
            try:
                data, sr = sf.read(io.BytesIO(wav), dtype="float32")
                if data.ndim == 1:
                    data = np.column_stack([data, data])
                data = np.ascontiguousarray(np.clip(data * self.volume, -1.0, 1.0), dtype="float32")
                errors = []
                threads = [threading.Thread(target=self._play_on, args=(data, sr, dev, errors))
                           for dev in list(self.targets)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
                if errors:
                    raise errors[0]
            except Exception as e:
                msg = f"Audio playback failed: {e}"
                print(msg, file=sys.stderr)
                if self.on_error:
                    self.on_error(msg)


def output_devices():
    """List of (label, index) for output devices."""
    # WASAPI only lists devices that are currently enabled and plugged in.
    # The other host APIs (MME, DirectSound, WDM-KS) also list disabled/ghost devices.
    apis = sd.query_hostapis()
    devs = sd.query_devices()

    def collect(match):
        out = []
        for i, d in enumerate(devs):
            api = apis[d["hostapi"]]["name"]
            if d["max_output_channels"] > 0 and match(api):
                out.append((f"{d['name']} ({api})", i))
        return out

    out = collect(lambda a: "WASAPI" in a)
    if not out:  # fallback if WASAPI is unavailable
        out = collect(lambda a: "MME" in a)
    return [("System default", None)] + out


# --------------------------------------------------------------------------- app
# --------------------------------------------------------------------------- cute theme
BG = "#FFF0F6"          # window background (soft pink)
CARD = "#FFFFFF"
FIELD = "#FFF7FA"
ACCENT = "#FF8FB1"
ACCENT_DARK = "#E8628C"
PINK_LIGHT = "#FFD6E5"
TEXT = "#6B4B5E"
MUTED = "#B08A9E"
ERROR = "#C62828"
KEY = "#010203"         # transparent colour so the pop-up can have rounded corners
FONT = "Yu Gothic UI"


def round_rect(c, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


class CuteSlider(tk.Canvas):
    """Round-knob pink slider, 0-100. Writes to `variable` and calls `command(value)`."""

    def __init__(self, master, variable, command, width=240):
        super().__init__(master, width=width, height=28, bg=CARD, highlightthickness=0, bd=0)
        self.var, self.cmd, self.def_w, self.pad = variable, command, width, 14
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._click)
        self.bind("<B1-Motion>", self._click)
        self._draw()

    def _width(self):
        w = self.winfo_width()
        return w if w > 1 else self.def_w

    def _draw(self):
        self.delete("all")
        w, pad, y = self._width(), self.pad, 14
        frac = max(0.0, min(1.0, float(self.var.get()) / 100.0))
        x = pad + (w - 2 * pad) * frac
        self.create_line(pad, y, w - pad, y, width=8, capstyle="round", fill=PINK_LIGHT)
        if x > pad:
            self.create_line(pad, y, x, y, width=8, capstyle="round", fill=ACCENT)
        self.create_oval(x - 10, y - 10, x + 10, y + 10, fill="white", outline=ACCENT_DARK, width=3)

    def _click(self, e):
        w, pad = self._width(), self.pad
        v = max(0.0, min(100.0, (e.x - pad) / max(1, w - 2 * pad) * 100.0))
        self.var.set(v)
        self._draw()
        self.cmd(v)


class App:
    def __init__(self):
        self.cfg = load_cfg()
        self.q = queue.Queue()
        self.player = Player()
        self.player.on_error = lambda m: self.root.after(0, lambda: self.set_status(m, True))
        self.osc = SimpleUDPClient("127.0.0.1", int(self.cfg["osc_port"]))
        self.speakers = []
        self.devices = []
        self.overlay = None
        self.vr_hwnd = None

        self.root = tk.Tk()
        self.root.title("VRChat TTS Chat ♡")
        self.root.resizable(False, False)
        self.root.configure(bg=BG)
        self._setup_style()
        self._build_ui()
        self.refresh_voices()
        self.refresh_devices()

        start_hook(int(self.cfg["hotkey_vk"]), self.cfg["process_name"], self.q)
        self.root.after(30, self.poll)

    # ---- settings window
    def _setup_style(self):
        st = ttk.Style(self.root)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("Card.TLabel", background=CARD, foreground=TEXT, font=(FONT, 10))
        st.configure("Cute.TEntry", fieldbackground=FIELD, foreground=TEXT, bordercolor=PINK_LIGHT,
                     lightcolor=PINK_LIGHT, darkcolor=PINK_LIGHT, insertcolor=ACCENT_DARK, padding=5)
        st.map("Cute.TEntry", bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)],
               darkcolor=[("focus", ACCENT)])
        st.configure("Cute.TCombobox", fieldbackground=FIELD, background=PINK_LIGHT, foreground=TEXT,
                     arrowcolor=ACCENT_DARK, bordercolor=PINK_LIGHT, lightcolor=PINK_LIGHT,
                     darkcolor=PINK_LIGHT, selectbackground=FIELD, selectforeground=TEXT, padding=5)
        st.map("Cute.TCombobox", fieldbackground=[("readonly", FIELD)],
               bordercolor=[("focus", ACCENT)], background=[("active", ACCENT)],
               selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", TEXT)])
        st.configure("Cute.TButton", background=ACCENT, foreground="white", bordercolor=ACCENT,
                     lightcolor=ACCENT, darkcolor=ACCENT, focuscolor=ACCENT, padding=(12, 6),
                     font=(FONT, 10, "bold"))
        hover, press = "#FF7AA2", ACCENT_DARK
        st.map("Cute.TButton",
               background=[("pressed", press), ("active", hover)],
               bordercolor=[("pressed", press), ("active", hover)],
               lightcolor=[("pressed", press), ("active", hover)],
               darkcolor=[("pressed", press), ("active", hover)])
        st.configure("Soft.TButton", background=PINK_LIGHT, foreground=ACCENT_DARK, bordercolor=PINK_LIGHT,
                     lightcolor=PINK_LIGHT, darkcolor=PINK_LIGHT, focuscolor=PINK_LIGHT, padding=(12, 6),
                     font=(FONT, 10, "bold"))
        st.map("Soft.TButton",
               background=[("pressed", ACCENT), ("active", "#FFC2D8")],
               bordercolor=[("pressed", ACCENT), ("active", "#FFC2D8")],
               lightcolor=[("pressed", ACCENT), ("active", "#FFC2D8")],
               darkcolor=[("pressed", ACCENT), ("active", "#FFC2D8")],
               foreground=[("pressed", "white")])
        st.configure("Card.TCheckbutton", background=CARD, foreground=TEXT, font=(FONT, 10),
                     indicatorbackground=FIELD, indicatorforeground=ACCENT_DARK,
                     focuscolor=CARD, bordercolor=ACCENT)
        st.map("Card.TCheckbutton", background=[("active", CARD)],
               indicatorbackground=[("pressed", PINK_LIGHT)])
        # drop-down list of the comboboxes (a plain Tk listbox)
        self.root.option_add("*TCombobox*Listbox.background", FIELD)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "white")
        self.root.option_add("*TCombobox*Listbox.font", (FONT, 10))

    # ---- settings window
    def _build_ui(self):
        outer = tk.Frame(self.root, bg=BG, padx=18, pady=14)
        outer.grid()
        tk.Label(outer, text="✿ VRChat TTS Chat ✿", bg=BG, fg=ACCENT_DARK,
                 font=(FONT, 17, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(outer, text="press Y in VRChat to chat ♪", bg=BG, fg=MUTED,
                 font=(FONT, 9)).grid(row=1, column=0, sticky="w", pady=(0, 10))

        f = tk.Frame(outer, bg=CARD, highlightbackground=PINK_LIGHT, highlightthickness=2,
                     padx=16, pady=14)
        f.grid(row=2, column=0, sticky="we")
        f.columnconfigure(1, weight=1)

        def label(text, row):
            ttk.Label(f, text=text, style="Card.TLabel").grid(row=row, column=0, sticky="w", pady=6)

        r = 0
        label("AivisSpeech URL", r)
        self.url_var = tk.StringVar(value=self.cfg["engine_url"])
        ttk.Entry(f, textvariable=self.url_var, width=30, style="Cute.TEntry").grid(
            row=r, column=1, sticky="we", padx=8)
        ttk.Button(f, text="↻ Reload", style="Cute.TButton", command=self.refresh_voices).grid(
            row=r, column=2, sticky="e")
        r += 1
        label("♡ Voice", r)
        self.voice_var = tk.StringVar()
        self.voice_cb = ttk.Combobox(f, textvariable=self.voice_var, state="readonly", width=38,
                                     style="Cute.TCombobox")
        self.voice_cb.grid(row=r, column=1, sticky="we", padx=8)
        self.voice_cb.bind("<<ComboboxSelected>>", lambda e: self._save())
        ttk.Button(f, text="Manage…", style="Soft.TButton", command=self.open_manager).grid(
            row=r, column=2, sticky="e")
        r += 1
        label("♪ Speakers", r)
        self.dev_var = tk.StringVar()
        self.dev_cb = ttk.Combobox(f, textvariable=self.dev_var, state="readonly", width=38,
                                   style="Cute.TCombobox")
        self.dev_cb.grid(row=r, column=1, columnspan=2, sticky="we", padx=8)
        self.dev_cb.bind("<<ComboboxSelected>>", lambda e: self._on_device())
        r += 1
        label("✿ Mic output", r)
        self.mic_var = tk.StringVar()
        self.mic_cb = ttk.Combobox(f, textvariable=self.mic_var, state="readonly", width=38,
                                   style="Cute.TCombobox")
        self.mic_cb.grid(row=r, column=1, columnspan=2, sticky="we", padx=8)
        self.mic_cb.bind("<<ComboboxSelected>>", lambda e: self._on_device())
        r += 1
        label("☆ Volume", r)
        self.vol_var = tk.DoubleVar(value=self.cfg["volume"])
        CuteSlider(f, self.vol_var, self._on_volume).grid(row=r, column=1, sticky="we", padx=8)
        self.vol_lbl = ttk.Label(f, text="100%", width=5, style="Card.TLabel")
        self.vol_lbl.grid(row=r, column=2, sticky="e")
        self._on_volume(self.cfg["volume"])
        r += 1
        self.chat_var = tk.BooleanVar(value=self.cfg["send_chatbox"])
        self.speak_var = tk.BooleanVar(value=self.cfg["speak"])
        self.snd_var = tk.BooleanVar(value=self.cfg["chatbox_sound"])
        for text, var in (("Send to VRChat chatbox", self.chat_var),
                          ("Speak with TTS", self.speak_var),
                          ("Chatbox notification sound", self.snd_var)):
            ttk.Checkbutton(f, text=text, variable=var, command=self._save,
                            style="Card.TCheckbutton").grid(
                row=r, column=1, columnspan=2, sticky="w", padx=8, pady=2)
            r += 1
        label("OSC port", r)
        self.port_var = tk.StringVar(value=str(self.cfg["osc_port"]))
        ttk.Entry(f, textvariable=self.port_var, width=8, style="Cute.TEntry").grid(
            row=r, column=1, sticky="w", padx=8)
        self.port_var.trace_add("write", lambda *a: self._on_port())
        ttk.Button(f, text="♪ Test voice", style="Cute.TButton", command=self.test_voice).grid(
            row=r, column=2, sticky="e")

        self.status = tk.Label(outer, text="Focus VRChat and press Y.", bg=BG, fg=MUTED,
                               font=(FONT, 9), wraplength=460, justify="left", anchor="w")
        self.status.grid(row=3, column=0, sticky="we", pady=(10, 0))

    # ---- voice model manager
    def open_manager(self):
        old = getattr(self, "mgr", None)
        if old is not None and old.winfo_exists():
            old.lift()
            old.focus_force()
            return
        w = tk.Toplevel(self.root)
        self.mgr = w
        w.title("Voice models ♡")
        w.configure(bg=BG)
        w.transient(self.root)
        w.geometry(f"+{self.root.winfo_x() + 40}+{self.root.winfo_y() + 40}")

        outer = tk.Frame(w, bg=BG, padx=18, pady=14)
        outer.pack(fill="both", expand=True)
        tk.Label(outer, text="♡ Voice models", bg=BG, fg=ACCENT_DARK,
                 font=(FONT, 15, "bold")).pack(anchor="w")
        tk.Label(outer, text="add .aivmx files, or remove voices you don't use", bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(anchor="w", pady=(0, 8))
        card = tk.Frame(outer, bg=CARD, highlightbackground=PINK_LIGHT, highlightthickness=2,
                        padx=10, pady=10)
        card.pack(fill="both", expand=True)
        lb = tk.Listbox(card, width=62, height=11, bg=FIELD, fg=TEXT, selectbackground=ACCENT,
                        selectforeground="white", activestyle="none", relief="flat", bd=0,
                        highlightthickness=0, font=(FONT, 10), exportselection=False)
        sb = ttk.Scrollbar(card, command=lb.yview)
        lb.config(yscrollcommand=sb.set)
        lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        state = {"busy": False, "meta": []}
        note = tk.Label(outer, text="", bg=BG, fg=MUTED, font=(FONT, 9), wraplength=520,
                        justify="left", anchor="w")

        def set_note(text, err=False):
            if w.winfo_exists():
                note.config(text=text, fg=ERROR if err else MUTED)

        def fill(infos, done=None, keep_note=False):
            if not w.winfo_exists():
                return
            rows = []
            for key, inf in infos.items():
                m = inf.get("manifest", {})
                rows.append({
                    "uuid": str(m.get("uuid", key)),
                    "name": m.get("name", key),
                    "version": m.get("version", "?"),
                    "mb": inf.get("file_size", 0) / 1e6,
                    "default": bool(inf.get("is_default_model")),
                    "private": bool(inf.get("is_private_model")),
                    "update": bool(inf.get("is_update_available")),
                })
            rows.sort(key=lambda x: x["name"].lower())
            state["meta"] = rows
            lb.delete(0, "end")
            for x in rows:
                tags = []
                if x["default"]:
                    tags.append("★ default")
                elif x["private"]:
                    tags.append("✿ yours")
                if x["update"]:
                    tags.append("⬆ update")
                lb.insert("end", f"{x['name']}   v{x['version']}   {x['mb']:.0f} MB"
                          + ("   " + "  ".join(tags) if tags else ""))
            if not keep_note:
                n = f"{len(rows)} model(s) installed."
                set_note(f"{done}  {n}" if done else n)

        def reload(done=None, keep_note=False):
            if not keep_note:
                set_note("Loading…")
            url = self.url_var.get().strip()

            def work():
                try:
                    infos = Aivis(url).models()
                except Exception as exc:
                    msg = f"Can't reach AivisSpeech ({exc.__class__.__name__}). Is the engine running?"
                    self.root.after(0, lambda: set_note(msg, True))
                    return
                self.root.after(0, lambda: fill(infos, done, keep_note))

            threading.Thread(target=work, daemon=True).start()

        def run(task, busy_text, done_text):
            if state["busy"]:
                return
            state["busy"] = True
            set_note(busy_text)
            url = self.url_var.get().strip()

            def work():
                err = None
                try:
                    task(Aivis(url))
                except Exception as exc:
                    err = str(exc) or exc.__class__.__name__
                self.root.after(0, lambda: finish(err))

            def finish(err):
                state["busy"] = False
                self.refresh_voices()  # keep the main voice dropdown in sync
                if not w.winfo_exists():
                    return
                if err:
                    reload(keep_note=True)  # refresh the list but keep the error visible
                    set_note(err, True)
                else:
                    reload(done=done_text)

            threading.Thread(target=work, daemon=True).start()

        def selected():
            sel = lb.curselection()
            if not sel:
                set_note("Select a voice model in the list first.", True)
                return None
            return state["meta"][sel[0]]

        def add_files():
            paths = filedialog.askopenfilenames(
                parent=w, title="Choose voice model file(s)",
                filetypes=[("AIVMX voice model", "*.aivmx"), ("All files", "*.*")])
            if not paths:
                return

            def task(api):
                for p in paths:
                    try:
                        api.install_file(p)
                    except Exception as exc:
                        raise RuntimeError(f"{os.path.basename(p)}: {exc}")

            run(task, "Installing… (large models can take a moment)", f"Added {len(paths)} model(s).")

        def add_url():
            url = simpledialog.askstring("Add from URL", "Paste a direct .aivmx download URL:", parent=w)
            if not url or not url.strip():
                return
            url = url.strip()
            run(lambda api: api.install_url(url), "Downloading and installing…", "Added model from URL.")

        def remove():
            x = selected()
            if not x:
                return
            msg = f"Remove “{x['name']}”?\nThe model file will be deleted."
            if x["default"]:
                msg += ("\n\nThis is one of AivisSpeech's default models. The engine installs its "
                        "default models again whenever it starts.")
            if not messagebox.askyesno("Remove voice", msg, parent=w):
                return
            run(lambda api: api.uninstall(x["uuid"]), "Removing…", f"Removed “{x['name']}”.")

        def update():
            x = selected()
            if not x:
                return
            if not x["update"]:
                set_note(f"“{x['name']}” is already up to date.")
                return
            run(lambda api: api.update_model(x["uuid"]), "Updating…", f"Updated “{x['name']}”.")

        btns = tk.Frame(outer, bg=BG)
        btns.pack(fill="x", pady=(10, 0))
        ttk.Button(btns, text="+ Add file…", style="Cute.TButton", command=add_files).pack(side="left")
        ttk.Button(btns, text="+ From URL…", style="Cute.TButton", command=add_url).pack(side="left", padx=6)
        ttk.Button(btns, text="↻", style="Soft.TButton", width=3, command=reload).pack(side="left")
        ttk.Button(btns, text="✕ Remove", style="Soft.TButton", command=remove).pack(side="right")
        ttk.Button(btns, text="⬆ Update", style="Soft.TButton", command=update).pack(side="right", padx=6)
        note.pack(fill="x", pady=(10, 0))
        reload()

    def set_status(self, text, err=False):
        self.status.config(text=text, foreground=ERROR if err else MUTED)

    def _save(self):
        self.cfg.update(
            engine_url=self.url_var.get().strip(),
            send_chatbox=self.chat_var.get(),
            speak=self.speak_var.get(),
            chatbox_sound=self.snd_var.get(),
            volume=int(self.vol_var.get()),
        )
        # Never overwrite a stored voice/device with an empty value
        # (e.g. before the lists are loaded, or if AivisSpeech wasn't running).
        if self.voice_var.get():
            self.cfg["speaker_label"] = self.voice_var.get()
        if self.dev_var.get():
            self.cfg["output_device"] = self.dev_var.get()
        if self.mic_var.get():
            self.cfg["mic_device"] = self.mic_var.get()
        save_cfg(self.cfg)

    def _on_volume(self, value):
        v = max(0, min(100, int(float(value))))
        self.vol_lbl.config(text=f"{v}%")
        self.player.volume = v / 100.0
        self.cfg["volume"] = v
        if hasattr(self, "_vol_save_job"):
            self.root.after_cancel(self._vol_save_job)
        self._vol_save_job = self.root.after(400, lambda: save_cfg(self.cfg))  # save once dragging settles

    def _on_port(self):
        try:
            p = int(self.port_var.get())
            self.cfg["osc_port"] = p
            self.osc = SimpleUDPClient("127.0.0.1", p)
            save_cfg(self.cfg)
        except ValueError:
            pass

    def _on_device(self):
        targets = []
        for var in (self.dev_var, self.mic_var):
            for lab, idx in self.devices:
                if lab == var.get() and idx not in targets:
                    targets.append(idx)
        self.player.targets = targets  # "Off" matches nothing, so it is skipped
        self._save()

    def refresh_voices(self):
        self.cfg["engine_url"] = self.url_var.get().strip()
        try:
            self.speakers = Aivis(self.url_var.get().strip()).speakers()
        except Exception as e:
            self.speakers = []
            self.set_status(f"Can't reach AivisSpeech ({e.__class__.__name__}). Is it running?", True)
            self.voice_cb["values"] = []
            return
        labels = [l for l, _ in self.speakers]
        self.voice_cb["values"] = labels
        if self.cfg["speaker_label"] in labels:
            self.voice_var.set(self.cfg["speaker_label"])
        elif labels:
            self.voice_var.set(labels[0])
        self._save()
        self.set_status(f"{len(labels)} voices loaded. Focus VRChat and press Y.")

    def refresh_devices(self):
        try:
            self.devices = output_devices()
        except Exception:
            self.devices = []
        labels = [l for l, _ in self.devices]
        speaker_vals = ["Off"] + labels
        mic_vals = ["Off"] + [l for l in labels if l != "System default"]
        self.dev_cb["values"] = speaker_vals
        self.mic_cb["values"] = mic_vals
        if self.cfg["output_device"] in speaker_vals:
            self.dev_var.set(self.cfg["output_device"])
        else:
            self.dev_var.set(labels[0] if labels else "Off")
        self.mic_var.set(self.cfg["mic_device"] if self.cfg["mic_device"] in mic_vals else "Off")
        self._on_device()

    def current_speaker_id(self):
        for lab, sid in self.speakers:
            if lab == self.voice_var.get():
                return sid
        return None

    # ---- sending
    def test_voice(self):
        self.speak("こんにちは、テストです。")

    def speak(self, text):
        sid = self.current_speaker_id()
        if sid is None:
            self.root.after(0, lambda: self.set_status("No voice selected.", True))
            return
        url = self.url_var.get().strip()

        def work():
            try:
                self.player.play(Aivis(url).synth(text, sid))
            except Exception as e:
                msg = f"TTS failed: {e}"
                self.root.after(0, lambda: self.set_status(msg, True))

        threading.Thread(target=work, daemon=True).start()

    def send(self, text):
        if self.cfg["send_chatbox"]:
            try:
                args = [text[:CHATBOX_LIMIT], True]
                if not self.cfg["chatbox_sound"]:
                    args.append(False)  # VRChat plays the sound by default, so only send n to mute it
                self.osc.send_message("/chatbox/input", args)
            except Exception as e:
                self.set_status(f"OSC send failed: {e}", True)
        if self.cfg["speak"]:
            self.speak(text)

    def set_typing(self, on):
        try:
            self.osc.send_message("/chatbox/typing", bool(on))
        except Exception:
            pass

    # ---- overlay
    def poll(self):
        try:
            while True:
                kind, arg = self.q.get_nowait()
                if kind == "open":
                    self.open_overlay(arg)
        except queue.Empty:
            pass
        self.root.after(30, self.poll)

    def open_overlay(self, vr_hwnd):
        if self.overlay:
            return
        self.vr_hwnd = vr_hwnd
        HOOK_STATE["open"] = True
        self.set_typing(True)

        w = tk.Toplevel(self.root)
        self.overlay = w
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=KEY)
        try:
            w.attributes("-transparentcolor", KEY)  # lets the rounded corners show through
        except tk.TclError:
            pass

        # Place on the monitor where VRChat is
        W, H = 680, 74
        rect = wt.RECT()
        if vr_hwnd and user32.GetWindowRect(vr_hwnd, ctypes.byref(rect)):
            x = rect.left + (rect.right - rect.left - W) // 2
            y = rect.top + int((rect.bottom - rect.top) * 0.82)
        else:
            x = (w.winfo_screenwidth() - W) // 2
            y = int(w.winfo_screenheight() * 0.82)
        w.geometry(f"{W}x{H}+{x}+{y}")

        var = tk.StringVar()
        c = tk.Canvas(w, width=W, height=H, bg=KEY, highlightthickness=0, bd=0)
        c.pack()
        round_rect(c, 1, 1, W - 1, H - 1, 24, fill=ACCENT, outline=ACCENT)
        round_rect(c, 4, 4, W - 4, H - 4, 21, fill=FIELD, outline=FIELD)
        e = tk.Entry(c, textvariable=var, font=(FONT, 14), bg=FIELD, fg=TEXT,
                     insertbackground=ACCENT_DARK, relief="flat", bd=0, highlightthickness=0)
        c.create_window(26, 12, anchor="nw", window=e, width=W - 52, height=32)
        hint = c.create_text(28, H - 15, anchor="w", font=(FONT, 9), fill=MUTED, text="")

        def update_hint(*_):
            s = var.get()
            clean = s.replace("\r", " ").replace("\n", " ")[:CHATBOX_LIMIT]  # cut pasted text to 144
            if clean != s:
                var.set(clean)
                e.icursor("end")
                return  # setting the var re-triggers this function
            n = len(s)
            c.itemconfig(hint, text=f"♡ {n}/{CHATBOX_LIMIT}     Enter = send   ·   Esc = cancel",
                         fill=ACCENT_DARK if n >= CHATBOX_LIMIT - 10 else MUTED)

        var.trace_add("write", update_hint)
        update_hint()

        def submit(_=None):
            text = var.get().strip()
            self.close_overlay()
            if text:
                self.send(text)
            elif self.cfg["send_chatbox"]:
                # empty message = clear the bubble above your head (silently)
                try:
                    self.osc.send_message("/chatbox/input", ["", True, False])
                except Exception as e:
                    self.set_status(f"OSC send failed: {e}", True)

        e.bind("<Return>", submit)
        e.bind("<KP_Enter>", submit)
        e.bind("<Escape>", lambda _: self.close_overlay())

        w.update_idletasks()
        hwnd = user32.GetParent(w.winfo_id()) or w.winfo_id()
        force_focus(hwnd)
        e.focus_force()

        self._opened_at = time.monotonic()
        self._miss = 0
        self.root.after(100, self._watch_focus)

    def _watch_focus(self):
        """Close the box if the user switches to another app while it is open."""
        if not self.overlay:
            return
        if time.monotonic() - self._opened_at > 0.8:  # grace period while focus is acquired
            fg = user32.GetForegroundWindow()
            pid = wt.DWORD()
            if fg:
                user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
            if fg and pid.value != os.getpid():
                self._miss += 1
            else:
                self._miss = 0
            if self._miss >= 2:  # require two checks in a row to ignore brief flickers
                self.close_overlay(refocus=False)
                return
        self.root.after(100, self._watch_focus)

    def close_overlay(self, refocus=True):
        if self.overlay:
            self.overlay.destroy()
            self.overlay = None
        HOOK_STATE["open"] = False
        self.set_typing(False)
        if refocus and self.vr_hwnd:
            user32.SetForegroundWindow(self.vr_hwnd)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()