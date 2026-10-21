-- Brain health: one JSON object per run, read by ops/health/health.sh.
-- Read-only. Rolling 7-day window, unless stated otherwise.
--
-- Sources:
--   mcp_request_log      every MCP call of the Claude sessions (not the CLI: the benches are not in it)
--   eval_candidates      every real search, with the pages returned (config eval.capture)
--   retrieval_events     a search/query answer carrying an answer_id
--   retrieval_event_pages  the pages of that answer, with their rank
--   retrieval_feedback   the rate_answer ratings, page by page
--   chat_usage_log       gbrain's LLM calls and their cost
with
mcp as (
  select * from mcp_request_log where created_at > now() - interval '7 days'
),
recherches as (
  select * from mcp where operation in ('search', 'query')
),
captees as (
  select * from eval_candidates where created_at > now() - interval '7 days'
),
notes as (
  select f.event_id, p.slug, p.rank + 1 as rank, f.rating  -- rank 1 = first
  from retrieval_feedback f
  join retrieval_event_pages p on p.event_id = f.event_id and p.slug = f.element_key and p.source_id = f.source_id
  where f.applied_at > now() - interval '7 days' and f.rating is not null
),
-- rank of the first page rated 5 in each rated answer
premier_bon as (
  select event_id, min(rank) filter (where rating = 5) as rang
  from notes group by event_id
),
connaissance as (
  select * from pages
  where deleted_at is null
    and type in ('system', 'project', 'process', 'person', 'partner', 'concept', 'original')
)
select json_build_object(
  'genere_le', now(),
  'usage', json_build_object(
    'recherches_7j', (select count(*) from recherches),
    'recherches_24h', (select count(*) from recherches where created_at > now() - interval '1 day'),
    'lectures_page_7j', (select count(*) from mcp where operation = 'get_page'),
    'ecritures_7j', (select count(*) from mcp where operation in ('put_page', 'put_pages', 'edit_page', 'append_page', 'add_timeline_entry')),
    'recherches_par_lecture', round((select count(*) from recherches)::numeric / nullif((select count(*) from mcp where operation = 'get_page'), 0), 2)
  ),
  'latence_ms', json_build_object(
    'search_p50', (select round(percentile_cont(0.5) within group (order by latency_ms)) from recherches where operation = 'search'),
    'search_p95', (select round(percentile_cont(0.95) within group (order by latency_ms)) from recherches where operation = 'search'),
    'query_p50', (select round(percentile_cont(0.5) within group (order by latency_ms)) from recherches where operation = 'query'),
    'query_p95', (select round(percentile_cont(0.95) within group (order by latency_ms)) from recherches where operation = 'query')
  ),
  'erreurs', json_build_object(
    'taux_global', round((select count(*) filter (where status = 'error') from mcp)::numeric / nullif((select count(*) from mcp), 0), 4),
    'taux_recherche', round((select count(*) filter (where status = 'error') from recherches)::numeric / nullif((select count(*) from recherches), 0), 4),
    'par_operation', (select coalesce(json_object_agg(operation, n), '{}'::json) from (
      select operation, count(*) as n from mcp where status = 'error' group by operation order by n desc limit 5) e)
  ),
  'resultats', json_build_object(
    'recherches_captees_7j', (select count(*) from captees),
    'taux_sans_resultat', round((select count(*) filter (where coalesce(cardinality(retrieved_slugs), 0) = 0) from captees)::numeric / nullif((select count(*) from captees), 0), 4),
    'questions_repetees', (select count(*) from (select lower(btrim(query)) from captees group by 1 having count(*) > 1) r)
  ),
  'notes', json_build_object(
    'reponses_notees_7j', (select count(distinct event_id) from notes),
    'pages_notees_7j', (select count(*) from notes),
    'note_moyenne', (select round(avg(rating), 2) from notes),
    'mrr_en_ligne', (select round(avg(case when rang is null then 0 else 1.0 / rang end), 3) from premier_bon),
    'precision_top3', round((select count(*) filter (where rating >= 4) from notes where rank <= 3)::numeric / nullif((select count(*) from notes where rank <= 3), 0), 3),
    'hors_sujet_top3', (select count(*) from notes where rank <= 3 and rating <= 1)
  ),
  'contenu', json_build_object(
    'pages_connaissance', (select count(*) from connaissance),
    'pages_modifiees_7j', (select count(*) from pages where deleted_at is null and updated_at > now() - interval '7 days'),
    'chunks_sans_embedding', (select count(*) from content_chunks c join pages p on p.id = c.page_id
                              where p.deleted_at is null and c.retired_generation is null and c.embedding is null),
    'orphelines', (select count(*) from connaissance k where not exists (
                     select 1 from links l where l.to_page_id = k.id and l.from_page_id <> k.id)),
    'liens_morts', (select count(*) from links l join pages p on p.id = l.to_page_id where p.deleted_at is not null)
  ),
  'cout_usd_7j', json_build_object(
    'llm_gbrain', (select round(coalesce(sum(cost_usd), 0)::numeric, 3) from chat_usage_log where created_at > now() - interval '7 days')
  )
);
