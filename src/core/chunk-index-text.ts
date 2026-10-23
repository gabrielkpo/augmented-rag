/**
 * Chunk index texts (doc2query): text produced OUTSIDE gbrain (Claude via
 * `claude -p`, ops/doc2query/generate.py) and added to a chunk's indexing
 * inputs, never to `content_chunks.chunk_text`.
 *
 * A stored text is keyed on (source_id, slug, chunk_hash, kind), where
 * chunk_hash is the sha256 hex of the chunk text's UTF-8 bytes (the SQL twin is
 * `encode(sha256(convert_to(chunk_text,'UTF8')),'hex')`, in
 * chunk-index-text-schema.ts). A chunk whose text changes no longer finds its
 * text until the next generation writes one for the new hash.
 *
 * When the brain setting `search.chunk_index_text` is `d2q`:
 *   - every embedding path appends the text inside the contextual prefix
 *     (`<context>{title}\n{text}\n</context>\n{chunk}`, embedding-context.ts),
 *     uncapped by the 300-character synopsis cap;
 *   - the embedding-input provenance hash covers it (embedding-input-hash.ts),
 *     so a vector built without it reads as stale and the reverse;
 *   - the chunk keyword vector adds it at weight C (the chunk stays at B).
 * With the setting off (the default) nothing reads the table: every input,
 * hash and keyword vector is byte-identical to a brain without it.
 *
 * Fenced code and image chunks are never enriched. gbrain calls no chat model
 * here: it stores, indexes and reports what is pending.
 */
import { sha256 } from './persistence/digest.ts';
import type { BrainEngine } from './engine.ts';

/** Brain config key (DB `config` table) that turns the index texts on. Off unless its value is `d2q`. */
export const CHUNK_INDEX_TEXT_CONFIG_KEY = 'search.chunk_index_text';
export const CHUNK_INDEX_TEXT_KINDS = ['d2q'] as const;
export type ChunkIndexTextKind = typeof CHUNK_INDEX_TEXT_KINDS[number];
/** Hard cap on one stored text. doc2query questions measure ~334 characters at the median. */
export const INDEX_TEXT_MAX_CHARS = 2000;

/** chunk_hash -> sanitized index text, for one page. */
export type ChunkIndexTexts = ReadonlyMap<string, string>;
export const NO_INDEX_TEXTS: ChunkIndexTexts = new Map();

export interface ChunkIndexTextRow {
  source_id: string;
  slug: string;
  chunk_hash: string;
  kind: ChunkIndexTextKind;
  text: string;
  model: string;
  prompt_version: string;
}

export function parseChunkIndexTextSetting(value: string | null | undefined): ChunkIndexTextKind | null {
  return value?.trim().toLowerCase() === 'd2q' ? 'd2q' : null;
}

/** sha256 hex of the chunk text's UTF-8 bytes. */
export function chunkTextHash(chunkText: string): string {
  return sha256(chunkText);
}

/** Fenced code and image chunks are never enriched (same rule as the SQL function). */
export function isIndexTextEligible(chunk: { chunk_source?: string | null }): boolean {
  return chunk.chunk_source !== 'fenced_code' && chunk.chunk_source !== 'image_asset';
}

/** Strips wrapper tags and collapses whitespace; capped at INDEX_TEXT_MAX_CHARS. */
export function sanitizeIndexText(text: string): string {
  return text.replace(/<\/?context>/gi, '').replace(/\s+/g, ' ').trim().slice(0, INDEX_TEXT_MAX_CHARS);
}

/** The index text a chunk carries under `texts`, or null (none stored, ineligible chunk, setting off). */
export function indexTextFor(texts: ChunkIndexTexts | null | undefined,
  chunk: { chunk_text: string; chunk_source?: string | null }): string | null {
  if (!texts || texts.size === 0 || !isIndexTextEligible(chunk)) return null;
  return texts.get(chunkTextHash(chunk.chunk_text)) || null;
}

const pageKey = (sourceId: string, slug: string) => `${sourceId}\0${slug}`;

/** The setting from the config table; tolerant of engine doubles that answer nothing. */
export async function readChunkIndexTextSetting(engine: Pick<BrainEngine, 'executeRaw'>): Promise<ChunkIndexTextKind | null> {
  const rows = await engine.executeRaw<{ value: string }>('SELECT value FROM config WHERE key = $1', [CHUNK_INDEX_TEXT_CONFIG_KEY]) ?? [];
  return parseChunkIndexTextSetting(rows[0]?.value);
}

/**
 * Index texts of several pages, keyed `${sourceId}\0${slug}`. `setting` is the
 * already-read setting (null: off, nothing is queried); undefined reads it.
 */
export async function loadChunkIndexTexts(engine: Pick<BrainEngine, 'executeRaw' | 'getChunkIndexTexts'>,
  refs: ReadonlyArray<{ sourceId: string; slug: string }>, setting?: ChunkIndexTextKind | null): Promise<Map<string, ChunkIndexTexts>> {
  const out = new Map<string, ChunkIndexTexts>();
  const kind = setting === undefined ? await readChunkIndexTextSetting(engine) : setting;
  if (!kind || refs.length === 0) return out;
  for (const row of await engine.getChunkIndexTexts(refs, { kind })) {
    const key = pageKey(row.source_id, row.slug);
    const texts = (out.get(key) ?? new Map<string, string>()) as Map<string, string>;
    const text = sanitizeIndexText(row.text);
    if (text) texts.set(row.chunk_hash, text);
    out.set(key, texts);
  }
  return out;
}

export async function loadPageChunkIndexTexts(engine: Pick<BrainEngine, 'executeRaw' | 'getChunkIndexTexts'>,
  sourceId: string, slug: string, setting?: ChunkIndexTextKind | null): Promise<ChunkIndexTexts> {
  return (await loadChunkIndexTexts(engine, [{ sourceId, slug }], setting)).get(pageKey(sourceId, slug)) ?? NO_INDEX_TEXTS;
}

export function pageIndexTexts(all: ReadonlyMap<string, ChunkIndexTexts>, sourceId: string, slug: string): ChunkIndexTexts {
  return all.get(pageKey(sourceId, slug)) ?? NO_INDEX_TEXTS;
}
