# Bit Buddy Input Forwarder

> [!WARNING]
> **Read this before installing.** To work at all, this daemon reads every
> keystroke you type, system-wide, on every keyboard connected to your
> machine — not just input meant for Bit Buddy. That's not a side effect,
> it's required by how the workaround has to function. As shipped, it never
> writes or sends that data anywhere except a live copy to Bit Buddy's
> window (see [Security implications](#security-implications) below for how
> to verify that yourself in the code) — but the *shape* of this tool is a
> keylogger with a self-imposed rule not to log. In the wrong hands, or if
> this project or your install of it were ever compromised (a hijacked
> maintainer account, a malicious PR, a tampered copy), that self-imposed
> rule is exactly the part that's trivial to remove, and it would run
> silently at every login with no visible sign anything changed.
>
> Only install this if you're willing to read `bitbuddy_forwarder.py` and
> `install.sh` yourself first (both are short and plain Python/bash, no
> obfuscation, no network calls), or you trust whoever's telling you to run
> it as much as you'd trust them with your keyboard. Don't `curl | bash`
> this from a link someone dropped in chat without checking it's actually
> pointing at this repo.

Bit Buddy is a Windows desktop-pet game that runs through Steam Proton. It
ships its own global-input helper ("RawInput Helper.exe") meant to read
keyboard input even when the game isn't focused, and relay it to the main
game process over Windows RPC. That RPC hand-off is broken under Proton
(fails with `RPC_S_SERVER_UNAVAILABLE`), so the game never reacts unless
its window is focused — not great for a desktop pet.

This is a workaround, not a fix for the underlying Proton bug: a small
background daemon that reads real keyboard input system-wide (from the
Linux kernel, via evdev) and forwards a synthetic copy directly to Bit
Buddy's X11 window by window ID, without touching real keyboard focus. Bit
Buddy's window already handles input fine when it *is* focused, so this
just gets input to it while it *isn't*.

## What it does and doesn't do

- **Forwards keyboard input.** Typing anywhere on the desktop reaches Bit
  Buddy's window, whether or not that window is focused.
- **Does not forward mouse input.** This was tried and dropped: sending
  synthetic clicks to Bit Buddy's window made it grab focus/window-stacking
  attention on every real click anywhere on the desktop, which broke normal
  window switching (needing a double-click to focus other windows). There's
  no clean way found so far to stop that side effect from the sending side.
- **Never grabs input.** Real keyboard events still reach the rest of the
  desktop completely normally — this only adds a synthetic copy, it never
  intercepts or consumes anything.
- **Never logs keystrokes.** The log file only records structural events
  (window found/lost, device errors), never what was typed.

## How it works

```
/dev/input/eventX (kernel)
        |  evdev, read-only, not grabbed
        v
Event filter (real keyboards only, by KEY_A capability)
        |
        v
X11 KeyPress/KeyRelease events, sent directly to Bit Buddy's window ID
via XSendEvent (python-xlib), independent of real focus
```

Bit Buddy's window is found by WM_CLASS (`steam_app_3874950`, Bit Buddy's
Steam app ID) and re-polled every 2 seconds, since its window ID changes
between launches and the game creates other windows under the same class
that must be filtered out (see "Known quirks" below).

Keycodes are forwarded as raw X11 keycodes using the standard convention
that X11 keycode = Linux evdev keycode + 8 (the "evdev" XKB ruleset used by
virtually all modern Linux X servers, including XWayland). This lets the
receiving app's own keyboard layout resolve the actual character, so no
manual keysym lookup table is needed, and it isn't limited to alnum keys.

## Known quirks (found the hard way)

- Bit Buddy's own broken `RawInput Helper.exe` leaves behind a large
  (near-fullscreen) **invisible** window under the same WM_CLASS as the
  real pet window. Picking the target window by size alone finds this
  helper window instead of the real one. The fix is to require
  `map_state == IsViewable` — the helper window is never actually mapped.
- A laptop touchpad's real position data comes through as absolute
  coordinates (`EV_ABS`), not relative deltas (`EV_REL`) — its legacy
  relative "Mouse" evdev node exists but the touchpad firmware never
  actually uses it. Moot here since mouse forwarding was dropped, but
  worth knowing if you ever revisit that.

## Security implications

This tool needs broad access to make the workaround possible, so it's worth
knowing exactly what it can see and do before installing it.

- **It reads every keystroke from every keyboard on the system, not just
  input meant for Bit Buddy.** To forward input while unfocused, it has to
  read raw input at the kernel level before any window gets it — there's no
  way to scope that to "only keys meant for this one game" at the source.
  That means the daemon process has live access to everything you type
  anywhere: passwords, 2FA codes, messages, all of it.
- **It never writes keystrokes anywhere.** The log file only records
  structural events (window found/lost, device errors) — see
  `setup_logging()` and every `log.*()` call in `bitbuddy_forwarder.py`,
  none of which touch key data. This is a property of the current code, not
  something enforced by the OS: anyone who can modify the installed script
  (`~/.local/share/bitbuddy-forwarder/bitbuddy_forwarder.py`, a normal
  user-writable path with no integrity check) could add real logging, and
  it would run silently at every login via the systemd user service.
- **`install.sh` adds your user to the `input` group.** This is required to
  read `/dev/input/event*` without root, and there's no narrower way to
  grant it on Linux. It's a standing, permanent grant, not scoped to this
  daemon — any other program you run afterwards (now or in the future) can
  also read raw keyboard/mouse events, for as long as you stay in that
  group. Remove yourself with `sudo gpasswd -d "$USER" input` (then log out
  and back in) if you ever uninstall this.
- **The target window is verified by owning process, not just by name.**
  Earlier versions of this matched Bit Buddy's window by WM_CLASS alone,
  which any other local X11 client could spoof to get itself sent a live
  copy of your real keystrokes. `find_target_window()` now also checks
  `_NET_WM_PID` against `SteamAppId`/`SteamGameId` in that process's
  `/proc/<pid>/environ`, so the window has to actually belong to a process
  Steam launched for this app ID, not just claim to. This assumes other
  processes running as your user aren't reading/spoofing that environ data
  themselves — like everything else here, it's a same-user trust model, not
  a sandbox.

In short: reasonable to run if you trust your own machine and the other
software running as your user, since that's the same trust boundary most
input-remapping tools (`xdotool`, `ydotool`, etc.) operate under. Not
something to install on a shared or otherwise untrusted account.

## Files

- `bitbuddy_forwarder.py` — the daemon.
- `bitbuddy-forwarder.service` — systemd `--user` unit, autostarts at login.
- `install.sh` — installs both on a Fedora machine.
- `INSTALL.md` — step-by-step install / troubleshooting.
- `PLAN.md` — the original design doc this was built from.

See `INSTALL.md` to set this up on a (Fedora) machine.
