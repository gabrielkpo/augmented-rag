#!/usr/bin/env python3
"""Converts the Haiku cache of the chunk lab (2026-10-21) to the `gbrain chunk-text import` format.

The cache (~/gbrain-labs/chunk/cache/context-haiku.jsonl) keeps the questions by (slug, chunk_index);
the chunk text they were generated for is in pages.jsonl (export of the database of 2026-10-21).
Each line written carries that chunk_text: gbrain recomputes its hash and ignores (and counts)
the chunks whose text has changed since. No call to Claude.

Usage: python3 -I ops/doc2query/convert-lab-cache.py [--cache DIR] [--out FILE] [--filter]
  --filter: also applies generate.py's deterministic filter to the questions (the lab applied it
            to the context sentences only; without the option, lab state C is reproduced as is).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate import clean, keep_question  # noqa: E402

LAB_PROMPT_VERSION = 3
LAB_MODEL = 'claude-haiku-5-5'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cache', default=os.path.expanduser('~/gbrain-labs/chunk/cache'))
    ap.add_argument('--out', default=os.path.expanduser('~/gbrain-labs/chunk/cache/d2q-import.jsonl'))
    ap.add_argument('--filter', action='store_true')
    args = ap.parse_args()

    texts = {}
    with open(os.path.join(args.cache, 'pages.jsonl'), encoding='utf-8') as f:
        for line in f:
            if line.strip():
                page = json.loads(line)
                for c in page['chunks']:
                    texts[(page['slug'], int(c['chunk_index']))] = c['chunk_text']

    items = {}
    with open(os.path.join(args.cache, 'context-haiku.jsonl'), encoding='utf-8') as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get('prompt_version') != LAB_PROMPT_VERSION:
                continue
            for i, v in (rec.get('items') or {}).items():
                items[(rec['slug'], int(i))] = v['questions']

    written = missing = 0
    with open(args.out, 'w', encoding='utf-8') as out:
        for (slug, idx), questions in sorted(items.items()):
            chunk_text = texts.get((slug, idx))
            if chunk_text is None:
                missing += 1
                continue
            qs = [clean(q) for q in questions]
            if args.filter:
                qs = [q for q in qs if keep_question(q)]
            if not qs:
                continue
            out.write(json.dumps({'source_id': 'default', 'slug': slug, 'chunk_index': idx, 'chunk_text': chunk_text,
                                  'questions': qs, 'model': LAB_MODEL, 'prompt_version': str(LAB_PROMPT_VERSION)}, ensure_ascii=False) + '\n')
            written += 1
    print(f'{written} ligne(s) -> {args.out} ; {missing} chunk(s) du cache sans texte dans pages.jsonl', file=sys.stderr)


if __name__ == '__main__':
    main()
