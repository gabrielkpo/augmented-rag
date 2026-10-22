/**
 * Page supersession after the reranker (truth maintenance, off by default).
 *
 * A page is superseded when its frontmatter says `superseded_by: <slug>`, or
 * when it is the target of a `supersedes` link (the edge the native
 * `applySupersedeDownrank` already reads). The native stage multiplies the
 * fused score by 0.5 BEFORE the reranker, so a cross-encoder (or the RRF
 * fusion of its order with the hybrid order) can put the stale page back on
 * top. This stage acts on the final reranked order instead.
 *
 * `GBRAIN_SUPERSEDE` picks the variant (anything else, or unset: off):
 *   annotate    the order is left as is; stale rows are only stamped, so the
 *               agent reading the results sees which page replaced them.
 *   penalty     the stale page's rows lose rank as if their reciprocal rank
 *               were multiplied by GBRAIN_SUPERSEDE_PENALTY (default 0.5:
 *               rank 3 becomes rank 6). Score scales differ between the
 *               fused, reranked and RRF-fused orders; ranks do not.
 *   behind      the canonical page takes the stale page's slot (read in when
 *               the arms did not return it) and the stale page follows it.
 *   substitute  the canonical page takes the stale page's slot and the stale
 *               page leaves the list (named in the canonical row's `collapsed`).
 * Every touched stale row is stamped `superseded` / `superseded_by`, which
 * the lean MCP rows already carry to agents.
 *
 * `GBRAIN_SUPERSEDE_MAP` (a JSON file) adds supersessions without writing
 * any page: `{ "<stale slug>": "<canonical slug>" }`, or the proposal file
 * ops/eval/truth/supersessions.json (its entries with `applique_labo: true`).
 * It lets a proposal be measured before anyone writes it to the brain.
 *
 * Within-source only, like the native stage. A canonical page that the
 * caller may not read (other source, private, excluded slug) neither
 * replaces nor demotes, and its slug is never named. Fail-open: any error
 * returns the rows unchanged.
 */
import { readFileSync } from 'node:fs';
import type { SearchResult } from '../../types.ts';
import type { HybridRequest } from './request.ts';
import { type IdentityTierOpts, isExcludedIdentity } from '../alias-hop.ts';
import { isPrivatePage } from '../private-visibility.ts';
import { pageLeadSource, projectedLead } from '../../page-window/bounded-lead.ts';

export type SupersedeMode = 'off' | 'annotate' | 'penalty' | 'behind' | 'substitute';
const INJECTED_LEAD_CHARS = 1500;

export function supersedeSettings(env: Record<string, string | undefined> = process.env): { mode: SupersedeMode; factor: number; mapPath?: string } {
  const raw = (env.GBRAIN_SUPERSEDE ?? '').toLowerCase().trim();
  const mode: SupersedeMode = raw === 'annotate' || raw === 'penalty' || raw === 'behind' || raw === 'substitute' ? raw : 'off';
  const f = Number(env.GBRAIN_SUPERSEDE_PENALTY);
  return { mode, factor: Number.isFinite(f) && f > 0 && f < 1 ? f : 0.5, mapPath: env.GBRAIN_SUPERSEDE_MAP || undefined };
}

let mapCache: { path: string; map: Map<string, string> } | undefined;

/** The override map: stale slug -> canonical slug. A missing or malformed file is an empty map. */
export function loadSupersedeMap(path: string | undefined): Map<string, string> {
  if (!path) return new Map();
  if (mapCache?.path === path) return mapCache.map;
  const map = new Map<string, string>();
  try {
    const data = JSON.parse(readFileSync(path, 'utf-8'));
    const pairs = Array.isArray(data?.paires) ? data.paires : null;
    if (pairs) {
      for (const p of pairs) if (p?.applique_labo && typeof p.remplacee === 'string' && typeof p.fait_foi === 'string') map.set(p.remplacee, p.fait_foi);
    } else if (data && typeof data === 'object') {
      for (const [k, v] of Object.entries(data)) if (typeof v === 'string') map.set(k, v);
    }
  } catch {
    // fail-open: no override
  }
  mapCache = { path, map };
  return map;
}

const keyOf = (r: Pick<SearchResult, 'slug' | 'source_id'>) => `${r.source_id ?? 'default'}::${r.slug}`;

/** page_id -> canonical slug, from frontmatter `superseded_by` and `supersedes` links (same source, live superseder). */
async function lookupSupersessions(req: HybridRequest, rows: SearchResult[], map: Map<string, string>): Promise<Map<string, string>> {
  const out = new Map<string, string>();
  for (const r of rows) {
    const by = map.get(r.slug);
    if (by && by !== r.slug) out.set(keyOf(r), by);
  }
  const ids = [...new Set(rows.map(r => r.page_id).filter(id => Number.isSafeInteger(id) && id > 0))];
  if (ids.length === 0) return out;
  const found = await req.engine.executeRaw<{ id: number; source_id: string; slug: string; by: string }>(
    `SELECT p.id, p.source_id, p.slug, btrim(p.frontmatter->>'superseded_by') AS by
       FROM pages p
      WHERE p.id = ANY($1::int[]) AND p.deleted_at IS NULL AND NULLIF(btrim(p.frontmatter->>'superseded_by'), '') IS NOT NULL
     UNION ALL
     SELECT pt.id, pt.source_id, pt.slug, pf.slug AS by
       FROM links l
       JOIN pages pf ON pf.id = l.from_page_id
       JOIN pages pt ON pt.id = l.to_page_id
      WHERE l.link_type = 'supersedes' AND l.to_page_id = ANY($1::int[])
        AND pf.deleted_at IS NULL AND pf.source_id = pt.source_id`,
    [ids],
  );
  for (const f of found) {
    const k = `${f.source_id ?? 'default'}::${f.slug}`;
    const by = f.by.replace(/^\[\[|\]\]$/g, '').trim();
    if (by && by !== f.slug && !out.has(k)) out.set(k, by);
  }
  return out;
}

/** The canonical page as a result row, or null when the caller may not see it. */
async function readCanonical(req: HybridRequest, sourceId: string, slug: string, like: SearchResult): Promise<SearchResult | null> {
  const opts: IdentityTierOpts = req.aliasHopOpts;
  if (isExcludedIdentity(slug, opts)) return null;
  if (opts.sourceIds && !opts.sourceIds.includes(sourceId)) return null;
  if (opts.sourceId && opts.sourceId !== sourceId) return null;
  const page = await req.engine.getPage(slug, { sourceId, excludePrivate: opts.excludePrivate });
  if (!page || (opts.excludePrivate && isPrivatePage(page))) return null;
  const lead = await projectedLead(req.engine, { slug: page.slug, source_id: page.source_id ?? sourceId, compiled_truth: await pageLeadSource(req.engine, page) });
  if (!lead.trim()) return null;
  return {
    page_id: page.id, slug: page.slug, title: page.title, type: page.type, source_id: page.source_id ?? sourceId,
    chunk_text: lead.slice(0, INJECTED_LEAD_CHARS), chunk_source: 'compiled_truth', chunk_id: 0, chunk_index: 0,
    score: like.score, base_score: like.score, stale: false,
    ...(Number.isFinite(like.rerank_score) ? { rerank_score: like.rerank_score } : {}),
  };
}

/** Apply the configured variant to the reranked order (module comment). Returns a new array. */
export async function applyPageSupersession(req: HybridRequest, rows: SearchResult[]): Promise<SearchResult[]> {
  const { mode, factor, mapPath } = supersedeSettings();
  if (mode === 'off' || rows.length === 0) return rows;
  try {
    const stale = await lookupSupersessions(req, rows.filter(r => r.result_type !== 'fact'), loadSupersedeMap(mapPath));
    if (stale.size === 0) return rows;
    // Resolve each canonical page once: in the list already, or read in; null = not visible.
    const canon = new Map<string, SearchResult | null>();
    const firstRow = new Map<string, SearchResult>();
    for (const r of rows) if (!firstRow.has(keyOf(r))) firstRow.set(keyOf(r), r);
    for (const [k, by] of stale) {
      const src = k.split('::')[0];
      const ck = `${src}::${by}`;
      if (canon.has(ck)) continue;
      canon.set(ck, firstRow.get(ck) ?? await readCanonical(req, src, by, firstRow.get(k)!));
    }
    const target = (r: SearchResult) => {
      const by = stale.get(keyOf(r));
      if (!by) return null;
      const ck = `${r.source_id ?? 'default'}::${by}`;
      return canon.get(ck) ? { by, ck } : null;
    };
    const stamp = (r: SearchResult, by: string): SearchResult => ({ ...r, superseded: true, superseded_by: by });

    if (mode === 'annotate') {
      return rows.map(r => {
        const t = target(r);
        return t ? stamp(r, t.by) : r;
      });
    }

    if (mode === 'penalty') {
      // Reciprocal-rank penalty: a row at rank i (1-based) moves to rank i / factor.
      const keyed = rows.map((r, i) => {
        const t = target(r);
        return t ? { r: { ...stamp(r, t.by), supersede_penalty: factor }, at: (i + 1) / factor - 1 + 0.5 } : { r, at: i };
      });
      return keyed.sort((a, b) => a.at - b.at).map(x => x.r);
    }

    // behind / substitute: walk the list; the first row of a stale page is where its canonical page lands.
    const out: SearchResult[] = [];
    const placed = new Set<string>();
    const pending = new Map<string, SearchResult[]>(); // canonical key -> stale rows to append after it (behind)
    const collapsedInto = new Map<string, string[]>();
    for (const r of rows) {
      const k = keyOf(r);
      const t = target(r);
      if (t) {
        if (!placed.has(t.ck)) {
          out.push(canon.get(t.ck)!);
          placed.add(t.ck);
        }
        if (mode === 'behind') {
          const list = pending.get(t.ck) ?? [];
          list.push(stamp(r, t.by));
          pending.set(t.ck, list);
        } else if (!(collapsedInto.get(t.ck) ?? []).includes(r.slug)) {
          collapsedInto.set(t.ck, [...(collapsedInto.get(t.ck) ?? []), r.slug]);
        }
        continue;
      }
      // A canonical page's first row lands once: here, or earlier at a stale page's slot.
      if (canon.get(k) === r) {
        if (placed.has(k)) continue;
        placed.add(k);
      }
      out.push(r);
    }
    const result: SearchResult[] = [];
    for (const r of out) {
      const k = keyOf(r);
      const folded = collapsedInto.get(k);
      result.push(folded && canon.get(k) === r
        ? { ...r, collapsed: [...(r.collapsed ?? []), ...folded.map(slug => ({ slug, source_id: r.source_id, reason: 'redirect' as const }))] }
        : r);
      const after = pending.get(k);
      if (after && canon.get(k) === r) { result.push(...after); pending.delete(k); }
    }
    return result;
  } catch {
    return rows;
  }
}
