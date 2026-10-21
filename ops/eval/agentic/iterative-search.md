## Recherche itérative dans gbrain

Pour répondre à une question à partir du brain, ne te contente pas d'une recherche.

1. Découpe la question en sous-questions : une entité, un fait ou une date chacune. Une question simple n'en a qu'une.
2. Tour 1 : une recherche par sous-question, lancées ensemble. Formule chaque recherche avec les mots que la page visée emploierait (noms propres, termes métier), pas avec la question entière.
3. Lis les deux ou trois pages les plus prometteuses (`get_page`). Pour chaque sous-question, note si elle a sa réponse et dans quelle page.
4. Tours 2 et 3, seulement pour les sous-questions restées sans réponse :
   - si une page lue nomme l'entité intermédiaire (une personne, un système, une réunion), cherche-la par son nom ou suis ses liens (`get_backlinks`, `get_links`, `traverse_graph`) ;
   - sinon reformule : un synonyme, l'autre langue (français ou anglais), un terme plus précis ou plus large ;
   - pour une question datée, lis la timeline de la page concernée (`get_timeline`).
5. Arrête-toi dès que chaque sous-question a une page qui y répond, au plus tard après le troisième tour. Si une partie reste sans réponse, dis-le au lieu de deviner.
6. Cite les pages qui portent la réponse.
