"""Pinned images: frameless, always on top (via the shell extension)."""
import gi

gi.require_version('Gdk', '4.0')
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

ZOOM_STEPS = (0.25, 0.33, 0.5, 0.67, 0.75, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0)


class PinWindow(Gtk.Window):
    def __init__(self, app, texture, device_scale, position=None):
        title = 'lumen-pin' if position is None else \
            f'lumen-pin@{int(round(position[0]))},{int(round(position[1]))}'
        super().__init__(application=app, title=title, resizable=False)
        self.app = app
        self.texture = texture
        self.base_w = texture.get_width() / device_scale
        self.base_h = texture.get_height() / device_scale
        self.zoom = 1.0
        self.add_css_class('lumen-pin')
        # An empty custom titlebar keeps client-side decorations (rounded
        # corners, theme shadow, resize-free frame) without any visible chrome.
        self.set_titlebar(Gtk.Box())

        self.picture = Gtk.Picture(paintable=texture, can_shrink=True,
                                   content_fit=Gtk.ContentFit.FILL)
        self.osd = Gtk.Label(css_classes=['lumen-osd'], halign=Gtk.Align.END,
                             valign=Gtk.Align.END, visible=False, can_target=False)
        overlay = Gtk.Overlay(child=self.picture)
        overlay.add_overlay(self.osd)
        self.set_child(overlay)
        self._osd_timeout = 0
        self._apply_zoom()

        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect('drag-update', self._drag_update)
        self.add_controller(drag)
        click = Gtk.GestureClick(button=0)
        click.connect('pressed', self._clicked)
        self.add_controller(click)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect('scroll', self._scroll)
        self.add_controller(scroll)
        keys = Gtk.EventControllerKey()
        keys.connect('key-pressed', self._key)
        self.add_controller(keys)

        group = Gio.SimpleActionGroup()
        for name, callback in (('copy', self.copy), ('save', self.save), ('reset', self.reset_zoom),
                               ('close', self.close)):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', lambda *_a, cb=callback: cb())
            group.add_action(action)
        self.insert_action_group('pin', group)
        menu = Gio.Menu()
        for label, action, accel in (('复制', 'pin.copy', 'Ctrl+C'), ('另存为…', 'pin.save', 'Ctrl+S'),
                                     ('原始大小', 'pin.reset', '1'), ('关闭', 'pin.close', 'Esc')):
            item = Gio.MenuItem.new(label, action)
            item.set_attribute_value('accel', GLib.Variant('s', accel))
            menu.append_item(item)
        self.menu = Gtk.PopoverMenu(menu_model=menu, has_arrow=False, halign=Gtk.Align.START)
        self.menu.add_css_class('lumen-menu')
        self.menu.set_parent(overlay)
        self.connect('destroy', lambda *_: self.menu.unparent())

    def _apply_zoom(self):
        w = max(16, int(round(self.base_w * self.zoom)))
        h = max(16, int(round(self.base_h * self.zoom)))
        self.picture.set_size_request(w, h)
        self.set_default_size(w, h)

    def _show_osd(self, text):
        self.osd.set_label(text)
        self.osd.set_visible(True)
        if self._osd_timeout:
            GLib.source_remove(self._osd_timeout)

        def hide():
            self._osd_timeout = 0
            self.osd.set_visible(False)
            return GLib.SOURCE_REMOVE
        self._osd_timeout = GLib.timeout_add(900, hide)

    def _drag_update(self, gesture, dx, dy):
        if abs(dx) + abs(dy) < 3:
            return
        ok, x, y = gesture.get_start_point()
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        gesture.reset()
        surface = self.get_surface()
        if ok and surface is not None:
            device = gesture.get_device()
            surface.begin_move(device, Gdk.BUTTON_PRIMARY, x, y, Gdk.CURRENT_TIME)

    def _clicked(self, gesture, n_press, x, y):
        button = gesture.get_current_button()
        if button == Gdk.BUTTON_PRIMARY and n_press == 2:
            self.close()
        elif button == Gdk.BUTTON_SECONDARY:
            rect = Gdk.Rectangle()
            rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
            self.menu.set_pointing_to(rect)
            self.menu.popup()

    def _scroll(self, ctrl, _dx, dy):
        state = ctrl.get_current_event_state()
        if state & Gdk.ModifierType.CONTROL_MASK:
            opacity = min(1.0, max(0.2, self.get_opacity() + (0.1 if dy < 0 else -0.1)))
            self.set_opacity(opacity)
            self._show_osd(f'{round(opacity * 100)}%  不透明')
            return True
        idx = min(range(len(ZOOM_STEPS)), key=lambda i: abs(ZOOM_STEPS[i] - self.zoom))
        idx = min(len(ZOOM_STEPS) - 1, max(0, idx + (1 if dy < 0 else -1)))
        self.zoom = ZOOM_STEPS[idx]
        self._apply_zoom()
        self._show_osd(f'{round(self.zoom * 100)}%')
        return True

    def _key(self, _ctrl, keyval, _code, state):
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        name = (Gdk.keyval_name(Gdk.keyval_to_lower(keyval)) or '').lower()
        if name == 'escape' or (ctrl and name == 'w'):
            self.close()
        elif ctrl and name == 'c':
            self.copy()
        elif ctrl and name == 's':
            self.save()
        elif name in ('1', 'kp_1'):
            self.reset_zoom()
        else:
            return False
        return True

    def reset_zoom(self):
        self.zoom = 1.0
        self._apply_zoom()
        self._show_osd('100%')

    def copy(self):
        self.app.copy_texture(self.texture)
        self._show_osd('已复制')

    def save(self):
        self.app.save_texture_as(self.texture, parent=self)
