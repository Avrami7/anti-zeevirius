# Tâches prêtes pour Jules

Trois tâches **indépendantes** : aucune ne modifie les fichiers d'une autre,
elles peuvent donc tourner en parallèle. Chacune est autonome — copiez-la
telle quelle dans Jules.

Dépôt : `Avrami7/anti-zeevirius`, branche `main`.
**Jules lit `AGENTS.md` à la racine** : doctrine, conventions, et les huit
pièges déjà rencontrés dans ce dépôt. Chaque tâche ci-dessous le rappelle.

Vérification commune : `python -m pytest tests/ -q` doit rester **entièrement
vert**. Base actuelle : 1449 tests, 1 ignoré.

---

## Tâche 1 — Tests de la Triarchie

> Le dépôt contient `assistant/triarchie.py` (977 lignes) : trois instances
> — PROMÉTHÉE, JUANITA JCV, ÉPIMÉTHÉE — qui délibèrent avant qu'une action soit
> proposée à l'utilisateur. **Ce module n'a AUCUN test.** Écris
> `tests/test_triarchie.py`. Ne modifie aucun autre fichier.
>
> Lis d'abord `AGENTS.md` puis `docs/TRIARCHIE.md`, qui est le contrat gelé.
>
> Les deux tests les plus importants, qui protègent des propriétés de sécurité :
>
> 1. **L'humain reste l'arbitre.** `exige_humain` doit être VRAI dès que
>    `Capacite.risque` dépasse `Risque.LECTURE`, quel que soit le verdict — même
>    sur une unanimité POUR avec une confiance de 1,0. Ce champ n'est jamais
>    calculé à partir des avis. Teste aussi qu'aucune délibération ne peut
>    produire ni contenir un `confirm_token`.
> 2. **Une triarchie incomplète ne décide pas.** Si moins de trois instances sont
>    actives, le verdict est `"incomplete"` — on ne bascule PAS en majorité de
>    deux. Teste pour 0, 1 et 2 instances.
>
> Ensuite : que `deliberer()` est bien PURE (aucun accès disque, aucune
> exécution, aucun réseau — prouve-le en interdisant `open` et `subprocess` par
> monkeypatch pendant l'appel) ; que `avis` est trié et le résultat déterministe
> sous plusieurs `PYTHONHASHSEED` (ce dépôt a déjà livré ce bug dans
> `security/network_watch.py`) ; qu'un `motif` vide est refusé à la construction
> d'un `Avis` ; et **qu'il existe au moins un état où les trois positions
> diffèrent** — si tu n'en trouves aucun, les critères sont redondants : dis-le
> dans ton rapport plutôt que d'écrire un test complaisant.
>
> Écris de VRAIS tests, qui peuvent échouer. Pour au moins deux invariants,
> casse volontairement le code pour vérifier qu'un test proteste, puis remets-le
> en état. Un test qui reste vert quand on casse ce qu'il couvre est pire que pas
> de test.

---

## Tâche 2 — Les trois personas dans le moteur de parole

> `assistant/voice.py` (1137 lignes) expose aujourd'hui une classe unique
> `Promethee`. Il doit en gérer **TROIS** qui partagent le même moteur.
> Modifie `assistant/voice.py` et crée `tests/test_assistant_voice.py`.
> **Ne modifie aucun autre fichier** — surtout pas `gui/bridge.py` ni
> `gui/web/*`.
>
> Lis d'abord `AGENTS.md`, puis `docs/PERSONNALITES.md` : c'est le contrat gelé,
> il donne les règles de langue de chaque persona.
>
> Trois choses à faire :
>
> 1. **Une dataclass `Persona`** (identifiant, nom, rôle, voix préférée, débit,
>    volume, gravités prises en charge) et trois instances : `promethee`,
>    `juanita`, `epimethee`. `dire()` prend un paramètre `persona`.
> 2. **Une file unique et partagée.** Deux voix ne doivent JAMAIS parler en même
>    temps — c'est le piège principal de cette architecture, écris un test qui le
>    prouve. PROMÉTHÉE peut **interrompre** ÉPIMÉTHÉE sur une gravité critique,
>    jamais l'inverse. `taire()` coupe instantanément et vide la file, toutes
>    personas confondues.
> 3. **`valider_style(texte, persona) -> tuple[bool, str]`**, appelé AVANT toute
>    énonciation. Il refuse une phrase non conforme et renvoie le motif du refus.
>    Contrôles : temps grammaticaux interdits (PROMÉTHÉE ne parle jamais au
>    passé, ÉPIMÉTHÉE jamais au futur ni à l'impératif, JUANITA au présent
>    seulement), longueur maximale (12 / 30 / 10 mots), et aucun adjectif
>    d'appréciation chez JUANITA.
>    **Pour chaque persona : au moins trois phrases conformes et trois non
>    conformes, avec le motif attendu du refus.** C'est ce qui transforme la
>    personnalité en propriété d'ingénierie au lieu d'un texte d'ambiance.
>
> Ne casse pas l'existant : l'expurgation des données personnelles avant
> énonciation (aucun chemin, nom de fichier, utilisateur, adresse IP ni nom de
> machine en mode `discrete`), les heures de silence, l'anti-répétition et
> l'échappement PowerShell doivent rester intacts et testés. **Jamais
> `shell=True`** : le texte à dire vient de noms de fichiers, donc de données non
> maîtrisées. Écris un test qui tente une injection et vérifie qu'elle échoue.
>
> Tu es sous Linux : le sous-processus PowerShell doit être simulé dans les
> tests, et le module doit rester importable et testable hors Windows.

---

## Tâche 3 — Brancher la voix et la Triarchie sur le pont web

> L'interface contient déjà un panneau vocal complet (`gui/web/index.html`,
> section Assistant) mais **`gui/bridge.py` n'expose rien** : le panneau est une
> façade, il ne peut pas parler au moteur. Branche-le.
> Modifie `gui/bridge.py` et `gui/API_CONTRACT.md`, crée
> `tests/test_bridge_voice.py`. **Ne modifie aucun autre fichier** — surtout pas
> `assistant/voice.py`, `assistant/triarchie.py` ni `gui/web/*`.
>
> Lis d'abord `AGENTS.md`, puis `gui/bridge.py` EN ENTIER pour reprendre ses
> conventions : import paresseux (un module absent produit
> `{"ok": false, "unavailable": true, "reason": "..."}` en **HTTP 200**, jamais
> un crash du serveur), et `_guarded()` pour tout ce qui écrit.
>
> Actions à ajouter :
>
> | Action | Effet |
> |---|---|
> | `voix.etat` | disponibilité, en train de parler, file, réglages |
> | `voix.personas` | les trois personas et leurs réglages |
> | `voix.dire` | énonce un texte (essai), accepte `persona` |
> | `voix.taire` | coupe immédiatement |
> | `voix.reglages` | lit / écrit ; `persona` optionnel, sinon global |
> | `voix.voix_disponibles` | les voix installées sur la machine |
> | `triarchie.deliberer` | **lecture seule** : rend la délibération, n'exécute RIEN |
>
> `triarchie.deliberer` ne doit **rien exécuter** et ne doit **jamais** produire
> de `confirm_token` : elle éclaire une décision, elle ne la prend pas. Écris un
> test qui le prouve.
>
> Les noms de champs renvoyés doivent correspondre exactement à ce que
> `gui/web/app.js` lit déjà — vérifie-le dans le fichier. Ce dépôt a déjà eu ce
> défaut : une fonction de normalisation lisait le mauvais champ, et TOUTES les
> modales destructives affichaient « Rien à faire » sans que rien ne signale
> l'erreur.
>
> Vérifie contre le vrai serveur, pas seulement en relisant ton code :
> ```python
> import sys, threading; sys.path.insert(0, ".")
> from gui.server import create_server
> httpd, token = create_server(0); port = httpd.server_address[1]
> threading.Thread(target=httpd.serve_forever, daemon=True).start()
> ```
> puis appelle chaque action et compare la réponse au contrat.

---

## Ce qu'aucune de ces tâches ne couvre

Le `.exe` n'a **jamais** tourné sur une machine Windows réelle. Les 1449 tests
simulent le système : `netsh`, `vssadmin`, `schtasks`, le registre et SAPI n'ont
jamais été exécutés une seule fois. Aucun agent ne lèvera ce risque — il faut une
machine Windows. C'est le premier jalon utile du projet.
