import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const IFACE = `<node><interface name="io.github.glint.TestSession">
  <method name="DebugState"><arg type="s" direction="out"/></method>
  <method name="DebugPointer">
    <arg type="d" direction="in"/><arg type="d" direction="in"/>
    <arg type="u" direction="in"/><arg type="i" direction="in"/>
  </method>
  <method name="DebugKey"><arg type="u" direction="in"/><arg type="i" direction="in"/></method>
  <method name="DebugScreenshot"><arg type="s" direction="in"/></method>
</interface></node>`;

export default class SessionDriver extends Extension {
    enable() {
        this._painted = new WeakSet();
        this._created = global.window_manager.connect('map', (_manager, actor) => {
            const window = actor.meta_window;
            const frame = actor.connect('first-frame', () => {
                actor.disconnect(frame);
                GLib.timeout_add(GLib.PRIORITY_DEFAULT, 16, () => {
                    this._painted?.add(window);
                    return GLib.SOURCE_REMOVE;
                });
            });
        });
        this._bus = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._bus.export(Gio.DBus.session, '/io/github/glint/TestSession');
    }

    disable() {
        global.window_manager.disconnect(this._created);
        this._painted = null;
        this._bus.unexport();
        this._pointer = this._keyboard = null;
    }

    DebugState() {
        const windows = global.get_window_actors().map(actor => actor.meta_window);
        const labels = [];
        const visit = actor => {
            const text = actor.get_text?.();
            if (text && actor.is_mapped()) {
                const [x, y] = actor.get_transformed_position();
                const [width, height] = actor.get_transformed_size();
                labels.push({text, x, y, width, height});
            }
            for (const child of actor.get_children())
                visit(child);
        };
        visit(global.stage);
        return JSON.stringify({
            focus: global.display.focus_window?.get_title() ?? null,
            windows: windows.map(window => window.get_title()),
            overlays: windows.filter(window => window.get_title() === 'glint-overlay').length,
            paintedOverlays: windows.filter(window =>
                window.get_title() === 'glint-overlay' && this._painted.has(window)).length,
            stage: {width: global.stage.width, height: global.stage.height},
            startingUp: Main.layoutManager._startingUp,
            overview: Main.overview.visible,
            labels,
        });
    }

    DebugPointer(x, y, button, state) {
        this._pointer ??= Clutter.get_default_backend().get_default_seat()
            .create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
        this._pointer.notify_absolute_motion(GLib.get_monotonic_time(), x, y);
        if (button) {
            this._pointer.notify_button(GLib.get_monotonic_time(), button,
                state ? Clutter.ButtonState.PRESSED : Clutter.ButtonState.RELEASED);
        }
    }

    DebugKey(keyval, state) {
        this._keyboard ??= Clutter.get_default_backend().get_default_seat()
            .create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
        this._keyboard.notify_keyval(GLib.get_monotonic_time(), keyval,
            state ? Clutter.KeyState.PRESSED : Clutter.KeyState.RELEASED);
    }

    async DebugScreenshotAsync([path], invocation) {
        try {
            const [content] = await new Shell.Screenshot().screenshot_stage_to_content();
            const stream = Gio.File.new_for_path(path).replace(null, false,
                Gio.FileCreateFlags.REPLACE_DESTINATION, null);
            await Shell.Screenshot.composite_to_stream(content.get_texture(),
                0, 0, -1, -1, 1, null, 0, 0, 1, stream);
            stream.close(null);
            invocation.return_value(null);
        } catch (error) {
            invocation.return_error_literal(Gio.DBusError, Gio.DBusError.FAILED, error.message);
        }
    }
}
