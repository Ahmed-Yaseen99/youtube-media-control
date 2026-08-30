"""Control YouTube (or any media) in a running browser window.

Deterministic single command - zero model decisions:
- Finds the real browser window by process (EnumWindows +
  QueryFullProcessImageName against a known list: Firefox, Chrome, Edge,
  Brave, Opera, Vivaldi), NOT by title guess
- play/pause/next/prev/stop/volume use the GLOBAL WINDOWS MEDIA KEYS -> they
  work WITHOUT bringing the browser to the foreground, and regardless of which
  app is playing (any tab, Spotify, etc). The keystroke is routed through the
  system media session (same path as the hardware media buttons on a
  keyboard), so Windows focus rules can never block them.
- mute/fullscreen/seek use YouTube's own shortcuts ('m'/'f'/'j'/'l') and DO
  need focus; focus is forced with the full Win32 recipe (AttachThreadInput +
  topmost SetWindowPos + simulated ALT input + SetForegroundWindow x2) and
  VERIFIED afterwards - the script never claims success it cannot prove.
- loop toggles the video's loop property via Firefox BiDi JS injection
  (Firefox only - it's Firefox's remote protocol; no focus needed)
- NEVER writes code, never browses, never asks the user anything

Usage:
  python youtube_media_control.py [action] [--browser firefox|chrome|...]

Actions (default: playpause):
  playpause  -> media play/pause  (GLOBAL - no focus needed, any browser/app)
  pause      -> alias of playpause
  next       -> media next track  (GLOBAL)
  prev       -> media prev track  (GLOBAL)
  stop       -> media stop        (GLOBAL)
  volumeup   -> media volume up   (GLOBAL)
  volumedown -> media volume down (GLOBAL)
  mute       -> 'm'               (needs focus; mutes the YouTube tab)
  fullscreen -> 'f'               (needs focus)
  rewind10   -> 'j'               (needs focus)
  forward10  -> 'l'               (needs focus)
  loop       -> JS loop=true      (BiDi, Firefox only, no focus needed)

Exit codes: 0 = OK, 2 = browser not running, 3 = focus failed (focused actions only),
            4 = unknown action.
"""
import argparse
import ctypes
import sys
import time
from ctypes import wintypes

import pyautogui

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

SW_RESTORE = 9
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_SHOWWINDOW = 0x0040
KEYEVENTF_KEYUP = 0x0002

VK_MENU = 0x12
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF

# Browser executables we recognize. --browser overrides; default = all found.
BROWSER_EXES = (
    "firefox.exe", "chrome.exe", "msedge.exe", "brave.exe",
    "opera.exe", "vivaldi.exe", "chromium.exe",
)

BROWSER_ALIASES = {
    "firefox": "firefox.exe", "ff": "firefox.exe",
    "chrome": "chrome.exe", "google-chrome": "chrome.exe",
    "edge": "msedge.exe", "msedge": "msedge.exe",
    "brave": "brave.exe",
    "opera": "opera.exe",
    "vivaldi": "vivaldi.exe",
    "chromium": "chromium.exe",
}

# Global media keys - routed by Windows to the active media session, no focus needed.
GLOBAL_KEYS = {
    "playpause": VK_MEDIA_PLAY_PAUSE,
    "pause": VK_MEDIA_PLAY_PAUSE,
    "next": VK_MEDIA_NEXT_TRACK,
    "prev": VK_MEDIA_PREV_TRACK,
    "stop": VK_MEDIA_STOP,
    "volumeup": VK_VOLUME_UP,
    "volumedown": VK_VOLUME_DOWN,
}

# YouTube page shortcuts - only work while the browser window has focus.
FOCUSED_KEYS = {
    "mute": "m",
    "fullscreen": "f",
    "rewind10": "j",
    "forward10": "l",
}


def process_name(pid):
    """Return the executable name of a PID via ctypes, or None if gone."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value.lower()
        return None
    finally:
        kernel32.CloseHandle(handle)


def find_browser_windows(browser_exes):
    """Return visible browser windows (top-level, non-empty) matching browser_exes."""
    found = []

    def enum_cb(hwnd, lparam):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name = process_name(pid.value)
        if name and name in browser_exes:
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    title = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, title, length + 1)
                    found.append({"hwnd": hwnd, "title": title.value})
        return True

    cb = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(enum_cb)
    user32.EnumWindows(cb, 0)
    return found


def pick_window(windows):
    """Prefer the most recently used (Z-order first) Firefox window."""
    if not windows:
        return None
    order = []
    user32.EnumWindows(
        ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(
            lambda h, l: (order.append(h), True)[1]
        ),
        0,
    )
    for hwnd in order:
        for w in windows:
            if w["hwnd"] == hwnd:
                return w
    return windows[0]


def force_foreground(hwnd):
    """Full Win32 focus-forcing recipe. Returns True only if focus was VERIFIED.

    Background processes normally cannot call SetForegroundWindow (Windows denies
    it and flashes the taskbar icon instead - the "orange glow"). This combines
    the known workarounds: attach input queues, flash topmost, simulate ALT input
    (fakes 'recent user input'), then retry SetForegroundWindow, then verify.
    """
    fg = user32.GetForegroundWindow()
    if fg == hwnd:
        return True
    cur_tid = kernel32.GetCurrentThreadId()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = False
    if fg and fg_tid:
        attached = bool(user32.AttachThreadInput(cur_tid, fg_tid, True))
    try:
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE)
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        for _ in range(3):
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.15)
            if user32.GetForegroundWindow() == hwnd:
                break
        user32.BringWindowToTop(hwnd)
        time.sleep(0.3)
        return user32.GetForegroundWindow() == hwnd
    finally:
        if attached:
            user32.AttachThreadInput(cur_tid, fg_tid, False)


def send_vk(vk):
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.02)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def loop_via_bidi():
    """Enable loop on the YouTube video via Firefox BiDi JS injection."""
    import json
    try:
        import websocket
    except ImportError:
        print("ERROR: websocket-client not installed", file=sys.stderr)
        sys.exit(2)
    try:
        ws = websocket.create_connection(
            "ws://127.0.0.1:9222/session", suppress_origin=True, timeout=5
        )
    except Exception as e:
        print(f"ERROR: BiDi not available ({e})", file=sys.stderr)
        sys.exit(2)
    try:
        ws.send(json.dumps({"id": 1, "method": "session.new",
                             "params": {"capabilities": {}}}))
        sid = json.loads(ws.recv()).get("result", {}).get("sessionId", "")
        if not sid:
            print("ERROR: BiDi session failed", file=sys.stderr)
            sys.exit(2)
        ws.send(json.dumps({"id": 2, "method": "browsingContext.getTree",
                             "params": {"maxDepth": 1}}))
        tabs = json.loads(ws.recv()).get("result", {}).get("contexts", [])
        yt = [t for t in tabs if "youtube.com/watch" in t.get("url", "")]
        if not yt:
            print("ERROR: no YouTube tab found", file=sys.stderr)
            sys.exit(2)
        ctx = yt[0]["context"]
        js = 'var v=document.querySelector("video");v?(v.loop=true,"loop="+v.loop):"no video"'
        ws.send(json.dumps({"id": 3, "method": "script.evaluate",
                             "params": {"expression": js, "awaitPromise": False,
                                        "target": {"context": ctx}}}))
        result = json.loads(ws.recv())
        val = result.get("result", {}).get("result", {}).get("value", "")
        print("ACTION: loop")
        print(f"RESULT: {val}")
        print("OK")
    finally:
        try:
            ws.send(json.dumps({"id": 99, "method": "session.end",
                                 "params": {}}))
            ws.recv()
        except Exception:
            pass
        ws.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", nargs="?", default="playpause",
                    choices=sorted(set(GLOBAL_KEYS) | set(FOCUSED_KEYS) | {"loop"}))
    ap.add_argument("--browser", choices=sorted(BROWSER_ALIASES),
                    help="only control this browser (default: first found of any supported)")
    args = ap.parse_args()
    action = args.action

    exes = {BROWSER_ALIASES[args.browser]} if args.browser else set(BROWSER_EXES)
    windows = find_browser_windows(exes)
    if not windows:
        print("ERROR: no supported browser is running - nothing to control", file=sys.stderr)
        sys.exit(2)

    win = pick_window(windows)
    print(f"WINDOW: {win['title']}", file=sys.stderr)

    if action == "loop":
        return loop_via_bidi()

    if action in GLOBAL_KEYS:
        send_vk(GLOBAL_KEYS[action])
        print(f"ACTION: {action}")
        print("KEY: global media key (no focus needed - routes to the system media session)")
        print("OK")
        sys.exit(0)

    if not force_foreground(win["hwnd"]):
        print("ERROR: could not bring Firefox to the foreground (Windows focus rule "
              "or Firefox runs elevated) - key NOT sent", file=sys.stderr)
        print("FOCUS: FAIL")
        sys.exit(3)

    pyautogui.press(FOCUSED_KEYS[action])
    print(f"ACTION: {action}")
    print(f"KEY: {FOCUSED_KEYS[action]}")
    print("FOCUS: OK")
    print("OK")


if __name__ == "__main__":
    main()
