"""Resident editor process.

Staying resident keeps Python/GTK start-up out of the capture path and keeps
Wayland clipboard contents alive after the overlay closes.
"""
import json
import math
import os
import time

import cairo
import gi

gi.require_version('Gdk', '4.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Gtk', '4.0')
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, Pango, PangoCairo  # noqa: E402

from . import APP_ID, DAEMON_IFACE, DAEMON_PATH, firstrun, render, theme  # noqa: E402
from .overlay import Session  # noqa: E402
from .pin import PinWindow  # noqa: E402
from .portal import ScreenshotPortal  # noqa: E402

DAEMON_XML = f"""
<node>
  <interface name="{DAEMON_IFACE}">
    <method name="Capture">
      <arg type="s" name="mode" direction="in"/>
      <arg type="s" name="path" direction="in"/>
      <arg type="s" name="meta" direction="in"/>
    </method>
    <method name="PinImage"><arg type="s" name="path" direction="in"/></method>
    <method name="PinText"><arg type="s" name="text" direction="in"/></method>
    <method name="PinLast"/>
    <method name="TogglePins"/>
    <method name="OpenFolder"/>
    <method name="PortalCapture"><arg type="s" name="mode" direction="in"/></method>
    <method name="PortalPin"/>
  </interface>
</node>
"""

# Methods that read files from the shell's private capture directory, keyed
# to the index of their path argument.
SHELL_ONLY = {'Capture': 1, 'PinImage': 0}


def screenshots_dir():
    pictures = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES) or \
        os.path.join(GLib.get_home_dir(), 'Pictures')
    path = os.path.join(pictures, 'Screenshots')
    os.makedirs(path, exist_ok=True)
    return path


def unique_path(directory):
    stamp = time.strftime('%Y-%m-%d_%H-%M-%S')
    path = os.path.join(directory, f'Glint_{stamp}.png')
    n = 2
    while os.path.exists(path):
        path = os.path.join(directory, f'Glint_{stamp}_{n}.png')
        n += 1
    return path


class GlintApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.session = None
        self.pins = []
        self.pins_hidden = False
        self.last = None
        self.last_sel = None
        self.style = {'color': theme.PALETTE[0],
                      'sizes': {'stroke': 1, 'marker': 1, 'text': 1, 'mosaic': 1, 'step': 1}}
        self._registration = 0
        self._capture_dir = os.path.join(GLib.get_user_runtime_dir(), 'glint')
        self._portal = None
        self._clipboard_reader = None

    # ---- lifecycle ------------------------------------------------------

    def do_startup(self):
        Gtk.Application.do_startup(self)
        theme.load_fonts()
        theme.install_css()
        self.hold()
        for name, handler, ptype in (('open-file', self._open_file, GLib.VariantType('s')),
                                     ('open-folder', lambda *_: self.open_folder(), None),
                                     ('take-over-hotkeys', lambda *_: self._take_over_hotkeys(), None),
                                     ('keep-hotkeys', lambda *_: self._keep_hotkeys(), None)):
            action = Gio.SimpleAction.new(name, ptype)
            action.connect('activate', handler)
            self.add_action(action)
        self._portal = ScreenshotPortal(self)
        self.get_dbus_connection().signal_subscribe(
            'org.gnome.Shell', 'org.gnome.Shell.Extensions', 'ExtensionStateChanged',
            '/org/gnome/Shell', firstrun.EXT_UUID, Gio.DBusSignalFlags.NONE,
            self._extension_changed)
        GLib.idle_add(lambda: self._first_run() and False)

    def do_activate(self):
        pass

    def do_shutdown(self):
        if self._portal:
            self._portal.close()
        Gtk.Application.do_shutdown(self)

    # ---- first run -------------------------------------------------------

    def _extension_changed(self, _bus, _sender, _path, _iface, _signal, params):
        _uuid, info = params.unpack()
        if info.get('state') == 1 and info.get('enabled'):
            firstrun.remove_fallback_shortcuts()
            firstrun.State().set('extension-enabled')
            self.withdraw_notification('glint-setup')

    def _first_run(self):
        state = firstrun.State()
        status = firstrun.prepare_session()
        if (status != 'enabled' and not state.get('extension-enabled') and
                not state.get('portal-introduced')):
            note = Gio.Notification.new('Glint 已就绪')
            binding = firstrun.fallback_capture_binding()
            start = f'现在即可按 {binding} 截图。' if binding else '从应用菜单打开 Glint 即可截图。'
            note.set_body(start + '首次使用请允许系统截图请求；'
                          '菜单抓取、贴图置顶等增强功能将在下次正常登录后自动启用。')
            self.send_notification('glint-setup', note)
            state.set('portal-introduced')

        clashes = firstrun.conflicts()
        if clashes and not state.get('hotkeys-decided'):
            keys = '、'.join(sorted({c['binding'].replace('<Control>', 'Ctrl+')
                                    .replace('<Shift>', 'Shift+') for c in clashes}))
            names = '、'.join(sorted({c['name'] or c['command'] for c in clashes}))[:120]
            note = Gio.Notification.new('快捷键冲突')
            note.set_body(f'{keys} 已被「{names}」占用。交给 Glint 吗？原设置会备份，'
                          '可用 glint hotkeys restore 恢复。')
            note.add_button('交给 Glint', 'app.take-over-hotkeys')
            note.add_button('保持现状', 'app.keep-hotkeys')
            note.set_priority(Gio.NotificationPriority.HIGH)
            self.send_notification('glint-hotkeys', note)

    def _take_over_hotkeys(self):
        taken = firstrun.take_over()
        if firstrun.extension_status() != 'enabled':
            firstrun.install_fallback_shortcuts()
        firstrun.State().set('hotkeys-decided')
        self.withdraw_notification('glint-hotkeys')
        note = Gio.Notification.new('快捷键已交给 Glint')
        note.set_body(f'已停用 {len(taken)} 个冲突的自定义快捷键，F1 截图、F3 贴图。')
        self.send_notification('glint-hotkeys-done', note)

    def _keep_hotkeys(self):
        firstrun.State().set('hotkeys-decided')
        self.withdraw_notification('glint-hotkeys')

    def do_dbus_register(self, connection, object_path):
        Gtk.Application.do_dbus_register(self, connection, object_path)
        info = Gio.DBusNodeInfo.new_for_xml(DAEMON_XML).interfaces[0]
        self._registration = connection.register_object(DAEMON_PATH, info, self._on_call, None, None)
        return True

    def do_dbus_unregister(self, connection, object_path):
        if self._registration:
            connection.unregister_object(self._registration)
            self._registration = 0
        Gtk.Application.do_dbus_unregister(self, connection, object_path)

    # ---- D-Bus ------------------------------------------------------------

    def _shell_owner(self, connection):
        try:
            reply = connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                                         'org.freedesktop.DBus', 'GetNameOwner',
                                         GLib.Variant('(s)', ('org.gnome.Shell',)),
                                         GLib.VariantType('(s)'), Gio.DBusCallFlags.NONE, 1000, None)
            return reply.unpack()[0]
        except GLib.Error:
            return None

    def _private_path(self, path):
        real = os.path.realpath(path)
        return os.path.dirname(real) == os.path.realpath(self._capture_dir)

    def _on_call(self, connection, sender, _path, _iface, method, params, invocation):
        args = params.unpack()
        if method in SHELL_ONLY:
            path = args[SHELL_ONLY[method]]
            if sender != self._shell_owner(connection) or not self._private_path(path):
                invocation.return_dbus_error('org.freedesktop.DBus.Error.AccessDenied',
                                             'only GNOME Shell may hand over captures')
                return
        invocation.return_value(None)
        # Reply first so the shell is not kept waiting on image decoding.
        handler = {
            'Capture': lambda: self.capture(*args),
            'PinImage': lambda: self.pin_image(*args),
            'PinText': lambda: self.pin_text(*args),
            'PinLast': self.pin_last,
            'TogglePins': self.toggle_pins,
            'OpenFolder': self.open_folder,
            'PortalCapture': lambda: self._portal.capture(args[0]),
            'PortalPin': self.portal_pin,
        }[method]
        GLib.idle_add(lambda: handler() and False)

    # ---- capture ----------------------------------------------------------

    def capture(self, mode, path, meta_json):
        try:
            t0 = time.monotonic()
            cap = render.Capture(path, json.loads(meta_json))
            t1 = time.monotonic()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        if self.session is not None:
            self.session.close()
        self.session = Session(self, cap, mode)
        print(f'glint: decode {1000 * (t1 - t0):.0f} ms, session {1000 * (time.monotonic() - t1):.0f} ms, '
              f'shell {cap.meta.get("timing")}', flush=True)

    def session_closed(self, session):
        if self.session is session:
            self.session = None

    def remember(self, texture, device_scale):
        self.last = (texture, device_scale)

    # ---- clipboard / files -------------------------------------------------

    def portal_pin(self):
        if self._clipboard_reader:
            self._clipboard_reader.present()
            return
        # Wayland grants clipboard access to the focused client. A short-lived
        # visible window acquires that focus without any compositor privileges.
        window = Gtk.Window(application=self, title='Glint', default_width=280,
                            default_height=96, resizable=False)
        window.set_child(Gtk.Label(label='正在读取剪贴板…', margin_top=20, margin_bottom=20,
                                   margin_start=20, margin_end=20))
        self._clipboard_reader = window

        def finish(texture=None, text=None, cancelled=False):
            if self._clipboard_reader is not window:
                return
            self._clipboard_reader = None
            window.destroy()
            if cancelled:
                return
            if texture:
                self.pin_texture(texture, self._display_scale())
            elif text:
                self.pin_text(text)
            else:
                self.pin_last()

        def text_ready(clipboard, result):
            try:
                finish(text=clipboard.read_text_finish(result))
            except GLib.Error:
                finish()

        def image_ready(clipboard, result):
            try:
                texture = clipboard.read_texture_finish(result)
            except GLib.Error:
                texture = None
            if self._clipboard_reader is not window:
                return
            if texture:
                finish(texture=texture)
            else:
                clipboard.read_text_async(None, text_ready)

        def focused(_window, _param):
            if window.is_active():
                window.disconnect(focus_id)
                Gdk.Display.get_default().get_clipboard().read_texture_async(None, image_ready)

        focus_id = window.connect('notify::is-active', focused)
        window.connect('close-request', lambda *_: finish(cancelled=True) or True)
        window.present()

    def copy_texture(self, texture):
        # Serializers are registered for GdkTexture, not GdkMemoryTexture.
        Gdk.Display.get_default().get_clipboard().set_content(
            Gdk.ContentProvider.new_for_value(GObject.Value(Gdk.Texture, texture)))

    def copy_text(self, text):
        Gdk.Display.get_default().get_clipboard().set_content(
            Gdk.ContentProvider.new_for_value(text))

    def save_surface(self, surface):
        path = unique_path(screenshots_dir())
        surface.write_to_png(path)
        self._notify_saved(path)
        return path

    def save_surface_as(self, surface):
        self._save_dialog(lambda path: surface.write_to_png(path), None)

    def save_texture_as(self, texture, parent=None):
        self._save_dialog(lambda path: texture.save_to_png(path), parent)

    def _save_dialog(self, writer, parent):
        dialog = Gtk.FileDialog(title='保存截图',
                                initial_name=os.path.basename(unique_path(screenshots_dir())),
                                initial_folder=Gio.File.new_for_path(screenshots_dir()))

        def done(dlg, result):
            try:
                file = dlg.save_finish(result)
            except GLib.Error:
                return
            path = file.get_path()
            writer(path)
            self._notify_saved(path)
        dialog.save(parent, None, done)

    def _notify_saved(self, path):
        note = Gio.Notification.new('截图已保存')
        note.set_body(os.path.basename(path))
        note.set_default_action_and_target_value('app.open-file', GLib.Variant('s', path))
        note.add_button('打开文件夹', 'app.open-folder')
        self.send_notification('glint-saved', note)

    def _open_file(self, _action, param):
        Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(param.get_string()).get_uri(), None)

    def open_folder(self):
        Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(screenshots_dir()).get_uri(), None)

    # ---- pins --------------------------------------------------------------

    def _display_scale(self):
        monitors = Gdk.Display.get_default().get_monitors()
        if monitors.get_n_items():
            return monitors.get_item(0).get_scale()
        return 1.0

    def pin_texture(self, texture, device_scale, position=None):
        if self.pins_hidden:
            self.toggle_pins()
        pin = PinWindow(self, texture, device_scale, position)
        self.pins.append(pin)
        pin.connect('destroy', lambda w: self.pins.remove(w) if w in self.pins else None)
        pin.present()

    def pin_image(self, path):
        try:
            texture = render.pixbuf_texture(GdkPixbuf.Pixbuf.new_from_file(path))
        except GLib.Error as e:
            print(f'glint: cannot pin {path}: {e.message}', flush=True)
            return
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        self.pin_texture(texture, self._display_scale())

    def pin_text(self, text):
        text = text.strip('\n')
        if not text.strip():
            self.pin_last()
            return
        self.pin_texture(text_card(text), 2.0)

    def pin_last(self):
        if self.last:
            self.pin_texture(*self.last)

    def toggle_pins(self):
        self.pins_hidden = not self.pins_hidden and bool(self.pins)
        for pin in self.pins:
            pin.set_visible(not self.pins_hidden)


def _card_layout(cr, text, desc, size, max_w):
    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(desc)
    layout.set_width(max_w * Pango.SCALE)
    layout.set_wrap(Pango.WrapMode.WORD_CHAR)
    layout.set_spacing(int(size * 0.35 * Pango.SCALE))
    layout.set_text(text[:6000], -1)
    return layout


def text_card(text, scale=2.0):
    """Render clipboard text as a paper card, the way Snipaste pins text."""
    looks_like_code = any(line.startswith(('    ', '\t')) for line in text.splitlines()) or \
        any(ch in text for ch in '{};=<>')
    family = theme.MONO if looks_like_code else theme.TEXT_FAMILY
    size = 13 if looks_like_code else 15
    pad, max_w = 18, 560
    desc = Pango.FontDescription.from_string(family)
    desc.set_absolute_size(size * Pango.SCALE)

    probe = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))
    _ink, logical = _card_layout(probe, text, desc, size, max_w).get_pixel_extents()
    w = logical.width + 2 * pad
    h = min(logical.height, 1400) + 2 * pad

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, math.ceil(w * scale), math.ceil(h * scale))
    cr = cairo.Context(surface)
    cr.scale(scale, scale)
    cr.set_source_rgb(0.984, 0.980, 0.961)
    cr.paint()
    cr.set_source_rgb(*theme.hex_to_rgb('#16171A'))
    cr.rectangle(0, 0, 3, h)
    cr.fill()
    cr.rectangle(pad, pad, w - 2 * pad, h - 2 * pad)
    cr.clip()
    cr.move_to(pad, pad)
    layout = _card_layout(cr, text, desc, size, max_w)
    PangoCairo.show_layout(cr, layout)
    return render.surface_texture(surface)


def run(argv):
    return GlintApp().run(argv)
