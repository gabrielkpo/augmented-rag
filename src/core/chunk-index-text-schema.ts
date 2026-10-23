/**
 * DDL for chunk index texts (doc2query, src/core/chunk-index-text.ts): one
 * copy, spliced into src/schema.sql (and the PGLite blob) by
 * `bun run build:schema` and applied by migration v265.
 *
 * `gbrain_chunk_index_text(page_id, chunk_source, chunk_text)` is what the
 * chunk keyword vector reads at weight C (fts-language.ts
 * `chunkSearchVectorSql`): the stored text for the chunk's current hash when
 * the `search.chunk_index_text` setting is `d2q`, else NULL, so with the
 * setting off every keyword vector is unchanged. plpgsql, so the blob may
 * create it before the `config` table exists; the trigger only runs later.
 */
export const CHUNK_INDEX_TEXT_SCHEMA_SQL = `
CREATE TABLE IF NOT EXISTS chunk_index_text (
  source_id      TEXT        NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  slug           TEXT        NOT NULL,
  chunk_hash     TEXT        NOT NULL,
  kind           TEXT        NOT NULL CHECK (kind IN ('d2q')),
  text           TEXT        NOT NULL,
  model          TEXT        NOT NULL,
  prompt_version TEXT        NOT NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (source_id, slug, chunk_hash, kind)
);
CREATE OR REPLACE FUNCTION gbrain_chunk_index_text(p_page_id integer, p_chunk_source text, p_chunk_text text) RETURNS text
  LANGUAGE plpgsql STABLE SET search_path = pg_catalog, public
AS $fn$
DECLARE
  setting text;
  t_text text;
BEGIN
  IF p_chunk_text IS NULL OR p_chunk_source IN ('fenced_code', 'image_asset') THEN
    RETURN NULL;
  END IF;
  SELECT value INTO setting FROM config WHERE key = 'search.chunk_index_text';
  IF lower(btrim(coalesce(setting, ''))) <> 'd2q' THEN
    RETURN NULL;
  END IF;
  SELECT x.text INTO t_text
    FROM pages p JOIN chunk_index_text x ON x.source_id = p.source_id AND x.slug = p.slug
   WHERE p.id = p_page_id AND x.kind = 'd2q'
     AND x.chunk_hash = encode(sha256(convert_to(p_chunk_text, 'UTF8')), 'hex');
  RETURN t_text;
END;
$fn$;
`;
