#!/usr/bin/env bash
# Search bench of the truth track, on a lab copy (never the live brain).
# Adapted copy of ops/eval/run-bench.sh: goes through ~/gbrain-labs/bin/gbrain-lab, and compares
# the demotion variants for superseded pages, without and with the Jev reranker.
#
# Usage: ops/eval/truth/bench.sh <v2|holdout-a> <variant> <jev|sans-jev>
#   Lab: TRUTH_LAB (default truth2, fresh native brain). The state of the lab must match the variant:
#     base         no `superseded_by` frontmatter, no `supersedes` links (apply_lab.py --unfrontmatter / --remove)
#     native       `supersedes` links set (apply_lab.py --links): native ×0.5 penalty, BEFORE the reranker
#     penalty      frontmatter set (apply_lab.py --frontmatter), no links, GBRAIN_SUPERSEDE=penalty (×0.5 on the
#                  reciprocal rank, AFTER the reranker)
#     penalty25    same, factor 0.25
#     behind       the authoritative page takes the slot, the superseded page follows it
#     substitute   the authoritative page takes the slot, the superseded page leaves the list
#   SUPERSEDE_MAP=<file> replaces the frontmatter with a map (lab `truth`, where no page is written).
#   Lab database: $GBRAIN_LAB_DB_PREFIX/gbrain_lab_<lab> (prefix default postgres://$USER@localhost:5432).
# Holdout B is sealed: it is not offered here.
# The script restores the lab's search.reranker.enabled to its initial state on exit.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
qdir="${QRELS_DIR:-$HOME/gbrain/ops/eval}"
case "${1:-}" in
  v2) qrels="$qdir/qrels-v2.json" ;;
  holdout-a) qrels="$qdir/qrels-holdout-a.json" ;;
  *) echo "usage: $0 <v2|holdout-a> <variante> <jev|sans-jev>" >&2; exit 2 ;;
esac
variant="${2:?variante}"; jev="${3:?jev|sans-jev}"
name="${TRUTH_LAB:-truth2}"
case "$name" in truth|truth2) ;; *) echo "labo inconnu : $name" >&2; exit 2 ;; esac
dburl="${GBRAIN_LAB_DB_PREFIX:-postgres://${USER:-$(id -un)}@localhost:5432}/gbrain_lab_$name"
lab() { GBRAIN_SRC="$repo" "$HOME/gbrain-labs/bin/gbrain-lab" "$name" "$@"; }

db="$(lab config get database_url 2>/dev/null | grep -E '^postgres' | tail -1)"
case "$db" in *"gbrain_lab_$name") ;; *) echo "refus : la commande ne vise pas la base de labo ($db)" >&2; exit 3 ;; esac
links="$(psql -X -At "$dburl" -c "select count(*) from links where link_type='supersedes'")"
fm="$(psql -X -At "$dburl" -c "select count(*) from pages where deleted_at is null and frontmatter ? 'superseded_by'")"

unset GBRAIN_SUPERSEDE GBRAIN_SUPERSEDE_PENALTY GBRAIN_SUPERSEDE_MAP
# Three tracks run in parallel on the machine: with the default timeout (6 s), some queries
# lose their embedding and fall back to keyword search. Same timeout for every variant.
# (Keyword search has its own cap, search.keyword_statement_timeout_ms, set to 30 s in the lab config.)
export GBRAIN_QUERY_EMBED_TIMEOUT_MS="${GBRAIN_QUERY_EMBED_TIMEOUT_MS:-30000}"
marked() { [ "$fm" != 0 ] || [ -n "${SUPERSEDE_MAP:-}" ]; }
case "$variant" in
  base) [ "$links" = 0 ] && [ "$fm" = 0 ] || { echo "variante base : retirer d'abord liens ($links) et frontmatter ($fm)" >&2; exit 4; } ;;
  native) [ "$links" != 0 ] || { echo "variante native : poser d'abord les liens (apply_lab.py --links)" >&2; exit 4; } ;;
  penalty|penalty25|behind|substitute)
    [ "$links" = 0 ] || { echo "variante $variant : retirer d'abord les liens" >&2; exit 4; }
    marked || { echo "variante $variant : poser d'abord le frontmatter (apply_lab.py --frontmatter)" >&2; exit 4; }
    [ -n "${SUPERSEDE_MAP:-}" ] && export GBRAIN_SUPERSEDE_MAP="$SUPERSEDE_MAP"
    case "$variant" in
      penalty) export GBRAIN_SUPERSEDE=penalty GBRAIN_SUPERSEDE_PENALTY=0.5 ;;
      penalty25) export GBRAIN_SUPERSEDE=penalty GBRAIN_SUPERSEDE_PENALTY=0.25 ;;
      *) export GBRAIN_SUPERSEDE="$variant" ;;
    esac ;;
  *) echo "variante inconnue : $variant" >&2; exit 2 ;;
esac

case "$jev" in jev) rerank=true ;; sans-jev) rerank=false ;; *) echo "jev|sans-jev" >&2; exit 2 ;; esac
orig="$(lab config get search.reranker.enabled 2>/dev/null | grep -E '^(true|false)$' | tail -1 || true)"
restore() { [ -n "$orig" ] && lab config set search.reranker.enabled "$orig" >/dev/null 2>&1 || true; }
trap restore EXIT
lab config set search.reranker.enabled "$rerank" >/dev/null

out="$here/results"; mkdir -p "$out"
file="$out/$(date +%Y%m%d-%H%M%S)-$name-$1-$variant-$jev.txt"
echo "== $name · $1 · $variant · $jev (liens supersedes : $links, frontmatter superseded_by : $fm, carte : ${GBRAIN_SUPERSEDE_MAP:-aucune})"
lab eval --qrels "$qrels" --limit 10 --k 5 > "$file" 2>&1
lost="$(grep -cE 'embeds failed|arm failed' "$file" || true)"
grep -vE 'UPGRADE|self-upgrade|eval.single' "$file" | grep -iE '^(mean|average)|mean ' | head -4 || true
[ "$lost" = 0 ] || echo "ATTENTION : $lost requête(s) dégradée(s) (embedding perdu ou bras expiré), mesure à refaire"
echo "Résultat complet : $file"
