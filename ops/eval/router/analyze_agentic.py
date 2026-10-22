#!/usr/bin/env python3
"""A/B/C table of the agentic overview bench: global judge (judge.py) and per-element judge
(judge_elements.py, two runs), mean of two rounds per variant, paired per-question differences
with a 95 % bootstrap interval.

Usage: python3 -I ops/eval/router/analyze_agentic.py A=<dir>,<dir> B=<dir>,<dir> C=<dir>,<dir> [--out data/agentic-abc.json]
"""
import argparse, glob, json, os, random

S = {'juste': 1.0, 'partiel': 0.5, 'faux': 0.0}


def load(dirs):
    per = {}
    for d in dirs:
        for f in glob.glob(os.path.join(d, 'ov-*.json')):
            r = json.load(open(f, encoding='utf-8'))
            e = [r[k]['score'] for k in ('juge_elements', 'juge_elements2') if 'score' in r.get(k, {})]
            g = S.get((r.get('juge') or {}).get('verdict'))
            x = per.setdefault(r['id'], {'kind': r['source'], 'el': [], 'gl': [], 'themes': 0, 'searches': 0, 'dur': []})
            x['el'] += e
            if g is not None:
                x['gl'].append(g)
            x['themes'] += any(s.startswith('themes/') for s in r['read'] + r['cited'])
            x['searches'] += len(r['searches'])
            x['dur'].append(r['duration_s'])
    return per


def mean(xs):
    return sum(xs) / len(xs) if xs else float('nan')


def boot(diffs, n=5000, seed=21):
    rnd = random.Random(seed)
    ms = sorted(mean([rnd.choice(diffs) for _ in diffs]) for _ in range(n))
    return ms[int(0.025 * n)], ms[int(0.975 * n)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('variants', nargs='+')
    ap.add_argument('--out', default='')
    a = ap.parse_args()
    V = {v.split('=', 1)[0]: load(v.split('=', 1)[1].split(',')) for v in a.variants}
    ids = sorted(set.intersection(*[set(p) for p in V.values()]))
    kinds = ['periode', 'personne', 'theme']
    print('| Variante | Juge par élément | dont période | personne | thème | Juge global | Réponses s\'appuyant sur un résumé | Recherches | Durée (s) |')
    print('|---|---|---|---|---|---|---|---|---|')
    for k, p in V.items():
        el = {i: mean(p[i]['el']) for i in ids}
        row = [f"{mean(list(el.values())):.2f}"] + [f"{mean([el[i] for i in ids if p[i]['kind'] == kd]):.2f}" for kd in kinds]
        nrun = len(p[ids[0]]['dur'])
        print(f"| {k} | " + ' | '.join(row) + f" | {mean([mean(p[i]['gl']) for i in ids]):.2f} | {sum(p[i]['themes'] for i in ids)}/{len(ids) * nrun} | "
              f"{sum(p[i]['searches'] for i in ids) / (len(ids) * nrun):.1f} | {mean([mean(p[i]['dur']) for i in ids]):.0f} |")
    base = V.get('A')
    out = {}
    if base:
        print('\n| Écart apparié (juge par élément) | Moyenne | IC 95 % | Questions en hausse / en baisse |')
        print('|---|---|---|---|')
        for k, p in V.items():
            if k == 'A':
                continue
            d = [mean(p[i]['el']) - mean(base[i]['el']) for i in ids]
            lo, hi = boot(d)
            print(f"| {k} − A | {mean(d):+.3f} | [{lo:+.3f} ; {hi:+.3f}] | {sum(x > 0.05 for x in d)} / {sum(x < -0.05 for x in d)} |")
            out[k] = {'diff': mean(d), 'ci': [lo, hi], 'per_question': dict(zip(ids, d))}
    if a.out:
        json.dump({'variants': {k: {i: {kk: vv for kk, vv in p[i].items()} for i in ids} for k, p in V.items()}, 'diffs': out},
                  open(a.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
