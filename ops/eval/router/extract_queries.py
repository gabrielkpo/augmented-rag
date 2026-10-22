#!/usr/bin/env python3
"""Extracts the real gbrain searches from Claude Code transcripts.

Walks ~/.claude/projects/**/*.jsonl and keeps every call of the gbrain MCP
tools `search` / `query` (and the Bash commands `gbrain search|query "..."`),
with the last user request that precedes it in the session.

Output (JSON): one row per call {ts, session, project, tool, query, user_ask}.
The bench questions (qrels*.json) and the sessions after --until (lab work)
are excluded.

Usage: python3 -I ops/eval/router/extract_queries.py --out <fichier.json> [--until 2026-10-21T16:00]
"""
import argparse
import glob
import json
import os
import re

GB_TOOLS = {'mcp__gbrain__search': 'search', 'mcp__gbrain__query': 'query'}
BASH_RE = re.compile(r'gbrain\s+(search|query)\s+(?:--?\S+\s+)*["\']([^"\']{3,300})["\']')
SKIP_USER = re.compile(r'^\s*(<command-|<local-command|<system-reminder>|Caveat:|\[Request interrupted)')


def user_text(msg):
    c = msg.get('content')
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = [b.get('text', '') for b in c if isinstance(b, dict) and b.get('type') == 'text']
        return '\n'.join(parts)
    return ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--until', default='2026-10-21T16:00')
    ap.add_argument('--qrels-dir', default=os.path.expanduser('~/gbrain/ops/eval'))
    args = ap.parse_args()

    bench = set()
    for f in glob.glob(os.path.join(args.qrels_dir, 'qrels*.json')):
        try:
            for q in json.load(open(f, encoding='utf-8')).get('queries', []):
                bench.add(q['query'].strip().lower())
        except (ValueError, KeyError, AttributeError):
            pass

    rows = []
    for path in glob.glob(os.path.expanduser('~/.claude/projects/**/*.jsonl'), recursive=True):
        project = os.path.relpath(path, os.path.expanduser('~/.claude/projects')).split(os.sep)[0]
        last_ask = ''
        try:
            fh = open(path, encoding='utf-8', errors='replace')
        except OSError:
            continue
        with fh:
            for line in fh:
                if 'gbrain' not in line and '"type":"user"' not in line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                msg = ev.get('message') or {}
                if ev.get('type') == 'user' and not ev.get('isSidechain'):
                    t = user_text(msg).strip()
                    if t and not SKIP_USER.match(t):
                        last_ask = t[:600]
                    continue
                if ev.get('type') != 'assistant':
                    continue
                ts = ev.get('timestamp', '')
                if ts and ts[:16] > args.until:
                    continue
                for b in msg.get('content') or []:
                    if not isinstance(b, dict) or b.get('type') != 'tool_use':
                        continue
                    name, inp = b.get('name', ''), b.get('input') or {}
                    found = []
                    if name in GB_TOOLS and inp.get('query'):
                        found.append((GB_TOOLS[name], str(inp['query'])))
                    elif name == 'Bash':
                        for m in BASH_RE.finditer(str(inp.get('command', ''))):
                            found.append(('bash-' + m.group(1), m.group(2)))
                    for tool, q in found:
                        if q.strip().lower() in bench:
                            continue
                        rows.append({'ts': ts, 'session': ev.get('sessionId', ''), 'project': project,
                                     'sidechain': bool(ev.get('isSidechain')), 'tool': tool,
                                     'query': q.strip(), 'user_ask': last_ask})
    rows.sort(key=lambda r: r['ts'])
    json.dump(rows, open(args.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    uq = {r['query'].lower() for r in rows}
    asks = {(r['session'], r['user_ask']) for r in rows}
    print(f'{len(rows)} appels, {len(uq)} requêtes distinctes, {len(asks)} demandes utilisateur distinctes')


if __name__ == '__main__':
    main()
