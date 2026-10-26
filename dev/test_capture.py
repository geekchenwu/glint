#!/usr/bin/python3
"""Lossless capture transport and clipboard format regressions.

Run with: PYTHONPATH=app python3 -m unittest discover -s dev -p 'test_*.py'
"""
import os
import tempfile
import unittest

import gi

gi.require_version('Gdk', '4.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf, GLib

from glint_app.render import Capture


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = os.path.join(self.temp.name, 'capture.tiles')

    def write_capture(self, alpha):
        width, height = 1031, 1027
        channels = 4 if alpha else 3
        pixels = bytes((x * 17 + y * 31 + channel * 47) % 256
                       for y in range(height) for x in range(width) for channel in range(channels))
        original = GdkPixbuf.Pixbuf.new_from_bytes(
            GLib.Bytes.new(pixels), GdkPixbuf.Colorspace.RGB, alpha, 8,
            width, height, width * channels)
        meta = {'version': 2, 'image': {'width': width, 'height': height},
                'stage': {'width': width / 2, 'height': height / 2}, 'tiles': []}
        with open(self.path, 'wb') as output:
            for y in range(0, height, 1024):
                for x in range(0, width, 1024):
                    w, h = min(1024, width - x), min(1024, height - y)
                    tile = original.new_subpixbuf(x, y, w, h)
                    ok, data = tile.save_to_bufferv('png', [], [])
                    self.assertTrue(ok)
                    output.write(data)
                    meta['tiles'].append({'x': x, 'y': y, 'width': w, 'height': h, 'length': len(data)})
        return original, meta

    def test_lossless_tiles_with_partial_edges_and_alpha(self):
        for alpha in (False, True):
            with self.subTest(alpha=alpha):
                original, meta = self.write_capture(alpha)
                capture = Capture(self.path, meta)
                expected = original if alpha else original.add_alpha(False, 0, 0, 0)
                self.assertEqual(capture.pixels, expected.get_pixels())
                self.assertEqual(capture.f, 2)
                self.assertEqual(capture.color_at(1024, 1024), tuple(expected.get_pixels()[
                    1024 * expected.get_rowstride() + 1024 * 4:
                    1024 * expected.get_rowstride() + 1024 * 4 + 3]))

    def test_existing_png_capture(self):
        original, _meta = self.write_capture(False)
        original.savev(self.path, 'png', [], [])
        capture = Capture(self.path, {})
        self.assertEqual(capture.pixels, original.get_pixels())

    def test_reject_truncated_tile(self):
        _original, meta = self.write_capture(True)
        with open(self.path, 'r+b') as output:
            output.truncate(os.path.getsize(self.path) - 1)
        with self.assertRaisesRegex(ValueError, 'truncated'):
            Capture(self.path, meta)

    def test_reject_invalid_tile_bounds(self):
        _original, meta = self.write_capture(False)
        meta['tiles'][0]['x'] = meta['image']['width']
        with self.assertRaisesRegex(ValueError, 'invalid'):
            Capture(self.path, meta)

    def test_reject_missing_row(self):
        _original, meta = self.write_capture(False)
        meta['tiles'] = meta['tiles'][:2]
        with open(self.path, 'r+b') as output:
            output.truncate(sum(tile['length'] for tile in meta['tiles']))
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            Capture(self.path, meta)

    def test_reject_overlapping_tiles(self):
        _original, meta = self.write_capture(False)
        meta['tiles'][1]['x'] = 0
        with self.assertRaisesRegex(ValueError, 'row order'):
            Capture(self.path, meta)


if __name__ == '__main__':
    unittest.main()
