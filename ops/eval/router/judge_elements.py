#!/usr/bin/env python3
"""Per-element judge for the overview bench (complements ops/eval/agentic/judge.py).

The global judge (juste / partiel / faux, i.e. right / partial / wrong) is very unstable
on list answers: the same complete answer flips from "juste" to "faux" from one round to
the next. Here, Claude Haiku says, expected element by expected element, whether the answer
covers it. Score of an answer = share of the elements covered. Contradicted elements are
counted separately.

Usage: python3 -I ops/eval/router/judge_elements.py <results folder> [...] \
         [--qrels ops/eval/router/qrels-overview.json] [--model claude-haiku-5-5] [--key juge_elements]
Writes the <key> field in each JSON and a <key>.md table per folder.
"""
import argparse
import concurrent.futures
import glob
import json
import os
import re
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPT = """Tu vérifies une réponse à une question de vue d'ensemble sur une base de connaissances interne.

Question : {q}

Éléments attendus (grille de correction) :
{elements}

Réponse à vérifier :
<<<
{ans}
>>>

Pour chaque élément, dis si la réponse le COUVRE (elle en donne l'idée principale, même avec d'autres mots ou moins de détails), si elle le CONTREDIT, ou s'il est ABSENT.
Réponds uniquement en JSON sur une ligne : {{"couverts": [<numéros>], "contredits": [<numéros>]}}"""


def judge(path, q, model, key):
    rec = json.load(open(path, encoding='utf-8'))
    if isinstance(rec.get(key), dict) and 'score' in rec[key]:
        return rec
    answer = re.sub(r'\n?Sources?\s*:.*$', '', rec.get('answer') or '', flags=re.S).strip()
    els = q['elements']
    listing = '\n'.join(f"{i + 1}. {e['element']}" for i, e in enumerate(els))
    verdict = None
    for _ in range(3):
        proc = subprocess.run(
            ['claude', '-p', PROMPT.format(q=q['query'], elements=listing, ans=answer or '(pas de réponse)'),
             '--model', model, '--settings', json.dumps({'disableAllHooks': True}), '--tools', '',
             '--no-session-persistence', '--strict-mcp-config', '--mcp-config', '{"mcpServers": {}}'],
            capture_output=True, text=True, timeout=180, cwd=os.path.dirname(path))
        m = re.search(r'\{.*\}', proc.stdout, re.S)
        try:
            v = json.loads(m.group(0)) if m else None
        except ValueError:
            v = None
        if isinstance(v, dict) and isinstance(v.get('couverts'), list):
            verdict = v
            break
    if verdict is None:
        rec[key] = {'erreur': (proc.stdout + proc.stderr)[-200:]}
    else:
        cov = sorted({int(i) for i in verdict['couverts'] if str(i).isdigit() and 1 <= int(i) <= len(els)})
        con = sorted({int(i) for i in verdict.get('contredits') or [] if str(i).isdigit() and 1 <= int(i) <= len(els)})
        rec[key] = {'couverts': cov, 'contredits': con, 'n': len(els), 'score': len(cov) / len(els)}
    json.dump(rec, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dirs', nargs='+')
    ap.add_argument('--qrels', default=os.path.join(HERE, 'qrels-overview.json'))
    ap.add_argument('--model', default='claude-haiku-5-5')
    ap.add_argument('--key', default='juge_elements')
    ap.add_argument('--jobs', type=int, default=4)
    args = ap.parse_args()
    qs = {q['id']: q for q in json.load(open(args.qrels, encoding='utf-8'))['queries']}
    for d in args.dirs:
        files = [f for f in sorted(glob.glob(os.path.join(d, '*.json'))) if os.path.basename(f).rsplit('-', 1)[0] in qs]
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
            recs = list(ex.map(lambda f: judge(f, qs[os.path.basename(f).rsplit('-', 1)[0]], args.model, args.key), files))
        rows = ['| Origine | n | Éléments couverts | Contredits (par réponse) | Erreurs |', '|---|---|---|---|---|']
        for src in sorted({r['source'] for r in recs}) + ['tout']:
            rs = [r for r in recs if src == 'tout' or r['source'] == src]
            ok = [r[args.key] for r in rs if 'score' in r[args.key]]
            rows.append(f"| {src} | {len(rs)} | {sum(o['score'] for o in ok) / max(1, len(ok)):.2f} | "
                        f"{sum(len(o['contredits']) for o in ok) / max(1, len(ok)):.2f} | {len(rs) - len(ok)} |")
        open(os.path.join(d, f'{args.key}.md'), 'w', encoding='utf-8').write('\n'.join(rows) + '\n')
        print(d)
        print('\n'.join(rows))


if __name__ == '__main__':
    main()
