#!/usr/bin/env python3
"""Compares the index states of the chunk track from `gbrain eval` outputs.

Usage: python3 -I compare.py <ref_folder> <variant_folder> [...]   (folders under results/)
For each bench and each mode (nojev1, jev1, jev2), gives Recall@5 / MRR / nDCG@5, the mean
latency per question, and the questions won / lost against the reference (on MRR).
The rows of the `gbrain eval` table follow the order of the qrels file.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BANKS = ['chunk', 'v2', 'holdout-a']
MODES = ['nojev1', 'jev1', 'jev2']
NUM = re.compile(r'(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s*$')


def parse(path):
    if not os.path.exists(path):
        return None
    rows, mean, elapsed, n, inside = [], None, None, None, False
    for line in open(path, encoding='utf-8'):
        if line.startswith('gbrain eval —'):
            m = re.search(r'(\d+) queries', line)
            n = int(m.group(1)) if m else None
        if line.startswith('Query') and 'nDCG' in line:
            inside = True
            continue
        if inside and line.startswith('─'):
            if rows:
                inside = False
            continue
        m = NUM.search(line)
        if line.startswith('Mean') and m:
            mean = tuple(float(x) for x in m.groups())
        elif inside and m:
            rows.append(tuple(float(x) for x in m.groups()))
        if line.startswith('elapsed_s'):
            elapsed = float(line.split()[1])
    return {'rows': rows, 'mean': mean, 'elapsed': elapsed, 'n': n}


def fmt(x):
    return f'{x:.2f}'.replace('.', ',')


def main():
    dirs = sys.argv[1:]
    ref = dirs[0]
    for bank in BANKS:
        print(f'\n### Banc {bank}\n')
        print('| État | Mode | Recall@5 | MRR | nDCG@5 | Gagnées / perdues (MRR) | Recall gagné / perdu | s / question |')
        print('|---|---|---|---|---|---|---|---|')
        for d in dirs:
            for mode in MODES:
                r = parse(os.path.join(HERE, 'results', d, f'{bank}-{mode}.txt'))
                if not r or not r['mean']:
                    continue
                b = parse(os.path.join(HERE, 'results', ref, f'{bank}-{mode}.txt'))
                win = lose = rw = rl = 0
                if b and len(b['rows']) == len(r['rows']):
                    for x, y in zip(b['rows'], r['rows']):
                        win += y[2] > x[2] + 1e-9
                        lose += y[2] < x[2] - 1e-9
                        rw += y[1] > x[1] + 1e-9
                        rl += y[1] < x[1] - 1e-9
                _, rec, mrr, ndcg = r['mean']
                lat = r['elapsed'] / r['n'] if r['elapsed'] and r['n'] else 0
                wl = '—' if d == ref else f'+{win} / −{lose}'
                rwl = '—' if d == ref else f'+{rw} / −{rl}'
                print(f'| {d} | {mode} | {fmt(rec)} | {fmt(mrr)} | {fmt(ndcg)} | {wl} | {rwl} | {lat:.2f} |')


if __name__ == '__main__':
    main()
