#!/usr/bin/env bash
# doc2query control measurement (condition 5 of the lab results) on the fresh NATIVE lab d2qctl,
# with the integrated code (chunk-text command, search.chunk_index_text setting), not the lab scripts.
#
#   ops/eval/chunk/run-d2qctl.sh import <jsonl>     stores the questions (setting off: no effect)
#   ops/eval/chunk/run-d2qctl.sh measure <off|on> [benches of the 2nd Jev run]
#       sets, refreshes, then targeted bench + v2 + holdout A: one run without Jev, two with Jev
#       (the second limited to the named benches, e.g. "chunk", to stay within the Jev budget)
#
# Results: ops/eval/chunk/results/d2qctl-<off|on>/ (ignored by Git), readable by compare.py and bootstrap.py.
# Holdout B is never run. Targets gbrain_lab_d2qctl only (checked first).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
cfg="$HOME/gbrain-labs/d2qctl/.gbrain/config.json"
url="$(python3 -I -c "import json,sys;print(json.load(open(sys.argv[1]))['database_url'].split('?')[0])" "$cfg")"
case "$url" in */gbrain_lab_d2qctl) ;; *) echo "refus : $url n'est pas la base de labo d2qctl" >&2; exit 3 ;; esac
export GBRAIN_QUERY_EMBED_TIMEOUT_MS=30000
lab() { GBRAIN_SRC="$repo" "$HOME/gbrain-labs/bin/gbrain-lab" d2qctl "$@"; }

# Measurement settings (loaded machine): long timeouts, so that nothing silently falls back to a degraded order.
lab config set search.keyword_statement_timeout_ms 60000 >/dev/null
lab config set search.reranker.timeout_ms 30000 >/dev/null

case "${1:-}" in
  import) lab chunk-text import "$2" --no-refresh ;;
  measure)
    state="$2"; out="$here/results/d2qctl-$state"; mkdir -p "$out"
    if [ "$state" = on ]; then lab config set search.chunk_index_text d2q >/dev/null; else lab config unset search.chunk_index_text >/dev/null 2>&1 || true; fi
    # refresh is idempotent: rerun while failures remain (connections dropped on a loaded machine), 5 times at most.
    for try in 1 2 3 4 5; do
      if lab chunk-text refresh > "$out/refresh-$try.txt" 2>&1; then break; fi
      echo "refresh $try : échecs, voir $out/refresh-$try.txt" >&2
    done
    grep -E "page\(s\)" "$out"/refresh-*.txt
    banks=("$here/qrels-chunk.json" "$HOME/gbrain/ops/eval/qrels-v2.json" "$HOME/gbrain/ops/eval/qrels-holdout-a.json")
    pass() {
      local mode="$1" n="$2" enabled=false
      [ "$mode" = jev ] && enabled=true
      lab config set search.reranker.enabled "$enabled" >/dev/null
      for q in "${banks[@]}"; do
        local name f fo; name="$(basename "$q" .json | sed 's/^qrels-//')"; f="$out/$name-$mode$n.txt"
        if [ "$mode$n" = jev2 ]; then case " $jev2_banks " in *" $name "*) ;; *) continue ;; esac; fi
        local t0; t0=$(date +%s)
        lab eval --qrels "$q" --limit 10 --k 5 > "$f" 2>&1
        echo "elapsed_s $(( $(date +%s) - t0 ))" >> "$f"
        fo=$(grep -cE "^\[gbrain\].*(failed|exceeded|fail-open)" "$f" || true)
        printf '%-10s %-16s %s  lignes-en-échec=%s\n' "d2qctl-$state" "$name-$mode$n" "$(grep -E '^Mean' "$f" | tail -1)" "$fo"
      done
    }
    jev2_banks="${3:-chunk v2 holdout-a}"
    pass nojev 1; pass jev 1; pass jev 2
    lab config set search.reranker.enabled true >/dev/null ;;
  *) echo "usage: $0 import <jsonl> | measure <off|on>" >&2; exit 2 ;;
esac
