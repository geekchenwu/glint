"""Hand-drawn toolbar glyphs on a 20x20 grid, painted with the widget colour.

Drawing them with cairo keeps stroke weight, caps and optical size identical
across tools, which icon-theme symbolics cannot guarantee.
"""
import math

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gtk  # noqa: E402


def _poly(cr, pts, close=False):
    cr.move_to(*pts[0])
    for p in pts[1:]:
        cr.line_to(*p)
    if close:
        cr.close_path()


def _rrect(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.close_path()


def _rect(cr):
    _rrect(cr, 3.5, 5, 13, 10, 2.2)
    cr.stroke()


def _ellipse(cr):
    cr.save()
    cr.translate(10, 10)
    cr.scale(7, 5.4)
    cr.arc(0, 0, 1, 0, 2 * math.pi)
    cr.restore()
    cr.stroke()


def _arrow(cr):
    _poly(cr, [(4.5, 15.5), (14.5, 5.5)])
    cr.stroke()
    _poly(cr, [(8.2, 5.2), (14.8, 5.2), (14.8, 11.8)])
    cr.stroke()


def _line(cr):
    _poly(cr, [(5, 15), (15, 5)])
    cr.stroke()
    for x, y in ((5, 15), (15, 5)):
        cr.arc(x, y, 1.9, 0, 2 * math.pi)
        cr.fill()


def _pen(cr):
    _poly(cr, [(13.2, 3.8), (16.2, 6.8), (8, 15), (4.2, 15.8), (5, 12)], close=True)
    cr.stroke()
    _poly(cr, [(11.4, 5.6), (14.4, 8.6)])
    cr.stroke()


def _marker(cr):
    _poly(cr, [(12, 3.6), (16.4, 8), (10.6, 13.8), (6.2, 13.8), (6.2, 9.4)], close=True)
    cr.stroke()
    cr.save()
    cr.set_line_width(2.6)
    _poly(cr, [(3.5, 17), (16.5, 17)])
    cr.stroke()
    cr.restore()


def _text(cr):
    _poly(cr, [(4.5, 6), (4.5, 4.5), (15.5, 4.5), (15.5, 6)])
    cr.stroke()
    _poly(cr, [(10, 4.5), (10, 15.5)])
    cr.stroke()
    _poly(cr, [(7.5, 15.5), (12.5, 15.5)])
    cr.stroke()


def _mosaic(cr):
    for i in range(3):
        for j in range(3):
            x, y = 4 + i * 4.2, 4 + j * 4.2
            if (i + j) % 2 == 0:
                cr.rectangle(x, y, 3.6, 3.6)
                cr.fill()
    _rrect(cr, 3.2, 3.2, 13.6, 13.6, 2)
    cr.save()
    cr.set_line_width(1.2)
    cr.stroke()
    cr.restore()


def _step(cr):
    cr.arc(10, 10, 7, 0, 2 * math.pi)
    cr.stroke()
    _poly(cr, [(8.6, 8), (10.4, 6.6), (10.4, 13.6)])
    cr.stroke()


def _undo(cr):
    cr.arc_negative(10.5, 11, 5.5, 0.15 * math.pi, -1.0 * math.pi + 0.05)
    cr.stroke()
    _poly(cr, [(2.6, 8.4), (5.1, 11.4), (8.2, 9.1)])
    cr.stroke()


def _redo(cr):
    cr.save()
    cr.translate(20, 0)
    cr.scale(-1, 1)
    _undo(cr)
    cr.restore()


def _pin(cr):
    _poly(cr, [(7, 3.8), (13, 3.8)])
    cr.stroke()
    _poly(cr, [(8.2, 3.8), (8.2, 8.6), (5.6, 11.4), (14.4, 11.4), (11.8, 8.6), (11.8, 3.8)])
    cr.stroke()
    _poly(cr, [(10, 11.4), (10, 16.8)])
    cr.stroke()


def _save(cr):
    _poly(cr, [(10, 3.8), (10, 12)])
    cr.stroke()
    _poly(cr, [(6.4, 8.6), (10, 12.2), (13.6, 8.6)])
    cr.stroke()
    _poly(cr, [(3.8, 12.5), (3.8, 16), (16.2, 16), (16.2, 12.5)])
    cr.stroke()


def _check(cr):
    cr.save()
    cr.set_line_width(2.2)
    _poly(cr, [(4.6, 10.4), (8.4, 14.2), (15.6, 6.4)])
    cr.stroke()
    cr.restore()


def _close(cr):
    _poly(cr, [(5.5, 5.5), (14.5, 14.5)])
    cr.stroke()
    _poly(cr, [(14.5, 5.5), (5.5, 14.5)])
    cr.stroke()


GLYPHS = {
    'rect': _rect, 'ellipse': _ellipse, 'arrow': _arrow, 'line': _line,
    'pen': _pen, 'marker': _marker, 'text': _text, 'mosaic': _mosaic,
    'step': _step, 'undo': _undo, 'redo': _redo, 'pin': _pin,
    'save': _save, 'check': _check, 'close': _close,
}


def paint(cr, name, size, color):
    cr.save()
    cr.scale(size / 20, size / 20)
    cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
    cr.set_line_width(1.6)
    cr.set_line_cap(1)   # ROUND
    cr.set_line_join(1)  # ROUND
    GLYPHS[name](cr)
    cr.restore()


class Glyph(Gtk.DrawingArea):
    """A toolbar glyph that follows its button's CSS colour."""

    def __init__(self, name, size=20):
        super().__init__(content_width=size, content_height=size,
                         halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, can_target=False)
        self._name = name
        self._size = size
        self.set_draw_func(self._draw)

    def _draw(self, _area, cr, _w, _h):
        paint(cr, self._name, self._size, self.get_color())


def glyph_button(cls, name, tooltip, css=()):
    button = cls(tooltip_text=tooltip, focus_on_click=False, can_focus=False)
    glyph = Glyph(name)
    button.set_child(glyph)
    for c in css:
        button.add_css_class(c)
    button.connect('state-flags-changed', lambda *_: glyph.queue_draw())
    return button
