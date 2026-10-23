#!/usr/bin/env python3
"""Draws the hand-check sample: N facts from results/work/facts.json (fixed seed), one per page first.

For each fact, prints the claim, valid_from, date basis, evidence and the page lines that hold
the evidence's dates, read from the LAB database, so a reviewer can check date and claim.
Usage: python3 -I ops/eval/dated/sample.py --lab dated [--n 30] [--seed 20261023] > sample.md
"""
import argparse
import json
import os
import random
import re
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lab', required=True)
    ap.add_argument('--n', type=int, default=30)
    ap.add_argument('--seed', type=int, default=20261023)
    args = ap.parse_args()
    facts = json.load(open(os.path.join(HERE, 'results', 'work', 'facts.json'), encoding='utf-8'))['facts']
    rng = random.Random(args.seed)
    rng.shuffle(facts)
    seen, sample = set(), []
    for f in facts:  # one per page first, then fill
        if f['page'] not in seen:
            seen.add(f['page']); sample.append(f)
    sample = sample[:args.n] + [f for f in facts if f not in sample][:max(0, args.n - len(sample))]
    for i, f in enumerate(sample, 1):
        body = subprocess.run(['psql', '-X', '-At', '-d', f'gbrain_lab_{args.lab}', '-c',
                               f"select compiled_truth || E'\\n' || coalesce(timeline,'') from pages where slug = '{f['page']}'"],
                              capture_output=True, text=True).stdout.splitlines()
        y, m, d = f['valid_from'].split('-')
        pat = re.compile(rf'\b0?{int(d)}/0?{int(m)}\b|{f["valid_from"]}')
        lines = [l[:220] for l in body if pat.search(l)][:4]
        print(f"### {i}. `{f['id']}` — {f['entity']}\n- fait : {f['claim']}\n- valid_from : {f['valid_from']} ({f.get('date_basis')})"
              f"{' · remplace ' + f['supersedes'] if f.get('supersedes') else ''}\n- preuve : {f.get('evidence')}\n- lignes datées de la page :")
        for l in lines:
            print(f'  - {l}')
        print('- verdict : date ? · fait ?\n')


if __name__ == '__main__':
    main()
