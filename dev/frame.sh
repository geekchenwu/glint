#!/usr/bin/env bash
# Exercise the Snipaste-style selection frame in the headless shell:
# outside-zone resizing, resizing while a tool is active, keyboard edge
# control, W A S D pointer steps, Space and R.
#   dev/headless.sh start && dev/frame.sh
set -euo pipefail
DEV="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source /tmp/lumen-hl/env
RUN="${1:-frame}"
OUT=/tmp/lumen-shots
mkdir -p "$OUT"
ctl() { python3 "$DEV/ctl.py" "$@"; }
shot() { sleep 0.3; ctl shot "$OUT/$RUN-$1.png"; echo "$OUT/$RUN-$1.png"; }

pkill -f '^python3 \S*testapp\.py$' || true
"$DEV/subject.sh"
sleep 1.5
ctl key Escape
sleep 0.5
ctl click 960 700

# F1 and drag at once, before the editor is up: the drag must not move the
# window under the frozen frame (its title bar is at y=318).
ctl move 700 318
ctl key F1; ctl drag 700 318 900 418 6
sleep 1.5
ctl key Escape
shot 00-early-drag                      # window still at x=580..1340

ctl move 600 300
ctl key F1
sleep 1.5
ctl drag 600 300 1300 800 12          # 700 x 500
ctl key r; ctl drag 700 380 1000 560 8  # a rectangle, tool stays active
shot 01-selected-with-tool

ctl move 1600 550; shot 02-hover-east   # cursor: ew-resize (not in stage shots)
ctl drag 1600 550 1700 550 8            # east edge +100 with the tool still active
ctl drag 300 120 350 170 8              # far north-west zone: corner +50,+50
shot 03-zone-resized                    # expect 750 x 450 at (650, 350)

ctl key Right ctrl; ctl key Right ctrl  # +2 east
ctl key Up shift                        # top edge 1 down
ctl key Left                            # move 1 left
shot 04-keyboard                        # expect 752 x 549

ctl drag 1350 300 1350 600 8            # north zone: top edge 300 down
shot 05-north                           # expect 752 x 249

ctl key space; shot 06-chrome-hidden
ctl key space
ctl key Return                          # copy & close; remembers the region
sleep 0.6

ctl move 400 400
ctl key F1
sleep 1.5
for _ in 1 2 3 4 5; do ctl key d; done  # pointer 5 px right
ctl key s; ctl key s                    # 2 px down
shot 07-wasd                            # loupe reads 405 , 402
ctl key r                               # last region
shot 08-last-region
ctl key Escape
echo "--- tracebacks: $(grep -c Traceback /tmp/lumen-hl/app.log || true)"
grep -E "JS ERROR|MovePointer|ACCESS" /tmp/lumen-hl/shell.log | tail -5 || true
