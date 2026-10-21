#!/usr/bin/env bash
# Brain health, weekly part: question bench and replay of the real searches.
#
# 1. Bench: the 107 questions of ops/eval/qrels-v2.json with the production
#    configuration (Jev reranker included: sends the questions and passages
#    to TypeSafe, a few cents).
# 2. Replay: the real searches captured during the past week (eval.capture)
#    are run again; a low Jaccard means the brain returns other pages than
#    at search time (gbrain update, setting, reindexing).
# Writes a row into ~/.gbrain/health/bench.tsv, read by health.sh.
#
# Run by the nightly job on Sundays (step 2c), or by hand.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
out="$HOME/.gbrain/health"
captures="$out/captures"
mkdir -p "$captures"
today="$(date +%F)"

bench_out="$(gbrain eval --qrels "$here/../eval/qrels-v2.json" --limit 10 --k 5 2>/dev/null)"
mean="$(printf '%s\n' "$bench_out" | grep '^Mean' | tail -1)"
questions="$(printf '%s\n' "$bench_out" | grep -oE '[0-9]+ queries' | head -1 | cut -d' ' -f1)"

# Replay of the previous snapshot, then a new snapshot of the past week.
previous="$(ls "$captures"/*.ndjson 2>/dev/null | grep -v "/$today.ndjson" | tail -1 || true)"
jaccard=""; top1=""
if [ -n "$previous" ] && [ -s "$previous" ]; then
  replay="$(gbrain eval replay --against "$previous" --json 2>/dev/null | tail -1)"
  jaccard="$(printf '%s' "$replay" | python3 -I -c "import json,sys;print(round(json.load(sys.stdin)['summary']['mean_jaccard'],3))" 2>/dev/null || true)"
  top1="$(printf '%s' "$replay" | python3 -I -c "import json,sys;print(round(json.load(sys.stdin)['summary']['top1_stability_rate'],3))" 2>/dev/null || true)"
fi
gbrain eval export --since 7d > "$captures/$today.ndjson" 2>/dev/null || true

if [ -z "$mean" ]; then
  echo "$(date '+%F %T') banc hebdomadaire : pas de ligne Mean, banc non enregistré" >&2
  exit 1
fi
# Mean  P@5  R@5  MRR  nDCG@5
read -r _ p5 r5 mrr ndcg <<< "$mean"

tsv="$out/bench.tsv"
[ -s "$tsv" ] || printf 'date\tquestions\tp5\trecall5\tmrr\tndcg5\trejeu_jaccard\trejeu_top1\n' > "$tsv"
grep -v "^$today	" "$tsv" > "$tsv.tmp" || true
printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$today" "${questions:-}" "$p5" "$r5" "$mrr" "$ndcg" "$jaccard" "$top1" >> "$tsv.tmp"
mv "$tsv.tmp" "$tsv"
echo "banc $today : R@5 $r5 · MRR $mrr · nDCG@5 $ndcg · rejeu Jaccard ${jaccard:-—} · top-1 ${top1:-—}"
