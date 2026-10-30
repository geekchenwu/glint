"""Shortcut ownership tests with an in-memory settings backend."""
import tempfile
import unittest
from unittest.mock import patch

from gi.repository import Gio

from lumen_app import firstrun


class FirstRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        backend = Gio.memory_settings_backend_new()
        source = Gio.SettingsSchemaSource.get_default()
        new_full = Gio.Settings.new_full

        def settings(schema, path=None):
            return new_full(source.lookup(schema, True), backend, path)

        for target, replacement in (
                ('config_dir', lambda: self.temp.name),
                ('extension_status', lambda: 'unknown'),
                ('_extension_settings', lambda: None)):
            context = patch.object(firstrun, target, replacement)
            context.start()
            self.addCleanup(context.stop)
        for method, replacement in (
                ('new', settings), ('new_with_path', settings)):
            context = patch.object(Gio.Settings, method, replacement)
            context.start()
            self.addCleanup(context.stop)
        self.base = settings(firstrun.MEDIA_KEYS)
        self.foreign = '/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/test-other/'

    def entry(self, path):
        return Gio.Settings.new_with_path(firstrun.CUSTOM_KEY, path)

    def add_foreign(self, binding='F1'):
        self.entry(self.foreign).set_string('name', 'Existing screenshot tool')
        self.entry(self.foreign).set_string('command', 'other-capture')
        self.entry(self.foreign).set_string('binding', binding)
        self.base.set_strv('custom-keybindings', [self.foreign])

    def test_existing_shortcut_is_preserved_and_reported(self):
        self.add_foreign()
        firstrun.prepare_session()
        self.assertEqual(self.entry(self.foreign).get_string('binding'), 'F1')
        self.assertEqual(firstrun.fallback_capture_binding(), '')
        self.assertEqual([item['path'] for item in firstrun.conflicts()], [self.foreign])
        self.assertEqual(len(firstrun._custom_paths()), 4)

    def test_repeated_activation_does_not_duplicate_bindings(self):
        firstrun.prepare_session()
        firstrun.prepare_session()
        self.assertEqual(len(firstrun._custom_paths()), 4)
        self.assertEqual(firstrun.fallback_capture_binding(), 'F1')

    def test_cleanup_preserves_edited_commands_and_foreign_shortcuts(self):
        self.add_foreign('F12')
        firstrun.install_fallback_shortcuts()
        edited = firstrun._fallback_path('pin')
        self.entry(edited).set_string('command', 'my-custom-pin')
        firstrun.remove_fallback_shortcuts()
        self.assertEqual(set(firstrun._custom_paths()), {self.foreign, edited})
        self.assertEqual(self.entry(edited).get_string('binding'), 'F3')

    def test_native_activation_releases_fallback_shortcuts(self):
        firstrun.install_fallback_shortcuts()
        with patch.object(firstrun, 'extension_status', return_value='enabled'):
            self.assertEqual(firstrun.prepare_session(), 'enabled')
        self.assertEqual(firstrun._custom_paths(), [])
        self.assertTrue(firstrun.State().get('extension-enabled'))

    def test_manual_extension_disable_is_respected(self):
        firstrun.State().set('extension-enabled')
        with patch.object(firstrun, 'extension_status', return_value='disabled'), \
                patch.object(firstrun, 'enable_extension') as enable:
            firstrun.prepare_session()
        enable.assert_not_called()
        self.assertEqual(firstrun._custom_paths(), [])

    def test_async_extension_loading_keeps_capture_available(self):
        with patch.object(firstrun, 'extension_status', return_value='disabled'), \
                patch.object(firstrun, 'enable_extension', return_value=True):
            firstrun.prepare_session()
        self.assertEqual(firstrun.fallback_capture_binding(), 'F1')
        self.assertFalse(firstrun.State().get('extension-enabled'))

    def test_native_enable_does_not_reinstall_fallback_during_import(self):
        firstrun.install_fallback_shortcuts()
        with patch.object(firstrun, 'extension_status',
                          side_effect=['disabled', 'disabled', 'enabled']), \
                patch.object(firstrun, 'enable_extension', return_value=True):
            self.assertEqual(firstrun.prepare_session(), 'enabled')
        self.assertEqual(firstrun._custom_paths(), [])
        self.assertTrue(firstrun.State().get('extension-enabled'))

    def test_take_over_and_restore_do_not_leave_competing_bindings(self):
        self.add_foreign()
        firstrun.prepare_session()
        firstrun.take_over()
        firstrun.install_fallback_shortcuts()
        self.assertEqual(firstrun.fallback_capture_binding(), 'F1')
        self.assertEqual(self.entry(self.foreign).get_string('binding'), '')
        firstrun.restore()
        self.assertEqual(firstrun.fallback_capture_binding(), '')
        self.assertEqual(self.entry(self.foreign).get_string('binding'), 'F1')

    def test_disabled_preference_removes_previously_installed_binding(self):
        firstrun.install_fallback_shortcuts()

        class Settings:
            def get_strv(self, key):
                return [] if key == 'capture' else [dict(zip(
                    firstrun.SHORTCUT_KEYS, firstrun.DEFAULT_SHORTCUTS))[key]]

        with patch.object(firstrun, '_extension_settings', return_value=Settings()):
            firstrun.install_fallback_shortcuts()
        self.assertEqual(firstrun.fallback_capture_binding(), '')
        self.assertEqual(len(firstrun._custom_paths()), 3)


if __name__ == '__main__':
    unittest.main()
