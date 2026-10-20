#!/bin/sh
# UserPromptSubmit hook: publish pending writes, pull, mark the end of the
# transcript so the Stop-time agents see exactly this turn, then inject the
# memory paths and contents.
#
# The SDK injects a command hook's `additionalContext` for UserPromptSubmit only
# (SessionStart output is recorded but dropped; Stop output is only read when it
# denies a stop), so everything the agent must see is emitted here.
set -u
. "$(dirname "$0")/common.sh"

ohgm_init || exit 0
ohgm_publish
ohgm_pull
python3 "$SCRIPT_DIR/turn_digest.py" --mark
# Debug force-save: a flag armed during the previous turn applies to the turn
# starting now, not to the turn in which it was armed.
[ -f "$OHGM_DIR/.ohgm/.force-save" ] && \
  mv -f "$OHGM_DIR/.ohgm/.force-save" "$OHGM_DIR/.ohgm/.force-next"

python3 "$SCRIPT_DIR/emit_context.py" "$OHGM_GLOBAL" "$OHGM_PROJECT"
exit 0
