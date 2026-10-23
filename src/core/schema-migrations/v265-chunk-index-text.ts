import type { Migration } from './types.ts';
import { CHUNK_INDEX_TEXT_SCHEMA_SQL } from '../chunk-index-text-schema.ts';
import { chunkSearchVectorTriggerFnSql, getFtsLanguage } from '../fts-language.ts';

// Applied by src/core/migrate.ts on the next initSchema(); the fresh-install
// copy is the CHUNK_INDEX_TEXT_SCHEMA_SQL region of src/schema.sql.
//
// doc2query (src/core/chunk-index-text.ts): the chunk_index_text table, the
// gbrain_chunk_index_text() lookup, and the chunk keyword trigger recreated
// under the configured language so it adds the index text at weight C. The
// lookup returns NULL unless config search.chunk_index_text = 'd2q', so no
// stored keyword vector changes and nothing is backfilled: turning the
// setting on is followed by `gbrain chunk-text refresh`.
export const v265: Migration = {
  version: 265,
  name: 'chunk_index_text',
  idempotent: true,
  sql: CHUNK_INDEX_TEXT_SCHEMA_SQL,
  // The trigger text needs the configured language as a literal (getFtsLanguage() validates it).
  handler: async (engine) => {
    await engine.executeRaw(chunkSearchVectorTriggerFnSql(getFtsLanguage()));
  },
};
