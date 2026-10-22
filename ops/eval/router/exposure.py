#!/usr/bin/env python3
"""Exposure to the summaries: for each question of a bench, how many themes/ pages come out
in the top 5 of `search` (lab brain router2, through ops/eval/router/lab.sh).

Usage: GBRAIN_THEME_ROUTER=off|route python3 -I ops/eval/router/exposure.py <qrels.json> [...] --out <fichier.json>
"""
import argparse, concurrent.futures, json, os, re, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))


def top5(q):
    p = subprocess.run([os.path.join(HERE, 'lab.sh'), 'search', q, '--limit', '5', '--json'], capture_output=True, text=True, timeout=300)
    out = p.stdout[min([i for i in (p.stdout.find('['), p.stdout.find('{')) if i >= 0] or [0]):]
    try:
        res = json.loads(out) if out else []
    except ValueError:
        res = []
    if isinstance(res, dict):
        res = res.get('results', [])
    return [r.get('slug', '') for r in res][:5]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('qrels', nargs='+')
    ap.add_argument('--out', required=True)
    ap.add_argument('--jobs', type=int, default=3)
    a = ap.parse_args()
    qs = [(os.path.basename(f), q) for f in a.qrels for q in json.load(open(f, encoding='utf-8'))['queries']]
    with concurrent.futures.ThreadPoolExecutor(a.jobs) as ex:
        tops = list(ex.map(lambda x: top5(x[1]['query']), qs))
    rows = [{'bench': b, 'id': q['id'], 'query': q['query'], 'top5': t, 'themes': sum(s.startswith('themes/') for s in t),
             'empty': not t} for (b, q), t in zip(qs, tops)]
    json.dump(rows, open(a.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    for b in sorted({r['bench'] for r in rows}):
        rs = [r for r in rows if r['bench'] == b]
        print(f"{b}: {len(rs)} questions, {sum(r['themes'] > 0 for r in rs)} avec au moins un résumé dans le top 5, "
              f"{sum(bool(r['top5']) and r['top5'][0].startswith('themes/') for r in rs)} avec un résumé en 1er, {sum(r['empty'] for r in rs)} sans résultat lu")


if __name__ == '__main__':
    main()
