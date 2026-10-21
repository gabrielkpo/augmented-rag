#!/usr/bin/env python3
"""Judges the answers of an agentic bench against the expected answer.

The score by cited slug underestimates right answers: the brain often has
two pages that say the same thing. This judge (Claude Haiku through `claude -p`,
no tools) compares each answer with the qrel's expected answer and returns
juste / partiel / faux (right / partial / wrong).

Usage: python3 -I ops/eval/agentic/judge.py <results folder> [...] --qrels ops/eval/qrels-multihop.json
Writes the verdict in each JSON (field "juge") and a juge.md table per folder.
"""
import argparse
import concurrent.futures
import glob
import json
import os
import re
import subprocess

PROMPT = """Tu juges une réponse à une question sur une base de connaissances interne.

Question : {q}

Réponse attendue (référence vérifiée) : {ref}

Réponse à juger :
<<<
{ans}
>>>

Compare les faits. « juste » : la réponse contient l'essentiel de la référence sans la contredire. « partiel » : une partie de la référence seulement, ou une partie fausse. « faux » : l'essentiel manque ou est contredit. Des détails en plus ne pénalisent pas tant qu'ils ne contredisent pas la référence.
Réponds sur une seule ligne, en JSON : {{"verdict": "juste|partiel|faux", "raison": "<12 mots au plus>"}}"""
SCORE = {'juste': 1.0, 'partiel': 0.5, 'faux': 0.0}


def judge(path, ref, model):
    rec = json.load(open(path, encoding='utf-8'))
    if rec.get('juge', {}).get('verdict') in SCORE:
        return rec
    answer = re.sub(r'\n?Sources?\s*:.*$', '', rec.get('answer') or '', flags=re.S).strip()
    proc = subprocess.run(
        ['claude', '-p', PROMPT.format(q=rec['query'], ref=ref, ans=answer or '(pas de réponse)'),
         '--model', model, '--settings', json.dumps({'disableAllHooks': True}),
         '--tools', '', '--no-session-persistence'],
        capture_output=True, text=True, timeout=180, cwd=os.path.dirname(path))
    m = re.search(r'\{.*\}', proc.stdout, re.S)
    try:
        verdict = json.loads(m.group(0)) if m else {}
    except ValueError:
        verdict = {}
    if verdict.get('verdict') not in SCORE:
        verdict = {'verdict': 'erreur', 'raison': proc.stdout[-200:] or proc.stderr[-200:]}
    rec['juge'] = verdict
    json.dump(rec, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dirs', nargs='+')
    ap.add_argument('--qrels', required=True)
    ap.add_argument('--model', default='claude-haiku-5-5')
    ap.add_argument('--jobs', type=int, default=4)
    args = ap.parse_args()
    refs = {q['id']: q.get('answer', '') for q in json.load(open(args.qrels, encoding='utf-8'))['queries']}

    for d in args.dirs:
        files = [f for f in sorted(glob.glob(os.path.join(d, '*.json'))) if os.path.basename(f).rsplit('-', 1)[0] in refs]
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
            recs = list(ex.map(lambda f: judge(f, refs[os.path.basename(f).rsplit('-', 1)[0]], args.model), files))
        rows = ['| Variante | Origine | n | Juste | Partiel | Faux | Score | Citées | Recherches | Durée (s) |', '|---|---|---|---|---|---|---|---|---|---|']
        keys = sorted({(r['variant'], r['source']) for r in recs}) + sorted({(r['variant'], 'tout') for r in recs})
        for v, src in keys:
            rs = [r for r in recs if r['variant'] == v and (src == 'tout' or r['source'] == src)]
            vs = [r['juge']['verdict'] for r in rs]
            score = sum(SCORE.get(x, 0) for x in vs) / len(rs)
            rows.append(f"| {v} | {src} | {len(rs)} | {vs.count('juste')} | {vs.count('partiel')} | {vs.count('faux')} | {score:.2f} | "
                        f"{sum(r['cite'] for r in rs) / len(rs):.2f} | {sum(len(r['searches']) for r in rs) / len(rs):.1f} | "
                        f"{sum(r['duration_s'] for r in rs) / len(rs):.0f} |")
        open(os.path.join(d, 'juge.md'), 'w', encoding='utf-8').write('\n'.join(rows) + '\n')
        print(d)
        print('\n'.join(rows))


if __name__ == '__main__':
    main()
