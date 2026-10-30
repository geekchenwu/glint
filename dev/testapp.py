#!/usr/bin/python3
"""A small GTK4 window with a menu, used as capture subject in headless tests."""
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gio, Gtk


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id='io.github.lumen.TestSubject')

    def do_activate(self):
        win = Gtk.ApplicationWindow(application=self, title='Release notes', default_width=760,
                                    default_height=520)
        header = Gtk.HeaderBar()
        menu = Gio.Menu()
        for label in ('New window', 'Export as PDF…', 'Preferences', 'Keyboard shortcuts', 'About'):
            menu.append(label, 'app.noop')
        self.add_action(Gio.SimpleAction.new('noop', None))
        button = Gtk.MenuButton(icon_name='open-menu-symbolic', menu_model=menu)
        button.connect('notify::active', lambda b, _p: print('menu active', b.get_active(), flush=True))
        header.pack_end(button)
        win.set_titlebar(header)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_top=28,
                      margin_start=36, margin_end=36)
        title = Gtk.Label(xalign=0)
        title.set_markup('<span size="xx-large" weight="bold">Version 2.4 — what changed</span>')
        box.append(title)
        for line in ('Faster cold start on large projects', 'New colour picker with HEX and RGB',
                     'Pinned images stay on top across workspaces', 'Fixed a crash when the clipboard was empty'):
            row = Gtk.Label(label='•  ' + line, xalign=0)
            box.append(row)
        bar = Gtk.Box(spacing=10)
        for name, css in (('Update now', 'suggested-action'), ('Later', ''), ('Delete', 'destructive-action')):
            b = Gtk.Button(label=name)
            if css:
                b.add_css_class(css)
            b.connect('clicked', lambda _b, n=name: print('clicked', n, flush=True))
            bar.append(b)
        box.append(bar)
        win.set_child(box)
        win.present()


App().run(None)
