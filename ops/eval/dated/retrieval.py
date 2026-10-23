#!/usr/bin/env python3
"""Retrieval-level check of the dated-facts layer on a LAB brain (no model call besides the query embedding).

Per question: `query` (limit 10, the facts arm on as by default) and `recall` with the question.
Recorded: page recall@10 of the expected pages, the fact rows `query` returned, the facts `recall`
returned, and a "dated hit": a returned fact taken from an expected page whose valid_from day
(dd/mm) appears in the expected answer.

Usage: python3 -I ops/eval/dated/retrieval.py --lab dated --src <code> --qrels <file> --tag <name>
       [--token-budget N] (passes token_budget to query, which the temporal fact reserve needs)
Output: ops/eval/dated/results/retrieval-<tag>.json
"""
import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcp_client import Lab  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def rows_of(body):
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for k in ('results', 'facts', 'rows'):
            if isinstance(body.get(k), list):
                return body[k]
    return []


def fact_page(f, prov_pages):
    """Expected page a fact row comes from, read from its provenance text."""
    text = json.dumps(f, ensure_ascii=False)
    return next((p for p in prov_pages if p in text), None)


def dated_hit(facts, q):
    for f in facts:
        text = json.dumps(f, ensure_ascii=False)
        if not any(p in text for p in q['relevant']):
            continue
        m = re.search(r'(20\d\d)-(\d\d)-(\d\d)', str(f.get('valid_from') or f.get('chunk_text') or text))
        if m and f'{m.group(3)}/{m.group(2)}' in q['answer']:
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lab', required=True)
    ap.add_argument('--src', required=True)
    ap.add_argument('--qrels', required=True)
    ap.add_argument('--tag', required=True)
    ap.add_argument('--token-budget', type=int, default=0)
    args = ap.parse_args()
    subprocess.run(['psql', '-X', '-q', '-d', f'gbrain_lab_{args.lab}', '-c', 'delete from query_cache'], check=True)
    qs = json.load(open(args.qrels, encoding='utf-8'))['queries']
    lab = Lab(args.lab, args.src)
    out = []
    for q in qs:
        params = {'query': q['query'], 'limit': 10}
        if args.token_budget:
            params['token_budget'] = args.token_budget
        body, err = lab.call('query', params)
        rows = rows_of(body)
        pages = [r.get('slug') for r in rows if r.get('result_type') != 'fact']
        qfacts = [r for r in rows if r.get('result_type') == 'fact']
        rbody, rerr = lab.call('recall', {'question': q['query'], 'limit': 5})
        rfacts = rows_of(rbody)
        rec = {
            'id': q['id'], 'source': q.get('source'), 'query': q['query'],
            'page_recall10': sum(s in pages for s in q['relevant']) / len(q['relevant']),
            'pages': pages, 'query_facts': qfacts, 'recall_facts': rfacts,
            'query_dated_hit': dated_hit(qfacts, q), 'recall_dated_hit': dated_hit(rfacts, q),
            'errors': [x for x in (body if err else None, rbody if rerr else None) if x],
        }
        out.append(rec)
        print(f"{q['id']:6} pages@10 {rec['page_recall10']:.2f} faits query {len(qfacts)} (daté {int(rec['query_dated_hit'])}) "
              f"recall {len(rfacts)} (daté {int(rec['recall_dated_hit'])})", flush=True)
    lab.close()
    os.makedirs(os.path.join(HERE, 'results'), exist_ok=True)
    json.dump(out, open(os.path.join(HERE, 'results', f'retrieval-{args.tag}.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    n = len(out)
    print(f"{args.tag} : pages@10 {sum(r['page_recall10'] for r in out) / n:.2f} · questions avec un fait dans query "
          f"{sum(bool(r['query_facts']) for r in out)}/{n} · fait daté juste dans query {sum(r['query_dated_hit'] for r in out)}/{n} · "
          f"dans recall {sum(r['recall_dated_hit'] for r in out)}/{n}")


if __name__ == '__main__':
    main()
