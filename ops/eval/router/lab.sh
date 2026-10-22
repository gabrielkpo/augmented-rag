#!/usr/bin/env bash
# gbrain on the native lab brain "router2" (database gbrain_lab_router2; GBRAIN_LAB_NAME=router for the older copy), with the code of this worktree.
# Never targets the live brain.
set -euo pipefail
wt="$(cd "$(dirname "$0")/../../.." && pwd)"
GBRAIN_SRC="$wt" exec "$HOME/gbrain-labs/bin/gbrain-lab" "${GBRAIN_LAB_NAME:-router2}" "$@"
