#!/usr/bin/env python3
"""Re-assigns the subject entity of facts extracted from meetings and Slack threads.

The extraction often kept the meeting page itself as the fact's entity, so `recall` by project
or system misses those facts and supersession across meetings is impossible. One `claude -p`
call (Sonnet, no tools) per batch of facts receives the lab's entity pages (project, system,
process, concept, partner, person: slug and title) and returns the subject slug of each fact.

Usage: python3 -I ops/eval/dated/reentity.py --lab dated [--batch 80] [--max-calls 10]
Output: ops/eval/dated/results/work/reentity.json ({fact gid: entity slug}), read by link.py.
"""
import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, 'results', 'work')
sys.path.insert(0, HERE)
from link import load_extractions  # noqa: E402

PROMPT = """Chaque fait ci-dessous vient d'une réunion ou d'un fil Slack. Donne pour chacun le slug de la page dont il parle (son sujet : le projet, le système, le processus, le partenaire ou la personne qui change), choisi dans la liste des pages. Si aucun ne convient, réponds `null`.

Pages (slug | titre) :
{pages}

Faits (id | réunion | fait) :
{facts}

Réponds uniquement en JSON : {{"<id>": "<slug ou null>", ...}}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lab', required=True)
    ap.add_argument('--model', default='claude-sonnet-5-5')
    ap.add_argument('--batch', type=int, default=80)
    ap.add_argument('--max-calls', type=int, default=10)
    args = ap.parse_args()
    pages = subprocess.run(['psql', '-X', '-At', '-F', ' | ', '-d', f'gbrain_lab_{args.lab}', '-c',
                            "select slug, title from pages where deleted_at is null and type in "
                            "('project','system','process','concept','partner','person') order by slug"],
                           capture_output=True, text=True, check=True).stdout.strip()
    valid = {l.split(' | ')[0] for l in pages.splitlines()}
    path = os.path.join(WORK, 'reentity.json')
    done = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else {}
    todo = [f for f in load_extractions(apply_reentity=False)
            if f['page'].startswith(('meeting', 'slack')) and f['entity'] not in valid and f['id'] not in done]
    calls = 0
    for i in range(0, len(todo), args.batch):
        if calls >= args.max_calls:
            break
        batch = todo[i:i + args.batch]
        rows = '\n'.join(f"{f['id']} | {f['page']} | {f['claim']}" for f in batch)
        proc = subprocess.run(['claude', '-p', PROMPT.format(pages=pages, facts=rows), '--model', args.model,
                               '--settings', json.dumps({'disableAllHooks': True}), '--tools', '',
                               '--no-session-persistence', '--output-format', 'json'],
                              capture_output=True, text=True, timeout=900, cwd=WORK)
        calls += 1
        try:
            out = json.loads(re.search(r'\{.*\}', json.loads(proc.stdout).get('result') or '', re.S).group(0))
        except (ValueError, AttributeError):
            print(f'lot {i // args.batch} : réponse illisible', flush=True)
            continue
        for f in batch:
            slug = out.get(f['id'])
            done[f['id']] = slug if slug in valid else None
        json.dump(done, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print(f"lot {i // args.batch} : {sum(1 for f in batch if done.get(f['id']))}/{len(batch)} rattachés", flush=True)
    print(f'appels claude : {calls}', file=sys.stderr)


if __name__ == '__main__':
    main()
