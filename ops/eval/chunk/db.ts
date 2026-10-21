// Connection to the lab databases of the chunk track. Refuses any database that is not gbrain_lab_chunk*.
import postgres from 'postgres';
import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';

export const LAB_HOME = `${homedir()}/gbrain-labs/chunk`;
export const CACHE_DIR = `${LAB_HOME}/cache`;

export function labUrl(variant: string): string {
  const cfg = JSON.parse(readFileSync(`${LAB_HOME}/.gbrain/config.json`, 'utf8'));
  const base: string = cfg.database_url;
  const db = variant === 'a' ? 'gbrain_lab_chunk' : `gbrain_lab_chunk_${variant}`;
  const url = base.replace(/\/[^/]+$/, `/${db}`);
  if (!/\/gbrain_lab_chunk(_[a-z0-9]+)?$/.test(url)) throw new Error(`refus : ${db} n'est pas une base de labo`);
  return url;
}

export function connect(variant: string) {
  return postgres(labUrl(variant), { max: 4, onnotice: () => {} });
}

export function openaiKey(): string {
  const env = readFileSync(`${LAB_HOME}/.gbrain/.env`, 'utf8');
  const m = env.match(/^OPENAI_API_KEY=(.+)$/m);
  if (!m) throw new Error('OPENAI_API_KEY absente du .env du labo');
  return m[1].trim().replace(/^['"]|['"]$/g, '');
}

/** Live pages split into 3 chunks or more: the scope of the track. */
export async function targetPages(sql: ReturnType<typeof connect>) {
  return sql<{ page_id: number; slug: string; type: string; title: string; n: number }[]>`
    SELECT p.id AS page_id, p.slug, p.type, p.title, c.n::int AS n
    FROM pages p
    JOIN (SELECT page_id, count(*) AS n FROM content_chunks GROUP BY page_id) c ON c.page_id = p.id
    WHERE p.deleted_at IS NULL AND c.n >= 3
    ORDER BY p.slug`;
}
