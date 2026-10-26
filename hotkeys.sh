#!/usr/bin/env bash
# Move F1/F3 family shortcuts from Snipaste's gsd custom keybindings to Glint.
#   hotkeys.sh take-over   back up and disable the Snipaste custom shortcuts
#   hotkeys.sh restore     put the backed-up custom shortcuts back
set -euo pipefail

BASE=org.gnome.settings-daemon.plugins.media-keys
BACKUP="${XDG_CONFIG_HOME:-$HOME/.config}/glint/snipaste-keybindings.backup"

case "${1:-}" in
  take-over)
    mkdir -p "$(dirname "$BACKUP")"
    if [ ! -f "$BACKUP" ]; then
      {
        echo "custom-keybindings=$(gsettings get $BASE custom-keybindings)"
        for p in $(gsettings get $BASE custom-keybindings | tr -d "[],'"); do
          echo "$p|$(gsettings get $BASE.custom-keybinding:$p binding)"
        done
      } > "$BACKUP"
      echo "backup: $BACKUP"
    fi
    for p in $(gsettings get $BASE custom-keybindings | tr -d "[],'"); do
      cmd="$(gsettings get $BASE.custom-keybinding:$p command)"
      binding="$(gsettings get $BASE.custom-keybinding:$p binding)"
      case "$cmd" in
        *snipaste*)
          case "$binding" in
            "'F1'"|"'<Control>F1'"|"'<Shift>F1'"|"'F3'"|"'<Shift>F3'"|"'<Control>F3'")
              gsettings set $BASE.custom-keybinding:$p binding "''"
              echo "disabled $p ($binding -> $cmd)" ;;
          esac ;;
      esac
    done
    ;;
  restore)
    [ -f "$BACKUP" ] || { echo "no backup at $BACKUP" >&2; exit 1; }
    while IFS= read -r line; do
      case "$line" in
        custom-keybindings=*) ;;
        *\|*)
          p="${line%%|*}"; binding="${line#*|}"
          gsettings set $BASE.custom-keybinding:$p binding "$binding"
          echo "restored $p -> $binding" ;;
      esac
    done < "$BACKUP"
    ;;
  *)
    echo "usage: $0 take-over|restore" >&2; exit 2 ;;
esac
