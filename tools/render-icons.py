#!/usr/bin/python3
"""Render the app icon into hicolor PNG sizes, optionally with a preview sheet.

  render-icons.py OUT_DIR             -> OUT_DIR/<size>x<size>/apps/<id>.png
  render-icons.py OUT_DIR --preview PNG
Sizes up to 32 px come from the hinted small variant.
"""
import os
import sys

import cairo
import gi

gi.require_version('Rsvg', '2.0')
from gi.repository import Rsvg  # noqa: E402

APP_ID = 'io.github.glint.Glint'
HERE = os.path.dirname(os.path.abspath(__file__))
ICONS = os.path.join(HERE, '..', 'data', 'icons')
SIZES = (16, 24, 32, 48, 64, 128, 256, 512)


def handle(size):
    name = f'{APP_ID}-small.svg' if size <= 32 else f'{APP_ID}.svg'
    return Rsvg.Handle.new_from_file(os.path.join(ICONS, name))


def draw(cr, size, x=0, y=0):
    viewport = Rsvg.Rectangle()
    viewport.x, viewport.y, viewport.width, viewport.height = x, y, size, size
    handle(size).render_document(cr, viewport)


def render(size):
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    draw(cairo.Context(surface), size)
    return surface


def preview(path):
    shown = (16, 24, 32, 48, 64, 128, 256)
    pad, gap = 28, 26
    width = pad * 2 + sum(shown) + gap * (len(shown) - 1)
    row = max(shown) + pad * 2
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, row * 2)
    cr = cairo.Context(surface)
    for i, bg in enumerate(((0.13, 0.13, 0.15), (0.96, 0.96, 0.95))):
        cr.set_source_rgb(*bg)
        cr.rectangle(0, i * row, width, row)
        cr.fill()
        x = pad
        for size in shown:
            draw(cr, size, x, i * row + row - pad - size)
            x += size + gap
    surface.write_to_png(path)


def main(argv):
    out = argv[1]
    for size in SIZES:
        target = os.path.join(out, f'{size}x{size}', 'apps')
        os.makedirs(target, exist_ok=True)
        render(size).write_to_png(os.path.join(target, f'{APP_ID}.png'))
    if '--preview' in argv:
        preview(argv[argv.index('--preview') + 1])
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
