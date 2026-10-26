// Glint - shell half.
//
// Snipaste-style tools launched from a gsd-media-keys custom shortcut are slow
// (process start + portal round trip) and never see Shell popups, because
// custom shortcuts are filtered while a menu holds its grab. Glint instead
// grabs the stage from inside the compositor the moment the key is pressed,
// freezes that frame on screen, and hands lossless image tiles to the resident
// GTK editor. Wayland clients cannot keep themselves above other windows or
// choose their position, so the extension does that for Glint's windows.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as BoxPointer from 'resource:///org/gnome/shell/ui/boxpointer.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

Gio._promisify(Shell.Screenshot.prototype, 'screenshot_stage_to_content');
Gio._promisify(Shell.Screenshot, 'composite_to_stream');
Gio._promisify(Gio.DBusConnection.prototype, 'call');
Gio._promisify(Gio.OutputStream.prototype, 'write_bytes_async');
Gio._promisify(Gio.OutputStream.prototype, 'close_async');

const APP_ID = 'io.github.glint.Glint';
const APP_PATH = '/io/github/glint/Glint/Daemon';
const APP_IFACE = 'io.github.glint.Daemon';
const SHELL_PATH = '/io/github/glint/Shell';
const CAPTURE_TILE_SIZE = 1024;

// Must match DIM in app/glint_app/theme.py so the hand-off is seamless.
const DIM = 'rgba(8, 9, 12, 0.5)';

const KEY_MODES = Shell.ActionMode.ALL &
    ~(Shell.ActionMode.LOCK_SCREEN | Shell.ActionMode.UNLOCK_SCREEN |
      Shell.ActionMode.LOGIN_SCREEN);

const SHELL_IFACE = `<node>
  <interface name="io.github.glint.Shell">
    <method name="Capture"><arg type="s" direction="in" name="mode"/></method>
    <method name="Pin"/>
    <method name="TogglePins"/>
    <method name="MovePointer">
      <arg type="d" direction="in" name="x"/><arg type="d" direction="in" name="y"/>
    </method>
    <method name="DebugScreenshot"><arg type="s" direction="in" name="path"/></method>
    <method name="DebugPointer">
      <arg type="d" direction="in" name="x"/><arg type="d" direction="in" name="y"/>
      <arg type="u" direction="in" name="button"/><arg type="i" direction="in" name="state"/>
    </method>
    <method name="DebugKey">
      <arg type="u" direction="in" name="keyval"/><arg type="i" direction="in" name="state"/>
    </method>
    <method name="DebugScroll">
      <arg type="d" direction="in" name="dx"/><arg type="d" direction="in" name="dy"/>
    </method>
    <method name="DebugState"><arg type="s" direction="out" name="state"/></method>
  </interface>
</node>`;

function extentsOf(actor) {
    const r = actor.get_transformed_extents();
    return {x: r.origin.x, y: r.origin.y, width: r.size.width, height: r.size.height};
}

// Client-side menus (xdg_popup / override-redirect) show up as these types.
const POPUP_TYPES = new Set([
    Meta.WindowType.DROPDOWN_MENU, Meta.WindowType.POPUP_MENU,
    Meta.WindowType.MENU, Meta.WindowType.COMBO,
]);

function nowUs() {
    return GLib.get_monotonic_time();
}

export default class GlintExtension extends Extension {
    enable() {
        this._settings = this.getSettings();
        this._overlays = new Set();
        this._watched = new Map();
        this._busy = false;
        this._freeze = null;
        this._freezeTimeoutId = 0;
        // Only talk to the editor installed alongside this extension:
        // <share>/gnome-shell/extensions/<uuid> pairs with <share>/glint, which
        // is ~/.local/share/glint for a user install and /usr/share/glint for
        // the package.
        const shareDir = [1, 2, 3].reduce(dir => GLib.path_get_dirname(dir), this.path);
        this._trustedDir = `${GLib.build_filenamev([shareDir, 'glint'])}/`;
        this._captureDir = GLib.build_filenamev([GLib.get_user_runtime_dir(), 'glint']);
        GLib.mkdir_with_parents(this._captureDir, 0o700);

        const bind = (name, handler) =>
            Main.wm.addKeybinding(name, this._settings,
                Meta.KeyBindingFlags.IGNORE_AUTOREPEAT, KEY_MODES, handler);
        bind('capture', () => this._capture('edit'));
        bind('capture-copy', () => this._capture('copy'));
        bind('pin', () => this._pin());
        bind('toggle-pins', () => this._callApp('TogglePins', null).catch(logError));

        this._windowCreatedId = global.display.connect('window-created',
            (_d, win) => this._watchWindow(win));
        for (const actor of global.get_window_actors())
            this._watchWindow(actor.meta_window);

        this._dbus = Gio.DBusExportedObject.wrapJSObject(SHELL_IFACE, this);
        this._dbus.export(Gio.DBus.session, SHELL_PATH);

        this._indicatorId = this._settings.connect('changed', (_s, key) => {
            // The menu shows the accelerators, so rebuild it when they change.
            if (key !== 'show-indicator' && key !== 'debug-mode') {
                this._indicator?.destroy();
                this._indicator = null;
            }
            this._syncIndicator();
        });
        this._syncIndicator();
    }

    disable() {
        for (const name of ['capture', 'capture-copy', 'pin', 'toggle-pins'])
            Main.wm.removeKeybinding(name);

        global.display.disconnect(this._windowCreatedId);
        for (const [win, ids] of this._watched) {
            for (const id of ids)
                win.disconnect(id);
        }
        this._watched = null;
        this._overlays = null;

        this._dbus.unexport();
        this._dbus = null;

        this._settings.disconnect(this._indicatorId);
        this._indicator?.destroy();
        this._indicator = null;

        this._hideFreeze();
        this._virtualPointer = null;
        this._virtualKeyboard = null;
        this._popupKeyboard = null;
        this._settings = null;
    }

    // ---- capture -------------------------------------------------------

    async _capture(mode) {
        if (this._busy || this._overlays.size > 0)
            return;
        this._busy = true;
        const t0 = nowUs();
        let path = null;
        try {
            const shooter = new Shell.Screenshot();
            const [content] = await shooter.screenshot_stage_to_content();
            const tGrab = nowUs();

            // Collect geometry before anything on screen changes.
            const meta = this._collectMeta(content.get_texture());
            const popups = this._clientPopups();
            this._showFreeze(content);
            this._dismissShellPopups();
            // An open client menu keeps the pointer grabbed and would swallow
            // the first drag in the editor. Close it under the frozen frame,
            // and wait for it so the Escape cannot reach the editor instead.
            const popupsGone = this._dismissClientPopups(popups);

            path = GLib.build_filenamev([this._captureDir,
                `capture-${GLib.get_real_time()}.tiles`]);
            meta.tiles = await this._writeCapture(content.get_texture(), path);
            meta.version = 2;
            const tEncode = nowUs();

            await popupsGone;
            meta.timing = {grabMs: (tGrab - t0) / 1000, encodeMs: (tEncode - tGrab) / 1000};
            await this._callApp('Capture',
                new GLib.Variant('(sss)', [mode, path, JSON.stringify(meta)]));
            console.log(`glint: capture grab ${meta.timing.grabMs.toFixed(1)} ms, ` +
                `encode ${meta.timing.encodeMs.toFixed(1)} ms, ` +
                `handoff ${((nowUs() - tEncode) / 1000).toFixed(1)} ms`);
        } catch (e) {
            if (path) {
                try {
                    Gio.File.new_for_path(path).delete(null);
                } catch {
                    // The editor may already have consumed the capture.
                }
            }
            this._hideFreeze();
            logError(e, 'glint: capture failed');
            Main.notifyError('Glint', `截图失败：${e.message}`);
        } finally {
            this._busy = false;
        }
    }

    async _writeCapture(texture, path) {
        const width = texture.get_width();
        const height = texture.get_height();
        const tiles = [];
        for (let y = 0; y < height; y += CAPTURE_TILE_SIZE) {
            for (let x = 0; x < width; x += CAPTURE_TILE_SIZE) {
                tiles.push({
                    x, y,
                    width: Math.min(CAPTURE_TILE_SIZE, width - x),
                    height: Math.min(CAPTURE_TILE_SIZE, height - y),
                });
            }
        }

        // Shell's PNG encoder has no compression setting. Small lossless
        // tiles let its worker pool encode in parallel instead of holding
        // up selection on one full-desktop PNG.
        const encoded = new Array(tiles.length);
        let next = 0;
        const workers = Array.from({length: Math.min(8, tiles.length)}, async () => {
            while (next < tiles.length) {
                const i = next++;
                const tile = tiles[i];
                const buffer = Gio.MemoryOutputStream.new_resizable();
                try {
                    await Shell.Screenshot.composite_to_stream(texture,
                        tile.x, tile.y, tile.width, tile.height, 1, null, 0, 0, 1, buffer);
                } finally {
                    buffer.close(null);
                }
                encoded[i] = buffer.steal_as_bytes();
                tile.length = encoded[i].get_size();
            }
        });
        const results = await Promise.allSettled(workers);
        const failed = results.find(result => result.status === 'rejected');
        if (failed)
            throw failed.reason;

        // A single private file keeps hand-off and cleanup atomic for the editor.
        const stream = Gio.File.new_for_path(path).replace(null, false,
            Gio.FileCreateFlags.PRIVATE | Gio.FileCreateFlags.REPLACE_DESTINATION, null);
        try {
            for (const bytes of encoded) {
                let offset = 0;
                while (offset < bytes.get_size()) {
                    const remaining = offset
                        ? GLib.Bytes.new_from_bytes(bytes, offset, bytes.get_size() - offset)
                        : bytes;
                    const written = await stream.write_bytes_async(remaining, GLib.PRIORITY_DEFAULT, null);
                    if (written <= 0)
                        throw new Error('无法写入截图');
                    offset += written;
                }
            }
        } finally {
            await stream.close_async(GLib.PRIORITY_DEFAULT, null);
        }
        return tiles;
    }

    _collectMeta(texture) {
        const targets = [];
        const push = (kind, label, r) => {
            if (!r || r.width < 6 || r.height < 6)
                return;
            targets.push({
                kind, label,
                x: Math.round(r.x), y: Math.round(r.y),
                w: Math.round(r.width), h: Math.round(r.height),
            });
        };

        // Shell menus first: they sit above every window.
        for (const child of Main.layoutManager.uiGroup.get_children()) {
            if (child instanceof BoxPointer.BoxPointer && child.visible && child.is_mapped())
                push('menu', '菜单', extentsOf(child.bin));
        }

        const ws = global.workspace_manager.get_active_workspace();
        const tracker = Shell.WindowTracker.get_default();
        const windows = global.display.sort_windows_by_stacking(
            global.display.list_all_windows()).reverse();
        for (const win of windows) {
            if (win.minimized || win.get_window_type() === Meta.WindowType.DESKTOP)
                continue;
            if (!win.is_on_all_workspaces() && !win.located_on_workspace(ws))
                continue;
            if (!win.get_compositor_private()?.visible || this._overlays.has(win))
                continue;
            if (POPUP_TYPES.has(win.get_window_type())) {
                push('menu', '菜单', win.get_frame_rect());
                continue;
            }
            const app = tracker.get_window_app(win);
            // Window-backed apps (no .desktop file) report their app id as
            // the name; the window title reads better there.
            const appName = app && !app.is_window_backed() ? app.get_name() : '';
            const label = appName || win.get_title() || win.get_wm_class() || '';
            push('window', label, win.get_frame_rect());
        }

        // Top bar, dock and other chrome sit below popups but above windows;
        // listing them after windows keeps window hits preferred where the
        // chrome does not actually cover the window.
        const chrome = [];
        for (const tracked of Main.layoutManager._trackedActors ?? []) {
            const actor = tracked.actor;
            if (actor?.visible && actor.is_mapped())
                chrome.push({kind: 'chrome', actor});
        }
        for (const {actor} of chrome)
            push('chrome', actor === Main.layoutManager.panelBox ? '顶栏' : '面板', extentsOf(actor));

        const [px, py] = global.get_pointer();
        return {
            version: 1,
            stage: {width: global.stage.width, height: global.stage.height},
            image: {width: texture.get_width(), height: texture.get_height()},
            monitors: Main.layoutManager.monitors.map(m => ({
                index: m.index, x: m.x, y: m.y, width: m.width, height: m.height,
                scale: m.geometry_scale,
            })),
            pointer: {x: px, y: py},
            targets,
        };
    }

    _dismissShellPopups() {
        for (const child of Main.layoutManager.uiGroup.get_children()) {
            if (child instanceof BoxPointer.BoxPointer && child.visible)
                child._delegate?.close?.(BoxPointer.PopupAnimation.NONE);
        }
        if (Main.overview.visible)
            Main.overview.hide();
    }

    _clientPopups() {
        return global.display.list_all_windows().filter(win =>
            POPUP_TYPES.has(win.get_window_type()) &&
            win.get_compositor_private()?.visible && !this._overlays.has(win));
    }

    _dismissClientPopups(popups) {
        if (popups.length === 0)
            return Promise.resolve();
        this._popupKeyboard ??= this._seat().create_virtual_device(
            Clutter.InputDeviceType.KEYBOARD_DEVICE);
        // One Escape per level closes nested menus from the inside out.
        for (let i = 0; i < popups.length; i++) {
            this._popupKeyboard.notify_keyval(nowUs(), Clutter.KEY_Escape, Clutter.KeyState.PRESSED);
            this._popupKeyboard.notify_keyval(nowUs(), Clutter.KEY_Escape, Clutter.KeyState.RELEASED);
        }
        const gone = popups.map(win => new Promise(resolve => {
            const id = win.connect('unmanaged', () => {
                win.disconnect(id);
                resolve();
            });
        }));
        const timeout = new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, 250, () => {
            resolve();
            return GLib.SOURCE_REMOVE;
        }));
        return Promise.race([Promise.all(gone), timeout]);
    }

    _showFreeze(content) {
        this._hideFreeze();
        const {width, height} = global.stage;
        // Reactive so a press made before the editor is up (a fast F1 +
        // drag) lands here instead of dragging a window under the frozen
        // frame. Deliberately no modal grab: the Escape that closes client
        // menus has to reach them.
        const group = new Clutter.Actor({reactive: true, width, height});
        group.connect('event', () => Clutter.EVENT_STOP);
        group.add_child(new Clutter.Actor({content, width, height}));
        group.add_child(new St.Widget({style: `background-color: ${DIM};`, width, height}));
        Main.layoutManager.uiGroup.add_child(group);
        Main.layoutManager.uiGroup.set_child_above_sibling(group, null);
        this._freeze = group;
        // Safety net in case the editor never shows up.
        this._freezeTimeoutId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 6000, () => {
            this._freezeTimeoutId = 0;
            this._hideFreeze();
            return GLib.SOURCE_REMOVE;
        });
    }

    _hideFreeze() {
        if (this._freezeTimeoutId)
            GLib.source_remove(this._freezeTimeoutId);
        this._freezeTimeoutId = 0;
        this._freeze?.destroy();
        this._freeze = null;
    }

    // Wayland only lets the focused client read the clipboard, and the
    // resident editor is never focused when F3 is pressed, so the shell reads
    // it and hands the content over.
    async _pin() {
        try {
            const clipboard = St.Clipboard.get_default();
            const types = clipboard.get_mimetypes(St.ClipboardType.CLIPBOARD) ?? [];
            const image = ['image/png', 'image/jpeg', 'image/webp', 'image/bmp', 'image/tiff']
                .find(t => types.includes(t));
            if (image) {
                const bytes = await new Promise(resolve =>
                    clipboard.get_content(St.ClipboardType.CLIPBOARD, image, (_c, b) => {
                        // St releases these GBytes when the callback returns.
                        resolve(b ? new Uint8Array(b.toArray()) : null);
                    }));
                if (bytes?.length) {
                    const path = GLib.build_filenamev([this._captureDir,
                        `pin-${GLib.get_real_time()}.${image.split('/')[1]}`]);
                    const file = Gio.File.new_for_path(path);
                    file.replace_contents(bytes, null, false,
                        Gio.FileCreateFlags.PRIVATE | Gio.FileCreateFlags.REPLACE_DESTINATION, null);
                    await this._callApp('PinImage', new GLib.Variant('(s)', [path]));
                    return;
                }
            }
            const text = await new Promise(resolve =>
                clipboard.get_text(St.ClipboardType.CLIPBOARD, (_c, t) => resolve(t)));
            if (text?.trim())
                await this._callApp('PinText', new GLib.Variant('(s)', [text]));
            else
                await this._callApp('PinLast', null);
        } catch (e) {
            logError(e, 'glint: pin failed');
        }
    }

    // ---- talking to the editor ----------------------------------------

    async _ownerPid() {
        const reply = await Gio.DBus.session.call('org.freedesktop.DBus',
            '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'GetConnectionUnixProcessID',
            new GLib.Variant('(s)', [APP_ID]), new GLib.VariantType('(u)'),
            Gio.DBusCallFlags.NONE, -1, null);
        return reply.deepUnpack()[0];
    }

    _waitForOwner(timeoutMs) {
        return new Promise((resolve, reject) => {
            let timeoutId = 0;
            const watchId = Gio.bus_watch_name(Gio.BusType.SESSION, APP_ID,
                Gio.BusNameWatcherFlags.NONE, () => {
                    GLib.source_remove(timeoutId);
                    Gio.bus_unwatch_name(watchId);
                    resolve();
                }, null);
            timeoutId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, timeoutMs, () => {
                Gio.bus_unwatch_name(watchId);
                reject(new Error('Glint 编辑器未能启动'));
                return GLib.SOURCE_REMOVE;
            });
        });
    }

    // Screenshots are handed to whoever owns APP_ID, so make sure that is our
    // own installed editor and not some other process squatting the name.
    async _ensureApp() {
        let pid;
        try {
            pid = await this._ownerPid();
        } catch {
            const launcher = `${this._trustedDir}glint`;
            Gio.Subprocess.new([launcher, '--daemon'], Gio.SubprocessFlags.NONE);
            await this._waitForOwner(8000);
            pid = await this._ownerPid();
        }
        const [, bytes] = GLib.file_get_contents(`/proc/${pid}/cmdline`);
        const argv = new TextDecoder().decode(bytes).split('\0');
        if (!argv.some(arg => arg.startsWith(this._trustedDir)))
            throw new Error(`${APP_ID} 被未知进程占用：${argv.join(' ')}`);
    }

    async _callApp(method, params) {
        await this._ensureApp();
        await Gio.DBus.session.call(APP_ID, APP_PATH, APP_IFACE, method, params,
            null, Gio.DBusCallFlags.NO_AUTO_START, 10000, null);
    }

    // ---- managing Glint's own windows --------------------------------

    _watchWindow(win) {
        if (!win || this._watched.has(win))
            return;
        const ids = [];
        const check = () => this._classify(win);
        ids.push(win.connect('notify::title', check));
        ids.push(win.connect('notify::wm-class', check));
        ids.push(win.connect('notify::gtk-application-id', check));
        ids.push(win.connect('shown', check));
        ids.push(win.connect('unmanaged', () => {
            for (const id of ids)
                win.disconnect(id);
            this._watched?.delete(win);
            this._overlays?.delete(win);
        }));
        this._watched.set(win, ids);
        check();
    }

    _classify(win) {
        if (win._glintRole)
            return;
        const appId = win.get_gtk_application_id?.() || win.get_wm_class();
        const title = win.get_title() ?? '';
        if (appId !== APP_ID || !title.startsWith('glint-'))
            return;
        const actor = win.get_compositor_private();
        if (!actor)
            return;

        if (title.startsWith('glint-overlay')) {
            win._glintRole = 'overlay';
            this._overlays.add(win);
            Main.wm.skipNextEffect(actor);
            const id = actor.connect('first-frame', () => {
                actor.disconnect(id);
                // Wait for the frame to actually reach the screen.
                GLib.timeout_add(GLib.PRIORITY_DEFAULT, 16, () => {
                    if (this._overlays?.has(win)) {
                        win.make_above();
                        this._hideFreeze();
                    }
                    return GLib.SOURCE_REMOVE;
                });
            });
        } else if (title.startsWith('glint-pin')) {
            win._glintRole = 'pin';
            Main.wm.skipNextEffect(actor);
            actor.opacity = 0;
            const place = () => {
                const m = title.match(/@(-?\d+),(-?\d+)/);
                const frame = win.get_frame_rect();
                let x, y;
                if (m) {
                    [x, y] = [parseInt(m[1]), parseInt(m[2])];
                } else {
                    const [px, py] = global.get_pointer();
                    [x, y] = [px - frame.width / 2, py - frame.height / 2];
                }
                win.make_above();
                win.stick();
                win.move_frame(true, Math.round(x), Math.round(y));
                actor.opacity = 255;
            };
            if (win.get_compositor_private()?.visible && win.showing_on_its_workspace())
                place();
            else
                this._once(win, 'shown', place);
        }
    }

    _once(obj, signal, fn) {
        const id = obj.connect(signal, (...args) => {
            obj.disconnect(id);
            fn(...args);
        });
    }

    // ---- indicator -----------------------------------------------------

    _syncIndicator() {
        const want = this._settings.get_boolean('show-indicator');
        if (want && !this._indicator) {
            this._indicator = this._buildIndicator();
            Main.panel.addToStatusArea(this.uuid, this._indicator);
        } else if (!want && this._indicator) {
            this._indicator.destroy();
            this._indicator = null;
        }
    }

    _buildIndicator() {
        const button = new PanelMenu.Button(0.5, 'Glint', false);
        button.add_child(new St.Icon({
            gicon: Gio.icon_new_for_string(`${this.path}/icons/glint-symbolic.svg`),
            style_class: 'system-status-icon',
        }));

        const accel = key => {
            const value = this._settings.get_strv(key)[0] ?? '';
            return value.replace(/<Control>/gi, 'Ctrl+').replace(/<Shift>/gi, 'Shift+')
                .replace(/<Alt>/gi, 'Alt+').replace(/<Super>/gi, 'Super+');
        };
        const item = (label, key, action) => {
            const menuItem = new PopupMenu.PopupMenuItem(label);
            if (key) {
                menuItem.add_child(new St.Label({
                    text: accel(key), x_expand: true, x_align: Clutter.ActorAlign.END,
                    style: 'color: rgba(255,255,255,0.45); font-feature-settings: "tnum";',
                }));
            }
            menuItem.connect('activate', action);
            button.menu.addMenuItem(menuItem);
        };
        // Let the menu close before the stage is grabbed.
        const later = fn => () => GLib.timeout_add(GLib.PRIORITY_DEFAULT, 180, () => {
            fn();
            return GLib.SOURCE_REMOVE;
        });
        item('截图', 'capture', later(() => this._capture('edit')));
        item('截图并复制', 'capture-copy', later(() => this._capture('copy')));
        item('贴图', 'pin', () => this._pin());
        item('隐藏 / 显示贴图', 'toggle-pins',
            () => this._callApp('TogglePins', null).catch(logError));
        button.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        item('打开截图文件夹', null,
            () => this._callApp('OpenFolder', null).catch(logError));
        item('设置', null, () => this.openPreferences());
        return button;
    }

    // ---- D-Bus methods ------------------------------------------------

    Capture(mode) {
        this._capture(mode === 'copy' ? 'copy' : 'edit');
    }

    Pin() {
        this._pin();
    }

    TogglePins() {
        this._callApp('TogglePins', null).catch(logError);
    }

    // Pixel-precise pointer steps (W A S D in the editor). Wayland clients
    // cannot warp the pointer, so the editor asks; only our own editor may,
    // and only while it has a capture open.
    async MovePointerAsync([x, y], invocation) {
        try {
            if (!this._overlays?.size)
                throw new Error('no capture open');
            const reply = await Gio.DBus.session.call('org.freedesktop.DBus',
                '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'GetNameOwner',
                new GLib.Variant('(s)', [APP_ID]), new GLib.VariantType('(s)'),
                Gio.DBusCallFlags.NONE, -1, null);
            if (reply.deepUnpack()[0] !== invocation.get_sender())
                throw new Error('caller is not the Glint editor');
            await this._ensureApp();
            x = Math.min(Math.max(0, x), global.stage.width - 1);
            y = Math.min(Math.max(0, y), global.stage.height - 1);
            this._virtualPointer ??= this._seat().create_virtual_device(
                Clutter.InputDeviceType.POINTER_DEVICE);
            this._virtualPointer.notify_absolute_motion(nowUs(), x, y);
            invocation.return_value(null);
        } catch (e) {
            invocation.return_error_literal(Gio.DBusError, Gio.DBusError.ACCESS_DENIED, e.message);
        }
    }

    _requireDebug() {
        if (!this._settings.get_boolean('debug-mode'))
            throw new Error('debug-mode is disabled');
    }

    DebugState() {
        this._requireDebug();
        return JSON.stringify({
            busy: this._busy,
            frozen: this._freeze !== null,
            overlays: this._overlays.size,
            stage: {width: global.stage.width, height: global.stage.height},
            focus: global.display.focus_window?.get_title() ?? null,
            indicator: this._indicator?.is_mapped() ? extentsOf(this._indicator) : null,
            menu: this._indicator?.menu.isOpen ? extentsOf(this._indicator.menu.actor) : null,
        });
    }

    async DebugScreenshotAsync([path], invocation) {
        try {
            this._requireDebug();
            const shooter = new Shell.Screenshot();
            const [content] = await shooter.screenshot_stage_to_content();
            const stream = Gio.File.new_for_path(path).replace(null, false,
                Gio.FileCreateFlags.REPLACE_DESTINATION, null);
            await Shell.Screenshot.composite_to_stream(content.get_texture(),
                0, 0, -1, -1, 1, null, 0, 0, 1, stream);
            stream.close(null);
            invocation.return_value(null);
        } catch (e) {
            invocation.return_error_literal(Gio.DBusError, Gio.DBusError.FAILED, e.message);
        }
    }

    _seat() {
        return Clutter.get_default_backend().get_default_seat();
    }

    DebugPointer(x, y, button, state) {
        this._requireDebug();
        this._virtualPointer ??= this._seat().create_virtual_device(
            Clutter.InputDeviceType.POINTER_DEVICE);
        this._virtualPointer.notify_absolute_motion(nowUs(), x, y);
        if (button) {
            this._virtualPointer.notify_button(nowUs(), button,
                state ? Clutter.ButtonState.PRESSED : Clutter.ButtonState.RELEASED);
        }
    }

    DebugScroll(dx, dy) {
        this._requireDebug();
        this._virtualPointer ??= this._seat().create_virtual_device(
            Clutter.InputDeviceType.POINTER_DEVICE);
        this._virtualPointer.notify_scroll_continuous(nowUs(), dx, dy,
            Clutter.ScrollSource.WHEEL, Clutter.ScrollFinishFlags.NONE);
    }

    DebugKey(keyval, state) {
        this._requireDebug();
        this._virtualKeyboard ??= this._seat().create_virtual_device(
            Clutter.InputDeviceType.KEYBOARD_DEVICE);
        this._virtualKeyboard.notify_keyval(nowUs(), keyval,
            state ? Clutter.KeyState.PRESSED : Clutter.KeyState.RELEASED);
    }
}
