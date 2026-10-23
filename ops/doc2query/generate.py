#!/usr/bin/env python3
"""doc2query: generates 3 to 5 questions per chunk for the chunks that have none, then hands them to gbrain.

"gbrain indexes, Claude reasons": gbrain calls no chat model. This script
  1. reads `gbrain chunk-text pending --json` (pages of 3 chunks or more where a chunk has no
     questions for its current text; code blocks and images are never concerned);
  2. calls Claude Haiku through `claude -p` (subscription), ONE call per page (groups of at most 14
     chunks; past 45,000 characters, the start of the page plus the group's chunks, 8 at a time),
     hooks off, no MCP server, no tool;
  3. writes a JSONL (one line per chunk: slug, chunk_hash, questions, model, prompt version);
  4. calls `gbrain chunk-text import <jsonl>`, which stores the questions, re-embeds and recomputes the
     keyword vectors of the touched chunks only (when the setting search.chunk_index_text = d2q is on).

Prompt: v3 of the lab (ops/eval/chunk/gen-context.py), reduced to the questions; same deterministic filter.
The default prompt is a neutral English one that asks for questions in the language of the page.
To use another prompt, point these variables at template files (read verbatim; one trailing newline
is dropped; same `str.format` placeholders as the defaults, literal braces doubled as `{{ }}`):
  DOC2QUERY_PROMPT_FILE         the user prompt ({title}, {slug}, {type}, {doc}, {targets})
  DOC2QUERY_SYSTEM_PROMPT_FILE  the system prompt (no placeholder)

Usage: python3 -I ops/doc2query/generate.py [--max-calls 40] [--jobs 4] [--gbrain "gbrain"]
                                            [--out-dir DIR] [--limit-pages N] [--dry-run] [--no-import]
"""
import argparse
import datetime
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

PROMPT_VERSION = '3-questions'
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

For EACH of the passages {targets}, produce "questions": 3 to 5 short questions this passage answers, as someone looking for this information without knowing the page would type them. Vary the vocabulary (synonyms, rephrasings). One question = one fact or decision present in the passage. Explicitly name what the passage does not name (project, system, people, period) when the question needs it. Never ask about what the passage does not contain. Invent nothing.

Write the questions in the language of the page.
Answer with a JSON array, one object per passage, in order:
[{{"i": N, "questions": ["...", "..."]}}]"""


def load_prompt(env_var, default):
    """The template in the file named by $env_var (read verbatim, one trailing newline dropped), else `default`."""
    path = os.environ.get(env_var)
    if not path:
        return default
    with open(os.path.expanduser(path), encoding='utf-8') as f:
        text = f.read()
    return text[:-1] if text.endswith('\n') else text


# Deterministic filter (taken from the lab): a question about what the passage does NOT contain
# would inject off-topic terms into the index; it is dropped. The patterns are French, the
# language the lab measured.
NEGATIVE = re.compile(r"(^|\s)((le|ce) passage|il|elle)\s+ne\s+(précise|détaille|couvre|mentionne|nomme|traite|donne|cite|décrit"
                      r"|contient|indique)\b|\bsans (préciser|détailler|nommer)\b", re.I)


def clean(s):
    return re.sub(r'\s+', ' ', re.sub(r'</?context>', '', str(s), flags=re.I)).strip()


def keep_question(q):
    return bool(q) and not NEGATIVE.search(q)


def plan_groups(page, wanted):
    """Target chunk groups for a page (only the chunks without questions are targeted)."""
    chunks = page['chunks']
    total = sum(len(c['chunk_text']) for c in chunks)
    full = total <= DOC_MAX
    size = GROUP_SMALL if full else GROUP_BIG
    targets = [c for c in chunks if c['chunk_index'] in wanted]
    return [(targets[i:i + size], full) for i in range(0, len(targets), size)]


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


def call_claude(prompt, system, cwd):
    t0 = time.time()
    proc = subprocess.run(
        ['claude', '-p', '--model', MODEL, '--output-format', 'json',
         '--system-prompt', system,
         '--settings', json.dumps({'disableAllHooks': True, 'alwaysThinkingEnabled': False}),
         '--strict-mcp-config', '--mcp-config', json.dumps({'mcpServers': {}}),
         '--tools', '', '--no-session-persistence'],
        input=prompt, capture_output=True, text=True, timeout=900, cwd=cwd)
    try:
        env = json.loads(proc.stdout)
    except ValueError:
        return None, {'error': (proc.stdout or proc.stderr)[-300:]}, time.time() - t0
    return env.get('result', ''), env, time.time() - t0


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
        qs = [clean(q) for q in (it.get('questions') or [])]
        qs = [q for q in qs if keep_question(q)][:5]
        if i in wanted and len(qs) >= 2:
            out[i] = qs
    return out


def gbrain(cmd, args):
    return subprocess.run(shlex.split(cmd) + args, capture_output=True, text=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--max-calls', type=int, default=40, help='cap on claude -p calls per run (quota)')
    ap.add_argument('--jobs', type=int, default=4)
    ap.add_argument('--gbrain', default=os.environ.get('GBRAIN_CMD', 'gbrain'), help='gbrain command (e.g. "gbrain-lab d2qctl")')
    ap.add_argument('--min-chunks', type=int, default=3)
    ap.add_argument('--limit-pages', type=int, default=0)
    ap.add_argument('--out-dir', default=os.path.join(os.environ.get('GBRAIN_HOME', os.path.expanduser('~')), '.gbrain', 'doc2query'))
    ap.add_argument('--dry-run', action='store_true', help='print the call plan, do not call Claude')
    ap.add_argument('--no-import', action='store_true', help='write the JSONL without importing it')
    args = ap.parse_args()
    prompt_tpl = load_prompt('DOC2QUERY_PROMPT_FILE', PROMPT)
    system = load_prompt('DOC2QUERY_SYSTEM_PROMPT_FILE', SYSTEM)

    pending_args = ['chunk-text', 'pending', '--json', '--min-chunks', str(args.min_chunks)]
    if args.limit_pages:
        pending_args += ['--limit-pages', str(args.limit_pages)]
    res = gbrain(args.gbrain, pending_args)
    if res.returncode != 0:
        print(f'pending a échoué (code {res.returncode}) : {res.stderr[-500:]}', file=sys.stderr)
        return 1
    pending = json.loads(res.stdout)

    tasks = []
    for page in pending['pages']:
        wanted = {c['chunk_index'] for c in page['chunks'] if c['eligible'] and not c['has_text']}
        for group, full in plan_groups(page, wanted):
            tasks.append((page, group, full))
    skipped = max(0, len(tasks) - args.max_calls)
    tasks = tasks[:args.max_calls]
    print(f"{len(pending['pages'])} page(s) en attente, {pending['missing_chunks']} chunk(s) ; "
          f"{len(tasks)} appel(s) à faire (plafond {args.max_calls}, {skipped} reporté(s) au prochain passage)", file=sys.stderr)
    if args.dry_run or not tasks:
        for page, group, full in tasks:
            print(f"  {page['slug']} : {len(group)} chunk(s){'' if full else ' (page longue)'}", file=sys.stderr)
        return 0

    os.makedirs(args.out_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    out_path = os.path.join(args.out_dir, f'd2q-{stamp}.jsonl')
    log_path = os.path.join(args.out_dir, f'd2q-{stamp}.calls.jsonl')
    lock = threading.Lock()
    workdir = tempfile.mkdtemp(prefix='d2q-')   # neutral cwd for claude -p

    def run(task):
        page, group, full = task
        wanted = {c['chunk_index'] for c in group}
        prompt = prompt_tpl.format(title=page['title'], slug=page['slug'], type=page['type'],
                               doc=render_doc(page, group, full), targets=', '.join(str(i) for i in sorted(wanted)))
        text, env, dt = call_claude(prompt, system, workdir)
        items = parse_items(text, wanted)
        by_index = {c['chunk_index']: c for c in group}
        lines = [{'source_id': page['source_id'], 'slug': page['slug'], 'chunk_index': i, 'chunk_hash': by_index[i]['chunk_hash'],
                  'questions': qs, 'model': MODEL, 'prompt_version': PROMPT_VERSION} for i, qs in sorted(items.items())]
        with lock:
            with open(out_path, 'a', encoding='utf-8') as f:
                for line in lines:
                    f.write(json.dumps(line, ensure_ascii=False) + '\n')
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps({'slug': page['slug'], 'wanted': sorted(wanted), 'got': sorted(items), 'duration_s': round(dt, 1),
                                    'usage': env.get('usage') if env else None, 'cost_usd': env.get('total_cost_usd') if env else None,
                                    'error': (env or {}).get('error') if not items else None}, ensure_ascii=False) + '\n')
        return page['slug'], len(items), len(wanted), dt

    done = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for fut in as_completed([ex.submit(run, t) for t in tasks]):
            done += 1
            try:
                slug, got, want, dt = fut.result()
                print(f'[{done}/{len(tasks)}] {slug} {got}/{want} {dt:.0f}s', file=sys.stderr, flush=True)
            except Exception as e:  # noqa: BLE001
                print(f'[{done}/{len(tasks)}] erreur {e}', file=sys.stderr, flush=True)

    if not os.path.exists(out_path):
        print('aucune question produite', file=sys.stderr)
        return 1
    print(f'questions : {out_path}', file=sys.stderr)
    if args.no_import:
        return 0
    res = gbrain(args.gbrain, ['chunk-text', 'import', out_path])
    sys.stderr.write(res.stdout + res.stderr)
    return res.returncode


if __name__ == '__main__':
    sys.exit(main())
