#!/usr/bin/env python3
"""Merges the per-page extractions into one fact list and links supersessions ACROSS pages.

Per entity whose facts come from two pages or more, one `claude -p` call (Sonnet, no tools)
receives the facts (id, page, slot, claim, valid_from) and returns:
  - duplicates: the same fact extracted from two pages (the copy dated by `described`, else the
    earliest, is kept);
  - supersessions: pairs (new, old) where the newer fact replaces the older on the same attribute.
In-page supersessions (`supersedes` in the extraction) are kept as they are.

Usage: python3 -I ops/eval/dated/link.py [--max-calls 30] [--jobs 2]
Output: ops/eval/dated/results/work/facts.json
"""
import argparse
import collections
import concurrent.futures
import glob
import json
import os
import re
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, 'results', 'work')
PROMPT = """Voici des faits datés sur l'entité `{entity}`, extraits de plusieurs pages d'un brain. Chaque ligne : id | page | attribut | date de prise d'effet | fait.

{rows}

1. Doublons : deux ids qui disent le même fait (même attribut, même état, même moment ou presque). Donne chaque groupe une fois.
2. Remplacements : paires où le fait le plus récent remplace le plus ancien sur le même attribut (nouvel état du même attribut : nouveau statut, nouveau modèle, nouveau chiffre, décision qui en annule une autre). Pas de paire entre attributs différents, pas de paire entre deux doublons, et seulement le remplacement direct (A remplacé par B, B remplacé par C : deux paires, pas A par C).
Réponds uniquement en JSON : {{"duplicates": [["id", "id"]], "supersessions": [{{"new": "id", "old": "id"}}]}}"""


def load_extractions(apply_reentity=True):
    remap_path = os.path.join(WORK, 'reentity.json')
    remap = json.load(open(remap_path, encoding='utf-8')) if apply_reentity and os.path.exists(remap_path) else {}
    facts = []
    for path in sorted(glob.glob(os.path.join(WORK, 'extract', '*.json'))):
        data = json.load(open(path, encoding='utf-8'))
        page = data['page']
        local = {}
        for f in data.get('facts') or []:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(f.get('valid_from') or '')) or not f.get('entity') or not f.get('claim'):
                continue
            gid = f"{page}#{f['id']}"
            local[f['id']] = gid
            fact = {**f, 'id': gid, 'page': page, 'page_date': data.get('page_date')}
            if remap.get(gid):  # subject entity re-assigned by reentity.py
                fact['extracted_entity'], fact['entity'] = f['entity'], remap[gid]
            facts.append(fact)
        for f in facts:
            if f['page'] == page and f.get('supersedes'):
                f['supersedes'] = local.get(f['supersedes'])
    return facts


def link_entity(entity, fs, model):
    rows = '\n'.join(f"{f['id']} | {f['page']} | {f['slot']} | {f['valid_from']} | {f['claim']}" for f in sorted(fs, key=lambda f: f['valid_from']))
    proc = subprocess.run(['claude', '-p', PROMPT.format(entity=entity, rows=rows), '--model', model,
                           '--settings', json.dumps({'disableAllHooks': True}), '--tools', '', '--no-session-persistence',
                           '--output-format', 'json'], capture_output=True, text=True, timeout=600, cwd=WORK)
    try:
        res = json.loads(proc.stdout)
        out = json.loads(re.search(r'\{.*\}', res.get('result') or '', re.S).group(0))
        out['cost_usd'] = res.get('total_cost_usd')
        return entity, out
    except (ValueError, AttributeError):
        return entity, {'error': (proc.stdout or proc.stderr)[-300:]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default='claude-sonnet-5-5')
    ap.add_argument('--max-calls', type=int, default=30)
    ap.add_argument('--jobs', type=int, default=2)
    args = ap.parse_args()
    facts = load_extractions()
    by_entity = collections.defaultdict(list)
    for f in facts:
        by_entity[f['entity']].append(f)
    multi = sorted((e for e, fs in by_entity.items() if len({f['page'] for f in fs}) >= 2), key=lambda e: -len(by_entity[e]))
    cache_path = os.path.join(WORK, 'link-cache.json')
    cache = json.load(open(cache_path, encoding='utf-8')) if os.path.exists(cache_path) else {}
    todo = [e for e in multi if e not in cache or 'error' in cache[e]][:args.max_calls]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for entity, out in ex.map(lambda e: link_entity(e, by_entity[e], args.model), todo):
            cache[entity] = out
            print(f"{entity:60} {len(out.get('duplicates') or [])} doublons, {len(out.get('supersessions') or [])} remplacements"
                  if 'error' not in out else f'{entity:60} échec', flush=True)
    json.dump(cache, open(cache_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)

    ids = {f['id']: f for f in facts}
    dropped = set()
    for out in cache.values():
        for group in out.get('duplicates') or []:
            group = [g for g in group if g in ids]
            if len(group) < 2:
                continue
            keep = min(group, key=lambda g: (ids[g].get('date_basis') != 'described', ids[g]['valid_from']))
            dropped.update(g for g in group if g != keep)
            for f in facts:  # a supersession that pointed at a dropped copy points at the kept one
                if f.get('supersedes') in group:
                    f['supersedes'] = keep
        for pair in out.get('supersessions') or []:
            new, old = pair.get('new'), pair.get('old')
            if new in ids and old in ids and ids[new]['valid_from'] >= ids[old]['valid_from'] and not ids[new].get('supersedes'):
                ids[new]['supersedes'] = old
                ids[new]['cross_page'] = ids[new]['page'] != ids[old]['page']
    kept = [f for f in facts if f['id'] not in dropped]
    for f in kept:
        if f.get('supersedes') in dropped:
            f['supersedes'] = None
    json.dump({'facts': kept, 'dropped_duplicates': sorted(dropped)}, open(os.path.join(WORK, 'facts.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)
    print(f'{len(facts)} faits extraits, {len(dropped)} doublons retirés, {len(kept)} gardés, '
          f"{sum(1 for f in kept if f.get('supersedes'))} remplacements ({sum(1 for f in kept if f.get('cross_page'))} entre pages)")


if __name__ == '__main__':
    main()
