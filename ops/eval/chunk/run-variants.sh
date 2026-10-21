#!/usr/bin/env bash
# Measures an index state of the chunk track on the three benches (qrels-chunk, v2, holdout A).
# Adapted copy of ops/eval/run-bench.sh: goes through lab.sh, so never through the live database.
#
#   ops/eval/chunk/run-variants.sh <db: a|b|c|d> <label> [jev: off|on|both]   (default both)
#
# off  : one run without reranker
# on   : two runs with Jev + RRF fusion (Jev varies from one call to the next)
# both : all three
# Holdout B is never run. The database's search configuration is restored on exit.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
db="$1"; label="$2"; jev="${3:-both}"
out="$here/results/$label"
mkdir -p "$out"
banks=("$here/qrels-chunk.json" "$HOME/gbrain/ops/eval/qrels-v2.json" "$HOME/gbrain/ops/eval/qrels-holdout-a.json")

orig="$("$here/lab.sh" "$db" config get search.reranker.enabled 2>/dev/null | tail -1 || true)"
restore() { "$here/lab.sh" "$db" config set search.reranker.enabled "${orig:-true}" >/dev/null 2>&1 || true; }
trap restore EXIT

pass() {
  local mode="$1" n="$2" enabled=false
  [ "$mode" = jev ] && enabled=true
  "$here/lab.sh" "$db" config set search.reranker.enabled "$enabled" >/dev/null 2>&1
  for q in "${banks[@]}"; do
    local name; name="$(basename "$q" .json | sed 's/^qrels-//')"
    local f="$out/$name-$mode$n.txt" t0 t1
    t0=$(python3 -c 'import time;print(time.time())')
    "$here/lab.sh" "$db" eval --qrels "$q" --limit 10 --k 5 > "$f" 2>&1
    t1=$(python3 -c 'import time;print(time.time())')
    echo "elapsed_s $(python3 -c "print(round($t1-$t0,1))")" >> "$f"
    local fo; fo=$(grep -E "^\[gbrain\].*(failed|exceeded|fail-open)" "$f" | grep -vc "^\[eval" || true)
    printf '%-12s %-14s %s  fail-open=%s\n' "$label" "$name-$mode$n" "$(grep -E '^Mean' "$f" | tail -1)" "$fo"
  done
}

case "$jev" in
  off)  pass nojev 1 ;;
  on)   pass jev 1; pass jev 2 ;;
  both) pass nojev 1; pass jev 1; pass jev 2 ;;
  *) echo "usage: $0 <base> <étiquette> [off|on|both]" >&2; exit 2 ;;
esac
