/**
 * `gbrain chunk-text`: host-only ingestion of chunk index texts (doc2query,
 * src/core/chunk-index-text.ts). gbrain never generates them: Claude does,
 * outside gbrain (ops/doc2query/generate.py), from `pending`, and hands the
 * result back through `import`. The texts are derived index data, written to
 * `chunk_index_text` and to the chunks' vectors and keyword vectors, never to
 * a page: no page coordinator, no revision, nothing in the content repository.
 */
import { readFileSync } from 'node:fs';
import type { BrainEngine } from '../core/engine.ts';
import { setCliExitVerdict } from '../core/cli-force-exit.ts';
import {
  CHUNK_INDEX_TEXT_CONFIG_KEY, chunkTextHash, isIndexTextEligible, readChunkIndexTextSetting, sanitizeIndexText,
  type ChunkIndexTextKind,
} from '../core/chunk-index-text.ts';
import type { ChunkIndexTextInput } from '../core/engine-sql/chunk-index-text.ts';
import { emptyRefreshResult, listIndexTextPages, refreshPageChunkIndex, type ChunkIndexRefreshResult } from '../core/chunk-index-text-refresh.ts';

const KIND: ChunkIndexTextKind = 'd2q';

export const CHUNK_TEXT_HELP = `gbrain chunk-text <subcommand>  (doc2query chunk index texts; host only)

  pending [--min-chunks 3] [--limit-pages N] [--source ID] [--json]
      Pages of at least N chunks with a chunk (not fenced code, not an image)
      that has no index text for its current text. --json prints every chunk of
      each page with chunk_hash, has_text and eligible (input of generate.py).
  import <file.jsonl> [--no-refresh] [--json]
      One JSON object per line: {source_id?, slug, chunk_hash | chunk_text,
      chunk_index?, questions: [...] | text, model?, prompt_version?}. A line
      whose hash matches no current chunk of its page is skipped and counted.
      With ${CHUNK_INDEX_TEXT_CONFIG_KEY}=d2q, the touched pages are refreshed.
  refresh [--source ID] [--slug SLUG ...] [--json]
      Re-embed the chunks whose vector was built for another input (texts
      written, setting toggled) and recompute their keyword vectors. Without
      --slug: every page holding an index text.
  prune [--all] [--json]
      Delete texts whose hash no live chunk holds (--all: every text).
  status [--json]
      The setting and how many texts and pages there are.

Setting: gbrain config set ${CHUNK_INDEX_TEXT_CONFIG_KEY} d2q (on), unset (off);
then gbrain chunk-text refresh. Docs: ops/doc2query/README.md`;

interface ImportLine {
  source_id?: string; slug?: string; chunk_hash?: string; chunk_text?: string; chunk_index?: number;
  questions?: unknown; text?: unknown; model?: string; prompt_version?: string | number;
}

export interface ChunkTextImportResult {
  lines: number;
  written: number;
  invalid: Array<{ line: number; reason: string }>;
  unmatched: number;
  pages: number;
  setting: ChunkIndexTextKind | null;
  refresh?: ChunkIndexRefreshResult;
}

function flagValue(args: string[], name: string): string | undefined {
  const at = args.indexOf(name);
  return at === -1 ? undefined : args[at + 1];
}

function flagValues(args: string[], name: string): string[] {
  return args.flatMap((arg, i) => (arg === name && args[i + 1] !== undefined ? [args[i + 1]!] : []));
}

/** Parses, validates against the current chunks, stores, then refreshes the touched pages when the setting is on. */
export async function importChunkIndexTexts(engine: BrainEngine, jsonl: string, opts: { refresh?: boolean } = {}): Promise<ChunkTextImportResult> {
  const setting = await readChunkIndexTextSetting(engine);
  const result: ChunkTextImportResult = { lines: 0, written: 0, invalid: [], unmatched: 0, pages: 0, setting };
  const byPage = new Map<string, { sourceId: string; slug: string; rows: ChunkIndexTextInput[] }>();
  jsonl.split('\n').forEach((raw, i) => {
    if (!raw.trim()) return;
    result.lines++;
    let line: ImportLine;
    try { line = JSON.parse(raw) as ImportLine; } catch { result.invalid.push({ line: i + 1, reason: 'not JSON' }); return; }
    const slug = typeof line.slug === 'string' ? line.slug : '';
    const hash = typeof line.chunk_hash === 'string' && /^[0-9a-f]{64}$/.test(line.chunk_hash) ? line.chunk_hash
      : typeof line.chunk_text === 'string' ? chunkTextHash(line.chunk_text) : '';
    const questions = Array.isArray(line.questions) ? line.questions.filter((q): q is string => typeof q === 'string') : [];
    const text = sanitizeIndexText(typeof line.text === 'string' ? line.text : questions.join(' '));
    if (!slug || !hash || !text) {
      result.invalid.push({ line: i + 1, reason: !slug ? 'missing slug' : !hash ? 'missing chunk_hash or chunk_text' : 'empty text' });
      return;
    }
    const sourceId = typeof line.source_id === 'string' && line.source_id ? line.source_id : 'default';
    const key = `${sourceId}\0${slug}`;
    const page = byPage.get(key) ?? { sourceId, slug, rows: [] };
    page.rows.push({ source_id: sourceId, slug, chunk_hash: hash, kind: KIND, text,
      model: typeof line.model === 'string' && line.model ? line.model : 'unknown',
      prompt_version: line.prompt_version === undefined ? '' : String(line.prompt_version) });
    byPage.set(key, page);
  });
  const touched: Array<{ sourceId: string; slug: string }> = [];
  let batch: ChunkIndexTextInput[] = [];
  const flush = async () => { if (batch.length) result.written += await engine.putChunkIndexTexts(batch); batch = []; };
  for (const page of byPage.values()) {
    const [row] = await engine.executeRaw<{ page_kind: string }>('SELECT page_kind FROM pages WHERE source_id = $1 AND slug = $2 AND deleted_at IS NULL',
      [page.sourceId, page.slug]);
    const chunks = row?.page_kind === 'markdown' ? await engine.getChunks(page.slug, { sourceId: page.sourceId }) : [];
    const current = new Set(chunks.filter(isIndexTextEligible).map(chunk => chunkTextHash(chunk.chunk_text)));
    const kept = page.rows.filter(r => current.has(r.chunk_hash));
    result.unmatched += page.rows.length - kept.length;
    if (!kept.length) continue;
    touched.push({ sourceId: page.sourceId, slug: page.slug });
    batch.push(...kept);
    if (batch.length >= 500) await flush();
  }
  await flush();
  result.pages = touched.length;
  if (setting && opts.refresh !== false) {
    result.refresh = emptyRefreshResult();
    for (const page of touched) await refreshPageChunkIndex(engine, page.sourceId, page.slug, result.refresh);
  }
  return result;
}

export async function runChunkText(engine: BrainEngine, args: string[]): Promise<void> {
  const sub = args[0];
  const json = args.includes('--json');
  const out = (value: unknown, text: string) => { console.log(json ? JSON.stringify(value, null, 2) : text); };
  if (!sub || sub === '--help' || sub === '-h' || args.includes('--help')) { console.log(CHUNK_TEXT_HELP); return; }

  if (sub === 'pending') {
    const minChunks = Number(flagValue(args, '--min-chunks') ?? 3);
    const limitRaw = flagValue(args, '--limit-pages');
    const limitPages = limitRaw === undefined ? undefined : Number(limitRaw);
    if (!Number.isInteger(minChunks) || minChunks < 1 || (limitPages !== undefined && (!Number.isInteger(limitPages) || limitPages < 1))) {
      console.error('chunk-text pending: --min-chunks and --limit-pages take positive integers.');
      setCliExitVerdict(2); return;
    }
    const rows = await engine.listChunkIndexTextPending({ kind: KIND, minChunks, sourceId: flagValue(args, '--source'), limitPages });
    const pages: Array<{ source_id: string; slug: string; title: string; type: string; chunks: unknown[] }> = [];
    for (const row of rows) {
      let page = pages[pages.length - 1];
      if (!page || page.source_id !== row.source_id || page.slug !== row.slug) {
        page = { source_id: row.source_id, slug: row.slug, title: row.title, type: row.type, chunks: [] };
        pages.push(page);
      }
      page.chunks.push({ chunk_index: row.chunk_index, chunk_source: row.chunk_source, chunk_hash: row.chunk_hash,
        has_text: row.has_text, eligible: row.eligible, chunk_text: row.chunk_text });
    }
    const missing = rows.filter(r => r.eligible && !r.has_text).length;
    out({ kind: KIND, setting: await readChunkIndexTextSetting(engine), min_chunks: minChunks, pages, missing_chunks: missing },
      `${pages.length} page(s), ${missing} chunk(s) without an index text\n${pages.map(p => `  ${p.source_id}:${p.slug}`).join('\n')}`);
    return;
  }

  if (sub === 'import') {
    const file = args[1];
    if (!file || file.startsWith('--')) { console.error('chunk-text import: pass the JSONL file.'); setCliExitVerdict(2); return; }
    const result = await importChunkIndexTexts(engine, readFileSync(file, 'utf8'), { refresh: !args.includes('--no-refresh') });
    out(result, [
      `${result.lines} line(s): ${result.written} text(s) written on ${result.pages} page(s), ${result.unmatched} without a matching current chunk, ${result.invalid.length} invalid`,
      result.setting ? (result.refresh ? `refreshed: ${result.refresh.reembedded} chunk(s) re-embedded, ${result.refresh.keywordRefreshed} keyword vector(s), ${result.refresh.failures.length} failure(s)` : 'not refreshed (--no-refresh)')
        : `${CHUNK_INDEX_TEXT_CONFIG_KEY} is off: texts stored, nothing re-embedded`,
    ].join('\n'));
    if (result.invalid.length || result.refresh?.failures.length) setCliExitVerdict(1);
    return;
  }

  if (sub === 'refresh') {
    const sourceId = flagValue(args, '--source');
    const slugs = flagValues(args, '--slug');
    const pages = slugs.length ? slugs.map(slug => ({ source_id: sourceId ?? 'default', slug })) : await listIndexTextPages(engine, KIND, sourceId);
    const result = emptyRefreshResult();
    for (const page of pages) await refreshPageChunkIndex(engine, page.source_id, page.slug, result);
    out({ setting: await readChunkIndexTextSetting(engine), ...result },
      `${result.pages} page(s): ${result.reembedded} chunk(s) re-embedded, ${result.keywordRefreshed} keyword vector(s) recomputed, `
      + `${result.skipped.length} skipped, ${result.failures.length} failure(s)${result.failures.map(f => `\n  ${f.source_id}:${f.slug}: ${f.error}`).join('')}`);
    if (result.failures.length) setCliExitVerdict(1);
    return;
  }

  if (sub === 'prune') {
    const deleted = await engine.pruneChunkIndexTexts({ kind: KIND, all: args.includes('--all') });
    out({ deleted }, `${deleted} text(s) deleted${args.includes('--all') ? '' : ' (no live chunk holds their hash)'}`);
    return;
  }

  if (sub === 'status') {
    const [counts] = await engine.executeRaw<{ texts: number; pages: number }>(
      'SELECT count(*)::int AS texts, count(DISTINCT (source_id, slug))::int AS pages FROM chunk_index_text WHERE kind = $1', [KIND]);
    const setting = await readChunkIndexTextSetting(engine);
    out({ kind: KIND, setting, texts: Number(counts?.texts ?? 0), pages: Number(counts?.pages ?? 0) },
      `${CHUNK_INDEX_TEXT_CONFIG_KEY}: ${setting ?? 'off'}; ${counts?.texts ?? 0} text(s) on ${counts?.pages ?? 0} page(s)`);
    return;
  }

  console.error(`chunk-text: unknown subcommand '${sub}'.\n\n${CHUNK_TEXT_HELP}`);
  setCliExitVerdict(2);
}
