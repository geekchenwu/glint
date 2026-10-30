"""Screenshot fallback before GNOME discovers a newly installed extension."""
import json
import os
import shutil
import tempfile
import uuid

import gi

gi.require_version('Gdk', '4.0')
from gi.repository import Gdk, Gio, GLib

PORTAL_NAME = 'org.freedesktop.portal.Desktop'
PORTAL_PATH = '/org/freedesktop/portal/desktop'
REQUEST_IFACE = 'org.freedesktop.portal.Request'


class ScreenshotPortal:
    def __init__(self, app):
        self.app = app
        self.bus = app.get_dbus_connection()
        self._subscription = 0
        self._request_path = None
        self._timeout = 0

    @property
    def pending(self):
        return self._request_path is not None

    def capture(self, mode):
        if self.pending or self.app.session is not None:
            return
        self.app.withdraw_notification('glint-setup')
        token = 'glint_' + uuid.uuid4().hex
        sender = self.bus.get_unique_name().lstrip(':').replace('.', '_')
        self._request_path = f'{PORTAL_PATH}/request/{sender}/{token}'
        request_path = self._request_path
        self._subscription = self.bus.signal_subscribe(
            PORTAL_NAME, REQUEST_IFACE, 'Response', self._request_path, None,
            Gio.DBusSignalFlags.NONE,
            lambda _bus, _sender, _path, _iface, _signal, params: self._response(mode, params))
        options = {'handle_token': GLib.Variant('s', token),
                   'interactive': GLib.Variant('b', False)}
        self._timeout = GLib.timeout_add_seconds(180, self._expired)
        self.bus.call(
            PORTAL_NAME, PORTAL_PATH, 'org.freedesktop.portal.Screenshot', 'Screenshot',
            GLib.Variant('(sa{sv})', ('', options)), GLib.VariantType('(o)'),
            Gio.DBusCallFlags.NONE, 10000, None,
            lambda bus, result: self._requested(bus, result, request_path))

    def _requested(self, bus, result, request_path):
        try:
            actual_path = bus.call_finish(result).unpack()[0]
            if self._request_path != request_path:
                return
            if actual_path != request_path:
                raise ValueError('Unexpected screenshot request handle')
        except (GLib.Error, ValueError) as error:
            if self._request_path == request_path:
                self.close()
                self._error(error)

    def _response(self, mode, params):
        response, results = params.unpack()
        self._clear()
        if response == 1:
            return
        if response != 0:
            self._error('The system screenshot request failed')
            return
        path = None
        try:
            source = Gio.File.new_for_uri(results['uri'])
            if not source.is_native():
                raise ValueError('The screenshot portal did not return a local image')
            os.makedirs(self.app._capture_dir, mode=0o700, exist_ok=True)
            # The portal owns its original file. Only our private copy is deleted.
            with tempfile.NamedTemporaryFile(
                    dir=self.app._capture_dir, prefix='portal-', suffix='.png', delete=False) as output:
                path = output.name
                with open(source.get_path(), 'rb') as image:
                    shutil.copyfileobj(image, output)
            self.app.capture(mode, path, json.dumps(self._metadata()))
        except (GLib.Error, OSError, ValueError, KeyError) as error:
            self._error(error)
        finally:
            if path and os.path.exists(path):
                os.unlink(path)

    @staticmethod
    def _metadata():
        monitors = []
        for index, monitor in enumerate(Gdk.Display.get_default().get_monitors()):
            rect = monitor.get_geometry()
            monitors.append({'index': index, 'x': rect.x, 'y': rect.y,
                             'gdk_x': rect.x, 'gdk_y': rect.y,
                             'width': rect.width, 'height': rect.height})
        if not monitors:
            return {}
        left = min(m['x'] for m in monitors)
        top = min(m['y'] for m in monitors)
        for monitor in monitors:
            monitor['x'] -= left
            monitor['y'] -= top
        width = max(m['x'] + m['width'] for m in monitors)
        height = max(m['y'] + m['height'] for m in monitors)
        return {'version': 1, 'stage': {'width': width, 'height': height},
                'monitors': monitors, 'pointer': {'x': width / 2, 'y': height / 2}}

    def _error(self, error):
        print(f'glint: screenshot portal: {error}', flush=True)
        note = Gio.Notification.new('鹿门截图未完成')
        note.set_body('请允许系统的截图请求后重试。')
        self.app.send_notification('glint-portal', note)

    def _expired(self):
        self._timeout = 0
        self.close()
        self._error('Screenshot permission request timed out')
        return GLib.SOURCE_REMOVE

    def _clear(self):
        if self._subscription:
            self.bus.signal_unsubscribe(self._subscription)
        if self._timeout:
            GLib.source_remove(self._timeout)
        self._subscription = self._timeout = 0
        self._request_path = None

    def close(self):
        if self._request_path:
            self.bus.call(PORTAL_NAME, self._request_path, REQUEST_IFACE, 'Close',
                          None, None, Gio.DBusCallFlags.NONE, 2000, None, None)
        self._clear()
