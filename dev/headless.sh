#!/usr/bin/env bash
# Start an isolated headless GNOME Shell with Glint installed, for automated
# testing. Nothing here touches the real session: HOME, dconf, runtime dir
# and the session bus are all private.
#
# The XDG variables must be exported *before* dbus-run-session: services the
# private bus activates (dconf-service!) inherit the bus daemon's environment,
# not the caller's. GSettings additionally uses the keyfile backend so no
# dconf-service is involved at all.
#   dev/headless.sh start [WxH] [scale]   then:  source /tmp/glint-hl/env
#   dev/headless.sh system [WxH]          test the installed .deb in /usr/share
#   dev/headless.sh firstrun [WxH]        like system, but as a fresh login after
#                                         install: extension off, F1 held by a
#                                         Snipaste-style custom shortcut
#   dev/headless.sh fresh-install        start without any Glint installation
#   dev/headless.sh next-login           keep the first-install test's user data
#   dev/headless.sh stop
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT=/tmp/glint-hl

# Every process of the sandbox session carries its private runtime dir, so
# match on that rather than on command lines; the real session is never hit.
sandbox_pids() {
  local env
  for env in /proc/[0-9]*/environ; do
    [ -O "$env" ] || continue
    { tr '\0' '\n' < "$env"; } 2>/dev/null | grep -qx "XDG_RUNTIME_DIR=$ROOT/run" || continue
    env="${env#/proc/}"
    case "${env%/environ}" in "$$"|"$PPID") ;; *) echo "${env%/environ}" ;; esac
  done
}

case "${1:-start}" in
  stop)
    [ -f "$ROOT/pid" ] && kill "$(cat "$ROOT/pid")" 2>/dev/null || true
    for mnt in "$ROOT/run/doc" "$ROOT/run/gvfs"; do
      mountpoint -q "$mnt" 2>/dev/null && fusermount3 -uz "$mnt" 2>/dev/null || true
    done
    pids="$(sandbox_pids)"
    [ -n "$pids" ] && kill $pids 2>/dev/null || true
    for _ in $(seq 1 30); do
      [ -z "$(sandbox_pids)" ] && break
      sleep 0.1
    done
    pids="$(sandbox_pids)"
    [ -n "$pids" ] && kill -9 $pids 2>/dev/null || true
    exit 0 ;;
esac

SIZE="${2:-1920x1080}"
SYSTEM=0
FIRSTRUN=0
FRESH=0
RESUME=0
case "${1:-start}" in
  system) SYSTEM=1 ;;
  firstrun) SYSTEM=1; FIRSTRUN=1 ;;
  fresh-install) FRESH=1 ;;
  next-login) FRESH=1; RESUME=1 ;;
esac
SCHEMADIR="--schemadir $ROOT/data/gnome-shell/extensions/glint@chenwu.local/schemas"
INSTALL="bash \"$SRC/install.sh\" --no-activate >/dev/null"
ENABLED="['glint@chenwu.local']"
MK=org.gnome.settings-daemon.plugins.media-keys
CUSTOM=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/snipaste-f1/
CLASH="true"
if [ "$SYSTEM" = 1 ]; then
  SCHEMADIR=""
  INSTALL="test -f /usr/share/gnome-shell/extensions/glint@chenwu.local/extension.js"
fi
if [ "$FIRSTRUN" = 1 ]; then
  ENABLED="[]"
  CLASH="gsettings set $MK custom-keybindings \"['$CUSTOM']\"
gsettings set $MK.custom-keybinding:$CUSTOM name 'Snipaste 截屏（F1）'
gsettings set $MK.custom-keybinding:$CUSTOM command 'snipaste snip'
gsettings set $MK.custom-keybinding:$CUSTOM binding 'F1'"
fi
if [ "$FRESH" = 1 ]; then
  INSTALL="mkdir -p \"$ROOT/data/gnome-shell/extensions\"
cp -r \"$SRC/dev/session-driver\" \"$ROOT/data/gnome-shell/extensions/glint-test@local\""
  ENABLED="['glint-test@local']"
fi
if [ "$RESUME" = 1 ]; then
  test -f "$ROOT/data/glint/glint"
  INSTALL="true"
fi
"$0" stop
if [ "$RESUME" = 1 ]; then
  rm -rf "$ROOT/run"
else
  rm -rf "$ROOT"
fi
mkdir -p "$ROOT"/{run,config,data,cache,home/Pictures}
chmod 700 "$ROOT/run"

cat > "$ROOT/inner.sh" <<EOF
set -e
$INSTALL
if [ "$FRESH" = 0 ]; then
  gsettings $SCHEMADIR set org.gnome.shell.extensions.glint debug-mode true
fi
if [ "$RESUME" = 0 ]; then
  gsettings set org.gnome.shell enabled-extensions "$ENABLED"
fi
# Refuse to continue unless the write landed in the sandbox.
grep -q glint "$ROOT/config/glib-2.0/settings/keyfile" || { echo "settings escaped sandbox" >&2; exit 1; }
$CLASH
gsettings set org.gnome.shell welcome-dialog-last-shown-version '999'
gsettings set org.gnome.desktop.interface enable-animations false
gsettings set org.gnome.desktop.interface enable-hot-corners false
{
  echo "export HOME=$ROOT/home XDG_RUNTIME_DIR=$ROOT/run XDG_CONFIG_HOME=$ROOT/config"
  echo "export XDG_DATA_HOME=$ROOT/data XDG_CACHE_HOME=$ROOT/cache GSETTINGS_BACKEND=keyfile"
  echo "export DBUS_SESSION_BUS_ADDRESS='\$DBUS_SESSION_BUS_ADDRESS' WAYLAND_DISPLAY=glint-hl"
  echo "unset DISPLAY"
} > $ROOT/env
if [ "$FRESH" = 1 ]; then
  (
    until [ -S "$ROOT/run/glint-hl" ]; do sleep 0.1; done
    export WAYLAND_DISPLAY=glint-hl
    exec /usr/libexec/gsd-media-keys
  ) > "$ROOT/media-keys.log" 2>&1 &
fi
exec gnome-shell --headless --no-x11 --virtual-monitor $SIZE --wayland-display glint-hl
EOF

runner=(dbus-run-session -- bash "$ROOT/inner.sh")
if [ "$FRESH" = 1 ]; then
  mkdir -p "$ROOT/empty-extensions"
  cat > "$ROOT/namespace.py" <<EOF
import ctypes
import os

libc = ctypes.CDLL(None, use_errno=True)
if libc.mount(b"$ROOT/empty-extensions", b"/usr/share/gnome-shell/extensions", None, 4096, None):
    raise OSError(ctypes.get_errno(), "cannot hide system extensions in the test namespace")
os.execvp("setpriv", ["setpriv", "--inh-caps=-all", "--ambient-caps=-all",
                     "dbus-run-session", "--", "bash", "$ROOT/inner.sh"])
EOF
  runner=(unshare --user --map-current-user --keep-caps --mount --fork python3 "$ROOT/namespace.py")
fi
env -u WAYLAND_DISPLAY -u DISPLAY -u DBUS_SESSION_BUS_ADDRESS -u GIO_LAUNCHED_DESKTOP_FILE \
  HOME=$ROOT/home XDG_RUNTIME_DIR=$ROOT/run XDG_CONFIG_HOME=$ROOT/config \
  XDG_DATA_HOME=$ROOT/data XDG_CACHE_HOME=$ROOT/cache GSETTINGS_BACKEND=keyfile \
  XDG_SESSION_TYPE=wayland XDG_CURRENT_DESKTOP=GNOME \
  setsid "${runner[@]}" > "$ROOT/shell.log" 2>&1 &
echo $! > "$ROOT/pid"
ready_obj=/io/github/glint/Shell ready_iface=io.github.glint.Shell
[ "$FIRSTRUN" = 1 ] && ready_obj=/org/gnome/Shell/Extensions ready_iface=org.gnome.Shell.Extensions
[ "$FRESH" = 1 ] && ready_obj=/io/github/glint/TestSession ready_iface=io.github.glint.TestSession
for _ in $(seq 1 100); do
  if [ -f "$ROOT/env" ] && (source "$ROOT/env"; gdbus introspect --session --dest org.gnome.Shell \
      --object-path $ready_obj 2>/dev/null | grep -q $ready_iface &&
      gdbus introspect --session --dest org.gnome.Shell --object-path /org/gnome/Shell \
        2>/dev/null | grep -q org.gnome.Shell.Extensions); then
    echo "headless shell ready: source $ROOT/env"
    exit 0
  fi
  sleep 0.2
done
echo "shell did not come up; see $ROOT/shell.log" >&2
tail -30 "$ROOT/shell.log" >&2
exit 1
