"""Jarvis 2.0 - live voice (Gemini Live) + full HUD interface.

Needs:  pip install sounddevice psutil   (everything else you already installed)
Run:    python main.py
"""
import array, asyncio, json, math, os, queue, re, subprocess, sys, threading, time, webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path

import pyautogui, pyperclip
import sounddevice as sd
from google import genai
from google.genai import types

try:
    import psutil
except Exception:
    psutil = None
try:                                   # drag & drop:  pip install tkinterdnd2
    from tkinterdnd2 import TkinterDnD, DND_FILES
except Exception:
    TkinterDnD = DND_FILES = None
try:                                   # optional PDF reading:  pip install pypdf
    import pypdf
except Exception:
    pypdf = None

# pythonw.exe (start-with-Windows) has no console, so print() would crash without this
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

# ======================= SETTINGS =======================
# NOTE: there is no API key in this file. You type it into the setup screen on first run.
MODELS = [
    "models/gemini-3.1-flash-live-preview",
    "models/gemini-2.5-flash-native-audio-preview-12-2025",
]
SEND_RATE, RECV_RATE = 16000, 24000
DATA_DIR = Path(__file__).parent
MEMORY_FILE = DATA_DIR / "memory.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
MAX_CODE_CHARS = 40000
pyautogui.PAUSE = 0.05

# ======================= THEMES (colours) =======================
THEMES = {
    "Cyan":    "#00e5ff",
    "Crimson": "#ff2d55",
    "Orange":  "#ff9500",
    "Purple":  "#b266ff",
    "Green":   "#2cff8f",
    "Ice":     "#9ad1ff",
    "Gold":    "#ffd02b",
}
BG, PANEL, TEXT_DIM = "#05070d", "#0b1020", "#7d8aa8"
INPUT_BG = "#060a14"

# ======================= VOICE PACKS =======================
# Original voices + speaking style (NOT copies of the real voice actors).
# To change a voice, edit "voice": Puck, Charon, Kore, Fenrir, Aoede, Leda, Orus, Zephyr
PACKS = {
    "jarvis": {
        "label": "JARVIS", "voice": "Orus", "theme": "Cyan",
        "style": "Speak like a polite, sharp, calm AI butler. Short sentences.",
    },
    "madara": {
        "label": "MADARA", "voice": "Charon", "theme": "Crimson",
        "style": "Speak like a proud, calm, ancient warrior: slow, deep, confident, a little dramatic. "
                 "Call the user 'boy'. Short sentences.",
    },
    "kakashi": {
        "label": "KAKASHI", "voice": "Fenrir", "theme": "Ice",
        "style": "Speak like a relaxed, lazy but wise ninja teacher: calm, dry humor, casual. "
                 "Short sentences. Occasionally say 'Yo.'",
    },
    "naruto": {
        "label": "NARUTO", "voice": "Puck", "theme": "Orange",
        "style": "Speak like an energetic, loud, cheerful and determined young ninja. "
                 "Say 'Believe it!' sometimes. Short sentences.",
    },
    "gojo": {
        "label": "GOJO", "voice": "Zephyr", "theme": "Purple",
        "style": "Speak like a cocky, playful, super-confident sorcerer: relaxed, teasing, upbeat. "
                 "Short sentences.",
    },
    "friday": {
        "label": "FRIDAY", "voice": "Kore", "theme": "Green",
        "style": "Speak like a warm, efficient, friendly AI assistant with a light Irish-style charm. "
                 "Short sentences.",
    },
}

def load_settings():
    s = {"pack": "jarvis", "theme": "Cyan", "name": "Sayan",
         "place": "New Delhi, India", "lat": 28.6139, "lon": 77.2090, "greet": True,
         "assistant": "Jarvis", "api_key": "", "setup_done": False}
    try:
        s.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    if s["pack"] not in PACKS:
        s["pack"] = "jarvis"
    if s["theme"] not in THEMES:
        s["theme"] = PACKS[s["pack"]]["theme"]
    return s

def save_settings():
    try:
        SETTINGS_FILE.write_text(json.dumps(SET, indent=2), encoding="utf-8")
    except Exception:
        pass

SET = load_settings()

def get_key():
    """API key typed in the setup screen (or GEMINI_API_KEY if you set it as an environment variable)."""
    return (SET.get("api_key") or "").strip() or os.environ.get("GEMINI_API_KEY", "")
STATE = {"restart": False, "ui_restart": False}
UI = {"mic": 0.0, "out": 0.0, "working": 0, "connected": False}
LOG: "queue.Queue[tuple]" = queue.Queue()
INJECT_Q: "queue.Queue[str]" = queue.Queue()      # code/text typed in the HUD -> live session

def log(tag, text):
    print(f"[{tag}] {text}")
    LOG.put((tag, text))

def fmt_lat(v):
    return f"{abs(v):.4f}\u00b0 {'N' if v >= 0 else 'S'}"

def fmt_lon(v):
    return f"{abs(v):.4f}\u00b0 {'E' if v >= 0 else 'W'}"

# ======================= START WITH WINDOWS =======================
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

def is_autostart():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, "Jarvis2")
        return True
    except Exception:
        return False

def set_autostart(on: bool):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            exe = Path(sys.executable)
            pw = exe.with_name("pythonw.exe")          # no black console window
            if pw.exists():
                exe = pw
            cmd = f'"{exe}" "{Path(__file__).resolve()}" --startup'
            winreg.SetValueEx(k, "Jarvis2", 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(k, "Jarvis2")
            except FileNotFoundError:
                pass

# ======================= DROPPED FILES =======================
IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".webp": "image/webp", ".gif": "image/gif"}

def prepare_file(path):
    """Turn a dropped file into items for the live session. Returns (items, error)."""
    p = Path(path)
    if not p.is_file():
        return None, f"{p.name}: not a file"
    ext = p.suffix.lower()
    if ext in IMAGE_MIME:
        data = p.read_bytes()
        if len(data) > 5_000_000:
            return None, f"{p.name}: image is over 5 MB"
        return [("image", data, IMAGE_MIME[ext], p.name)], None
    if ext == ".pdf":
        if not pypdf:
            return None, "to read PDFs run:  pip install pypdf"
        reader = pypdf.PdfReader(str(p))
        body = "\n".join((pg.extract_text() or "") for pg in reader.pages[:40])
    else:
        raw = p.read_bytes()
        if b"\x00" in raw[:2048]:
            return None, f"{p.name}: binary file, can't read it"
        body = raw.decode("utf-8", errors="replace")
    body = body.strip()
    if not body:
        return None, f"{p.name}: nothing readable inside"
    if len(body) > MAX_CODE_CHARS:
        body = body[:MAX_CODE_CHARS] + "\n...[file cut here, too long]"
    msg = (f"[FILE FROM USER - {p.name}]\n{body}\n[END FILE]\n"
           "I dropped this file for you to analyse. Give me a short spoken summary "
           "(two sentences at most), then wait for my questions.")
    return [msg], None

# ======================= MEMORY (likes / dislikes) =======================
def load_memory():
    try:
        return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_memory(mem):
    MEMORY_FILE.write_text(json.dumps(mem, indent=2, ensure_ascii=False), encoding="utf-8")

# ======================= TOOLS =======================
SITES = {"youtube": "youtube.com", "google": "google.com", "gmail": "mail.google.com",
         "instagram": "instagram.com", "facebook": "facebook.com", "chatgpt": "chatgpt.com",
         "github": "github.com", "maps": "maps.google.com"}

def tool_open_website(name_or_url: str):
    key = name_or_url.lower().replace(" ", "")
    url = SITES.get(key, name_or_url)
    webbrowser.open(url if url.startswith("http") else "https://" + url)
    return f"opened {url}"

def desktop_dir() -> Path:
    # Some PCs keep the Desktop inside OneDrive; use whichever one really exists.
    for base in (os.environ.get("OneDrive"), str(Path.home())):
        if base and (Path(base) / "Desktop").is_dir():
            return Path(base) / "Desktop"
    return Path.home() / "Desktop"

def tool_open_app(name: str):
    folders = [
        os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
        os.path.expandvars(r"%AppData%\Microsoft\Windows\Start Menu\Programs"),
        str(desktop_dir()),
    ]
    name = name.lower()
    for folder in folders:
        for root, _, files in os.walk(folder):
            for f in files:
                if f.lower().endswith((".lnk", ".url")) and name in f.lower():
                    os.startfile(os.path.join(root, f))
                    return f"opened {f}"
    return "app not found"

def tool_create_file(filename: str, content: str = ""):
    path = desktop_dir() / Path(filename).name      # .name blocks "..\\" tricks
    path.write_text(content, encoding="utf-8")
    return f"created {path}"

def _paste(text):
    pyperclip.copy(text)
    time.sleep(0.1)
    pyautogui.hotkey("ctrl", "v")

def tool_send_whatsapp(name: str, message: str):
    pyautogui.press("win")
    time.sleep(0.6)
    _paste("WhatsApp")
    time.sleep(0.8)
    pyautogui.press("enter")
    time.sleep(4)
    pyautogui.hotkey("ctrl", "f")
    time.sleep(0.6)
    pyautogui.hotkey("ctrl", "a")
    pyautogui.press("delete")
    _paste(name)
    time.sleep(1.2)
    pyautogui.press("enter")
    time.sleep(0.9)
    _paste(message)
    time.sleep(0.3)
    pyautogui.press("enter")
    return (f"typed the message to {name} in WhatsApp and pressed send; "
            f"not verified, tell the user to glance at the screen")

def tool_remember(key: str, value: str):
    mem = load_memory()
    mem[key] = value
    save_memory(mem)
    return "saved"

def tool_get_datetime():
    return time.strftime("%A, %d %B %Y, %H:%M")

def tool_change_voice_pack(pack: str):
    pack = pack.lower().strip()
    if pack not in PACKS:
        return f"unknown pack, choose from {list(PACKS)}"
    SET["pack"], SET["theme"] = pack, PACKS[pack]["theme"]
    save_settings()
    STATE["restart"] = True      # reconnect with the new voice after this reply
    return f"switching to {pack}"

ALIASES = {"red": "Crimson", "blue": "Cyan", "yellow": "Gold", "violet": "Purple",
           "white": "Ice", "light blue": "Ice", "lime": "Green", "amber": "Gold"}

def tool_change_color(color: str):
    color = ALIASES.get(color.lower().strip(), color)
    for name in THEMES:
        if name.lower() == color.lower().strip():
            SET["theme"] = name
            save_settings()
            return f"interface colour is now {name}"
    return f"unknown colour, choose from {list(THEMES)}"

TOOL_FUNCS = {
    "open_website": tool_open_website,
    "open_app": tool_open_app,
    "create_file": tool_create_file,
    "send_whatsapp": tool_send_whatsapp,
    "remember": tool_remember,
    "get_datetime": tool_get_datetime,
    "change_voice_pack": tool_change_voice_pack,
    "change_color": tool_change_color,
}

def S(**props):
    return {"type": "OBJECT",
            "properties": {k: {"type": "STRING", "description": v} for k, v in props.items()},
            "required": list(props)}

DECLARATIONS = [
    {"name": "open_website", "description": "Open a website in the browser by name or URL (e.g. youtube, google.com).",
     "parameters": S(name_or_url="site name or URL")},
    {"name": "open_app", "description": "Open an installed app or game by name (needs a Start Menu or Desktop shortcut).",
     "parameters": S(name="app or game name")},
    {"name": "create_file", "description": "Create a text or code file on the Desktop.",
     "parameters": S(filename="file name with extension", content="full file content")},
    {"name": "send_whatsapp", "description": "Send a WhatsApp message to a contact by searching their name.",
     "parameters": S(name="contact name exactly as saved", message="message text")},
    {"name": "remember", "description": "Save a fact about the user: likes, dislikes, preferences, names.",
     "parameters": S(key="short label", value="the fact")},
    {"name": "get_datetime", "description": "Get the current date and time on the user's PC."},
    {"name": "change_voice_pack", "description": "Change Jarvis voice character: " + ", ".join(PACKS) + ".",
     "parameters": S(pack="one of: " + ", ".join(PACKS))},
    {"name": "change_color", "description": "Change the interface colour: " + ", ".join(THEMES) + ".",
     "parameters": S(color="one of: " + ", ".join(THEMES))},
]

# ======================= AUDIO =======================
out_q: "queue.Queue[bytes]" = queue.Queue()
audio_state = {"last_audio": 0.0}

def pcm_level(b: bytes) -> float:
    """Loudness 0..1 of 16-bit PCM audio (for the orb animation)."""
    a = array.array("h")
    a.frombytes(b[: len(b) // 2 * 2])
    if not a:
        return 0.0
    step = max(1, len(a) // 64)
    sample = a[::step]
    return min(1.0, (sum(abs(x) for x in sample) / len(sample)) / 6000)

def playback_thread():
    stream = sd.RawOutputStream(samplerate=RECV_RATE, channels=1, dtype="int16")
    stream.start()
    while True:
        chunk = out_q.get()
        audio_state["last_audio"] = time.time()
        UI["out"] = pcm_level(chunk)
        stream.write(chunk)
        audio_state["last_audio"] = time.time()

def jarvis_is_speaking():
    return (not out_q.empty()) or (time.time() - audio_state["last_audio"] < 0.6)

def clear_playback():
    try:
        while True:
            out_q.get_nowait()
    except queue.Empty:
        pass

# ======================= LIVE SESSION =======================
def build_config():
    pack = PACKS[SET["pack"]]
    mem = load_memory()
    mem_text = "\n".join(f"- {k}: {v}" for k, v in mem.items()) or "(nothing yet)"
    system = (
        f"You are {SET['assistant']}, a fast voice assistant on the user's Windows PC. "
        f"The user is {SET['name']}. They are located at {SET['place']} "
        f"({fmt_lat(SET['lat'])}, {fmt_lon(SET['lon'])}). Right now it is {tool_get_datetime()}. "
        "Use your tools to open websites, apps, games, create files, "
        "send WhatsApp messages, change your voice character and change the interface colour. "
        "When the user shares a like, dislike or preference, call remember. "
        "Sometimes the user pastes code or text into the interface; it arrives as a message that starts with "
        "[CODE FROM USER]. Read it carefully, keep it in mind for the rest of the session, and answer "
        "questions about it, explain it, find bugs, or improve it. When code arrives, only say one short "
        "sentence confirming you received it. "
        "Files the user drops into the interface arrive as [FILE FROM USER - name] (text, PDF text, or an "
        "image). Analyse them, give a short spoken summary, then answer follow-up questions. "
        "A message starting with [SYSTEM EVENT] is an event from the interface, not the user speaking; "
        "follow it naturally. "
        "Keep every spoken reply to one or two short sentences. Do the action first, then confirm briefly.\n"
        f"VOICE STYLE: {pack['style']}\n"
        f"WHAT YOU KNOW ABOUT THE USER:\n{mem_text}"
    )
    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=system,
        tools=[{"function_declarations": DECLARATIONS}],
        input_audio_transcription={},
        output_audio_transcription={},
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=pack["voice"])
            )
        ),
    )

async def run_session(client, model):
    loop = asyncio.get_running_loop()
    mic_q: asyncio.Queue = asyncio.Queue(maxsize=50)

    def mic_cb(indata, frames, t, status):
        data = bytes(indata)
        if jarvis_is_speaking():          # mute mic while Jarvis talks (prevents echo loop)
            UI["mic"] = 0.0
            return
        UI["mic"] = pcm_level(data)
        loop.call_soon_threadsafe(lambda: mic_q.put_nowait(data) if not mic_q.full() else None)

    async with client.aio.live.connect(model=model, config=build_config()) as session:
        log("System", f"online - {model.split('/')[-1]} - voice: {PACKS[SET['pack']]['label']}")
        UI["connected"] = True
        STATE["restart"] = False
        STATE["ui_restart"] = False
        stop = asyncio.Event()
        text = {"You": "", "Jarvis": ""}

        def flush_text():
            for who in ("You", "Jarvis"):
                t = text[who].strip()
                text[who] = ""
                if not t:
                    continue
                log(who, t)
                if who == "You" and re.fullmatch(
                        r"\W*(exit|quit|goodbye|stop " + re.escape(SET["assistant"].lower()) + r")\W*", t, re.I):
                    os._exit(0)

        async def send_mic():
            with sd.RawInputStream(samplerate=SEND_RATE, channels=1, dtype="int16",
                                   blocksize=1024, callback=mic_cb):
                while not stop.is_set():
                    data = await mic_q.get()
                    await session.send_realtime_input(
                        audio=types.Blob(data=data, mime_type=f"audio/pcm;rate={SEND_RATE}"))

        async def watch_ui():
            # The voice buttons in the window ask for a reconnect.
            while not stop.is_set():
                await asyncio.sleep(0.2)
                if STATE["ui_restart"]:
                    stop.set()
                    return

        async def pump_inject():
            # Code / text typed into the HUD is delivered to the live AI here.
            while not stop.is_set():
                try:
                    msg = INJECT_Q.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.2)
                    continue
                try:
                    if isinstance(msg, tuple):                    # an image: ("image", bytes, mime, name)
                        _, data, mime, name = msg
                        await session.send_realtime_input(video=types.Blob(data=data, mime_type=mime))
                        await session.send_realtime_input(
                            text=f"[FILE FROM USER - {name}] I dropped this image. Describe and analyse it "
                                 "in one or two short spoken sentences, then wait for my questions.")
                    else:
                        try:
                            await session.send_realtime_input(text=msg)
                        except Exception:
                            await session.send_client_content(
                                turns=types.Content(role="user", parts=[types.Part(text=msg)]),
                                turn_complete=True)
                    log("System", "delivered to the live session")
                except Exception as e:
                    log("System", f"could not deliver to Jarvis: {str(e)[:120]}")

        async def receive():
            while not stop.is_set():
                async for resp in session.receive():
                    if resp.data:
                        out_q.put(resp.data)
                    sc = resp.server_content
                    if sc:
                        if getattr(sc, "interrupted", False):
                            clear_playback()
                        if sc.input_transcription and sc.input_transcription.text:
                            text["You"] += sc.input_transcription.text
                        if sc.output_transcription and sc.output_transcription.text:
                            text["Jarvis"] += sc.output_transcription.text
                        if sc.turn_complete:
                            flush_text()
                            if STATE["restart"]:
                                await asyncio.sleep(2.5)      # let the reply finish playing
                                stop.set()
                                return
                    if resp.tool_call:
                        flush_text()
                        results = []
                        for fc in resp.tool_call.function_calls:
                            args = dict(fc.args or {})
                            log("Tool", f"{fc.name} {args}")
                            fn = TOOL_FUNCS.get(fc.name)
                            UI["working"] += 1
                            try:
                                result = await asyncio.to_thread(fn, **args) if fn else "unknown tool"
                            except Exception as e:
                                result = f"error: {e}"
                            finally:
                                UI["working"] -= 1
                            results.append(types.FunctionResponse(
                                id=fc.id, name=fc.name, response={"result": result}))
                        await session.send_tool_response(function_responses=results)

        tasks = [asyncio.create_task(send_mic()), asyncio.create_task(receive()),
                 asyncio.create_task(watch_ui()), asyncio.create_task(pump_inject())]
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            for t in done:
                t.result()          # raise any error so the caller can reconnect
        finally:
            UI["connected"] = False

async def main():
    threading.Thread(target=playback_thread, daemon=True).start()
    model_i = 0
    while True:
        try:
            client = genai.Client(api_key=get_key(), http_options={"api_version": "v1beta"})
            await run_session(client, MODELS[model_i])
        except Exception as e:
            log("System", "session problem: " + str(e)[:150])
            if "not found" in str(e).lower() or "not supported" in str(e).lower():
                model_i = (model_i + 1) % len(MODELS)
            await asyncio.sleep(2)

# ======================= QUICK TOOLS (buttons on the right) =======================
def qt_youtube():  tool_open_website("youtube")
def qt_browser():  webbrowser.open("https://www.google.com")
def qt_vscode():   subprocess.Popen("code", shell=True)
def qt_notepad():  subprocess.Popen("notepad")
def qt_settings(): os.startfile("ms-settings:")

def qt_shutdown():
    if messagebox.askyesno("Shut down PC", "Shut down this PC in 15 seconds?"):
        os.system("shutdown /s /t 15")
        log("System", "PC shuts down in 15 s. Type  shutdown /a  in a terminal to cancel.")

QUICK_TOOLS = [("Open YouTube", qt_youtube), ("Open Browser", qt_browser), ("Open VS Code", qt_vscode),
               ("Open Notepad", qt_notepad), ("Open Settings", qt_settings), ("Shut Down PC", qt_shutdown)]

# ======================= INTERFACE =======================
def blend(c1, c2, t):
    """Mix two #rrggbb colours: t=0 -> c1, t=1 -> c2."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

def spaced(name):
    """'Nova' -> 'N O V A'  (words separated by three spaces)."""
    return "   ".join(" ".join(w) for w in name.upper().split())

FONT = "Segoe UI"

class HUD:
    def __init__(self, root):
        self.root = root
        self.lv = 0.0
        self.last_look = None
        self.panels = []          # (outer frame, title label) so colours follow the theme
        self.tab = "code"
        self.code_name = ""
        self.tick_n = 0
        accent = THEMES[SET["theme"]]

        root.title(SET["assistant"].upper())
        root.configure(bg=BG)
        root.geometry("1280x840")
        root.minsize(1120, 740)
        root.grid_columnconfigure(0, minsize=250)
        root.grid_columnconfigure(1, weight=1)
        root.grid_columnconfigure(2, minsize=350)
        root.grid_rowconfigure(1, weight=1)

        # ---------- header ----------
        self.head = tk.Canvas(root, height=72, bg=BG, highlightthickness=0)
        self.head.grid(row=0, column=0, columnspan=3, sticky="ew", padx=10, pady=(6, 0))

        # ---------- left column: voice packs + themes ----------
        left = tk.Frame(root, bg=BG)
        left.grid(row=1, column=0, sticky="nsew", padx=(10, 5), pady=6)
        outer, body = self.panel(left, "VOICE PACKS")
        outer.pack(fill="x")
        self.pack_btns = {}
        for name, p in PACKS.items():
            b = tk.Button(body, text=p["label"].title(), relief="flat", bd=0, anchor="w", padx=12, pady=5,
                          font=(FONT, 10, "bold"), cursor="hand2", command=lambda n=name: self.pick_pack(n))
            b.pack(fill="x", pady=1)
            self.pack_btns[name] = b

        outer, body = self.panel(left, "THEMES / COLOURS")
        outer.pack(fill="x", pady=(8, 0))
        self.theme_btns = {}
        for name, hexcol in THEMES.items():
            b = tk.Button(body, text="\u25cf  " + name, relief="flat", bd=0, anchor="w", padx=12, pady=3,
                          font=(FONT, 10), cursor="hand2", fg=hexcol, activeforeground=hexcol,
                          command=lambda n=name: self.pick_theme(n))
            b.pack(fill="x", pady=1)
            self.theme_btns[name] = b

        # ---------- centre: orb ----------
        self.canvas = tk.Canvas(root, bg=BG, highlightthickness=0)
        self.canvas.grid(row=1, column=1, sticky="nsew", pady=6)

        # ---------- right column: system status + quick tools ----------
        right = tk.Frame(root, bg=BG)
        right.grid(row=1, column=2, sticky="nsew", padx=(5, 10), pady=6)
        outer, body = self.panel(right, "SYSTEM STATUS")
        outer.pack(fill="x")
        self.bars, self.vals = {}, {}
        for key in ("CPU", "RAM", "DISK"):
            row = tk.Frame(body, bg=PANEL)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=key, bg=PANEL, fg="#c4cde4", font=(FONT, 9), width=9, anchor="w").pack(side="left")
            cv = tk.Canvas(row, width=120, height=8, bg=PANEL, highlightthickness=0)
            cv.pack(side="left", padx=4)
            val = tk.Label(row, text="--", bg=PANEL, font=(FONT, 9, "bold"), width=6, anchor="e")
            val.pack(side="right")
            self.bars[key], self.vals[key] = cv, val
        for key in ("NETWORK", "MICROPHONE", "SPEAKER"):
            row = tk.Frame(body, bg=PANEL)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=key, bg=PANEL, fg="#c4cde4", font=(FONT, 9), width=12, anchor="w").pack(side="left")
            val = tk.Label(row, text="--", bg=PANEL, font=(FONT, 9, "bold"), anchor="e")
            val.pack(side="right")
            self.vals[key] = val

        outer, body = self.panel(right, "QUICK TOOLS")
        outer.pack(fill="both", expand=True, pady=(8, 0))
        self.quick_btns = []
        for label, fn in QUICK_TOOLS:
            b = tk.Button(body, text=label, relief="flat", bd=0, anchor="w", padx=12, pady=5,
                          font=(FONT, 10), cursor="hand2", command=lambda f=fn: self.run_quick(f))
            b.pack(fill="x", pady=1)
            self.quick_btns.append(b)

        # ---------- bottom left: location card ----------
        outer, body = self.panel(root, "LOCATION")
        outer.grid(row=2, column=0, sticky="nsew", padx=(10, 5), pady=(0, 10))
        self.globe = tk.Canvas(body, width=76, height=76, bg=PANEL, highlightthickness=0)
        self.globe.pack(side="left")
        info = tk.Frame(body, bg=PANEL)
        info.pack(side="left", padx=8)
        self.loc_planet = tk.Label(info, text="EARTH", bg=PANEL, font=(FONT, 11, "bold"), anchor="w")
        self.loc_planet.pack(anchor="w")
        self.loc_place = tk.Label(info, text="", bg=PANEL, fg=TEXT_DIM, font=(FONT, 8), anchor="w")
        self.loc_place.pack(anchor="w")
        self.loc_lat = tk.Label(info, text="", bg=PANEL, fg="#d7def0", font=("Consolas", 10), anchor="w")
        self.loc_lat.pack(anchor="w")
        self.loc_lon = tk.Label(info, text="", bg=PANEL, fg="#d7def0", font=("Consolas", 10), anchor="w")
        self.loc_lon.pack(anchor="w")

        # ---------- bottom centre: conversation log ----------
        outer, body = self.panel(root, "CONVERSATION LOG")
        outer.grid(row=2, column=1, sticky="nsew", padx=5, pady=(0, 10))
        self.logbox = tk.Text(body, bg=PANEL, fg="#d7def0", bd=0, highlightthickness=0, wrap="word",
                              font=("Consolas", 10), height=9, padx=4, pady=2)
        self.logbox.pack(fill="both", expand=True)
        self.logbox.configure(state="disabled")

        # ---------- bottom right: tabs (code inject / customise) ----------
        outer = tk.Frame(root, bg=PANEL, highlightthickness=1)
        outer.grid(row=2, column=2, sticky="nsew", padx=(5, 10), pady=(0, 10))
        self.tab_outer = outer
        tabbar = tk.Frame(outer, bg=PANEL)
        tabbar.pack(fill="x", padx=8, pady=(6, 4))
        self.tab_btns = {}
        for key, label in (("code", "CODE INJECT"), ("cust", "CUSTOMISE")):
            b = tk.Button(tabbar, text=label, relief="flat", bd=0, padx=10, pady=3, cursor="hand2",
                          font=(FONT, 9, "bold"), command=lambda k=key: self.show_tab(k))
            b.pack(side="left", padx=(0, 4))
            self.tab_btns[key] = b
        holder = tk.Frame(outer, bg=PANEL)
        holder.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # -- code tab
        self.tab_code = tk.Frame(holder, bg=PANEL)
        self.code_box = tk.Text(self.tab_code, bg=INPUT_BG, fg="#e6ecff", insertbackground="#ffffff", bd=0,
                                highlightthickness=1, highlightbackground="#1a2236", wrap="none",
                                font=("Consolas", 9), height=7, padx=6, pady=4, undo=True)
        self.code_box.pack(fill="both", expand=True)
        self.code_box.bind("<Control-Return>", lambda e: (self.send_code(), "break")[1])
        brow = tk.Frame(self.tab_code, bg=PANEL)
        brow.pack(fill="x", pady=(5, 0))
        self.send_btn = tk.Button(brow, text="SEND TO JARVIS", relief="flat", bd=0, padx=10, pady=4,
                                  font=(FONT, 9, "bold"), cursor="hand2", command=self.send_code)
        self.send_btn.pack(side="left")
        self.load_btn = tk.Button(brow, text="LOAD FILE", relief="flat", bd=0, padx=10, pady=4,
                                  font=(FONT, 9, "bold"), cursor="hand2", command=self.load_code_file)
        self.load_btn.pack(side="left", padx=4)
        self.clear_btn = tk.Button(brow, text="CLEAR", relief="flat", bd=0, padx=10, pady=4,
                                   font=(FONT, 9, "bold"), cursor="hand2",
                                   command=lambda: self.code_box.delete("1.0", "end"))
        self.clear_btn.pack(side="left")
        tk.Label(self.tab_code, text="Ctrl+Enter = send   |   drop files anywhere in this window",
                 bg=PANEL, fg=TEXT_DIM, font=(FONT, 8)).pack(anchor="w")

        # -- customise tab
        self.tab_cust = tk.Frame(holder, bg=PANEL)
        self.entries = {}
        for i, (key, label) in enumerate((("name", "Your name"), ("assistant", "Assistant name"),
                                          ("place", "Place"),
                                          ("lat", "Lat (+N / -S)"), ("lon", "Lon (+E / -W)"))):
            tk.Label(self.tab_cust, text=label, bg=PANEL, fg="#c4cde4", font=(FONT, 9), anchor="w",
                     width=10).grid(row=i, column=0, sticky="w", pady=3)
            e = tk.Entry(self.tab_cust, bg=INPUT_BG, fg="#ffffff", insertbackground="#ffffff", bd=0,
                         highlightthickness=1, highlightbackground="#1a2236", font=(FONT, 10))
            e.grid(row=i, column=1, sticky="ew", pady=3, ipady=3)
            e.insert(0, str(SET[key]))
            self.entries[key] = e
        self.tab_cust.grid_columnconfigure(1, weight=1)
        self.save_btn = tk.Button(self.tab_cust, text="SAVE", relief="flat", bd=0, padx=14, pady=4,
                                  font=(FONT, 9, "bold"), cursor="hand2", command=self.save_custom)
        self.greet_var = tk.IntVar(value=1 if SET.get("greet", True) else 0)
        self.auto_var = tk.IntVar(value=1 if is_autostart() else 0)
        ck = dict(bg=PANEL, fg="#c4cde4", selectcolor=INPUT_BG, activebackground=PANEL,
                  activeforeground="#ffffff", font=(FONT, 9), bd=0, highlightthickness=0, anchor="w")
        tk.Checkbutton(self.tab_cust, text="Greet me when the assistant opens", variable=self.greet_var,
                       command=self.toggle_greet, **ck).grid(row=5, column=0, columnspan=2, sticky="w")
        tk.Checkbutton(self.tab_cust, text="Start the assistant when Windows starts", variable=self.auto_var,
                       command=self.toggle_autostart, **ck).grid(row=6, column=0, columnspan=2, sticky="w")
        self.key_btn = tk.Button(self.tab_cust, text="CHANGE API KEY", relief="flat", bd=0, padx=10, pady=4,
                                 font=(FONT, 9, "bold"), cursor="hand2",
                                 command=lambda: self.open_wizard(start=3))
        self.key_btn.grid(row=7, column=0, sticky="w", pady=(4, 0))
        self.save_btn.grid(row=7, column=1, sticky="e", pady=(4, 0))

        # ---------- drag & drop files anywhere on the window ----------
        if DND_FILES:
            try:
                root.drop_target_register(DND_FILES)
                root.dnd_bind("<<Drop>>", self.on_drop)
            except Exception as e:
                log("System", f"drag & drop unavailable: {e}")
        else:
            log("System", "drag & drop needs:  pip install tkinterdnd2")

        self.show_tab("code")
        self.update_location()
        self.refresh_looks()
        self.tick()

    # ---- building blocks ----
    def panel(self, parent, title):
        outer = tk.Frame(parent, bg=PANEL, highlightthickness=1)
        lbl = tk.Label(outer, text=title, bg=PANEL, font=(FONT, 10, "bold"), anchor="w", padx=10, pady=6)
        lbl.pack(fill="x")
        body = tk.Frame(outer, bg=PANEL)
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.panels.append((outer, lbl))
        return outer, body

    def show_tab(self, key):
        self.tab = key
        self.tab_code.pack_forget()
        self.tab_cust.pack_forget()
        (self.tab_code if key == "code" else self.tab_cust).pack(fill="both", expand=True)
        self.last_look = None            # force colour refresh

    # ---- actions ----
    def pick_pack(self, name):
        if name == SET["pack"]:
            return
        SET["pack"], SET["theme"] = name, PACKS[name]["theme"]
        save_settings()
        STATE["ui_restart"] = True          # reconnect with the new voice
        log("System", f"switching voice to {PACKS[name]['label']} ...")

    def pick_theme(self, name):
        SET["theme"] = name
        save_settings()

    def run_quick(self, fn):
        try:
            fn()
        except Exception as e:
            log("System", f"quick tool error: {e}")

    def send_code(self):
        code = self.code_box.get("1.0", "end").strip()
        if not code:
            log("System", "code box is empty")
            return
        if len(code) > MAX_CODE_CHARS:
            code = code[:MAX_CODE_CHARS]
            log("System", f"code was longer than {MAX_CODE_CHARS} characters, sent the first part only")
        title = f" - {self.code_name}" if self.code_name else ""
        msg = (f"[CODE FROM USER{title}]\n{code}\n[END CODE]\n"
               "I shared this code with you. Remember it. Confirm in one short spoken sentence, "
               "then wait for my question.")
        INJECT_Q.put(msg)
        log("System", f"sending {code.count(chr(10)) + 1} lines of code to Jarvis"
                      + ("" if UI["connected"] else " (will go out when connected)"))
        self.code_name = ""

    def toggle_greet(self):
        SET["greet"] = bool(self.greet_var.get())
        save_settings()

    def toggle_autostart(self):
        on = bool(self.auto_var.get())
        try:
            set_autostart(on)
            log("System", "Jarvis will now start with Windows" if on else "Jarvis will no longer start with Windows")
        except Exception as e:
            self.auto_var.set(0 if on else 1)
            log("System", f"could not change auto-start: {e}")

    def on_drop(self, event):
        for f in self.root.tk.splitlist(event.data):
            self.send_file(f)

    def send_file(self, path):
        try:
            items, err = prepare_file(path)
        except Exception as e:
            items, err = None, f"{Path(path).name}: {e}"
        if err:
            log("System", err)
            return
        for it in items:
            INJECT_Q.put(it)
        log("System", f"analysing {Path(path).name} ..." + ("" if UI["connected"] else " (waiting for connection)"))

    def load_code_file(self):
        path = filedialog.askopenfilename(title="Choose a code or text file")
        if not path:
            return
        try:
            data = Path(path).read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            log("System", f"could not read file: {e}")
            return
        self.code_box.delete("1.0", "end")
        self.code_box.insert("1.0", data)
        self.code_name = Path(path).name
        log("System", f"loaded {self.code_name} - press SEND TO JARVIS")

    def save_custom(self):
        try:
            lat = max(-90.0, min(90.0, float(self.entries["lat"].get().strip())))
            lon = max(-180.0, min(180.0, float(self.entries["lon"].get().strip())))
        except ValueError:
            log("System", "latitude and longitude must be numbers, e.g. 28.6139 and 77.2090")
            return
        name = self.entries["name"].get().strip() or SET["name"]
        assistant = self.entries["assistant"].get().strip() or SET["assistant"]
        place = self.entries["place"].get().strip() or SET["place"]
        changed = (name != SET["name"]) or (place != SET["place"]) or (assistant != SET["assistant"])
        SET.update(name=name, assistant=assistant, place=place, lat=lat, lon=lon)
        self.root.title(assistant.upper())
        save_settings()
        self.update_location()
        log("System", "customisation saved")
        if changed:
            STATE["ui_restart"] = True       # reconnect so Jarvis learns the new name / place

    # ---- setup screen: your name -> assistant name -> API key (verified) ----
    WZ_STEPS = {
        1: ("name", "What is your name?", "Your assistant will call you by this name."),
        2: ("assistant", "What name will you give your assistant?", "For example: Jarvis, Friday, Nova ..."),
        3: ("key", "Paste your Gemini API key",
            "Free key: aistudio.google.com/apikey   -   it is saved only on this PC."),
    }

    def open_wizard(self, start=1, on_done=None):
        accent = THEMES[SET["theme"]]
        self.wz_done_cb = on_done
        self.wz_start = start
        self.wz_data = {"name": SET["name"] if SET.get("setup_done") else "",
                        "assistant": SET["assistant"], "key": ""}
        self.wz_q = queue.Queue()
        wz = tk.Frame(self.root, bg=BG)
        wz.place(relx=0, rely=0, relwidth=1, relheight=1)
        wz.lift()
        card = tk.Frame(wz, bg=PANEL, highlightthickness=1, highlightbackground=accent, padx=44, pady=32)
        card.place(relx=0.5, rely=0.5, anchor="center")
        self.wz, self.wz_card = wz, card
        self.wz_show(start)

    def wz_show(self, n):
        accent = THEMES[SET["theme"]]
        self.wz_n = n
        for w in self.wz_card.winfo_children():
            w.destroy()
        key, prompt, hint = self.WZ_STEPS[n]
        c = self.wz_card
        tk.Label(c, text=f"SETUP  -  STEP {n} OF 3", bg=PANEL, fg=accent, font=(FONT, 10, "bold")).pack()
        tk.Label(c, text=prompt, bg=PANEL, fg="#ffffff", font=(FONT, 18, "bold")).pack(pady=(14, 4))
        tk.Label(c, text=hint, bg=PANEL, fg=TEXT_DIM, font=(FONT, 10)).pack(pady=(0, 14))
        self.wz_entry = tk.Entry(c, width=42, bg=INPUT_BG, fg="#ffffff", insertbackground=accent, bd=0,
                                 highlightthickness=1, highlightbackground=blend(PANEL, accent, 0.5),
                                 highlightcolor=accent, font=(FONT, 14), justify="center",
                                 show="\u2022" if key == "key" else "")
        self.wz_entry.pack(ipady=8)
        if key != "key":
            self.wz_entry.insert(0, self.wz_data[key])
        self.wz_entry.focus_set()
        self.wz_entry.bind("<Return>", lambda e: self.wz_next())
        if key == "key":
            show_var = tk.IntVar(value=0)
            tk.Checkbutton(c, text="show key", variable=show_var, bg=PANEL, fg=TEXT_DIM, selectcolor=INPUT_BG,
                           activebackground=PANEL, activeforeground="#ffffff", bd=0, highlightthickness=0,
                           command=lambda: self.wz_entry.configure(show="" if show_var.get() else "\u2022")
                           ).pack(anchor="e", pady=(4, 0))
        self.wz_msg = tk.Label(c, text="", bg=PANEL, fg="#ff6b81", font=(FONT, 10), wraplength=480)
        self.wz_msg.pack(pady=(10, 4))
        row = tk.Frame(c, bg=PANEL)
        row.pack(pady=(6, 0))
        if n > self.wz_start:
            tk.Button(row, text="BACK", relief="flat", bd=0, padx=16, pady=6, cursor="hand2",
                      bg=blend(PANEL, accent, 0.18), fg="#e6ecff", font=(FONT, 10, "bold"),
                      command=lambda: self.wz_show(n - 1)).pack(side="left", padx=4)
        if SET.get("setup_done"):
            tk.Button(row, text="CANCEL", relief="flat", bd=0, padx=16, pady=6, cursor="hand2",
                      bg=blend(PANEL, accent, 0.18), fg="#e6ecff", font=(FONT, 10, "bold"),
                      command=lambda: self.wz.destroy()).pack(side="left", padx=4)
        self.wz_btn = tk.Button(row, text="VERIFY & START" if n == 3 else "NEXT", relief="flat", bd=0,
                                padx=20, pady=6, cursor="hand2", bg=accent, fg="#02060c",
                                activebackground=accent, font=(FONT, 10, "bold"), command=self.wz_next)
        self.wz_btn.pack(side="left", padx=4)

    def wz_next(self):
        key = self.WZ_STEPS[self.wz_n][0]
        val = self.wz_entry.get().strip()
        if not val:
            self.wz_msg.configure(text="Please type something first.", fg="#ff6b81")
            return
        self.wz_data[key] = val
        if self.wz_n < 3:
            self.wz_show(self.wz_n + 1)
            return
        self.wz_btn.configure(state="disabled", text="CHECKING...")
        self.wz_msg.configure(text="Checking your key with Google ...", fg=TEXT_DIM)
        threading.Thread(target=self.wz_verify, args=(val,), daemon=True).start()
        self.root.after(150, self.wz_poll)

    def wz_verify(self, key):
        try:
            c = genai.Client(api_key=key)
            for _ in c.models.list():          # a key that is wrong or blocked fails right here
                break
            self.wz_q.put((True, ""))
        except Exception as e:
            self.wz_q.put((False, str(e)[:200]))

    def wz_poll(self):
        try:
            ok, err = self.wz_q.get_nowait()
        except queue.Empty:
            self.root.after(150, self.wz_poll)
            return
        if not ok:
            self.wz_btn.configure(state="normal", text="VERIFY & START")
            self.wz_msg.configure(text="That key did not work: " + err, fg="#ff6b81")
            return
        self.wz_finish()

    def wz_finish(self):
        d = self.wz_data
        was_done = SET.get("setup_done")
        SET.update(name=d["name"], assistant=d["assistant"], api_key=d["key"], setup_done=True)
        save_settings()
        self.wz.destroy()
        self.root.title(SET["assistant"].upper())
        for k in ("name", "assistant"):
            self.entries[k].delete(0, "end")
            self.entries[k].insert(0, SET[k])
        log("System", "setup complete - API key verified")
        if self.wz_done_cb:
            self.wz_done_cb()
        elif was_done:
            STATE["ui_restart"] = True          # reconnect using the new key

    def update_location(self):
        self.loc_place.configure(text=SET["place"])
        self.loc_lat.configure(text=fmt_lat(SET["lat"]))
        self.loc_lon.configure(text=fmt_lon(SET["lon"]))

    # ---- colours of everything follow the theme ----
    def refresh_looks(self):
        accent = THEMES[SET["theme"]]
        dim = blend(PANEL, accent, 0.5)
        for outer, lbl in self.panels:
            outer.configure(highlightbackground=dim, highlightcolor=dim)
            lbl.configure(fg=accent)
        self.tab_outer.configure(highlightbackground=dim, highlightcolor=dim)
        for name, b in self.pack_btns.items():
            if name == SET["pack"]:
                b.configure(bg=accent, fg="#02060c", activebackground=accent, activeforeground="#02060c")
            else:
                b.configure(bg=PANEL, fg="#c4cde4", activebackground=blend(PANEL, accent, 0.3),
                            activeforeground="#ffffff")
        for name, b in self.theme_btns.items():
            sel = name == SET["theme"]
            b.configure(bg=blend(PANEL, THEMES[name], 0.25) if sel else PANEL,
                        activebackground=blend(PANEL, THEMES[name], 0.35))
        for b in self.quick_btns:
            b.configure(bg=blend(PANEL, accent, 0.10), fg="#e6ecff",
                        activebackground=blend(PANEL, accent, 0.35), activeforeground="#ffffff")
        for key, b in self.tab_btns.items():
            if key == self.tab:
                b.configure(bg=accent, fg="#02060c", activebackground=accent)
            else:
                b.configure(bg=PANEL, fg="#c4cde4", activebackground=blend(PANEL, accent, 0.3))
        for b in (self.send_btn, self.save_btn):
            b.configure(bg=accent, fg="#02060c", activebackground=accent, activeforeground="#02060c")
        for b in (self.load_btn, self.clear_btn, self.key_btn):
            b.configure(bg=blend(PANEL, accent, 0.18), fg="#e6ecff",
                        activebackground=blend(PANEL, accent, 0.4), activeforeground="#ffffff")
        for v in self.vals.values():
            v.configure(fg=accent)
        self.loc_planet.configure(fg=accent)
        self.code_box.configure(insertbackground=accent)
        self.logbox.tag_configure("You", foreground="#ffffff")
        self.logbox.tag_configure("Jarvis", foreground=accent)
        self.logbox.tag_configure("Tool", foreground="#8a94b0")
        self.logbox.tag_configure("System", foreground="#6c778f")
        self.last_look = (SET["pack"], SET["theme"], self.tab)

    def drain_log(self):
        added = False
        while True:
            try:
                tag, txt = LOG.get_nowait()
            except queue.Empty:
                break
            if not added:
                self.logbox.configure(state="normal")
                added = True
            who = SET["assistant"] if tag == "Jarvis" else tag
            self.logbox.insert("end", time.strftime("[%H:%M:%S] "), "System")
            self.logbox.insert("end", f"{who}: ", tag)
            self.logbox.insert("end", txt + "\n")
        if added:
            self.logbox.see("end")
            self.logbox.configure(state="disabled")

    # ---- system status ----
    def set_bar(self, key, pct, accent):
        cv = self.bars[key]
        cv.delete("all")
        cv.create_rectangle(0, 0, 120, 8, fill=blend(PANEL, accent, 0.15), outline="")
        cv.create_rectangle(0, 0, 120 * max(0, min(100, pct)) / 100, 8, fill=accent, outline="")
        self.vals[key].configure(text=f"{pct:.0f}%")

    def update_status(self, accent):
        if psutil:
            try:
                self.set_bar("CPU", psutil.cpu_percent(), accent)
                self.set_bar("RAM", psutil.virtual_memory().percent, accent)
                self.set_bar("DISK", psutil.disk_usage(os.path.abspath(os.sep)).percent, accent)
            except Exception:
                pass
        else:
            for k in ("CPU", "RAM", "DISK"):
                self.vals[k].configure(text="pip install psutil")
        speaking = jarvis_is_speaking()
        self.vals["NETWORK"].configure(text="Online" if UI["connected"] else "Connecting")
        self.vals["MICROPHONE"].configure(text=("Paused" if speaking else "Active") if UI["connected"] else "Off")
        self.vals["SPEAKER"].configure(text="Speaking" if speaking else "Ready")

    # ---- animation ----
    def status(self):
        if not UI["connected"]:
            return "CONNECTING"
        if UI["working"] > 0:
            return "WORKING"
        if jarvis_is_speaking():
            return "SPEAKING"
        return "LISTENING"

    def draw_header(self, accent):
        c = self.head
        W = c.winfo_width() or 1200
        c.delete("all")
        c.create_text(W / 2, 28, text=spaced(SET["assistant"]), fill=blend(accent, "#ffffff", 0.3),
                      font=(FONT, 24, "bold"))
        c.create_text(W / 2, 56, text="Y O U R   P E R S O N A L   A I   A S S I S T A N T",
                      fill=TEXT_DIM, font=(FONT, 9))
        c.create_text(14, 26, text=f"WELCOME, {SET['name'].upper()}", fill=accent, font=(FONT, 11, "bold"), anchor="w")
        c.create_text(14, 48, text=f"voice pack: {PACKS[SET['pack']]['label']}", fill=TEXT_DIM,
                      font=(FONT, 9), anchor="w")
        c.create_text(W - 14, 26, text=time.strftime("%H:%M:%S"), fill="#ffffff", font=("Consolas", 24, "bold"), anchor="e")
        c.create_text(W - 14, 54, text=time.strftime("%a, %d %b %Y"), fill=accent, font=(FONT, 11), anchor="e")
        c.create_line(0, 70, W, 70, fill=blend(accent, BG, 0.6))

    def draw_globe(self, accent, t):
        g = self.globe
        g.delete("all")
        cx = cy = 38
        R = 30
        col = blend(PANEL, accent, 0.55)
        g.create_oval(cx - R, cy - R, cx + R, cy + R, outline=accent, width=2, fill=blend(PANEL, accent, 0.10))
        for k in (0.35, 0.7):
            g.create_oval(cx - R * k, cy - R, cx + R * k, cy + R, outline=col)
        for dy in (-15, 0, 15):
            half = math.sqrt(max(0, R * R - dy * dy))
            g.create_line(cx - half, cy + dy, cx + half, cy + dy, fill=col)
        g.create_line(cx, cy - R, cx, cy + R, fill=col)
        pulse = 3 + 2 * math.sin(t * 3)
        g.create_oval(cx - pulse - 2, cy - pulse - 2, cx + pulse + 2, cy + pulse + 2, outline=accent)
        g.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill="#ffffff", outline="")

    def draw(self, c, t, state, accent, label):
        W = c.winfo_width() or 640
        H = c.winfo_height() or 400
        cx, cy = W / 2, H / 2 - 14
        c.delete("all")
        target = UI["out"] if state == "SPEAKING" else UI["mic"] * 0.8
        self.lv = max(target, self.lv * 0.86)
        UI["out"] *= 0.9
        lv = min(1.0, self.lv)
        speed = {"LISTENING": 1.0, "SPEAKING": 2.2, "WORKING": 4.0, "CONNECTING": 0.5}[state]

        # faint background rings
        for r, k in ((142, 0.90), (108, 0.86)):
            c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=blend(accent, BG, k), width=1)
        # rotating HUD arcs
        for r, ext, sp, w, k in ((132, 70, 40, 3, 0.15), (132, 40, -65, 2, 0.35), (100, 110, -28, 2, 0.30)):
            ang = (t * sp * speed) % 360
            for i in range(3):
                c.create_arc(cx - r, cy - r, cx + r, cy + r, start=ang + i * 120, extent=ext,
                             style="arc", outline=blend(accent, BG, k), width=w)
        # glow
        base = 58 + lv * 30 + math.sin(t * 2 * speed) * 2.5
        for i in range(7, 0, -1):
            r = base + i * 7
            c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=blend(BG, accent, 0.035 * (8 - i) * (0.6 + lv)), outline="")
        # voice ring
        pts = []
        for k in range(72):
            th = k / 72 * 2 * math.pi
            rr = base + 16 + (3 + lv * 20) * math.sin(5 * th + t * 5 * speed) * math.sin(2 * th - t * 2)
            pts += [cx + rr * math.cos(th), cy + rr * math.sin(th)]
        c.create_polygon(*pts, smooth=True, fill="", outline=accent, width=2)
        # core
        c.create_oval(cx - base, cy - base, cx + base, cy + base, fill=blend(BG, accent, 0.22), outline=accent, width=2)
        r2 = base * 0.55
        c.create_oval(cx - r2, cy - r2, cx + r2, cy + r2, fill=blend(accent, "#ffffff", 0.35 + 0.3 * lv), outline="")
        nm = SET["assistant"].upper()
        c.create_text(cx, cy, text=nm[:12], fill="#02060c",
                      font=(FONT, 11 if len(nm) <= 6 else 8 if len(nm) <= 9 else 6, "bold"))
        # side sound bars
        for side in (-1, 1):
            for i in range(11):
                x = cx + side * (168 + i * 9)
                if x < 8 or x > W - 8:
                    continue
                h = 3 + lv * 38 * abs(math.sin(t * 4 * speed + i * 0.6))
                c.create_line(x, cy - h, x, cy + h, fill=blend(accent, BG, 0.15 + i * 0.05), width=2)
        # state text
        hint = {"LISTENING": "How can I help you?", "SPEAKING": "...", "WORKING": "On it...",
                "CONNECTING": "Connecting to Gemini Live..."}[state]
        c.create_text(cx, H - 50, text=state, fill=accent, font=(FONT, 14, "bold"))
        c.create_text(cx, H - 28, text=hint, fill=TEXT_DIM, font=(FONT, 10))

    def tick(self):
        try:
            accent = THEMES[SET["theme"]]
            if self.last_look != (SET["pack"], SET["theme"], self.tab):
                self.refresh_looks()
            t = time.time()
            self.draw_header(accent)
            self.draw(self.canvas, t, self.status(), accent, PACKS[SET["pack"]]["label"])
            self.draw_globe(accent, t)
            if self.tick_n % 30 == 0:
                self.update_status(accent)
            self.tick_n += 1
            self.drain_log()
        except Exception as e:           # never let a drawing bug kill the window
            print("[UI] ", e)
        self.root.after(33, self.tick)

def run():
    startup = "--startup" in sys.argv          # set by the "start with Windows" entry
    root = TkinterDnD.Tk() if TkinterDnD else tk.Tk()
    hud = HUD(root)
    root.protocol("WM_DELETE_WINDOW", lambda: os._exit(0))

    def start_backend(first_run=False):
        if first_run:                          # right after the setup screen
            INJECT_Q.put(
                f"[SYSTEM EVENT] Setup just finished. Introduce yourself as {SET['assistant']} to "
                f"{SET['name']} in one short sentence and say you are ready.")
        elif SET.get("greet", True):           # queued now, spoken as soon as the assistant connects
            h = time.localtime().tm_hour
            part = "morning" if h < 12 else "afternoon" if h < 17 else "evening"
            INJECT_Q.put(
                f"[SYSTEM EVENT] The PC just started and you opened. Greet {SET['name']} warmly in one short "
                f"sentence for the {part}, mention the time ({time.strftime('%H:%M')}), "
                "and ask what they would like to do.")

        def go():
            if startup:
                time.sleep(8)                  # give Windows time to bring up network and audio
            asyncio.run(main())

        threading.Thread(target=go, daemon=True).start()

    if SET.get("setup_done") and get_key():
        start_backend()
    else:                                      # first run: name -> assistant name -> API key
        hud.open_wizard(start=1, on_done=lambda: start_backend(first_run=True))
    root.mainloop()

if __name__ == "__main__":
    run()
