Tu extrais des **faits datés** d'une page du brain gbrain, pour une couche de faits qui doit répondre à des questions du type « quand a-t-on décidé X ? », « quel était l'état de Y à telle date ? », « qu'est-ce qui a changé entre telle et telle date ? ».

Page à traiter : `{slug}`

1. Lis la page avec `get_page` (slug `{slug}`) et sa timeline avec `get_timeline`. N'ouvre pas d'autre page, sauf pour vérifier le slug exact d'une entité (`resolve_slugs`).
2. Retiens au plus **{max_facts}** faits, les plus utiles pour des questions temporelles : décisions, changements d'état (lancé, mis en prod, abandonné, suspendu, remplacé), choix techniques qui ont changé (modèle, outil, architecture), chiffres qui évoluent (nombre de profils, de leads, version), changements de responsable, jalons et échéances fixées. Pas de faits intemporels (définitions, descriptions stables), pas de doublons.
3. Pour chaque fait :
   - `entity` : slug de la page dont le fait parle (souvent `{slug}` ; pour une réunion, le projet ou le système concerné, s'il a une page). Un slug qui existe, jamais inventé.
   - `slot` : quelques mots qui nomment l'attribut qui change (« statut », « modèle de l'agent », « nombre de profils persona », « date de mise en prod »). Deux faits sur le même attribut de la même entité ont le même `slot`, mot pour mot.
   - `claim` : une phrase en français, autonome (compréhensible sans la page : nomme l'entité), sans date relative (« la semaine dernière »).
   - `valid_from` : date `AAAA-MM-JJ` à laquelle le fait **a pris effet**, c'est-à-dire le moment qu'il décrit, **jamais** la date de la page qui le rapporte. Une page écrite le 20/10 qui dit « décidé le 13/10 » donne 2026-10-13. Une réunion du 16/09 qui annonce une décision prise en séance donne 2026-09-16. Un mois seul donne le 1er du mois. L'année, quand elle manque, est celle du contexte (2026 en général).
   - `date_basis` : `described` si la date vient du texte ou de la timeline qui date le fait lui-même ; `page` si la seule date disponible est celle de la page ou de la réunion qui le constate (état « au » jour de la page).
   - `supersedes` : `id` d'un fait plus ancien **de ta liste** que celui-ci remplace (même entité, même `slot`, nouvel état). Sinon `null`.
   - `valid_until` : seulement si la page dit explicitement quand le fait a cessé d'être vrai sans qu'un fait de ta liste le remplace ; sinon `null`.
   - `evidence` : la phrase de la page qui porte le fait et sa date (30 mots au plus, recopiée).
   Un fait dont tu ne peux pas trouver la date de prise d'effet, ni la date de la page, n'est pas retenu.
4. Réponds **uniquement** par un objet JSON, sans texte autour :

{"page": "{slug}", "page_date": "AAAA-MM-JJ ou null", "facts": [{"id": "f1", "entity": "...", "slot": "...", "claim": "...", "valid_from": "AAAA-MM-JJ", "date_basis": "described|page", "supersedes": null, "valid_until": null, "evidence": "..."}]}
