"""Captured image access and annotation rendering.

All annotation geometry lives in image pixels, so the same drawing code feeds
the on-screen preview cache and the exported PNG (what you see is what you
copy).
"""
import math
from concurrent.futures import ThreadPoolExecutor

import cairo
import gi

gi.require_version('Gdk', '4.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gdk, GdkPixbuf, GLib, Pango, PangoCairo  # noqa: E402

from . import theme  # noqa: E402


class Capture:
    """A captured frame. With path None only its geometry is known yet; the
    shell is still encoding the pixels and set_pixbuf() adds them later."""

    def __init__(self, path, meta):
        self.meta = meta
        self.pixbuf = self.texture = None
        if path is None:
            self.width, self.height = meta['image']['width'], meta['image']['height']
        elif meta.get('tiles'):
            image = meta['image']
            self.set_pixbuf(load_tiles(path, image['width'], image['height'], meta['tiles']))
        else:
            self.set_pixbuf(GdkPixbuf.Pixbuf.new_from_file(path))
        stage = meta.get('stage') or {'width': self.width, 'height': self.height}
        # Image pixels per stage (logical) pixel.
        self.f = self.width / stage['width']

    def set_pixbuf(self, pixbuf):
        self.pixbuf = pixbuf
        self.width = pixbuf.get_width()
        self.height = pixbuf.get_height()
        self.channels = pixbuf.get_n_channels()
        self.rowstride = pixbuf.get_rowstride()
        self.texture = pixbuf_texture(pixbuf)
        self._pixels = None

    @property
    def pixels(self):
        if self._pixels is None:
            self._pixels = self.pixbuf.get_pixels()
        return self._pixels

    def color_at(self, x, y):
        x = min(max(int(x), 0), self.width - 1)
        y = min(max(int(y), 0), self.height - 1)
        o = y * self.rowstride + x * self.channels
        p = self.pixels
        return p[o], p[o + 1], p[o + 2]

    def loupe_texture(self, cx, cy, n):
        """n x n image pixels centred on (cx, cy), padded black off-image."""
        half = n // 2
        dest = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, self.pixbuf.get_has_alpha(), 8, n, n)
        dest.fill(0x000000ff)
        x0, y0 = int(cx) - half, int(cy) - half
        sx, sy = max(x0, 0), max(y0, 0)
        ex, ey = min(x0 + n, self.width), min(y0 + n, self.height)
        if ex > sx and ey > sy:
            self.pixbuf.copy_area(sx, sy, ex - sx, ey - sy, dest, sx - x0, sy - y0)
        return pixbuf_texture(dest)


def load_tiles(path, width, height, tiles):
    """Reassemble the shell's lossless PNG tiles into one RGBA pixbuf."""
    if width <= 0 or height <= 0:
        raise ValueError('invalid capture dimensions')
    with open(path, 'rb') as source:
        data = source.read()
    chunks = []
    offset, next_x, next_y, row_height = 0, 0, 0, 0
    for tile in tiles:
        x, y, w, h, length = (tile[k] for k in ('x', 'y', 'width', 'height', 'length'))
        if not (0 <= x < width and 0 <= y < height and
                0 < w <= 1024 and 0 < h <= 1024 and
                x + w <= width and y + h <= height and 0 < length <= 4 * w * h + 65536):
            raise ValueError('invalid capture tile')
        if x != next_x or y != next_y or (x and h != row_height):
            raise ValueError('capture tiles must cover the image in row order')
        if x == 0:
            row_height = h
        if offset + length > len(data):
            raise ValueError('truncated capture tile')
        chunks.append((x, y, w, h, data[offset:offset + length]))
        offset += length
        next_x = x + w
        if next_x == width:
            next_x, next_y = 0, y + h
    if offset != len(data):
        raise ValueError('unexpected capture data')
    if next_x or next_y != height:
        raise ValueError('incomplete capture tiles')

    # The tiles cover every pixel, and copying RGB into RGBA sets alpha.
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, width, height)

    def decode(chunk):
        x, y, w, h, png = chunk
        loader = GdkPixbuf.PixbufLoader.new_with_type('png')
        loader.write(png)
        loader.close()
        image = loader.get_pixbuf()
        if image.get_width() != w or image.get_height() != h:
            raise ValueError('capture tile dimensions do not match')
        image.copy_area(0, 0, w, h, pixbuf, x, y)

    # Decoding releases the GIL; side by side a 4K frame takes ~45 ms
    # instead of ~190 ms.
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(decode, chunks))
    return pixbuf


def pixbuf_texture(pixbuf):
    fmt = Gdk.MemoryFormat.R8G8B8A8 if pixbuf.get_has_alpha() else Gdk.MemoryFormat.R8G8B8
    return Gdk.MemoryTexture.new(pixbuf.get_width(), pixbuf.get_height(), fmt,
                                 pixbuf.read_pixel_bytes(), pixbuf.get_rowstride())


def surface_texture(surface):
    surface.flush()
    data = GLib.Bytes.new(bytes(surface.get_data()))
    return Gdk.MemoryTexture.new(surface.get_width(), surface.get_height(),
                                 Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
                                 data, surface.get_stride())


def norm_rect(x0, y0, x1, y1):
    return min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)


def _rrect(cr, x, y, w, h, r):
    r = max(0.0, min(r, w / 2, h / 2))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.close_path()


def _source(cr, spec, alpha=1.0):
    r, g, b = theme.hex_to_rgb(spec)
    cr.set_source_rgba(r, g, b, alpha)


def _smooth_path(cr, pts):
    cr.move_to(*pts[0])
    if len(pts) == 1:
        cr.line_to(pts[0][0] + 0.01, pts[0][1])
        return
    if len(pts) == 2:
        cr.line_to(*pts[1])
        return
    # Quadratic B-spline through the midpoints keeps freehand strokes soft.
    for i in range(1, len(pts) - 1):
        (x0, y0), (x1, y1) = pts[i], pts[i + 1]
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        cx, cy = cr.get_current_point()
        cr.curve_to(cx + 2 / 3 * (x0 - cx), cy + 2 / 3 * (y0 - cy),
                    mx + 2 / 3 * (x0 - mx), my + 2 / 3 * (y0 - my), mx, my)
    cr.line_to(*pts[-1])


def arrow_outline(x0, y0, x1, y1, w):
    """A tapered arrow: thin tail, full-width shaft, swept-back head."""
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length < 1:
        return None
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux
    head = min(length * 0.7, max(w * 4.6, 12.0))
    head_half = head * 0.52
    back = head * 0.18
    tail_half, shaft_half = w * 0.18, w * 0.62
    bx, by = x1 - ux * head, y1 - uy * head
    wx, wy = bx - ux * back, by - uy * back
    return [
        (x0 + nx * tail_half, y0 + ny * tail_half),
        (bx + nx * shaft_half, by + ny * shaft_half),
        (wx + nx * head_half, wy + ny * head_half),
        (x1, y1),
        (wx - nx * head_half, wy - ny * head_half),
        (bx - nx * shaft_half, by - ny * shaft_half),
        (x0 - nx * tail_half, y0 - ny * tail_half),
    ]


def text_layout(cr, a):
    layout = PangoCairo.create_layout(cr)
    desc = Pango.FontDescription.from_string(theme.TEXT_FAMILY)
    desc.set_weight(Pango.Weight.SEMIBOLD)
    desc.set_absolute_size(a['size'] * Pango.SCALE)
    layout.set_font_description(desc)
    layout.set_text(a['text'], -1)
    return layout


def draw_annotation(cr, a, cap):
    kind = a['kind']
    cr.save()
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    w = a.get('width', 4)

    if kind in ('rect', 'ellipse'):
        x, y, rw, rh = norm_rect(a['x0'], a['y0'], a['x1'], a['y1'])
        if kind == 'rect':
            _rrect(cr, x, y, rw, rh, w * 0.9)
        elif rw > 0 and rh > 0:
            cr.save()
            cr.translate(x + rw / 2, y + rh / 2)
            cr.scale(rw / 2, rh / 2)
            cr.arc(0, 0, 1, 0, 2 * math.pi)
            cr.restore()
        _source(cr, a['color'])
        cr.set_line_width(w)
        cr.stroke()

    elif kind == 'line':
        cr.move_to(a['x0'], a['y0'])
        cr.line_to(a['x1'], a['y1'])
        _source(cr, a['color'])
        cr.set_line_width(w)
        cr.stroke()

    elif kind == 'arrow':
        pts = arrow_outline(a['x0'], a['y0'], a['x1'], a['y1'], w)
        if pts:
            cr.move_to(*pts[0])
            for p in pts[1:]:
                cr.line_to(*p)
            cr.close_path()
            _source(cr, a['color'])
            cr.set_line_width(max(1.0, w * 0.3))
            cr.fill_preserve()
            cr.stroke()

    elif kind == 'pen':
        _smooth_path(cr, a['points'])
        _source(cr, a['color'])
        cr.set_line_width(w)
        cr.stroke()

    elif kind == 'marker':
        _smooth_path(cr, a['points'])
        cr.set_line_cap(cairo.LINE_CAP_SQUARE)
        cr.set_line_width(w)
        # Multiply keeps text under a coloured highlight crisp and dark;
        # white and ink have no meaningful multiply, so they go translucent,
        # as does everything until the pixels to multiply with have arrived.
        if a['color'] in ('#FFFFFF', '#16171A') or cap.pixbuf is None:
            _source(cr, a['color'], 0.42)
        else:
            cr.set_operator(cairo.OPERATOR_MULTIPLY)
            _source(cr, a['color'], 0.85)
        cr.stroke()

    elif kind == 'mosaic':
        x, y, rw, rh = (int(round(v)) for v in norm_rect(a['x0'], a['y0'], a['x1'], a['y1']))
        x, y = max(x, 0), max(y, 0)
        rw, rh = min(rw, cap.width - x), min(rh, cap.height - y)
        if rw > 1 and rh > 1 and cap.pixbuf is not None:
            block = max(2, int(a['block']))
            sub = cap.pixbuf.new_subpixbuf(x, y, rw, rh)
            small = sub.scale_simple(max(1, rw // block), max(1, rh // block),
                                     GdkPixbuf.InterpType.BILINEAR)
            big = small.scale_simple(rw, rh, GdkPixbuf.InterpType.NEAREST)
            Gdk.cairo_set_source_pixbuf(cr, big, x, y)
            cr.rectangle(x, y, rw, rh)
            cr.fill()

    elif kind == 'text':
        layout = text_layout(cr, a)
        cr.move_to(a['x'], a['y'])
        PangoCairo.layout_path(cr, layout)
        halo = (0, 0, 0, 0.55) if theme.luminance(a['color']) > 0.55 else (1, 1, 1, 0.9)
        cr.set_source_rgba(*halo)
        cr.set_line_width(max(2.0, a['size'] * 0.16))
        cr.stroke_preserve()
        _source(cr, a['color'])
        cr.fill()

    elif kind == 'step':
        r = a['radius']
        cx, cy = a['x'], a['y']
        cr.arc(cx, cy + r * 0.12, r * 1.06, 0, 2 * math.pi)
        cr.set_source_rgba(0, 0, 0, 0.28)
        cr.fill()
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        _source(cr, a['color'])
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.95)
        cr.set_line_width(max(1.5, r * 0.14))
        cr.stroke()
        layout = PangoCairo.create_layout(cr)
        desc = Pango.FontDescription.from_string(theme.MONO)
        desc.set_weight(Pango.Weight.BOLD)
        desc.set_absolute_size(r * 1.12 * Pango.SCALE)
        layout.set_font_description(desc)
        layout.set_text(str(a['n']), -1)
        ink, _logical = layout.get_pixel_extents()
        cr.move_to(cx - ink.x - ink.width / 2, cy - ink.y - ink.height / 2)
        if theme.luminance(a['color']) > 0.6:
            cr.set_source_rgb(*theme.hex_to_rgb(theme.INK))
        else:
            cr.set_source_rgb(1, 1, 1)
        PangoCairo.show_layout(cr, layout)

    cr.restore()


def annotation_bounds(a):
    """Loose bounding box in image pixels, used to limit live redraws."""
    pad = a.get('width', 4) * 2 + 8
    kind = a['kind']
    if kind in ('pen', 'marker'):
        xs = [p[0] for p in a['points']]
        ys = [p[1] for p in a['points']]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    elif kind == 'step':
        r = a['radius'] * 1.4
        x0, y0, x1, y1 = a['x'] - r, a['y'] - r, a['x'] + r, a['y'] + r
    elif kind == 'text':
        lines = a['text'].split('\n')
        x0, y0 = a['x'], a['y']
        x1 = x0 + a['size'] * max(1, max(len(line) for line in lines))
        y1 = y0 + a['size'] * (1.6 * len(lines) + 0.5)
    else:
        x0, y0 = min(a['x0'], a['x1']), min(a['y0'], a['y1'])
        x1, y1 = max(a['x0'], a['x1']), max(a['y0'], a['y1'])
        if kind == 'arrow':
            pad += a.get('width', 4) * 4
    return x0 - pad, y0 - pad, x1 - x0 + 2 * pad, y1 - y0 + 2 * pad


def render_region(cap, rect, anns):
    x, y, w, h = rect
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surface)
    cr.translate(-x, -y)
    # Without pixels yet the annotations go on a clear layer over the
    # shell's frozen frame.
    if cap.pixbuf is not None:
        Gdk.cairo_set_source_pixbuf(cr, cap.pixbuf.new_subpixbuf(x, y, w, h), x, y)
        cr.paint()
    cr.rectangle(x, y, w, h)
    cr.clip()
    for a in anns:
        draw_annotation(cr, a, cap)
    return surface
