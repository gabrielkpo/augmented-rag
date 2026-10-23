## Questions sur le temps dans gbrain

Quand la question porte sur une date, une durée, un ordre ou un changement (« quand », « depuis quand », « au 10 octobre », « avant / après », « fin septembre », « qu'est-ce qui a changé », « où en était »), suis ces étapes en plus de ta méthode habituelle.

1. **Période.** Écris la période visée en dates (`AAAA-MM-JJ`, ou un intervalle) et le type de question : *quand* (la date d'un événement), *état à date* (ce qui était vrai à un moment), *changement* (l'état à deux moments, ou ce qui s'est passé entre eux). Sans période dans la question, c'est l'état actuel.
2. **Entité.** Trouve le slug de la page de chaque entité en jeu (projet, système, personne) : `search` avec son nom, ou `resolve_slugs`.
3. **Faits datés.** Pour chaque entité, lance `recall` avec `entity: <slug>` et `include_expired: true` : tu obtiens ses faits avec `valid_from` (date de prise d'effet), `valid_until` et `superseded_by` (le fait qui l'a remplacé), y compris ceux qui ne sont plus vrais. Range-les en frise : un fait vaut de son `valid_from` jusqu'à son `valid_until` ou jusqu'au fait qui le remplace. Pour « depuis le … », `recall` avec `since` donne les faits apparus après cette date. Lance ces appels ensemble, avec la recherche de l'étape 4.
4. **Timeline et pages.** Lis `get_timeline` de la page de l'entité (entrées datées), puis ouvre avec `get_page` la page qui porte la réponse. N'utilise pas `since` / `until` de `query` pour viser une période : ils filtrent sur la date de la page, pas sur celle des faits, et écartent la page projet ou système qui raconte toute l'histoire. Un fait de `recall` est un index : sa provenance nomme la page d'où il vient ; vérifie-le sur cette page avant de l'affirmer, et cite cette page.
5. **Choisis la date juste.**
   - *quand* : la date à laquelle l'événement a eu lieu (`valid_from` du fait, date d'une entrée de timeline, date écrite dans la page), jamais la date de la page ou de la réunion qui le rapporte.
   - *état à date* : le fait le plus récent dont la date de prise d'effet précède la date visée et qui n'avait pas encore été remplacé ou terminé à cette date. Un état livré mais pas encore allumé, décidé mais pas encore en prod, n'est pas en vigueur.
   - *changement* : l'état en vigueur à chacun des deux moments, avec la date du passage de l'un à l'autre.
   Si deux sources donnent des dates différentes pour le même fait, donne celle qui décrit l'événement lui-même, et signale l'autre.
6. **Réponds** avec les dates en clair (jour/mois/année) et cite les pages, pas les faits.
