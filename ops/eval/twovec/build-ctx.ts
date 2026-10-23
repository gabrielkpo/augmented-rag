// Builds the second vector arm of the two-vector lab: "title + context sentence" vectors,
// stored in side columns of the lab database gbrain_lab_twovec (a copy of a native lab in doc2query state C).
//
//   bun run ops/eval/twovec/build-ctx.ts            (embeds what the cache lacks, writes both columns)
//
// embedding_ctx_g : every chunk. Chunks with a context sentence get <context>{title}\n{context}\n</context>\n{chunk};
//                   every other chunk gets its stored vector, which is title-only (no doc2query text) or raw code.
// embedding_ctx_h : the same vectors, only on chunks of pages split into 3 chunks or more; NULL elsewhere.
// The context sentence is the lab cache of 2026-10-21 (~/gbrain-labs/chunk/cache/context-haiku.jsonl), matched to a
// chunk by (slug, sha256 of the chunk text it was generated for): a chunk whose text changed since keeps its stored vector.
// No chat call. Embeddings are cached (~/gbrain-labs/twovec/cache/emb-ctx.jsonl).
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, appendFileSync } from 'node:fs';
import { homedir } from 'node:os';
import postgres from 'postgres';
import { sanitizeTitle } from '../../../src/core/embedding-context.ts';

const LAB = `${homedir()}/gbrain-labs/twovec`;
const CHUNK_CACHE = `${homedir()}/gbrain-labs/chunk/cache`;
const MODEL = 'text-embedding-3-large';
const DIMS = 1536;

const cfg = JSON.parse(readFileSync(`${LAB}/.gbrain/config.json`, 'utf8'));
const url: string = cfg.database_url;
if (!/\/gbrain_lab_twovec(\?|$)/.test(url)) throw new Error('refus : la config ne vise pas gbrain_lab_twovec');
const sql = postgres(url, { max: 2, onnotice: () => {} });
const db = (await sql`SELECT current_database() AS d`)[0].d as string;
if (db !== 'gbrain_lab_twovec') throw new Error(`refus : ${db}`);
const env = readFileSync(`${LAB}/.gbrain/.env`, 'utf8');
const key = (env.match(/^OPENAI_API_KEY=(.+)$/m)?.[1] ?? '').trim().replace(/^['"]|['"]$/g, '');
if (!key) throw new Error('OPENAI_API_KEY absente');

const sha = (s: string) => createHash('sha256').update(s, 'utf8').digest('hex');

// Same deterministic filter as the chunk lab (apply-variant.ts): drops "the passage does not ..." sentences.
const NEGATIVE = /(^|\s)((le|ce) passage|il|elle)\s+ne\s+(précise|détaille|couvre|mentionne|nomme|traite|donne|cite|décrit|contient|indique)\b|\bsans (préciser|détailler|nommer)\b/i;
const clean = (s: string) => s.replace(/<\/?context>/gi, '').replace(/\s+/g, ' ').trim();
function dropNegative(s: string): string {
  const parts = s.split(/(?<=[.;])\s+/);
  const kept = parts.filter(p => !NEGATIVE.test(p));
  return (kept.length ? kept : parts).join(' ');
}

// (slug, hash of the chunk text the sentence was generated for) -> context sentence
const texts = new Map<string, string>();
for (const line of readFileSync(`${CHUNK_CACHE}/pages.jsonl`, 'utf8').split('\n')) {
  if (!line.trim()) continue;
  const p = JSON.parse(line);
  for (const c of p.chunks) texts.set(`${p.slug}#${c.chunk_index}`, c.chunk_text);
}
const ctx = new Map<string, string>();
for (const line of readFileSync(`${CHUNK_CACHE}/context-haiku.jsonl`, 'utf8').split('\n')) {
  if (!line.trim()) continue;
  const r = JSON.parse(line);
  if (r.prompt_version !== 3) continue;
  for (const [i, v] of Object.entries(r.items ?? {})) {
    const t = texts.get(`${r.slug}#${i}`);
    if (t !== undefined) ctx.set(`${r.slug}\0${sha(t)}`, dropNegative(clean((v as { contexte: string }).contexte)));
  }
}

type Row = { id: number; slug: string; title: string; chunk_text: string; chunk_source: string; n: number };
const rows = await sql<Row[]>`
  SELECT cc.id, p.slug, p.title, cc.chunk_text, cc.chunk_source, c.n::int AS n
  FROM content_chunks cc JOIN pages p ON p.id = cc.page_id
  JOIN (SELECT page_id, count(*) AS n FROM content_chunks GROUP BY page_id) c ON c.page_id = cc.page_id
  WHERE p.deleted_at IS NULL AND p.source_id = 'default'`;
const todo: { id: number; input: string }[] = [];
for (const r of rows) {
  if (r.chunk_source === 'fenced_code' || r.chunk_source === 'image_asset') continue;
  const c = ctx.get(`${r.slug}\0${sha(r.chunk_text)}`);
  if (c) todo.push({ id: r.id, input: `<context>${sanitizeTitle(r.title)}\n${c}\n</context>\n${r.chunk_text}` });
}
console.error(`${rows.length} chunks, ${todo.length} avec phrase de contexte`);

let tokens = 0;
async function embedAll(inputs: string[]): Promise<number[][]> {
  const out: number[][] = [];
  for (let i = 0; i < inputs.length; i += 96) {
    const batch = inputs.slice(i, i + 96);
    for (let attempt = 0; ; attempt++) {
      const res = await fetch('https://api.openai.com/v1/embeddings', {
        method: 'POST',
        headers: { 'content-type': 'application/json', authorization: `Bearer ${key}` },
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

const cacheFile = `${LAB}/cache/emb-ctx.jsonl`;
const cache = new Map<string, number[]>();
if (existsSync(cacheFile)) for (const l of readFileSync(cacheFile, 'utf8').split('\n')) if (l.trim()) { const r = JSON.parse(l); cache.set(r.h, r.v); }
const h = (s: string) => sha(`${MODEL}:${DIMS}:${s}`);
const need = todo.filter(t => !cache.has(h(t.input)));
console.error(`embeddings : ${todo.length - need.length} en cache, ${need.length} à calculer`);
for (let i = 0; i < need.length; i += 480) {
  const part = need.slice(i, i + 480);
  const vs = await embedAll(part.map(t => t.input));
  appendFileSync(cacheFile, part.map((t, k) => JSON.stringify({ h: h(t.input), v: vs[k] })).join('\n') + '\n');
  part.forEach((t, k) => cache.set(h(t.input), vs[k]));
  console.error(`  ${Math.min(i + 480, need.length)}/${need.length}`);
}

await sql.unsafe(`ALTER TABLE content_chunks ADD COLUMN IF NOT EXISTS embedding_ctx_g vector(${DIMS}), ADD COLUMN IF NOT EXISTS embedding_ctx_h vector(${DIMS})`);
await sql`UPDATE content_chunks SET embedding_ctx_g = embedding, embedding_ctx_h = NULL`;
for (const t of todo) await sql`UPDATE content_chunks SET embedding_ctx_g = ${`[${cache.get(h(t.input))!.join(',')}]`}::vector WHERE id = ${t.id}`;
await sql`UPDATE content_chunks cc SET embedding_ctx_h = embedding_ctx_g
  WHERE (SELECT count(*) FROM content_chunks c2 WHERE c2.page_id = cc.page_id) >= 3`;
await sql.unsafe('CREATE INDEX IF NOT EXISTS idx_chunks_embedding_ctx_g ON content_chunks USING hnsw (embedding_ctx_g vector_cosine_ops)');
await sql.unsafe('CREATE INDEX IF NOT EXISTS idx_chunks_embedding_ctx_h ON content_chunks USING hnsw (embedding_ctx_h vector_cosine_ops) WHERE embedding_ctx_h IS NOT NULL');
const [s] = await sql`SELECT count(embedding_ctx_g)::int AS g, count(embedding_ctx_h)::int AS hh,
  count(*) FILTER (WHERE embedding_ctx_g IS DISTINCT FROM embedding)::int AS differ FROM content_chunks`;
console.error(`écrit : ctx_g ${s.g}, ctx_h ${s.hh}, vecteurs différents du bras doc2query ${s.differ} ; tokens facturés à ce passage : ${tokens}`);
await sql.end();
