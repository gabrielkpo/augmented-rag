#!/usr/bin/env python3
"""Re-parses failed extractions from their saved .log (no new model call), with extract.py's JSON repair.

Usage: python3 -I ops/eval/dated/salvage.py <extract dir>
"""
import glob
import importlib.util
import json
import os
import sys

spec = importlib.util.spec_from_file_location('extract', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'extract.py'))
extract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(extract)

for log in sorted(glob.glob(os.path.join(sys.argv[1], '*.log'))):
    out = log[:-4] + '.json'
    if os.path.exists(out):
        continue
    try:
        res = json.loads(open(log, encoding='utf-8').read().split('\n--- stderr')[0])
        data = extract.parse_result(res.get('result') or '')
    except (ValueError, AttributeError) as e:
        print(f'{os.path.basename(log)} : toujours illisible ({e})')
        continue
    data.update(cost_usd=res.get('total_cost_usd'), repaired=True)
    json.dump(data, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f"{os.path.basename(log)} : {len(data.get('facts') or [])} faits récupérés")
