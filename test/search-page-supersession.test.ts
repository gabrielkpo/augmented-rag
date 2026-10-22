/**
 * Page supersession after the reranker (src/core/search/hybrid/supersession.ts).
 *
 * Protects: with GBRAIN_SUPERSEDE unset the reranked order is returned as is;
 * `annotate` keeps the order and only stamps the stale rows;
 * `penalty` moves a stale page down by its reciprocal rank; `behind` puts the
 * canonical page in the stale page's slot with the stale page right after it;
 * `substitute` drops the stale page and names it in the canonical row's
 * `collapsed`; a canonical page the caller cannot read changes nothing.
 * Fails when: the stage runs by default, places the canonical page twice, or
 * demotes a page toward a canonical page that is not visible.
 * Seams: a fake engine (executeRaw answers the frontmatter/link lookup,
 * getPage the visibility check); supersessions come from the frontmatter row
 * and from a GBRAIN_SUPERSEDE_MAP file.
 */
import { expect, test } from 'bun:test';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { applyPageSupersession, loadSupersedeMap, supersedeSettings } from '../src/core/search/hybrid/supersession.ts';
import type { SearchResult } from '../src/core/types.ts';
import { withEnv } from './helpers/with-env.ts';

const row = (slug: string, id: number, score = 1): SearchResult => ({
  slug, page_id: id, title: slug, type: 'project', chunk_text: slug, chunk_source: 'compiled_truth',
  chunk_id: id, chunk_index: 0, score, stale: false, source_id: 'default',
} as SearchResult);

function fakeReq(frontmatter: Array<{ id: number; slug: string; by: string }> = [], visible = true) {
  return {
    engine: {
      executeRaw: async () => frontmatter.map(f => ({ ...f, source_id: 'default' })),
      getPage: async () => (visible ? { id: 99, slug: 'x' } : null),
    },
    aliasHopOpts: {},
  } as never;
}

/** Runs fn with only the given supersession variables set (the others cleared), restored afterwards. */
const supersede = <T>(env: Record<string, string>, fn: () => Promise<T>) =>
  withEnv({ GBRAIN_SUPERSEDE: undefined, GBRAIN_SUPERSEDE_PENALTY: undefined, GBRAIN_SUPERSEDE_MAP: undefined, ...env }, fn);

const list = () => [row('a', 1), row('projects/old', 2), row('b', 3), row('c', 4), row('systems/new', 5), row('d', 6)];
const slugs = (rs: SearchResult[]) => rs.map(r => r.slug);
const fm = [{ id: 2, slug: 'projects/old', by: 'systems/new' }];

test('off by default: the reranked order comes back untouched', async () => {
  const rows = list();
  expect(supersedeSettings({}).mode).toBe('off');
  expect(await applyPageSupersession(fakeReq(fm), rows)).toBe(rows);
});

test('annotate: the order stays, the stale row names its canonical page', async () => {
  await supersede({ GBRAIN_SUPERSEDE: 'annotate' }, async () => {
    const out = await applyPageSupersession(fakeReq(fm), list());
    expect(slugs(out)).toEqual(slugs(list()));
    expect(out[1].superseded_by).toBe('systems/new');
    expect(out[0].superseded).toBeUndefined();
  });
});

test('penalty: rank 2 becomes rank 4 at factor 0.5 and the row is stamped', async () => {
  await supersede({ GBRAIN_SUPERSEDE: 'penalty' }, async () => {
    const out = await applyPageSupersession(fakeReq(fm), list());
    expect(slugs(out)).toEqual(['a', 'b', 'c', 'projects/old', 'systems/new', 'd']);
    const old = out.find(r => r.slug === 'projects/old')!;
    expect(old.superseded).toBe(true);
    expect(old.superseded_by).toBe('systems/new');
    expect(old.supersede_penalty).toBe(0.5);
  });
});

test('behind: the canonical page takes the slot once, the stale page follows it', async () => {
  await supersede({ GBRAIN_SUPERSEDE: 'behind' }, async () => {
    const out = await applyPageSupersession(fakeReq(fm), list());
    expect(slugs(out)).toEqual(['a', 'systems/new', 'projects/old', 'b', 'c', 'd']);
  });
});

test('substitute: the stale page leaves and is named on the canonical row', async () => {
  await supersede({ GBRAIN_SUPERSEDE: 'substitute' }, async () => {
    const out = await applyPageSupersession(fakeReq(fm), list());
    expect(slugs(out)).toEqual(['a', 'systems/new', 'b', 'c', 'd']);
    expect(out[1].collapsed?.map(c => c.slug)).toEqual(['projects/old']);
  });
});

test('a canonical page ranked above the stale page is not placed twice', async () => {
  await supersede({ GBRAIN_SUPERSEDE: 'behind' }, async () => {
    const rows = [row('systems/new', 5), row('a', 1), row('projects/old', 2)];
    expect(slugs(await applyPageSupersession(fakeReq(fm), rows))).toEqual(['systems/new', 'projects/old', 'a']);
  });
});

test('an invisible canonical page neither replaces nor demotes', async () => {
  await supersede({ GBRAIN_SUPERSEDE: 'substitute' }, async () => {
    const rows = [row('a', 1), row('projects/old', 2), row('b', 3)];
    expect(slugs(await applyPageSupersession(fakeReq([{ id: 2, slug: 'projects/old', by: 'systems/hidden' }], false), rows)))
      .toEqual(['a', 'projects/old', 'b']);
  });
});

test('the proposal file drives the stage without any page write', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'supersede-map-'));
  const path = join(dir, 'supersessions.json');
  writeFileSync(path, JSON.stringify({ paires: [
    { remplacee: 'projects/old', fait_foi: 'systems/new', applique_labo: true },
    { remplacee: 'b', fait_foi: 'c', applique_labo: false },
  ] }));
  expect([...loadSupersedeMap(path)]).toEqual([['projects/old', 'systems/new']]);
  await supersede({ GBRAIN_SUPERSEDE: 'substitute', GBRAIN_SUPERSEDE_MAP: path }, async () => {
    expect(slugs(await applyPageSupersession(fakeReq([]), list()))).toEqual(['a', 'systems/new', 'b', 'c', 'd']);
  });
});
