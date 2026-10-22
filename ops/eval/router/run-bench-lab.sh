#!/usr/bin/env bash
# Adapted copy of ops/eval/run-bench.sh for the "router" lab copy:
# everything goes through ops/eval/router/lab.sh (database gbrain_lab_router), never through `gbrain` (the live brain).
#
#   a: no reranker      b: Jev reranker (RRF fusion, v2 rubric, as in production)
#
# Usage: ops/eval/router/run-bench-lab.sh <label> [a|b|all]
#   QRELS=…  bench (default ~/gbrain/ops/eval/qrels-v2.json)
#   GBRAIN_THEME_ROUTER=off|hide|route, GBRAIN_THEME_ROUTER_CLASSIFIER=rules|haiku  (variants A/B/C)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
lab="$here/lab.sh"
label="${1:?étiquette}"; which="${2:-all}"
qrels="${QRELS:-$HOME/gbrain/ops/eval/qrels-v2.json}"
out="$here/results/$(date +%Y%m%d-%H%M%S)-$label-$(basename "$qrels" .json)"
mkdir -p "$out"

cfg_get() { "$lab" config get "$1" 2>/dev/null | grep -vE 'UPGRADE|self-upgrade|^\[config\]' | tail -1 || true; }
orig_rerank="$(cfg_get search.reranker.enabled)"
restore() {
  if [ -n "$orig_rerank" ]; then "$lab" config set search.reranker.enabled "$orig_rerank" >/dev/null; fi
  echo "Configuration du labo restaurée (reranker=${orig_rerank:-défaut})."
}
trap restore EXIT

run() {
  local name="$1" rerank="$2"
  "$lab" config set search.reranker.enabled "$rerank" >/dev/null
  echo "== $label $name (reranker=$rerank, router=${GBRAIN_THEME_ROUTER:-off}/${GBRAIN_THEME_ROUTER_CLASSIFIER:-rules})"
  "$lab" eval --qrels "$qrels" --limit 10 --k 5 > "$out/$name.txt" 2>&1
  grep -vE 'UPGRADE|self-upgrade' "$out/$name.txt" | grep -iE 'mean|average|precision|recall|mrr|ndcg|queries' | head -8
}

case "$which" in
  a)   run a-sans-reranker false ;;
  b)   run b-jev true ;;
  all) run a-sans-reranker false; run b-jev true ;;
  *)   echo "usage: $0 <étiquette> [a|b|all]" >&2; exit 2 ;;
esac
echo "Résultats complets : $out"
