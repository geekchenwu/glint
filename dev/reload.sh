#!/usr/bin/env bash
# Reinstall the editor into the headless sandbox and restart its daemon.
set -euo pipefail
source /tmp/glint-hl/env
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bash "$SRC/install.sh" --no-activate >/dev/null
pkill -f "python3 [/]tmp/glint-hl/data/glint/glint" || true
sleep 0.3
setsid -f "$XDG_DATA_HOME/glint/glint" --daemon >> /tmp/glint-hl/app.log 2>&1 < /dev/null
for _ in $(seq 1 50); do
  gdbus introspect --session --dest io.github.glint.Glint --object-path /io/github/glint/Glint/Daemon 2>/dev/null \
    | grep -q io.github.glint.Daemon && { echo "daemon up"; exit 0; }
  sleep 0.1
done
echo "daemon did not start" >&2; tail -20 /tmp/glint-hl/app.log >&2; exit 1
