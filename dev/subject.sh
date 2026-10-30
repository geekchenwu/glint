#!/usr/bin/env bash
# Launch the test subject window detached from the calling shell.
source /tmp/lumen-hl/env
setsid -f python3 "$(dirname "${BASH_SOURCE[0]}")/testapp.py" >> /tmp/lumen-hl/testapp.log 2>&1 < /dev/null
