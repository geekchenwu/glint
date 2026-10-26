import math

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gtk  # noqa: E402

from . import theme  # noqa: E402
from .icons import glyph_button  # noqa: E402

TOOLS = [
    ('rect', '矩形', 'R'),
    ('ellipse', '椭圆', 'E'),
    ('arrow', '箭头', 'A'),
    ('line', '直线', 'L'),
    ('pen', '画笔', 'P'),
    ('marker', '荧光笔', 'M'),
    ('text', '文字', 'T'),
    ('mosaic', '马赛克', 'X'),
    ('step', '序号', 'N'),
]
TOOL_KEYS = {key.lower(): name for name, _label, key in TOOLS}


def size_family(tool):
    return {'marker': 'marker', 'text': 'text', 'mosaic': 'mosaic', 'step': 'step'}.get(tool, 'stroke')


def _sep():
    sep = Gtk.Box(css_classes=['sep'])
    return sep


class Toolbar(Gtk.Box):
    def __init__(self, session):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, css_classes=['glint-bar'],
                         halign=Gtk.Align.START, valign=Gtk.Align.START, visible=False)
        self.session = session
        self._syncing = False
        self.tool_buttons = {}
        for name, label, key in TOOLS:
            button = glyph_button(Gtk.ToggleButton, name, f'{label}    {key}')
            button.connect('toggled', self._on_tool_toggled, name)
            self.tool_buttons[name] = button
            self.append(button)

        self.sep = _sep()
        self.append(self.sep)
        self.undo = self._action('undo', '撤销    Ctrl+Z', session.undo)
        self.redo = self._action('redo', '重做    Ctrl+Y', session.redo)
        self.append(_sep())
        self._action('pin', '贴到屏幕    Ctrl+T', lambda: session.finish('pin'))
        self._action('save', '保存    Ctrl+S', lambda: session.finish('save'))
        self._action('close', '退出    Esc', session.cancel, css=('danger',))
        self._action('check', '复制到剪贴板    Enter', lambda: session.finish('copy'), css=('primary',))

    def _action(self, glyph, tooltip, callback, css=()):
        button = glyph_button(Gtk.Button, glyph, tooltip, css)
        button.connect('clicked', lambda *_: callback())
        self.append(button)
        return button

    def _on_tool_toggled(self, button, name):
        if self._syncing:
            return
        self.session.set_tool(name if button.get_active() else None)

    def sync(self):
        self._syncing = True
        for name, button in self.tool_buttons.items():
            button.set_active(self.session.tool == name)
        self._syncing = False
        self.undo.set_sensitive(bool(self.session.anns))
        self.redo.set_sensitive(bool(self.session.redo_stack))


class _Swatch(Gtk.DrawingArea):
    def __init__(self, button, color):
        super().__init__(content_width=20, content_height=20, can_target=False)
        self.button = button
        self.color = color
        self.set_draw_func(self._draw)

    def _draw(self, _a, cr, w, h):
        r, g, b = theme.hex_to_rgb(self.color)
        cx, cy = w / 2, h / 2
        if self.button.get_active():
            cr.arc(cx, cy, 9, 0, 2 * math.pi)
            cr.set_source_rgba(1, 1, 1, 0.95)
            cr.set_line_width(1.6)
            cr.stroke()
        cr.arc(cx, cy, 6.2, 0, 2 * math.pi)
        cr.set_source_rgb(r, g, b)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.22)
        cr.set_line_width(1)
        cr.stroke()


class _SizeDot(Gtk.DrawingArea):
    def __init__(self, radius):
        super().__init__(content_width=20, content_height=20, can_target=False)
        self.radius = radius
        self.set_draw_func(self._draw)

    def _draw(self, _a, cr, w, h):
        color = self.get_color()
        cr.arc(w / 2, h / 2, self.radius, 0, 2 * math.pi)
        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        cr.fill()


class PropsBar(Gtk.Box):
    def __init__(self, session):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL,
                         css_classes=['glint-bar', 'glint-props'],
                         halign=Gtk.Align.START, valign=Gtk.Align.START, visible=False)
        self.session = session
        self._syncing = False
        self.size_buttons = []
        for i, (radius, label) in enumerate(zip((2.5, 4.2, 6.2), ('细', '中', '粗'))):
            button = Gtk.ToggleButton(tooltip_text=label, focus_on_click=False, can_focus=False)
            dot = _SizeDot(radius)
            button.set_child(dot)
            button.connect('state-flags-changed', lambda *_a, d=dot: d.queue_draw())
            button.connect('toggled', self._on_size, i)
            self.size_buttons.append(button)
            self.append(button)
        self.sep = _sep()
        self.append(self.sep)
        self.color_buttons = {}
        for color, name in zip(theme.PALETTE, theme.COLOR_NAMES):
            button = Gtk.ToggleButton(tooltip_text=name, focus_on_click=False, can_focus=False)
            swatch = _Swatch(button, color)
            button.set_child(swatch)
            button.connect('toggled', self._on_color, color)
            button.connect('state-flags-changed', lambda *_a, s=swatch: s.queue_draw())
            self.color_buttons[color] = button
            self.append(button)

    def _on_size(self, button, index):
        if self._syncing:
            return
        if button.get_active():
            self.session.set_size_index(index)
        self.session.sync_chrome()

    def _on_color(self, button, color):
        if self._syncing:
            return
        if button.get_active():
            self.session.set_color(color)
        self.session.sync_chrome()

    def sync(self):
        self._syncing = True
        index = self.session.size_index()
        for i, button in enumerate(self.size_buttons):
            button.set_active(i == index)
        for color, button in self.color_buttons.items():
            button.set_active(color == self.session.color)
            button.get_child().queue_draw()
        # Mosaic has no colour.
        mosaic = self.session.tool == 'mosaic'
        self.sep.set_visible(not mosaic)
        for button in self.color_buttons.values():
            button.set_visible(not mosaic)
        self._syncing = False
