#!/usr/bin/env python3
"""Generates once, for every chunk of the pages of 3 chunks or more:
  (B) a context sentence that places the chunk in its page (Contextual Retrieval, Anthropic 2024);
  (C) 3 to 5 questions the chunk answers (doc2query).

The text is produced by Claude Haiku through `claude -p` (subscription), never by gbrain's LLM.
One call per page for all its chunks; very long pages are split into groups of chunks.
Outputs are cached in a replayable JSONL: a group already present is never regenerated.

The default prompt is a neutral English one. To use another prompt, point these variables at
template files (read verbatim, one trailing newline dropped, same `str.format` placeholders,
literal braces doubled as `{{ }}`):
  CHUNK_CONTEXT_PROMPT_FILE         the user prompt ({title}, {slug}, {type}, {doc}, {targets})
  CHUNK_CONTEXT_SYSTEM_PROMPT_FILE  the system prompt (no placeholder)

Usage: python3 -I gen-context.py [--jobs 6] [--limit N] [--only slug1,slug2]
Input: ~/gbrain-labs/chunk/cache/pages.jsonl (export-pages.ts)
Output: ~/gbrain-labs/chunk/cache/context-haiku.jsonl
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

CACHE = os.path.expanduser('~/gbrain-labs/chunk/cache')
PROMPT_VERSION = 3
MODEL = 'claude-haiku-5-5'
DOC_MAX = 45000        # past this, the page is reduced to its start + the group's chunks
HEAD_KEEP = 6000       # start of page kept for pages that are too long
GROUP_SMALL = 14       # chunks per call when the page fits in DOC_MAX
GROUP_BIG = 8          # chunks per call for pages that are too long

SYSTEM = ("You are building the search index of a personal knowledge base. "
          "You answer only with the requested JSON, with no comment and no code block. "
          "You describe what each passage contains, never what it lacks.")

PROMPT = """Here is a page from a personal knowledge base. It is split into numbered passages, tagged <chunk i="N">.

Page: {title}
Identifier: {slug} (type: {type})

<document>
{doc}
</document>

For EACH of the passages {targets}, produce:
1. "contexte": a single short sentence (40 words at most) that places the passage in the page to improve its retrieval: what the page is about, and what this specific passage covers (topic, project, system, people, date when it matters). Explicitly name what the passage does not name (page, project, people, period). Describe only what is present: never write what the passage does not contain or does not specify (no "does not cover", "does not detail", "without specifying"). Invent nothing.
2. "questions": 3 to 5 short questions this passage answers, as someone looking for this information without knowing the page would type them. Vary the vocabulary (synonyms, rephrasings). One question = one fact or decision present in the passage.

Write in the language of the page.
Answer with a JSON array, one object per passage, in order:
[{{"i": N, "contexte": "...", "questions": ["...", "..."]}}]"""


def load_prompt(env_var, default):
    """The template in the file named by $env_var (read verbatim, one trailing newline dropped), else `default`."""
    path = os.environ.get(env_var)
    if not path:
        return default
    with open(os.path.expanduser(path), encoding='utf-8') as f:
        text = f.read()
    return text[:-1] if text.endswith('\n') else text


def load_pages(path):
    with open(path, encoding='utf-8') as f:
        return [json.loads(l) for l in f if l.strip()]


def plan_groups(page):
    chunks = page['chunks']
    total = sum(len(c['chunk_text']) for c in chunks)
    size = GROUP_SMALL if total <= DOC_MAX else GROUP_BIG
    groups = [chunks[i:i + size] for i in range(0, len(chunks), size)]
    return [(gi, g, total <= DOC_MAX) for gi, g in enumerate(groups)]


def render_doc(page, group, full):
    def tag(c):
        return f'<chunk i="{c["chunk_index"]}">\n{c["chunk_text"]}\n</chunk>'
    if full:
        return '\n'.join(tag(c) for c in page['chunks'])
    head = page['chunks'][0]['chunk_text'][:HEAD_KEEP]
    ids = {c['chunk_index'] for c in group}
    parts = [f'<debut_de_page>\n{head}\n</debut_de_page>', '[... passages omis ...]']
    parts += [tag(c) for c in page['chunks'] if c['chunk_index'] in ids]
    return '\n'.join(parts)


def call_claude(prompt, system):
    t0 = time.time()
    proc = subprocess.run(
        ['claude', '-p', '--model', MODEL, '--output-format', 'json',
         '--system-prompt', system,
         '--settings', json.dumps({'disableAllHooks': True, 'alwaysThinkingEnabled': False}),
         '--strict-mcp-config', '--mcp-config', json.dumps({'mcpServers': {}}),
         '--tools', '', '--no-session-persistence'],
        input=prompt, capture_output=True, text=True, timeout=900, cwd=CACHE)
    dt = time.time() - t0
    try:
        env = json.loads(proc.stdout)
    except ValueError:
        return None, {'error': (proc.stdout or proc.stderr)[-300:]}, dt
    return env.get('result', ''), env, dt


def parse_items(text, wanted):
    m = re.search(r'\[.*\]', text or '', re.S)
    if not m:
        return {}
    try:
        arr = json.loads(m.group(0))
    except ValueError:
        return {}
    out = {}
    for it in arr if isinstance(arr, list) else []:
        try:
            i = int(it.get('i'))
        except (TypeError, ValueError, AttributeError):
            continue
        ctx = str(it.get('contexte') or '').strip()
        qs = [str(q).strip() for q in (it.get('questions') or []) if str(q).strip()]
        if i in wanted and ctx and len(qs) >= 2:
            out[i] = {'contexte': ctx, 'questions': qs[:5]}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--jobs', type=int, default=6)
    ap.add_argument('--limit', type=int, default=0, help='number of pages (trial run)')
    ap.add_argument('--only', default='', help='comma-separated slugs')
    ap.add_argument('--pages', default=os.path.join(CACHE, 'pages.jsonl'))
    ap.add_argument('--out', default=os.path.join(CACHE, 'context-haiku.jsonl'))
    args = ap.parse_args()
    prompt_tpl = load_prompt('CHUNK_CONTEXT_PROMPT_FILE', PROMPT)
    system = load_prompt('CHUNK_CONTEXT_SYSTEM_PROMPT_FILE', SYSTEM)

    pages = load_pages(args.pages)
    if args.only:
        keep = set(args.only.split(','))
        pages = [p for p in pages if p['slug'] in keep]
    if args.limit:
        pages = pages[:args.limit]

    done = {}
    if os.path.exists(args.out):
        for l in open(args.out, encoding='utf-8'):
            r = json.loads(l)
            if r.get('prompt_version') == PROMPT_VERSION:
                for i in r.get('items', {}):
                    done[(r['slug'], int(i))] = True

    tasks = []
    for p in pages:
        for gi, g, full in plan_groups(p):
            missing = [c for c in g if (p['slug'], c['chunk_index']) not in done]
            if missing:
                tasks.append((p, gi, missing, full))
    print(f'{len(tasks)} appels à faire ({len(pages)} pages)', file=sys.stderr)

    lock = threading.Lock()

    def run(task):
        p, gi, group, full = task
        wanted = {c['chunk_index'] for c in group}
        prompt = prompt_tpl.format(title=p['title'], slug=p['slug'], type=p['type'],
                               doc=render_doc(p, group, full),
                               targets=', '.join(str(i) for i in sorted(wanted)))
        text, env, dt = call_claude(prompt, system)
        items = parse_items(text, wanted)
        rec = {'slug': p['slug'], 'page_id': p['page_id'], 'group': gi, 'model': MODEL,
               'prompt_version': PROMPT_VERSION, 'full_doc': full, 'prompt_chars': len(prompt),
               'wanted': sorted(wanted), 'items': {str(k): v for k, v in items.items()},
               'usage': env.get('usage') if env else None,
               'cost_usd': env.get('total_cost_usd') if env else None,
               'duration_s': round(dt, 1), 'error': env.get('error') if env and not items else None,
               'at': time.strftime('%Y-%m-%dT%H:%M:%S')}
        with lock:
            with open(args.out, 'a', encoding='utf-8') as f:
                f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        return p['slug'], len(items), len(wanted), dt

    n = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for fut in as_completed([ex.submit(run, t) for t in tasks]):
            n += 1
            try:
                slug, got, want, dt = fut.result()
                print(f'[{n}/{len(tasks)}] {slug} {got}/{want} {dt:.0f}s', file=sys.stderr, flush=True)
            except Exception as e:  # noqa: BLE001
                print(f'[{n}/{len(tasks)}] erreur {e}', file=sys.stderr, flush=True)


if __name__ == '__main__':
    main()
