#!/usr/bin/env python3
"""Agentic bench: Claude answers the questions with the gbrain read tools.

Compares two variants on the same questions:
  base       the usual instructions (gbrain server instructions, CLAUDE.md)
  iterative  the same + ops/eval/agentic/iterative-search.md as system prompt

Every question goes through `claude -p` (Claude subscription, no key), hooks
off, tools limited to reading the brain. Measured per question:
  - cite   : share of the expected pages cited in the "Sources:" line
  - lu     : share of the expected pages opened with get_page
  - ok     : every expected page cited
  - searches, turns, duration, cost

Usage:
  python3 -I ops/eval/agentic/run.py --qrels ops/eval/qrels-multihop.json \
      [--variants base,iterative] [--model claude-sonnet-5-5] [--jobs 3] [--only mh-01,mh-02]
Results: ops/eval/results/<timestamp>-agentic/ (one JSON per question and variant, and resume.md).
"""
import argparse
import concurrent.futures
import datetime
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
READ_TOOLS = [
    'mcp__gbrain__search', 'mcp__gbrain__query', 'mcp__gbrain__get_page',
    'mcp__gbrain__get_backlinks', 'mcp__gbrain__get_links', 'mcp__gbrain__traverse_graph',
    'mcp__gbrain__entity', 'mcp__gbrain__get_timeline', 'mcp__gbrain__resolve_slugs',
]
SEARCH_TOOLS = {'mcp__gbrain__search', 'mcp__gbrain__query'}
PROMPT = (
    "Réponds à cette question à partir du brain gbrain, en français et en quelques phrases. "
    "Termine ta réponse par une dernière ligne exactement de la forme "
    "`Sources: slug-1, slug-2` listant les slugs des pages qui portent la réponse.\n\n"
    "Question : {q}"
)


def run_one(q, variant, model, out_dir, cwd, mcp_config=''):
    cmd = [
        'claude', '-p', PROMPT.format(q=q['query']),
        '--model', model,
        '--output-format', 'stream-json', '--verbose',
        '--settings', json.dumps({'disableAllHooks': True}),
        '--allowedTools', ','.join(READ_TOOLS),
        '--no-session-persistence',
    ]
    # Copy of the database (lab): the gbrain MCP server of this file replaces the user's.
    if mcp_config:
        cmd += ['--mcp-config', mcp_config, '--strict-mcp-config']
    # "base" variant: nothing added. Otherwise ops/eval/agentic/<variant>.md
    # ("iterative" keeps its historical name iterative-search.md).
    if variant != 'base':
        name = 'iterative-search.md' if variant == 'iterative' else f'{variant}.md'
        cmd += ['--append-system-prompt', open(os.path.join(HERE, name), encoding='utf-8').read()]
    start = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=600)
    elapsed = time.time() - start

    searches, reads, tools, final, result = [], [], [], '', {}
    for line in proc.stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get('type') == 'assistant':
            for block in (ev.get('message') or {}).get('content') or []:
                if block.get('type') == 'tool_use':
                    name, args = block.get('name', ''), block.get('input') or {}
                    tools.append(name)
                    if name in SEARCH_TOOLS:
                        searches.append(args.get('query', ''))
                    if name == 'mcp__gbrain__get_page' and args.get('slug'):
                        reads.append(str(args['slug']).split(':', 1)[-1])
        elif ev.get('type') == 'result':
            result = ev
            final = ev.get('result') or ''

    m = re.findall(r'Sources?\s*:\s*(.+)', final)
    cited = [s.strip(' `*[]') for s in m[-1].split(',')] if m else []
    cited = [s.split(':', 1)[-1] for s in cited if s]
    required = q['relevant']
    rec = {
        'id': q['id'], 'variant': variant, 'source': q.get('source'), 'query': q['query'],
        'required': required, 'cited': cited, 'read': reads, 'searches': searches,
        'tool_calls': len(tools), 'turns': result.get('num_turns'),
        'duration_s': round(elapsed, 1), 'cost_usd': result.get('total_cost_usd'),
        'cite': sum(s in cited for s in required) / len(required),
        'lu': sum(s in reads or s in cited for s in required) / len(required),
        'answer': final, 'exit': proc.returncode,
    }
    rec['ok'] = rec['cite'] == 1.0
    with open(os.path.join(out_dir, f"{q['id']}-{variant}.json"), 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--qrels', required=True)
    ap.add_argument('--variants', default='base,iterative')
    ap.add_argument('--model', default='claude-sonnet-5-5')
    ap.add_argument('--jobs', type=int, default=3)
    ap.add_argument('--only', default='')
    ap.add_argument('--mcp-config', default='', help='MCP config file pointing at a lab gbrain server')
    args = ap.parse_args()

    queries = json.load(open(args.qrels, encoding='utf-8'))['queries']
    if args.only:
        wanted = set(args.only.split(','))
        queries = [q for q in queries if q['id'] in wanted]
    variants = args.variants.split(',')
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    out_dir = os.path.join(HERE, '..', 'results', f'{stamp}-agentic')
    os.makedirs(out_dir, exist_ok=True)
    # Neutral folder: no project CLAUDE.md, only the user config.
    cwd = os.path.join(out_dir, 'cwd')
    os.makedirs(cwd, exist_ok=True)

    jobs = [(q, v) for q in queries for v in variants]
    recs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = {ex.submit(run_one, q, v, args.model, out_dir, cwd, args.mcp_config): (q['id'], v) for q, v in jobs}
        for fut in concurrent.futures.as_completed(futs):
            qid, v = futs[fut]
            try:
                r = fut.result()
            except Exception as e:  # noqa: BLE001 — a failing question must not stop the bench
                print(f'{qid} {v} : échec ({e})', file=sys.stderr)
                continue
            recs.append(r)
            print(f"{qid:8} {v:10} cité {r['cite']:.2f} lu {r['lu']:.2f} "
                  f"recherches {len(r['searches'])} {r['duration_s']} s", flush=True)

    def mean(xs):
        return sum(xs) / len(xs) if xs else 0.0

    lines = [f'# Banc agentique — {stamp}', '', f'Modèle : `{args.model}` · questions : {len(queries)} · fichier : `{os.path.basename(args.qrels)}`', '',
             '| Variante | Origine | n | Toutes citées | Citées | Lues | Recherches | Durée (s) | Coût ($) |', '|---|---|---|---|---|---|---|---|---|']
    groups = sorted({(r['variant'], r['source']) for r in recs}) + [(v, 'tout') for v in variants]
    for v, src in groups:
        rs = [r for r in recs if r['variant'] == v and (src == 'tout' or r['source'] == src)]
        if not rs:
            continue
        lines.append(f"| {v} | {src} | {len(rs)} | {mean([r['ok'] for r in rs]):.2f} | {mean([r['cite'] for r in rs]):.2f} | "
                     f"{mean([r['lu'] for r in rs]):.2f} | {mean([len(r['searches']) for r in rs]):.1f} | "
                     f"{mean([r['duration_s'] for r in rs]):.0f} | {sum((r['cost_usd'] or 0) for r in rs):.2f} |")
    open(os.path.join(out_dir, 'resume.md'), 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
    print('\n'.join(lines))
    print(f'\nRésultats : {out_dir}')


if __name__ == '__main__':
    main()
