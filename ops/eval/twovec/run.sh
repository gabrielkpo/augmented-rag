#!/usr/bin/env bash
# Measures one variant of the two-vector lab on the three benches (targeted, v2, holdout A).
#
#   ops/eval/twovec/run.sh <label> <nojev|jev> <n> [VAR=value ...]
#
# The VAR=value pairs are exported for gbrain (the lab-only switches of the twovec patch):
#   GBRAIN_LAB_CTX_COL=embedding_ctx_g|embedding_ctx_h   second vector arm over that column
#   GBRAIN_LAB_CTX_WEIGHT=<w>                            its RRF weight (default 1)
#   GBRAIN_LAB_CTX_RESCORE_COL=<column>                  cosine re-score takes the best of both columns
# Results: ops/eval/twovec/results/<label>/<bench>-<mode><n>.txt (ignored by Git). Holdout B is never run.
# Targets gbrain_lab_twovec only (checked first). Run from the worktree that carries the lab patch.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
label="$1"; mode="$2"; n="$3"; shift 3
cfg="$HOME/gbrain-labs/twovec/.gbrain/config.json"
url="$(python3 -I -c "import json,sys;print(json.load(open(sys.argv[1]))['database_url'].split('?')[0])" "$cfg")"
case "$url" in */gbrain_lab_twovec) ;; *) echo "refus : $url n'est pas la base de labo twovec" >&2; exit 3 ;; esac
for kv in "$@"; do export "$kv"; done
export GBRAIN_QUERY_EMBED_TIMEOUT_MS=30000
lab() { GBRAIN_SRC="$repo" "$HOME/gbrain-labs/bin/gbrain-lab" twovec "$@"; }
out="$here/results/$label"; mkdir -p "$out"
printf '%s\n' "$@" > "$out/env.txt"
enabled=false; [ "$mode" = jev ] && enabled=true
lab config set search.reranker.enabled "$enabled" >/dev/null
for q in "$repo/ops/eval/chunk/qrels-chunk.json" "$HOME/gbrain/ops/eval/qrels-v2.json" "$HOME/gbrain/ops/eval/qrels-holdout-a.json"; do
  name="$(basename "$q" .json | sed 's/^qrels-//')"; f="$out/$name-$mode$n.txt"
  t0=$(date +%s)
  lab eval --qrels "$q" --limit 10 --k 5 > "$f" 2>&1
  echo "elapsed_s $(( $(date +%s) - t0 ))" >> "$f"
  fo=$(grep -cE "^\[gbrain\].*(failed|exceeded|fail-open)" "$f" || true)
  printf '%-10s %-16s %s  lignes-en-échec=%s\n' "$label" "$name-$mode$n" "$(grep -E '^Mean' "$f" | tail -1)" "$fo"
done
