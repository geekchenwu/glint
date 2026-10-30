// Lumen preferences: shortcuts, top bar indicator, shortcut clashes.
import Adw from 'gi://Adw';
import Gdk from 'gi://Gdk';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Gtk from 'gi://Gtk';

import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

const SHORTCUTS = [
    ['capture', '截图', '框选、标注，然后复制 / 保存 / 贴图'],
    ['capture-copy', '截图并复制', '选好区域直接进剪贴板'],
    ['pin', '贴图', '把剪贴板里的图片或文字贴到屏幕上'],
    ['toggle-pins', '隐藏 / 显示贴图', '一键收起全部贴图'],
];

// Keys that are fine on their own; everything else needs a modifier.
const BARE_OK = /^(F\d+|Print|Pause|Scroll_Lock|Insert|XF86\w+)$/;

function shortcutDialog(parent, title, onDone) {
    const dialog = new Adw.AlertDialog({
        heading: `设置「${title}」快捷键`,
        body: '按下新的组合键。Esc 取消，Backspace 清除。',
    });
    dialog.add_response('cancel', '取消');
    const keys = new Gtk.EventControllerKey();
    keys.connect('key-pressed', (_c, keyval, keycode, state) => {
        const mods = state & Gtk.accelerator_get_default_mod_mask();
        const name = Gdk.keyval_name(keyval) ?? '';
        if (/^(Shift|Control|Alt|Super|Meta|Hyper|ISO_Level3)_/.test(name))
            return Gdk.EVENT_STOP;
        if (!mods && keyval === Gdk.KEY_Escape) {
            dialog.close();
            return Gdk.EVENT_STOP;
        }
        if (!mods && keyval === Gdk.KEY_BackSpace) {
            onDone([]);
            dialog.close();
            return Gdk.EVENT_STOP;
        }
        if (!mods && !BARE_OK.test(name)) {
            dialog.body = `「${name}」需要配合 Ctrl / Alt / Super 使用。`;
            return Gdk.EVENT_STOP;
        }
        const accel = Gtk.accelerator_name_with_keycode(null, keyval, keycode, mods);
        onDone([accel]);
        dialog.close();
        return Gdk.EVENT_STOP;
    });
    dialog.add_controller(keys);
    dialog.present(parent);
}

export default class LumenPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings();
        // Shell keeps session metadata cached when an installed extension changes.
        const [, metadataBytes] = this.dir.get_child('metadata.json').load_contents(null);
        const installedMetadata = JSON.parse(new TextDecoder().decode(metadataBytes));
        window.set_default_size(560, 720);
        window.set_search_enabled(false);

        const page = new Adw.PreferencesPage({title: '鹿门', icon_name: 'io.github.lumen.Lumen-symbolic'});
        window.add(page);

        // Header.
        const hero = new Adw.PreferencesGroup();
        const box = new Gtk.Box({
            orientation: Gtk.Orientation.VERTICAL, spacing: 6,
            margin_top: 12, margin_bottom: 6, halign: Gtk.Align.CENTER,
        });
        const icon = new Gtk.Image({pixel_size: 112});
        const themeIcons = Gtk.IconTheme.get_for_display(Gdk.Display.get_default());
        if (themeIcons.has_icon('io.github.lumen.Lumen'))
            icon.icon_name = 'io.github.lumen.Lumen';
        else
            icon.gicon = Gio.icon_new_for_string(`${this.path}/icons/lumen.svg`);
        box.append(icon);
        box.append(new Gtk.Label({label: '鹿门 Lumen', css_classes: ['title-1']}));
        box.append(new Gtk.Label({
            label: '在 GNOME Shell 内部完成截图：零延迟，菜单与弹窗都能截到。',
            css_classes: ['dim-label'], wrap: true, justify: Gtk.Justification.CENTER,
        }));
        hero.add(box);
        page.add(hero);

        // Shortcuts.
        const keys = new Adw.PreferencesGroup({title: '快捷键'});
        const reset = new Gtk.Button({
            icon_name: 'edit-undo-symbolic', tooltip_text: '恢复默认快捷键',
            valign: Gtk.Align.CENTER, css_classes: ['flat'],
        });
        reset.connect('clicked', () => SHORTCUTS.forEach(([key]) => settings.reset(key)));
        keys.set_header_suffix(reset);
        for (const [key, title, subtitle] of SHORTCUTS) {
            const row = new Adw.ActionRow({title, subtitle, activatable: true});
            const label = new Gtk.ShortcutLabel({
                disabled_text: '未设置', valign: Gtk.Align.CENTER,
            });
            const sync = () => {
                label.accelerator = settings.get_strv(key)[0] ?? '';
            };
            sync();
            settings.connect(`changed::${key}`, sync);
            row.add_suffix(label);
            row.connect('activated', () =>
                shortcutDialog(window, title, value => settings.set_strv(key, value)));
            keys.add(row);
        }
        page.add(keys);

        // Shortcut clashes with Snipaste and friends.
        const lumen = GLib.find_program_in_path('lumen');
        if (lumen) {
            const clash = new Adw.PreferencesGroup({
                title: '快捷键冲突',
                description: '其他截图工具（例如 Snipaste）的自定义快捷键会和鹿门抢同一个键。',
            });
            const takeRow = new Adw.ActionRow({
                title: '把冲突的快捷键交给鹿门',
                subtitle: '停用占用相同按键的自定义快捷键，原设置会备份',
            });
            const restoreRow = new Adw.ActionRow({
                title: '恢复原来的快捷键',
                subtitle: '撤销上一步，重新启用被停用的快捷键',
            });
            const run = (args, done) => {
                const proc = Gio.Subprocess.new([lumen, ...args],
                    Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE);
                proc.communicate_utf8_async(null, null, (p, res) => {
                    const [, out] = p.communicate_utf8_finish(res);
                    window.add_toast(new Adw.Toast({title: done(out.trim()), timeout: 4}));
                });
            };
            const takeButton = new Gtk.Button({label: '接管', valign: Gtk.Align.CENTER,
                css_classes: ['suggested-action']});
            takeButton.connect('clicked', () => run(['hotkeys', 'take-over'],
                out => out.split('\n')[0] || '完成'));
            const restoreButton = new Gtk.Button({label: '恢复', valign: Gtk.Align.CENTER});
            restoreButton.connect('clicked', () => run(['hotkeys', 'restore'],
                out => out.split('\n')[0] || '完成'));
            takeRow.add_suffix(takeButton);
            restoreRow.add_suffix(restoreButton);
            clash.add(takeRow);
            clash.add(restoreRow);
            page.add(clash);
        }

        // Appearance and files.
        const misc = new Adw.PreferencesGroup({title: '其他'});
        const indicator = new Adw.SwitchRow({
            title: '顶栏图标', subtitle: '在顶栏显示鹿门菜单',
        });
        settings.bind('show-indicator', indicator, 'active', Gio.SettingsBindFlags.DEFAULT);
        misc.add(indicator);
        const pictures = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES) ??
            GLib.build_filenamev([GLib.get_home_dir(), 'Pictures']);
        const folder = GLib.build_filenamev([pictures, 'Screenshots']);
        const folderRow = new Adw.ActionRow({
            title: '保存位置', subtitle: folder.replace(GLib.get_home_dir(), '~'),
            activatable: true,
        });
        folderRow.add_suffix(new Gtk.Image({icon_name: 'folder-open-symbolic'}));
        folderRow.connect('activated', () => {
            GLib.mkdir_with_parents(folder, 0o755);
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(folder).get_uri(), null);
        });
        misc.add(folderRow);
        page.add(misc);

        const about = new Adw.PreferencesGroup({
            title: '关于鹿门',
            description: `版本 ${installedMetadata['version-name'] ?? installedMetadata.version}`,
        });
        const email = installedMetadata['contact-email'];
        const contact = new Adw.ActionRow({title: '作者联系', subtitle: email});
        const copyEmail = new Gtk.Button({
            icon_name: 'edit-copy-symbolic', tooltip_text: '复制作者邮箱',
            valign: Gtk.Align.CENTER, css_classes: ['flat'],
        });
        copyEmail.connect('clicked', () => {
            const value = new GObject.Value();
            value.init(GObject.TYPE_STRING);
            value.set_string(email);
            Gdk.Display.get_default().get_clipboard().set_content(
                Gdk.ContentProvider.new_for_value(value));
            window.add_toast(new Adw.Toast({title: '作者邮箱已复制', timeout: 3}));
        });
        const sendEmail = new Gtk.LinkButton({
            label: '发送邮件', uri: `mailto:${email}`, valign: Gtk.Align.CENTER,
        });
        contact.add_suffix(copyEmail);
        contact.add_suffix(sendEmail);
        contact.set_activatable_widget(sendEmail);
        about.add(contact);
        page.add(about);
    }
}
