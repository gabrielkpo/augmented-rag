#!/usr/bin/env bash
# Replays the chunk track end to end, in the order it was measured on 2026-10-21.
#
#   ops/eval/chunk/run-all.sh prepare   database copies, measurement settings, export, Haiku generation
#   ops/eval/chunk/run-all.sh build     vectors and keyword vectors of states B to F
#   ops/eval/chunk/run-all.sh measure   the 9 states: without Jev, then Jev x2 on the retained states
#
# Databases: a = gbrain_lab_chunk (title only, current state), b..f = copies (createdb -T).
# Never touches the live database: everything goes through lab.sh, which refuses a database outside gbrain_lab_chunk*.
# Run the measurements one or two at a time at most: Postgres is shared (max_connections = 100).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../../.." && pwd)"
apply() { (cd "$root" && bun run ops/eval/chunk/apply-variant.ts "$@"); }
url="$(python3 -I -c "import json,os;print(json.load(open(os.path.expanduser('~/gbrain-labs/chunk/.gbrain/config.json')))['database_url'])")"

case "${1:-}" in
  prepare)
    for v in b c d f; do createdb -T gbrain_lab_chunk "gbrain_lab_chunk_$v" --maintenance-db="$url"; done
    # MEASUREMENT settings (shared, loaded machine): without them, search arms and Jev calls
    # time out and silently fall back to a degraded order. Production keeps its values (8 s, 5 s).
    for v in a b c d f; do
      "$here/lab.sh" "$v" config set search.keyword_statement_timeout_ms 60000
      "$here/lab.sh" "$v" config set search.reranker.timeout_ms 30000
    done
    (cd "$root" && bun run ops/eval/chunk/export-pages.ts)
    python3 -I "$here/gen-context.py" --jobs 6
    python3 -I "$here/gen-context.py" --jobs 3   # retry of the failed pages
    ;;
  build)
    apply --db b --text ctx --fts on              # B: context, embedding + keyword vector
    apply --db c --text q --fts on                # C: doc2query, embedding + keyword vector
    apply --db d --text both --fts on             # D: context + doc2query, embedding + keyword vector
    createdb -T gbrain_lab_chunk_b gbrain_lab_chunk_e --maintenance-db="$url"
    apply --db e --text q --fts on --emb off      # E: context in the embedding, doc2query in the keyword vector
    apply --db f --text q --fts on --emb off      # F: doc2query in the keyword vector only, original embedding
    ;;
  measure)
    "$here/run-variants.sh" a A-titre both
    for v in b:ctx:B-ctx c:q:C-d2q d:both:D-ctx-d2q; do
      IFS=: read -r db text label <<<"$v"
      apply --db "$db" --text "$text" --fts off --emb off
      "$here/run-variants.sh" "$db" "$label-emb" off
      apply --db "$db" --text "$text" --fts on --emb off
      "$here/run-variants.sh" "$db" "$label-emb-fts" both
    done
    "$here/run-variants.sh" e E-ctx-emb-d2q-fts both
    "$here/run-variants.sh" f F-d2q-fts both
    ;;
  *) echo "usage: $0 prepare | build | measure" >&2; exit 2 ;;
esac
