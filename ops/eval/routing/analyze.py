#!/usr/bin/env python3
"""Compares strategies on a judged agentic bench: base, each strategy where it was
measured, and the router (each question answered by the strategy it picked).

Each question's score is first averaged over its passes; confidence intervals are
paired bootstraps over questions. Inputs are agentic result folders already judged
by judge.py (field "juge" in every JSON) and the router's output.

Usage: python3 -I ops/eval/routing/analyze.py --qrels ops/eval/qrels-multihop-v3.json \
    --router ops/eval/routing/results/router-v2.json DIR [DIR ...]
"""
import argparse
import collections
import glob
import json
import os
import random

SCORE = {'juste': 1.0, 'partiel': 0.5, 'faux': 0.0}
TYPES = ('temporal', 'bridge', 'compare', 'simple', 'overview')


def load(dirs):
    runs = collections.defaultdict(list)  # (qid, variant) -> [record, ...], one per pass
    for d in dirs:
        for f in sorted(glob.glob(os.path.join(d, '*.json'))):
            r = json.load(open(f, encoding='utf-8'))
            if 'juge' not in r or 'variant' not in r:
                continue
            r['score'] = SCORE.get(r['juge'].get('verdict'), 0.0)
            runs[(r['id'], r['variant'])].append(r)
    return runs


def mean(xs):
    return sum(xs) / len(xs) if xs else float('nan')


def boot(diffs, n=10000, seed=7):
    rng = random.Random(seed)
    k = len(diffs)
    ms = sorted(mean([diffs[rng.randrange(k)] for _ in range(k)]) for _ in range(n))
    return ms[int(0.025 * n)], ms[int(0.975 * n)]


def qscore(runs, qid, variant):
    rs = runs.get((qid, variant))
    return mean([r['score'] for r in rs]) if rs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dirs', nargs='+')
    ap.add_argument('--qrels', required=True)
    ap.add_argument('--router', required=True)
    args = ap.parse_args()
    qs = {q['id']: q for q in json.load(open(args.qrels, encoding='utf-8'))['queries']}
    route = {r['id']: r['strategy'] for r in json.load(open(args.router, encoding='utf-8'))}
    runs = load(args.dirs)
    variants = sorted({v for _, v in runs})
    out = []

    def table(title, ids_by_group, cols):
        out.append(f'### {title}\n')
        out.append('| Groupe | n | ' + ' | '.join(cols) + ' |')
        out.append('|---|---|' + '---|' * len(cols))
        for g, ids in ids_by_group:
            cells = []
            for c in cols:
                vals = [qscore(runs, i, route[i] if c == 'routeur' else c) for i in ids]
                vals = [v for v in vals if v is not None]
                cells.append(f'{mean(vals):.2f} ({len(vals)})' if vals else '—')
            out.append(f'| {g} | {len(ids)} | ' + ' | '.join(cells) + ' |')
        out.append('')

    groups = [(t, [i for i in qs if qs[i]['source'] == t]) for t in TYPES]
    groups += [('dures (single_search_top5 faux)', [i for i in qs if not qs[i].get('single_search_top5')]),
               ('tout', list(qs))]
    table('Score jugé moyen par question (n mesurées entre parenthèses)', groups, ['base', 'routeur'] + [v for v in variants if v != 'base'])

    out.append('### Différences appariées (moyenne des passages par question, IC bootstrap 95 %)\n')
    out.append('| Comparaison | Groupe | n | Écart | IC 95 % |')
    out.append('|---|---|---|---|---|')
    for other in ['routeur'] + [v for v in variants if v != 'base']:
        for g, ids in groups:
            diffs = []
            for i in ids:
                if other == 'routeur' and route[i] == 'base':
                    diffs.append(0.0)  # left to base: the router is base by construction
                    continue
                a = qscore(runs, i, route[i] if other == 'routeur' else other)
                b = qscore(runs, i, 'base')
                if a is not None and b is not None:
                    diffs.append(a - b)
            if len(diffs) >= 3:
                lo, hi = boot(diffs)
                out.append(f'| {other} − base | {g} | {len(diffs)} | {mean(diffs):+.3f} | [{lo:+.3f} ; {hi:+.3f}] |')
    out.append('')

    # Oracle on the questions where all three strategies were measured.
    full = [i for i in qs if all(qscore(runs, i, v) is not None for v in ('base', 'iterative2', 'temporal'))]
    if full:
        orc = mean([max(qscore(runs, i, v) for v in ('base', 'iterative2', 'temporal')) for i in full])
        out.append(f'Oracle (meilleure stratégie par question) sur les {len(full)} questions où les trois sont mesurées : '
                   f'{orc:.2f} ; base {mean([qscore(runs, i, "base") for i in full]):.2f} ; routeur {mean([qscore(runs, i, route[i]) for i in full]):.2f}.\n')

    # Pass-to-pass agreement, per variant.
    for v in variants:
        pairs = [runs[(i, v)] for i in qs if len(runs.get((i, v), [])) >= 2]
        if pairs:
            same = sum(r[0]['juge']['verdict'] == r[1]['juge']['verdict'] for r in pairs)
            out.append(f'Accord passage à passage de {v} : {same}/{len(pairs)} questions avec le même verdict aux deux passages.\n')

    out.append('### Coût par réponse\n')
    out.append('| Variante | Réponses | Recherches / réponse | Appels d\'outils / réponse | Caractères rendus par les outils / réponse | Tokens d\'entrée / réponse | Durée (s) / réponse | Coût ($) |')
    out.append('|---|---|---|---|---|---|---|---|')
    for v in variants:
        rs = [r for (i, vv), lst in runs.items() if vv == v for r in lst]
        chars = [r['tool_result_chars'] for r in rs if 'tool_result_chars' in r]
        toks = [sum((r['usage'] or {}).get(k, 0) or 0 for k in ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens'))
                for r in rs if r.get('usage')]
        out.append(f"| {v} | {len(rs)} | {mean([len(r['searches']) for r in rs]):.1f} | {mean([r['tool_calls'] for r in rs]):.1f} | "
                   f"{mean(chars):.0f} | {mean(toks):.0f} | {mean([r['duration_s'] for r in rs]):.0f} | {sum((r.get('cost_usd') or 0) for r in rs):.2f} |")
    print('\n'.join(out))


if __name__ == '__main__':
    main()
