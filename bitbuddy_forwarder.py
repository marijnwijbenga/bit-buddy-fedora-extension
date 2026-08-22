#!/usr/bin/env python3
"""
Bit Buddy input forwarder.

Bit Buddy's own global-input helper (RawInput Helper.exe) relies on a
Windows RPC hand-off to the main game process that fails under Proton
(RPC_S_SERVER_UNAVAILABLE). This daemon works around that: it reads real
keyboard input straight from the kernel (evdev), without grabbing it (so
it still reaches the rest of the desktop normally), and forwards a
synthetic copy directly to Bit Buddy's X11 window by window ID -- so the
game reacts to keystrokes even when it is not focused.

Keyboard only. Mouse forwarding was tried and dropped: sending synthetic
clicks to Bit Buddy's window made it (or Proton's window handling) grab
focus/stacking attention on every real click anywhere on the desktop,
breaking normal window-focus clicking elsewhere.

Never logs which keys were pressed -- only structural events (window
found/lost, device errors) go to the log file.
"""

import asyncio
import logging
import logging.handlers
import os
import signal
import sys

import evdev
from evdev import ecodes
from Xlib import X, display as xdisplay
from Xlib.protocol import event as xevent
from Xlib import error as xerror

TARGET_WM_CLASS = "steam_app_3874950"  # Bit Buddy's Steam app ID
MIN_WINDOW_SIZE = 10  # px; filters out Steam's tiny placeholder windows
WINDOW_POLL_INTERVAL = 2.0  # seconds

# Standard XKB "evdev" ruleset (used by virtually all modern Linux X
# servers, including XWayland): X11 keycode = Linux evdev keycode + 8.
# This lets the target app's own XKB engine resolve the actual character,
# so no per-key keysym lookup table is needed.
KEYCODE_OFFSET = 8

MODIFIER_KEYS = {
    ecodes.KEY_LEFTSHIFT: "shift",
    ecodes.KEY_RIGHTSHIFT: "shift",
    ecodes.KEY_LEFTCTRL: "ctrl",
    ecodes.KEY_RIGHTCTRL: "ctrl",
    ecodes.KEY_LEFTALT: "alt",
    ecodes.KEY_RIGHTALT: "alt",
}

LOG_PATH = os.path.expanduser("~/.local/share/bitbuddy-forwarder.log")

log = logging.getLogger("bitbuddy-forwarder")


def setup_logging():
    log.setLevel(logging.INFO)
    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=1_000_000, backupCount=1
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    log.addHandler(handler)
    log.addHandler(logging.StreamHandler(sys.stderr))


def iter_windows(win):
    yield win
    try:
        children = win.query_tree().children
    except xerror.XError:
        return
    for child in children:
        yield from iter_windows(child)


def find_target_window(disp):
    """Return (window, geometry) for Bit Buddy's real pet window, or None."""
    root = disp.screen().root
    candidates = []
    for win in iter_windows(root):
        try:
            wm_class = win.get_wm_class()
        except xerror.XError:
            continue
        if not wm_class:
            continue
        instance, cls = wm_class
        if TARGET_WM_CLASS not in (instance, cls):
            continue
        try:
            attrs = win.get_attributes()
            if attrs.map_state != X.IsViewable:
                # Excludes invisible helper/placeholder windows Steam
                # creates under the same WM_CLASS -- notably Bit Buddy's
                # own broken "RawInput Helper" window, which is huge and
                # would otherwise win on size.
                continue
            geom = win.get_geometry()
        except xerror.XError:
            continue
        if geom.width < MIN_WINDOW_SIZE or geom.height < MIN_WINDOW_SIZE:
            continue
        candidates.append((geom.width * geom.height, win, geom))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0], reverse=True)
    _, win, geom = candidates[0]
    return win, geom


class ModifierState:
    def __init__(self, disp):
        self._held = {"shift": 0, "ctrl": 0, "alt": 0}
        self._mask_bits = self._build_mask_bits(disp)

    def _build_mask_bits(self, disp):
        mapping = disp.get_modifier_mapping()
        bits = {"shift": 0, "ctrl": 0, "alt": 0}
        # mapping index: 0=Shift 1=Lock 2=Control 3=Mod1(Alt) ...
        name_by_index = {0: "shift", 2: "ctrl", 3: "alt"}
        for index, keycodes in enumerate(mapping):
            name = name_by_index.get(index)
            if name is None:
                continue
            if any(keycodes):
                bits[name] = 1 << index
        return bits

    def update(self, evdev_code, pressed):
        name = MODIFIER_KEYS.get(evdev_code)
        if name is None:
            return
        self._held[name] = 1 if pressed else 0

    @property
    def state_mask(self):
        mask = 0
        for name, held in self._held.items():
            if held:
                mask |= self._mask_bits.get(name, 0)
        return mask


class WindowTarget:
    """Holds the currently known Bit Buddy window, refreshed periodically."""

    def __init__(self, disp):
        self.disp = disp
        self.window = None
        self.geometry = None

    def refresh(self):
        found = find_target_window(self.disp)
        had_window = self.window is not None
        if found is None:
            if had_window:
                log.info("Bit Buddy window lost, will keep polling")
            self.window = None
            self.geometry = None
            return
        win, geom = found
        is_new = self.window is None or win.id != self.window.id
        self.window = win
        self.geometry = geom
        if is_new:
            log.info("Bit Buddy window found: id=0x%x size=%dx%d",
                      win.id, geom.width, geom.height)


class Forwarder:
    def __init__(self, disp, target: WindowTarget, modifiers: ModifierState):
        self.disp = disp
        self.target = target
        self.modifiers = modifiers

    def _send(self, ev):
        win = self.target.window
        if win is None:
            return
        try:
            win.send_event(ev, event_mask=0, propagate=False)
            self.disp.flush()
        except xerror.XError:
            log.info("Lost Bit Buddy window mid-send, will re-poll")
            self.target.window = None
            self.target.geometry = None

    def send_key(self, evdev_code, pressed):
        win = self.target.window
        if win is None:
            return
        keycode = evdev_code + KEYCODE_OFFSET
        cls = xevent.KeyPress if pressed else xevent.KeyRelease
        ev = cls(
            time=X.CurrentTime,
            root=self.disp.screen().root,
            window=win,
            same_screen=1,
            child=X.NONE,
            root_x=0, root_y=0,
            event_x=0, event_y=0,
            state=self.modifiers.state_mask,
            detail=keycode,
        )
        self._send(ev)


def usable_input_devices():
    """Real keyboards only -- identified by having KEY_A, which excludes
    mice/touchpads (BTN_* only) and oddities like power/sleep buttons."""
    devices = []
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except (OSError, PermissionError) as e:
            log.warning("Could not open %s: %s", path, e)
            continue
        caps = dev.capabilities()
        if ecodes.KEY_A in caps.get(ecodes.EV_KEY, []):
            devices.append(dev)
        else:
            dev.close()
    return devices


async def listen_device(dev, forwarder: Forwarder, modifiers: ModifierState):
    log.info("Listening on device: %s", dev.name)
    try:
        async for ev in dev.async_read_loop():
            if ev.type != ecodes.EV_KEY:
                continue
            if ev.value not in (0, 1):  # ignore key-repeat
                continue
            modifiers.update(ev.code, ev.value == 1)
            forwarder.send_key(ev.code, ev.value == 1)
    except (OSError, PermissionError) as e:
        log.warning("Device %s disconnected: %s", dev.name, e)
    except asyncio.CancelledError:
        raise


async def poll_window(target: WindowTarget, stop_event: asyncio.Event):
    while not stop_event.is_set():
        target.refresh()
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=WINDOW_POLL_INTERVAL)
        except asyncio.TimeoutError:
            pass


async def main_async():
    setup_logging()
    log.info("Starting Bit Buddy input forwarder")

    disp = xdisplay.Display()
    target = WindowTarget(disp)
    modifiers = ModifierState(disp)
    forwarder = Forwarder(disp, target, modifiers)
    target.refresh()

    devices = usable_input_devices()
    if not devices:
        log.error("No usable input devices found (check `input` group membership)")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def handle_stop():
        log.info("Shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, handle_stop)

    tasks = [asyncio.create_task(poll_window(target, stop_event))]
    for dev in devices:
        tasks.append(asyncio.create_task(listen_device(dev, forwarder, modifiers)))

    await stop_event.wait()

    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    for dev in devices:
        dev.close()
    disp.close()
    log.info("Bit Buddy input forwarder stopped")


def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
