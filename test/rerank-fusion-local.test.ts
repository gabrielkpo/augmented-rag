import { describe, expect, test } from 'bun:test';
import { fuseRerankWithInputOrder } from '../src/core/search/rerank.ts';

// Reranker output in its own order: index = position in the fused (input) order.
const rerankOrder = [
  { index: 5, relevanceScore: 1 },
  { index: 0, relevanceScore: 0.66 },
  { index: 1, relevanceScore: 0.66 },
  { index: 2, relevanceScore: 0.33 },
  { index: 3, relevanceScore: 0 },
  { index: 4, relevanceScore: 0 },
];

describe('fuseRerankWithInputOrder', () => {
  test('keeps the reranker order when fusion is not enabled', () => {
    expect(fuseRerankWithInputOrder(rerankOrder, {})).toBe(rerankOrder);
    expect(fuseRerankWithInputOrder(rerankOrder, { GBRAIN_RERANK_FUSION: 'replace' })).toBe(rerankOrder);
  });

  test('a page first in both orders stays first', () => {
    const both = [{ index: 0, relevanceScore: 1 }, { index: 1, relevanceScore: 0.5 }, { index: 2, relevanceScore: 0 }];
    expect(fuseRerankWithInputOrder(both, { GBRAIN_RERANK_FUSION: 'rrf' }).map(r => r.index)).toEqual([0, 1, 2]);
  });

  test('the hybrid first result no longer sinks on the reranker alone', () => {
    const fused = fuseRerankWithInputOrder(rerankOrder, { GBRAIN_RERANK_FUSION: 'rrf', GBRAIN_RERANK_FUSION_K: '0' }).map(r => r.index);
    // input #0 is 2nd for the reranker: it beats input #5 (reranker 1st, input 6th).
    expect(fused[0]).toBe(0);
    expect(fused.indexOf(0)).toBeLessThan(fused.indexOf(5));
  });

  test('a large reranker weight gives the reranker order back', () => {
    const fused = fuseRerankWithInputOrder(rerankOrder, { GBRAIN_RERANK_FUSION: 'rrf', GBRAIN_RERANK_FUSION_WEIGHT: '1000' }).map(r => r.index);
    expect(fused).toEqual(rerankOrder.map(r => r.index));
  });

  test('invalid settings fall back to the defaults', () => {
    const a = fuseRerankWithInputOrder(rerankOrder, { GBRAIN_RERANK_FUSION: 'RRF', GBRAIN_RERANK_FUSION_K: 'x', GBRAIN_RERANK_FUSION_WEIGHT: '-1' });
    const b = fuseRerankWithInputOrder(rerankOrder, { GBRAIN_RERANK_FUSION: 'rrf' });
    expect(a.map(r => r.index)).toEqual(b.map(r => r.index));
  });
});

import { RELEVANCE_LEVELS, RELEVANCE_LEVELS_V2, rerankQuestions } from '../src/core/ai/decide/rerank-adapter.ts';

describe('rerank rubric v2 (local)', () => {
  test('v1 stays the default', () => {
    const q = rerankQuestions(['a'], {})[0]!;
    expect(q.kind === 'score' && q.levels).toEqual(RELEVANCE_LEVELS);
  });

  test('GBRAIN_RERANK_RUBRIC=v2 sends four described levels and says the title leads the candidate', () => {
    const q = rerankQuestions(['a', 'b'], { GBRAIN_RERANK_RUBRIC: 'v2' });
    expect(q).toHaveLength(2);
    expect(q[0]!.kind === 'score' && q[0]!.levels).toEqual(RELEVANCE_LEVELS_V2);
    expect(RELEVANCE_LEVELS_V2).toHaveLength(RELEVANCE_LEVELS.length);
    expect(q[0]!.instructions).toContain('starts with the title');
  });
});
