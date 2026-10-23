/**
 * doc2query chunk index texts (src/core/chunk-index-text.ts).
 *
 * Protects: (1) with the setting off, embedding inputs, provenance hashes and
 * keyword vectors are byte-identical to a brain without the feature; (2) with
 * it on, every plain embed path wraps `<context>{title}\n{text}\n</context>\n`
 * (uncapped, fenced code never), the provenance hash covers the text, and the
 * keyword vector carries the text at weight C; (3) `embed --stale` keeps the
 * text; (4) a chunk whose text changes loses its index text until the next
 * generation. A regression in any embed path or in the hash would silently
 * erase (or never install) the generated questions on the next re-embed.
 */
import { describe, test, expect, beforeAll, afterAll, beforeEach } from 'bun:test';
import { PGLiteEngine } from '../src/core/pglite-engine.ts';
import { resetPgliteState } from './helpers/reset-pglite.ts';
import { installFixtureChunks } from './helpers/page-projection.ts';
import {
  CHUNK_INDEX_TEXT_CONFIG_KEY, chunkTextHash, loadPageChunkIndexTexts, type ChunkIndexTexts,
} from '../src/core/chunk-index-text.ts';
import { buildContextualPrefix, wrapChunkForEmbedding, wrapChunkTextsForStoredMode } from '../src/core/embedding-context.ts';
import { acceptedEmbeddingInputHashes, embeddingInputHash, type EmbeddingInputContext } from '../src/core/embedding-input-hash.ts';
import { embeddingInputContext, embeddingWriteTarget, readProjectionSnapshot } from '../src/core/page-state/projections.ts';
import { emptyRefreshResult, refreshPageChunkIndex } from '../src/core/chunk-index-text-refresh.ts';
import { importChunkIndexTexts } from '../src/commands/chunk-text.ts';
import { embedStalePages } from '../src/core/embed-stale.ts';
import type { ChunkInput } from '../src/core/types.ts';
import { configureGateway, resetGateway, __setEmbedTransportForTests } from '../src/core/ai/gateway.ts';
import { importFromContent } from '../src/core/import-file.ts';

const TITLE = 'Projet Atlas';
const SLUG = 'projects/atlas';
const CHUNKS = [
  'Le projet démarre en mars avec trois personnes.',
  'La facturation passe par le prestataire Zephyr chaque trimestre.',
  '```sql\nselect 1;\n```',
  'Le comité valide les budgets le premier lundi du mois.',
];
const QUESTIONS = 'Quel prestataire facture le projet ? Quelle est la fréquence de facturation ? Qui envoie les factures ? '
  + 'Comment le paiement est-il organisé ? Combien de fois par an la facture arrive-t-elle ? Quelle entreprise gère la facturation récurrente ? '
  + 'À quel rythme le fournisseur externe envoie-t-il ses relevés au projet ?';

const CTX: EmbeddingInputContext = { column: 'embedding', model: 'openai:text-embedding-3-large', dimensions: 1536, title: TITLE,
  corpusGeneration: null, bodyHash: 'b' };

describe('embedding inputs and provenance (pure)', () => {
  const chunk = { chunk_text: CHUNKS[1]!, chunk_source: 'compiled_truth' };
  const fenced = { chunk_text: CHUNKS[2]!, chunk_source: 'fenced_code' };
  const texts: ChunkIndexTexts = new Map([[chunkTextHash(chunk.chunk_text), QUESTIONS], [chunkTextHash(fenced.chunk_text), QUESTIONS]]);

  test('no texts: the title-tier input and hash are the pre-feature ones', () => {
    const legacy = wrapChunkForEmbedding(chunk.chunk_text, buildContextualPrefix(TITLE, null), chunk.chunk_source);
    expect(wrapChunkTextsForStoredMode({ title: TITLE, contextual_retrieval_mode: 'title' }, [chunk], new Map())).toEqual([legacy]);
    expect(wrapChunkTextsForStoredMode({ title: TITLE, contextual_retrieval_mode: 'none' }, [chunk])).toEqual([chunk.chunk_text]);
    expect(embeddingInputHash({ ...CTX, indexTexts: new Map() }, 'title', chunk)).toBe(embeddingInputHash(CTX, 'title', chunk));
  });

  test('a text joins the context block whole (not cut at 300 characters); fenced code stays raw', () => {
    expect(QUESTIONS.length).toBeGreaterThan(300);
    const [input, code] = wrapChunkTextsForStoredMode({ title: TITLE, contextual_retrieval_mode: 'title' }, [chunk, fenced], texts);
    expect(input).toBe(`<context>${TITLE}\n${QUESTIONS}\n</context>\n${chunk.chunk_text}`);
    expect(code).toBe(fenced.chunk_text);
    const [none] = wrapChunkTextsForStoredMode({ title: TITLE, contextual_retrieval_mode: 'none' }, [chunk], texts);
    expect(none).toBe(`<context>\n${QUESTIONS}\n</context>\n${chunk.chunk_text}`);
  });

  test('the provenance hash covers the text in every tier, never for fenced code', () => {
    const on = { ...CTX, indexTexts: texts };
    for (const tier of ['none', 'title', 'per_chunk_synopsis'] as const) {
      expect(embeddingInputHash(on, tier, chunk)).not.toBe(embeddingInputHash(CTX, tier, chunk));
    }
    expect(embeddingInputHash(on, 'title', fenced)).toBe(embeddingInputHash(CTX, 'title', fenced));
    // A vector built without the text is not current once the text exists, and the reverse.
    expect(acceptedEmbeddingInputHashes(on, 'title', chunk)).not.toContain(embeddingInputHash(CTX, 'title', chunk));
    expect(acceptedEmbeddingInputHashes(CTX, 'title', chunk)).not.toContain(embeddingInputHash(on, 'title', chunk));
  });
});

let engine: PGLiteEngine;

beforeAll(async () => {
  engine = new PGLiteEngine();
  await engine.connect({});
  await engine.initSchema();
}, 60_000);

afterAll(async () => {
  await engine.disconnect();
});

beforeEach(async () => {
  await resetPgliteState(engine);
});

const fakeEmbed = (seen: string[][]) => async (texts: string[]) => {
  seen.push(texts);
  return texts.map(t => { const v = new Float32Array(1536); v[0] = t.length; v[1] = 1; return v; });
};

async function seed(): Promise<void> {
  await engine.putPage(SLUG, { type: 'project', title: TITLE, compiled_truth: CHUNKS.join('\n\n') });
  const chunks: ChunkInput[] = CHUNKS.map((text, i) => ({ chunk_index: i, chunk_text: text, chunk_source: i === 2 ? 'fenced_code' : 'compiled_truth',
    token_count: 4, embedding: (() => { const v = new Float32Array(1536); v[0] = 1; return v; })() }));
  await installFixtureChunks(engine, SLUG, chunks);
  await engine.executeRaw(`UPDATE pages SET contextual_retrieval_mode = 'title' WHERE slug = $1`, [SLUG]);
}

const line = (chunkText: string, extra: Record<string, unknown> = {}) =>
  JSON.stringify({ slug: SLUG, chunk_text: chunkText, questions: [QUESTIONS], model: 'claude-haiku-5-5', prompt_version: 3, ...extra });

async function keyword(term: string, weights = '{a,b,c}'): Promise<number[]> {
  const rows = await engine.executeRaw<{ chunk_index: number }>(`SELECT cc.chunk_index FROM content_chunks cc JOIN pages p ON p.id = cc.page_id
    WHERE p.slug = $1 AND ts_filter(cc.search_vector, $3::"char"[]) @@ to_tsquery('english', $2) ORDER BY cc.chunk_index`, [SLUG, term, weights]);
  return rows.map(r => Number(r.chunk_index));
}

describe('stored texts with the setting off', () => {
  test('nothing reads them: no snapshot texts, keyword vectors unchanged, import stores without re-embedding', async () => {
    await seed();
    const before = await engine.executeRaw<{ v: string }>(`SELECT search_vector::text AS v FROM content_chunks ORDER BY chunk_index`);
    const result = await importChunkIndexTexts(engine, [line(CHUNKS[1]!), line(CHUNKS[2]!), line('a chunk that no longer exists')].join('\n'));
    expect(result).toMatchObject({ lines: 3, written: 1, unmatched: 2, setting: null });
    expect(result.refresh).toBeUndefined();
    expect((await readProjectionSnapshot(engine, SLUG, 'default'))!.indexTexts).toBeUndefined();
    expect(await loadPageChunkIndexTexts(engine, 'default', SLUG)).toEqual(new Map());
    await engine.executeRaw(`UPDATE content_chunks SET chunk_text = chunk_text`);
    const after = await engine.executeRaw<{ v: string }>(`SELECT search_vector::text AS v FROM content_chunks ORDER BY chunk_index`);
    expect(after).toEqual(before);
    expect(await keyword('paiement')).toEqual([]);
  });
});

describe('stored texts with the setting on', () => {
  test('import re-embeds only the enriched chunk with its questions and adds them to its keyword vector at weight C', async () => {
    await seed();
    await engine.setConfig(CHUNK_INDEX_TEXT_CONFIG_KEY, 'd2q');
    const seen: string[][] = [];
    const result = await importChunkIndexTexts(engine, line(CHUNKS[1]!), { refresh: false });
    expect(result.written).toBe(1);
    const refresh = emptyRefreshResult();
    await refreshPageChunkIndex(engine, 'default', SLUG, refresh, { embed: fakeEmbed(seen) });
    expect(refresh.failures).toEqual([]);
    expect(refresh.reembedded).toBe(1);
    const input = `<context>${TITLE}\n${QUESTIONS}\n</context>\n${CHUNKS[1]}`;
    expect(seen).toEqual([[input]]);
    const [row] = await engine.executeRaw<{ h: string }>(`SELECT embedding_input_hash AS h FROM content_chunks cc JOIN pages p ON p.id = cc.page_id
      WHERE p.slug = $1 AND cc.chunk_index = 1`, [SLUG]);
    const prepared = (await readProjectionSnapshot(engine, SLUG, 'default'))!;
    const provenance = embeddingInputContext(await embeddingWriteTarget(engine), TITLE, null, prepared.chunks, prepared.indexTexts);
    expect(row!.h).toBe(embeddingInputHash(provenance, 'title', { chunk_text: CHUNKS[1]!, chunk_source: 'compiled_truth' }));
    // The question words reach the keyword index, at weight C only; the chunk text keeps weight B.
    expect(await keyword('paiement & entreprise')).toEqual([1]);
    expect(await keyword('paiement', '{c}')).toEqual([1]);
    expect(await keyword('paiement', '{b}')).toEqual([]);
    expect(await keyword('Zephyr', '{b}')).toEqual([1]);
    // A second refresh finds nothing to redo.
    const again = emptyRefreshResult();
    await refreshPageChunkIndex(engine, 'default', SLUG, again, { embed: fakeEmbed(seen) });
    expect(again.reembedded).toBe(0);
    expect(seen).toHaveLength(1);
  });

  test('embed --stale keeps the text; a rewritten chunk loses it and shows up as pending', async () => {
    await seed();
    await engine.setConfig(CHUNK_INDEX_TEXT_CONFIG_KEY, 'd2q');
    await importChunkIndexTexts(engine, line(CHUNKS[1]!), { refresh: false });
    await engine.executeRaw(`UPDATE content_chunks SET embedding = NULL, embedded_at = NULL, embedding_input_hash = NULL WHERE chunk_index IN (0, 1)`);
    const seen: string[][] = [];
    const stale = await embedStalePages(engine, [SLUG], 'default', { embedFn: fakeEmbed(seen) as never });
    expect(stale.embedded).toBe(2);
    expect(seen.flat()).toEqual([`<context>${TITLE}\n</context>\n${CHUNKS[0]}`, `<context>${TITLE}\n${QUESTIONS}\n</context>\n${CHUNKS[1]}`]);
    expect((await engine.listChunkIndexTextPending({ kind: 'd2q', minChunks: 3 })).filter(r => r.eligible && !r.has_text).map(r => r.chunk_index)).toEqual([0, 3]);

    // The chunk is rewritten: its old text no longer applies, to the embedding or to the keyword vector.
    await installFixtureChunks(engine, SLUG, CHUNKS.map((text, i) => ({ chunk_index: i, chunk_text: i === 1 ? `${text} Révisé.` : text,
      chunk_source: i === 2 ? 'fenced_code' : 'compiled_truth', token_count: 4 } as ChunkInput)));
    expect(await keyword('paiement')).toEqual([]);
    expect((await engine.listChunkIndexTextPending({ kind: 'd2q', minChunks: 3 })).filter(r => r.eligible && !r.has_text).map(r => r.chunk_index)).toEqual([0, 1, 3]);
    expect(await engine.pruneChunkIndexTexts({ kind: 'd2q' })).toBe(1);
  });

  test('turning the setting off and refreshing returns the vector and keyword index to the plain input', async () => {
    await seed();
    await engine.setConfig(CHUNK_INDEX_TEXT_CONFIG_KEY, 'd2q');
    const seen: string[][] = [];
    await importChunkIndexTexts(engine, line(CHUNKS[1]!), { refresh: false });
    await refreshPageChunkIndex(engine, 'default', SLUG, emptyRefreshResult(), { embed: fakeEmbed(seen) });
    await engine.unsetConfig(CHUNK_INDEX_TEXT_CONFIG_KEY);
    const off = emptyRefreshResult();
    await refreshPageChunkIndex(engine, 'default', SLUG, off, { embed: fakeEmbed(seen) });
    expect(off.reembedded).toBe(1);
    expect(seen[1]).toEqual([`<context>${TITLE}\n</context>\n${CHUNKS[1]}`]);
    expect(await keyword('paiement')).toEqual([]);
  });

  test('SQL and TypeScript agree on the chunk hash (UTF-8, accents)', async () => {
    const [row] = await engine.executeRaw<{ h: string }>(`SELECT encode(sha256(convert_to($1::text, 'UTF8')), 'hex') AS h`, [CHUNKS[3]]);
    expect(row!.h).toBe(chunkTextHash(CHUNKS[3]!));
  });
});

describe('sync/import path with the setting on', () => {
  const embedded: string[] = [];
  beforeEach(() => {
    embedded.length = 0;
    configureGateway({ embedding_model: 'openai:text-embedding-3-large', embedding_dimensions: 1536, env: { OPENAI_API_KEY: 'sk-test' } });
    __setEmbedTransportForTests((async ({ values }: { values: string[] }) => {
      embedded.push(...values);
      return { embeddings: values.map((_, i) => { const v = new Array(1536).fill(0); v[i % 1536] = 1; return v; }), usage: { tokens: values.length } };
    }) as never);
  });
  afterAll(() => { __setEmbedTransportForTests(null); resetGateway(); });

  // Five sections; only the body of the last one changes, its header stays, so the enriched chunk keeps its text.
  const section = (word: string, tag = '') => `## ${word}\n\n${Array.from({ length: 300 }, (_, i) => `${word}${tag}${i}`).join(' ')}.`;
  const doc = (tag: string) => `---\ntype: note\ntitle: ${TITLE}\n---\n\n${['alpha', 'beta', 'gamma', 'delta'].map(w => section(w)).join('\n\n')}\n\n${section('omega', tag)}\n`;

  test('an edit re-embeds an enriched chunk once with its questions, then reuses its vector', async () => {
    await engine.setConfig(CHUNK_INDEX_TEXT_CONFIG_KEY, 'd2q');
    await importFromContent(engine, SLUG, doc('a'));
    const chunks = await engine.getChunks(SLUG);
    const target = chunks.find(c => c.chunk_text.includes('beta1 '))!;
    expect(target.chunk_text).not.toContain('omega');
    expect(await importChunkIndexTexts(engine, line(target.chunk_text), { refresh: false })).toMatchObject({ written: 1 });

    embedded.length = 0;
    await importFromContent(engine, SLUG, doc('b'));
    expect(embedded.filter(t => t.includes(QUESTIONS))).toEqual([expect.stringContaining(target.chunk_text)]);
    expect(embedded.some(t => t.includes('omegab1 '))).toBe(true);

    embedded.length = 0;
    await importFromContent(engine, SLUG, doc('c'));
    expect(embedded.some(t => t.includes(QUESTIONS) || t.includes('beta1 '))).toBe(false);
    expect(embedded.some(t => t.includes('omegac1 '))).toBe(true);
  });
});
