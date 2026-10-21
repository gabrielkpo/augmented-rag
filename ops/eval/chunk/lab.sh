#!/usr/bin/env bash
# lab.sh <variant> <gbrain args...>: the worktree's gbrain on a lab database of the chunk track.
#   variant a -> gbrain_lab_chunk (title only, current state)
#   variant b|c|d|... -> gbrain_lab_chunk_<variant>
# Never targets the live database: GBRAIN_HOME points at the lab and
# GBRAIN_DATABASE_URL is forced to the variant's database (checked before running).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
src="$(cd "$here/../../.." && pwd)"
v="$1"; shift
base_url="$(python3 -I -c "import json,os;print(json.load(open(os.path.expanduser('~/gbrain-labs/chunk/.gbrain/config.json')))['database_url'])")"
if [ "$v" = a ]; then db=gbrain_lab_chunk; else db="gbrain_lab_chunk_$v"; fi
url="${base_url%/*}/$db"
case "$url" in */gbrain_lab_chunk*) ;; *) echo "refus : $url n'est pas une base de labo" >&2; exit 3 ;; esac
export GBRAIN_DATABASE_URL="$url"
export GBRAIN_SRC="$src"
# Shared, loaded machine: query embedding timeout raised from 6 s to 30 s (measurement, not production).
export GBRAIN_QUERY_EMBED_TIMEOUT_MS="${GBRAIN_QUERY_EMBED_TIMEOUT_MS:-30000}"
cd "$src"
exec "$HOME/gbrain-labs/bin/gbrain-lab" chunk "$@"
