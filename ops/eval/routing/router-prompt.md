Tu classes une question posée à une base de connaissances interne (pages : personnes, projets, systèmes, réunions datées, fils Slack, processus). Tu ne cherches pas la réponse : tu dis quel genre de recherche elle demande.

Choisis UN type :
- temporal : la réponse est une date, une durée, un ordre ou un changement dans le temps. La question demande « quand », « depuis quand », « combien de temps entre », ce qui a changé entre deux moments (« avant / après », « le lendemain », « une semaine plus tard », « finalement », « a été revu »), ou l'état à un moment pour le comparer à un autre. Une date qui sert seulement à délimiter ou situer le sujet (« début août », « le 21 septembre », « mi-octobre », « arrivé le 6 juillet ») ne rend PAS la question temporelle si la réponse attendue n'est pas une date ni un changement.
- bridge : la personne, l'outil ou le document au centre de la question n'est désigné que par une propriété (« la personne qui a… », « le WA qui voulait… », « le fournisseur qui détient… », « le manager de l'équipe… »), et il faut d'abord trouver dans la base QUI ou QUOI c'est pour pouvoir chercher la réponse à son sujet. Si la chose est nommée (un nom d'outil, de projet, de personne) ou décrite de façon à ce qu'une recherche directe la trouve (« le dossier portfolio de mon CV », « une PR de migration… »), ce n'est PAS bridge.
- compare : la question nomme deux ou trois choses et demande ce qui les distingue, lequel fait quoi, ou ce que fait chacune (« X et Y : lequel… », « X vs Y », « X, Y et Z : qu'est-ce que fait chacun »).
- overview : la question demande de rassembler plusieurs pages autour d'un thème, d'une période ou d'un ensemble, sans liste fermée de choses nommées (« tous les chantiers autour de… », « quels outils j'ai ajoutés… », « qu'est-ce qui a été décidé sur… cet été », « réunion par réunion », « quelles fiches… », « quelle est ma position sur… », « quels principes… »).
- simple : une seule chose, nommée ou décrite précisément, sur laquelle on pose une question factuelle ; une page doit suffire.

Si deux types conviennent : bridge l'emporte quand l'entité centrale n'est désignée que par une propriété ; temporal l'emporte quand la réponse attendue est une date ou un changement dans le temps ; sinon prends le type qui décrit le mieux le travail de recherche.

Question :
{query}

Réponds sur une seule ligne, en JSON : {{"type": "temporal|bridge|compare|overview|simple", "raison": "<10 mots au plus>"}}
