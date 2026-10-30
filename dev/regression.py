#!/usr/bin/python3
"""Real screenshot, cross-process paste and panel geometry checks.

dev/headless.sh start && dev/reload.sh
source /tmp/lumen-hl/env && python3 dev/regression.py
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from gi.repository import Gio

import ctl


def state():
    result = ctl.bus.call_sync(
        'org.gnome.Shell', '/io/github/lumen/Shell', 'io.github.lumen.Shell',
        'DebugState', None, None, Gio.DBusCallFlags.NONE, 1000, None)
    return json.loads(result.unpack()[0])


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError(f'timed out; shell state: {state()}')


def control(*args):
    ctl.main(['ctl', *map(str, args)])


def main():
    if os.environ.get('XDG_RUNTIME_DIR') != '/tmp/lumen-hl/run':
        raise SystemExit('Run only in the isolated dev/headless.sh session.')
    with tempfile.TemporaryDirectory(prefix='lumen-paste-') as temp:
        output = Path(temp) / 'paste.json'
        receiver = subprocess.Popen([
            sys.executable, str(Path(__file__).with_name('clipboard_receiver.py')), str(output)])
        try:
            control('key', 'Escape')
            time.sleep(.3)
            control('key', 'Escape')
            wait_for(lambda: state()['focus'] == 'Clipboard receiver')
            initial = state()
            cx, cy = initial['stage']['width'] // 2, initial['stage']['height'] // 2
            durations = []
            for action in ('enter', 'ctrl-c', 'double-click', 'quick-copy', 'pin'):
                control('click', cx, cy + 120)
                start = time.monotonic()
                control('key', 'F1', *(['ctrl'] if action == 'quick-copy' else []))
                wait_for(lambda: state()['overlays'] > 0 and not state()['frozen'])
                durations.append(round((time.monotonic() - start) * 1000))
                control('drag', cx - 200, cy - 150, cx + 100, cy + 50)
                if action == 'enter':
                    control('key', 'Return')
                elif action == 'ctrl-c':
                    control('key', 'c', 'ctrl')
                elif action == 'double-click':
                    control('click', cx - 50, cy - 50, 'double')
                elif action == 'pin':
                    control('key', 't', 'ctrl')
                wait_for(lambda: state()['overlays'] == 0)
                if action == 'pin':
                    wait_for(lambda: (state()['focus'] or '').startswith('lumen-pin'))
                    time.sleep(.15)
                output.unlink(missing_ok=True)
                control('click', cx, cy + 120)
                wait_for(lambda: state()['focus'] == 'Clipboard receiver')
                control('key', 'v', 'ctrl')
                wait_for(output.exists)
                result = json.loads(output.read_text())
                assert 'error' not in result, (action, result)
                assert 'image/png' in result['formats'], (action, result)
                assert (result['width'], result['height']) == (300, 200), (action, result)
                print(f'{action}: paste 300x200 PNG OK; overlay ready in {durations[-1]} ms', flush=True)
                if action == 'pin':
                    control('click', cx - 50, cy - 50)
                    control('key', 'Escape')

            # Opening and cancelling the next capture must preserve the image.
            control('key', 'F1')
            wait_for(lambda: state()['overlays'] > 0 and not state()['frozen'])
            control('key', 'Escape')
            wait_for(lambda: state()['overlays'] == 0)
            output.unlink()
            control('click', cx, cy + 120)
            control('key', 'v', 'ctrl')
            wait_for(output.exists)
            assert json.loads(output.read_text()).get('width') == 300
            print('cancel: previous clipboard image preserved', flush=True)

            # Exercise Shell's asynchronous clipboard callback repeatedly;
            # retaining its borrowed GBytes past the callback can crash Shell.
            for _ in range(3):
                control('key', 'F3')
                wait_for(lambda: (state()['focus'] or '').startswith('lumen-pin'))
                control('key', 'Escape')
                wait_for(lambda: not (state()['focus'] or '').startswith('lumen-pin'))
            print('F3: three clipboard image pins without a Shell restart', flush=True)

            indicator = state()['indicator']
            assert indicator is not None, 'indicator is not visible'
            control('click', indicator['x'] + indicator['width'] / 2,
                    indicator['y'] + indicator['height'] / 2)
            wait_for(lambda: state()['menu'] is not None)
            menu = state()['menu']
            menu_center = menu['x'] + menu['width'] / 2
            icon_center = indicator['x'] + indicator['width'] / 2
            offset = abs(menu_center - icon_center)
            # Shell clamps the popup to the monitor's work area at its edges.
            edge_clamped = (menu_center < icon_center and
                            0 <= initial['stage']['width'] - menu['x'] - menu['width'] <= 8)
            assert offset <= 2 or edge_clamped, (indicator, menu, offset)
            control('shot', '/tmp/lumen-menu-centered.png')
            control('key', 'Escape')
            print(f'indicator menu: center offset {offset:.1f} px; edge clamp={edge_clamped}', flush=True)
            # The first window also initializes GTK's renderer and font caches.
            assert durations[0] < 2500 and max(durations[1:]) < 1500, durations
            assert not list(Path('/tmp/lumen-hl/run/lumen').glob('capture-*'))
        finally:
            receiver.terminate()
            receiver.wait(timeout=5)


if __name__ == '__main__':
    main()
