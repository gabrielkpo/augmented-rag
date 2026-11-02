#!/usr/bin/env python3
"""Strategy router: one Claude Haiku call per question picks a question type,
and a fixed table maps the type to an agentic strategy.

  temporal -> temporal   (ops/eval/agentic/temporal.md)
  bridge   -> iterative2 (ops/eval/agentic/iterative2.md)
  compare  -> iterative2
  overview -> iterative2
  simple   -> base       (no added instructions)

The table is fixed before any agentic run; the prompt (router-prompt.md) is only
checked against the bench's `source` field, never against judged scores.

Usage: python3 -I ops/eval/routing/route.py --qrels ops/eval/qrels-multihop-v3.json \
    --out ops/eval/routing/results/router.json [--only id,id] [--reuse previous.json] [--max-calls 60]
Prints the agreement with `source` and the confusion table.
"""
import argparse
import collections
import concurrent.futures
import json
import os
import re
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
TYPES = ('temporal', 'bridge', 'compare', 'overview', 'simple')
STRATEGY = {'temporal': 'temporal', 'bridge': 'iterative2', 'compare': 'iterative2',
            'overview': 'iterative2', 'simple': 'base'}


def classify(query, template, model):
    proc = subprocess.run(
        ['claude', '-p', template.format(query=query), '--model', model,
         '--settings', json.dumps({'disableAllHooks': True}), '--tools', '', '--no-session-persistence'],
        capture_output=True, text=True, timeout=180)
    m = re.search(r'\{.*\}', proc.stdout, re.S)
    try:
        out = json.loads(m.group(0)) if m else {}
    except ValueError:
        out = {}
    if out.get('type') not in TYPES:
        out = {'type': 'simple', 'raison': 'erreur: ' + (proc.stdout or proc.stderr)[-120:], 'error': True}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--qrels', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--prompt', default=os.path.join(HERE, 'router-prompt.md'))
    ap.add_argument('--model', default='claude-haiku-5-5')
    ap.add_argument('--only', default='')
    ap.add_argument('--reuse', default='', help='previous output: its decisions are kept for questions not in --only')
    ap.add_argument('--max-calls', type=int, default=60)
    ap.add_argument('--jobs', type=int, default=4)
    args = ap.parse_args()

    template = open(args.prompt, encoding='utf-8').read()
    queries = json.load(open(args.qrels, encoding='utf-8'))['queries']
    previous = {r['id']: r for r in json.load(open(args.reuse, encoding='utf-8'))} if args.reuse else {}
    wanted = set(args.only.split(',')) if args.only else {q['id'] for q in queries if q['id'] not in previous}
    todo = [q for q in queries if q['id'] in wanted]
    if len(todo) > args.max_calls:
        raise SystemExit(f'{len(todo)} calls needed, more than --max-calls {args.max_calls}')
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        fresh = dict(zip([q['id'] for q in todo], ex.map(lambda q: classify(q['query'], template, args.model), todo)))

    rows = []
    for q in queries:
        if q['id'] in fresh:
            d = fresh[q['id']]
        elif q['id'] in previous:
            d = {'type': previous[q['id']]['type'], 'raison': previous[q['id']]['raison']}
        else:
            continue
        rows.append({'id': q['id'], 'source': q.get('source'), 'type': d['type'], 'raison': d.get('raison', ''),
                     'strategy': STRATEGY[d['type']], 'error': bool(d.get('error'))})
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    json.dump(rows, open(args.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    agree = sum(r['type'] == r['source'] for r in rows)
    print(f'calls: {len(todo)} · agreement with source: {agree}/{len(rows)}')
    conf = collections.Counter((r['source'], r['type']) for r in rows)
    print('source \\ type  ' + ' '.join(f'{t[:8]:>8}' for t in TYPES))
    for s in TYPES:
        print(f'{s:13}  ' + ' '.join(f'{conf[(s, t)]:>8}' for t in TYPES))
    for r in rows:
        if r['type'] != r['source']:
            print(f"  {r['id']}: {r['source']} -> {r['type']} ({r['raison']})")


if __name__ == '__main__':
    main()
