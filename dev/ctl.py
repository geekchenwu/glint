#!/usr/bin/python3
"""Drive the headless test shell through Glint's debug D-Bus methods.

  ctl.py shot PATH            stage screenshot
  ctl.py capture [edit|copy]  same as pressing F1 / Ctrl+F1
  ctl.py pin | toggle-pins
  ctl.py move X Y
  ctl.py click X Y [right] [double]
  ctl.py drag X1 Y1 X2 Y2 [steps] [shift]
  ctl.py key NAME [ctrl] [shift]   (GDK key names, e.g. Escape, Return, r, z)
  ctl.py type TEXT
  ctl.py scroll DY
Run after `source /tmp/glint-hl/env`.
"""
import os
import sys
import time

import gi

gi.require_version('Gdk', '4.0')
from gi.repository import Gdk, Gio, GLib  # noqa: E402

# Clutter virtual devices take Clutter button numbers, not evdev codes.
BTN_LEFT, BTN_RIGHT = 1, 3
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
TEST_SESSION = os.environ.get('GLINT_TEST_SESSION') == '1'
OBJECT = '/io/github/glint/TestSession' if TEST_SESSION else '/io/github/glint/Shell'
INTERFACE = 'io.github.glint.TestSession' if TEST_SESSION else 'io.github.glint.Shell'


def call(method, sig=None, *args):
    params = GLib.Variant(sig, args) if sig else None
    deadline = time.monotonic() + 3
    while True:
        try:
            bus.call_sync('org.gnome.Shell', OBJECT, INTERFACE,
                          method, params, None, Gio.DBusCallFlags.NONE, 20000, None)
            return
        except GLib.Error as error:
            # GNOME temporarily rebases test extensions during initial startup.
            if (not TEST_SESSION or time.monotonic() >= deadline or
                    not error.matches(Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_METHOD)):
                raise
            time.sleep(.05)


def pointer(x, y, button=0, state=0):
    call('DebugPointer', '(ddui)', float(x), float(y), button, state)


def key(name, down):
    keyval = Gdk.keyval_from_name(name)
    call('DebugKey', '(ui)', keyval, 1 if down else 0)


def main(argv):
    cmd, args = argv[1], argv[2:]
    if cmd == 'shot':
        if os.path.exists(args[0]):
            os.unlink(args[0])
        call('DebugScreenshot', '(s)', args[0])
        # The shell writes the PNG asynchronously; wait until it settles.
        last = -1
        for _ in range(100):
            size = os.path.getsize(args[0]) if os.path.exists(args[0]) else -1
            if size > 0 and size == last:
                break
            last = size
            time.sleep(0.05)
    elif cmd == 'capture':
        call('Capture', '(s)', args[0] if args else 'edit')
    elif cmd == 'pin':
        call('Pin')
    elif cmd == 'toggle-pins':
        call('TogglePins')
    elif cmd == 'move':
        pointer(args[0], args[1])
    elif cmd == 'click':
        x, y = float(args[0]), float(args[1])
        button = BTN_RIGHT if 'right' in args else BTN_LEFT
        for _ in range(2 if 'double' in args else 1):
            pointer(x, y)
            pointer(x, y, button, 1)
            time.sleep(0.03)
            pointer(x, y, button, 0)
            time.sleep(0.06)
    elif cmd == 'drag':
        x1, y1, x2, y2 = (float(v) for v in args[:4])
        steps = int(args[4]) if len(args) > 4 and args[4].isdigit() else 12
        if 'shift' in args:
            key('Shift_L', True)
        pointer(x1, y1)
        pointer(x1, y1, BTN_LEFT, 1)
        time.sleep(0.05)
        for i in range(1, steps + 1):
            t = i / steps
            pointer(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)
            time.sleep(0.012)
        pointer(x2, y2, BTN_LEFT, 0)
        if 'shift' in args:
            key('Shift_L', False)
    elif cmd == 'key':
        mods = [m for m in ('ctrl', 'shift') if m in args[1:]]
        names = {'ctrl': 'Control_L', 'shift': 'Shift_L'}
        for m in mods:
            key(names[m], True)
        key(args[0], True)
        key(args[0], False)
        for m in reversed(mods):
            key(names[m], False)
    elif cmd == 'type':
        for ch in args[0]:
            name = Gdk.keyval_name(Gdk.unicode_to_keyval(ord(ch)))
            key(name, True)
            key(name, False)
    elif cmd == 'scroll':
        call('DebugScroll', '(dd)', 0.0, float(args[0]))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
