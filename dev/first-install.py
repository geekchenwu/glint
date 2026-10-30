#!/usr/bin/python3
"""Verify first installation without changing GNOME Shell or the real desktop.

dev/headless.sh fresh-install
source /tmp/lumen-hl/env && python3 dev/first-install.py
dev/headless.sh next-login
source /tmp/lumen-hl/env && python3 dev/first-install.py --next-login
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from gi.repository import Gio, GLib

if os.environ.get('XDG_RUNTIME_DIR') != '/tmp/lumen-hl/run':
    raise SystemExit('Run only in the isolated dev/headless.sh session.')

bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)


def shell_pid():
    return bus.call_sync(
        'org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
        'GetConnectionUnixProcessID', GLib.Variant('(s)', ('org.gnome.Shell',)),
        None, Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]


pid = shell_pid()
# Portals inspect /proc/PID/root; all clients must share the test user namespace.
if os.readlink('/proc/self/ns/user') != os.readlink(f'/proc/{pid}/ns/user'):
    os.execvp('nsenter', ['nsenter', '--target', str(pid), '--user', '--mount',
                          '--preserve-credentials', sys.executable, __file__, *sys.argv[1:]])

os.environ['LUMEN_TEST_SESSION'] = '1'
import ctl

SRC = Path(__file__).resolve().parent.parent


def state():
    return json.loads(bus.call_sync(
        'org.gnome.Shell', ctl.OBJECT, ctl.INTERFACE, 'DebugState', None, None,
        Gio.DBusCallFlags.NONE, 1000, None).unpack()[0])


def extension_info():
    return bus.call_sync(
        'org.gnome.Shell', '/org/gnome/Shell',
        'org.gnome.Shell.Extensions', 'GetExtensionInfo',
        GLib.Variant('(s)', ('lumen@chenwu.local',)), None,
        Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = predicate()
        except GLib.Error as error:
            if not error.matches(Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_METHOD):
                raise
            result = False
        if result:
            return result
        time.sleep(.05)
    raise AssertionError(f'timed out: {state()}')


def control(*args):
    ctl.main(['ctl', *map(str, args)])


def main():
    next_login = '--next-login' in sys.argv
    wait_for(lambda: not state()['startingUp'])
    control('key', 'Escape')
    wait_for(lambda: not state()['overview'])

    def overlay_ready():
        snapshot = state()
        if snapshot['focus'] != 'lumen-overlay' or not snapshot['paintedOverlays']:
            return False
        if not next_login:
            return True
        native = json.loads(bus.call_sync(
            'org.gnome.Shell', '/io/github/lumen/Shell', 'io.github.lumen.Shell',
            'DebugState', None, None, Gio.DBusCallFlags.NONE, 1000, None).unpack()[0])
        return native['overlays'] > 0 and not native['frozen']

    if next_login:
        assert extension_info(), 'GNOME must discover the extension on the next login'
        subprocess.run([
            'gsettings', '--schemadir',
            '/tmp/lumen-hl/data/gnome-shell/extensions/lumen@chenwu.local/schemas',
            'set', 'org.gnome.shell.extensions.lumen', 'debug-mode', 'true'], check=True)
        # Let gsd-media-keys acquire the previous session's temporary keys.
        time.sleep(1.3)
        subprocess.run(['/tmp/lumen-hl/data/lumen/lumen', 'activate'], check=True)
        wait_for(lambda: extension_info().get('state') == 1)
        settings = Gio.Settings.new('org.gnome.settings-daemon.plugins.media-keys')
        wait_for(lambda: not any('/lumen-' in path for path in settings.get_strv('custom-keybindings')))
    else:
        assert not extension_info(), 'native Lumen extension must not be registered'
        subprocess.run(['bash', str(SRC / 'install.sh')], check=True)
        assert not extension_info(), 'installing files must not require restarting Shell'
    with tempfile.TemporaryDirectory(prefix='lumen-first-install-') as temp:
        output = Path(temp) / 'paste.json'
        receiver = subprocess.Popen([
            sys.executable, str(SRC / 'dev/clipboard_receiver.py'), str(output)])
        try:
            control('key', 'Escape')
            wait_for(lambda: state()['focus'] == 'Clipboard receiver')
            control('click', 960, 660)
            if state()['overview']:
                control('key', 'Escape')
                wait_for(lambda: not state()['overview'])
            control('key', 'F1')
            if not next_login:
                # Approve only the permission prompt in this disposable test desktop.
                allow = wait_for(lambda: next(
                    (label for label in state()['labels'] if label['text'] == 'Allow' and
                     label.get('width') and label.get('height')), None), 30)
                control('shot', '/tmp/lumen-first-install-permission.png')
                control('click', allow['x'] + allow['width'] / 2,
                        allow['y'] + allow['height'] / 2)
            wait_for(overlay_ready)
            control('shot', '/tmp/lumen-first-install-overlay.png')

            for action in ('enter', 'ctrl-c', 'double-click', 'quick-copy'):
                if action != 'enter':
                    control('key', 'F1', *(['ctrl'] if action == 'quick-copy' else []))
                    wait_for(overlay_ready)
                control('drag', 760, 390, 1060, 590)
                if action == 'enter':
                    control('key', 'Return')
                elif action == 'ctrl-c':
                    control('key', 'c', 'ctrl')
                elif action == 'double-click':
                    control('click', 900, 500, 'double')
                wait_for(lambda: state()['overlays'] == 0)
                control('click', 960, 660)
                wait_for(lambda: state()['focus'] == 'Clipboard receiver')
                output.unlink(missing_ok=True)
                control('key', 'v', 'ctrl')
                wait_for(output.exists)
                result = json.loads(output.read_text())
                assert 'error' not in result, result
                assert 'image/png' in result['formats'], result
                assert (result['width'], result['height']) == (300, 200), result
                print(f'{action}: real F1 and Ctrl+V, 300x200 PNG OK', flush=True)

            control('key', 'F1')
            wait_for(overlay_ready)
            control('key', 'Escape')
            wait_for(lambda: state()['overlays'] == 0)
            control('click', 960, 660)
            output.unlink()
            control('key', 'v', 'ctrl')
            wait_for(output.exists)
            assert json.loads(output.read_text()).get('width') == 300
            print('Cancel: previous clipboard image preserved', flush=True)

            control('key', 'F3')
            wait_for(lambda: any(title.startswith('lumen-pin') for title in state()['windows']))
            control('key', 'F3', 'shift')
            wait_for(lambda: not any(title.startswith('lumen-pin') for title in state()['windows']))
            control('key', 'F3', 'shift')
            wait_for(lambda: any(title.startswith('lumen-pin') for title in state()['windows']))
            print('F3 clipboard pin and Shift+F3 available', flush=True)
            assert shell_pid() == pid
            assert bool(extension_info()) == next_login
            assert not list(Path('/tmp/lumen-hl/run/lumen').glob('portal-*.png'))
            if next_login:
                print('PASS: native extension enabled, temporary shortcuts removed, all keys work', flush=True)
            else:
                print(f'PASS: same GNOME Shell PID {pid}, native Lumen absent, no logout', flush=True)
        finally:
            receiver.terminate()
            receiver.wait(timeout=5)


if __name__ == '__main__':
    main()
