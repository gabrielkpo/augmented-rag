#!/usr/bin/env python3
"""Loads ops/eval/dated/results/work/facts.json into a LAB brain through the native `remember` verb.

Per fact, oldest valid_from first: remember {fact, entity, valid_from, provenance, visibility: world,
replaces: <fact_id of the fact it supersedes, when that one is loaded and still active>}.
Native `replaces` sets the old fact's superseded_by and expired_at = now(), and leaves valid_until
empty. With --set-valid-until, a lab-only SQL step then sets each superseded fact's valid_until
to its successor's valid_from (what the native API cannot do).

Refuses any database whose name does not start with gbrain_lab_.
Usage: python3 -I ops/eval/dated/load.py --lab dated --src <code dir> [--set-valid-until]
Output: ops/eval/dated/results/work/load.json (fact id, status or error per extracted fact).
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcp_client import Lab  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, 'results', 'work')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lab', required=True)
    ap.add_argument('--src', required=True)
    ap.add_argument('--set-valid-until', action='store_true')
    args = ap.parse_args()
    db = f'gbrain_lab_{args.lab}'
    cfg = json.load(open(os.path.expanduser(f'~/gbrain-labs/{args.lab}/.gbrain/config.json')))
    if not cfg['database_url'].split('?')[0].endswith('/' + db):
        sys.exit('refus : la config du labo ne vise pas une base de labo')

    facts = json.load(open(os.path.join(WORK, 'facts.json'), encoding='utf-8'))['facts']
    facts.sort(key=lambda f: (f['valid_from'], f['page'], f['id']))
    lab = Lab(args.lab, args.src)
    loaded, out = {}, []
    for f in facts:
        params = {
            'fact': f['claim'], 'entity': f['entity'], 'valid_from': f['valid_from'] + 'T00:00:00Z',
            'provenance': f"dated-lab: extraction Sonnet de {f['page']} (date {f.get('date_basis')}, attribut « {f['slot']} »), 2026-10-23",
            'visibility': 'world', 'kind': 'fact',
        }
        old = loaded.get(f.get('supersedes'))
        if old:
            params['replaces'] = str(old)
        body, err = lab.call('remember', params)
        if err and old:  # e.g. target already replaced: keep the fact, without the link
            rec_err = body
            params.pop('replaces')
            body, err = lab.call('remember', params)
            body = {'replaces_error': rec_err, **(body if isinstance(body, dict) else {'raw': body})}
        rec = {'gid': f['id'], 'entity': f['entity'], 'supersedes': f.get('supersedes'), 'replaces': params.get('replaces')}
        if err or not isinstance(body, dict):
            rec['error'] = body if isinstance(body, str) else json.dumps(body)[:400]
        else:
            fid = body.get('fact_id') or body.get('id') or (body.get('facts') or [{}])[0].get('fact_id')
            rec.update({'fact_id': fid, 'status': body.get('status'), 'replaces_error': body.get('replaces_error')})
            if fid:
                loaded[f['id']] = fid
        out.append(rec)
        print(f"{f['valid_from']} {f['entity'][:40]:40} {rec.get('status') or 'ERREUR'} {rec.get('fact_id') or rec.get('error', '')[:80]}", flush=True)
    lab.close()
    json.dump(out, open(os.path.join(WORK, 'load.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f"{len(out)} faits : {sum(1 for r in out if r.get('fact_id'))} écrits, {sum(1 for r in out if r.get('replaces'))} avec replaces, "
          f"{sum(1 for r in out if r.get('error'))} erreurs")

    if args.set_valid_until:
        # Lab-only: valid_until of a superseded fact = valid_from of the fact that replaced it.
        sql = ("update facts o set valid_until = n.valid_from from facts n "
               "where o.superseded_by = n.id and (o.valid_until is null or o.valid_until <> n.valid_from); "
               "select count(*) from facts where superseded_by is not null and valid_until is not null;")
        print(subprocess.run(['psql', '-X', '-At', '-d', db, '-c', sql], capture_output=True, text=True).stdout)


if __name__ == '__main__':
    main()
