#!/usr/bin/env python3
"""Dated-fact extraction on a LAB brain, one `claude -p` call per page.

Claude (Sonnet, subscription) reads the page through the lab's gbrain MCP server
(read-only tools) and returns the dated facts as JSON (ops/eval/dated/extract-prompt.md).
Nothing is written to any brain here: load.py writes the facts into the lab.

Usage: python3 -I ops/eval/dated/extract.py --mcp-config <lab mcp.json> --pages ops/eval/dated/results/pages.txt [--jobs 2] [--max-calls 60]
Output: ops/eval/dated/results/work/extract/<slug with / as __>.json (raw stream in .log); existing files are skipped.
"""
import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = ['mcp__gbrain__get_page', 'mcp__gbrain__get_timeline', 'mcp__gbrain__resolve_slugs']


def parse_result(text):
    """The JSON object in Claude's answer; repairs the stray `,"` before a closing brace seen in practice."""
    raw = re.search(r'\{.*\}', text, re.S).group(0)
    try:
        return json.loads(raw)
    except ValueError:
        return json.loads(re.sub(r'",\s*"\s*}', '"}', raw))


def extract(slug, args, out_dir):
    path = os.path.join(out_dir, slug.replace('/', '__') + '.json')
    if os.path.exists(path):
        return slug, 'déjà fait', 0
    prompt = open(os.path.join(HERE, 'extract-prompt.md'), encoding='utf-8').read() \
        .replace('{slug}', slug).replace('{max_facts}', str(args.max_facts))
    cmd = ['claude', '-p', prompt, '--model', args.model, '--output-format', 'json',
           '--settings', json.dumps({'disableAllHooks': True}), '--allowedTools', ','.join(TOOLS),
           '--mcp-config', args.mcp_config, '--strict-mcp-config', '--no-session-persistence']
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, cwd=out_dir)
    open(path[:-5] + '.log', 'w', encoding='utf-8').write(proc.stdout + '\n--- stderr\n' + proc.stderr)
    try:
        res = json.loads(proc.stdout)
        text = res.get('result') or ''
        data = parse_result(text)
    except (ValueError, AttributeError):
        return slug, 'échec (JSON illisible)', 1
    data['cost_usd'] = res.get('total_cost_usd')
    json.dump(data, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    return slug, f"{len(data.get('facts') or [])} faits", 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mcp-config', required=True)
    ap.add_argument('--pages', required=True)
    ap.add_argument('--model', default='claude-sonnet-5-5')
    ap.add_argument('--max-facts', type=int, default=20)
    ap.add_argument('--jobs', type=int, default=2)
    ap.add_argument('--max-calls', type=int, default=60)
    args = ap.parse_args()
    out_dir = os.path.join(HERE, 'results', 'work', 'extract')
    os.makedirs(out_dir, exist_ok=True)
    slugs = [s.strip() for s in open(args.pages, encoding='utf-8') if s.strip() and not s.startswith('#')]
    todo = [s for s in slugs if not os.path.exists(os.path.join(out_dir, s.replace('/', '__') + '.json'))][:args.max_calls]
    calls = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for slug, status, n in ex.map(lambda s: extract(s, args, out_dir), todo):
            calls += n
            print(f'{slug:70} {status}', flush=True)
    print(f'appels claude : {calls}', file=sys.stderr)


if __name__ == '__main__':
    main()
