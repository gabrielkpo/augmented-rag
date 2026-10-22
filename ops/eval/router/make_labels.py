#!/usr/bin/env python3
"""Labelled set to measure the router's accuracy (overview: yes / no).

Positives: the 20 questions of the overview bench + the real queries that the
Haiku classification marked "vue_ensemble" AND that the manual review confirms.
Negatives: v2 (107), holdout A (41), multi-hop (22, bridge, comparison, temporal:
the hard negatives), the real queries marked "vue_ensemble" that the manual review
rejects, and 50 real "precise" queries drawn at random (reviewed: no overview).

The manual review is a JSON file given by --manual-labels (or ROUTER_MANUAL_LABELS):
{"labels": [{"query": "<real query, lower case>", "overview": true|false, "note": "..."}, ...]}
Without it, no reviewed real query is added.

Usage: python3 -I ops/eval/router/make_labels.py --out ops/eval/router/router-labels.json
                                                 [--manual-labels <labels.json>]
"""
import argparse
import json
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL = os.path.expanduser('~/gbrain/ops/eval')


def load_manual_labels(path):
    """Manual review of real queries: {lower-cased query: overview bool}; empty without a file."""
    if not path:
        return {}
    data = json.load(open(os.path.expanduser(path), encoding='utf-8'))
    return {r['query'].strip().lower(): bool(r['overview']) for r in data['labels']}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--manual-labels', default=os.environ.get('ROUTER_MANUAL_LABELS', ''),
                    help='JSON file of hand-reviewed real queries (see the module docstring)')
    args = ap.parse_args()
    hand_overview = load_manual_labels(args.manual_labels)
    rows = []
    for q in json.load(open(os.path.join(HERE, 'qrels-overview.json'), encoding='utf-8'))['queries']:
        rows.append({'query': q['query'], 'overview': True, 'origin': 'banc-vue'})
    for name, origin in [('qrels-v2.json', 'v2'), ('qrels-holdout-a.json', 'holdout-a'), ('qrels-multihop.json', 'multi-etapes')]:
        for q in json.load(open(os.path.join(EVAL, name), encoding='utf-8'))['queries']:
            rows.append({'query': q['query'], 'overview': False, 'origin': origin})
    real = json.load(open(os.path.join(HERE, 'data', 'freq-query-cache.json'), encoding='utf-8'))
    for r in real:
        key = r['query'].strip().lower()
        if r['label'] == 'vue_ensemble' and key in hand_overview:
            rows.append({'query': r['query'], 'overview': hand_overview[key], 'origin': 'reelle-relue'})
    random.seed(7)
    for r in random.sample([r for r in real if r['label'] in ('precise', 'entite')], 50):
        rows.append({'query': r['query'], 'overview': False, 'origin': 'reelle-precise'})
    json.dump(rows, open(args.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    pos = sum(r['overview'] for r in rows)
    print(f'{len(rows)} requêtes étiquetées : {pos} vue d\'ensemble, {len(rows) - pos} autres')


if __name__ == '__main__':
    main()
