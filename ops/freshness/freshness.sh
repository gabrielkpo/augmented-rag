#!/usr/bin/env bash
# gbrain freshness indicator: which summaries lag behind their timeline.
# Read-only (one SQL query), no external call.
#
# Usage: ops/freshness/freshness.sh [threshold_days]   (default: 14)
# Writes:
#   ~/.gbrain/freshness/latest.json   one lagging page per line (JSON), for the nightly job
#   ~/.gbrain/freshness/YYYY-MM-DD.md readable report
#   ~/.gbrain/freshness/history.tsv   date, knowledge pages, lagging, beyond the threshold
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
seuil="${1:-14}"
out="$HOME/.gbrain/freshness"
mkdir -p "$out"
url="$(python3 -I -c "import json;print(json.load(open('$HOME/.gbrain/config.json'))['database_url'])")"

psql "$url" -At -f "$here/freshness.sql" > "$out/latest.json"
total="$(psql "$url" -At -c "select count(*) from pages where deleted_at is null and type in ('system','project','process','person','partner','concept','original')")"

python3 -I - "$out/latest.json" "$seuil" "$total" "$out" <<'EOF'
import json, sys, datetime, os
path, seuil, total, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
rows = [json.loads(l) for l in open(path, encoding='utf-8') if l.strip()]
over = [r for r in rows if r['retard_jours'] > seuil]
today = datetime.date.today().isoformat()
lines = [
    f"# Fraîcheur des pages gbrain — {today}", "",
    f"- Pages de connaissance : **{total}**",
    f"- Synthèse en retard sur la timeline : **{len(rows)}**",
    f"- Retard de plus de {seuil} jours : **{len(over)}**", "",
    "| Page | Type | Synthèse réécrite le | Événements après | Dernier événement | Retard (jours) |",
    "|---|---|---|---|---|---|",
]
lines += [f"| `{r['slug']}` | {r['type']} | {r['synthese_reecrite_le']} | {r['evenements_apres']} | {r['dernier_evenement']} | {r['retard_jours']} |" for r in rows]
open(os.path.join(out, f"{today}.md"), 'w', encoding='utf-8').write("\n".join(lines) + "\n")
with open(os.path.join(out, 'history.tsv'), 'a', encoding='utf-8') as h:
    h.write(f"{today}\t{total}\t{len(rows)}\t{len(over)}\n")
print(f"fraîcheur : {len(rows)} pages en retard sur {total}, dont {len(over)} de plus de {seuil} jours — {out}/{today}.md")
EOF
