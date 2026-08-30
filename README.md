# youtube-media-control

Deterministic keyboard control of a running Firefox + YouTube playback on Windows — zero ML decisions, made for AI agents calling it as a single command.

## What it does

One command, one action. Never writes code, never browses, never asks anything.

- **Global actions** (play/pause, next, prev, stop, volume) use the **Windows global media keys** via `keybd_event` — routed by Windows to the active media session. No foreground needed, immune to focus stealing.
- **Focused actions** (mute `m`, fullscreen `f`, rewind `j`, forward `l`) send YouTube's page shortcuts and force foreground with the full Win32 recipe (AttachThreadInput + topmost SetWindowPos + simulated ALT input + SetForegroundWindow ×3) and **verify** focus succeeded — never claims success it can't prove.
- **`loop`** toggles the video's `loop` property via Firefox BiDi JS injection (no focus needed).

Finds the Firefox window by process name (`EnumWindows` + `QueryFullProcessImageName`), not by title guessing — picks the most recently used window.

## Usage

```bash
python youtube_media_control.py [action]
```

| Action | Key | Focus needed? |
|---|---|---|
| `playpause` / `pause` | Media play/pause | No |
| `next` / `prev` / `stop` | Media next/prev/stop | No |
| `volumeup` / `volumedown` | Media volume | No |
| `mute` | `m` | Yes |
| `fullscreen` | `f` | Yes |
| `rewind10` | `j` | Yes |
| `forward10` | `l` | Yes |
| `loop` | BiDi JS | No |

Exit codes: `0` OK, `2` Firefox not running / BiDi unavailable, `3` focus failed (focused actions only — key NOT sent), `4` unknown action.

## Requirements

- Windows
- Firefox running (with the remote agent port `9222` enabled for `loop`)
- `pyautogui` for focused actions; `websocket-client` for `loop`

## Design notes

Every path prints an `ACTION:` / `RESULT:` / `OK` or error to stdout, making it deterministic for agents to parse. Global-key actions deliberately avoid focus games — Windows media keys are the same path as hardware media buttons.