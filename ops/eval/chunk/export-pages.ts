// Exports the pages of 3 chunks or more (lab database A) to $CACHE_DIR/pages.jsonl:
// one line per page, with its chunks in order. Read-only.
import { mkdirSync, writeFileSync } from 'node:fs';
import { connect, targetPages, CACHE_DIR } from './db.ts';

const sql = connect('a');
mkdirSync(CACHE_DIR, { recursive: true });
const pages = await targetPages(sql);
const lines: string[] = [];
for (const p of pages) {
  const chunks = await sql<{ id: number; chunk_index: number; chunk_source: string; chunk_text: string }[]>`
    SELECT id, chunk_index, chunk_source, chunk_text FROM content_chunks
    WHERE page_id = ${p.page_id} ORDER BY chunk_index`;
  lines.push(JSON.stringify({ ...p, chunks }));
}
writeFileSync(`${CACHE_DIR}/pages.jsonl`, lines.join('\n') + '\n');
console.log(`${pages.length} pages, ${lines.reduce((n, l) => n + JSON.parse(l).chunks.length, 0)} chunks -> ${CACHE_DIR}/pages.jsonl`);
await sql.end();
