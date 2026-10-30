#!/usr/bin/env bash
# Install Lumen for the current user. Safe to re-run; it never touches
# existing shortcuts unless --take-over-hotkeys is given.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}"
UUID="lumen@chenwu.local"
APPDIR="$DATA/lumen"
EXTDIR="$DATA/gnome-shell/extensions/$UUID"
TAKE_OVER=0
ACTIVATE=1
for arg in "$@"; do
  case "$arg" in
    --take-over-hotkeys) TAKE_OVER=1 ;;
    --no-activate) ACTIVATE=0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

mkdir -p "$DATA"
stage="$(mktemp -d "$DATA/.lumen-install.XXXXXX")"
trap 'rm -rf "$stage"' EXIT

# Editor: copy into a staging dir, then swap so a running daemon keeps
# working from its already-loaded modules.
mkdir -p "$stage/app"
cp -r "$SRC/app/lumen" "$SRC/app/lumen_app" "$SRC/fonts" "$stage/app/"
find "$stage/app" -name '__pycache__' -prune -exec rm -rf {} +
chmod 755 "$stage/app/lumen"
if [ -d "$APPDIR" ]; then
  rm -rf "$APPDIR.previous"
  mv "$APPDIR" "$APPDIR.previous"
fi
mv "$stage/app" "$APPDIR"
rm -rf "$APPDIR.previous"

# Shell extension.
mkdir -p "$(dirname "$EXTDIR")"
rm -rf "$stage/ext"
cp -r "$SRC/extension/$UUID" "$stage/ext"
glib-compile-schemas "$stage/ext/schemas"
rm -rf "$EXTDIR"
mv "$stage/ext" "$EXTDIR"

# Launcher, icon, desktop entry, autostart.
mkdir -p "$HOME/.local/bin" "$DATA/applications" "$CONFIG/autostart" \
  "$DATA/icons/hicolor/scalable/apps" "$DATA/icons/hicolor/symbolic/apps"
ln -sfn "$APPDIR/lumen" "$HOME/.local/bin/lumen"
cp "$SRC/data/icons/io.github.lumen.Lumen.svg" "$DATA/icons/hicolor/scalable/apps/"
cp "$SRC/data/icons/io.github.lumen.Lumen-symbolic.svg" "$DATA/icons/hicolor/symbolic/apps/"
sed "s|@LUMEN@|$APPDIR/lumen|g" "$SRC/data/io.github.lumen.Lumen.desktop" \
  > "$DATA/applications/io.github.lumen.Lumen.desktop"
sed "s|@LUMEN@|$APPDIR/lumen|g" "$SRC/data/lumen-daemon.desktop" \
  > "$CONFIG/autostart/io.github.lumen.Lumen.Daemon.desktop"
update-desktop-database "$DATA/applications" >/dev/null 2>&1 || true
gtk-update-icon-cache -q "$DATA/icons/hicolor" >/dev/null 2>&1 || true

if [ "$TAKE_OVER" = 1 ]; then
  "$SRC/hotkeys.sh" take-over
fi

echo "Lumen installed:"
echo "  editor     $APPDIR"
echo "  extension  $EXTDIR"
if [ "$ACTIVATE" = 1 ] && [ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ] &&
   gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
     --method org.freedesktop.DBus.NameHasOwner org.gnome.Shell 2>/dev/null | grep -q true; then
  "$APPDIR/lumen" activate
else
  echo "Start with: $APPDIR/lumen activate"
fi
