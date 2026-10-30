"""The capture overlay: region selection, window snapping, loupe, annotation.

Coordinates: everything the session stores is in image pixels ("ip"). Each
monitor gets its own fullscreen window whose canvas maps widget (logical)
pixels to ip through the capture's stage scale, so a selection can span
monitors without special cases.
"""
import math

import gi

gi.require_version('Gdk', '4.0')
gi.require_version('Gsk', '4.0')
gi.require_version('Gtk', '4.0')
gi.require_version('Graphene', '1.0')
gi.require_version('Pango', '1.0')
from gi.repository import Gdk, Gio, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402

from . import SHELL_IFACE, SHELL_PATH, render, theme  # noqa: E402
from .toolbar import TOOL_KEYS, PropsBar, Toolbar, size_family  # noqa: E402

HANDLE_CURSORS = {
    'nw': 'nwse-resize', 'n': 'ns-resize', 'ne': 'nesw-resize', 'e': 'ew-resize',
    'se': 'nwse-resize', 's': 'ns-resize', 'sw': 'nesw-resize', 'w': 'ew-resize',
    'move': 'move',
}
ARROWS = {'left': (-1, 0), 'right': (1, 0), 'up': (0, -1), 'down': (0, 1)}
WASD = {'a': 'left', 'd': 'right', 'w': 'up', 's': 'down'}
LOUPE_CELLS = 15
LOUPE_CELL = 8

ACCENT = theme.rgba(theme.ACCENT)
ACCENT_SOFT = theme.rgba(theme.ACCENT, 0.28)
DIM = theme.rgba(theme.DIM)
GLASS = theme.rgba(theme.GLASS)
WHITE = theme.rgba('#FFFFFF')
WHITE_DIM = theme.rgba('#FFFFFF', 0.55)
HALO = theme.rgba('#000000', 0.38)
SHADOW = theme.rgba('#000000', 0.38)
GRID = theme.rgba('#000000', 0.14)


def R(x, y, w, h):
    r = Graphene.Rect()
    r.init(x, y, w, h)
    return r


def RR(x, y, w, h, radius):
    # init_from_rect()'s return value is a garbage copy under PyGObject;
    # only the instance it was called on is initialised.
    rr = Gsk.RoundedRect()
    rr.init_from_rect(R(x, y, w, h), radius)
    return rr


def P(x, y):
    p = Graphene.Point()
    p.init(x, y)
    return p


def inside(p, r, pad=0.0):
    x, y, w, h = r
    return x - pad <= p[0] <= x + w + pad and y - pad <= p[1] <= y + h + pad


class Target:
    __slots__ = ('kind', 'label', 'rect')

    def __init__(self, kind, label, rect):
        self.kind, self.label, self.rect = kind, label, rect


class Session:
    def __init__(self, app, cap, mode, backdrop=False):
        self.app = app
        self.cap = cap
        self.mode = mode
        # The shell paints the frozen frame under the transparent overlay
        # (extension.js), so the editor can open before cap has its pixels.
        self.backdrop = backdrop
        self.waiting = []
        self.f = cap.f
        self.state = 'hover'
        self.sel = None
        self.hover = None
        self.tool = None
        self.anns = []
        self.redo_stack = []
        self.live = None
        self.cache = None
        self.drag = None
        self.editor = None
        self.color_format = 'hex'
        self.chrome_hidden = False
        self.closed = False
        meta = cap.meta
        pointer = meta.get('pointer') or {'x': 0, 'y': 0}
        self.pointer = (pointer['x'] * self.f, pointer['y'] * self.f)
        self.targets = self._build_targets(meta)
        self.update_hover()

        display = Gdk.Display.get_default()
        gdk_monitors = [display.get_monitors().get_item(i)
                        for i in range(display.get_monitors().get_n_items())]
        monitors = meta.get('monitors') or [
            {'x': 0, 'y': 0, 'width': cap.width / self.f, 'height': cap.height / self.f}]
        self.windows = []
        for i, mon in enumerate(monitors):
            gdk_monitor = next((g for g in gdk_monitors
                                if g.get_geometry().x == mon.get('gdk_x', mon['x']) and
                                g.get_geometry().y == mon.get('gdk_y', mon['y'])),
                               gdk_monitors[i] if i < len(gdk_monitors) else None)
            self.windows.append(OverlayWindow(self, mon, gdk_monitor))
        for win in self.windows:
            win.present()

    # ---- geometry -----------------------------------------------------

    def _clip(self, rect):
        x, y, w, h = rect
        x0, y0 = max(0, int(round(x))), max(0, int(round(y)))
        x1 = min(self.cap.width, int(round(x + w)))
        y1 = min(self.cap.height, int(round(y + h)))
        if x1 - x0 < 2 or y1 - y0 < 2:
            return None
        return (x0, y0, x1 - x0, y1 - y0)

    def _build_targets(self, meta):
        f = self.f
        targets = []
        for t in meta.get('targets', []):
            rect = self._clip((t['x'] * f, t['y'] * f, t['w'] * f, t['h'] * f))
            if rect:
                targets.append(Target(t['kind'], t.get('label', ''), rect))
        for i, m in enumerate(meta.get('monitors', [])):
            rect = self._clip((m['x'] * f, m['y'] * f, m['width'] * f, m['height'] * f))
            if rect:
                targets.append(Target('screen', f'屏幕 {i + 1}', rect))
        if not targets:
            targets.append(Target('screen', '屏幕', (0, 0, self.cap.width, self.cap.height)))
        return targets

    def target_at(self, p):
        for t in self.targets:
            if inside(p, t.rect):
                return t
        return None

    def update_hover(self):
        self.hover = self.target_at(self.pointer) if self.state == 'hover' else None

    def clamp_point(self, p, rect=None):
        x, y, w, h = rect or (0, 0, self.cap.width, self.cap.height)
        return (min(max(p[0], x), x + w), min(max(p[1], y), y + h))

    def hit(self, p):
        """What a press at p would do once a region is selected.

        As in Snipaste the whole area outside the selection is a handle:
        the screen splits into nine zones around it, and dragging in a zone
        moves the matching edge(s). Inside, a press draws with the active
        tool or moves the selection; a thin inner band grabs the edges too
        unless a tool is active (then annotations may go right up to them).
        """
        if not self.sel:
            return None
        x, y, w, h = self.sel
        if self.tool and inside(p, self.sel):
            return 'draw'
        band = 0 if self.tool else 9 * self.f
        tx, ty = min(band, w / 4), min(band, h / 4)
        horiz = 'w' if p[0] < x + tx else 'e' if p[0] > x + w - tx else ''
        vert = 'n' if p[1] < y + ty else 's' if p[1] > y + h - ty else ''
        return (vert + horiz) or 'move'

    # ---- style --------------------------------------------------------

    def size_index(self):
        return self.app.style['sizes'].get(size_family(self.tool), 1)

    def set_size_index(self, index):
        self.app.style['sizes'][size_family(self.tool)] = max(0, min(2, index))
        if self.editor:
            self.editor.restyle()

    def size_ip(self):
        return theme.SIZES[size_family(self.tool)][self.size_index()] * self.f

    @property
    def color(self):
        return self.app.style['color']

    def set_color(self, color):
        self.app.style['color'] = color
        if self.editor:
            self.editor.restyle()

    def set_tool(self, tool):
        self.commit_editor()
        self.tool = tool
        self.sync_chrome()
        self.redraw()

    # ---- drawing helpers ----------------------------------------------

    def redraw(self):
        for win in self.windows:
            win.canvas.queue_draw()

    def set_pixels(self, pixbuf):
        self.cap.set_pixbuf(pixbuf)
        self.refresh_cache()
        self.redraw()
        waiting, self.waiting = self.waiting, []
        for action in waiting:
            if self.closed:
                break
            action()

    def refresh_cache(self):
        # Rendered over the annotations' own bounds, not the selection, so
        # the selection can still be moved and resized after drawing.
        self.cache = None
        if not self.anns:
            return
        boxes = [render.annotation_bounds(a) for a in self.anns]
        x0 = min(b[0] for b in boxes)
        y0 = min(b[1] for b in boxes)
        x1 = max(b[0] + b[2] for b in boxes)
        y1 = max(b[1] + b[3] for b in boxes)
        rect = self._clip((x0, y0, x1 - x0, y1 - y0))
        if rect:
            tex = render.surface_texture(render.render_region(self.cap, rect, self.anns))
            self.cache = (rect, tex)

    def sync_chrome(self):
        if self.closed:
            return
        show = (self.state == 'selected' and self.sel is not None and not self.chrome_hidden and
                not (self.drag and self.drag['op'] in ('new', 'move', 'resize')))
        host = None
        if show:
            x, y, w, h = self.sel
            corner = (x + w - 1, y + h - 1)
            host = next((win for win in self.windows if inside(corner, win.canvas.ip_rect())),
                        self.windows[0])
        for win in self.windows:
            win.place_chrome(win is host)

    # ---- pointer flow ---------------------------------------------------

    def on_motion(self, p):
        self.pointer = p
        if self.state == 'hover':
            self.update_hover()
        self.redraw()

    def press(self, p):
        self.pointer = p
        if self.editor:
            self.commit_editor()
            self.drag = {'op': 'none', 'start': p}
            return
        if self.state == 'hover':
            self.drag = {'op': 'pending', 'start': p}
            return
        handle = self.hit(p)
        if handle == 'draw':
            p = self.clamp_point(p, self.sel)
            if self.tool == 'text':
                self.drag = {'op': 'text', 'start': p}
            elif self.tool == 'step':
                n = 1 + sum(1 for a in self.anns if a['kind'] == 'step')
                self.push({'kind': 'step', 'x': p[0], 'y': p[1], 'n': n,
                           'radius': self.size_ip(), 'color': self.color})
                self.drag = {'op': 'none', 'start': p}
            else:
                self.live = self._new_annotation(p)
                self.drag = {'op': 'draw', 'start': p}
        elif handle == 'move':
            self.drag = {'op': 'move', 'start': p, 'orig': self.sel}
        else:
            self.drag = {'op': 'resize', 'start': p, 'orig': self.sel, 'handle': handle}

    def _new_annotation(self, p):
        kind = self.tool
        a = {'kind': kind, 'color': self.color}
        if kind in ('pen', 'marker'):
            a['points'] = [p]
            a['width'] = self.size_ip()
        elif kind == 'mosaic':
            a.update(x0=p[0], y0=p[1], x1=p[0], y1=p[1], block=self.size_ip())
        else:
            a.update(x0=p[0], y0=p[1], x1=p[0], y1=p[1], width=self.size_ip())
        return a

    def drag_to(self, p, shift=False):
        self.pointer = p
        d = self.drag
        if not d:
            return
        op = d['op']
        if op == 'pending':
            if math.hypot(p[0] - d['start'][0], p[1] - d['start'][1]) < 4 * self.f:
                return
            d['op'] = op = 'new'
            self.state = 'selecting'
            self.hover = None
            self.sync_chrome()
        if op == 'new':
            a = self.clamp_point(d['start'])
            b = self.clamp_point(p)
            x, y, w, h = render.norm_rect(a[0], a[1], b[0], b[1])
            self.sel = (int(x), int(y), max(1, int(round(w))), max(1, int(round(h))))
        elif op == 'move':
            ox, oy, w, h = d['orig']
            nx = min(max(0, ox + p[0] - d['start'][0]), self.cap.width - w)
            ny = min(max(0, oy + p[1] - d['start'][1]), self.cap.height - h)
            self.sel = (int(round(nx)), int(round(ny)), w, h)
        elif op == 'resize':
            # Relative to the press, so grabbing far outside does not jump.
            self.sel = self._resized(d['orig'], d['handle'],
                                     p[0] - d['start'][0], p[1] - d['start'][1])
        elif op == 'draw' and self.live:
            self._extend_live(self.clamp_point(p, self.sel), shift)
        self.redraw()

    def _resized(self, orig, handle, dx, dy):
        x0, y0, w, h = orig
        x1, y1 = x0 + w, y0 + h
        if 'w' in handle:
            x0 += dx
        if 'e' in handle:
            x1 += dx
        if 'n' in handle:
            y0 += dy
        if 's' in handle:
            y1 += dy
        x0, x1 = (min(max(0, v), self.cap.width) for v in (x0, x1))
        y0, y1 = (min(max(0, v), self.cap.height) for v in (y0, y1))
        x, y, w, h = render.norm_rect(x0, y0, x1, y1)
        return (int(round(x)), int(round(y)), max(1, int(round(w))), max(1, int(round(h))))

    def _extend_live(self, p, shift):
        a = self.live
        if a['kind'] in ('pen', 'marker'):
            last = a['points'][-1]
            if shift and a['kind'] == 'marker':
                # Shift draws a straight highlight from the first point.
                a['points'] = [a['points'][0], (p[0], a['points'][0][1])]
            elif math.hypot(p[0] - last[0], p[1] - last[1]) >= 1.5 * self.f:
                a['points'].append(p)
            return
        x0, y0 = a['x0'], a['y0']
        x1, y1 = p
        if shift:
            dx, dy = x1 - x0, y1 - y0
            if a['kind'] in ('line', 'arrow'):
                angle = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
                length = math.hypot(dx, dy)
                x1, y1 = x0 + length * math.cos(angle), y0 + length * math.sin(angle)
            else:
                side = max(abs(dx), abs(dy))
                x1, y1 = x0 + math.copysign(side, dx or 1), y0 + math.copysign(side, dy or 1)
        a['x1'], a['y1'] = x1, y1

    def release(self, p):
        d, self.drag = self.drag, None
        if not d:
            return
        op = d['op']
        if op == 'pending':
            target = self.target_at(d['start'])
            if target:
                self.sel = target.rect
                self._selected()
        elif op == 'new':
            if self.sel and self.sel[2] >= 3 and self.sel[3] >= 3:
                self._selected()
            else:
                self.reset_selection()
        elif op == 'draw' and self.live:
            a, self.live = self.live, None
            if self._meaningful(a):
                self.push(a)
        elif op == 'text':
            self.open_editor(d['start'])
        self.sync_chrome()
        self.redraw()

    def _selected(self):
        self.state = 'selected'
        self.hover = None
        if self.mode == 'copy':
            GLib.idle_add(lambda: self.finish('copy') and False)

    def _meaningful(self, a):
        if a['kind'] in ('pen', 'marker'):
            return len(a['points']) > 1 or a['kind'] == 'pen'
        return math.hypot(a['x1'] - a['x0'], a['y1'] - a['y0']) >= 3 * self.f

    def reset_selection(self):
        self.commit_editor(discard=True)
        self.state = 'hover'
        self.sel = None
        self.tool = None
        self.chrome_hidden = False
        self.anns.clear()
        self.redo_stack.clear()
        self.cache = None
        self.update_hover()
        self.sync_chrome()
        self.redraw()

    def right_click(self):
        if self.editor:
            self.commit_editor()
        elif self.tool:
            self.set_tool(None)
        elif self.state == 'selected':
            self.reset_selection()
        else:
            self.cancel()

    def double_click(self, p):
        if self.state == 'selected' and self.tool is None and inside(p, self.sel):
            self.finish('copy')

    def scroll(self, dy):
        if self.state != 'selected' or not self.tool:
            return False
        self.set_size_index(self.size_index() + (1 if dy < 0 else -1))
        self.sync_chrome()
        return True

    # ---- annotation stack ---------------------------------------------

    def push(self, a):
        self.anns.append(a)
        self.redo_stack.clear()
        self.refresh_cache()
        self.sync_chrome()
        self.redraw()

    def undo(self):
        self.commit_editor()
        if self.anns:
            self.redo_stack.append(self.anns.pop())
            self.refresh_cache()
            self.sync_chrome()
            self.redraw()

    def redo(self):
        if self.redo_stack:
            self.anns.append(self.redo_stack.pop())
            self.refresh_cache()
            self.sync_chrome()
            self.redraw()

    # ---- text ----------------------------------------------------------

    def open_editor(self, p):
        host = next((w for w in self.windows if inside(p, w.canvas.ip_rect())), self.windows[0])
        self.editor = TextEditor(self, host, p)

    def commit_editor(self, discard=False):
        editor, self.editor = self.editor, None
        if not editor:
            return
        text = editor.text().rstrip()
        editor.destroy_editor()
        if text.strip() and not discard:
            self.push({'kind': 'text', 'x': editor.p[0], 'y': editor.p[1], 'text': text,
                       'size': editor.size_ip, 'color': editor.color})

    # ---- keyboard -------------------------------------------------------

    def on_key(self, keyval, state):
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        name = (Gdk.keyval_name(Gdk.keyval_to_lower(keyval)) or '').lower()

        if self.editor:
            if name == 'escape' or (ctrl and name in ('return', 'kp_enter')):
                self.commit_editor()
                self.redraw()
                return True
            return False

        if name == 'escape':
            self.cancel()
        elif name in ('return', 'kp_enter'):
            if self.state == 'hover' and self.hover:
                self.sel = self.hover.rect
                self.state = 'selected'
            self.finish('copy')
        elif ctrl and name == 'c':
            self.finish('copy')
        elif ctrl and name == 's':
            self.finish('save-as' if shift else 'save')
        elif ctrl and name == 't':
            self.finish('pin')
        elif ctrl and (name == 'y' or (shift and name == 'z')):
            self.redo()
        elif ctrl and name == 'z':
            self.undo()
        elif ctrl and name == 'a':
            self.sel = (0, 0, self.cap.width, self.cap.height)
            self.state = 'selected'
            self.hover = None
            self.sync_chrome()
            self.redraw()
        elif self.state == 'hover' and name == 'c':
            self.copy_color()
        elif self.state == 'hover' and name in ('shift_l', 'shift_r'):
            self.color_format = 'rgb' if self.color_format == 'hex' else 'hex'
            self.redraw()
        elif self.state == 'hover' and name == 'r' and self.app.last_sel:
            sel, size = self.app.last_sel
            if size != (self.cap.width, self.cap.height):
                return False
            self.sel = sel
            self._selected()
            self.sync_chrome()
            self.redraw()
        elif (self.state != 'selected' or self.drag) and (name in ARROWS or name in WASD):
            # Before a region is fixed, and while dragging one, the keys
            # step the pointer itself, one image pixel at a time.
            self.nudge_pointer(*ARROWS[WASD.get(name, name)])
        elif self.state == 'selected' and name in ARROWS:
            self.nudge(name, 'grow' if ctrl else 'shrink' if shift else 'move')
        elif self.state == 'selected' and name == 'space':
            self.chrome_hidden = not self.chrome_hidden
            self.sync_chrome()
        elif self.state == 'selected' and self.tool and name in ('bracketleft', 'bracketright'):
            self.set_size_index(self.size_index() + (1 if name == 'bracketright' else -1))
            self.sync_chrome()
        elif self.state == 'selected' and not ctrl and name in TOOL_KEYS:
            tool = TOOL_KEYS[name]
            self.set_tool(None if self.tool == tool else tool)
        else:
            return False
        return True

    def nudge(self, direction, mode):
        """Arrow keys, as in Snipaste: move the selection by one pixel;
        with Ctrl push the edge on that side out, with Shift pull it in."""
        dx, dy = ARROWS[direction]
        x, y, w, h = self.sel
        x0, y0, x1, y1 = x, y, x + w, y + h
        W, H = self.cap.width, self.cap.height
        if mode == 'move':
            dx = min(max(dx, -x0), W - x1)
            dy = min(max(dy, -y0), H - y1)
            x0, x1, y0, y1 = x0 + dx, x1 + dx, y0 + dy, y1 + dy
        else:
            sign = 1 if mode == 'grow' else -1
            if direction == 'left':
                x0 = min(max(0, x0 - sign), x1 - 1)
            elif direction == 'right':
                x1 = max(min(W, x1 + sign), x0 + 1)
            elif direction == 'up':
                y0 = min(max(0, y0 - sign), y1 - 1)
            else:
                y1 = max(min(H, y1 + sign), y0 + 1)
        self.sel = (x0, y0, x1 - x0, y1 - y0)
        self.sync_chrome()
        self.redraw()

    def nudge_pointer(self, dx, dy):
        # Aim at pixel centres so repeated steps never skip or repeat a
        # pixel under fractional scaling.
        x = min(max(0, math.floor(self.pointer[0]) + dx), self.cap.width - 1) + 0.5
        y = min(max(0, math.floor(self.pointer[1]) + dy), self.cap.height - 1) + 0.5
        # Update right away; key repeat can outrun the motion round trip.
        if self.drag:
            self.drag_to((x, y))
        else:
            self.on_motion((x, y))
        for win in self.windows:
            win.canvas._update_cursor()
        Gio.bus_get_sync(Gio.BusType.SESSION, None).call(
            'org.gnome.Shell', SHELL_PATH, SHELL_IFACE, 'MovePointer',
            GLib.Variant('(dd)', (x / self.f, y / self.f)), None, Gio.DBusCallFlags.NONE,
            1000, None, None)

    # ---- results --------------------------------------------------------

    def color_string(self, rgb):
        if self.color_format == 'rgb':
            return 'RGB({}, {}, {})'.format(*rgb)
        return '#{:02X}{:02X}{:02X}'.format(*rgb)

    def copy_color(self, p=None):
        p = p or self.pointer
        if self.cap.pixbuf is None:
            self.waiting.append(lambda: self.copy_color(p))
            return
        self.app.copy_text(self.color_string(self.cap.color_at(*p)))
        self.close()

    def finish(self, action):
        if self.closed:
            return
        self.commit_editor()
        if self.state != 'selected' or not self.sel:
            return
        if self.cap.pixbuf is None:
            # Stay open until the pixels arrive; closing would cancel them.
            self.waiting.append(lambda: self.finish(action))
            return
        surface = render.render_region(self.cap, self.sel, self.anns)
        texture = render.surface_texture(surface)
        x, y = self.sel[0] / self.f, self.sel[1] / self.f
        self.app.remember(texture, self.f)
        self.app.last_sel = (self.sel, (self.cap.width, self.cap.height))
        if action in ('copy', 'pin'):
            self.app.copy_texture(texture)
        self.close()
        if action == 'save':
            self.app.save_surface(surface)
        elif action == 'save-as':
            self.app.save_surface_as(surface)
        elif action == 'pin':
            self.app.pin_texture(texture, self.f, (x, y))

    def cancel(self):
        self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        for win in self.windows:
            win.destroy()
        self.windows = []
        self.app.session_closed(self)


class TextEditor:
    """A live TextView placed so its first glyph lands on the click point."""

    PAD_X, PAD_Y = 5, 3  # border + CSS padding of .glint-text-editor

    def __init__(self, session, window, p):
        self.session = session
        self.window = window
        self.p = p
        self.box = Gtk.Box(css_classes=['glint-text-editor'],
                           halign=Gtk.Align.START, valign=Gtk.Align.START)
        self.view = Gtk.TextView(wrap_mode=Gtk.WrapMode.NONE, accepts_tab=False,
                                 top_margin=0, bottom_margin=0, left_margin=0, right_margin=0)
        self.view.set_name('glint-text')
        self.view.set_size_request(24, -1)
        self.box.append(self.view)
        self.provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self.provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.restyle()
        wx, wy = window.canvas.to_widget(*p)
        self.box.set_margin_start(max(0, int(round(wx - self.PAD_X))))
        self.box.set_margin_top(max(0, int(round(wy - self.PAD_Y))))
        window.overlay.add_overlay(self.box)
        self.view.grab_focus()

    def restyle(self):
        self.color = self.session.color
        self.size_ip = self.session.size_ip()
        px = self.size_ip / self.session.f
        self.provider.load_from_string(
            f'#glint-text {{ color: {self.color}; font-family: {theme.TEXT_FAMILY};'
            f' font-weight: 600; font-size: {px:.2f}px; }}')

    def text(self):
        buf = self.view.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

    def destroy_editor(self):
        self.window.overlay.remove_overlay(self.box)
        Gtk.StyleContext.remove_provider_for_display(Gdk.Display.get_default(), self.provider)
        self.window.canvas.grab_focus()


class Canvas(Gtk.Widget):
    def __init__(self, session, mon):
        super().__init__(focusable=True, hexpand=True, vexpand=True)
        self.s = session
        self.mon = mon
        self.mono = Pango.FontDescription.from_string(f'{theme.MONO} Medium 9.5')
        self.mono_big = Pango.FontDescription.from_string(f'{theme.MONO} SemiBold 10.5')
        self.sans = Pango.FontDescription.from_string(f'{theme.SANS}, Noto Sans CJK SC Medium 10')
        self._loupe_key = None
        self._loupe_tex = None
        self._cursor = None

        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect('drag-begin', self._drag_begin)
        drag.connect('drag-update', self._drag_update)
        drag.connect('drag-end', self._drag_end)
        self.add_controller(drag)
        click = Gtk.GestureClick(button=0)
        click.connect('pressed', self._clicked)
        self.add_controller(click)
        motion = Gtk.EventControllerMotion()
        motion.connect('motion', self._motion)
        self.add_controller(motion)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect('scroll', lambda _c, _dx, dy: self.s.scroll(dy))
        self.add_controller(scroll)
        self._set_cursor('crosshair')

    # ---- coordinate mapping ---------------------------------------------

    def to_ip(self, wx, wy):
        f = self.s.f
        return ((wx + self.mon['x']) * f, (wy + self.mon['y']) * f)

    def to_widget(self, ix, iy):
        f = self.s.f
        return (ix / f - self.mon['x'], iy / f - self.mon['y'])

    def rect_widget(self, r):
        x, y = self.to_widget(r[0], r[1])
        return (x, y, r[2] / self.s.f, r[3] / self.s.f)

    def ip_rect(self):
        f = self.s.f
        return (self.mon['x'] * f, self.mon['y'] * f, self.mon['width'] * f, self.mon['height'] * f)

    # ---- events -----------------------------------------------------------

    def _drag_begin(self, gesture, x, y):
        self._origin = (x, y)
        self.grab_focus()
        self.s.press(self.to_ip(x, y))
        self.queue_draw()

    def _drag_update(self, gesture, dx, dy):
        state = gesture.get_current_event_state()
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        self.s.drag_to(self.to_ip(self._origin[0] + dx, self._origin[1] + dy), shift)

    def _drag_end(self, gesture, dx, dy):
        self.s.release(self.to_ip(self._origin[0] + dx, self._origin[1] + dy))
        self._update_cursor()

    def _clicked(self, gesture, n_press, x, y):
        button = gesture.get_current_button()
        if button == Gdk.BUTTON_SECONDARY:
            self.s.right_click()
        elif button == Gdk.BUTTON_PRIMARY and n_press == 2:
            self.s.double_click(self.to_ip(x, y))

    def _motion(self, _ctrl, x, y):
        self.s.on_motion(self.to_ip(x, y))
        self._update_cursor()

    def _set_cursor(self, name):
        if name != self._cursor:
            self._cursor = name
            self.set_cursor_from_name(name)

    def _update_cursor(self):
        s = self.s
        if s.drag:
            return
        if s.state != 'selected':
            self._set_cursor('crosshair')
            return
        hit = s.hit(s.pointer)
        if hit == 'draw':
            self._set_cursor('text' if s.tool == 'text' else 'crosshair')
        else:
            self._set_cursor(HANDLE_CURSORS[hit])

    # ---- painting ---------------------------------------------------------

    def do_snapshot(self, snap):
        s = self.s
        cap = s.cap
        w, h = self.get_width(), self.get_height()
        f = s.f
        mx, my = self.mon['x'], self.mon['y']
        snap.push_clip(R(0, 0, w, h))
        if not s.backdrop:
            snap.append_scaled_texture(cap.texture, Gsk.ScalingFilter.NEAREST,
                                       R(-mx, -my, cap.width / f, cap.height / f))

        bright = s.sel if s.sel else (s.hover.rect if s.hover else None)
        if s.sel and s.cache is not None:
            rect, tex = s.cache
            snap.push_clip(R(*self.rect_widget(s.sel)))
            snap.append_scaled_texture(tex, Gsk.ScalingFilter.NEAREST, R(*self.rect_widget(rect)))
            snap.pop()
        if s.live is not None:
            self._paint_live(snap)
        self._paint_dim(snap, w, h, bright)

        if s.state == 'hover' and s.hover:
            self._paint_hover(snap, s.hover)
        if s.sel:
            self._paint_selection(snap)
        if s.state in ('hover', 'selecting'):
            self._paint_guides(snap, w, h)
        if s.state == 'hover':
            self._paint_hint(snap, w, h)
        show_loupe = cap.pixbuf is not None and (s.state in ('hover', 'selecting') or (
            s.drag is not None and s.drag['op'] in ('resize', 'new')))
        if show_loupe:
            self._paint_loupe(snap, w, h)
        snap.pop()

    def _paint_dim(self, snap, w, h, bright):
        if not bright:
            snap.append_color(DIM, R(0, 0, w, h))
            return
        x, y, bw, bh = self.rect_widget(bright)
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(w, x + bw), min(h, y + bh)
        if x1 <= x0 or y1 <= y0:
            snap.append_color(DIM, R(0, 0, w, h))
            return
        snap.append_color(DIM, R(0, 0, w, y0))
        snap.append_color(DIM, R(0, y1, w, h - y1))
        snap.append_color(DIM, R(0, y0, x0, y1 - y0))
        snap.append_color(DIM, R(x1, y0, w - x1, y1 - y0))

    def _paint_live(self, snap):
        s = self.s
        a = s.live
        bx, by, bw, bh = render.annotation_bounds(a)
        sx, sy, sw, sh = s.sel
        x0, y0 = max(bx, sx), max(by, sy)
        x1, y1 = min(bx + bw, sx + sw), min(by + bh, sy + sh)
        if x1 - x0 < 1 or y1 - y0 < 1:
            return
        if a['kind'] in ('marker', 'mosaic') and s.cap.pixbuf is not None:
            # These depend on the pixels underneath, so preview them on the
            # real composite instead of a transparent layer.
            rect = (int(x0), int(y0), int(math.ceil(x1 - x0)), int(math.ceil(y1 - y0)))
            tex = render.surface_texture(render.render_region(s.cap, rect, s.anns + [a]))
            snap.append_scaled_texture(tex, Gsk.ScalingFilter.NEAREST, R(*self.rect_widget(rect)))
            return
        wx, wy, ww, wh = self.rect_widget((x0, y0, x1 - x0, y1 - y0))
        cr = snap.append_cairo(R(wx, wy, ww, wh))
        cr.translate(-self.mon['x'], -self.mon['y'])
        cr.scale(1 / s.f, 1 / s.f)
        render.draw_annotation(cr, a, s.cap)

    def _border(self, snap, x, y, w, h, width, color):
        snap.append_border(RR(x, y, w, h, 0), [width] * 4, [color] * 4)

    def _paint_hover(self, snap, target):
        x, y, w, h = self.rect_widget(target.rect)
        self._border(snap, x - 1, y - 1, w + 2, h + 2, 2.0, ACCENT)
        label = target.label or ''
        text = f'{label}   {target.rect[2]} × {target.rect[3]}' if label else \
            f'{target.rect[2]} × {target.rect[3]}'
        self._pill_near(snap, x, y, text)

    def _paint_selection(self, snap):
        s = self.s
        x, y, w, h = self.rect_widget(s.sel)
        self._border(snap, x - 2, y - 2, w + 4, h + 4, 1.0, HALO)
        self._border(snap, x - 1, y - 1, w + 2, h + 2, 1.0, ACCENT)
        if s.state == 'selected':
            t = 3.0
            L = min(18.0, w / 2.5, h / 2.5) + t
            for rx, ry, rw, rh in (
                (x - t, y - t, L, t), (x - t, y - t, t, L),
                (x + w + t - L, y - t, L, t), (x + w, y - t, t, L),
                (x - t, y + h, L, t), (x - t, y + h + t - L, t, L),
                (x + w + t - L, y + h, L, t), (x + w, y + h + t - L, t, L),
            ):
                snap.append_color(ACCENT, R(rx, ry, rw, rh))
            if w > 72:
                snap.append_color(ACCENT, R(x + w / 2 - 8, y - t, 16, t))
                snap.append_color(ACCENT, R(x + w / 2 - 8, y + h, 16, t))
            if h > 72:
                snap.append_color(ACCENT, R(x - t, y + h / 2 - 8, t, 16))
                snap.append_color(ACCENT, R(x + w, y + h / 2 - 8, t, 16))
        self._pill_near(snap, x, y, f'{s.sel[2]} × {s.sel[3]}')

    def _layout(self, text, desc, markup=False):
        layout = self.create_pango_layout(None)
        layout.set_font_description(desc)
        if markup:
            layout.set_markup(text, -1)
        else:
            layout.set_text(text, -1)
        return layout

    def _pill(self, snap, x, y, layout, fg=ACCENT, pad_x=9, pad_y=4, radius=7):
        _ink, logical = layout.get_pixel_extents()
        w, h = logical.width + 2 * pad_x, logical.height + 2 * pad_y
        rr = RR(x, y, w, h, radius)
        snap.append_outset_shadow(rr, SHADOW, 0, 3, 0, 10)
        snap.push_rounded_clip(rr)
        snap.append_color(GLASS, R(x, y, w, h))
        snap.pop()
        snap.save()
        snap.translate(P(x + pad_x, y + pad_y))
        snap.append_layout(layout, fg)
        snap.restore()
        return w, h

    def _pill_near(self, snap, x, y, text):
        layout = self._layout(text, self.mono)
        _ink, logical = layout.get_pixel_extents()
        ph = logical.height + 8
        px = min(max(4, x), self.get_width() - logical.width - 22)
        py = y - ph - 7
        if py < 4:
            py = max(4, y) + 7
            px = max(4, x) + 7
        self._pill(snap, px, py, layout)

    def _paint_guides(self, snap, w, h):
        px, py = self.to_widget(*self.s.pointer)
        if not (0 <= px <= w and 0 <= py <= h):
            return
        snap.append_color(ACCENT_SOFT, R(0, int(py), w, 1))
        snap.append_color(ACCENT_SOFT, R(int(px), 0, 1, h))

    def _paint_hint(self, snap, w, h):
        a = theme.ACCENT
        markup = (f'拖拽框选   单击选窗口   <span foreground="{a}">C</span> 复制色值   '
                  f'<span foreground="{a}">Shift</span> 切换格式   '
                  f'<span foreground="{a}">WASD</span> 逐像素   '
                  f'<span foreground="{a}">R</span> 上次选区   '
                  f'<span foreground="{a}">Ctrl+A</span> 全屏   '
                  f'<span foreground="{a}">Esc</span> 退出')
        layout = self._layout(markup, self.sans, markup=True)
        _ink, logical = layout.get_pixel_extents()
        pw = logical.width + 28
        px, py = self.to_widget(*self.s.pointer)
        y = h - logical.height - 40
        if py > y - 40 and abs(px - w / 2) < pw:
            y = 32
        self._pill(snap, (w - pw) / 2, y, layout, fg=WHITE_DIM, pad_x=14, pad_y=7, radius=10)

    def _paint_loupe(self, snap, w, h):
        s = self.s
        px, py = self.to_widget(*s.pointer)
        if not (0 <= px < w and 0 <= py < h):
            return
        n, cell = LOUPE_CELLS, LOUPE_CELL
        size = n * cell
        info_h = 48
        lx, ly = px + 24, py + 24
        if lx + size > w - 6:
            lx = px - 24 - size
        if ly + size + info_h + 6 > h - 6:
            ly = py - 24 - size - info_h - 6
        ix, iy = int(s.pointer[0]), int(s.pointer[1])
        if self._loupe_key != (ix, iy):
            self._loupe_key = (ix, iy)
            self._loupe_tex = s.cap.loupe_texture(ix, iy, n)

        rr = RR(lx, ly, size, size, 12)
        snap.append_outset_shadow(rr, SHADOW, 0, 8, 0, 22)
        snap.push_rounded_clip(rr)
        snap.append_scaled_texture(self._loupe_tex, Gsk.ScalingFilter.NEAREST, R(lx, ly, size, size))
        for i in range(1, n):
            snap.append_color(GRID, R(lx + i * cell, ly, 1, size))
            snap.append_color(GRID, R(lx, ly + i * cell, size, 1))
        c = n // 2
        snap.append_color(ACCENT_SOFT, R(lx, ly + c * cell, size, cell))
        snap.append_color(ACCENT_SOFT, R(lx + c * cell, ly, cell, size))
        snap.pop()
        self._border(snap, lx + c * cell - 1, ly + c * cell - 1, cell + 2, cell + 2, 1.5, ACCENT)
        snap.append_border(rr, [1.0] * 4, [theme.rgba('#FFFFFF', 0.22)] * 4)

        rgb = s.cap.color_at(ix, iy)
        iy0 = ly + size + 6
        irr = RR(lx, iy0, size, info_h, 10)
        snap.append_outset_shadow(irr, SHADOW, 0, 6, 0, 16)
        snap.push_rounded_clip(irr)
        snap.append_color(GLASS, R(lx, iy0, size, info_h))
        snap.pop()
        pos = self._layout(f'{ix:>5} , {iy:<5}', self.mono)
        snap.save()
        snap.translate(P(lx + 10, iy0 + 6))
        snap.append_layout(pos, WHITE_DIM)
        snap.restore()
        swatch = RR(lx + 10, iy0 + 27, 13, 13, 4)
        snap.push_rounded_clip(swatch)
        snap.append_color(theme.rgba(tuple(v / 255 for v in rgb)), R(lx + 10, iy0 + 27, 13, 13))
        snap.pop()
        snap.append_border(swatch, [1.0] * 4, [theme.rgba('#FFFFFF', 0.35)] * 4)
        value = self._layout(s.color_string(rgb), self.mono_big if s.color_format == 'hex' else self.mono)
        snap.save()
        snap.translate(P(lx + 30, iy0 + 24))
        snap.append_layout(value, WHITE)
        snap.restore()


class OverlayWindow(Gtk.Window):
    def __init__(self, session, mon, gdk_monitor):
        super().__init__(application=session.app, title='glint-overlay', decorated=False)
        self.add_css_class('glint-overlay')
        if session.backdrop:
            self.add_css_class('shell-backdrop')
        self.session = session
        self.canvas = Canvas(session, mon)
        self.overlay = Gtk.Overlay(child=self.canvas)
        self.toolbar = Toolbar(session)
        self.props = PropsBar(session)
        self.overlay.add_overlay(self.toolbar)
        self.overlay.add_overlay(self.props)
        self.set_child(self.overlay)

        keys = Gtk.EventControllerKey()
        keys.connect('key-pressed', lambda _c, keyval, _code, state: session.on_key(keyval, state))
        self.add_controller(keys)
        self.connect('close-request', lambda *_: session.cancel() or True)

        if gdk_monitor is not None:
            self.fullscreen_on_monitor(gdk_monitor)
        else:
            self.fullscreen()

    def place_chrome(self, visible):
        tb, pb = self.toolbar, self.props
        s = self.session
        if not visible:
            tb.set_visible(False)
            pb.set_visible(False)
            return
        tb.sync()
        pb.sync()
        W, H = self.canvas.get_width(), self.canvas.get_height()
        x, y, w, h = self.canvas.rect_widget(s.sel)
        gap, edge = 10, 8

        show_props = s.tool is not None
        # Hidden widgets measure as 0x0 and margins count towards the
        # measured size, so show the bars and clear margins first.
        for bar, shown in ((tb, True), (pb, show_props)):
            bar.set_visible(shown)
            bar.set_margin_start(0)
            bar.set_margin_top(0)
        tw = tb.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
        th = tb.measure(Gtk.Orientation.VERTICAL, -1)[1]
        pw = pb.measure(Gtk.Orientation.HORIZONTAL, -1)[1] if show_props else 0
        ph = pb.measure(Gtk.Orientation.VERTICAL, -1)[1] if show_props else 0
        need = th + (ph + 6 if show_props else 0)

        if y + h + gap + need <= H - edge:
            ty, side = y + h + gap, 'below'
        elif y - gap - need >= edge:
            ty, side = y - gap - th, 'above'
        else:
            ty, side = y + h - gap - th, 'inside'
        tx = min(max(edge, x + w - tw), W - tw - edge)
        ty = min(max(edge, ty), H - th - edge)
        tb.set_margin_start(int(tx))
        tb.set_margin_top(int(ty))
        tb.set_visible(True)

        if show_props:
            px = min(max(edge, tx), W - pw - edge)
            py = ty + th + 6 if side == 'below' else ty - 6 - ph
            pb.set_margin_start(int(px))
            pb.set_margin_top(int(min(max(edge, py), H - ph - edge)))
        pb.set_visible(show_props)
