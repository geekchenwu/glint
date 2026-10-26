"""Graphite controls with a warm orange accent for selection and feedback."""
import os

import gi

gi.require_version('Gdk', '4.0')
gi.require_version('Gtk', '4.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gdk, Gtk, PangoCairo  # noqa: E402

ACCENT = '#F37021'
ACCENT_HOVER = '#FF8A3D'
INK = '#0E0F11'
GLASS = (0.071, 0.075, 0.086, 0.92)
# Must match DIM in extension.js so the frozen frame hands off seamlessly.
DIM = (8 / 255, 9 / 255, 12 / 255, 0.5)

PALETTE = ['#FF3D2E', ACCENT, '#D7FF3C', '#2EE6D6', '#3D7BFF', '#FFFFFF', '#16171A']
COLOR_NAMES = ['朱红', '爱马仕橙', '青柠', '湖青', '钴蓝', '纯白', '墨黑']

# Logical pixels per size step, keyed by the tool family.
SIZES = {
    'stroke': (2.0, 4.0, 7.0),
    'marker': (12.0, 18.0, 28.0),
    'text': (16.0, 24.0, 36.0),
    'mosaic': (8.0, 14.0, 24.0),
    'step': (11.0, 15.0, 20.0),
}

MONO = 'JetBrains Mono'
SANS = 'Space Grotesk'
TEXT_FAMILY = 'Space Grotesk, Noto Sans CJK SC, sans-serif'

_HERE = os.path.dirname(os.path.abspath(__file__))


def rgba(spec, alpha=None):
    color = Gdk.RGBA()
    if isinstance(spec, tuple):
        color.red, color.green, color.blue = spec[:3]
        color.alpha = spec[3] if len(spec) > 3 else 1.0
    else:
        color.parse(spec)
    if alpha is not None:
        color.alpha = alpha
    return color


def hex_to_rgb(spec):
    spec = spec.lstrip('#')
    return tuple(int(spec[i:i + 2], 16) / 255 for i in (0, 2, 4))


def luminance(spec):
    r, g, b = hex_to_rgb(spec)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def load_fonts():
    fontmap = PangoCairo.FontMap.get_default()
    for root in (os.path.join(_HERE, '..', 'fonts'), os.path.join(_HERE, '..', '..', 'fonts')):
        root = os.path.normpath(root)
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            if name.endswith('.ttf'):
                fontmap.add_font_file(os.path.join(root, name))
        return


CSS = f"""
window.glint-overlay {{ background: black; }}

.glint-bar {{
  background: rgba(18, 19, 22, 0.94);
  border-radius: 14px;
  padding: 5px;
  box-shadow: 0 14px 36px rgba(0, 0, 0, 0.42), 0 2px 6px rgba(0, 0, 0, 0.3),
              inset 0 0 0 1px rgba(255, 255, 255, 0.07);
}}
.glint-bar button {{
  min-width: 34px; min-height: 34px; padding: 0; margin: 0 1px;
  border-radius: 9px; border: none; box-shadow: none; outline: none;
  background: transparent; color: #E8EAED;
  transition: background 120ms ease, color 120ms ease;
}}
.glint-bar button:hover {{ background: rgba(255, 255, 255, 0.09); }}
.glint-bar button:active {{ background: rgba(255, 255, 255, 0.14); }}
.glint-bar button:checked {{ background: {ACCENT}; color: {INK}; }}
.glint-bar button.primary {{ background: {ACCENT}; color: {INK}; min-width: 40px; }}
.glint-bar button.primary:hover {{ background: {ACCENT_HOVER}; }}
.glint-bar button.danger:hover {{ background: rgba(255, 61, 46, 0.22); color: #FF8A80; }}
.glint-bar button:disabled {{ color: rgba(232, 234, 237, 0.28); }}
.glint-bar .sep {{ min-width: 1px; background: rgba(255, 255, 255, 0.10); margin: 8px 5px; }}

.glint-props button {{ min-width: 30px; min-height: 30px; border-radius: 8px; }}
.glint-props button:checked {{ background: rgba(255, 255, 255, 0.14); color: #fff; }}

.glint-text-editor {{
  border: 1px dashed {rgba(ACCENT, 0.85).to_string()};
  border-radius: 4px;
  padding: 2px 4px;
  background: rgba(0, 0, 0, 0.0);
}}
.glint-text-editor textview, .glint-text-editor textview text {{
  background: transparent;
  caret-color: {ACCENT};
}}

tooltip.background {{
  background: rgba(18, 19, 22, 0.96);
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  box-shadow: none;
}}
tooltip label {{ color: #E8EAED; font-family: "{SANS}", "Noto Sans CJK SC"; font-size: 12px; }}

window.glint-pin {{ background: transparent; }}
window.glint-pin > .titlebar {{
  min-height: 0; min-width: 0; padding: 0; margin: 0;
  border: none; background: none; box-shadow: none;
}}
window.glint-pin.csd {{
  border-radius: 6px;
  box-shadow: 0 0 0 1px {rgba(ACCENT, 0.95).to_string()}, 0 14px 34px rgba(0, 0, 0, 0.42);
}}
window.glint-pin.csd:backdrop {{
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.22), 0 8px 22px rgba(0, 0, 0, 0.32);
}}
.glint-osd {{
  background: rgba(18, 19, 22, 0.88);
  color: {ACCENT};
  border-radius: 999px;
  padding: 3px 10px;
  margin: 10px;
  font-family: "{MONO}";
  font-size: 12px;
  font-weight: 600;
}}
popover.glint-menu contents {{
  background: rgba(18, 19, 22, 0.97);
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.08);
}}
popover.glint-menu modelbutton {{ color: #E8EAED; border-radius: 7px; min-height: 30px; }}
popover.glint-menu modelbutton:hover {{ background: rgba(255, 255, 255, 0.09); }}
popover.glint-menu accelerator {{ color: rgba(232, 234, 237, 0.45); font-family: "{MONO}"; }}
"""


def install_css():
    provider = Gtk.CssProvider()
    provider.load_from_string(CSS)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
