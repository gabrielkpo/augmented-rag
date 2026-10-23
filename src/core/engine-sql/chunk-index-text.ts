/**
 * Chunk index texts (doc2query, src/core/chunk-index-text.ts): one SQL
 * implementation for both engines. The table holds text produced outside
 * gbrain, keyed on (source_id, slug, chunk_hash, kind); chunk_hash is
 * `encode(sha256(convert_to(chunk_text,'UTF8')),'hex')`. Reads are internal
 * indexing reads addressed by explicit page keys, so they are unscoped.
 */
import type { ChunkIndexTextKind, ChunkIndexTextRow } from '../chunk-index-text.ts';
import type { SqlExecutor } from './executor.ts';
import { jsonbParam } from './executor.ts';
import type { LegacyUnscopedRead } from './brands.ts';
import { sqlFragment, trustedSql } from './fragment.ts';
import { chunkVisibleFilter } from '../search/safe-chunks.ts';

export interface ChunkIndexTextInput {
  source_id: string;
  slug: string;
  chunk_hash: string;
  kind: ChunkIndexTextKind;
  text: string;
  model: string;
  prompt_version: string;
}

/** One chunk of a page that still needs an index text, with every chunk of that page (`has_text` marks the covered ones). */
export interface ChunkIndexTextPendingRow {
  source_id: string;
  slug: string;
  page_id: number;
  title: string;
  type: string;
  chunk_index: number;
  chunk_source: string;
  chunk_text: string;
  chunk_hash: string;
  has_text: boolean;
  eligible: boolean;
}

export async function getChunkIndexTexts(exec: LegacyUnscopedRead, refs: ReadonlyArray<{ sourceId: string; slug: string }>,
  opts: { kind: ChunkIndexTextKind }): Promise<ChunkIndexTextRow[]> {
  if (refs.length === 0) return [];
  const { rows } = await exec.run(sqlFragment`
    SELECT x.source_id, x.slug, x.chunk_hash, x.kind, x.text, x.model, x.prompt_version
      FROM chunk_index_text x
      JOIN jsonb_to_recordset((${jsonbParam({ rows: refs.map(r => ({ source_id: r.sourceId, slug: r.slug })) })}::jsonb)->'rows')
        AS v(source_id text, slug text) ON v.source_id = x.source_id AND v.slug = x.slug
     WHERE x.kind = ${opts.kind}`);
  return rows as unknown as ChunkIndexTextRow[];
}

export async function putChunkIndexTexts(exec: SqlExecutor, rows: ReadonlyArray<ChunkIndexTextInput>): Promise<number> {
  if (rows.length === 0) return 0;
  const { affectedRows } = await exec.run(sqlFragment`
    INSERT INTO chunk_index_text (source_id, slug, chunk_hash, kind, text, model, prompt_version)
    SELECT v.source_id, v.slug, v.chunk_hash, v.kind, v.text, v.model, v.prompt_version
      FROM jsonb_to_recordset((${jsonbParam({ rows })}::jsonb)->'rows')
        AS v(source_id text, slug text, chunk_hash text, kind text, text text, model text, prompt_version text)
    ON CONFLICT (source_id, slug, chunk_hash, kind) DO UPDATE
      SET text = EXCLUDED.text, model = EXCLUDED.model, prompt_version = EXCLUDED.prompt_version, created_at = now()`);
  return affectedRows;
}

/**
 * Live markdown pages with at least `minChunks` visible chunks that hold an
 * eligible chunk (not fenced code, not an image) without a text for its
 * current hash, every chunk of each, newest pages first (`limitPages` pages).
 */
export async function listChunkIndexTextPending(exec: LegacyUnscopedRead, opts: { kind: ChunkIndexTextKind; minChunks: number;
  sourceId?: string; limitPages?: number }): Promise<ChunkIndexTextPendingRow[]> {
  const { rows } = await exec.run(sqlFragment`
    WITH chunk AS (
      SELECT p.id AS page_id, p.source_id, p.slug, p.title, p.type, p.updated_at, cc.chunk_index, cc.chunk_source, cc.chunk_text,
             encode(sha256(convert_to(cc.chunk_text, 'UTF8')), 'hex') AS chunk_hash,
             cc.chunk_source NOT IN ('fenced_code', 'image_asset') AS eligible,
             count(*) OVER (PARTITION BY p.id) AS n
        FROM pages p
        JOIN sources s ON s.id = p.source_id AND NOT s.archived
        JOIN content_chunks cc ON cc.page_id = p.id
       WHERE p.deleted_at IS NULL AND p.page_kind = 'markdown'
         AND (${opts.sourceId ?? null}::text IS NULL OR p.source_id = ${opts.sourceId ?? null}::text)
         AND ${trustedSql(chunkVisibleFilter('p', 'cc'))}
    ), marked AS (
      SELECT c.*, EXISTS (SELECT 1 FROM chunk_index_text x WHERE x.source_id = c.source_id AND x.slug = c.slug
               AND x.kind = ${opts.kind} AND x.chunk_hash = c.chunk_hash) AS has_text
        FROM chunk c WHERE c.n >= ${opts.minChunks}
    ), wanted AS (
      SELECT page_id, max(updated_at) AS updated_at FROM marked WHERE eligible AND NOT has_text GROUP BY page_id
    ), ranked AS (
      SELECT page_id, dense_rank() OVER (ORDER BY updated_at DESC, page_id) AS r FROM wanted
    )
    SELECT m.source_id, m.slug, m.page_id, m.title, m.type, m.chunk_index, m.chunk_source, m.chunk_text, m.chunk_hash,
           m.has_text, m.eligible
      FROM marked m JOIN ranked k ON k.page_id = m.page_id
     WHERE (${opts.limitPages ?? null}::int IS NULL OR k.r <= ${opts.limitPages ?? null}::int)
     ORDER BY k.r, m.chunk_index`);
  return (rows as unknown as ChunkIndexTextPendingRow[]).map(r => ({ ...r, page_id: Number(r.page_id), chunk_index: Number(r.chunk_index) }));
}

/** Deletes a kind's texts: every one (`all`), else only those whose hash no live chunk of their page holds. */
export async function pruneChunkIndexTexts(exec: SqlExecutor, opts: { kind: ChunkIndexTextKind; all?: boolean }): Promise<number> {
  const { affectedRows } = await exec.run(sqlFragment`
    DELETE FROM chunk_index_text x
     WHERE x.kind = ${opts.kind}
       AND (${opts.all ?? false}::boolean OR NOT EXISTS (
         SELECT 1 FROM pages p JOIN content_chunks cc ON cc.page_id = p.id
          WHERE p.source_id = x.source_id AND p.slug = x.slug AND p.deleted_at IS NULL
            AND encode(sha256(convert_to(cc.chunk_text, 'UTF8')), 'hex') = x.chunk_hash))`);
  return affectedRows;
}
