// Builds an index state on a lab database of the chunk track, from the Haiku cache.
//
//   bun run ops/eval/chunk/apply-variant.ts --db b --text ctx  [--fts on|off]
//   bun run ops/eval/chunk/apply-variant.ts --db a --check 20      (check: rebuilds 20 "title only" vectors)
//
// --text ctx   : (B) the context sentence
// --text q     : (C) the doc2query questions
// --text both  : (D) context + questions
// --fts on     : the added text also goes into the chunk's keyword vector (weight C, the chunk staying at B)
// --fts off    : original keyword vector (recomputed by the native trigger)
// --emb off    : leaves the vectors alone (to toggle the keyword vector only)
//
// The added text follows the native context format (src/core/embedding-context.ts):
//   <context>{title}\n{added text}\n</context>\n{chunk}
// It never goes into content_chunks.chunk_text: excerpts, Jev and display read the original chunk.
// Embeddings are cached (cache/emb-<text>.jsonl): a second run costs nothing.
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, appendFileSync } from 'node:fs';
import { connect, targetPages, openaiKey, CACHE_DIR } from './db.ts';
import { buildContextualPrefix, sanitizeTitle } from '../../../src/core/embedding-context.ts';

const args = new Map<string, string>();
for (let i = 2; i < process.argv.length; i += 2) args.set(process.argv[i].replace(/^--/, ''), process.argv[i + 1]);
const variant = args.get('db') ?? '';
if (!variant) throw new Error('--db requis');
const text = args.get('text') ?? 'ctx';
const fts = args.get('fts') ?? 'off';
const emb = args.get('emb') ?? 'on';
const check = Number(args.get('check') ?? 0);
const PROMPT_VERSION = 3;
const MODEL = 'text-embedding-3-large';
const DIMS = 1536;

const sql = connect(variant);
const db = (await sql`SELECT current_database() AS d`)[0].d as string;
if (!db.startsWith('gbrain_lab_chunk')) throw new Error(`refus : ${db}`);
console.error(`base ${db}`);

async function embedAll(inputs: string[]): Promise<number[][]> {
  const out: number[][] = [];
  for (let i = 0; i < inputs.length; i += 96) {
    const batch = inputs.slice(i, i + 96);
    for (let attempt = 0; ; attempt++) {
      const res = await fetch('https://api.openai.com/v1/embeddings', {
        method: 'POST',
        headers: { 'content-type': 'application/json', authorization: `Bearer ${openaiKey()}` },
        body: JSON.stringify({ model: MODEL, input: batch, dimensions: DIMS }),
      });
      if (res.ok) {
        const j = await res.json() as { data: { embedding: number[] }[]; usage: { total_tokens: number } };
        tokens += j.usage.total_tokens;
        out.push(...j.data.map(d => d.embedding));
        break;
      }
      if (attempt >= 5) throw new Error(`OpenAI ${res.status}: ${await res.text()}`);
      await new Promise(r => setTimeout(r, 2000 * (attempt + 1)));
    }
  }
  return out;
}
let tokens = 0;
const vec = (v: number[]) => `[${v.join(',')}]`;

if (check) {
  // Check: does the native "title only" format rebuild the stored vectors?
  const rows = await sql<{ id: number; chunk_text: string; title: string; emb: string }[]>`
    SELECT cc.id, cc.chunk_text, p.title, cc.embedding::text AS emb FROM content_chunks cc JOIN pages p ON p.id = cc.page_id
    WHERE p.contextual_retrieval_mode = 'title' AND cc.chunk_source <> 'fenced_code' ORDER BY cc.id LIMIT ${check}`;
  const got = await embedAll(rows.map(r => (buildContextualPrefix(r.title, null) ?? '') + r.chunk_text));
  const cos = rows.map((r, i) => {
    const a = JSON.parse(r.emb) as number[]; const b = got[i];
    let d = 0, na = 0, nb = 0; for (let k = 0; k < a.length; k++) { d += a[k] * b[k]; na += a[k] ** 2; nb += b[k] ** 2; }
    return d / Math.sqrt(na * nb);
  });
  console.log(`cosinus vecteur stocké / refait : min ${Math.min(...cos).toFixed(4)}, moyenne ${(cos.reduce((s, x) => s + x, 0) / cos.length).toFixed(4)}`);
  await sql.end(); process.exit(0);
}

// Haiku cache: (slug, chunk_index) -> {contexte, questions}
const items = new Map<string, { contexte: string; questions: string[] }>();
for (const line of readFileSync(`${CACHE_DIR}/context-haiku.jsonl`, 'utf8').split('\n')) {
  if (!line.trim()) continue;
  const r = JSON.parse(line);
  if (r.prompt_version !== PROMPT_VERSION) continue;
  for (const [i, v] of Object.entries(r.items ?? {})) items.set(`${r.slug}#${i}`, v as { contexte: string; questions: string[] });
}

// Deterministic filter: Haiku sometimes adds a sentence about what the passage does NOT contain
// ("Le passage ne précise pas ..."), which would inject off-topic terms. These sentences are dropped.
const NEGATIVE = /(^|\s)((le|ce) passage|il|elle)\s+ne\s+(précise|détaille|couvre|mentionne|nomme|traite|donne|cite|décrit|contient|indique)\b|\bsans (préciser|détailler|nommer)\b/i;
function dropNegative(s: string): string {
  const parts = s.split(/(?<=[.;])\s+/);
  const kept = parts.filter(p => !NEGATIVE.test(p));
  return (kept.length ? kept : parts).join(' ');
}
function addition(it: { contexte: string; questions: string[] }): string {
  const clean = (s: string) => s.replace(/<\/?context>/gi, '').replace(/\s+/g, ' ').trim();
  const ctx = dropNegative(clean(it.contexte));
  const qs = it.questions.map(clean).join(' ');
  if (text === 'ctx') return ctx;
  if (text === 'q') return qs;
  return `${ctx} ${qs}`;
}

const pages = await targetPages(sql);
type Row = { id: number; chunk_index: number; chunk_source: string; chunk_text: string };
const todo: { id: number; input: string; add: string }[] = [];
let missing = 0;
for (const p of pages) {
  const chunks = await sql<Row[]>`SELECT id, chunk_index, chunk_source, chunk_text FROM content_chunks WHERE page_id = ${p.page_id} ORDER BY chunk_index`;
  for (const c of chunks) {
    const it = items.get(`${p.slug}#${c.chunk_index}`);
    if (!it) { missing++; continue; }
    const add = addition(it);
    // Code blocks are never wrapped (native rule D20-T4); their keyword vector still gets the addition.
    const input = c.chunk_source === 'fenced_code' ? c.chunk_text : `<context>${sanitizeTitle(p.title)}\n${add}\n</context>\n${c.chunk_text}`;
    todo.push({ id: c.id, input, add });
  }
}
console.error(`${todo.length} chunks à traiter, ${missing} sans texte généré (laissés en titre seul)`);

if (emb === 'on') {
  const cacheFile = `${CACHE_DIR}/emb-${text}.jsonl`;
  const cache = new Map<string, number[]>();
  if (existsSync(cacheFile)) for (const l of readFileSync(cacheFile, 'utf8').split('\n')) if (l.trim()) { const r = JSON.parse(l); cache.set(r.h, r.v); }
  const hash = (s: string) => createHash('sha256').update(`${MODEL}:${DIMS}:${s}`).digest('hex');
  const need = todo.filter(t => !cache.has(hash(t.input)));
  console.error(`embeddings : ${todo.length - need.length} en cache, ${need.length} à calculer`);
  for (let i = 0; i < need.length; i += 480) {
    const part = need.slice(i, i + 480);
    const vs = await embedAll(part.map(t => t.input));
    appendFileSync(cacheFile, part.map((t, k) => JSON.stringify({ h: hash(t.input), v: vs[k] })).join('\n') + '\n');
    part.forEach((t, k) => cache.set(hash(t.input), vs[k]));
    console.error(`  ${Math.min(i + 480, need.length)}/${need.length}`);
  }
  for (const t of todo) await sql`UPDATE content_chunks SET embedding = ${vec(cache.get(hash(t.input))!)}::vector WHERE id = ${t.id}`;
  console.error(`vecteurs écrits ; tokens d'embedding facturés à ce passage : ${tokens}`);
}

if (fts === 'on') {
  for (const t of todo) {
    await sql`UPDATE content_chunks SET search_vector =
      setweight(to_tsvector('french', gbrain_fts_input(COALESCE(doc_comment, ''))), 'A') ||
      setweight(to_tsvector('french', gbrain_fts_input(COALESCE(symbol_name_qualified, ''))), 'A') ||
      setweight(to_tsvector('french', gbrain_fts_input(chunk_text)), 'B') ||
      setweight(to_tsvector('french', gbrain_fts_input(${t.add})), 'C')
      WHERE id = ${t.id}`;
  }
  console.error('plein texte : ajout écrit (poids C)');
} else if (fts === 'off') {
  for (const t of todo) await sql`UPDATE content_chunks SET chunk_text = chunk_text WHERE id = ${t.id}`;
  console.error("plein texte : remis à l'état natif");
}
await sql.end();
