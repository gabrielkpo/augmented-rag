#!/usr/bin/env bash
# gbrain nightly hygiene: refreshes the summaries that lag behind their timeline.
#
# 1. Recomputes the freshness indicator (ops/freshness/freshness.sh).
# 2. Takes the HYGIENE_MAX pages (default 5) that lag the most beyond
#    HYGIENE_SEUIL days (default 14).
# 3. Hands them to Claude Code in non-interactive mode, limited to the gbrain
#    page read and write tools. Every write makes a commit in
#    ~/gbrain-content: everything can be undone with git.
#
# The prompt is ops/hygiene/refresh-prompt.md, or the file named by
# HYGIENE_PROMPT_FILE when set; the list of pages is appended to it.
#
# Usage: ops/hygiene/refresh-stale.sh [--dry-run] [slug ...]
#   --dry-run  prints the chosen pages and the prompt, without running Claude
#   slug ...   processes these pages instead of the most lagging ones
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
max="${HYGIENE_MAX:-5}"
seuil="${HYGIENE_SEUIL:-14}"
model="${HYGIENE_MODEL:-claude-sonnet-5-5}"
log_dir="$HOME/.gbrain/hygiene"
mkdir -p "$log_dir"
log="$log_dir/$(date +%F).log"

dry=0
if [ "${1:-}" = "--dry-run" ]; then dry=1; shift; fi

"$here/../freshness/freshness.sh" "$seuil"

pages="$(python3 -I - "$HOME/.gbrain/freshness/latest.json" "$seuil" "$max" "$@" <<'EOF'
import json, sys
path, seuil, mx, wanted = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4:]
rows = [json.loads(l) for l in open(path, encoding='utf-8') if l.strip()]
# Originals hold the owner's exact wording: never rewritten automatically.
rows = [r for r in rows if r['type'] != 'original']
if wanted:
    rows = [r for r in rows if r['slug'] in wanted]
else:
    rows = [r for r in rows if r['retard_jours'] > seuil][:mx]
for r in rows:
    print(f"- `{r['slug']}` ({r['type']}) : synthèse réécrite le {r['synthese_reecrite_le']}, "
          f"{r['evenements_apres']} événement(s) après, le dernier le {r['dernier_evenement']}")
EOF
)"

if [ -z "$pages" ]; then
  echo "$(date '+%F %T') hygiène : aucune page en retard au-delà de $seuil jours" | tee -a "$log"
  exit 0
fi

prompt="$(cat "${HYGIENE_PROMPT_FILE:-$here/refresh-prompt.md}")
$pages"

if [ "$dry" = 1 ]; then
  printf '%s\n' "$prompt"
  exit 0
fi

{
  echo "========== $(date '+%F %T') — hygiène nocturne ($model) =========="
  printf '%s\n' "$pages"
  echo "---"
} >> "$log"

claude -p "$prompt" \
  --model "$model" \
  --allowedTools "mcp__gbrain__get_page,mcp__gbrain__get_timeline,mcp__gbrain__put_page,mcp__gbrain__add_timeline_entry" \
  >> "$log" 2>&1 || echo "claude a terminé avec le code $?" >> "$log"

echo "--- fin $(date '+%F %T')" >> "$log"
tail -n 20 "$log"
