#!/usr/bin/env python3
"""Writes the thematic summary pages with Claude Sonnet (`claude -p`), then stores
them in the lab copy through ops/eval/router/lab.sh (never the live brain).

Three groupings, a single summary level:
  topic   the k-means groups over the page embeddings (data/clusters.json)
  person  the --people most linked people of the graph (incoming links), the brain's
          owner excluded (--owner or ROUTER_THEMES_OWNER, the owner's person slug; no default)
  month   one summary per month (pages' effective date)

Each page: frontmatter `type: theme`, `theme_summary: true`, `theme_kind`, `theme_members`
(source slugs), Markdown body with [[slug]] links to the source pages.
The Markdown files are kept outside the repository, in ~/gbrain-labs/router/themes/.

The default prompt is a neutral English one; ROUTER_THEMES_PROMPT_FILE, when set, names a
template file used instead (read verbatim, one trailing newline dropped, placeholders
{kind_label}, {n}, {pages} and {last}, literal braces doubled as `{{ }}`).

Usage:
  python3 -I ops/eval/router/write_themes.py generate [--only topic|person|month] [--jobs 3] [--owner people/<slug>]
  python3 -I ops/eval/router/write_themes.py load        # writes the pages into the lab database
"""
import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import time
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get('ROUTER_LAB_DB', 'gbrain_lab_router2')  # summaries generated from gbrain_lab_router (same snapshot)
OUT = os.path.expanduser('~/gbrain-labs/router/themes')
BUDGET = 110_000  # characters of pages per summary

PROMPT = """You are writing a THEMATIC SUMMARY page for a personal knowledge base. This page is used to answer overview questions ("what keeps coming up on this topic?", "which topics with this person?", "what happened that month?").

Grouping: {kind_label}
Pages of the grouping ({n} pages, from the most central to the most peripheral, or from the most recent to the oldest):

{pages}

Write the page in the main language of the pages, in Markdown, and nothing else. Structure (headings translated into that language):
# <short title of the theme, without the word "Theme">
One sentence: what this grouping is about and over which period (absolute dates).
## Recurring topics
5 to 10 bullets. Each bullet: the topic, what was said or decided about it, the dates, and the source pages as [[slug]] links (at least one per bullet, the exact slugs given above).
## Timeline
5 to 12 dated bullets (YYYY-MM-DD), the notable events and decisions, with [[slug]].
## People
The people who keep coming up and their role in this theme, with [[slug]] when a person page is cited.
## State as of {last}
What is in progress, paused, closed, and the open questions, with [[slug]].

Rules: only facts present in the pages, nothing invented; absolute dates; a more recent fact wins over an older one, and you say so ("replaced on …"); no em dash; 600 to 1,200 words."""


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


def q(s):
    return "'" + s.replace("'", "''") + "'"


def pages_for(slugs, order_by_input=True):
    rows = psql_json(
        "select coalesce(json_agg(x), '[]') from (select slug, title, type, "
        "to_char(coalesce(effective_date, created_at), 'YYYY-MM-DD') as date, compiled_truth as text "
        f"from pages where deleted_at is null and slug in ({','.join(q(s) for s in slugs)})) x")
    by = {r['slug']: r for r in rows}
    return [by[s] for s in slugs if s in by] if order_by_input else rows


def slugify(s):
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', '-', s).strip('-')[:60]


def groups(only):
    out = []
    if only in ('', 'topic'):
        for c in json.load(open(os.path.join(HERE, 'data', 'clusters.json'), encoding='utf-8')):
            out.append({'kind': 'topic', 'key': c['id'], 'slugs': c['slugs'],
                        'label': f"groupe thématique {c['id']} (pages proches par leur sens, k-means sur les embeddings)"})
    if only in ('', 'person'):
        owner = f" and t.slug <> {q(ARGS.owner)}" if ARGS.owner else ''
        people = psql_json(
            "select coalesce(json_agg(x), '[]') from (select t.slug, count(*) n from links l join pages t on t.id = l.to_page_id "
            f"where t.slug like 'people/%'{owner} and t.deleted_at is null group by 1 order by 2 desc limit {ARGS.people}) x")
        for p in people:
            members = psql_json(
                "select coalesce(json_agg(slug), '[]') from (select distinct f.slug, coalesce(f.effective_date, f.created_at) d "
                "from links l join pages t on t.id = l.to_page_id join pages f on f.id = l.from_page_id "
                f"where t.slug = {q(p['slug'])} and f.deleted_at is null and f.slug not like 'themes/%' order by d desc) x")
            out.append({'kind': 'person', 'key': p['slug'].split('/', 1)[1], 'slugs': [p['slug']] + members,
                        'label': f"tout ce qui est lié à la personne {p['slug']} dans le graphe (sa page, puis les pages qui la citent, de la plus récente à la plus ancienne)"})
    if only in ('', 'month'):
        for m in psql_json("select coalesce(json_agg(distinct to_char(coalesce(effective_date, created_at), 'YYYY-MM')), '[]') from pages where deleted_at is null and slug not like 'themes/%'"):
            members = psql_json(
                "select coalesce(json_agg(slug order by d desc), '[]') from (select slug, coalesce(effective_date, created_at) d from pages "
                f"where deleted_at is null and slug not like 'themes/%' and to_char(coalesce(effective_date, created_at), 'YYYY-MM') = {q(m)}) x")
            if len(members) >= 10:
                out.append({'kind': 'month', 'key': m, 'slugs': members,
                            'label': f"toutes les pages datées du mois {m} (de la plus récente à la plus ancienne)"})
    return out


def generate(g, model, prompt_tpl):
    os.makedirs(OUT, exist_ok=True)
    pages = pages_for(g['slugs'])
    per = max(300, min(3000, BUDGET // max(1, len(pages))))
    # "@path" in a `claude -p` prompt attaches the file: the pages' @ are neutralised.
    block = '\n\n'.join(f"### [[{p['slug']}]] ({p['type']}, {p['date']}) {p['title']}\n{(p['text'] or '')[:per]}" for p in pages).replace('@', '(at)')
    last = max((p['date'] for p in pages), default='')
    t0 = time.time()
    for _ in range(3):
        proc = subprocess.run(
            ['claude', '-p', prompt_tpl.format(kind_label=g['label'], n=len(pages), pages=block, last=last), '--model', model,
             '--settings', json.dumps({'disableAllHooks': True}), '--tools', '', '--no-session-persistence',
             '--strict-mcp-config', '--mcp-config', '{"mcpServers": {}}'],
            capture_output=True, text=True, timeout=900)
        body = proc.stdout.strip()
        if body.startswith('#'):
            break
        time.sleep(20)
    else:
        return {**g, 'error': (proc.stdout + proc.stderr)[-300:]}
    title = body.splitlines()[0].lstrip('# ').strip()
    slug = 'themes/' + {'topic': 'sujet-' + slugify(title), 'person': 'personne-' + g['key'], 'month': 'mois-' + g['key']}[g['kind']]
    cited = sorted(set(re.findall(r'\[\[([^\]|#]+)', body)))
    fm = {'type': 'theme', 'title': title, 'theme_summary': True, 'theme_kind': g['kind'], 'theme_group': g['key'],
          'theme_members': [p['slug'] for p in pages], 'theme_built_from': '2026-10-21 copie gbrain_lab_router',
          'generated_by': f'{model} via claude -p (piste routeur gbrain 2.0)'}
    md = '---\n' + '\n'.join(f'{k}: {json.dumps(v, ensure_ascii=False)}' for k, v in fm.items()) + '\n---\n\n' + body + '\n'
    path = os.path.join(OUT, slug.split('/', 1)[1] + '.md')
    open(path, 'w', encoding='utf-8').write(md)
    return {**g, 'slug': slug, 'title': title, 'path': path, 'pages': len(pages), 'cited': len(cited),
            'cited_unknown': [c for c in cited if c not in set(g['slugs'])], 'seconds': round(time.time() - t0), 'words': len(body.split())}


def load():
    lab = os.path.join(HERE, 'lab.sh')
    known = set(psql_json("select coalesce(json_agg(slug), '[]') from pages where deleted_at is null"))
    for f in sorted(os.listdir(OUT)):
        if not f.endswith('.md'):
            continue
        slug = 'themes/' + f[:-3]
        md = open(os.path.join(OUT, f), encoding='utf-8').read()
        # A link to a page that does not exist (slug made up by the model) is removed, and counted.
        bad = sorted({m for m in re.findall(r'\[\[([^\]|#]+)\]\]', md) if m not in known})
        for b in bad:
            md = md.replace(f'[[{b}]]', '')
        proc = subprocess.run([lab, 'put', slug, '--content-origin', 'inferred', '--force'], input=md, capture_output=True, text=True)
        print(slug, 'ok' if proc.returncode == 0 else f'ÉCHEC {proc.stderr[-300:]}', f'liens inventés retirés : {bad}' if bad else '')


def main():
    global ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument('action', choices=['generate', 'load'])
    ap.add_argument('--only', default='')
    ap.add_argument('--keys', default='', help='comma-separated group keys to (re)build')
    ap.add_argument('--people', type=int, default=10)
    ap.add_argument('--owner', default=os.environ.get('ROUTER_THEMES_OWNER', ''),
                    help="the brain owner's person slug, left out of the person groupings (default: none)")
    ap.add_argument('--model', default='claude-sonnet-5-5')
    ap.add_argument('--jobs', type=int, default=3)
    ARGS = ap.parse_args()
    if ARGS.action == 'load':
        return load()
    gs = groups(ARGS.only)
    if ARGS.keys:
        gs = [g for g in gs if g['key'] in ARGS.keys.split(',')]
    prompt_tpl = load_prompt('ROUTER_THEMES_PROMPT_FILE', PROMPT)
    with concurrent.futures.ThreadPoolExecutor(max_workers=ARGS.jobs) as ex:
        res = list(ex.map(lambda g: generate(g, ARGS.model, prompt_tpl), gs))
    man_path = os.path.join(HERE, 'data', 'themes-manifest.json')
    prev = {}
    if os.path.exists(man_path):
        prev = {(r['kind'], r['key']): r for r in json.load(open(man_path, encoding='utf-8'))}
    for r in res:
        r = {k: v for k, v in r.items() if k not in ('slugs', 'label')}
        prev[(r['kind'], r['key'])] = r
        print(r.get('slug', r['key']), r.get('error', ''), r.get('pages'), r.get('cited'), r.get('cited_unknown', [])[:3], r.get('seconds'))
    json.dump(sorted(prev.values(), key=lambda r: (r['kind'], r['key'])), open(man_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


ARGS = None
if __name__ == '__main__':
    main()
