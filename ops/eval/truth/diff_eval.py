#!/usr/bin/env python3
"""Compares two `gbrain eval` outputs question by question.

Usage: python3 -I ops/eval/truth/diff_eval.py <before.txt> <after.txt>
Prints the means of both runs and every question whose Recall@5, MRR or
nDCG@5 changed (the question text is truncated by gbrain eval).
"""
import re
import sys

ROW = re.compile(r'^(.{38})\s+([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s*$')


def rows(path):
    out, mean = [], None
    for line in open(path, encoding='utf-8'):
        m = ROW.match(line.rstrip('\n'))
        if not m:
            continue
        vals = tuple(float(m.group(i)) for i in (3, 4, 5))
        if m.group(1).startswith('Mean'):
            mean = vals
        else:
            out.append((m.group(1).strip(), vals))
    return out, mean


def main():
    (a, ma), (b, mb) = rows(sys.argv[1]), rows(sys.argv[2])
    if len(a) != len(b):
        raise SystemExit(f'nombre de questions différent : {len(a)} contre {len(b)}')
    print(f'moyennes R@5 / MRR / nDCG@5 : {ma} -> {mb}')
    changed = [(qa, va, vb) for (qa, va), (qb, vb) in zip(a, b) if va != vb]
    for q, va, vb in changed:
        sign = '+' if sum(vb) > sum(va) else '-'
        print(f'{sign} {q:40} R {va[0]:.2f}->{vb[0]:.2f}  MRR {va[1]:.2f}->{vb[1]:.2f}  nDCG {va[2]:.2f}->{vb[2]:.2f}')
    up = sum(1 for _, va, vb in changed if sum(vb) > sum(va))
    print(f'{len(changed)} question(s) changée(s) : {up} en hausse, {len(changed) - up} en baisse')


if __name__ == '__main__':
    main()
