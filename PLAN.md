# Plan: Global Input Forwarder for Bit Buddy (Proton/XWayland)

## Problem statement

Bit Buddy is a Windows desktop-pet game run through Steam Proton on Fedora KDE
(Wayland session). It ships its own "RawInput Helper.exe" process meant to
read keyboard/mouse input globally (even when the game isn't focused) and
relay it to the main game process over Windows RPC. That RPC hand-off fails
constantly (`RPC_S_SERVER_UNAVAILABLE`, status `0x3e6`, seen repeatedly in
`~/steam-3874950.log`), confirmed independent of Wayland/X11 session type,
Proton version (stable, Experimental, GE), and simulated Windows version
(7 vs 10). This is a genuine bug inside Wine/Proton's RPC implementation for
this game's specific pattern, not a Linux permissions issue.

**Workaround approach:** instead of fixing the broken internal RPC channel,
bypass it. Bit Buddy's window already accepts normal input correctly when it
*is* focused — that path is not broken. So: capture real keyboard/mouse input
system-wide (outside the app), and forward a synthetic copy of it directly to
Bit Buddy's X11 window (it runs under XWayland, so it has a real X11 window
even on a Wayland desktop) — without touching real focus or the real cursor.

## Goal

A small background daemon that:
1. Listens to real keyboard and mouse input system-wide, regardless of which
   window has focus.
2. Forwards a synthetic copy of that input directly to Bit Buddy's window,
   targeted by window ID, using X11 mechanisms.
3. Runs quietly in the background (ideally autostarted), with negligible
   resource use, and fails safely (never crashes the user's normal
   input — it must only ever *add* synthetic events, never intercept or
   consume real ones).

## Feasibility test (do this FIRST, before building anything else)

Before writing the full daemon, verify the core assumption: that Bit Buddy's
window actually reacts to *synthetic* X11 input events at all. Some
Windows apps (via Wine) ignore synthetic events as an anti-cheat/anti-bot
measure, which would make this whole approach a dead end.

1. Launch Bit Buddy.
2. Find its window ID:
   ```bash
   xdotool search --name "Bit Buddy"
   ```
   (Adjust the search string if the window title differs — try
   `xdotool search --class "bit"` or `wmctrl -l` to list all windows and
   find it by eye if the name search fails.)
3. With the game window NOT focused (click into a different window first),
   send it a synthetic keystroke directly by window ID:
   ```bash
   xdotool key --window <WINDOW_ID> a
   ```
4. Check whether the game visibly reacted (animation, sound, dialogue
   change — whatever Bit Buddy does in response to input).

**If it does not react at all** — stop here. Report back "feasibility test
failed" and do not proceed to building the daemon; the synthetic-input
approach won't work for this app and effort should go elsewhere.

**If it does react** — proceed to build the full tool below.

## Architecture

```
[Kernel /dev/input/eventX devices]
        |
   evdev listener (Python, python-evdev)
        |  (reads raw hardware key/mouse events, all keyboards/mice)
        v
   Event translator (map evdev keycodes -> xdotool/X11 keysyms)
        |
        v
   xdotool forwarder (sends synthetic key/click events to target window ID
                       only, via --window flag; never moves real cursor
                       or changes real focus)
        |
        v
   [Bit Buddy's XWayland window]
```

### Why read from `/dev/input/eventX` instead of an X11/Wayland-level hook

- Works identically regardless of X11 vs Wayland session — reads hardware
  events directly from the kernel input subsystem, bypassing the display
  server's input-isolation rules entirely (this is legitimate, standard
  Linux practice for tools like `xdotool`'s own kin, `ydotool`, and
  various remapping tools — not a security bypass, just a different valid
  API for input, gated by normal Linux permissions).
- Avoids needing to run under X11 specifically — user can stay on Wayland
  for the rest of their desktop.

### Why forward via `xdotool --window` instead of `xdotool key` (unscoped)

- `xdotool key` unscoped sends to the currently focused window — would
  steal the user's real focus/input the whole time the daemon runs, ruining
  normal desktop use.
- `--window <id>` targets a specific window by X11 window ID, delivering
  synthetic events to it without touching real focus, as long as Bit
  Buddy's window still exists and X11/XWayland allows send-to-unfocused
  (most apps do; some ignore it — this is exactly what the feasibility
  test above checks).

## Implementation steps for Claude Code

### 1. Environment setup
- Check for and install dependencies:
  - `xdotool` (system package, likely already present or `dnf install xdotool`)
  - `python3-evdev` (`pip install evdev --break-system-packages` or distro
    package `python3-evdev`)
- Confirm user is in the `input` group (needed to read `/dev/input/eventX`
  without root):
  ```bash
  groups | grep input
  ```
  If not present:
  ```bash
  sudo usermod -aG input $USER
  ```
  (requires logout/login to take effect — flag this to the user, don't
  silently assume it's applied in the same session)

### 2. Window discovery module
- Function to locate Bit Buddy's window ID reliably, since it may not be
  running yet when the daemon starts, and its window ID can change between
  launches.
- Poll periodically (e.g. every 2 seconds) for a window matching the game's
  title/class if not currently found, so the daemon works whether started
  before or after the game.
- Handle the game closing gracefully — stop forwarding, keep polling for it
  to reappear, don't crash.

### 3. Input listener module
- Use `python-evdev`'s `InputDevice` and `list_devices()` to enumerate
  keyboard and mouse devices under `/dev/input/`.
- Use `evdev.InputDevice(path).grab()` **NOT** required and should be
  **avoided** — grabbing would consume the real event and stop it reaching
  the rest of the desktop, which the user explicitly does not want (they
  want input to work normally everywhere AND reach Bit Buddy). Read
  events without grabbing, so they remain available to the rest of the
  system as normal.
- Only listen to `EV_KEY` (keyboard/mouse buttons) and relevant `EV_REL`
  (mouse movement) event types.

### 4. Event translation module
- Map evdev keycodes (e.g. `KEY_A`) to X11 keysym names `xdotool` expects
  (e.g. `a`). A lookup table or existing library mapping should be used
  rather than hand-written per-key, since this is error-prone — investigate
  whether `python-evdev`'s `ecodes` plus a small keycode-to-keysym table
  covers the needed range (alphanumeric + common punctuation is likely
  sufficient for this use case; full international layout support is out
  of scope initially).

### 5. Forwarder module
- On each translated key event, shell out to (or better, use a Python X11
  binding like `python-xlib` directly instead of shelling out to `xdotool`
  per keystroke, which is more efficient and avoids process-spawn
  overhead for every keystroke):
  ```python
  # conceptually:
  xdotool_cmd = ["xdotool", "key", "--window", str(window_id), keysym]
  ```
- Rate-limit / debounce if needed to avoid flooding the target window if
  the user is typing quickly (test whether this is actually a problem
  before adding complexity here).
- Only forward while a valid window ID is known; no-op otherwise.

### 6. Daemon wrapper
- Combine listener + translator + forwarder into a single long-running
  script with clean start/stop (`systemd --user` service is a nicer fit
  than a raw `.desktop` autostart entry, since it gives proper
  start/stop/status/logs — consider offering both, defaulting to the
  simpler `.desktop` autostart approach used earlier in this
  conversation for consistency, unless the user wants a proper systemd
  unit).
- Log errors to a file (e.g. `~/.local/share/bitbuddy-forwarder.log`)
  rather than crashing silently, so problems are diagnosable.
- Graceful shutdown on SIGTERM/SIGINT.

### 7. Testing checklist (for Claude Code to verify before calling this done)
- [ ] Daemon starts without errors as the normal user (no root needed
      after group setup).
- [ ] Typing/clicking while a *different* window is focused visibly
      reaches Bit Buddy (confirmed via its in-game reaction).
- [ ] Normal system-wide typing/clicking is completely unaffected — no
      double-typing, no stolen focus, no lag introduced elsewhere.
- [ ] Daemon survives Bit Buddy being closed and relaunched without
      needing to be restarted itself.
- [ ] Daemon does not spike CPU usage at idle (event-driven, not polling
      in a tight loop for the input listening portion — polling is fine
      only for the low-frequency window-discovery check).

## Explicit non-goals / scope boundaries

- No attempt to fix the actual underlying Wine RPC bug — this is a
  workaround, not a repair.
- No global keylogging storage/logging of what keys were pressed — the
  daemon should only relay events live, never write captured keystrokes to
  disk or anywhere persistent (this matters both for the user's privacy and
  to avoid building something that looks like a keylogger, even though its
  purpose here is benign and user-initiated).
- No attempt to support arbitrary other apps out of the box initially —
  build for Bit Buddy specifically first; generalizing to "any app" can be
  a later enhancement once the core approach is proven to work.

## Handoff note for Claude Code

Start with the **Feasibility test** section above and report the result
before writing any of the daemon code — if the synthetic-input test fails,
stop and say so rather than building the full tool on a broken assumption.
