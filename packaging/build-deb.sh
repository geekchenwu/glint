#!/usr/bin/env bash
# Build dist/lumen_<version>_all.deb from the source tree.
#   packaging/build-deb.sh            build
#   packaging/build-deb.sh --stage    also keep the staged tree in dist/stage
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(sed -n "s/^VERSION = '\(.*\)'/\1/p" "$SRC/app/lumen_app/__init__.py")"
UUID="lumen@chenwu.local"
APP_ID="io.github.lumen.Lumen"
PKG="lumen_${VERSION}_all"
DIST="$SRC/dist"
# Timestamp of the newest changelog entry, so rebuilds are reproducible.
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(date -d "$(sed -n 's/^ -- .*>  //p' \
  "$SRC/packaging/changelog" | head -1)" +%s)}"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
root="$work/$PKG"
share="$root/usr/share"

# Editor.
install -d "$share/lumen/lumen_app" "$share/lumen/fonts" "$root/usr/bin"
install -m 755 "$SRC/app/lumen" "$share/lumen/lumen"
install -m 644 "$SRC"/app/lumen_app/*.py "$share/lumen/lumen_app/"
install -m 644 "$SRC"/fonts/*.ttf "$SRC/fonts/OFL.txt" "$share/lumen/fonts/"
# A wrapper rather than a symlink: the extension checks the daemon's argv
# for the trusted /usr/share/lumen path.
printf '#!/bin/sh\nexec /usr/share/lumen/lumen "$@"\n' > "$root/usr/bin/lumen"
chmod 755 "$root/usr/bin/lumen"

# Shell extension; its schema goes to the system schema dir, compiled by
# the libglib2.0 dpkg trigger.
ext="$share/gnome-shell/extensions/$UUID"
install -d "$ext/icons" "$share/glib-2.0/schemas"
install -m 644 "$SRC/extension/$UUID"/{metadata.json,extension.js,prefs.js} "$ext/"
install -m 644 "$SRC/extension/$UUID/icons/lumen.svg" "$ext/icons/"
install -m 644 "$SRC/extension/$UUID/schemas/org.gnome.shell.extensions.lumen.gschema.xml" \
  "$share/glib-2.0/schemas/"

# Icons.
install -d "$share/icons/hicolor/scalable/apps" "$share/icons/hicolor/symbolic/apps"
install -m 644 "$SRC/data/icons/$APP_ID.svg" "$share/icons/hicolor/scalable/apps/"
install -m 644 "$SRC/data/icons/$APP_ID-symbolic.svg" "$share/icons/hicolor/symbolic/apps/"
python3 "$SRC/tools/render-icons.py" "$share/icons/hicolor"

# Desktop integration.
install -d "$share/applications" "$root/etc/xdg/autostart" "$share/metainfo"
sed "s|@LUMEN@|/usr/bin/lumen|g" "$SRC/data/$APP_ID.desktop" > "$share/applications/$APP_ID.desktop"
sed "s|@LUMEN@|/usr/share/lumen/lumen|g" "$SRC/data/lumen-daemon.desktop" \
  > "$root/etc/xdg/autostart/$APP_ID.Daemon.desktop"
install -m 644 "$SRC/data/$APP_ID.metainfo.xml" "$share/metainfo/"

# Docs.
install -d "$share/doc/lumen"
install -m 644 "$SRC/packaging/copyright" "$share/doc/lumen/copyright"
install -m 644 "$SRC/packaging/INSTALL.md" "$share/doc/lumen/INSTALL.md"
gzip -9n -c "$SRC/packaging/changelog" > "$share/doc/lumen/changelog.gz"

# Control files.
install -d "$root/DEBIAN"
install -m 755 "$SRC/packaging/postinst" "$SRC/packaging/prerm" "$root/DEBIAN/"
echo "/etc/xdg/autostart/$APP_ID.Daemon.desktop" > "$root/DEBIAN/conffiles"
size="$(du -sk --exclude=DEBIAN "$root" | cut -f1)"
cat > "$root/DEBIAN/control" <<EOF
Package: lumen
Version: $VERSION
Architecture: all
Maintainer: chenwu <geekchenwu@gmail.com>
Installed-Size: $size
Depends: gnome-shell (>= 50), gnome-shell (<< 51), python3 (>= 3.12), python3-gi (>= 3.50),
 python3-gi-cairo, python3-cairo, gir1.2-gtk-4.0 (>= 4.14), gir1.2-pango-1.0,
 gir1.2-gdkpixbuf-2.0, gir1.2-glib-2.0, gir1.2-adw-1, libglib2.0-bin,
 xdg-desktop-portal (>= 1.16), xdg-desktop-portal-gnome
Section: graphics
Priority: optional
Description: instant screenshots, annotation and pinning for GNOME
 Lumen captures from inside GNOME Shell the moment F1 is pressed, so there
 is no start-up delay and open menus, popups and tooltips stay in the shot.
 .
 It auto-detects windows, menus and panels, offers a pixel loupe and colour
 picker, nine annotation tools (rectangle, ellipse, arrow, line, pen,
 marker, text, mosaic, step markers) and pins captures or clipboard
 contents on top of all windows.
EOF
(cd "$root" && find . -path ./DEBIAN -prune -o -type f -printf '%P\0' | sort -z \
  | xargs -0 md5sum > DEBIAN/md5sums)

find "$root" -type d -exec chmod 755 {} +
# Files written via redirection inherit the caller's umask.
find "$root" -type f ! -perm -u=x -exec chmod 644 {} +
find "$root" -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +
mkdir -p "$DIST"
dpkg-deb --root-owner-group -Zxz --build "$root" "$DIST/$PKG.deb" >/dev/null
if [ "${1:-}" = "--stage" ]; then
  rm -rf "$DIST/stage"
  cp -a "$root" "$DIST/stage"
fi
echo "$DIST/$PKG.deb"
python3 "$SRC/tools/build-installer.py" "$SRC/packaging/installer.sh.in" \
  "$DIST/$PKG.deb" "$VERSION" "$DIST/Lumen-$VERSION.run"
