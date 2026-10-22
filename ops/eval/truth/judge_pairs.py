#!/usr/bin/env python3
"""Claude judges the candidate pairs: which page is authoritative, which is superseded, and why.

Stages, all through `claude -p` (subscription, no tools, hooks off), never
through gbrain's model:
  tri (triage)  : batches of 40 pairs, on a short card (type, title, dates,
             status, aliases, start of the text, freshness). Recall first:
             only the pairs that MAY be about the same subject are kept.
  jugement (judgement): pair by pair, on both full pages (truncated at
             7,000 characters), the most likely first (alias, banner,
             different types), with a structured verdict (Sonnet).
  confirmation : the pairs judged "remplacee" or "doublon" are judged
             again, blind, by Opus (work/confirmed.json); only a confirmed
             verdict becomes a proposal.

Reads the LAB database only (unless --live-readonly). Output:
ops/eval/truth/work/judged.json (resumable: a pair already judged is not
judged again), then apply_lab.py --build turns it into supersessions.json.
The default --db is $GBRAIN_LAB_DB_PREFIX/gbrain_lab_truth (prefix default
postgres://<current user>@localhost:5432), unless TRUTH_DB is set.

Usage: python3 -I ops/eval/truth/judge_pairs.py [--stage tri|jugement|confirmation|tout]
       [--tri-model claude-sonnet-5-5] [--model claude-sonnet-5-5] [--confirm-model claude-opus-5-5] [--jobs 4]
"""
import argparse
import concurrent.futures
import getpass
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, 'work')
DEFAULT_DB = (os.environ.get('GBRAIN_LAB_DB_PREFIX') or f'postgres://{getpass.getuser()}@localhost:5432') + '/gbrain_lab_truth'
LIMIT_HIT = []
# Empty folder outside the repository: no project CLAUDE.md nor neighbouring files loaded into the context.
NEUTRAL_CWD = os.path.join(os.environ.get('TMPDIR', '/tmp'), 'truth-claude-cwd')
os.makedirs(NEUTRAL_CWD, exist_ok=True)

TRI_PROMPT = """Tu aides à maintenir la vérité d'une base de connaissances personnelle (pages Markdown sur des systèmes, projets, process, personnes, concepts et principes « originals »).

Voici des paires de pages proches. Pour chacune, dis si les deux pages PEUVENT décrire la même entité ou le même sujet, de sorte que l'une soit un doublon de l'autre, ou une version plus ancienne qu'une autre page a remplacée (ex. une page projet qui décrit la conception initiale d'un agent, et la page système qui décrit l'agent tel qu'il tourne aujourd'hui).
NE retiens PAS deux sujets voisins mais différents (deux personnes, deux projets d'une même équipe, un système et un process qui l'utilise, un principe et le projet où il est né), sauf si l'une reprend la description de l'autre.
Sois large : un second passage lira les pages en entier. En cas de doute, retiens.

{pairs}

Réponds sur une seule ligne, en JSON : {{"retenues": [<numéros des paires retenues>]}}"""

JUDGE_PROMPT = """Tu maintiens la vérité d'une base de connaissances personnelle. Deux pages proches, A et B. Lis-les et décide ce qu'un lecteur qui pose une question sur l'état ACTUEL du sujet doit lire.

Verdicts possibles :
- "remplacee" : les deux pages décrivent la même chose (même entité, même agent, même processus...) et l'une est dépassée : conception initiale, état ancien, chiffres ou architecture qui ne tiennent plus, ou elle renvoie elle-même vers l'autre. L'autre fait foi. Une page qui reste juste comme HISTORIQUE (journal d'un chantier, banc d'essai, décision datée) mais dont la description du sujet est dépassée est bien « remplacee » : le lien veut dire « pour l'état actuel, lire l'autre ».
- "doublon" : même sujet, même époque, contenus qui se recouvrent ; aucune n'est fausse, mais une seule devrait faire référence (canonique).
- "complementaires" : même entité ou sujet, mais des aspects différents ; les deux restent valables, aucune ne contredit l'autre.
- "distinctes" : sujets différents.

Règles :
- Date un fait par le moment qu'il décrit, pas par la date de la page qui le rapporte.
- Une contradiction précise entre A et B (architecture, modèle utilisé, fréquence, chiffres, propriétaire, statut) est la meilleure preuve : cite-la dans la justification.
- Ne propose « remplacee » que si un lecteur qui lirait la page remplacée pour l'état actuel se tromperait réellement.
- Confiance entre 0 et 1 : 0,9 et plus seulement si la page remplacée contredit l'autre sur un point vérifiable, ou si elle le dit elle-même.

=== Page A : {a} ({at}, créée {ac}, mise à jour {au}, statut {ast}) {afresh}
{atext}

=== Page B : {b} ({bt}, créée {bc}, mise à jour {bu}, statut {bst}) {bfresh}
{btext}

Réponds sur une seule ligne, en JSON :
{{"verdict": "remplacee|doublon|complementaires|distinctes", "fait_foi": "<slug de la page qui fait foi, ou null>", "remplacee": "<slug de la page dépassée ou du doublon à rétrograder, ou null>", "contradiction": "<le point précis où la page dépassée se trompe aujourd'hui, ou null>", "justification": "<deux phrases au plus>", "confiance": <0 à 1>}}"""


def psql_rows(db, sql):
    out = subprocess.run(['psql', '-X', '-At', db, '-c', sql], capture_output=True, text=True, check=True).stdout
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def claude(prompt, model, timeout=600):
    proc = subprocess.run(
        ['claude', '-p', prompt, '--model', model, '--settings', json.dumps({'disableAllHooks': True}),
         '--tools', '', '--no-session-persistence',
         # None of the user's MCP servers: their instructions sometimes inflated the context past 1M tokens.
         '--strict-mcp-config', '--mcp-config', '{"mcpServers": {}}'],
        capture_output=True, text=True, timeout=timeout, cwd=NEUTRAL_CWD, stdin=subprocess.DEVNULL)
    m = re.search(r'\{.*\}', proc.stdout, re.S)
    if not m:
        raise RuntimeError((proc.stdout or proc.stderr)[-300:])
    return json.loads(m.group(0))


def load_texts(db, slugs):
    quoted = ','.join("'" + s.replace("'", "''") + "'" for s in slugs)
    rows = psql_rows(db, f"select json_build_object('slug', slug, 'text', compiled_truth) from pages where deleted_at is null and slug in ({quoted})")
    return {r['slug']: r['text'] or '' for r in rows}


def fresh_note(info):
    f = info.get('freshness')
    return f"[en retard : {f['evenements_apres']} événements de timeline après la dernière réécriture du {f['synthese_reecrite_le']}, jusqu'au {f['dernier_evenement']}]" if f else ''


def card(slug, info, text):
    head = re.sub(r'\s+', ' ', text)[:420]
    al = ', '.join(str(a) for a in info.get('aliases') or [])[:120]
    return (f"{slug} ({info['type']}, créée {info.get('created')}, mise à jour {info.get('updated') or '?'}, "
            f"statut {info.get('status') or '?'}{', alias : ' + al if al else ''}) {fresh_note(info)}\n    « {head} »")


def stage_tri(cand, texts, model, jobs, path):
    done = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else {}
    pairs = [p for p in cand['pairs'] if f"{p['a']}|{p['b']}" not in done]
    info = cand['page_info']
    batches = [pairs[i:i + 40] for i in range(0, len(pairs), 40)]

    def run(batch):
        lines = []
        for i, p in enumerate(batch, 1):
            lines.append(f"Paire {i} (similarité {p['sim']}, {', '.join(p['why'])})\n  A. {card(p['a'], info[p['a']], texts[p['a']])}\n  B. {card(p['b'], info[p['b']], texts[p['b']])}")
        res = claude(TRI_PROMPT.format(pairs='\n\n'.join(lines)), model)
        keep = {int(x) for x in res.get('retenues', [])}
        return {f"{p['a']}|{p['b']}": (i in keep) for i, p in enumerate(batch, 1)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as ex:
        for res in ex.map(run, batches):
            done.update(res)
            json.dump(done, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
            print(f'tri : {len(done)} paires vues, {sum(done.values())} retenues', flush=True)
    return done


def stage_judge(cand, texts, keep, model, jobs, path, max_pairs=0):
    done = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else {}
    info = cand['page_info']
    done = {k: v for k, v in done.items() if v.get('verdict') != 'erreur'}  # a pair in error is judged again
    todo = [p for p in cand['pairs'] if keep(f"{p['a']}|{p['b']}") and f"{p['a']}|{p['b']}" not in done]
    # The most likely pairs first: if the usage limit hits, they have been judged.
    rank = lambda p: (0 if any(w.startswith(('alias', 'bandeau')) for w in p['why']) else 1 if info[p['a']]['type'] != info[p['b']]['type'] else 2, -(p['sim'] or 0))
    todo.sort(key=rank)
    if max_pairs > 0:
        todo = todo[:max_pairs]  # cap on calls per run (Claude subscription quota)

    def run(p):
        a, b = info[p['a']], info[p['b']]
        prompt = JUDGE_PROMPT.format(
            a=p['a'], at=a['type'], ac=a.get('created'), au=a.get('updated') or '?', ast=a.get('status') or '?', afresh=fresh_note(a),
            atext=texts[p['a']][:7000], b=p['b'], bt=b['type'], bc=b.get('created'), bu=b.get('updated') or '?',
            bst=b.get('status') or '?', bfresh=fresh_note(b), btext=texts[p['b']][:7000])
        if LIMIT_HIT:
            return None, None
        try:
            v = claude(prompt, model)
        except Exception as e:  # noqa: BLE001 — a failing pair must not stop the judgement
            if re.search(r'spend limit|usage limit|session limit|rate limit', str(e), re.I):
                LIMIT_HIT.append(str(e)[:200])
                return None, None
            v = {'verdict': 'erreur', 'justification': str(e)[:200]}
        v.update({'a': p['a'], 'b': p['b'], 'sim': p['sim'], 'why': p['why'], 'model': model})
        return f"{p['a']}|{p['b']}", v

    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as ex:
        for k, v in ex.map(run, todo):
            if k is None:
                continue
            done[k] = v
            json.dump(done, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            print(f"{v.get('verdict', '?'):16} {v.get('confiance', '')!s:5} {k}", flush=True)
    if LIMIT_HIT:
        print(f'arrêt : limite d\'usage atteinte ({LIMIT_HIT[0]}) ; relancer la même commande reprend où on en est', file=sys.stderr)
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=os.environ.get('TRUTH_DB', DEFAULT_DB))
    ap.add_argument('--stage', default='tout', choices=['tri', 'jugement', 'confirmation', 'tout'])
    ap.add_argument('--tri-model', default='claude-sonnet-5-5')
    ap.add_argument('--model', default='claude-sonnet-5-5')
    ap.add_argument('--confirm-model', default='claude-opus-5-5')
    ap.add_argument('--jobs', type=int, default=4)
    ap.add_argument('--work', default=WORK, help='folder of the working files (candidates.json, tri.json...)')
    ap.add_argument('--max-judge', type=int, default=0, help='at most N pairs judged per stage (0 = no cap)')
    ap.add_argument('--live-readonly', action='store_true',
                    help='allow the live database (read-only: this script only runs SELECTs)')
    args = ap.parse_args()
    if 'gbrain_lab_' not in args.db and not args.live_readonly:
        raise SystemExit('refus : base hors labo sans --live-readonly')
    work = args.work
    cand = json.load(open(os.path.join(work, 'candidates.json'), encoding='utf-8'))
    slugs = {s for p in cand['pairs'] for s in (p['a'], p['b'])}
    texts = load_texts(args.db, slugs)
    tri_path = os.path.join(work, 'tri.json')
    if args.stage in ('tri', 'tout'):
        tri = stage_tri(cand, texts, args.tri_model, args.jobs, tri_path)
    else:
        tri = json.load(open(tri_path, encoding='utf-8'))
    judged_path = os.path.join(work, 'judged.json')
    if args.stage in ('jugement', 'tout'):
        stage_judge(cand, texts, lambda k: tri.get(k), args.model, args.jobs, judged_path, args.max_judge)
    if args.stage in ('confirmation', 'tout'):
        judged = json.load(open(judged_path, encoding='utf-8'))
        stage_judge(cand, texts, lambda k: judged.get(k, {}).get('verdict') in ('remplacee', 'doublon'),
                    args.confirm_model, args.jobs, os.path.join(work, 'confirmed.json'), args.max_judge)
    print('fini', file=sys.stderr)


if __name__ == '__main__':
    main()
