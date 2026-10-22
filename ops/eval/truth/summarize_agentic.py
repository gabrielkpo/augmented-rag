#!/usr/bin/env python3
"""Aggregates the runs of the truth track's agentic bench (ops/eval/truth/results/agentic-*).

For each variant (folder agentic-<lab>-<variant>[-<tag>]-<run>): judged score per
run, mean, and per question type (temporal, bridge, comparison); then, when a
stale page is given (--stale-page or TRUTH_STALE_PAGE, no default), every answer
to mh-06 with its verdict and whether that stale page was read or cited.

Usage: python3 -I ops/eval/truth/summarize_agentic.py [--labo truth2] [--dossier results/juge-sonnet]
                                                     [--stale-page SLUG]
"""
import argparse
import collections
import glob
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SCORE = {'juste': 1.0, 'partiel': 0.5, 'faux': 0.0}
# The superseded pages applied to the lab (supersessions.json, applique_labo).
REPLACED = {p['remplacee'] for p in json.load(open(os.path.join(HERE, 'supersessions.json'), encoding='utf-8'))['paires'] if p['applique_labo']}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--labo', default='truth2')
    ap.add_argument('--dossier', default='results', help='results, or results/juge-sonnet for the second judge\'s scores')
    ap.add_argument('--stale-page', default=os.environ.get('TRUTH_STALE_PAGE', ''),
                    help='slug of the stale page whose reads and citations are reported for mh-06')
    args = ap.parse_args()
    stale = args.stale_page
    groups = collections.defaultdict(dict)
    for d in sorted(glob.glob(os.path.join(HERE, args.dossier, f'agentic-{args.labo}-*'))):
        m = re.match(rf'agentic-{args.labo}-(.+)-(\d+)$', os.path.basename(d))
        if not m or not os.path.isdir(d):
            continue
        recs = [json.load(open(f, encoding='utf-8')) for f in glob.glob(os.path.join(d, 'mh-*.json'))]
        groups[m.group(1)][int(m.group(2))] = recs

    print('| Variante | Passages | Score par passage | Moyenne | Temporelles | À relais | mh-06 | Réponses citant une page remplacée | Erreurs de juge |')
    print('|---|---|---|---|---|---|---|---|---|')
    for variant, passes in groups.items():
        per_pass, by_src, errors, stale_cites, n = [], collections.defaultdict(list), 0, 0, 0
        for _, recs in sorted(passes.items()):
            vs = [SCORE.get((r.get('juge') or {}).get('verdict')) for r in recs]
            errors += sum(v is None for v in vs)
            ok = [v for v in vs if v is not None]
            per_pass.append(sum(ok) / len(ok) if ok else 0)
            n += len(recs)
            stale_cites += sum(bool(REPLACED & set(r['cited'])) for r in recs)
            for r, v in zip(recs, vs):
                if v is not None:
                    by_src[r['source'] if r['id'] != 'mh-06' else 'mh-06'].append(v)
        mean = lambda xs: f'{sum(xs) / len(xs):.2f}' if xs else '—'
        print(f"| {variant} | {len(per_pass)} | {' / '.join(f'{x:.2f}' for x in per_pass)} | {mean(per_pass)} | "
              f"{mean(by_src['temporal'])} | {mean(by_src['bridge'])} | {mean(by_src['mh-06'])} | {stale_cites}/{n} | {errors} |")

    if not stale:
        return
    print('\nmh-06 : verdict, page périmée lue / citée, raison du juge')
    for variant, passes in groups.items():
        for i, recs in sorted(passes.items()):
            for r in recs:
                if r['id'] == 'mh-06':
                    j = r.get('juge') or {}
                    print(f"- {variant} #{i} : {j.get('verdict')} · lue {stale in r['read']} · citée {stale in r['cited']} · {j.get('raison')}")


if __name__ == '__main__':
    main()
