#!/usr/bin/env bash
# Agentic bench of the dated-facts track, on the LAB brain (never the live one).
# Usage: ops/eval/dated/agentic.sh <pass>   (variants base and temporal, recall allowed in both)
# AGENTIC_IDS restricts the questions (comma-separated ids). A run with failed answers (exit code
# or usage-limit message) stops before judging: rerun those ids, never judge them.
# Judged twice: Haiku in results/agentic-<pass>, Sonnet in results/agentic-<pass>-sonnet.
# Lab: DATED_LAB (default dated), served over stdio with the code of THIS worktree; Jev reranker on.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
pass="${1:?passage}"
name="${DATED_LAB:-dated}"
qrels="$here/results/qrels-agentic.json"
db="gbrain_lab_$name"
case "$db" in gbrain_lab_*) ;; *) echo "refus : $db" >&2; exit 1 ;; esac
[ "$(psql -X -At -d "$db" -c "select value from config where key='search.reranker.enabled'")" = true ] || { echo "Jev doit être allumé" >&2; exit 4; }
psql -X -At -d "$db" -c "delete from query_cache" >/dev/null
live_before="$(git -C "$HOME/gbrain-content" rev-parse HEAD)"
run_out="$(python3 -I "$repo/ops/eval/agentic/run.py" --qrels "$qrels" --variants base,temporal \
  --extra-tools mcp__gbrain__recall --mcp-config "$HOME/gbrain-labs/$name/mcp.json" --jobs 2 \
  ${AGENTIC_IDS:+--only "$AGENTIC_IDS"})"
dir="$(echo "$run_out" | sed -n 's/^Résultats : //p' | tail -1)"
failed="$(python3 -I - "$dir" <<'PY'
import glob, json, os, sys
bad = [os.path.basename(p)[:-5] for p in glob.glob(os.path.join(sys.argv[1], '*.json'))
       if (r := json.load(open(p, encoding='utf-8')))['exit'] != 0 or any(m in (r['answer'] or '')[:300] for m in ("You've hit your", 'spend limit', 'usage limit'))]
print(' '.join(sorted(bad)))
PY
)"
if [ -n "$failed" ]; then echo "réponses en échec, pas de jugement : $failed ($dir)" >&2; exit 6; fi
# Two judges: Haiku (protocol continuity) on the run folder, Sonnet on a copy (the verdict we rely on).
# judge.py judges "erreur" verdicts again on a second call.
rm -rf "$dir/cwd"; cp -R "$dir" "$dir-sonnet"
for _ in 1 2; do
  python3 -I "$repo/ops/eval/agentic/judge.py" "$dir" --qrels "$qrels" >/dev/null
  python3 -I "$repo/ops/eval/agentic/judge.py" "$dir-sonnet" --qrels "$qrels" --model claude-sonnet-5-5 >/dev/null
done
dest="$here/results/agentic-$pass"
rm -rf "$dest" "$dest-sonnet"; mv "$dir" "$dest"; mv "$dir-sonnet" "$dest-sonnet"
echo "dépôt live : $live_before -> $(git -C "$HOME/gbrain-content" rev-parse HEAD)" | tee "$dest/depot-live.txt"
echo "juge Haiku"; cat "$dest/juge.md"; echo "juge Sonnet"; cat "$dest-sonnet/juge.md"
