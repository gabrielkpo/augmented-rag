#!/usr/bin/env python3
"""Builds the supersessions.json proposal and applies it to the LAB copy.

  --build   reads work/judged.json (Sonnet) and work/confirmed.json (Opus,
            blind) and writes ops/eval/truth/supersessions.json: every pair
            Sonnet judged "remplacee" (superseded) or "doublon" (duplicate),
            with both verdicts. `applique_labo` is true when Opus confirms the
            verdict AND the direction (same superseded page, same authoritative
            page) with a confidence >= --seuil (0.8). It is a PROPOSAL for the
            brain owner (TRUTH_REVIEWER, when set, is named in its status).
  --links   adds, in gbrain_lab_truth only, a `supersedes` link
            (authoritative page -> superseded page, link_source "truth-lab")
            per applique_labo pair: that is what the native demotion reads.
  --remove  removes these links.
  --frontmatter / --unfrontmatter  (native lab truth2 only) sets or removes,
            the normal way (`gbrain get` then `gbrain put` at the revision
            read), the key `superseded_by: <authoritative page>` on every
            applique_labo superseded page. This is the proposed convention,
            tested as is: the search step reads it, and an agent sees it in get_page.
The last two also clear the lab's query cache (query_cache), otherwise the
lab MCP server would serve results from before the change.

Labs: `truth` is a pg_restore copy tied to the live brain's Git repository
(no page is written there, only links and the cache); `truth2` is a fresh
native brain (database only, no repository), where pages can be written.
--lab picks one (default truth2). Lab database URLs are
$GBRAIN_LAB_DB_PREFIX/gbrain_lab_<lab>, the prefix defaulting to
postgres://<current user>@localhost:5432.
"""
import argparse
import getpass
import json
import os
import re
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
# TRUTH_WORK / TRUTH_OUT: the weekly run (ops/truth/weekly-detect.sh) works outside the repository.
WORK = os.environ.get('TRUTH_WORK') or os.path.join(HERE, 'work')
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
LAB = 'truth2'


def lab_db(name):
    """Lab database URL: $GBRAIN_LAB_DB_PREFIX (default postgres://<user>@localhost:5432) + /gbrain_lab_<name>."""
    prefix = os.environ.get('GBRAIN_LAB_DB_PREFIX') or f'postgres://{getpass.getuser()}@localhost:5432'
    return f'{prefix}/gbrain_lab_{name}'


DB = lab_db(LAB)
DATED = re.compile(r'\d{2}-\d{2}-\d{4}|\d{4}-\d{2}-\d{2}')


def psql(sql):
    return subprocess.run(['psql', '-X', '-At', DB, '-v', 'ON_ERROR_STOP=1', '-c', sql], capture_output=True, text=True, check=True).stdout


def build(seuil):
    judged = json.load(open(os.path.join(WORK, 'judged.json'), encoding='utf-8'))
    conf_path = os.path.join(WORK, 'confirmed.json')
    confirmed = json.load(open(conf_path, encoding='utf-8')) if os.path.exists(conf_path) else {}
    pairs = []
    for k, s in judged.items():
        if s.get('verdict') not in ('remplacee', 'doublon'):
            continue
        o = confirmed.get(k, {})
        agree = o.get('verdict') == s['verdict'] and o.get('remplacee') == s.get('remplacee') and o.get('fait_foi') == s.get('fait_foi')
        # Two independent judges agree: the mean of their confidences (the minimum penalised twice).
        conf = (float(s.get('confiance') or 0) + float(o.get('confiance') or 0)) / 2 if agree else 0.0
        pairs.append({
            'paire': [s['a'], s['b']],
            'verdict': s['verdict'],
            'remplacee': s.get('remplacee'),
            'fait_foi': s.get('fait_foi'),
            'contradiction': o.get('contradiction') if agree else s.get('contradiction'),
            'justification': o.get('justification') if agree else s.get('justification'),
            'confiance': round(conf, 2),
            'sonnet': {x: s.get(x) for x in ('verdict', 'remplacee', 'fait_foi', 'confiance', 'justification')},
            'opus': {x: o.get(x) for x in ('verdict', 'remplacee', 'fait_foi', 'confiance', 'justification')} if o else None,
            'confirmee': agree,
            'applique_labo': bool(agree and conf >= seuil and s.get('remplacee') and s.get('fait_foi')),
            'similarite': s.get('sim'),
            'trouvee_par': s.get('why'),
        })
    # A dated page ("state as of 30/07", minutes of 2026-09-14) is history by construction:
    # it stays a proposal, never demoted automatically (temporal questions need it).
    for p in pairs:
        if p['applique_labo'] and DATED.search(p['remplacee'] or ''):
            p['applique_labo'] = False
            p['non_applique'] = 'page datée : historique par construction'
    # A single `superseded_by` per page: the most confident proposal wins.
    best = {}
    for p in pairs:
        if p['applique_labo'] and (p['remplacee'] not in best or p['confiance'] > best[p['remplacee']]['confiance']):
            best[p['remplacee']] = p
    for p in pairs:
        if p['applique_labo'] and best[p['remplacee']] is not p:
            p['applique_labo'] = False
            p['non_applique'] = f"une proposition plus sûre existe pour cette page ({best[p['remplacee']]['fait_foi']})"
    # Chains (A superseded by B, B superseded by C): the last page is authoritative.
    nxt = {p['remplacee']: p['fait_foi'] for p in pairs if p['applique_labo']}
    for p in pairs:
        if p['applique_labo']:
            end, seen = p['fait_foi'], {p['remplacee']}
            while end in nxt and end not in seen:
                seen.add(end)
                end = nxt[end]
            p['fait_foi_final'] = end
    pairs.sort(key=lambda p: (not p['applique_labo'], not p['confirmee'], -p['confiance']))
    verdicts = {}
    for s in judged.values():
        verdicts[s.get('verdict')] = verdicts.get(s.get('verdict'), 0) + 1
    reviewer = os.environ.get('TRUTH_REVIEWER')
    out = {
        'version': 1,
        'date': os.environ.get('TRUTH_DATE') or '2026-10-21',
        'statut': f"PROPOSITION : à valider{' par ' + reviewer if reviewer else ''} avant toute application au brain en service. Rien n'a été écrit dans le brain en service.",
        'base': os.environ.get('TRUTH_BASE_LABEL') or 'gbrain_lab_truth (copie du 21/10 18:09)',
        'methode': ("Paires candidates (candidates.py : 6 plus proches voisins en embedding moyen, alias communs, "
                    "pages qui se déclarent périmées, fraîcheur), tri large par Claude Sonnet, jugement de chaque paire "
                    "par Sonnet sur les deux pages, confirmation à l'aveugle par Opus. applique_labo : verdict et sens "
                    f"confirmés par Opus, moyenne des deux confiances >= {seuil}, page remplacée non datée, une seule proposition par page."),
        'convention': {
            'page_remplacee': 'frontmatter `superseded_by: <slug de la page qui fait foi>`',
            'page_de_reference': 'frontmatter `canonical: true` (facultatif, une par entité)',
        },
        'compte': {'paires_candidates': len(json.load(open(os.path.join(WORK, 'candidates.json'), encoding='utf-8'))['pairs']),
                   'paires_jugees': len(judged), 'verdicts_sonnet': verdicts,
                   'propositions': len(pairs), 'confirmees': sum(p['confirmee'] for p in pairs),
                   'appliquees_labo': sum(p['applique_labo'] for p in pairs)},
        'paires': pairs,
    }
    path = os.environ.get('TRUTH_OUT') or os.path.join(HERE, 'supersessions.json')
    json.dump(out, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(json.dumps(out['compte'], ensure_ascii=False))
    print(path)


def lab(*args, stdin=None):
    env = dict(os.environ, GBRAIN_SRC=REPO)
    p = subprocess.run([os.path.expanduser('~/gbrain-labs/bin/gbrain-lab'), LAB, *args], input=stdin,
                       capture_output=True, text=True, env=env)
    if p.returncode != 0:
        raise SystemExit(f"gbrain-lab {LAB} {args[0]} : {p.stderr[-400:]}")
    return p.stdout


def set_frontmatter(slug, value):
    """Sets (value = slug) or removes (value = None) `superseded_by` by a get then a put at the revision read."""
    md = lab('get', slug)
    if not md.startswith('---\n'):
        raise SystemExit(f'{slug} : pas de frontmatter')
    end = md.index('\n---', 4)
    head = [l for l in md[4:end].split('\n') if not l.startswith('superseded_by:')]
    if value:
        head.append(f'superseded_by: {value}')
    new = '---\n' + '\n'.join(head) + md[end:]
    if new == md:
        return False
    rev = psql(f"select knowledge_revision from pages where slug = '{slug}' and source_id = 'default' and deleted_at is null").strip()
    lab('put', slug, '--expected-revision', rev, '--content-origin', 'inferred', stdin=new)
    return True


def frontmatter(apply):
    data = json.load(open(os.path.join(HERE, 'supersessions.json'), encoding='utf-8'))
    n = 0
    for p in data['paires']:
        if p['applique_labo']:
            n += set_frontmatter(p['remplacee'], p['fait_foi'] if apply else None)
    rows = psql("select slug || ' -> ' || (frontmatter->>'superseded_by') from pages where frontmatter ? 'superseded_by' and deleted_at is null")
    print(f"{n} pages modifiées ; superseded_by présent sur :\n{rows}")
    clear_cache()


def clear_cache():
    psql('delete from query_cache')


def links():
    data = json.load(open(os.path.join(HERE, 'supersessions.json'), encoding='utf-8'))
    rows = [(p['fait_foi'], p['remplacee']) for p in data['paires'] if p['applique_labo']]
    values = ','.join("('%s','%s')" % (a.replace("'", "''"), b.replace("'", "''")) for a, b in rows)
    if values:
        print(psql(f"""insert into links (from_page_id, to_page_id, link_type, context, link_source)
          select pf.id, pt.id, 'supersedes', 'truth-lab : proposition supersessions.json', 'truth-lab'
          from (values {values}) v(f, t)
          join pages pf on pf.slug = v.f and pf.source_id = 'default' and pf.deleted_at is null
          join pages pt on pt.slug = v.t and pt.source_id = 'default' and pt.deleted_at is null
          on conflict do nothing returning id""").count('\n'), 'liens posés')
    clear_cache()


def remove():
    print(psql("delete from links where link_type = 'supersedes' and link_source = 'truth-lab' returning id").count('\n'), 'liens retirés')
    clear_cache()


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--build', action='store_true')
    g.add_argument('--links', action='store_true')
    g.add_argument('--remove', action='store_true')
    g.add_argument('--clear-cache', action='store_true')
    g.add_argument('--frontmatter', action='store_true')
    g.add_argument('--unfrontmatter', action='store_true')
    ap.add_argument('--lab', default='truth2', choices=['truth', 'truth2'])
    ap.add_argument('--seuil', type=float, default=0.8)
    args = ap.parse_args()
    global LAB, DB
    LAB = args.lab
    DB = lab_db(LAB)
    if (args.frontmatter or args.unfrontmatter) and LAB != 'truth2':
        raise SystemExit('refus : on n\'écrit de pages que dans le labo natif truth2')
    if args.build:
        build(args.seuil)
    elif args.links:
        links()
    elif args.remove:
        remove()
    elif args.frontmatter or args.unfrontmatter:
        frontmatter(args.frontmatter)
    else:
        clear_cache()


if __name__ == '__main__':
    main()
