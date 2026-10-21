#!/usr/bin/env python3
"""Paired MRR and Recall@5 difference between a state and reference A, with a 95 % interval (bootstrap).

Usage: python3 -I bootstrap.py <mode: nojev|jev> <state> [<state> ...]
With Jev, each question takes the mean of its two runs (jev1, jev2).
Benches: chunk (40), regular = v2 + holdout A (148).
"""
import os
import random
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NUM = re.compile(r'(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s*$')


def rows(state, bank, run):
    t = open(os.path.join(HERE, 'results', state, f'{bank}-{run}.txt'), encoding='utf-8').read()
    seg = t[t.index('\nQuery'):t.index('\nMean')]
    return [tuple(float(x) for x in NUM.search(l).groups()) for l in seg.splitlines() if NUM.search(l)]


def per_question(state, bank, mode):
    runs = ['nojev1'] if mode == 'nojev' else ['jev1', 'jev2']
    rs = [rows(state, bank, r) for r in runs]
    return [(sum(r[i][1] for r in rs) / len(rs), sum(r[i][2] for r in rs) / len(rs)) for i in range(len(rs[0]))]


def ci(diffs, n=10000, seed=7):
    rnd = random.Random(seed)
    m = len(diffs)
    boots = sorted(sum(diffs[rnd.randrange(m)] for _ in range(m)) / m for _ in range(n))
    return sum(diffs) / m, boots[int(0.025 * n)], boots[int(0.975 * n)]


def fmt(x):
    return f'{x:+.3f}'.replace('.', ',')


def main():
    mode, states = sys.argv[1], sys.argv[2:]
    groups = {'chunk': ['chunk'], 'réguliers (v2 + holdout A)': ['v2', 'holdout-a']}
    print('| État | Banc | Δ Recall@5 [IC 95 %] | Δ MRR [IC 95 %] |')
    print('|---|---|---|---|')
    for s in states:
        for g, banks in groups.items():
            ref = sum((per_question('A-titre', b, mode) for b in banks), [])
            cur = sum((per_question(s, b, mode) for b in banks), [])
            out = []
            for k in (0, 1):
                m, lo, hi = ci([c[k] - r[k] for c, r in zip(cur, ref)])
                out.append(f'{fmt(m)} [{fmt(lo)} ; {fmt(hi)}]')
            print(f'| {s} | {g} | {out[0]} | {out[1]} |')


if __name__ == '__main__':
    main()
