#!/bin/sh
# Stop hook, step 3: assemble the remember agent's input from the staged notes.
#
# Sits between the two agents so each one reads exactly one file.
set -u
. "$(dirname "$0")/common.sh"

ohgm_init || exit 0
python3 "$SCRIPT_DIR/stage_notes.py" "$OHGM_GLOBAL" "$OHGM_PROJECT"
exit 0
