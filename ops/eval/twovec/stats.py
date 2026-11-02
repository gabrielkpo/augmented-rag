#!/usr/bin/env python3
"""Two-vector lab: means, paired differences against a reference (bootstrap 95 %), questions won / lost.

Usage: python3 -I stats.py <nojev|jev> <ref> <variant> [<variant> ...]   (folders under results/)
With Jev, each question takes the mean of the runs present (jev1, jev2).
Benches: chunk (40), v2 (107), holdout-a (41), regular = v2 + holdout A (148).
"""
import os
import random
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NUM = re.compile(r'(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s*$')
GROUPS = {'ciblé': ['chunk'], 'v2': ['v2'], 'holdout A': ['holdout-a'], 'réguliers': ['v2', 'holdout-a']}


def rows(state, bank, run):
    t = open(os.path.join(HERE, 'results', state, f'{bank}-{run}.txt'), encoding='utf-8').read()
    seg = t[t.index('\nQuery'):t.index('\nMean')]
    return [tuple(float(x) for x in NUM.search(l).groups()[1:]) for l in seg.splitlines() if NUM.search(l)]


def per_question(state, bank, mode):
    runs = ['nojev1'] if mode == 'nojev' else [r for r in ('jev1', 'jev2')
                                               if os.path.exists(os.path.join(HERE, 'results', state, f'{bank}-{r}.txt'))]
    rs = [rows(state, bank, r) for r in runs]
    return [tuple(sum(r[i][k] for r in rs) / len(rs) for k in range(3)) for i in range(len(rs[0]))]


def ci(diffs, n=10000, seed=7):
    rnd = random.Random(seed)
    m = len(diffs)
    boots = sorted(sum(diffs[rnd.randrange(m)] for _ in range(m)) / m for _ in range(n))
    return sum(diffs) / m, boots[int(0.025 * n)], boots[int(0.975 * n)]


f2 = lambda x: f'{x:.2f}'.replace('.', ',')
f3 = lambda x: f'{x:+.3f}'.replace('.', ',')


def main():
    mode, ref, variants = sys.argv[1], sys.argv[2], sys.argv[3:]
    print(f'Mode {mode}, référence {ref}\n')
    print('| Variante | Banc | Recall@5 / MRR / nDCG@5 | Δ Recall@5 [IC 95 %] | Δ MRR [IC 95 %] | gagnées / perdues (MRR) |')
    print('|---|---|---|---|---|---|')
    for v in [ref] + variants:
        for g, banks in GROUPS.items():
            r = sum((per_question(ref, b, mode) for b in banks), [])
            c = sum((per_question(v, b, mode) for b in banks), [])
            means = ' / '.join(f2(sum(x[k] for x in c) / len(c)) for k in range(3))
            if v == ref:
                print(f'| {v} | {g} | {means} | — | — | — |')
                continue
            d = []
            for k in (0, 1):
                m, lo, hi = ci([a[k] - b[k] for a, b in zip(c, r)])
                d.append(f'{f3(m)} [{f3(lo)} ; {f3(hi)}]')
            win = sum(a[1] > b[1] + 0.01 for a, b in zip(c, r))
            lose = sum(a[1] < b[1] - 0.01 for a, b in zip(c, r))
            print(f'| {v} | {g} | {means} | {d[0]} | {d[1]} | +{win} / −{lose} |')


if __name__ == '__main__':
    main()
