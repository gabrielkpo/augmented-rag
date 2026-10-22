#!/usr/bin/env python3
"""Candidate page pairs for duplicate or supersession (truth maintenance, §5.1).

Reads the LAB database (never the live one, unless --live-readonly) and writes
a JSON of pairs, each with what surfaced it. No decision here: Claude judges
each pair afterwards (judge_pairs.py). "gbrain indexes, Claude reasons".

Four candidate sources, over the knowledge pages (system, project, process,
person, partner, concept, original, note):
  voisins    (neighbours): the 6 nearest neighbours of each page by mean
               embedding (summary chunks, no timeline), similarity >= 0.78;
               person/person pairs are dropped, as a shared writing style
               brings them close without them being about the same entity.
  alias      : an alias of one page is the name of another (slug tail, title
               or alias), e.g. projects/ticket-automation has the alias
               "ticket-agent".
  bandeau    (banner): a page declares itself stale ("périmé", "obsolète",
               "n'est plus à jour"...); it is paired with its 3 nearest neighbours.
  fraîcheur  (freshness): the ops/freshness indicator (summary older than its
               timeline), added as information on each page, not as a source
               of pairs.

--known-pair A,B (or TRUTH_KNOWN_PAIR) reports whether a pair you already know
about was found, as a recall check; nothing is reported when it is unset.
The default --db is the `truth` lab database, $GBRAIN_LAB_DB_PREFIX/gbrain_lab_truth
(prefix default postgres://<current user>@localhost:5432), unless TRUTH_DB is set.

Usage: python3 -I ops/eval/truth/candidates.py [--db URL] [--out FILE] [--known-pair A,B]
"""
import argparse
import getpass
import json
import os
import re
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
DEFAULT_DB = (os.environ.get('GBRAIN_LAB_DB_PREFIX') or f'postgres://{getpass.getuser()}@localhost:5432') + '/gbrain_lab_truth'
KNOWLEDGE = "('system','project','process','person','partner','concept','original','note')"
STALE_BANNER = r"(périmée?s?\b|obsolète|n.est plus (à jour|la référence|valable|d.actualité)|remplacée? par|superseded|deprecated|ne plus (lire|utiliser) cette page)"


def psql_json(db, sql):
    """A query that returns a single JSON column per row."""
    out = subprocess.run(['psql', '-X', '-At', db, '-c', sql], capture_output=True, text=True, check=True).stdout
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=os.environ.get('TRUTH_DB', DEFAULT_DB))
    ap.add_argument('--out', default=os.path.join(HERE, 'work', 'candidates.json'))
    ap.add_argument('--k', type=int, default=6)
    ap.add_argument('--min-sim', type=float, default=0.78)
    ap.add_argument('--live-readonly', action='store_true',
                    help='allow the live database (read-only: this script only runs SELECTs)')
    ap.add_argument('--since-days', type=int, default=0,
                    help='keep only the pairs where one page changed in the last N days (0 = all)')
    ap.add_argument('--known-pair', default=os.environ.get('TRUTH_KNOWN_PAIR', ''),
                    help='"slug-a,slug-b": report whether this known pair is among the candidates')
    args = ap.parse_args()
    if 'gbrain_lab_' not in args.db and not args.live_readonly:
        raise SystemExit('refus : base hors labo sans --live-readonly')
    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    pages = {p['slug']: p for p in psql_json(args.db, f"""
      select json_build_object('id', id, 'slug', slug, 'type', type, 'title', title,
        'created', coalesce(frontmatter->>'created', to_char(created_at, 'YYYY-MM-DD')),
        'updated', frontmatter->>'updated', 'status', frontmatter->>'status',
        'aliases', coalesce(frontmatter->'aliases', '[]'::jsonb),
        'chars', length(compiled_truth),
        'banner', compiled_truth ~* '{STALE_BANNER}',
        'recent', {'updated_at > now() - interval ' + repr(str(args.since_days) + ' days') if args.since_days > 0 else 'true'})
      from pages where deleted_at is null and type in {KNOWLEDGE}""")}

    fresh_sql = open(os.path.join(REPO, 'ops', 'freshness', 'freshness.sql'), encoding='utf-8').read()
    fresh_sql = re.sub(r'--[^\n]*', '', fresh_sql).strip().rstrip(';')
    for row in psql_json(args.db, fresh_sql):
        if row['slug'] in pages:
            pages[row['slug']]['freshness'] = {k: row[k] for k in ('synthese_reecrite_le', 'evenements_apres', 'dernier_evenement', 'retard_jours')}

    neighbours = psql_json(args.db, f"""
      with pv as (
        select p.id, p.slug, p.type, avg(c.embedding) as e
        from pages p join content_chunks c on c.page_id = p.id
        where p.deleted_at is null and c.embedding is not null and c.chunk_source <> 'timeline'
          and p.type in {KNOWLEDGE}
        group by p.id, p.slug, p.type),
      nb as (
        select a.slug a, b.slug b, a.type at, b.type bt, 1 - (a.e <=> b.e) sim,
               row_number() over (partition by a.id order by a.e <=> b.e) rk
        from pv a join pv b on a.id <> b.id)
      select json_build_object('a', a, 'b', b, 'at', at, 'bt', bt, 'sim', round(sim::numeric, 3), 'rk', rk)
      from nb where rk <= greatest({args.k}, 3)""")

    pairs = {}

    def add(a, b, why, sim=None):
        if a == b or a not in pages or b not in pages:
            return
        key = tuple(sorted((a, b)))
        p = pairs.setdefault(key, {'a': key[0], 'b': key[1], 'why': [], 'sim': None})
        if why not in p['why']:
            p['why'].append(why)
        if sim is not None:
            p['sim'] = max(p['sim'] or 0, sim)

    sims = {}
    for n in neighbours:
        sims[tuple(sorted((n['a'], n['b'])))] = n['sim']
        if n['rk'] <= args.k and n['sim'] >= args.min_sim and not (n['at'] == 'person' and n['bt'] == 'person'):
            add(n['a'], n['b'], 'voisins', n['sim'])
        if n['rk'] <= 3 and pages[n['a']]['banner']:
            add(n['a'], n['b'], 'bandeau', n['sim'])

    def names(p):
        tail = p['slug'].split('/', 1)[-1]
        out = {tail.lower(), (p['title'] or '').split(' — ')[0].strip().lower()}
        out |= {str(a).strip().lower() for a in p['aliases'] if isinstance(a, str)}
        return {n for n in out if len(n) >= 4}

    by_name = {}
    for slug, p in pages.items():
        for n in names(p):
            by_name.setdefault(n, set()).add(slug)
    for n, slugs in by_name.items():
        slugs = sorted(slugs)
        if 1 < len(slugs) <= 4:
            for i, a in enumerate(slugs):
                for b in slugs[i + 1:]:
                    if not (pages[a]['type'] == 'person' and pages[b]['type'] == 'person' and n.count(' ') == 0):
                        add(a, b, f'alias:{n}', sims.get((a, b)))

    if args.since_days > 0:
        pairs = {k: p for k, p in pairs.items() if pages[p['a']]['recent'] or pages[p['b']]['recent']}
    out = {'db': args.db.rsplit('/', 1)[-1], 'pages': len(pages),
           'pairs': sorted(pairs.values(), key=lambda p: -(p['sim'] or 0)),
           'page_info': {s: {k: v for k, v in p.items() if k != 'id'} for s, p in pages.items()}}
    json.dump(out, open(args.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    whys = {}
    for p in pairs.values():
        for w in p['why']:
            w = w.split(':')[0]
            whys[w] = whys.get(w, 0) + 1
    print(f"{len(pages)} pages de connaissance, {len(pairs)} paires candidates {whys} -> {args.out}")
    if args.known_pair:
        known = tuple(s.strip() for s in args.known_pair.split(','))
        print('paire connue présente :', tuple(sorted(known)) in pairs)


if __name__ == '__main__':
    main()
