## Recherche itérative dans gbrain (v2)

Pour répondre à une question à partir du brain, suis ces étapes. Ne réponds pas avant l'étape 4.

1. **Découpe.** Écris la liste des sous-questions : une entité, un fait ou une date chacune. Une question qui compare deux choses, ou qui demande ce qui a changé entre deux moments, en a au moins deux. Une question qui désigne quelqu'un ou quelque chose sans le nommer (« la personne qui… », « l'outil que… ») commence par la sous-question « qui ou quoi est-ce ? ».
2. **Cherche.** Une recherche par sous-question, lancées ensemble, avec les mots que la page visée emploierait (noms propres, termes métier, dates), pas la question entière.
3. **Lis.** Ouvre avec `get_page` chaque page qui semble répondre ; un extrait de recherche ne suffit pas comme preuve.
4. **Vérifie.** Pour chaque sous-question, recopie la phrase d'une page lue qui y répond, avec son slug. S'il manque une phrase pour une sous-question, fais un tour de plus pour elle seule :
   - si une page lue nomme l'entité intermédiaire, cherche-la par son nom ou suis ses liens (`get_backlinks`, `get_links`, `traverse_graph`) ;
   - pour un changement dans le temps, cherche la page de chaque date (réunion, état daté) ou lis la timeline (`get_timeline`) ;
   - sinon reformule : synonyme, autre langue, terme plus précis ou plus large.
   Au plus trois tours en tout. Ce qui reste sans phrase après le troisième tour, dis-le au lieu de deviner.
5. **Dates.** Date chaque fait par le moment qu'il décrit (« décidé le 13/10 », une entrée de timeline datée), jamais par la date de la page qui le rapporte : une page récente peut rappeler un fait ancien, une page ancienne peut être la seule source d'un fait toujours vrai. Si deux faits se contredisent, celui qui porte la date la plus récente l'emporte, sauf quand la question vise un moment précis (« à son lancement », « mi-septembre ») : c'est alors le fait de ce moment-là qui répond. Un fait sans date ne remplace pas un fait daté ; s'il reste un doute, donne les deux avec leurs dates.
6. **Réponds** à partir des phrases vérifiées seulement, et cite les pages d'où elles viennent. Ne recopie pas la liste de vérification dans la réponse.
