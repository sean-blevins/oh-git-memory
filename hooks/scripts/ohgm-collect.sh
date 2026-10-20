#!/bin/sh
# Stop hook, step 1: build the decide agent's input for the turn that just ended,
# and stage anything already decided (remember command, debug force flag).
#
# This runs before the decide agent hook, so the digest exists by the time it
# starts. It deliberately does not publish - nothing is committed until the notes
# have been filed.
set -u
. "$(dirname "$0")/common.sh"

ohgm_init || exit 0
python3 "$SCRIPT_DIR/turn_digest.py" --digest "$OHGM_GLOBAL" "$OHGM_PROJECT"
exit 0
