/**
 * Accuracy and latency of the router forms, on the labelled set router-labels.json.
 *
 *   rules   word rules (src/core/search/router/theme-router.ts)
 *   intent  gbrain's native intent classifier (classifyQuery): "overview" when the
 *           intent is temporal, or general with a "strong" recency hint (the native
 *           classifier has no overview class: this measures how close it gets)
 *   haiku   Claude Haiku through `claude -p` (subscription), no cache
 *
 * Usage: bun ops/eval/router/eval_router.ts [--forms rules,intent,haiku] [--jobs 4]
 * Writes ops/eval/router/data/router-eval.json (verdict per query and per form).
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { classifyQuery } from '../../../src/core/search/query-intent.ts';
import { isOverviewByHaiku, isOverviewByRules } from '../../../src/core/search/router/theme-router.ts';

type Row = { query: string; overview: boolean; origin: string };
const here = import.meta.dir;
const rows: Row[] = JSON.parse(readFileSync(join(here, 'router-labels.json'), 'utf-8'));
const argv = process.argv.slice(2);
const opt = (name: string, def: string) => { const i = argv.indexOf(`--${name}`); return i >= 0 ? argv[i + 1]! : def; };
const forms = opt('forms', 'rules,intent,haiku').split(',');
const jobs = Number(opt('jobs', '4'));

const intentOverview = (q: string) => {
  const s = classifyQuery(q);
  return s.intent === 'temporal' || (s.intent === 'general' && s.suggestedRecency === 'strong');
};

// No disk cache for the measurement: GBRAIN_HOME unset.
delete process.env.GBRAIN_HOME;

async function runForm(form: string): Promise<{ pred: boolean[]; ms: number[] }> {
  const pred: boolean[] = new Array(rows.length);
  const ms: number[] = new Array(rows.length);
  let next = 0;
  const worker = async () => {
    while (next < rows.length) {
      const i = next++;
      const t = performance.now();
      pred[i] = form === 'rules' ? isOverviewByRules(rows[i]!.query)
        : form === 'intent' ? intentOverview(rows[i]!.query)
          : await isOverviewByHaiku(rows[i]!.query);
      ms[i] = performance.now() - t;
    }
  };
  await Promise.all(Array.from({ length: form === 'haiku' ? jobs : 1 }, worker));
  return { pred, ms };
}

const out: Record<string, unknown> = {};
const lines = ['| Forme | Justesse | Précision (vue) | Rappel (vue) | Faux positifs | Faux négatifs | Latence médiane | Latence p90 |', '|---|---|---|---|---|---|---|---|'];
for (const form of forms) {
  const { pred, ms } = await runForm(form);
  let tp = 0, fp = 0, fn = 0, tn = 0;
  rows.forEach((r, i) => { if (pred[i] && r.overview) tp++; else if (pred[i]) fp++; else if (r.overview) fn++; else tn++; });
  const sorted = [...ms].sort((a, b) => a - b);
  const q = (p: number) => sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))]!;
  const fmt = (v: number) => (v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${v.toFixed(2)} ms`);
  lines.push(`| ${form} | ${((tp + tn) / rows.length).toFixed(3)} | ${(tp / Math.max(1, tp + fp)).toFixed(2)} | ${(tp / Math.max(1, tp + fn)).toFixed(2)} | ${fp} | ${fn} | ${fmt(q(0.5))} | ${fmt(q(0.9))} |`);
  out[form] = rows.map((r, i) => ({ ...r, pred: pred[i], ms: Math.round(ms[i]! * 100) / 100 }));
  const errs = rows.map((r, i) => ({ r, p: pred[i] })).filter(x => x.p !== x.r.overview);
  console.log(`\n${form} : erreurs`);
  for (const e of errs) console.log(`  ${e.r.overview ? 'FN' : 'FP'} [${e.r.origin}] ${e.r.query.slice(0, 110)}`);
}
const pos = rows.filter(r => r.overview).length;
console.log(`\n${rows.length} requêtes, ${pos} vue d'ensemble\n` + lines.join('\n'));
writeFileSync(join(here, 'data', 'router-eval.json'), JSON.stringify({ table: lines, results: out }, null, 1));
