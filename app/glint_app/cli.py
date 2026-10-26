import os
import subprocess
import sys
import time

from . import APP_ID, DAEMON_IFACE, DAEMON_PATH, SHELL_IFACE, SHELL_PATH, VERSION

USAGE = '''glint - 截图 / 标注 / 贴图

用法:
  glint              启动常驻编辑器（通常由登录自启或扩展拉起）
  glint capture      截图并标注（等同 F1）
  glint copy         截图后直接复制（等同 Ctrl+F1）
  glint pin          贴图（等同 F3）
  glint toggle-pins  隐藏 / 显示全部贴图（等同 Shift+F3）
  glint activate     安装后立即启用，无需立即注销桌面
  glint setup        启用 GNOME 扩展，并把冲突的快捷键交给 Glint（会备份）
  glint hotkeys restore   恢复被 Glint 接管前的快捷键
  glint --version
'''
COMMANDS = {
    'capture': ('Capture', 'edit'),
    'copy': ('Capture', 'copy'),
    'pin': ('Pin', None),
    'toggle-pins': ('TogglePins', None),
}


def _shell_call(method, arg):
    from gi.repository import Gio, GLib
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    params = GLib.Variant('(s)', (arg,)) if arg is not None else None
    bus.call_sync('org.gnome.Shell', SHELL_PATH, SHELL_IFACE, method, params,
                  None, Gio.DBusCallFlags.NONE, 5000, None)


def _missing_shell(error):
    from gi.repository import Gio
    return any(error.matches(Gio.dbus_error_quark(), code) for code in (
        Gio.DBusError.SERVICE_UNKNOWN, Gio.DBusError.NAME_HAS_NO_OWNER,
        Gio.DBusError.UNKNOWN_OBJECT, Gio.DBusError.UNKNOWN_METHOD,
        Gio.DBusError.UNKNOWN_INTERFACE))


def _ensure_daemon():
    from gi.repository import Gio, GLib
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def running():
        return bus.call_sync(
            'org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
            'NameHasOwner', GLib.Variant('(s)', (APP_ID,)), GLib.VariantType('(b)'),
            Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]

    if not running():
        launcher = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'glint'))
        runtime = os.path.join(GLib.get_user_runtime_dir(), 'glint')
        os.makedirs(runtime, mode=0o700, exist_ok=True)
        log_fd = os.open(os.path.join(runtime, 'daemon.log'),
                         os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(log_fd, 'ab') as log:
            process = subprocess.Popen([sys.executable, launcher, '--daemon'],
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       start_new_session=True)
        deadline = time.monotonic() + 8
        while not running():
            if process.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError(f'Glint 编辑器未能启动，请查看 {runtime}/daemon.log')
            time.sleep(.05)
    return bus


def _app_call(method, arg=None):
    from gi.repository import Gio, GLib
    bus = _ensure_daemon()
    params = GLib.Variant('(s)', (arg,)) if arg is not None else None
    try:
        bus.call_sync(APP_ID, DAEMON_PATH, DAEMON_IFACE, method, params, None,
                      Gio.DBusCallFlags.NONE, 5000, None)
    except GLib.Error as error:
        if error.matches(Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_METHOD):
            raise RuntimeError('旧版 Glint 仍在运行，请保存贴图后退出旧进程并重新打开 Glint。') from error
        raise


def _capture_or_fallback(method, arg):
    from gi.repository import GLib
    from . import firstrun
    try:
        _shell_call(method, arg)
        return
    except GLib.Error as error:
        if not _missing_shell(error):
            raise
    firstrun.prepare_session()
    try:
        _shell_call(method, arg)
        return
    except GLib.Error as error:
        if not _missing_shell(error):
            raise
    fallback = {'Capture': 'PortalCapture', 'Pin': 'PortalPin', 'TogglePins': 'TogglePins'}
    _app_call(fallback[method], arg)


def _describe(items):
    for item in items:
        print(f"  {item['binding']:<14} {item['name'] or item['command']}")


def _setup(args):
    from . import firstrun
    if args[0] == 'hotkeys' and args[1:] == ['restore']:
        restored = firstrun.restore()
        print('已恢复：' if restored else '没有可恢复的备份。')
        _describe(restored)
        return 0
    if args[0] == 'hotkeys' and args[1:] != ['take-over']:
        print(USAGE)
        return 2
    if args[0] == 'setup':
        status = firstrun.prepare_session()
        print({'enabled': 'GNOME 扩展：已启用',
               'disabled': 'Glint：使用系统截图方式，可立即截图',
               'unknown': 'Glint：使用系统截图方式，可立即截图；增强功能将在下次登录自动启用'}[status])
    taken = firstrun.take_over()
    if firstrun.extension_status() != 'enabled':
        firstrun.install_fallback_shortcuts()
    firstrun.State().set('hotkeys-decided')
    print('已接管快捷键（原设置已备份）：' if taken else '没有冲突的快捷键。')
    _describe(taken)
    return 0


def main(argv):
    args = argv[1:]
    if args and args[0] in ('-h', '--help', 'help'):
        print(USAGE)
        return 0
    if args and args[0] in ('-v', '--version'):
        print(f'glint {VERSION}')
        return 0
    if args and args[0] == 'activate':
        from . import firstrun
        try:
            status = firstrun.prepare_session()
            _ensure_daemon()
        except Exception as error:
            print(f'glint: {error}', file=sys.stderr)
            return 1
        print('Glint 已就绪，可从应用菜单打开并截图。' if status == 'enabled' else
              'Glint 已就绪，可从应用菜单打开并截图。首次请允许系统截图请求，'
              '增强功能将在下次正常登录启用（已手动禁用扩展时除外）。')
        if status != 'enabled' and firstrun.fallback_capture_binding():
            print(f'截图快捷键：{firstrun.fallback_capture_binding()}')
        return 0
    if args and args[0] in ('setup', 'hotkeys'):
        return _setup(args)
    if args and args[0] in COMMANDS:
        method, arg = COMMANDS[args[0]]
        if method == 'Capture':
            # Launched from a menu or the app grid: let it close first.
            time.sleep(0.3)
        try:
            _capture_or_fallback(method, arg)
        except Exception as e:  # noqa: BLE001
            print(f'glint: {e}', file=sys.stderr)
            return 1
        return 0
    import gi
    try:
        gi.require_foreign('cairo')
    except ImportError:
        print('Glint 安装尚未完成：缺少 Cairo 绑定。请运行附带的 .run 安装器自动补齐依赖。',
              file=sys.stderr)
        return 1
    from .main import run
    return run([argv[0]] + [a for a in args if a != '--daemon'])
