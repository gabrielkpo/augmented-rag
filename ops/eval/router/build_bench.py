#!/usr/bin/env python3
"""Builds the bench of overview questions (qrels-overview.json).

For each question of bench-spec.json, a set of pages is picked by an explicit
rule (pages linked to a person, pages of a period, keyword search on a topic)
and handed to Claude Sonnet (`claude -p`), which proposes the elements a good
answer must contain, each with the page that carries it and a word-for-word
quote. Every quote is then VERIFIED in the lab database (exact substring,
normalised whitespace): an unverified element is dropped.

Read-only on the lab database gbrain_lab_router (psql), never the live brain.

The default prompt is a neutral English one; ROUTER_BENCH_PROMPT_FILE, when set,
names a template file used instead (read verbatim, one trailing newline dropped,
placeholders {q} and {pages}, literal braces doubled as `{{ }}`).

Usage: python3 -I ops/eval/router/build_bench.py --spec ops/eval/router/bench-spec.json \
         --out ops/eval/router/qrels-overview.json [--only ov-01,ov-02]
"""
import argparse
import concurrent.futures
import json
import os
import re
import subprocess

DB = 'gbrain_lab_router'
MAX_PAGES = 45
EXCERPT = 1800

PROMPT = """Here are pages from a personal knowledge base.

Overview question: {q}

Your task: write the grading rubric for this question. List 4 to 6 ELEMENTS a good answer must contain. An element is a topic, a fact or a trend that answers the question, preferably present in several pages. For each element, give the page that carries it best (exact slug) and a WORD-FOR-WORD quote from that page (8 to 25 words, copied without any change, without "…"), which proves the element. Write the elements in the language of the pages.

Answer only in JSON:
{{"elements": [{{"element": "<a short sentence>", "slug": "<slug>", "citation": "<exact excerpt>", "autres_pages": ["<slug>", ...]}}]}}

Pages:
{pages}"""


def load_prompt(env_var, default):
    """The template in the file named by $env_var (read verbatim, one trailing newline dropped), else `default`."""
    path = os.environ.get(env_var)
    if not path:
        return default
    with open(os.path.expanduser(path), encoding='utf-8') as f:
        text = f.read()
    return text[:-1] if text.endswith('\n') else text


def psql_json(sql):
    out = subprocess.run(['psql', '-d', DB, '-At', '-c', sql], capture_output=True, text=True, check=True).stdout.strip()
    return json.loads(out) if out else []


def sql_str(s):
    return "'" + s.replace("'", "''") + "'"


def select_pages(sel):
    """Selection rules: person, period, fts (combinable), always on live pages."""
    conds, order = ['p.deleted_at is null', "p.slug not like 'themes/%'"], 'coalesce(p.effective_date, p.created_at) desc'
    if 'person' in sel:
        conds.append(f"(p.slug = {sql_str(sel['person'])} or p.id in (select l.from_page_id from links l join pages t on t.id = l.to_page_id where t.slug = {sql_str(sel['person'])}))")
    if 'since' in sel:
        conds.append(f"coalesce(p.effective_date, p.created_at) >= {sql_str(sel['since'])}::timestamptz")
    if 'until' in sel:
        conds.append(f"coalesce(p.effective_date, p.created_at) < {sql_str(sel['until'])}::timestamptz")
    if 'fts' in sel:
        conds.append(f"p.search_vector @@ websearch_to_tsquery('french', {sql_str(sel['fts'])})")
        order = f"ts_rank(p.search_vector, websearch_to_tsquery('french', {sql_str(sel['fts'])})) desc"
    if 'slug_like' in sel:
        conds.append('(' + ' or '.join(f'p.slug like {sql_str(s)}' for s in sel['slug_like']) + ')')
    if 'types' in sel:
        conds.append('p.type in (' + ','.join(sql_str(t) for t in sel['types']) + ')')
    sql = (f"select coalesce(json_agg(x), '[]') from (select p.slug, p.title, p.type, "
           f"to_char(coalesce(p.effective_date, p.created_at), 'YYYY-MM-DD') as date, "
           f"left(p.compiled_truth, {EXCERPT}) as text from pages p where {' and '.join(conds)} "
           f"order by {order} limit {sel.get('limit', MAX_PAGES)}) x")
    return psql_json(sql)


def norm(s):
    s = s.replace('’', "'").replace(' ', ' ')
    return re.sub(r'\s+', ' ', s).strip().lower()


def verify(slug, citation):
    rows = psql_json(f"select coalesce(json_agg(compiled_truth || ' ' || coalesce(timeline, '')), '[]') from pages where slug = {sql_str(slug)} and deleted_at is null")
    return bool(rows) and norm(citation) in norm(rows[0])


def build(spec, model, prompt_tpl):
    pages = select_pages(spec['select'])
    # "@path" in a `claude -p` prompt attaches the file: the pages' @ are neutralised.
    block = '\n\n'.join(f"### {p['slug']} ({p['type']}, {p['date']}) {p['title']}\n{p['text']}" for p in pages).replace('@', '(at)')
    proc = subprocess.run(
        ['claude', '-p', prompt_tpl.format(q=spec['query'], pages=block), '--model', model,
         '--settings', json.dumps({'disableAllHooks': True}), '--tools', '', '--no-session-persistence',
             '--strict-mcp-config', '--mcp-config', '{"mcpServers": {}}'],
        capture_output=True, text=True, timeout=600)
    m = re.search(r'\{.*\}', proc.stdout, re.S)
    try:
        elems = json.loads(m.group(0))['elements'] if m else []
    except (ValueError, KeyError):
        elems = []
    kept, dropped = [], []
    for e in elems:
        (kept if verify(e.get('slug', ''), e.get('citation', '')) else dropped).append(e)
    raw = '' if elems else (proc.stdout[-400:] + ' | ' + proc.stderr[-400:])
    return {**spec, 'candidates': len(pages), 'elements': kept, 'dropped': dropped, 'raw': raw}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--spec', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--model', default='claude-sonnet-5-5')
    ap.add_argument('--only', default='')
    ap.add_argument('--jobs', type=int, default=4)
    args = ap.parse_args()
    prompt_tpl = load_prompt('ROUTER_BENCH_PROMPT_FILE', PROMPT)
    specs = json.load(open(args.spec, encoding='utf-8'))
    if args.only:
        specs = [s for s in specs if s['id'] in args.only.split(',')]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        built = list(ex.map(lambda s: build(s, args.model, prompt_tpl), specs))
    prev = {}
    if os.path.exists(args.out):
        prev = {q['id']: q for q in json.load(open(args.out, encoding='utf-8')).get('_draft', [])}
    for b in built:
        prev[b['id']] = b
    draft = sorted(prev.values(), key=lambda q: q['id'])
    queries = []
    for b in draft:
        els = b['elements']
        need = min(3, len(els))
        answer = (f"Une bonne réponse cite au moins {need} de ces {len(els)} éléments, sans les contredire : "
                  + ' ; '.join(f"({i + 1}) {e['element']}" for i, e in enumerate(els)) + '.')
        relevant = list(dict.fromkeys(e['slug'] for e in els))
        queries.append({'id': b['id'], 'query': b['query'], 'source': b.get('kind', 'overview'),
                        'relevant': relevant, 'answer': answer,
                        'elements': [{'element': e['element'], 'slug': e['slug'], 'citation': e['citation'],
                                      'autres_pages': e.get('autres_pages', [])} for e in els]})
    json.dump({'version': 1, 'note': "Banc de vue d'ensemble, éléments vérifiés mot pour mot dans gbrain_lab_router (copie du 21/10/2026).",
               'queries': queries, '_draft': draft}, open(args.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    for b in built:
        print(f"{b['id']}: {b['candidates']} pages, {len(b['elements'])} éléments vérifiés, {len(b['dropped'])} écartés {b['raw'][:300]}")


if __name__ == '__main__':
    main()
