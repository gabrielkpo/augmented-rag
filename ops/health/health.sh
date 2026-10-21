#!/usr/bin/env bash
# Brain health: daily report, history and alerts.
#
# Read-only, no external call. Run by the nightly job (step 2c).
#
# Usage: ops/health/health.sh
# Writes:
#   ~/.gbrain/health/latest.json    today's report (health.sql + freshness)
#   ~/.gbrain/health/history.tsv    one row per day, the key indicators
#   ~/.gbrain/health/YYYY-MM-DD.md  readable report, alerts first
# Exit code 0 even with alerts: the alerts are in the report and in the
# nightly job's log, not in a failure.
# Optional: GBRAIN_SERVE_LABEL, the scheduler label of the gbrain server
# (e.g. a launchd label), named in the "no MCP search" alert.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
out="$HOME/.gbrain/health"
mkdir -p "$out"
url="$(python3 -I -c "import json;print(json.load(open('$HOME/.gbrain/config.json'))['database_url'])")"

psql "$url" -At -f "$here/health.sql" > "$out/latest.json.tmp"
mv "$out/latest.json.tmp" "$out/latest.json"

python3 -I - "$out" "$HOME/.gbrain/freshness/latest.json" <<'EOF'
import datetime, json, os, sys

out, freshness_path = sys.argv[1], sys.argv[2]
h = json.load(open(os.path.join(out, 'latest.json'), encoding='utf-8'))

# Freshness: computed by ops/freshness (step 2b of the nightly job).
late = []
if os.path.exists(freshness_path):
    late = [json.loads(l) for l in open(freshness_path, encoding='utf-8') if l.strip()]
h['fraicheur'] = {
    'en_retard': len(late),
    'au_dela_14j': sum(1 for r in late if r['retard_jours'] > 14),
}
# Latest weekly bench, if any.
bench = os.path.join(out, 'bench.tsv')
if os.path.exists(bench):
    rows = [l.rstrip('\n').split('\t') for l in open(bench, encoding='utf-8') if l.strip()]
    if len(rows) > 1:
        h['banc'] = dict(zip(rows[0], rows[-1]))
        if len(rows) > 2:
            h['banc_precedent'] = dict(zip(rows[0], rows[-2]))
json.dump(h, open(os.path.join(out, 'latest.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=2)

COLS = [
    ('date', lambda: datetime.date.today().isoformat()),
    ('recherches_7j', lambda: h['usage']['recherches_7j']),
    ('search_p95_ms', lambda: h['latence_ms']['search_p95']),
    ('taux_erreur_recherche', lambda: h['erreurs']['taux_recherche']),
    ('taux_sans_resultat', lambda: h['resultats']['taux_sans_resultat']),
    ('pages_notees_7j', lambda: h['notes']['pages_notees_7j']),
    ('precision_top3', lambda: h['notes']['precision_top3']),
    ('mrr_en_ligne', lambda: h['notes']['mrr_en_ligne']),
    ('orphelines', lambda: h['contenu']['orphelines']),
    ('chunks_sans_embedding', lambda: h['contenu']['chunks_sans_embedding']),
    ('fraicheur_au_dela_14j', lambda: h['fraicheur']['au_dela_14j']),
]
row = {k: ('' if f() is None else str(f())) for k, f in COLS}

hist_path = os.path.join(out, 'history.tsv')
history = []
if os.path.exists(hist_path):
    lines = [l.rstrip('\n').split('\t') for l in open(hist_path, encoding='utf-8') if l.strip()]
    if lines and lines[0] == [k for k, _ in COLS]:
        history = [dict(zip(lines[0], l)) for l in lines[1:]]
history = [r for r in history if r['date'] != row['date']] + [row]
with open(hist_path, 'w', encoding='utf-8') as f:
    f.write('\t'.join(k for k, _ in COLS) + '\n')
    for r in history:
        f.write('\t'.join(r.get(k, '') for k, _ in COLS) + '\n')
previous = history[-2] if len(history) > 1 else None

def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

# Thresholds: an alert = something to look at, not a certain failure.
alerts = []
serve_label = os.environ.get('GBRAIN_SERVE_LABEL')
if h['usage']['recherches_7j'] == 0:
    alerts.append(f"Aucune recherche MCP en 7 jours : le serveur gbrain{f' (launchd {serve_label})' if serve_label else ''} répond-il ?")
if (num(h['latence_ms']['search_p95']) or 0) > 5000:
    alerts.append(f"Recherche lente : p95 à {h['latence_ms']['search_p95']} ms (seuil 5000).")
if (num(h['erreurs']['taux_recherche']) or 0) > 0.02:
    alerts.append(f"Recherches en erreur : {h['erreurs']['taux_recherche']:.1%} (seuil 2 %).")
if (num(h['resultats']['taux_sans_resultat']) or 0) > 0.10:
    alerts.append(f"Recherches sans résultat : {h['resultats']['taux_sans_resultat']:.1%} (seuil 10 %).")
if h['notes']['pages_notees_7j'] >= 10 and (num(h['notes']['precision_top3']) or 0) < 0.6:
    alerts.append(f"Précision en ligne faible : {h['notes']['precision_top3']:.0%} des pages notées du top 3 sont utiles (seuil 60 %).")
if h['notes']['hors_sujet_top3'] > 3:
    alerts.append(f"{h['notes']['hors_sujet_top3']} pages notées hors sujet dans un top 3 cette semaine.")
if h['contenu']['chunks_sans_embedding'] > 0:
    alerts.append(f"{h['contenu']['chunks_sans_embedding']} chunks sans embedding : invisibles pour la recherche vectorielle.")
if h['contenu']['liens_morts'] > 0:
    alerts.append(f"{h['contenu']['liens_morts']} liens vers des pages supprimées.")
if previous and (num(row['orphelines']) or 0) - (num(previous['orphelines']) or 0) > 5:
    alerts.append(f"Orphelines en hausse : {previous['orphelines']} → {row['orphelines']}.")
if h['fraicheur']['au_dela_14j'] > 25:
    alerts.append(f"{h['fraicheur']['au_dela_14j']} synthèses en retard de plus de 14 jours (seuil 25) : l'hygiène de nuit ne suit pas.")
if 'banc' in h and 'banc_precedent' in h:
    for m in ('recall5', 'mrr', 'ndcg5'):
        a, b = num(h['banc_precedent'].get(m)), num(h['banc'].get(m))
        if a is not None and b is not None and a - b > 0.03:
            alerts.append(f"Banc en recul sur {m} : {a:.2f} → {b:.2f} (seuil 3 points).")
    j = num(h['banc'].get('rejeu_jaccard'))
    if j is not None and j < 0.6:
        alerts.append(f"Les vraies recherches de la semaine passée renvoient d'autres pages (Jaccard {j:.2f}, seuil 0,60).")

def fmt(v):
    return '—' if v in (None, '') else v

today = row['date']
md = [f"# Santé du brain — {today}", ""]
md += ["## Alertes", ""] + ([f"- {a}" for a in alerts] or ["Aucune."]) + [""]
md += ["## Indicateurs (7 jours glissants)", "", "| Indicateur | Valeur | Veille |", "|---|---|---|"]
for k, _ in COLS[1:]:
    md.append(f"| {k} | {fmt(row[k])} | {fmt(previous.get(k) if previous else None)} |")
if 'banc' in h:
    b = h['banc']
    md += ["", f"Dernier banc ({b.get('date')}, {b.get('questions')} questions) : "
           f"R@5 {b.get('recall5')} · MRR {b.get('mrr')} · nDCG@5 {b.get('ndcg5')} · "
           f"rejeu Jaccard {fmt(b.get('rejeu_jaccard'))} · top-1 stable {fmt(b.get('rejeu_top1'))}"]
md += ["", "Détail complet : `~/.gbrain/health/latest.json`."]
open(os.path.join(out, f'{today}.md'), 'w', encoding='utf-8').write('\n'.join(md) + '\n')

print(f"santé {today} : {len(alerts)} alerte(s)")
for a in alerts:
    print(f"  ALERTE {a}")
EOF
