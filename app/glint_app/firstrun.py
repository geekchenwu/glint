"""Per-user setup after a package install: enable the shell extension and
resolve shortcut clashes with other screenshot tools (Snipaste binds the same
keys through gsd-media-keys custom shortcuts).

Existing shortcuts are changed only on request, with a restorable backup.
Unoccupied shortcuts provide temporary access before the extension loads.
"""
import json
import os
import shlex
import time

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gio, GLib, Gtk  # noqa: E402

EXT_UUID = 'glint@chenwu.local'
EXT_SCHEMA = 'org.gnome.shell.extensions.glint'
SHORTCUT_KEYS = ('capture', 'capture-copy', 'pin', 'toggle-pins')
DEFAULT_SHORTCUTS = ('F1', '<Control>F1', 'F3', '<Shift>F3')
MEDIA_KEYS = 'org.gnome.settings-daemon.plugins.media-keys'
CUSTOM_KEY = MEDIA_KEYS + '.custom-keybinding'
FALLBACK_PREFIX = '/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/glint-'
FALLBACK_COMMANDS = dict(zip(SHORTCUT_KEYS, ('capture', 'copy', 'pin', 'toggle-pins')))


def config_dir():
    path = os.path.join(GLib.get_user_config_dir(), 'glint')
    os.makedirs(path, exist_ok=True)
    return path


class State:
    """Small key file remembering what first-run setup already did."""

    def __init__(self):
        self.path = os.path.join(config_dir(), 'state.ini')
        self.kf = GLib.KeyFile()
        try:
            self.kf.load_from_file(self.path, GLib.KeyFileFlags.NONE)
        except GLib.Error:
            pass

    def get(self, key):
        try:
            return self.kf.get_boolean('setup', key)
        except GLib.Error:
            return False

    def set(self, key, value=True):
        self.kf.set_boolean('setup', key, value)
        self.kf.save_to_file(self.path)


# ---- extension -------------------------------------------------------------

def _extensions_call(method, args, reply_type):
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    reply = bus.call_sync('org.gnome.Shell', '/org/gnome/Shell',
                          'org.gnome.Shell.Extensions', method, args,
                          GLib.VariantType(reply_type), Gio.DBusCallFlags.NONE, 5000, None)
    return reply.unpack()[0]


def extension_status():
    """'enabled', 'disabled', or 'unknown' when the shell has not scanned it
    yet (a package installed during the current session)."""
    try:
        info = _extensions_call('GetExtensionInfo', GLib.Variant('(s)', (EXT_UUID,)), '(a{sv})')
    except GLib.Error:
        return 'unknown'
    if not info:
        return 'unknown'
    return 'enabled' if info.get('enabled') and info.get('state') == 1 else 'disabled'


def enable_extension():
    try:
        return _extensions_call('EnableExtension', GLib.Variant('(s)', (EXT_UUID,)), '(b)')
    except GLib.Error:
        return False


def prepare_session():
    """Use the native extension if it is known; otherwise provide usable keys
    for the portal fallback until the next normal login."""
    state = State()
    status = extension_status()
    if status == 'enabled':
        remove_fallback_shortcuts()
        state.set('extension-enabled')
    elif status == 'disabled' and not state.get('extension-enabled'):
        if remove_fallback_shortcuts():
            # gsd-media-keys releases its compositor grabs asynchronously.
            time.sleep(.15)
        if enable_extension():
            # Do not re-register fallback keys while the extension is importing:
            # they can steal its grabs and leave F1 unbound after cleanup.
            status = extension_status()
            deadline = time.monotonic() + 2
            while status != 'enabled' and time.monotonic() < deadline:
                time.sleep(.05)
                status = extension_status()
            if status == 'enabled':
                state.set('extension-enabled')
    if status != 'enabled' and not state.get('extension-enabled'):
        install_fallback_shortcuts()
    return status


# ---- shortcuts -------------------------------------------------------------

def glint_shortcuts():
    settings = _extension_settings()
    if settings:
        return {accel for key in SHORTCUT_KEYS for accel in settings.get_strv(key) if accel}
    return set(DEFAULT_SHORTCUTS)


def _extension_settings():
    source = Gio.SettingsSchemaSource.get_default()
    local = os.path.join(GLib.get_user_data_dir(), 'gnome-shell', 'extensions', EXT_UUID, 'schemas')
    if os.path.isfile(os.path.join(local, 'gschemas.compiled')):
        source = Gio.SettingsSchemaSource.new_from_directory(local, source, False)
    schema = source.lookup(EXT_SCHEMA, True) if source else None
    return Gio.Settings.new_full(schema, None, None) if schema else None


def _normalize(accel):
    ok, key, mods = Gtk.accelerator_parse(accel)
    return (key, int(mods)) if ok and key else None


def _custom_paths():
    return list(Gio.Settings.new(MEDIA_KEYS).get_strv('custom-keybindings'))


def _fallback_path(key):
    return FALLBACK_PREFIX + key + '/'


def _launcher():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'glint'))


def _owned_fallback(path):
    key = next((key for key in SHORTCUT_KEYS if path == _fallback_path(key)), None)
    if key is None:
        return False
    entry = Gio.Settings.new_with_path(CUSTOM_KEY, path)
    try:
        command = shlex.split(entry.get_string('command'))
    except ValueError:
        return False
    launchers = {_launcher(), '/usr/share/glint/glint',
                 os.path.join(GLib.get_user_data_dir(), 'glint', 'glint')}
    return (entry.get_string('name') == 'Glint (session fallback)' and
            len(command) == 2 and command[0] in launchers and
            command[1] == FALLBACK_COMMANDS[key])


def fallback_capture_binding():
    path = _fallback_path('capture')
    if path in _custom_paths() and _owned_fallback(path):
        return Gio.Settings.new_with_path(CUSTOM_KEY, path).get_string('binding')
    return ''


def install_fallback_shortcuts():
    base = Gio.Settings.new(MEDIA_KEYS)
    paths = _custom_paths()
    occupied = {_normalize(Gio.Settings.new_with_path(CUSTOM_KEY, p).get_string('binding'))
                for p in paths if not _owned_fallback(p)} - {None}
    settings = _extension_settings()
    for key, default in zip(SHORTCUT_KEYS, DEFAULT_SHORTCUTS):
        bindings = settings.get_strv(key) if settings else [default]
        binding = bindings[0] if bindings else ''
        path = _fallback_path(key)
        if path in paths and not _owned_fallback(path):
            continue
        entry = Gio.Settings.new_with_path(CUSTOM_KEY, path)
        if not _normalize(binding) or _normalize(binding) in occupied:
            if path in paths:
                entry.set_string('binding', '')
                paths.remove(path)
            continue
        entry.set_string('name', 'Glint (session fallback)')
        entry.set_string('command', f'{shlex.quote(_launcher())} {FALLBACK_COMMANDS[key]}')
        entry.set_string('binding', binding)
        occupied.add(_normalize(binding))
        if path not in paths:
            paths.append(path)
    base.set_strv('custom-keybindings', paths)
    Gio.Settings.sync()


def remove_fallback_shortcuts():
    paths = _custom_paths()
    owned = [path for path in paths if _owned_fallback(path)]
    if not owned:
        return False
    for path in owned:
        Gio.Settings.new_with_path(CUSTOM_KEY, path).set_string('binding', '')
    Gio.Settings.new(MEDIA_KEYS).set_strv('custom-keybindings', [p for p in paths if p not in owned])
    Gio.Settings.sync()
    return True


def conflicts():
    """Custom shortcuts that use one of Glint's keys."""
    ours = {_normalize(a) for a in glint_shortcuts()} - {None}
    found = []
    for path in _custom_paths():
        if _owned_fallback(path):
            continue
        entry = Gio.Settings.new_with_path(CUSTOM_KEY, path)
        binding = entry.get_string('binding')
        if binding and _normalize(binding) in ours:
            found.append({'path': path, 'binding': binding,
                          'name': entry.get_string('name'), 'command': entry.get_string('command')})
    return found


def _backup_path():
    return os.path.join(config_dir(), 'hotkeys-backup.json')


def take_over():
    """Clear clashing custom shortcuts, appending them to the backup."""
    found = conflicts()
    if not found:
        return []
    try:
        with open(_backup_path(), encoding='utf-8') as f:
            saved = json.load(f)
    except (OSError, ValueError):
        saved = []
    known = {item['path'] for item in saved}
    saved += [item for item in found if item['path'] not in known]
    with open(_backup_path(), 'w', encoding='utf-8') as f:
        json.dump(saved, f, ensure_ascii=False, indent=2)
    for item in found:
        Gio.Settings.new_with_path(CUSTOM_KEY, item['path']).set_string('binding', '')
    Gio.Settings.sync()
    return found


def restore():
    try:
        with open(_backup_path(), encoding='utf-8') as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return []
    remove_fallback_shortcuts()
    existing = set(_custom_paths())
    restored = []
    for item in saved:
        if item['path'] in existing:
            Gio.Settings.new_with_path(CUSTOM_KEY, item['path']).set_string('binding', item['binding'])
            restored.append(item)
    Gio.Settings.sync()
    os.replace(_backup_path(), _backup_path() + '.restored')
    if extension_status() != 'enabled' and not State().get('extension-enabled'):
        install_fallback_shortcuts()
    return restored
