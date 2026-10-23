/**
 * `gbrain chunk-text`: post-connect dispatch for the doc2query chunk index
 * texts (src/commands/chunk-text.ts); the record lives in src/cli/command-table.ts.
 */
import type { BrainEngine } from '../../core/engine.ts';

export async function run(engine: BrainEngine, args: string[]): Promise<void> {
  const { runChunkText } = await import('../../commands/chunk-text.ts');
  await runChunkText(engine, args);
}
