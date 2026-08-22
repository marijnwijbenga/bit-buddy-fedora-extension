# Install (Fedora)

Requires: Fedora with KDE Plasma (or any desktop where the systemd
`--user` session inherits `DISPLAY` at login — most modern desktops do
this; if the service fails to find `DISPLAY`, see Troubleshooting below).
Should work on other X11/XWayland desktops too, but this was only tested
on Fedora KDE.

## 1. Run the installer

```bash
git clone <this-repo-url>
cd bitbuddy-input-forwarder
./install.sh
```

This will:
- Ask for your sudo password to install `python3-evdev` and `python3-xlib`
  (Fedora packages — no compiling needed).
- Add you to the `input` group, so the daemon can read `/dev/input/eventX`
  as your normal user (no root at runtime).
- Copy the daemon to `~/.local/share/bitbuddy-forwarder/`.
- Install and enable the systemd `--user` service.

**`sudo` needs a real terminal.** If you're running this through an agent
or non-interactive shell and it fails with "a terminal is required to read
the password," run `install.sh` in a normal terminal window instead.

## 2. Log out and back in

Two things only take effect after a full logout/login, not just a new
shell:
- Your `input` group membership.
- The systemd `--user` session picking up that new group membership.

The service is enabled, so it starts automatically once you're back in.

## 3. Verify it's running

```bash
systemctl --user status bitbuddy-forwarder.service
tail -f ~/.local/share/bitbuddy-forwarder.log
```

You should see `Starting Bit Buddy input forwarder`, then `Bit Buddy
window found: id=... size=...` once the game is running, and `Listening
on device: ...` lines for your keyboard(s).

## 4. Confirm the game actually reacts to it

Before trusting this to run in the background, check it's actually
working: launch Bit Buddy, click into a *different* window so Bit Buddy
loses focus, then type something. Watch for Bit Buddy to react
(animation, sound, whatever it does on input). If it doesn't react at all,
see Troubleshooting.

## If this is a different Steam app / game

The daemon targets Bit Buddy specifically via its Steam app ID. If you're
adapting this for a different game, edit the top of
`~/.local/share/bitbuddy-forwarder/bitbuddy_forwarder.py`:

```python
TARGET_WM_CLASS = "steam_app_3874950"  # change to your game's app ID
```

Find the right value with the game running:

```bash
wmctrl -lx | grep -i <part of game name>
```

The class shown (e.g. `steam_app_123456.steam_app_123456`) is what to use.

If the wrong window gets picked (some games/helpers leave invisible
windows under the same class — see `README.md`'s "Known quirks"), the
`find_target_window()` function in `bitbuddy_forwarder.py` is what does
the selection; it already filters to `map_state == IsViewable`, which
should handle most cases.

## Troubleshooting

**Log shows "No usable input devices found"**
Your `input` group membership hasn't taken effect yet — log out and back
in (see step 2). Check with `groups | grep input`.

**Service fails to start / can't connect to X display**
The systemd `--user` session didn't inherit `DISPLAY`. Check with:
```bash
systemctl --user show-environment | grep DISPLAY
```
If empty, your desktop environment isn't exporting it into the systemd
user session automatically. As a workaround, add to the `[Service]`
section of `~/.config/systemd/user/bitbuddy-forwarder.service`:
```ini
Environment=DISPLAY=:0
```
(adjust `:0` if your XWayland display differs — check `echo $DISPLAY` in
a real desktop terminal session).

**Daemon runs, but the game never reacts to forwarded input**
Confirm the underlying assumption still holds: the game's window must
accept *synthetic* X11 events at all (some Wine apps ignore them as an
anti-cheat measure). Test directly:
```bash
xdotool key --window <window_id> a
```
using a window ID from `wmctrl -lx`. If the game doesn't react to that
either, this whole approach won't work for it.

**Wrong window gets targeted, or it keeps flip-flopping**
Check `map_state` for all matching windows (see `README.md`'s "Known
quirks") — something invisible under the same WM_CLASS is probably
winning on size. Only windows with `map_state == IsViewable` should be
considered.

## Uninstall

```bash
systemctl --user disable --now bitbuddy-forwarder.service
rm ~/.config/systemd/user/bitbuddy-forwarder.service
rm -rf ~/.local/share/bitbuddy-forwarder
rm -f ~/.local/share/bitbuddy-forwarder.log
systemctl --user daemon-reload
```
Removing yourself from the `input` group is optional and not
Bit-Buddy-specific — leave it unless something else needs it gone.
