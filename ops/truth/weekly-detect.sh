#!/usr/bin/env bash
# Weekly detection of stale pages (truth maintenance, gbrain 2.0).
#
# Reads the live brain READ-ONLY (SELECT only), on the pairs where one page
# changed in the last 8 days, has the pairs judged by Claude (`claude -p`:
# Sonnet triage, Sonnet judgement, blind Opus confirmation) with a cap on
# calls, and writes a PROPOSAL:
#     ~/.gbrain/health/truth/<date>/supersessions.json
# Nothing is ever written to the brain: the owner reviews it, then
# `superseded_by` is set in the frontmatter of the accepted pages.
#
# Optional: TRUTH_REVIEWER names the reviewer in the proposal's status line;
# TRUTH_KNOWN_PAIR ("slug-a,slug-b") makes candidates.py report whether a
# known pair was found (see ops/eval/truth/candidates.py).
#
# Usage: ops/truth/weekly-detect.sh [--max-judge N]   (default 25 pairs per stage)
set -euo pipefail
here="$(cd "$(dirname "$0")/../.." && pwd)"
max_judge=25
[ "${1:-}" = "--max-judge" ] && max_judge="${2:?}"
day="$(date +%Y-%m-%d)"
work="$HOME/.gbrain/health/truth/$day"
mkdir -p "$work"
db="$(python3 -I -c "import json,os;print(json.load(open(os.path.expanduser('~/.gbrain/config.json')))['database_url'])")"

python3 -I "$here/ops/eval/truth/candidates.py" --db "$db" --live-readonly --since-days 8 \
  --out "$work/candidates.json" > "$work/candidates.log" 2>&1
n="$(python3 -I -c "import json,sys;print(len(json.load(open(sys.argv[1]))['pairs']))" "$work/candidates.json")"
echo "paires candidates (pages modifiées depuis 8 jours) : $n"
if [ "$n" -eq 0 ]; then echo "rien à juger"; exit 0; fi

python3 -I "$here/ops/eval/truth/judge_pairs.py" --db "$db" --live-readonly --work "$work" \
  --max-judge "$max_judge" --jobs 2 > "$work/judge.log" 2>&1 || true

TRUTH_WORK="$work" TRUTH_OUT="$work/supersessions.json" TRUTH_DATE="$day" \
TRUTH_BASE_LABEL="brain en service, lecture seule" \
  python3 -I "$here/ops/eval/truth/apply_lab.py" --build > "$work/build.log" 2>&1
python3 -I - "$work/supersessions.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
ok = [p for p in d['paires' if 'paires' in d else 'pairs'] if p.get('applique_labo')] if isinstance(d, dict) else []
print(f"propositions retenues : {len(ok)}")
for p in ok:
    print(f"  {p['remplacee']}  ->  {p['fait_foi']}  ({p['confiance']})")
PY
echo "proposition : $work/supersessions.json (à valider${TRUTH_REVIEWER:+ par $TRUTH_REVIEWER}, rien n'est appliqué)"
