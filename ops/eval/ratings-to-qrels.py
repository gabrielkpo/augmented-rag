#!/usr/bin/env python3
"""Turn rate_answer ratings into candidate bench questions.

gbrain records ratings (retrieval_feedback) but not the question text, so the
question comes from the Claude Code transcripts: a gbrain `search`/`query`
call, its result (which prints the answer_id), then the `rate_answer` call on
that answer_id. Pages rated 5 become the expected page (grade 3), pages rated
4 a secondary page (grade 1). Questions already in a bench file are skipped.

Usage: python3 -I ops/eval/ratings-to-qrels.py [--since AAAA-MM-JJ]
Writes ops/eval/qrels-ratings-candidates.json, to review before merging any
of it into a bench: a rating says a page helped, not that it was the only
right page.
"""
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
since = sys.argv[sys.argv.index('--since') + 1] if '--since' in sys.argv else ''
ANSWER_ID = re.compile(r'ans_[0-9A-Z]{20,}')


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ' '.join(text_of(c.get('text', '') if isinstance(c, dict) else c) for c in content)
    return ''


queries = {}   # answer_id -> query text
ratings = {}   # answer_id -> {slug: rating}
for path in glob.glob(os.path.expanduser('~/.claude/projects/**/*.jsonl'), recursive=True):
    pending = {}  # tool_use id -> query text
    try:
        lines = open(path, encoding='utf-8')
    except OSError:
        continue
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if since and str(row.get('timestamp', '')) < since:
            continue
        content = (row.get('message') or {}).get('content')
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'tool_use':
                name, args = block.get('name', ''), block.get('input') or {}
                if name in ('mcp__gbrain__search', 'mcp__gbrain__query') and isinstance(args.get('query'), str):
                    pending[block.get('id')] = args['query']
                elif name == 'mcp__gbrain__rate_answer' and args.get('answer_id'):
                    for page in args.get('pages') or []:
                        ref, rating = str(page.get('ref', '')), page.get('rating')
                        if isinstance(rating, (int, float)):
                            slug = ref.split(':', 1)[1] if ':' in ref else ref
                            ratings.setdefault(args['answer_id'], {})[slug] = int(rating)
            elif block.get('type') == 'tool_result' and block.get('tool_use_id') in pending:
                for answer_id in ANSWER_ID.findall(text_of(block.get('content'))):
                    queries[answer_id] = pending[block['tool_use_id']]

known = set()
for bench in glob.glob(os.path.join(HERE, 'qrels*.json')):
    if 'candidates' in bench:
        continue
    try:
        known |= {q['query'].strip().lower() for q in json.load(open(bench, encoding='utf-8'))['queries']}
    except (OSError, ValueError, KeyError):
        pass

out, seen = [], set()
for answer_id, pages in ratings.items():
    query = queries.get(answer_id)
    good = {s: r for s, r in pages.items() if r >= 4}
    if not query or not good or query.strip().lower() in known | seen:
        continue
    seen.add(query.strip().lower())
    out.append({
        'id': f'rating-{len(out) + 1:03d}',
        'query': query,
        'relevant': sorted(good, key=lambda s: -good[s]),
        'grades': {s: (3 if r == 5 else 1) for s, r in good.items()},
        'source': 'rating',
    })

target = os.path.join(HERE, 'qrels-ratings-candidates.json')
json.dump({'version': 1, 'queries': out}, open(target, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
print(f'{len(ratings)} réponses notées, {len(out)} nouvelles questions candidates -> {target}')
