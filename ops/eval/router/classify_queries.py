#!/usr/bin/env python3
"""Classifies real gbrain queries by question grain, with Claude (`claude -p`).

Input: TSV (timestamp, normalised query, query) or JSON [{query, ...}].
Output: JSON [{query, ts, label}] and a count per label.

Labels:
  vue_ensemble  (overview) synthesis of several pages around a theme, a person, a period
  relais        (bridge) an intermediate entity must be found first, then searched from
  precise       a fact, a decision, a procedure, a system, a specific page
  entite        (entity) only a name (person, company, system), with no other request
  autre         (other) noise, test, pasted text, off topic

The default prompt is a neutral English one; ROUTER_CLASSIFY_PROMPT_FILE, when set, names a
template file used instead (read verbatim, one trailing newline dropped, placeholder {items},
literal braces doubled as `{{ }}`). Either way the model must answer with the labels above.

Usage: python3 -I ops/eval/router/classify_queries.py <input> --out <output.json> [--model claude-haiku-5-5] [--batch 40]
"""
import argparse
import concurrent.futures
import glob
import json
import os
import re
import subprocess

LABELS = ('vue_ensemble', 'relais', 'precise', 'entite', 'autre')
PROMPT = """You classify queries sent by an agent (Claude Code) to a personal knowledge base (pages: people, projects, systems, meetings, chat threads, processes).

For each query, pick ONE label according to what it takes to answer it:
- vue_ensemble: synthesise SEVERAL pages around a theme, a person, a period or a set; no single page carries the answer. E.g. "what keeps coming up in my projects this quarter", "which topics with Alice", "what is Bob working on right now, ongoing work", "status of all projects", "lessons learned from all the experiments".
- relais: an intermediate entity (person, system) must be found first, then searched from. E.g. "who maintains the tool the compliance team uses".
- precise: a fact, a decision, a procedure, a system, a setting, a meeting, a specific page. A list of technical keywords is almost always precise.
- entite: only a name (person, company, tool) with no other request.
- autre: noise, test, unrelated pasted sentence, meaningless query.

Queries:
{items}

Answer only in JSON, a list: [{{"i": <number>, "l": "<label>"}}, ...] covering every query."""


def load_prompt(env_var, default):
    """The template in the file named by $env_var (read verbatim, one trailing newline dropped), else `default`."""
    path = os.environ.get(env_var)
    if not path:
        return default
    with open(os.path.expanduser(path), encoding='utf-8') as f:
        text = f.read()
    return text[:-1] if text.endswith('\n') else text


def load(path):
    if path.endswith('.json'):
        data = json.load(open(path, encoding='utf-8'))
        return [{'query': r['query'], 'ts': r.get('ts', '')} for r in data]
    rows = []
    for line in open(path, encoding='utf-8'):
        parts = line.rstrip('\n').split('\t')
        if len(parts) >= 3 and parts[2].strip():
            rows.append({'ts': parts[0], 'query': parts[2].strip()})
    return rows


def classify(batch, model, prompt_tpl):
    items = '\n'.join(f'{i}. {r["query"][:300]}' for i, r in enumerate(batch))
    for _ in range(3):
        proc = subprocess.run(
            ['claude', '-p', prompt_tpl.format(items=items), '--model', model,
             '--settings', json.dumps({'disableAllHooks': True}), '--tools', '', '--no-session-persistence',
             '--strict-mcp-config', '--mcp-config', '{"mcpServers": {}}'],
            capture_output=True, text=True, timeout=300)
        m = re.search(r'\[.*\]', proc.stdout, re.S)
        try:
            out = {int(x['i']): x['l'] for x in json.loads(m.group(0))} if m else {}
        except (ValueError, KeyError, TypeError):
            out = {}
        if len(out) >= len(batch) * 0.9:
            break
    return [dict(r, label=out.get(i) if out.get(i) in LABELS else 'erreur') for i, r in enumerate(batch)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('input')
    ap.add_argument('--out', required=True)
    ap.add_argument('--model', default='claude-haiku-5-5')
    ap.add_argument('--batch', type=int, default=40)
    ap.add_argument('--jobs', type=int, default=4)
    ap.add_argument('--qrels-dir', default=os.path.expanduser('~/gbrain/ops/eval'))
    args = ap.parse_args()
    prompt_tpl = load_prompt('ROUTER_CLASSIFY_PROMPT_FILE', PROMPT)
    rows = load(args.input)
    # The bench questions (qrels*.json) are not real searches.
    bench = set()
    for f in glob.glob(os.path.join(args.qrels_dir, 'qrels*.json')):
        bench |= {q['query'].strip().lower() for q in json.load(open(f, encoding='utf-8')).get('queries', [])}
    before = len(rows)
    rows = [r for r in rows if r['query'].strip().lower() not in bench]
    print(f'{before - len(rows)} requêtes de banc écartées, {len(rows)} gardées')
    batches = [rows[i:i + args.batch] for i in range(0, len(rows), args.batch)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        res = [r for b in ex.map(lambda b: classify(b, args.model, prompt_tpl), batches) for r in b]
    json.dump(res, open(args.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    n = len(res)
    for lab in LABELS + ('erreur',):
        c = sum(r['label'] == lab for r in res)
        print(f'{lab:13} {c:4}  {100 * c / n:5.1f} %')
    print(f'total         {n:4}')


if __name__ == '__main__':
    main()
