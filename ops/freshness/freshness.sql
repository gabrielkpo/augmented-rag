-- Freshness indicator of the gbrain knowledge pages.
--
-- A page "lags" when its timeline holds events dated more than one day
-- after the last rewrite of its summary (compiled truth). The rewrite date
-- comes from the version history (page_versions): it is the last snapshot
-- whose summary differs from the current summary, else the page's creation.
-- The frontmatter `updated` field is not read: 65 % of the pages lack it.
--
-- The one-day margin avoids counting the timeline entry that a session-end
-- routine adds right after rewriting the summary. The event's date
-- (timeline_entries.date) is compared, not its write date, so that
-- timelines recomputed in bulk by a repair are not counted.
--
-- Output: one JSON row per lagging page, the most lagging first.
with truth as (
  select p.id, p.slug, p.type, p.title,
         coalesce(
           (select max(v.snapshot_at) from page_versions v
             where v.page_id = p.id and v.compiled_truth is distinct from p.compiled_truth),
           p.created_at) as truth_at
  from pages p
  where p.deleted_at is null
    and p.type in ('system', 'project', 'process', 'person', 'partner', 'concept', 'original')
)
select json_build_object(
         'slug', t.slug,
         'type', t.type,
         'title', t.title,
         'synthese_reecrite_le', to_char(t.truth_at, 'YYYY-MM-DD'),
         'evenements_apres', count(*),
         'dernier_evenement', to_char(max(te.date), 'YYYY-MM-DD'),
         'retard_jours', max(te.date) - t.truth_at::date)
from truth t
join timeline_entries te on te.page_id = t.id and te.date > t.truth_at::date + 1
group by t.slug, t.type, t.title, t.truth_at
order by max(te.date) - t.truth_at::date desc, count(*) desc;
