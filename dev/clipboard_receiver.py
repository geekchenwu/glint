#!/usr/bin/python3
"""A separate Wayland client that records real Ctrl+V paste results as JSON."""
import json
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Gdk, GLib, Gtk


class Receiver(Gtk.Application):
    def __init__(self, result_path):
        super().__init__(application_id='io.github.glint.ClipboardReceiver')
        self.result_path = result_path

    def do_activate(self):
        window = Gtk.ApplicationWindow(application=self, title='Clipboard receiver')
        window.set_default_size(760, 520)
        window.set_child(Gtk.Label(label='Ctrl+V: read the image from another application'))
        keys = Gtk.EventControllerKey()
        keys.connect('key-pressed', self.on_key)
        window.add_controller(keys)
        window.present()

    def on_key(self, _controller, keyval, _keycode, state):
        if Gdk.keyval_to_lower(keyval) != Gdk.KEY_v or not state & Gdk.ModifierType.CONTROL_MASK:
            return False
        clipboard = Gdk.Display.get_default().get_clipboard()
        result = {'formats': list(clipboard.get_formats().get_mime_types())}

        def received(clip, task):
            try:
                texture = clip.read_texture_finish(task)
                result.update(width=texture.get_width(), height=texture.get_height())
                texture.save_to_png(self.result_path + '.png')
            except GLib.Error as error:
                result['error'] = error.message
            with open(self.result_path, 'w') as output:
                json.dump(result, output)

        clipboard.read_texture_async(None, received)
        return True


Receiver(sys.argv[1]).run([sys.argv[0]])
