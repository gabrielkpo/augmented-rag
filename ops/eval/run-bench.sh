#!/usr/bin/env bash
# gbrain search test bench: the same question set (qrels.json), run under
# several configurations, to find out whether a setting really helps.
#
#   a: no reranker      — local, only the questions go to OpenAI (embeddings)
#   b: Jev reranker     — sends the question and the candidate passages to TypeSafe
#   c: Jev + autocut    — same, and the list is cut where the scores drop
#
# Usage: ops/eval/run-bench.sh [a|b|c|all]    (default: all)
# Cost:  b and c ≈ $0.02 each for 33 questions; gbrain caps Jev at $1/day.
# The script restores the search configuration to its initial state on exit.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
qrels="${QRELS:-$here/qrels.json}"   # QRELS=…/qrels-v2.json for the wider bench
out="$here/results/$(date +%Y%m%d-%H%M%S)-$(basename "$qrels" .json)"
mkdir -p "$out"

cfg_get() { gbrain config get "$1" 2>/dev/null | grep -vE 'UPGRADE|self-upgrade' | tail -1 || true; }
cfg_put() { if [ -n "$2" ]; then gbrain config set "$1" "$2" >/dev/null; else gbrain config unset "$1" >/dev/null 2>&1 || true; fi; }

orig_rerank="$(cfg_get search.reranker.enabled)"
orig_autocut="$(cfg_get search.autocut)"
restore() {
  cfg_put search.reranker.enabled "$orig_rerank"
  cfg_put search.autocut "$orig_autocut"
  echo "Configuration restaurée (reranker=${orig_rerank:-défaut}, autocut=${orig_autocut:-défaut})."
}
trap restore EXIT

run() {
  local name="$1" rerank="$2" autocut="$3"
  gbrain config set search.reranker.enabled "$rerank" >/dev/null
  gbrain config set search.autocut "$autocut" >/dev/null
  echo "== $name (reranker=$rerank, autocut=$autocut)"
  gbrain eval --qrels "$qrels" --limit 10 --k 5 > "$out/$name.txt" 2>&1
  grep -vE 'UPGRADE|self-upgrade' "$out/$name.txt" | grep -iE 'mean|average|precision|recall|mrr|ndcg|queries' | head -8
}

case "${1:-all}" in
  a)   run a-sans-reranker false false ;;
  b)   run b-jev true false ;;
  c)   run c-jev-autocut true true ;;
  all) run a-sans-reranker false false; run b-jev true false; run c-jev-autocut true true ;;
  *)   echo "usage: $0 [a|b|c|all]" >&2; exit 2 ;;
esac

echo "Résultats complets : $out"
