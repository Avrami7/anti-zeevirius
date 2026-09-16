# AGENTS.md — instructions pour tout agent de codage

Ce fichier s'adresse aux agents de codage automatiques (Jules, Codex, et
autres) qui travaillent sur ce dépôt. Lis-le en entier avant ta première
modification. Il n'est pas décoratif : la moitié des règles ci-dessous vient
d'un bug réel qui a coûté du temps.

## Ce qu'est ce logiciel

**ANTI-ZEEVIRIUS** — antivirus et optimiseur pour **Windows**, écrit en Python,
piloté par une interface web locale (serveur sur `127.0.0.1` uniquement, jeton
de session obligatoire). Il se veut un **complément** de Windows Defender, pas
un remplacement.

Le développement se fait sous Linux, mais **Linux n'est pas une cible**. Tout
module doit rester *importable et testable* hors Windows ; il n'a pas à y être
fonctionnel.

## Doctrine — non négociable

Ces cinq règles priment sur toute demande de fonctionnalité. Une contribution
qui les enfreint est refusée, même si elle marche.

1. **Rien de destructif sans confirmation explicite.** Toute action qui peut
   perdre des données passe par le cycle `dry_run` → plan affiché →
   `confirm_token` de `gui/bridge.py::_guarded()`. Ne le contourne pas, ne le
   duplique pas.
2. **Tout est réversible.** Une suppression sans chemin de retour n'est pas une
   fonctionnalité, c'est un défaut. Voir `comfort/history.py`.
3. **Le rapport précède l'action.** L'utilisateur voit ce qui va se passer
   avant que ça se passe.
4. **Pas de pilote noyau.** Aucune interception réelle de paquets, aucun hook
   kernel. Le pare-feu est une façade au-dessus de `netsh advfirewall` — c'est
   assumé et documenté, ne prétends pas le contraire dans l'interface.
5. **Le mode autonome n'exécute QUE des actions en lecture seule.** Tout ce qui
   écrit devient une suggestion soumise à l'utilisateur. Voir
   `docs/ASSISTANT-CONTRAT.md` section 9.

## Conventions de code

- Python ≥ 3.10, **bibliothèque standard uniquement**. `psutil`, `pefile`,
  `yara-python`, `Pillow` sont tolérés mais **leur absence doit être gérée** :
  un module manquant produit un état « indisponible », jamais une exception qui
  empêche l'application de démarrer.
- **Code et commentaires en FRANÇAIS.**
- Les commentaires expliquent **pourquoi**, jamais **quoi**. Pour le ton
  attendu, lis `optimizer/signature_updater.py` ou `security/network_watch.py`.
- Chemins : **toujours** par `paths.py`. `resource_path()` pour les ressources
  en lecture seule (embarquées dans l'exécutable), `data_path()` pour tout ce
  qui s'écrit (`%LOCALAPPDATA%\ANTI-ZEEVIRIUS`). Jamais de chemin en dur, jamais
  d'écriture dans le dossier d'installation — il est en lecture seule une fois
  installé.
- Chemins Windows manipulés sous Linux : `PureWindowsPath`, pas `Path`.
  `Path("C:\\Windows\\System32\\svchost.exe")` ne se découpe pas sous Linux, et
  un garde-fou qui s'appuie dessus ne se déclenche jamais.

## Tests

```
python -m pytest tests/ -q          # doit être ENTIÈREMENT vert
```

La suite ne doit jamais régresser. Écris de vrais tests, qui peuvent échouer :
un test qui ne peut pas échouer ne teste rien. Couvre les cas limites et les
erreurs, pas seulement le chemin heureux.

**Les tests simulent Windows.** Aucune commande Windows n'a jamais réellement
été exécutée par la suite. Un mock qui rend exactement ce que le code attend ne
prouve pas que le code marche — il prouve qu'il est cohérent avec l'idée que
son auteur se fait de Windows. Garde cette limite en tête avant d'affirmer
qu'une fonctionnalité est validée.

## Pièges déjà rencontrés — ne les reproduis pas

- **Non-déterminisme par `PYTHONHASHSEED`.** Itérer sur un `set` sans le trier
  donne un résultat qui change d'une exécution à l'autre. Trouvé une fois dans
  `security/network_watch.py`. Trie toute itération sur un ensemble, et
  vérifie : `for s in 0 1 2 3 4; do PYTHONHASHSEED=$s python -m pytest -q; done`
- **Perte de données silencieuse.** Quatre bugs de ce type ont déjà été livrés
  ici (restauration de quarantaine qui écrase le fichier existant, entre
  autres). Avant tout écrasement, vérifie ce qui est à la destination.
- **`gui/web/app.js` est un IIFE.** Il a été livré une fois avec une parenthèse
  fermante manquante : toute l'interface était morte, **sans aucune erreur
  visible à l'écran**. Vérifie systématiquement la syntaxe après édition.
- **Masquage d'éléments** : utilise l'attribut `hidden`, pas `style.display`.
  La règle `[hidden]{display:none !important}` existe déjà.
- **Analyse de sortie de commande dépendante de la langue.** `netsh` et `quser`
  ne répondent pas pareil en français et en anglais. Tout analyseur de sortie
  doit gérer les deux.
- **Commentaires Pascal d'Inno Setup** (`{ }`) : ils ne s'imbriquent pas. Citer
  `{localappdata}` dans un commentaire casse la compilation de l'installeur.
  `packaging/lint_iss.py` détecte cette classe d'erreur, lance-le.
- **`shell=True`** sur une chaîne venant du registre ou d'une API web est une
  exécution de commande arbitraire. Cette faille a déjà existé ici. N'utilise
  jamais `shell=True` sur une donnée non maîtrisée.

## Terminologie imposée

Le propriétaire du projet impose le mot **« Dashboard »**. N'écris jamais
« Poste de commandement », nulle part.

## Identité visuelle

Le logo est un **trou noir**, d'après `docs/identite/reference-trou-noir.png`,
qui est la référence normative. Trois supports doivent rester le même objet :
le `<symbol id="blackhole">` de `gui/web/index.html`, `gui/web/favicon.svg`, et
le rendu Pillow de `packaging/make_icon.py`. Si tu touches à l'un, mets les
trois à jour.

## Licence et emprunts

Ce dépôt n'intègre **aucun code tiers sous licence non commerciale**. Une
fusion avec un assistant tiers sous Creative Commons BY-NC 4.0 a été
explicitement écartée par le propriétaire : elle aurait rendu le produit non
commercial à vie. Avant tout emprunt de code, vérifie la licence et signale-la.
En cas de doute, réécris.

## Documents à lire avant de contribuer

| Fichier | Contenu |
|---|---|
| `gui/API_CONTRACT.md` | contrat gelé de l'interface web |
| `docs/ASSISTANT-CONTRAT.md` | contrat gelé de la couche assistant |
| `docs/CONCEPTION-V2.md` | décisions d'architecture |
| `packaging/README.md` | construction de l'installeur Windows |

## Ce qu'il ne faut pas faire

- Ne pousse pas sur `main` sans que la suite soit verte.
- N'ouvre pas de *pull request* sans qu'on te l'ait demandé.
- Ne désactive pas un test pour faire passer la suite. Corrige la cause.
- Ne convertis pas les commentaires en anglais.
