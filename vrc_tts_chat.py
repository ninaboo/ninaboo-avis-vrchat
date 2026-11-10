"""
VRChat Chat + AivisSpeech TTS
-----------------------------
Press Y while VRChat is the focused window -> a small chat box pops up.
Enter sends the text to the VRChat chatbox (OSC) AND speaks it with AivisSpeech.
Esc cancels.
Optional voice input: just talk. After a pause your speech is transcribed with Qwen3-ASR
and sent the same way, then it goes back to listening.

Requires: VRChat OSC enabled (Action Menu > Options > OSC > Enabled),
          AivisSpeech running (local engine, default http://127.0.0.1:10101).
"""
import collections
import ctypes
import ctypes.wintypes as wt
import gc
import inspect
import io
import json
import os
import queue
import shutil
import sys
import tarfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

# A --noconsole exe has no stdout/stderr, and libraries like tqdm (the model download bar) crash on that.
for _name in ("stdout", "stderr"):
    if getattr(sys, _name) is None:
        setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))

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
    # voice input (speech -> text)
    "stt_enabled": False,
    "stt_model": "0.6B",            # "0.6B" or "1.7B" (downloaded from inside the app)
    "stt_mic": "System default",
    "stt_silence": 0.8,             # seconds of quiet before the message is sent
    "stt_threshold_db": -45,        # mic level that counts as speaking
    "stt_mute_while_speaking": True,
    "ui_language": "",              # "en", "ja", or empty = follow Windows
    "stt_language": "Auto",         # "Auto" or a language name from STT_LANGUAGES
}
CHATBOX_LIMIT = 144


# --------------------------------------------------------------------------- languages (UI text)
# tr("English text") -> the text in the chosen language. English is the source; only Japanese needs a table.
UI_LANGS = {"en": "English", "ja": "日本語"}
_LANG = "en"
JA = {
    "press Y in VRChat to chat ♪": "VRChatでYキーを押してチャット ♪",
    "Focus VRChat and press Y.": "VRChatを選択してYキーを押してください。",
    "{n} voices loaded. Focus VRChat and press Y.": "{n}個のボイスを読み込みました。VRChatを選択してYキーを押してください。",
    "↻ Reload": "↻ 再読み込み",
    "♡ Voice": "♡ ボイス",
    "Manage…": "管理…",
    "♪ Speakers": "♪ スピーカー",
    "✿ Mic output": "✿ マイク出力",
    "☆ Volume": "☆ 音量",
    "Send to VRChat chatbox": "VRChatのチャットボックスに送信",
    "Speak with TTS": "TTSで読み上げる",
    "Chatbox notification sound": "チャットボックスの通知音",
    "OSC port": "OSCポート",
    "♪ Test voice": "♪ 音声テスト",
    "Mic Input mode": "マイク入力モード",
    "▸ Settings": "▸ 設定",
    "▾ Settings": "▾ 設定",
    "Pause listening while TTS is speaking": "TTSの読み上げ中は聞き取りを一時停止",
    "♡ Recognition": "♡ 音声認識",
    "♡ Language": "♡ 認識言語",
    "♪ Mic input": "♪ マイク入力",
    "☆ Pause to send": "☆ 無音で送信",
    "☆ Mic threshold": "☆ マイクのしきい値",
    "♡ Mic level": "♡ マイクレベル",
    "{sec:.1f} s": "{sec:.1f} 秒",
    "faster": "高速",
    "more accurate": "高精度",
    "about 0.9 GB": "約0.9 GB",
    "about 2.3 GB": "約2.3 GB",
    "Downloading…": "ダウンロード中…",
    "✓ Downloaded": "✓ ダウンロード済み",
    "Not downloaded ({approx})": "未ダウンロード（{approx}）",
    "✕ Cancel": "✕ キャンセル",
    "⬇ Download": "⬇ ダウンロード",
    "Delete": "削除",
    # language names for the recognition dropdown
    "Auto": "自動",
    "Japanese": "日本語",
    "English": "英語",
    "Chinese": "中国語",
    "Korean": "韓国語",
    "Spanish": "スペイン語",
    "French": "フランス語",
    "German": "ドイツ語",
    "Portuguese": "ポルトガル語",
    "Italian": "イタリア語",
    "Indonesian": "インドネシア語",
    "Vietnamese": "ベトナム語",
    "Russian": "ロシア語",
    "Thai": "タイ語",
    # voice model manager
    "Voice models ♡": "ボイスモデル ♡",
    "♡ Voice models": "♡ ボイスモデル",
    "add .aivmx files, or remove voices you don't use": ".aivmxファイルの追加や、使わないボイスの削除ができます",
    "★ default": "★ 標準",
    "✿ yours": "✿ マイモデル",
    "⬆ update": "⬆ 更新あり",
    "{n} model(s) installed.": "{n}件のモデルがインストール済み。",
    "Loading…": "読み込み中…",
    "Can't reach AivisSpeech ({name}). Is the engine running?": "AivisSpeechに接続できません（{name}）。エンジンは起動していますか？",
    "Can't reach AivisSpeech ({name}). Is it running?": "AivisSpeechに接続できません（{name}）。起動していますか？",
    "Select a voice model in the list first.": "先にリストからボイスモデルを選んでください。",
    "Choose voice model file(s)": "ボイスモデルファイルを選択",
    "AIVMX voice model": "AIVMXボイスモデル",
    "All files": "すべてのファイル",
    "Installing… (large models can take a moment)": "インストール中…（大きいモデルは時間がかかります）",
    "Added {n} model(s).": "{n}件のモデルを追加しました。",
    "Add from URL": "URLから追加",
    "Paste a direct .aivmx download URL:": "直接ダウンロードできる.aivmxのURLを貼り付けてください:",
    "Downloading and installing…": "ダウンロードしてインストール中…",
    "Added model from URL.": "URLからモデルを追加しました。",
    "Remove “{name}”?\nThe model file will be deleted.": "「{name}」を削除しますか？\nモデルファイルも削除されます。",
    "\n\nThis is one of AivisSpeech's default models. The engine installs its default models again whenever it starts.":
        "\n\nこれはAivisSpeech標準のモデルです。エンジンは起動するたびに標準モデルを再インストールします。",
    "Remove voice": "ボイスを削除",
    "Removing…": "削除中…",
    "Removed “{name}”.": "「{name}」を削除しました。",
    "“{name}” is already up to date.": "「{name}」は最新です。",
    "Updating…": "更新中…",
    "Updated “{name}”.": "「{name}」を更新しました。",
    "+ Add file…": "+ ファイル追加…",
    "+ From URL…": "+ URLから追加…",
    "✕ Remove": "✕ 削除",
    "⬆ Update": "⬆ 更新",
    # speech recognition model download / use
    "Checking the size of {name}…": "{name}のサイズを確認中…",
    "Not enough free disk space for {name} ({size}).": "{name}の空き容量が足りません（{size}）。",
    "Not enough space": "容量不足",
    "{name} needs about {size} of free disk space.": "{name}には約{size}の空き容量が必要です。",
    "Download voice model": "音声認識モデルのダウンロード",
    "Download {name}?\n\nSize: {size}.\nSaved in:\n{dir}\n\nIt is only downloaded once. You can delete it again here.":
        "{name}をダウンロードしますか？\n\nサイズ: {size}\n保存先:\n{dir}\n\nダウンロードは一度だけです。ここから削除もできます。",
    "Download cancelled.": "ダウンロードをキャンセルしました。",
    "Download failed: {e}": "ダウンロードに失敗しました: {e}",
    "{name} downloaded.": "{name}をダウンロードしました。",
    "Downloading {name}… {done} / {total} MB": "{name}をダウンロード中… {done} / {total} MB",
    "Downloading {name}… {done} MB": "{name}をダウンロード中… {done} MB",
    "Delete voice model": "音声認識モデルの削除",
    "Delete the downloaded {name}?\nYou can download it again later.": "ダウンロード済みの{name}を削除しますか？\n後でもう一度ダウンロードできます。",
    "{name} deleted.": "{name}を削除しました。",
    "Mic input mode off.": "マイク入力モードをオフにしました。",
    "Loading {name}… (a few seconds)": "{name}を読み込み中…（数秒かかります）",
    "Listening with {name} on {device} ({lang}). Just talk!": "{name}（{device}）で聞き取り中（{lang}）。話しかけてください！",
    "Voice input needs the sherpa-onnx package ({e}). pip install -U sherpa-onnx":
        "音声入力にはsherpa-onnxパッケージが必要です（{e}）。pip install -U sherpa-onnx",
    "Couldn't load {name}: {e}": "{name}を読み込めませんでした: {e}",
    "Speech recognition failed: {e}": "音声認識に失敗しました: {e}",
    "Heard: {text}": "認識結果: {text}",
    "No voice selected.": "ボイスが選択されていません。",
    "TTS failed: {e}": "TTSに失敗しました: {e}",
    "OSC send failed: {e}": "OSC送信に失敗しました: {e}",
    "Audio playback failed: {e}": "音声の再生に失敗しました: {e}",
    "Can't open the microphone: {e}": "マイクを開けません: {e}",
    "{err}  (tried {n} formats; pick another entry under Mic input, ideally the (MME) one)":
        "{err}（{n}通りの形式を試しました。マイク入力で別の項目、できれば(MME)のものを選んでください）",
    "this sherpa-onnx is too old for Qwen3-ASR (needs 1.13.4 or newer): pip install -U sherpa-onnx":
        "お使いのsherpa-onnxはQwen3-ASRに対応していません（1.13.4以降が必要）: pip install -U sherpa-onnx",
    "This sherpa-onnx version can't force {lang}, so it is on Auto (wrong-language results are still filtered out).":
        "お使いのsherpa-onnxでは{lang}に固定できないため、自動判定になっています（違う言語の結果は除外されます）。",
    "Enter = send   ·   Esc = cancel": "Enter＝送信   ·   Esc＝キャンセル",
}
_JA_REV = {v: k for k, v in JA.items()}


def set_ui_language(code):
    global _LANG
    _LANG = code if code in UI_LANGS else "en"


def system_ui_language():
    try:
        return "ja" if (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF) == 0x11 else "en"
    except Exception:
        return "en"


def tr(_s, /, **kw):
    out = JA.get(_s, _s) if _LANG == "ja" else _s
    return out.format(**kw) if kw else out



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
        self.playing = False
        self.last_end = 0.0
        threading.Thread(target=self._run, daemon=True).start()

    @property
    def busy(self):
        """True while audio is queued or playing, and for a moment afterwards (room echo)."""
        return self.playing or not self.q.empty() or time.monotonic() - self.last_end < 0.5

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
            self.playing = True
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
                msg = tr("Audio playback failed: {e}", e=e)
                print(msg, file=sys.stderr)
                if self.on_error:
                    self.on_error(msg)
            finally:
                self.playing = False
                self.last_end = time.monotonic()


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


# --------------------------------------------------------------------------- voice input (STT)
# Qwen3-ASR runs through sherpa-onnx (ONNX Runtime, about 25 MB), not PyTorch. The model files are
# NOT part of the app: they are downloaded from inside it, once, into a "models" folder next to it.


def _app_dir():
    """Folder of the exe (or of this script when run from source): where build.bat puts everything."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _pick_models_dir():
    """<app folder>\\models, or %APPDATA%\\VRChatTTSChat\\models if the app folder can't be written to
    (for example when it sits under Program Files)."""
    here = _app_dir()
    try:
        probe = os.path.join(here, ".write_test")
        open(probe, "w").close()
        os.remove(probe)
        return os.path.join(here, "models")
    except OSError:
        return os.path.join(CONFIG_DIR, "models")


MODELS_DIR = _pick_models_dir()
STT_THREADS = 3  # CPU threads for recognition (leave the rest for VRChat)
STT_FILES = ["conv_frontend.onnx", "encoder.int8.onnx", "decoder.int8.onnx",
             "tokenizer/vocab.json", "tokenizer/merges.txt", "tokenizer/tokenizer_config.json"]
STT_MODELS = {
    "0.6B": {
        "name": "Qwen3-ASR 0.6B", "label": "Qwen3-ASR 0.6B  (faster)", "approx": "about 0.9 GB",
        # official sherpa-onnx release archive
        "tar": "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
               "sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25.tar.bz2",
    },
    "1.7B": {
        "name": "Qwen3-ASR 1.7B", "label": "Qwen3-ASR 1.7B  (more accurate)", "approx": "about 2.3 GB",
        # Community int8 export in the same layout (I found no official 1.7B int8 package). To use
        # your own copy instead, upload the same 6 files and change this base URL.
        "files": "https://huggingface.co/thieunv-asilla/sherpa-onnx-qwen3-asr-1.7B-int8/resolve/main/",
    },
}
_STT_HINTS = {"0.6B": "faster", "1.7B": "more accurate"}


def stt_labels():
    """Dropdown text for each recognition model, in the current UI language."""
    return {k: f"{v['name']}  ({tr(_STT_HINTS[k])})" for k, v in STT_MODELS.items()}
BLOCK_S = 0.03  # mic is read in 30 ms blocks


class _Cancelled(Exception):
    pass


def stt_dir(key):
    return os.path.join(MODELS_DIR, f"qwen3-asr-{key}")


def stt_downloaded(key):
    d = stt_dir(key)
    return (os.path.isfile(os.path.join(d, ".complete"))
            and all(os.path.isfile(os.path.join(d, *f.split("/"))) for f in STT_FILES))


def _http_size(url):
    try:
        r = requests.head(url, allow_redirects=True, timeout=15)
        return int(r.headers.get("Content-Length", 0)) or None
    except Exception:
        return None


def stt_download_size(key):
    """Download size in bytes, or None if the server doesn't say."""
    m = STT_MODELS[key]
    if "tar" in m:
        return _http_size(m["tar"])
    sizes = [_http_size(m["files"] + f) for f in STT_FILES]
    return sum(sizes) if all(sizes) else None


class _Counter:
    """File-like wrapper that reports how many bytes have been read (for the tar stream)."""

    def __init__(self, raw, on_read):
        self.raw, self.on_read, self.n = raw, on_read, 0

    def read(self, size=-1):
        data = self.raw.read(size)
        self.n += len(data)
        self.on_read(self.n)
        return data


def _copy_out(src, out, cancel):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "wb") as fh:
        while True:
            chunk = src.read(1 << 20)
            if not chunk:
                break
            if cancel.is_set():
                raise _Cancelled()
            fh.write(chunk)


def _download_tar(url, dest, on_progress, cancel):
    """Streams the .tar.bz2 straight to disk, skipping the test wavs, without a temp copy."""
    with requests.get(url, stream=True, timeout=(15, 60)) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0)) or None
        counter = _Counter(r.raw, lambda n: on_progress(n, total))
        with tarfile.open(fileobj=counter, mode="r|bz2") as tar:
            for m in tar:
                if cancel.is_set():
                    raise _Cancelled()
                if not m.isfile():
                    continue
                parts = [p for p in m.name.replace("\\", "/").split("/") if p not in ("", ".")]
                if len(parts) < 2 or ".." in parts:  # drop the archive's top folder; refuse odd paths
                    continue
                rel = parts[1:]
                if rel[0] == "test_wavs" or rel[-1] == "README.md":
                    continue
                _copy_out(tar.extractfile(m), os.path.join(dest, *rel), cancel)


def _download_files(base, dest, on_progress, cancel):
    sizes = [_http_size(base + f) for f in STT_FILES]
    total = sum(sizes) if all(sizes) else None
    done = 0
    for f in STT_FILES:
        with requests.get(base + f, stream=True, timeout=(15, 60)) as r:
            r.raise_for_status()
            out = os.path.join(dest, *f.split("/"))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    if cancel.is_set():
                        raise _Cancelled()
                    fh.write(chunk)
                    done += len(chunk)
                    on_progress(done, total)


def download_stt(key, on_progress, cancel):
    """Downloads one model into MODELS_DIR. Only a finished, complete download is ever kept."""
    m = STT_MODELS[key]
    dest = stt_dir(key)
    tmp = dest + ".part"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    try:
        if "tar" in m:
            _download_tar(m["tar"], tmp, on_progress, cancel)
        else:
            _download_files(m["files"], tmp, on_progress, cancel)
        missing = [f for f in STT_FILES if not os.path.isfile(os.path.join(tmp, *f.split("/")))]
        if missing:
            raise RuntimeError("the download is missing: " + ", ".join(missing))
        open(os.path.join(tmp, ".complete"), "w").close()
        shutil.rmtree(dest, ignore_errors=True)
        os.replace(tmp, dest)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def input_devices():
    """List of (label, index) for input devices. WASAPI first, then MME / DirectSound: those convert
    formats themselves, so they work with virtual mics (Elgato Wave Link, VoiceMeeter...) that WASAPI
    refuses. WDM-KS is left out because it is unreliable."""
    apis = sd.query_hostapis()
    devs = sd.query_devices()
    rank = {"WASAPI": 0, "MME": 1, "DirectSound": 2}
    out = []
    for i, d in enumerate(devs):
        api = apis[d["hostapi"]]["name"]
        key = next((v for k, v in rank.items() if k in api), None)
        if d["max_input_channels"] > 0 and key is not None:
            out.append((key, f"{d['name']} ({api})", i))
    out.sort(key=lambda x: x[0])
    return [("System default", None)] + [(label, i) for _, label, i in out]


class Segmenter:
    """Turns a stream of mic blocks into utterances.

    Speech starts after a few loud blocks in a row and ends after `silence_s` of quiet.
    feed() returns None, ("start", None) or ("end", audio) where audio is None if it was too short.
    """
    PRE_ROLL = 0.3        # seconds of audio kept from just before speech was detected
    TAIL_KEEP = 0.3       # seconds of the trailing silence that stay in the clip
    MIN_SPEECH = 0.2      # less than this much actual speech (clicks, bumps) is thrown away
    MAX_UTTERANCE = 20.0  # force a cut (also keeps clips short, which suits the ASR model)
    START_BLOCKS = 3

    def __init__(self, sr, block_s):
        self.sr, self.block_s = sr, block_s
        self.threshold_db = -45.0
        self.silence_s = 0.8
        self.level = -100.0
        self.pre = collections.deque(maxlen=max(1, int(self.PRE_ROLL / block_s)))
        self.reset()

    def reset(self):
        self.pre.clear()
        self.seg = []
        self.speaking = False
        self.voiced_run = 0
        self.voiced_s = 0.0
        self.silent = 0.0

    def feed(self, block):
        rms = float(np.sqrt(np.mean(np.square(block)))) if block.size else 0.0
        self.level = 20.0 * float(np.log10(rms + 1e-9))
        voiced = self.level > self.threshold_db
        if not self.speaking:
            self.pre.append(block)
            self.voiced_run = self.voiced_run + 1 if voiced else 0
            if self.voiced_run >= self.START_BLOCKS:
                self.speaking = True
                self.seg = list(self.pre)
                self.silent = 0.0
                self.voiced_s = self.voiced_run * self.block_s
                return ("start", None)
            return None
        self.seg.append(block)
        self.silent = 0.0 if voiced else self.silent + self.block_s
        if voiced:
            self.voiced_s += self.block_s
        total = len(self.seg) * self.block_s
        if self.silent >= self.silence_s or total >= self.MAX_UTTERANCE:
            speech = self.voiced_s
            extra_tail = max(0, int((self.silent - self.TAIL_KEEP) / self.block_s))
            audio = np.concatenate(self.seg[:len(self.seg) - extra_tail]) if speech >= self.MIN_SPEECH else None
            self.reset()
            return ("end", audio)
        return None


class VoiceInput:
    """Always-on mic listener. Cuts the mic into utterances by silence and hands each one to on_audio."""

    def __init__(self, is_muted, on_start, on_discard, on_audio, on_error):
        self.is_muted, self.on_start, self.on_discard = is_muted, on_start, on_discard
        self.on_audio, self.on_error = on_audio, on_error
        self.threshold_db = -45.0
        self.silence_s = 0.8
        self.level = -100.0   # live mic level (dBFS), read by the settings window's meter
        self.speaking = False
        self._thread = None
        self._stop = threading.Event()

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def start(self, device):
        self.stop()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(device, self._stop), daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._thread = None
        self.level, self.speaking = -100.0, False

    @staticmethod
    def _same_mic_other_apis(device):
        """Indices of the same microphone under other host APIs (MME first: it converts formats)."""
        if device is None:
            return []
        try:
            info = sd.query_devices(device, "input")
            name, apis = info["name"], sd.query_hostapis()
            found = []
            for i, d in enumerate(sd.query_devices()):
                if i == device or d["max_input_channels"] <= 0 or d["hostapi"] == info["hostapi"]:
                    continue
                api = apis[d["hostapi"]]["name"]
                if "WDM" in api:
                    continue
                n = d["name"]
                if n.startswith(name[:28]) or name.startswith(n[:28]):  # MME cuts names at 31 chars
                    found.append((0 if "MME" in api else 1, i))
            return [i for _, i in sorted(found)]
        except Exception:
            return []

    @staticmethod
    def _open_device(device, cb, errors):
        """Try the device's own format first, then conversions (WASAPI shared mode only accepts the
        device format unless Windows is asked to convert)."""
        try:
            info = sd.query_devices(device, "input")
            native_sr = int(info["default_samplerate"])
            native_ch = max(1, int(info["max_input_channels"]))
            wasapi = "WASAPI" in sd.query_hostapis()[info["hostapi"]]["name"]
            where = info["name"]
        except Exception:
            native_sr, native_ch, wasapi, where = 48000, 2, False, "default"
        conv = sd.WasapiSettings(auto_convert=True) if wasapi else None
        attempts = []
        for sr, ch, extra in ((native_sr, native_ch, None), (16000, 1, conv), (native_sr, 1, conv),
                              (16000, native_ch, conv), (native_sr, native_ch, conv),
                              (16000, 1, None), (native_sr, 1, None)):
            if (sr, ch, extra) not in attempts:
                attempts.append((sr, ch, extra))
        for sr, ch, extra in attempts:
            try:
                stream = sd.InputStream(samplerate=sr, device=device, channels=ch, dtype="float32",
                                        blocksize=int(sr * BLOCK_S), callback=cb, extra_settings=extra)
                stream.start()
                return stream, sr
            except Exception as e:
                errors.append(f"{where} [{device}] {sr} Hz, {ch} ch{', converted' if extra else ''}: {e}")
        return None

    @staticmethod
    def _open(device, q):
        def cb(indata, frames, t, status):
            q.put(indata.mean(axis=1).astype("float32"))  # any number of channels -> mono

        errors = []
        for dev in [device] + VoiceInput._same_mic_other_apis(device):
            got = VoiceInput._open_device(dev, cb, errors)
            if got:
                return got
        for line in errors:
            print("mic open failed ->", line, file=sys.stderr)
        raise RuntimeError(tr("{err}  (tried {n} formats; pick another entry under Mic input, "
                              "ideally the (MME) one)", err=errors[0], n=len(errors)))

    def _run(self, device, stop):
        q = queue.Queue()
        try:
            stream, sr = self._open(device, q)
        except Exception as e:
            self.on_error(tr("Can't open the microphone: {e}", e=e))
            return
        seg = Segmenter(sr, int(sr * BLOCK_S) / sr)
        try:
            while not stop.is_set():
                try:
                    block = q.get(timeout=0.1)
                except queue.Empty:
                    continue
                if self.is_muted():  # our own TTS is playing: don't listen to it
                    if seg.speaking:
                        self.on_discard()
                    seg.reset()
                    self.level, self.speaking = -100.0, False
                    continue
                seg.threshold_db, seg.silence_s = self.threshold_db, self.silence_s
                ev = seg.feed(block)
                self.level = seg.level
                if ev is None:
                    continue
                kind, audio = ev
                if kind == "start":
                    self.speaking = True
                    self.on_start()
                else:
                    self.speaking = False
                    if audio is None:
                        self.on_discard()
                    else:
                        self.on_audio(audio, sr)
        finally:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
            if seg.speaking:
                self.on_discard()
            self.speaking = False


# Languages offered in the dropdown. "Auto" lets Qwen detect it (the default and the safest choice).
# The regex is a sanity check on the result when a language is forced (see QwenASR.accept).
import re
_LATIN = r"[A-Za-z\u00C0-\u024F]"
STT_LANGUAGES = {
    "Auto": None,
    "Japanese": r"[\u3040-\u30FF\u3400-\u9FFF]",
    "English": _LATIN,
    "Chinese": r"[\u3400-\u9FFF]",
    "Korean": r"[\uAC00-\uD7AF\u1100-\u11FF]",
    "Spanish": _LATIN,
    "French": _LATIN,
    "German": _LATIN,
    "Portuguese": _LATIN,
    "Italian": _LATIN,
    "Indonesian": _LATIN,
    "Vietnamese": _LATIN,
    "Russian": r"[\u0400-\u04FF]",
    "Thai": r"[\u0E00-\u0E7F]",
}
# Sounds Qwen tends to invent from breaths/noise when it is forced to pick a language.
_FILLERS = {"嗯", "啊", "呃", "哦", "额", "唔", "えっと", "えー", "えーと", "あの", "あー", "ん",
            "uh", "um", "umm", "hmm", "mm", "mhm", "ah", "oh", "huh", "eh"}


class QwenASR:
    """Qwen3-ASR via sherpa-onnx (CPU, no PyTorch). Imported lazily so the rest of the app works
    even if sherpa-onnx isn't installed."""

    def __init__(self):
        self.rec = None
        self.key = None
        self.lang = "Auto"
        self.lang_note = ""   # what happened when we tried to apply the language (shown in the status line)
        self.lock = threading.Lock()

    @property
    def ready(self):
        return self.rec is not None

    def load(self, key, lang="Auto"):
        """Load a model that is already downloaded. Returns where it runs."""
        with self.lock:
            if self.rec is not None and self.key == key and self.lang == lang:
                return "CPU"
            self._unload()
            import sherpa_onnx

            make = getattr(sherpa_onnx.OfflineRecognizer, "from_qwen3_asr", None)
            if make is None:
                raise RuntimeError(tr("this sherpa-onnx is too old for Qwen3-ASR (needs 1.13.4 or newer): "
                                      "pip install -U sherpa-onnx"))
            d = stt_dir(key)
            opts = {"max_new_tokens": 256}
            try:  # only pass optional settings this sherpa-onnx version knows about
                known = inspect.signature(make).parameters
                opts = {k: v for k, v in opts.items() if k in known}
            except (TypeError, ValueError):
                opts = {}
            files = dict(
                conv_frontend=os.path.join(d, "conv_frontend.onnx"),
                encoder=os.path.join(d, "encoder.int8.onnx"),
                decoder=os.path.join(d, "decoder.int8.onnx"),
                tokenizer=os.path.join(d, "tokenizer"))
            self.lang_note = ""
            rec = None
            if lang != "Auto":
                rec = self._make_with_language(sherpa_onnx, make, files, opts, lang)
                if rec is None:
                    self.lang_note = tr("This sherpa-onnx version can't force {lang}, so it is on Auto "
                                        "(wrong-language results are still filtered out).", lang=tr(lang))
            if rec is None:
                rec = make(num_threads=STT_THREADS, **files, **opts)
            self.rec = rec
            self.key = key
            self.lang = lang
            return "CPU"

    @staticmethod
    def _make_with_language(sherpa_onnx, make, files, opts, lang):
        """Build the recognizer with a forced language. Returns None if this sherpa-onnx can't."""
        # 1) newer versions: a plain `language` argument
        try:
            if "language" in inspect.signature(make).parameters:
                return make(num_threads=STT_THREADS, language=lang, **files, **opts)
        except Exception:
            pass
        # 2) build the config by hand with the language set on the Qwen3 model config
        try:
            try:
                from sherpa_onnx.lib import _sherpa_onnx as native
            except Exception:
                import _sherpa_onnx as native
            qc = native.OfflineQwen3ASRModelConfig(language=lang, **files, **opts)
            mc = native.OfflineModelConfig(qwen3_asr=qc, num_threads=STT_THREADS)
            fc = native.FeatureExtractorConfig(sampling_rate=16000, feature_dim=128)
            cfg = native.OfflineRecognizerConfig(feat_config=fc, model_config=mc)
            self = sherpa_onnx.OfflineRecognizer.__new__(sherpa_onnx.OfflineRecognizer)
            self.recognizer = native.OfflineRecognizer(cfg)
            self.config = cfg
            return self
        except Exception:
            return None

    def unload(self):
        with self.lock:
            self._unload()

    def _unload(self):
        self.rec = None
        self.key = None
        gc.collect()

    def transcribe(self, audio, sr):
        """audio: mono float32 numpy array at any sample rate (sherpa-onnx resamples)."""
        with self.lock:
            if self.rec is None:
                return ""
            stream = self.rec.create_stream()
            stream.accept_waveform(sr, audio)
            self.rec.decode_stream(stream)
            text = (stream.result.text or "").strip()
            lang = self.lang
        return text if self.accept(text, lang) else ""

    @staticmethod
    def accept(text, lang):
        """Drop empty results, and (when a language is chosen) filler noise or text in the wrong script."""
        if not any(ch.isalnum() for ch in text):  # empty / punctuation-only
            return False
        pattern = STT_LANGUAGES.get(lang)
        if pattern is None:  # Auto
            return True
        bare = re.sub(r"[\W_]+", "", text).lower()
        if bare in _FILLERS:
            return False
        return re.search(pattern, text) is not None


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
        set_ui_language(self.cfg.get("ui_language") or system_ui_language())
        self.q = queue.Queue()
        self.ui_q = queue.Queue()  # callables from background threads, run on the main thread by poll()
        self.player = Player()
        self.player.on_error = lambda m: self.ui(lambda: self.set_status(m, True))
        self.osc = SimpleUDPClient("127.0.0.1", int(self.cfg["osc_port"]))
        self.speakers = []
        self.devices = []
        self.overlay = None
        self.vr_hwnd = None

        # voice input
        self.inputs = []
        self.asr = QwenASR()
        self.stt_q = queue.Queue()  # utterances waiting to be transcribed
        self._stt_gen = 0           # bumped whenever listening is (re)started or stopped
        self._dl = None             # the running model download, if any
        self.voice = VoiceInput(
            is_muted=lambda: bool(self.cfg.get("stt_mute_while_speaking", True)) and self.player.busy,
            on_start=self._stt_on_start,
            on_discard=self._stt_on_discard,
            on_audio=lambda audio, sr: self.stt_q.put((audio, sr)),
            on_error=lambda m: self.ui(lambda: self.set_status(m, True)))
        self.voice.threshold_db = float(self.cfg["stt_threshold_db"])
        self.voice.silence_s = float(self.cfg["stt_silence"])
        threading.Thread(target=self._stt_worker, daemon=True).start()

        self.root = tk.Tk()
        self.root.title("VRChat TTS Chat ♡")
        self.root.resizable(False, False)
        self.root.configure(bg=BG)
        self._setup_style()
        self._build_ui()
        self.refresh_voices()
        self.refresh_devices()
        self.refresh_inputs()
        if self.cfg["stt_enabled"]:
            self._start_listening()

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
        st.configure("Vertical.TScrollbar", background=PINK_LIGHT, troughcolor=FIELD, bordercolor=FIELD,
                     arrowcolor=ACCENT_DARK, lightcolor=PINK_LIGHT, darkcolor=PINK_LIGHT)
        st.map("Vertical.TScrollbar", background=[("active", ACCENT)])
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
        st.configure("Cute.Horizontal.TProgressbar", troughcolor=PINK_LIGHT, background=ACCENT,
                     bordercolor=PINK_LIGHT, lightcolor=ACCENT, darkcolor=ACCENT, thickness=10)
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
        outer.columnconfigure(0, weight=1)
        tk.Label(outer, text="✿ VRChat TTS Chat ✿", bg=BG, fg=ACCENT_DARK,
                 font=(FONT, 17, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(outer, text=tr("press Y in VRChat to chat ♪"), bg=BG, fg=MUTED,
                 font=(FONT, 9)).grid(row=1, column=0, sticky="w", pady=(0, 10))
        self.ui_lang_var = tk.StringVar(value=UI_LANGS[_LANG])
        self.ui_lang_cb = ttk.Combobox(outer, textvariable=self.ui_lang_var, values=list(UI_LANGS.values()),
                                       state="readonly", width=9, style="Cute.TCombobox")
        self.ui_lang_cb.grid(row=0, column=1, rowspan=2, sticky="ne")
        self.ui_lang_cb.bind("<<ComboboxSelected>>", lambda e: self._on_ui_language())

        f = tk.Frame(outer, bg=CARD, highlightbackground=PINK_LIGHT, highlightthickness=2,
                     padx=16, pady=14)
        f.grid(row=2, column=0, columnspan=2, sticky="we")
        f.columnconfigure(1, weight=1)

        def label(text, row):
            ttk.Label(f, text=tr(text), style="Card.TLabel").grid(row=row, column=0, sticky="w", pady=6)

        r = 0
        label("AivisSpeech URL", r)
        self.url_var = tk.StringVar(value=self.cfg["engine_url"])
        ttk.Entry(f, textvariable=self.url_var, width=30, style="Cute.TEntry").grid(
            row=r, column=1, sticky="we", padx=8)
        ttk.Button(f, text=tr("↻ Reload"), style="Cute.TButton", command=self.refresh_voices).grid(
            row=r, column=2, sticky="e")
        r += 1
        label("♡ Voice", r)
        self.voice_var = tk.StringVar()
        self.voice_cb = ttk.Combobox(f, textvariable=self.voice_var, state="readonly", width=38,
                                     style="Cute.TCombobox")
        self.voice_cb.grid(row=r, column=1, sticky="we", padx=8)
        self.voice_cb.bind("<<ComboboxSelected>>", lambda e: self._save())
        ttk.Button(f, text=tr("Manage…"), style="Soft.TButton", command=self.open_manager).grid(
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
            ttk.Checkbutton(f, text=tr(text), variable=var, command=self._save,
                            style="Card.TCheckbutton").grid(
                row=r, column=1, columnspan=2, sticky="w", padx=8, pady=2)
            r += 1
        label("OSC port", r)
        self.port_var = tk.StringVar(value=str(self.cfg["osc_port"]))
        ttk.Entry(f, textvariable=self.port_var, width=8, style="Cute.TEntry").grid(
            row=r, column=1, sticky="w", padx=8)
        self.port_var.trace_add("write", lambda *a: self._on_port())
        ttk.Button(f, text=tr("♪ Test voice"), style="Cute.TButton", command=self.test_voice).grid(
            row=r, column=2, sticky="e")
        self._build_voice_rows(f, r + 1, label)

        self.status = tk.Label(outer, text=tr("Focus VRChat and press Y."), bg=BG, fg=MUTED,
                               font=(FONT, 9), wraplength=460, justify="left", anchor="w")
        self.status.grid(row=3, column=0, columnspan=2, sticky="we", pady=(10, 0))

    # ---- voice input: settings rows
    def _build_voice_rows(self, f, r, label):
        head = tk.Frame(f, bg=CARD)
        head.grid(row=r, column=0, columnspan=3, sticky="we", pady=(12, 0))
        self.stt_var = tk.BooleanVar(value=self.cfg["stt_enabled"])
        ttk.Checkbutton(head, text=tr("Mic Input mode"), variable=self.stt_var,
                        command=self._on_stt_toggle, style="Card.TCheckbutton").pack(side="left")
        self.stt_arrow = ttk.Button(head, text=tr("▸ Settings"), style="Soft.TButton",
                                    command=lambda: self._set_voice_open(not self._voice_open))
        self.stt_arrow.pack(side="right")
        r += 1
        # everything below lives in a panel that drops down
        d = tk.Frame(f, bg=CARD)
        d.grid(row=r, column=0, columnspan=3, sticky="we")
        d.columnconfigure(1, weight=1)
        self._voice_panel = d
        self._voice_open = False
        f = d  # the rows below go inside the panel

        def label(text, row):
            ttk.Label(f, text=tr(text), style="Card.TLabel").grid(row=row, column=0, sticky="w", pady=6)

        r = 0
        self.stt_mute_var = tk.BooleanVar(value=self.cfg["stt_mute_while_speaking"])
        ttk.Checkbutton(f, text=tr("Pause listening while TTS is speaking"), variable=self.stt_mute_var,
                        command=self._save, style="Card.TCheckbutton").grid(
            row=r, column=1, columnspan=2, sticky="w", padx=8, pady=2)
        r += 1
        label("♡ Recognition", r)
        self.stt_model_var = tk.StringVar(
            value=stt_labels().get(self.cfg["stt_model"], stt_labels()["0.6B"]))
        self.stt_model_cb = ttk.Combobox(f, textvariable=self.stt_model_var,
                                         values=list(stt_labels().values()), state="readonly",
                                         width=38, style="Cute.TCombobox")
        self.stt_model_cb.grid(row=r, column=1, columnspan=2, sticky="we", padx=8)
        self.stt_model_cb.bind("<<ComboboxSelected>>", lambda e: self._on_stt_model())
        r += 1
        self.stt_status_lbl = ttk.Label(f, text="", style="Card.TLabel")
        self.stt_status_lbl.grid(row=r, column=1, sticky="w", padx=8)
        btns = tk.Frame(f, bg=CARD)
        btns.grid(row=r, column=2, sticky="e")
        self.dl_btn = ttk.Button(btns, text=tr("⬇ Download"), style="Cute.TButton", command=self._on_stt_download)
        self.dl_btn.pack(side="left")
        self.del_btn = ttk.Button(btns, text=tr("Delete"), style="Soft.TButton", command=self._on_stt_delete)
        self.del_btn.pack(side="left", padx=(6, 0))
        r += 1
        self.dl_bar = ttk.Progressbar(f, style="Cute.Horizontal.TProgressbar", mode="determinate",
                                      maximum=100)
        self.dl_bar.grid(row=r, column=1, columnspan=2, sticky="we", padx=8, pady=(2, 4))
        self._refresh_stt_model_ui()
        r += 1
        label("♡ Language", r)
        lang = self.cfg.get("stt_language", "Auto")
        self.stt_lang_var = tk.StringVar(value=tr(lang if lang in STT_LANGUAGES else "Auto"))
        self.stt_lang_cb = ttk.Combobox(f, textvariable=self.stt_lang_var, values=[tr(k) for k in STT_LANGUAGES],
                                        state="readonly", width=38, style="Cute.TCombobox")
        self.stt_lang_cb.grid(row=r, column=1, columnspan=2, sticky="we", padx=8)
        self.stt_lang_cb.bind("<<ComboboxSelected>>", lambda e: self._on_stt_language())
        r += 1
        label("♪ Mic input", r)
        self.stt_mic_var = tk.StringVar()
        self.stt_mic_cb = ttk.Combobox(f, textvariable=self.stt_mic_var, state="readonly", width=38,
                                       style="Cute.TCombobox")
        self.stt_mic_cb.grid(row=r, column=1, columnspan=2, sticky="we", padx=8)
        self.stt_mic_cb.bind("<<ComboboxSelected>>", lambda e: self._on_stt_mic())
        r += 1
        label("☆ Pause to send", r)
        self.sil_var = tk.DoubleVar(value=(float(self.cfg["stt_silence"]) - 0.3) / 2.7 * 100)
        CuteSlider(f, self.sil_var, self._on_stt_silence).grid(row=r, column=1, sticky="we", padx=8)
        self.sil_lbl = ttk.Label(f, text="", width=6, style="Card.TLabel")
        self.sil_lbl.grid(row=r, column=2, sticky="e")
        self._on_stt_silence(self.sil_var.get())
        r += 1
        label("☆ Mic threshold", r)
        self.thr_var = tk.DoubleVar(value=(float(self.cfg["stt_threshold_db"]) + 70) / 60 * 100)
        CuteSlider(f, self.thr_var, self._on_stt_threshold).grid(row=r, column=1, sticky="we", padx=8)
        self.thr_lbl = ttk.Label(f, text="", width=6, style="Card.TLabel")
        self.thr_lbl.grid(row=r, column=2, sticky="e")
        self._on_stt_threshold(self.thr_var.get())
        r += 1
        label("♡ Mic level", r)
        self.meter = tk.Canvas(f, height=16, bg=CARD, highlightthickness=0, bd=0)
        self.meter.grid(row=r, column=1, columnspan=2, sticky="we", padx=8)
        self.root.after(60, self._tick_meter)
        self._set_voice_open(bool(self.cfg["stt_enabled"]))

    def _set_voice_open(self, show):
        self._voice_open = show
        if show:
            self._voice_panel.grid()
        else:
            self._voice_panel.grid_remove()
        self.stt_arrow.config(text=tr("▾ Settings" if show else "▸ Settings"))

    def _tick_meter(self):
        c = self.meter
        w = max(c.winfo_width(), 60)
        c.delete("all")
        c.create_rectangle(0, 4, w, 12, fill=PINK_LIGHT, outline="")
        if self.voice.running:
            frac = max(0.0, min(1.0, (self.voice.level + 70.0) / 60.0))
            c.create_rectangle(0, 4, w * frac, 12, fill=ACCENT_DARK if self.voice.speaking else ACCENT,
                               outline="")
        tx = w * (float(self.cfg["stt_threshold_db"]) + 70.0) / 60.0
        c.create_line(tx, 1, tx, 15, fill=TEXT, width=2)  # the threshold: speak louder than this line
        self.root.after(60, self._tick_meter)

    def _save_soon(self):
        if hasattr(self, "_save_job"):
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(400, lambda: save_cfg(self.cfg))

    def _on_stt_silence(self, value):
        sec = max(0.3, min(3.0, round(0.3 + 2.7 * float(value) / 100.0, 1)))
        self.sil_lbl.config(text=tr("{sec:.1f} s", sec=sec))
        self.cfg["stt_silence"] = sec
        self.voice.silence_s = sec
        self._save_soon()

    def _on_stt_threshold(self, value):
        db = max(-70, min(-10, round(-70 + 60 * float(value) / 100.0)))
        self.thr_lbl.config(text=f"{db} dB")
        self.cfg["stt_threshold_db"] = db
        self.voice.threshold_db = float(db)
        self._save_soon()

    # ---- voice model manager
    def open_manager(self):
        old = getattr(self, "mgr", None)
        if old is not None and old.winfo_exists():
            old.lift()
            old.focus_force()
            return
        w = tk.Toplevel(self.root)
        self.mgr = w
        w.title(tr("Voice models ♡"))
        w.configure(bg=BG)
        w.transient(self.root)
        w.geometry(f"+{self.root.winfo_x() + 40}+{self.root.winfo_y() + 40}")

        outer = tk.Frame(w, bg=BG, padx=18, pady=14)
        outer.pack(fill="both", expand=True)
        tk.Label(outer, text=tr("♡ Voice models"), bg=BG, fg=ACCENT_DARK,
                 font=(FONT, 15, "bold")).pack(anchor="w")
        tk.Label(outer, text=tr("add .aivmx files, or remove voices you don't use"), bg=BG, fg=MUTED,
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
            sel = lb.curselection()  # remember what was selected so a refresh doesn't lose it
            keep = state["meta"][sel[0]]["uuid"] if sel and sel[0] < len(state["meta"]) else None
            state["meta"] = rows
            lb.delete(0, "end")
            for x in rows:
                tags = []
                if x["default"]:
                    tags.append(tr("★ default"))
                elif x["private"]:
                    tags.append(tr("✿ yours"))
                if x["update"]:
                    tags.append(tr("⬆ update"))
                lb.insert("end", f"{x['name']}   v{x['version']}   {x['mb']:.0f} MB"
                          + ("   " + "  ".join(tags) if tags else ""))
            for i, x in enumerate(rows):
                if x["uuid"] == keep:
                    lb.selection_set(i)
                    lb.see(i)
            if not keep_note:
                n = tr("{n} model(s) installed.", n=len(rows))
                set_note(f"{done}  {n}" if done else n)

        def reload(done=None, keep_note=False):
            if not keep_note:
                set_note(tr("Loading…"))
            url = self.url_var.get().strip()

            def work():
                try:
                    infos = Aivis(url).models()
                except Exception as exc:
                    msg = tr("Can't reach AivisSpeech ({name}). Is the engine running?", name=exc.__class__.__name__)
                    self.ui(lambda: set_note(msg, True))
                    return
                self.ui(lambda: fill(infos, done, keep_note))

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
                self.ui(lambda: finish(err))

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
                set_note(tr("Select a voice model in the list first."), True)
                return None
            return state["meta"][sel[0]]

        def add_files():
            paths = filedialog.askopenfilenames(
                parent=w, title=tr("Choose voice model file(s)"),
                filetypes=[(tr("AIVMX voice model"), "*.aivmx"), (tr("All files"), "*.*")])
            if not paths:
                return

            def task(api):
                for p in paths:
                    try:
                        api.install_file(p)
                    except Exception as exc:
                        raise RuntimeError(f"{os.path.basename(p)}: {exc}")

            run(task, tr("Installing… (large models can take a moment)"), tr("Added {n} model(s).", n=len(paths)))

        def add_url():
            url = simpledialog.askstring(tr("Add from URL"), tr("Paste a direct .aivmx download URL:"), parent=w)
            if not url or not url.strip():
                return
            url = url.strip()
            run(lambda api: api.install_url(url), tr("Downloading and installing…"), tr("Added model from URL."))

        def remove():
            x = selected()
            if not x:
                return
            msg = tr("Remove “{name}”?\nThe model file will be deleted.", name=x["name"])
            if x["default"]:
                msg += tr("\n\nThis is one of AivisSpeech's default models. The engine installs its "
                          "default models again whenever it starts.")
            if not messagebox.askyesno(tr("Remove voice"), msg, parent=w):
                return
            run(lambda api: api.uninstall(x["uuid"]), tr("Removing…"), tr("Removed “{name}”.", name=x["name"]))

        def update():
            x = selected()
            if not x:
                return
            if not x["update"]:
                set_note(tr("“{name}” is already up to date.", name=x["name"]))
                return
            run(lambda api: api.update_model(x["uuid"]), tr("Updating…"), tr("Updated “{name}”.", name=x["name"]))

        btns = tk.Frame(outer, bg=BG)
        btns.pack(fill="x", pady=(10, 0))
        ttk.Button(btns, text=tr("+ Add file…"), style="Cute.TButton", command=add_files).pack(side="left")
        ttk.Button(btns, text=tr("+ From URL…"), style="Cute.TButton", command=add_url).pack(side="left", padx=6)
        ttk.Button(btns, text="↻", style="Soft.TButton", width=3, command=reload).pack(side="left")
        ttk.Button(btns, text=tr("✕ Remove"), style="Soft.TButton", command=remove).pack(side="right")
        ttk.Button(btns, text=tr("⬆ Update"), style="Soft.TButton", command=update).pack(side="right", padx=6)
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
            stt_enabled=self.stt_var.get(),
            stt_model=self._stt_size(),
            stt_language=self._stt_lang(),
            stt_mute_while_speaking=self.stt_mute_var.get(),
        )
        # Never overwrite a stored voice/device with an empty value
        # (e.g. before the lists are loaded, or if AivisSpeech wasn't running).
        if self.voice_var.get():
            self.cfg["speaker_label"] = self.voice_var.get()
        if self.dev_var.get():
            self.cfg["output_device"] = self.dev_var.get()
        if self.mic_var.get():
            self.cfg["mic_device"] = self.mic_var.get()
        if self.stt_mic_var.get():
            self.cfg["stt_mic"] = self.stt_mic_var.get()
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
            self.set_status(tr("Can't reach AivisSpeech ({name}). Is it running?", name=e.__class__.__name__), True)
            self.voice_cb["values"] = []
            return
        labels = [l for l, _ in self.speakers]
        self.voice_cb["values"] = labels
        if self.cfg["speaker_label"] in labels:
            self.voice_var.set(self.cfg["speaker_label"])
        elif labels:
            self.voice_var.set(labels[0])
        self._save()
        self.set_status(tr("{n} voices loaded. Focus VRChat and press Y.", n=len(labels)))

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

    # ---- voice input: logic
    def refresh_inputs(self):
        try:
            self.inputs = input_devices()
        except Exception:
            self.inputs = [("System default", None)]
        labels = [l for l, _ in self.inputs]
        self.stt_mic_cb["values"] = labels
        self.stt_mic_var.set(self.cfg["stt_mic"] if self.cfg["stt_mic"] in labels else labels[0])

    def _stt_lang(self):
        """The recognition language as stored/used internally ('Auto', 'Japanese', ...)."""
        cur = self.stt_lang_var.get()
        for k in STT_LANGUAGES:
            if tr(k) == cur:
                return k
        return "Auto"

    def _on_ui_language(self):
        """Switch the language of the app's own text (English / 日本語), live."""
        code = next((c for c, n in UI_LANGS.items() if n == self.ui_lang_var.get()), "en")
        size, lang = self._stt_size(), self._stt_lang()  # read these before the labels change
        set_ui_language(code)
        self.cfg["ui_language"] = code
        self._retranslate(self.root)
        mgr = getattr(self, "mgr", None)
        if mgr is not None and mgr.winfo_exists():
            mgr.title(tr("Voice models ♡"))
            self._retranslate(mgr)
        labels = stt_labels()
        self.stt_model_cb["values"] = list(labels.values())
        self.stt_model_var.set(labels[size])
        self.stt_lang_cb["values"] = [tr(k) for k in STT_LANGUAGES]
        self.stt_lang_var.set(tr(lang))
        self._set_voice_open(self._voice_open)
        self._refresh_stt_model_ui()
        self._on_stt_silence(self.sil_var.get())
        if self.speakers:
            self.set_status(tr("{n} voices loaded. Focus VRChat and press Y.", n=len(self.speakers)))
        else:
            self.set_status(tr("Focus VRChat and press Y."))
        self._save()

    def _retranslate(self, w):
        """Re-text every label/button whose text is one of our known phrases."""
        try:
            cur = w.cget("text")
        except tk.TclError:
            cur = None
        if isinstance(cur, str) and cur:
            src = _JA_REV.get(cur, cur)
            if src in JA:
                new = tr(src)
                if new != cur:
                    w.configure(text=new)
        for c in w.winfo_children():
            self._retranslate(c)

    def _stt_size(self):
        for key, lab in stt_labels().items():
            if lab == self.stt_model_var.get():
                return key
        return "0.6B"

    def _stt_mic_index(self):
        for lab, idx in self.inputs:
            if lab == self.stt_mic_var.get():
                return idx
        return None

    # -- model download / delete
    def _refresh_stt_model_ui(self):
        key = self._stt_size()
        busy = self._dl is not None
        have = stt_downloaded(key)
        if busy:
            text = tr("Downloading…")
        elif have:
            text = tr("✓ Downloaded")
        else:
            text = tr("Not downloaded ({approx})", approx=tr(STT_MODELS[key]["approx"]))
        self.stt_status_lbl.config(text=text)
        self.dl_btn.config(text=tr("✕ Cancel" if busy else "⬇ Download"),
                           state="normal" if (busy or not have) else "disabled")
        self.del_btn.config(state="normal" if (have and not busy) else "disabled")
        self.stt_model_cb.config(state="disabled" if busy else "readonly")

    def _on_stt_download(self, then_listen=False):
        if self._dl:  # while downloading this button is Cancel
            self._dl["cancel"].set()
            return
        key = self._stt_size()
        if stt_downloaded(key):
            return
        self.set_status(tr("Checking the size of {name}…", name=STT_MODELS[key]["name"]))

        def probe():
            size = stt_download_size(key)
            self.ui(lambda: confirm(size))

        def confirm(size):
            m = STT_MODELS[key]
            how_big = f"{size / 1e9:.2f} GB" if size and size >= 1e9 else (
                f"{size / 1e6:.0f} MB" if size else tr(m["approx"]))
            try:
                os.makedirs(MODELS_DIR, exist_ok=True)
                free = shutil.disk_usage(MODELS_DIR).free
            except OSError:
                free = None
            if size and free is not None and free < size * 1.3:
                self.set_status(tr("Not enough free disk space for {name} ({size}).", name=m["name"], size=how_big), True)
                messagebox.showerror(tr("Not enough space"),
                                     tr("{name} needs about {size} of free disk space.",
                                        name=m["name"], size=how_big), parent=self.root)
                return
            if not messagebox.askyesno(
                    tr("Download voice model"),
                    tr("Download {name}?\n\nSize: {size}.\nSaved in:\n{dir}\n\nIt is only downloaded once. "
                       "You can delete it again here.", name=m["name"], size=how_big, dir=MODELS_DIR),
                    parent=self.root):
                self.set_status(tr("Download cancelled."))
                return
            self._start_download(key, then_listen)

        threading.Thread(target=probe, daemon=True).start()

    def _start_download(self, key, then_listen):
        cancel = threading.Event()
        self._dl = {"key": key, "cancel": cancel}
        self.dl_bar.config(mode="determinate", value=0)
        self._refresh_stt_model_ui()
        last = [0.0]

        def progress(done, total):
            now = time.monotonic()
            if now - last[0] < 0.15 and not (total and done >= total):
                return
            last[0] = now
            self.ui(lambda: self._show_progress(key, done, total))

        def work():
            try:
                download_stt(key, progress, cancel)
            except _Cancelled:
                self.ui(lambda: finished(tr("Download cancelled.")))
            except Exception as e:
                msg = tr("Download failed: {e}", e=e)
                self.ui(lambda: finished(msg, err=True))
            else:
                self.ui(lambda: finished(tr("{name} downloaded.", name=STT_MODELS[key]["name"]), ok=True))

        def finished(msg, err=False, ok=False):
            self._dl = None
            self.dl_bar.stop()
            self.dl_bar.config(mode="determinate", value=0)
            self._refresh_stt_model_ui()
            self.set_status(msg, err)
            if ok and (then_listen or self.stt_var.get()) and self._stt_size() == key:
                self.stt_var.set(True)
                self._save()
                self._start_listening()

        threading.Thread(target=work, daemon=True).start()

    def _show_progress(self, key, done, total):
        if self._dl is None:
            return
        name = STT_MODELS[key]["name"]
        if total:
            self.dl_bar.config(mode="determinate", value=min(100.0, done / total * 100.0))
            self.set_status(tr("Downloading {name}… {done} / {total} MB", name=name,
                               done=f"{done / 1e6:.0f}", total=f"{total / 1e6:.0f}"))
        else:
            self.dl_bar.config(mode="indeterminate")
            self.dl_bar.step(3)
            self.set_status(tr("Downloading {name}… {done} MB", name=name, done=f"{done / 1e6:.0f}"))

    def _on_stt_delete(self):
        key = self._stt_size()
        if self._dl or not stt_downloaded(key):
            return
        name = STT_MODELS[key]["name"]
        if not messagebox.askyesno(tr("Delete voice model"),
                                   tr("Delete the downloaded {name}?\nYou can download it again later.", name=name),
                                   parent=self.root):
            return
        if self.asr.key == key:  # it's the one in use: stop listening first
            self._stt_gen += 1
            self.voice.stop()
            self.asr.unload()
            self.stt_var.set(False)
            self._save()
        shutil.rmtree(stt_dir(key), ignore_errors=True)
        self._refresh_stt_model_ui()
        self.set_status(tr("{name} deleted.", name=name))

    # -- listening
    def _on_stt_toggle(self):
        if self.stt_var.get():
            self._set_voice_open(True)
            if not stt_downloaded(self._stt_size()):
                # stay off until the model is on this PC, then switch on by itself
                self._stt_gen += 1
                self.voice.stop()
                self.stt_var.set(False)
                self._save()
                self._on_stt_download(then_listen=True)
                return
            self._save()
            self._start_listening()
        else:
            self._set_voice_open(False)
            self._save()
            self._stt_gen += 1
            self.voice.stop()
            self.set_status(tr("Mic input mode off."))

    def _on_stt_model(self):
        self._save()
        self._refresh_stt_model_ui()
        if self.stt_var.get():
            self._on_stt_toggle()  # same path as turning it on, for the newly chosen model

    def _on_stt_language(self):
        self._save()
        if self.stt_var.get() and self.asr.ready:
            self._start_listening()  # reload so the new language takes effect

    def _on_stt_mic(self):
        self._save()
        if self.stt_var.get() and self.asr.ready:
            self.voice.start(self._stt_mic_index())

    def _start_listening(self):
        self._stt_gen += 1
        gen = self._stt_gen
        key = self._stt_size()
        name = STT_MODELS[key]["name"]
        lang = self._stt_lang()
        self.voice.stop()
        self.set_status(tr("Loading {name}… (a few seconds)", name=name))

        def ready(device):
            if gen != self._stt_gen or not self.stt_var.get():
                return  # switched off or restarted while loading
            self.voice.start(self._stt_mic_index())
            msg = tr("Listening with {name} on {device} ({lang}). Just talk!", name=name, device=device, lang=tr(lang))
            if self.asr.lang_note:
                msg += "\n" + self.asr.lang_note
            self.set_status(msg)

        def fail(msg):
            if gen != self._stt_gen:
                return
            self.stt_var.set(False)
            self._save()
            self.set_status(msg, True)

        def work():
            try:
                device = self.asr.load(key, lang)
            except ImportError as e:
                msg = tr("Voice input needs the sherpa-onnx package ({e}). pip install -U sherpa-onnx", e=e)
                self.ui(lambda: fail(msg))
            except Exception as e:
                msg = tr("Couldn't load {name}: {e}", name=name, e=e)
                self.ui(lambda: fail(msg))
            else:
                self.ui(lambda: ready(device))

        threading.Thread(target=work, daemon=True).start()

    def _stt_on_start(self):  # speech detected: show the typing bubble
        if not self.overlay:
            self.set_typing(True)

    def _stt_on_discard(self):  # too short / cancelled: nothing to send
        if not self.overlay:
            self.set_typing(False)

    def _stt_worker(self):
        """Transcribes utterances one at a time; the mic keeps listening meanwhile."""
        while True:
            audio, sr = self.stt_q.get()
            if not self.voice.running:  # listening was switched off or restarted: drop stale audio
                continue
            text = ""
            try:
                text = self.asr.transcribe(audio, sr)
            except Exception as e:
                msg = tr("Speech recognition failed: {e}", e=e)
                self.ui(lambda: self.set_status(msg, True))
            if not self.overlay:
                self.set_typing(False)
            if text:
                self.ui(lambda t=text: self._on_heard(t))

    def _on_heard(self, text):
        self.set_status(tr("Heard: {text}", text=text))
        self.send(text)

    # ---- sending
    def test_voice(self):
        self.speak("こんにちは、テストです。")

    def speak(self, text):
        sid = self.current_speaker_id()
        if sid is None:
            self.root.after(0, lambda: self.set_status(tr("No voice selected."), True))
            return
        url = self.url_var.get().strip()

        def work():
            try:
                self.player.play(Aivis(url).synth(text, sid))
            except Exception as e:
                msg = tr("TTS failed: {e}", e=e)
                self.ui(lambda: self.set_status(msg, True))

        threading.Thread(target=work, daemon=True).start()

    def send(self, text):
        if self.cfg["send_chatbox"]:
            try:
                args = [text[:CHATBOX_LIMIT], True]
                if not self.cfg["chatbox_sound"]:
                    args.append(False)  # VRChat plays the sound by default, so only send n to mute it
                self.osc.send_message("/chatbox/input", args)
            except Exception as e:
                self.set_status(tr("OSC send failed: {e}", e=e), True)
        if self.cfg["speak"]:
            self.speak(text)

    def set_typing(self, on):
        try:
            self.osc.send_message("/chatbox/typing", bool(on))
        except Exception:
            pass

    # ---- overlay
    def ui(self, fn):
        """Run fn on the main (Tk) thread. Safe to call from background threads."""
        self.ui_q.put(fn)

    def poll(self):
        try:
            while True:
                try:
                    kind, arg = self.q.get_nowait()
                except queue.Empty:
                    break
                if kind == "open":
                    self.open_overlay(arg)
            while True:
                try:
                    fn = self.ui_q.get_nowait()
                except queue.Empty:
                    break
                try:
                    fn()
                except Exception as e:  # a bad callback must never stop the polling loop
                    print("ui callback error:", e, file=sys.stderr)
        finally:
            self.root.after(30, self.poll)  # always keep polling, or the hotkey would stop working

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
            c.itemconfig(hint, text=f"♡ {n}/{CHATBOX_LIMIT}     " + tr("Enter = send   ·   Esc = cancel"),
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
                    self.set_status(tr("OSC send failed: {e}", e=e), True)

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
    import multiprocessing
    multiprocessing.freeze_support()
    App().run()