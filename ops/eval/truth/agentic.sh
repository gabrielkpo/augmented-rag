#!/usr/bin/env bash
# Agentic bench of the truth track, on a LAB copy (never the live brain).
# The 5 temporal questions, the 6 bridge questions and mh-06 of qrels-multihop.json, with
# run.py's "base" prompt variant (no prompt added), scored by judge.py.
#
# Usage: ops/eval/truth/agentic.sh <variant> <run>
#   Lab: TRUTH_LAB (default truth2, MCP over stdio: every session starts its own lab server,
#   with the code of THIS worktree). Variants, to match the state of the lab:
#     avant       (before) no `superseded_by` frontmatter, no `supersedes` links
#     convention  frontmatter set, search unchanged (the agent only sees the key in get_page)
#     native      frontmatter + `supersedes` links (native demotion, before the reranker)
#     annotate    frontmatter + GBRAIN_SUPERSEDE=annotate: order unchanged, the superseded row carries superseded_by
#     penalty | behind | substitute   frontmatter + GBRAIN_SUPERSEDE=<variant> (after the reranker)
#   With TRUTH_LAB=truth (older lab, HTTP server on port 3142, see ~/gbrain-labs/truth/mcp.json),
#   only the avant variant is supported.
# AGENTIC_IDS restricts the questions, AGENTIC_TAG suffixes the results folder.
# Lab database: $GBRAIN_LAB_DB_PREFIX/gbrain_lab_<lab> (prefix default postgres://$USER@localhost:5432).
# The lab's query cache is cleared before each run.
# Safeguard: the head of the live brain's Git repository is recorded before and after.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
variant="${1:?variante}"; pass="${2:?passage}"
name="${TRUTH_LAB:-truth2}"
ids="${AGENTIC_IDS:-mh-01,mh-02,mh-03,mh-04,mh-05,mh-06,mh-10,mh-11,mh-12,mh-13,mh-14,mh-15}"
# AGENTIC_PROMPT=iterative replays the v1 prompt (the one that had read the stale page on mh-06, §4.4).
prompt="${AGENTIC_PROMPT:-base}"
qrels="${QRELS_DIR:-$HOME/gbrain/ops/eval}/qrels-multihop.json"
db="${GBRAIN_LAB_DB_PREFIX:-postgres://${USER:-$(id -un)}@localhost:5432}/gbrain_lab_$name"
links="$(psql -X -At "$db" -c "select count(*) from links where link_type='supersedes'")"
fm="$(psql -X -At "$db" -c "select count(*) from pages where deleted_at is null and frontmatter ? 'superseded_by'")"

supersede=""
case "$variant" in
  avant) [ "$links" = 0 ] && [ "$fm" = 0 ] || { echo "avant : retirer liens ($links) et frontmatter ($fm)" >&2; exit 4; } ;;
  convention) [ "$links" = 0 ] && [ "$fm" != 0 ] || { echo "convention : frontmatter seul attendu" >&2; exit 4; } ;;
  native) [ "$links" != 0 ] && [ "$fm" != 0 ] || { echo "native : frontmatter et liens attendus" >&2; exit 4; } ;;
  annotate|penalty|behind|substitute) [ "$links" = 0 ] && [ "$fm" != 0 ] || { echo "$variant : frontmatter seul attendu" >&2; exit 4; }; supersede="$variant" ;;
  *) echo "variante inconnue : $variant" >&2; exit 2 ;;
esac

out="$here/results"; mkdir -p "$out"
work="$here/work"; mkdir -p "$work"
if [ "$name" = truth2 ]; then
  mcp="$work/mcp-$variant.json"
  python3 -I - "$mcp" "$repo" "$supersede" <<'EOF'
import json, os, sys
path, repo, supersede = sys.argv[1:]
env = {'GBRAIN_SRC': repo, 'GBRAIN_QUERY_EMBED_TIMEOUT_MS': '30000'}
if supersede:
    env['GBRAIN_SUPERSEDE'] = supersede
json.dump({'mcpServers': {'gbrain': {'type': 'stdio', 'command': os.path.expanduser('~/gbrain-labs/bin/gbrain-lab'),
                                     'args': ['truth2', 'serve'], 'env': env}}}, open(path, 'w'), indent=2)
EOF
else
  [ "$variant" = avant ] || { echo "labo truth : seule la variante avant" >&2; exit 2; }
  mcp="$HOME/gbrain-labs/truth/mcp.json"; port=3142
  if lsof -nP -iTCP:$port -sTCP:LISTEN >/dev/null 2>&1; then echo "le port $port est déjà pris" >&2; exit 5; fi
  GBRAIN_SRC="$repo" GBRAIN_QUERY_EMBED_TIMEOUT_MS=30000 "$HOME/gbrain-labs/bin/gbrain-lab" truth serve --http --port $port \
    > "$out/agentic-$name-$variant-$pass-serve.log" 2>&1 &
  serve_pid=$!
  trap 'kill "$serve_pid" 2>/dev/null || true' EXIT
  for _ in $(seq 1 60); do curl -fsS "http://127.0.0.1:$port/health" >/dev/null 2>&1 && break; sleep 2; done
fi
psql -X -At "$db" -c "delete from query_cache" >/dev/null

live_before="$(git -C "$HOME/gbrain-content" rev-parse HEAD)"
run_out="$(python3 -I "$repo/ops/eval/agentic/run.py" --qrels "$qrels" --only "$ids" --variants "$prompt" \
  --mcp-config "$mcp" --jobs 3)"
echo "$run_out" | tail -3
dir="$(echo "$run_out" | sed -n 's/^Résultats : //p' | tail -1)"
[ -n "${serve_pid:-}" ] && { kill "$serve_pid" 2>/dev/null || true; trap - EXIT; }
# judge.py judges "erreur" verdicts again (context too long on the claude -p side): two attempts.
python3 -I "$repo/ops/eval/agentic/judge.py" "$dir" --qrels "$qrels" >/dev/null
python3 -I "$repo/ops/eval/agentic/judge.py" "$dir" --qrels "$qrels" >/dev/null
dest="$out/agentic-$name-$variant${AGENTIC_TAG:+-$AGENTIC_TAG}-$pass"
rm -rf "$dest"; mv "$dir" "$dest"; rm -rf "$dest/cwd"
live_after="$(git -C "$HOME/gbrain-content" rev-parse HEAD)"
echo "dépôt live : $live_before -> $live_after" | tee "$dest/depot-live.txt"
git -C "$HOME/gbrain-content" log --format='%h %ad %s' --date=iso "$live_before..$live_after" | tee -a "$dest/depot-live.txt"
cat "$dest/juge.md"
