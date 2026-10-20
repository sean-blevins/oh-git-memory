#!/bin/sh
# Stop hook, step 5: publish what the remember agent filed. A no-op when it filed
# nothing, which is the common case.
set -u
. "$(dirname "$0")/common.sh"

ohgm_init || exit 0
ohgm_pull
ohgm_publish
exit 0
