#!/usr/bin/env bash
# End-to-end run in the headless shell: open a GTK client menu, press F1
# while it is open, select, annotate, pin. Screenshots land in
# /tmp/lumen-shots/<run>-NN-*.png (unique names per run).
#   dev/headless.sh start && dev/reload.sh && dev/e2e.sh
set -euo pipefail
DEV="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source /tmp/lumen-hl/env
RUN="${1:-$(date +%H%M%S)}"
OUT=/tmp/lumen-shots
mkdir -p "$OUT"
ctl() { python3 "$DEV/ctl.py" "$@"; }
shot() { ctl shot "$OUT/$RUN-$1.png"; echo "$OUT/$RUN-$1.png"; }

pkill -f '^python3 \S*testapp\.py$' || true
: > /tmp/lumen-hl/testapp.log
"$DEV/subject.sh"
sleep 1.5
ctl key Escape            # leave the overview shown at startup
sleep 0.5
ctl click 960 700         # focus the subject window
for _ in 1 2 3 4 5; do    # open its hamburger menu (xdg_popup)
  ctl click 1267 318
  sleep 0.6
  tail -1 /tmp/lumen-hl/testapp.log | grep -q 'menu active True' && break
done
shot 01-menu

ctl move 1260 430
ctl key F1
sleep 0.8
shot 02-overlay

ctl drag 560 260 1380 840 16
sleep 0.3
shot 03-selected

ctl key r; ctl drag 1170 340 1370 520 10
ctl key a; ctl drag 900 700 1160 440 10
ctl key n; ctl click 600 400; ctl click 600 470
ctl key m; ctl drag 630 425 870 425 8
ctl key x; ctl drag 820 545 910 578 6
ctl key t; ctl click 980 640; ctl type 'Ship it'; ctl key Escape
ctl key e; ctl drag 1180 600 1320 700 8
sleep 0.3
shot 04-annotated

ctl key t ctrl            # pin
sleep 0.8
shot 05-pinned
ctl move 900 500; ctl scroll -1; ctl scroll -1
sleep 0.3
shot 06-pin-zoom

echo "--- app.log tracebacks: $(grep -c Traceback /tmp/lumen-hl/app.log || true)"
grep 'lumen:' /tmp/lumen-hl/shell.log | tail -1
cat /tmp/lumen-hl/testapp.log
