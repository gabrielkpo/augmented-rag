/**
 * Brings a page's derived indexes in line with its chunk index texts
 * (doc2query, chunk-index-text.ts) under the current setting: re-embeds only
 * the chunks whose recorded embedding input differs from the one the page
 * would embed now (a text written, changed, or the setting toggled), and
 * recomputes the page's chunk keyword vectors (the trigger expression, which
 * reads the text at weight C). Never writes a page, its chunk text or its
 * revision: vectors land through `installPageEmbeddings`, under the page
 * guard and its input-hash check, like every other late embed.
 */
import type { BrainEngine } from './engine.ts';
import type { ChunkInput } from './types.ts';
import { embeddingInputContext, embeddingWriteTarget, installPageEmbeddings, readProjectionSnapshot } from './page-state/projections.ts';
import { acceptedEmbeddingInputHashes, hasIndexText } from './embedding-input-hash.ts';
import { wrapChunkTextsForStoredMode } from './embedding-context.ts';
import { embedBatchWithBackoff } from './embed-retry.ts';
import { meterPageBatch, spendGrant } from './page-embedding-budget.ts';
import { carryChunkMetadata } from './embed-stale.ts';
import { isEmbedSkipped } from './embed-skip.ts';
import { chunkSearchVectorSql, getFtsLanguage } from './fts-language.ts';
import { currentEmbeddingSignature } from './embedding.ts';

export interface ChunkIndexRefreshResult {
  pages: number;
  reembedded: number;
  keywordRefreshed: number;
  skipped: Array<{ source_id: string; slug: string; reason: string }>;
  failures: Array<{ source_id: string; slug: string; error: string }>;
}

export type EmbedTexts = (texts: string[], opts: { abortSignal?: AbortSignal }) => Promise<(Float32Array | null)[]>;

export function emptyRefreshResult(): ChunkIndexRefreshResult {
  return { pages: 0, reembedded: 0, keywordRefreshed: 0, skipped: [], failures: [] };
}

/** The page's chunk keyword vectors recomputed under the configured language, under the page guard. */
export async function refreshPageChunkKeywordVectors(engine: BrainEngine, sourceId: string, slug: string): Promise<number> {
  return engine.transaction(async tx => {
    await tx.lockPageKeys([{ sourceId, slug }]);
    const rows = await tx.executeRaw<{ id: number }>(`UPDATE content_chunks cc SET search_vector = ${chunkSearchVectorSql(getFtsLanguage(), 'cc.')}
      FROM pages p WHERE p.id = cc.page_id AND p.source_id = $1 AND p.slug = $2 AND p.deleted_at IS NULL RETURNING cc.id`, [sourceId, slug]);
    return rows.length;
  });
}

/**
 * Refreshes one page into `result`. `embed` is the provider seam (tests); keyword vectors are refreshed even when embedding
 * fails. A page's failure (provider, dropped connection) is recorded and never stops the others; a rerun redoes only what is left.
 */
export async function refreshPageChunkIndex(engine: BrainEngine, sourceId: string, slug: string, result: ChunkIndexRefreshResult,
  opts: { embed?: EmbedTexts; signal?: AbortSignal } = {}): Promise<void> {
  try {
    await refreshPage(engine, sourceId, slug, result, opts);
  } catch (error) {
    if (opts.signal?.aborted) throw error;
    result.failures.push({ source_id: sourceId, slug, error: error instanceof Error ? error.message : String(error) });
  }
}

async function refreshPage(engine: BrainEngine, sourceId: string, slug: string, result: ChunkIndexRefreshResult,
  opts: { embed?: EmbedTexts; signal?: AbortSignal }): Promise<void> {
  result.pages++;
  try {
    result.keywordRefreshed += await refreshPageChunkKeywordVectors(engine, sourceId, slug);
  } catch (error) {
    result.failures.push({ source_id: sourceId, slug, error: `keyword vectors: ${error instanceof Error ? error.message : String(error)}` });
  }
  const prepared = await readProjectionSnapshot(engine, slug, sourceId, { requireLiveSource: true });
  if (!prepared) { result.skipped.push({ source_id: sourceId, slug, reason: 'no sealed inline projection (missing, unsealed or segmented page)' }); return; }
  const page = prepared.snapshot.page;
  if (isEmbedSkipped(page.frontmatter)) { result.skipped.push({ source_id: sourceId, slug, reason: 'frontmatter.embed_skip' }); return; }
  const target = await embeddingWriteTarget(engine);
  const [row] = await engine.executeRaw<{ corpus_generation: string | null }>('SELECT corpus_generation FROM pages WHERE id = $1', [page.id]);
  const provenance = embeddingInputContext(target, page.title, row?.corpus_generation ?? null, prepared.chunks, prepared.indexTexts);
  const recorded = new Map((await engine.executeRaw<{ id: number; embedding_input_hash: string | null }>(
    'SELECT id, embedding_input_hash FROM content_chunks WHERE page_id = $1', [page.id])).map(r => [Number(r.id), r.embedding_input_hash]));
  const mode = page.contextual_retrieval_mode;
  // A chunk without a vector is left to `embed --stale`; one whose vector was built for another input is redone.
  const stale = prepared.chunks.filter(chunk => !chunk.embedding_is_null && chunk.embedded_at).filter(chunk => {
    const hash = recorded.get(Number(chunk.id)) ?? null;
    return hash === null ? hasIndexText(provenance, chunk) : !acceptedEmbeddingInputHashes(provenance, mode, chunk).includes(hash);
  });
  if (stale.length === 0) return;
  const metered = await meterPageBatch(engine, page, stale, { signature: currentEmbeddingSignature(), announceHeld: true });
  if (!metered || metered.chunks.length === 0) { result.skipped.push({ source_id: sourceId, slug, reason: 'embedding budget held' }); return; }
  const chunks = metered.chunks;
  const embed: EmbedTexts = opts.embed ?? ((texts, o) => embedBatchWithBackoff(texts, o));
  try {
    const vectors = await spendGrant(metered.grant, () => embed(wrapChunkTextsForStoredMode(page, chunks, prepared.indexTexts), { abortSignal: opts.signal }));
    const byIndex = new Map(chunks.map((chunk, i) => [chunk.chunk_index, vectors[i] ?? undefined]));
    const merged: ChunkInput[] = prepared.chunks.map(chunk => carryChunkMetadata(chunk, {
      chunk_index: chunk.chunk_index, chunk_text: chunk.chunk_text, chunk_source: chunk.chunk_source,
      embedding: byIndex.get(chunk.chunk_index) ?? undefined,
      token_count: chunk.token_count || Math.ceil(chunk.chunk_text.length / 4),
    }));
    const install = await engine.transaction(tx => installPageEmbeddings(tx, prepared, merged));
    if (install.installed === 0) result.failures.push({ source_id: sourceId, slug, error: 'the page changed during the embed; nothing installed (run refresh again)' });
    result.reembedded += install.installed;
  } catch (error) {
    if (opts.signal?.aborted) throw error;
    result.failures.push({ source_id: sourceId, slug, error: error instanceof Error ? error.message : String(error) });
  }
}

/** Pages that hold at least one index text of `kind` (every page a toggle of the setting affects). */
export async function listIndexTextPages(engine: BrainEngine, kind: string, sourceId?: string): Promise<Array<{ source_id: string; slug: string }>> {
  return engine.executeRaw<{ source_id: string; slug: string }>(`SELECT DISTINCT x.source_id, x.slug FROM chunk_index_text x
    JOIN pages p ON p.source_id = x.source_id AND p.slug = x.slug AND p.deleted_at IS NULL
    WHERE x.kind = $1 AND ($2::text IS NULL OR x.source_id = $2::text) ORDER BY x.source_id, x.slug`, [kind, sourceId ?? null]);
}
