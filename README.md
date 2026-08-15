# ANTI-ZEEVIRIUS — Antivirus et optimiseur Windows

Projet Python combinant 3 couches de détection antivirus, une quarantaine, une
surveillance temps réel, et un ensemble d'outils d'optimisation, de rangement
et de désencombrement — 30 options au total dans le menu.

## Architecture

```
anti-zeevirius/
├── main.py                       # Point d'entrée — menu CLI (30 options)
├── requirements.txt
├── requirements-dev.txt          # + pytest, pour lancer la suite de tests
├── tests/                        # Suite de tests pytest (voir section dédiée)
├── gui/                          # Interface web locale — EN COURS (section dédiée)
│   ├── API_CONTRACT.md           # Contrat d'API figé (backend ↔ frontend)
│   ├── server.py                 # Serveur local stdlib 127.0.0.1:8777 — en cours
│   └── web/                      # Front statique HTML/CSS/JS, sans CDN — en cours
├── scanner/
│   ├── hash_scanner.py           # Détection par signature SHA-256
│   ├── yara_scanner.py           # Détection par règles YARA (patterns)
│   └── heuristics.py             # Analyse comportementale (entropie, PE, extensions)
├── monitor/
│   └── realtime_monitor.py       # Surveillance temps réel (watchdog)
├── quarantine/
│   └── quarantine_manager.py     # Isolation + restauration des fichiers
├── optimizer/
│   ├── temp_cleaner.py           # Nettoyage Temp/%temp%/cache/corbeille (7)
│   ├── startup_manager.py        # Gestion des programmes au démarrage (8)
│   ├── disk_analyzer.py          # Gros fichiers et doublons (9)
│   ├── task_scheduler.py         # Tâches Windows planifiées (10, 23, 24)
│   ├── file_triage.py            # Tri + zone tampon partagée (11, 12)
│   ├── ransomware_shield.py      # Fichiers canari + détection de rafale (13)
│   ├── reputation_checker.py     # Réputation cloud via API VirusTotal (14)
│   ├── phishing_link_checker.py  # Vérification anti-phishing des liens (15)
│   ├── folder_organizer.py       # Réorganisation de dossiers + undo (16 à 21)
│   ├── guardian.py               # MODE GARDIEN, orchestrateur un clic (22 à 24)
│   ├── app_manager.py            # Applications installées / bloatware (25 à 27)
│   └── residue_cleaner.py        # Résidus d'applications désinstallées (28 à 30)
├── signatures/
│   ├── malicious_hashes.txt      # Base de hashes connus (à enrichir)
│   └── rules.yar                 # Règles YARA (auto-générées au 1er lancement)
└── logs/                         # Journaux datés (scan_YYYYMMDD.log)
```

Dossiers créés automatiquement au premier usage (jamais versionnés, jamais
scannés par l'outil lui-même) : `quarantine_storage/` (fichiers isolés),
`triage_staging/` (zone tampon partagée par les options 11, 22, 28 et 30),
`organizer_logs/` (journal d'annulation des réorganisations) et `cache/`
(cache VirusTotal et liste anti-phishing).

## Modules d'optimisation — vue d'ensemble

| Fonction | Ce qu'elle fait | Droits requis |
|---|---|---|
| **Nettoyage complet** | Vide `%TEMP%`, corbeille, cache miniatures, cache Chrome/Edge/Firefox. Avec droits admin : + `C:\Windows\Temp`, Prefetch, cache Windows Update | Utilisateur standard (partiel) / Admin (complet) |
| **Gestion démarrage** | Liste les programmes au démarrage (registre + dossier Démarrage), signale ceux couramment désactivables, permet de les désactiver de façon **réversible** | Admin pour les entrées HKLM |
| **Analyse disque** | Top gros fichiers/dossiers + détection de doublons (par hash) | Aucun |
| **Planification** | Crée une tâche Windows native (`schtasks`) pour un nettoyage hebdomadaire automatique | Admin recommandé |
| **Réorganisation de dossiers** | Range un dossier par catégorie, par application associée ou par niveau d'importance ; déplace un dossier dans un autre ; isole les fichiers les moins utilisés. **Uniquement des déplacements, tous annulables** | Utilisateur standard |
| **Mode Gardien** | Orchestre en une action : nettoyage + mise de côté + scan antivirus + rangement + protection continue. N'ajoute aucune logique de suppression | Admin pour le nettoyage complet |
| **Gestion des applications** | Liste/trie les applications installées (Win32 + UWP), désinstalle via le désinstalleur natif, retire le bloatware Microsoft connu en lot | Selon l'application (UAC) |
| **Résidus** | Raccourcis orphelins, clés de registre `Uninstall` orphelines, dossiers orphelins de `Program Files`/`AppData` | Admin pour HKLM et `Program Files` |

### Lancer en administrateur (recommandé pour un nettoyage complet)

1. Ouvre une invite de commandes **en administrateur** (clic droit → "Exécuter en tant qu'administrateur")
2. Navigue jusqu'au dossier : `cd chemin\vers\anti-zeevirius`
3. Lance : `python main.py`

### Sécurité des opérations de désactivation

- Le nettoyage Temp ne supprime que le **contenu** de dossiers de cache — jamais les dossiers eux-mêmes, et ignore silencieusement tout fichier verrouillé (même logique que le message "Accès refusé" que tu as rencontré dans l'Explorateur).
- La désactivation d'un programme au démarrage **déplace** l'entrée vers une clé de sauvegarde réversible plutôt que de la supprimer — restaurable à tout moment.

## Installation

```bash
cd anti-zeevirius
pip install -r requirements.txt
```

**Note sur `yara-python` sous Windows** : l'installation via pip fournit
un wheel précompilé pour Windows (pas besoin de compiler YARA vous-même).
Si l'installation échoue, vérifiez que vous êtes en Python 64 bits.

## Utilisation

```bash
python main.py
```

Le menu expose **30 options**. Attention à l'**ordre d'affichage** : le bloc
MODE GARDIEN (options 22 à 24) est volontairement présenté **en premier**,
avant l'option 1, parce que c'est l'entrée « un clic » du programme. La
numérotation, elle, est celle du tableau ci-dessous.

**Protection et quarantaine**

| # | Option | Ce qu'elle fait |
|---|---|---|
| 1 | Scanner un fichier | Analyse ponctuelle (hash + YARA + heuristique) |
| 2 | Scanner un dossier | Scan récursif complet (parallélisé) |
| 3 | Protection temps réel | Surveille Téléchargements/Bureau (ou des dossiers personnalisés) et scanne tout nouveau fichier |
| 4 | Voir la quarantaine | Liste des fichiers isolés |
| 5 | Restaurer | En cas de faux positif |
| 6 | Suppression définitive | Purge un fichier en quarantaine |

**Optimisation** *(détails : § Modules d'optimisation et § Comment fonctionne le tri)*

| # | Option | Ce qu'elle fait |
|---|---|---|
| 7 | Nettoyage complet | Temp, %temp%, caches navigateurs, corbeille, miniatures |
| 8 | Gérer le démarrage | Liste et désactive (de façon réversible) les programmes au démarrage |
| 9 | Analyser le disque | Gros fichiers, gros dossiers, doublons |
| 10 | Planifier un nettoyage | Tâche Windows hebdomadaire (`AntiZeevirius_AutoCleanup`) |
| 11 | Trier les fichiers d'un dossier | Classifie (sûr / à vérifier / jamais touché) et demande confirmation avant toute mise de côté |
| 12 | Voir / restaurer les fichiers mis de côté | Récupère un fichier trié par erreur, ou purge définitivement après 30 jours |

**Fonctionnalités premium** *(détails : § Fonctionnalités premium)*

| # | Option | Ce qu'elle fait |
|---|---|---|
| 13 | Bouclier anti-ransomware | Fichiers canari + détection de rafale de modifications |
| 14 | Réputation d'un fichier | Interrogation de l'API VirusTotal |
| 15 | Vérifier un lien | Anti-phishing avant de cliquer |

**Organisation des fichiers** *(détails : § Organisation des fichiers)*

| # | Option | Ce qu'elle fait |
|---|---|---|
| 16 | Réorganiser par catégorie | Documents, Images, Vidéos, Code… |
| 17 | Réorganiser par application associée | Word, Photoshop, VLC… |
| 18 | Réorganiser par niveau d'importance | Actif récent / Important / Archive / À purger |
| 19 | Déplacer un dossier entier dans un autre | Avec fusion en cas de conflit de nom |
| 20 | Ranger les fichiers les moins utilisés | Dans `00_Non_utilises_depuis_longtemps` |
| 21 | Annuler une réorganisation | Restaure toute une session à l'identique |

**Mode Gardien** *(détails : § MODE GARDIEN)*

| # | Option | Ce qu'elle fait |
|---|---|---|
| 22 | Activer le Mode Gardien maintenant | Passe complète, suppression définitive validée à la fin |
| 23 | Planifier le Mode Gardien chaque jour | Tâche Windows quotidienne (`AntiZeevirius_GuardianDaily`) |
| 24 | Désactiver le Mode Gardien quotidien | Supprime cette tâche |

**Gestion des applications** *(détails : § Gestion des applications installées)*

| # | Option | Ce qu'elle fait |
|---|---|---|
| 25 | Voir / trier les applications installées | Par taille, nom, ou bloatware en premier |
| 26 | Désinstaller une application | Désinstalleur natif (Win32) ou `Remove-AppxPackage` (UWP) |
| 27 | Retirer le bloatware Microsoft connu | En lot, UWP uniquement, avec confirmation |

**Résidus d'applications désinstallées** *(détails : § Résidus d'applications désinstallées)*

| # | Option | Ce qu'elle fait |
|---|---|---|
| 28 | Raccourcis orphelins | Bureau et Menu Démarrer, cible manquante |
| 29 | Entrées de registre orphelines | Clés `Uninstall` dont le chemin n'existe plus |
| 30 | Dossiers orphelins | `Program Files` / `AppData` sans application correspondante |

Deux modes non interactifs sont réservés aux tâches planifiées :
`python main.py --auto-clean` (nettoyage TEMP seul, option 10) et
`python main.py --guardian` (passe complète du Mode Gardien, option 23).

### Comment fonctionne le tri (option 11)

Chaque fichier est classé selon 3 niveaux, **jamais mélangés** :

| Catégorie | Exemples | Action |
|---|---|---|
| **Jamais touché** | `.docx`, `.jpg`, `.py`, dossiers Documents/Bureau/Photos, dossiers système | Non proposé, même pas affiché |
| **À vérifier** | Vieux installeurs `.exe`/`.msi` (>6 mois), gros fichiers inactifs, doublons | Proposé avec avertissement explicite |
| **Sûr à supprimer** | `.tmp`, `.log`, `.bak`, `.old`, `.dmp` | Recommandé, confirmation quand même requise |

**Aucune suppression n'est jamais définitive au moment du tri** : les fichiers validés sont déplacés vers un dossier tampon (`triage_staging/`) et restaurables via l'option 12 tant qu'ils ne sont pas purgés explicitement (après 30 jours minimum, et uniquement sur confirmation manuelle).

## Fonctionnalités premium (inspirées de Bitdefender / Kaspersky)

D'après les comparatifs 2026, Bitdefender Total Security est la référence du marché (100% détection AV-Test), suivi de Kaspersky et Norton. Voici ce qui a été repris et ce qui ne peut honnêtement pas l'être.

### 13. Bouclier anti-ransomware (équivalent "Ransomware Remediation")

- **Fichiers canari** : des leurres invisibles sont déposés dans Documents/Bureau/Images. Un ransomware qui chiffre "tout" un dossier les touche en premier — c'est le signal d'alerte précoce.
- **Détection de taux de modification** : le seuil appliqué est
  `max(plancher empirique, μ + t·σ)`. Le plancher, jamais désactivé, vaut
  **15 fichiers modifiés en moins de 10 secondes** (`MASS_MODIFICATION_THRESHOLD`
  et `MASS_MODIFICATION_WINDOW_SECONDS`). Au-delà de 30 fenêtres observées
  (`BASELINE_MIN_SAMPLES`), une couche adaptative calibrée sur l'activité
  réelle de la machine (moyenne et variance en ligne par l'algorithme de
  Welford, puis borne de Cantelli pour un taux de fausses alertes cible de
  10⁻³) peut **relever** ce seuil — jamais l'abaisser.
- **Identification du processus responsable** : liste les processus qui écrivent le plus intensément sur disque (via `psutil`), pour repérer le coupable pendant une attaque en cours.

**Limite honnête** : ceci reste une détection en espace utilisateur, réactive (après les premières écritures), pas un driver kernel-mode qui intercepte AVANT écriture comme le font Bitdefender/Kaspersky. Elle limite les dégâts, elle ne les empêche pas à 100%.

### 14. Réputation cloud (équivalent détection cloud propriétaire)

Interroge l'API **VirusTotal** (agrège 70+ moteurs antivirus) pour la réputation d'un hash de fichier.

**Configuration requise** (gratuite) :
1. Crée un compte : https://www.virustotal.com/gui/join-us
2. Récupère ta clé API : https://www.virustotal.com/gui/my-apikey
3. Colle-la dans `signatures/vt_api_key.txt`

**Limite honnête** : le tier gratuit est plafonné à 500 requêtes/jour et 4/minute — largement suffisant pour vérifier ponctuellement un fichier douteux, mais pas pour scanner tout un disque. Bitdefender/Kaspersky n'ont pas cette limite pour leurs clients car ils opèrent leur propre infrastructure cloud alimentée par des centaines de millions de machines.

### 15. Anti-phishing des liens (équivalent "Chat/Web Protection")

Vérifie une URL avant que tu cliques dessus : liste noire publique **OpenPhish** + heuristiques locales (typosquatting bancaire, TLD gratuits suspects comme `.tk`/`.ml`, IP brute au lieu d'un domaine).

**Limite honnête** : un flux public gratuit est solide mais moins exhaustif et moins réactif que les flux propriétaires + télémétrie utilisateur en temps réel des suites commerciales.

### Ce qui N'A PAS pu être reproduit (transparence totale)

| Fonctionnalité Bitdefender/Norton | Pourquoi c'est hors de portée d'un projet perso |
|---|---|
| Driver kernel-mode (interception avant écriture disque) | Nécessite une signature de driver Microsoft (WHQL), un processus de certification et une expertise noyau Windows poussée |
| Moteur cloud propriétaire (500M+ machines) | Nécessite une infrastructure mondiale et une base d'utilisateurs — impossible à répliquer seul |
| VPN illimité, gestionnaire de mots de passe, backup cloud | Hors du périmètre "antivirus" — ce sont des produits à part entière |
| Sandbox de détonation automatique | Nécessite une infrastructure de virtualisation dédiée et sécurisée |

## Organisation des fichiers (options 16 à 21)

Module : `optimizer/folder_organizer.py`.

**Principe central** : ce module ne supprime **jamais** rien — il ne fait que
**déplacer**. Chaque déplacement est journalisé fichier par fichier dans
`organizer_logs/reorg_index.json`, regroupé par identifiant de session, et
l'option 21 remet toute une session à son emplacement d'origine.

| # | Option | Ce qu'elle fait | Droits requis |
|---|---|---|---|
| **16** | Réorganiser par **catégorie** | Range chaque fichier dans un sous-dossier créé *à l'intérieur du dossier analysé*, d'après son extension : `01_Documents`, `02_Feuilles_de_calcul`, `03_Presentations`, `04_PDF`, `05_Images`, `06_Videos`, `07_Audio`, `08_Archives`, `09_Code_Developpement`, `10_Executables_Installateurs`, et `11_Divers` pour tout le reste | Utilisateur standard |
| **17** | Réorganiser par **application associée** | Même mécanique, mais le sous-dossier porte le nom de l'application qui ouvre le fichier (`Microsoft Word`, `Adobe Photoshop`, `Lecteur vidéo (VLC)`…). La table extension → application (`APPLICATION_MAP`) est **statique** : le module ne lit pas les associations de fichiers réelles de Windows, le résultat ne dépend donc pas de ce qui est installé. Extensions inconnues → `Application générique / inconnue` | Utilisateur standard |
| **18** | Réorganiser par **niveau d'importance** | `01_Actif_Recent` (≤ 30 j), `02_Important` (31–180 j), `03_Archive` (> 180 j), `04_A_Purger` (extensions techniques jetables : `.tmp .temp .log .bak .old .dmp .chk .~`). « À purger » est un **dossier de rangement**, pas une suppression : rien n'y est effacé | Utilisateur standard |
| **19** | **Déplacer un dossier entier** dans un autre | `move_folder_into()` : déplace `Source` *à l'intérieur* de `Cible` (→ `Cible\Source`). Si un dossier du même nom existe déjà à destination, **fusion fichier par fichier** au lieu d'un écrasement, les sous-dossiers vidés étant supprimés au passage. Un dossier ne peut pas être déplacé dans lui-même ou dans un de ses sous-dossiers (refusé, rien ne bouge) | Utilisateur standard sur ses propres dossiers |
| **20** | Ranger les fichiers **les moins utilisés** | Liste les fichiers non utilisés depuis N jours (180 par défaut, saisissable), puis — après confirmation — les déplace dans le sous-dossier `00_Non_utilises_depuis_longtemps` en **conservant leur arborescence relative** | Utilisateur standard |
| **21** | **Annuler** une réorganisation | Liste les sessions enregistrées (id, date, nombre de déplacements, combien sont déjà annulés) et remet chaque fichier d'une session choisie à son emplacement d'origine, dans l'ordre inverse des déplacements | Utilisateur standard |

### Garde-fous des options 16 à 21

- **Aperçu obligatoire avant toute action (16-18).** `build_plan()` ne modifie
  rien : il retourne la liste des déplacements envisagés. Le menu affiche les
  30 premiers, le total, et n'applique le plan (`apply_plan()`) que si tu tapes
  exactement `oui`. Idem pour les options 19 et 20, qui affichent ce qu'elles
  s'apprêtent à faire avant de demander confirmation.
- **Extensions jamais déplacées** (`NEVER_MOVE_EXTENSIONS`) : `.dll`, `.sys`,
  `.ini`, `.config`, `.lnk` — sortir une DLL de son dossier d'installation
  casserait l'application concernée. S'applique aux options 16-18 et 20.
- **Dossiers jamais traversés** (`NEVER_ENTER_FOLDER_KEYWORDS`) : tout chemin
  contenant `windows`, `program files`, `programdata`, `$recycle.bin`, `.git`,
  `node_modules`, `__pycache__`, `system volume information` est ignoré, y
  compris pendant le parcours récursif. Le test porte sur le **chemin complet
  en minuscules**, donc un sous-dossier nommé `node_modules` est écarté où
  qu'il se trouve.
- **Dossiers internes de l'outil exclus (16-18)** : `quarantine_storage/`,
  `logs/`, `cache/`, `triage_staging/`, `organizer_logs/` sont passés en
  exclusion explicite par `main.py`, pour que l'outil ne se range pas
  lui-même s'il est installé dans le dossier analysé.
- **Aucun écrasement possible.** Si un fichier du même nom existe déjà à
  destination, le nouveau est renommé ` (2)`, ` (3)`… — l'existant n'est
  jamais remplacé.
- **Idempotence.** Un fichier déjà rangé dans un dossier généré par un passage
  précédent (préfixe strict `NN_`, deux chiffres + underscore) n'est pas
  re-traité. La regex est volontairement stricte pour ne pas confondre un
  dossier généré avec un vrai dossier utilisateur du type `2024_Rapports`.
- **Tolérance aux erreurs.** Fichier verrouillé, permission refusée, lien
  symbolique cassé : l'élément est ignoré, la liste des erreurs est retournée
  et affichée, l'opération continue sur le reste.
- **Journal robuste.** Écriture atomique du journal (`.tmp` + `os.replace`) et
  verrou en mémoire, comme pour la quarantaine et le tri (option 11).

### Ce qui n'est PAS protégé (à savoir avant de lancer)

- L'**option 19 déplace tout le contenu du dossier**, y compris les extensions
  de la liste `NEVER_MOVE_EXTENSIONS` : ce filtre s'applique aux options
  16-18 et 20, pas au déplacement d'un dossier entier. Déplacer un dossier
  d'installation d'application avec l'option 19 cassera donc l'application —
  c'est un déplacement de dossier, pas un désinstalleur (voir options 25-27).
- L'**option 20 ne reçoit pas la liste d'exclusion des dossiers internes** de
  l'outil (contrairement aux options 16-18). Évite de la pointer sur le
  dossier d'installation d'ANTI-ZEEVIRIUS lui-même.
- L'annulation (option 21) **dépend entièrement de
  `organizer_logs/reorg_index.json`** : ce fichier supprimé, plus aucune
  session n'est annulable automatiquement.
- L'annulation restaure les **fichiers**, pas les dossiers d'origine vidés
  puis supprimés lors d'une fusion (option 19) — les fichiers reviennent bien
  à leur chemin d'origine, l'arborescence est recréée au besoin.

### Limite honnête — « le fichier le moins utilisé » (options 18 et 20)

Depuis Windows Vista, le suivi NTFS de la **date de dernier accès** est
désactivé par défaut (`NtfsDisableLastAccessUpdate=1`). Sur ces machines,
`st_atime` vaut de fait `st_mtime` : « le moins **utilisé** » devient « le
moins **modifié** », ce qui n'est pas la même chose — un PDF relu chaque
semaine mais jamais modifié paraîtra inutilisé.

Le module traite ce problème sans le masquer :

1. Il **échantillonne** les 50 premiers fichiers rencontrés pour déterminer si
   le suivi du dernier accès semble actif (si ≥ 90 % des fichiers ont
   `atime == mtime`, il le considère désactivé).
2. Il croise avec les **Éléments récents de Windows**
   (`%APPDATA%\Microsoft\Windows\Recent`) : la date de modification de chaque
   raccourci `.lnk` y est un signal d'ouverture **réelle**, indépendant du
   réglage NTFS. Cette lecture nécessite `pywin32` ; sans lui, l'index est
   simplement vide (dégradation silencieuse).
3. Il **affiche explicitement** dans le résultat (`note`) laquelle des trois
   situations s'applique, y compris l'avertissement « cette liste est basée
   sur la date de dernière MODIFICATION » quand c'est le cas.

Ce signal reste **complémentaire** : un fichier lu par un script, ou par une
application qui n'alimente pas les Éléments récents, n'y apparaîtra pas.
L'option 20 propose de rafraîchir cet index avant l'analyse.

## MODE GARDIEN (options 22 à 24)

Module : `optimizer/guardian.py`. C'est l'entrée « un clic » du programme —
c'est pourquoi le menu l'affiche **en premier**, avant l'option 1.

Le Mode Gardien **n'ajoute aucune logique de suppression** : il orchestre des
briques déjà existantes (`temp_cleaner`, `disk_analyzer`, `file_triage`,
`folder_organizer`, `quarantine_manager`, `ransomware_shield`,
`realtime_monitor`), chacune restant individuellement réversible.

| # | Option | Ce qu'elle fait | Droits requis |
|---|---|---|---|
| **22** | Activer le Mode Gardien **maintenant** | Passe complète en 3 étapes, décrites ci-dessous | Admin pour un nettoyage TEMP complet ; le reste fonctionne en utilisateur standard |
| **23** | **Planifier** le Mode Gardien chaque jour | Crée la tâche Windows `AntiZeevirius_GuardianDaily` via `schtasks` (`/SC DAILY`, heure au choix, `09:00` par défaut) qui relance `main.py --guardian` sans interface | Admin recommandé |
| **24** | **Désactiver** la planification quotidienne | Supprime cette tâche (`schtasks /Delete`) | Admin recommandé |

La tâche du Mode Gardien est **distincte** de la tâche de nettoyage
hebdomadaire de l'option 10 (`AntiZeevirius_AutoCleanup`) : les deux peuvent
coexister, et planifier l'une ne modifie jamais silencieusement l'autre.

### Ce que fait exactement l'option 22

1. **Passe de maintenance** (`run_full_pass`) :
   nettoyage TEMP / `%temp%` / caches navigateurs / corbeille → mise de côté
   des fichiers « sûrs à supprimer » → scan antivirus de Téléchargements,
   Bureau et Documents (quarantaine automatique des menaces) → rangement des
   fichiers non utilisés depuis plus de **180 jours**.
2. **Protection continue** : déploiement du bouclier anti-ransomware et
   démarrage de la surveillance temps réel **en arrière-plan** (le menu reste
   utilisable).
3. **Validation avant suppression définitive** : affichage de tout ce qui est
   actuellement en attente (y compris les mises de côté antérieures) et, seulement
   si tu tapes `oui`, purge définitive de ce qui est en attente depuis plus de
   30 jours. Répondre autre chose ne supprime rien.

### Garde-fous du Mode Gardien

- **Aucune suppression définitive automatique.** `confirm_permanent_deletion()`
  est la seule méthode du module qui efface réellement des données ; elle n'est
  appelée ni par `run_full_pass()`, ni par `run_unattended()` — uniquement
  depuis le menu interactif après un `oui` explicite.
- **Seuls les fichiers « sûrs » sont mis de côté.** La catégorie « à vérifier »
  du tri (`caution` : vieux installeurs, gros fichiers inactifs, doublons)
  n'est **jamais** touchée automatiquement, même en Mode Gardien : elle est
  seulement comptée et renvoyée vers l'examen manuel de l'option 11.
- **Mise de côté ≠ suppression.** Les fichiers partent dans le même dossier
  tampon `triage_staging/` que l'option 11 et restent restaurables via
  l'option 12.
- **Seuil plus prudent en automatique.** La passe planifiée (option 23) utilise
  **365 jours** d'inutilisation au lieu de 180 : personne ne supervise
  l'exécution, un seuil agressif y serait plus risqué. Tout est journalisé
  dans `logs/` plutôt qu'affiché.
- **Dossiers par défaut** : `~/Downloads`, `~/Desktop`, `~/Documents`. Ceux qui
  n'existent pas sont ignorés silencieusement.

## Gestion des applications installées (options 25 à 27)

Module : `optimizer/app_manager.py`. Équivalent de la page Windows
*Paramètres > Applications*, avec en plus le tri par taille et la détection du
bloatware connu.

| # | Option | Ce qu'elle fait | Droits requis |
|---|---|---|---|
| **25** | **Lister / trier** les applications | Combine les applications Win32 (clés de registre `Uninstall` : HKLM 64 bits, HKLM `WOW6432Node`, HKCU) et les paquets UWP/Store (`Get-AppxPackage` via PowerShell). Tri au choix : taille décroissante (défaut), nom, ou bloatware connu en premier | Lecture seule — utilisateur standard |
| **26** | **Désinstaller** une application | Win32 → lance le **désinstalleur natif de l'éditeur** (`UninstallString` du registre), aucun argument silencieux n'est deviné, un assistant peut s'ouvrir. UWP → `Remove-AppxPackage`, silencieux | L'assistant Win32 demande lui-même l'élévation (UAC) si nécessaire ; UWP s'applique au compte courant |
| **27** | Retirer le **bloatware Microsoft connu** en lot | Ne traite **que les paquets UWP/Store** figurant dans la liste `KNOWN_BLOATWARE_PACKAGES` (38 entrées : Xbox Game Bar, Solitaire, Cartes, Actualités/Météo Bing, Skype Store, 3D Builder, Clipchamp, Cortana…), après affichage nominatif et confirmation | Compte courant |

### Garde-fous des options 25 à 27

- **Jamais de désinstallation sans confirmation explicite** (`oui` tapé). Le
  tri « bloatware en premier » ne fait que **proposer un ordre**, il n'agit pas.
- **Jamais de lot pour les applications Win32.** L'option 27 est volontairement
  limitée aux UWP : enchaîner plusieurs assistants de désinstallation
  graphiques serait confus et risqué. `Remove-AppxPackage`, lui, est fiable et
  silencieux.
- **Le désinstalleur de l'éditeur est toujours préféré** à une suppression de
  fichiers maison : lui seul sait nettoyer ses clés de registre, services,
  tâches planifiées et raccourcis.
- **Composants système exclus dès la détection**, par défense en profondeur —
  trois filtres cumulés :
  - Win32 : toute clé marquée `SystemComponent = 1` est ignorée ;
  - UWP : `Get-AppxPackage` est filtré sur `-not IsFramework -and -not NonRemovable` ;
  - UWP : tout paquet dont le nom contient un mot-clé de
    `NEVER_REMOVE_PACKAGE_KEYWORDS` (`sechealthui`, `windowsstore`, `vclibs`,
    `net.native`, `ui.xaml`, `desktopappinstaller`, `accountscontrol`,
    `lockapp`, `shellexperiencehost`, `startmenuexperiencehost`,
    `securityhealth`, `windows.search`, `windows.photos`, `appinstaller`…)
    n'est **même pas affiché** comme candidat.
- **La liste de bloatware est volontairement prudente** : mieux vaut rater un
  candidat que proposer par erreur un composant utile. Elle est explicite dans
  le code, chaque entrée porte sa justification en français, affichée avant
  confirmation.
- **Le Mode Gardien ne désinstalle jamais rien** : ces options ne sont
  accessibles que manuellement.

### Limites honnêtes (25-27)

- **Une désinstallation n'est pas réversible** par ANTI-ZEEVIRIUS : c'est la
  seule famille d'opérations de l'outil qui ne passe pas par une zone tampon.
  Réinstaller l'application est le seul retour en arrière possible.
- La taille (`EstimatedSize`) n'est disponible que pour les applications
  Win32 ; les paquets UWP sont affichés `UWP/Store` sans taille.
- Windows uniquement : sans `winreg` la liste Win32 est vide, et sans
  PowerShell la liste UWP l'est aussi — l'outil ne plante pas, il ne trouve
  simplement rien.

## Résidus d'applications désinstallées (options 28 à 30)

Module : `optimizer/residue_cleaner.py`. Cible ce que laissent derrière elles
les applications supprimées à la main ou par un désinstalleur incomplet.

| # | Option | Ce qu'elle fait | Droits requis |
|---|---|---|---|
| **28** | **Raccourcis orphelins** | Parcourt le Bureau et les Menus Démarrer (utilisateur et commun) à la recherche des `.lnk` dont la cible n'existe plus, puis — après confirmation — les met de côté | Admin pour le Menu Démarrer commun (`%PROGRAMDATA%`) ; le reste en utilisateur standard. Nécessite `pywin32` |
| **29** | **Entrées de registre orphelines** | Liste les clés `Uninstall` (HKLM 64/32 bits, HKCU) dont l'`InstallLocation` référencé n'existe plus. Suppression possible une par une ou en lot, après confirmation | Admin pour les clés HKLM |
| **30** | **Dossiers orphelins** | Diagnostic des dossiers de premier niveau de `Program Files`, `Program Files (x86)`, `ProgramData`, `%LOCALAPPDATA%` et `%APPDATA%` qui ne correspondent à aucune application installée | Admin pour mettre de côté un dossier de `Program Files` / `ProgramData` |

### Garde-fous des options 28 à 30

- **Rien n'est supprimé directement.** Raccourcis et dossiers passent par la
  **même** zone tampon `triage_staging/` que l'option 11, avec le même écran de
  restauration (option 12) et le même délai de rétention. Une seule zone
  tampon pour tout l'outil, plus simple à superviser.
- **Les entrées de registre sont sauvegardées avant suppression.** Toutes les
  valeurs de la clé orpheline sont recopiées sous
  `HKCU\Software\AntiZeevirius\OrphanedUninstallBackup\<HIVE>_<clé>` avant que
  l'originale ne soit supprimée — restaurable manuellement via Regedit.
- **Une entrée de registre incomplète n'est jamais jugée.** Sans `DisplayName`
  **et** sans `InstallLocation`, l'entrée est laissée telle quelle : pas assez
  d'information pour décider sans risque.
- **La détection de dossiers orphelins (option 30) est triplement prudente.**
  Un dossier n'est proposé que si :
  1. aucune application installée ne lui correspond (recherche floue par
     sous-chaîne, sur les noms **et** les éditeurs de toutes les applications
     détectées à l'option 25) ;
  2. il n'a pas été modifié depuis au moins **30 jours**
     (`ORPHAN_MIN_AGE_DAYS`) ;
  3. son nom n'est pas dans `PROTECTED_FOLDER_NAMES` — `common files`,
     `microsoft`, `packages` (données UWP), `windowsapps`, `windows nt`,
     `windows defender`, `windows security`, `google`, `mozilla firefox`,
     `installer`, `temp`, `crashdumps`, `diagnostics`, `d3dscache`… soit des
     composants partagés et des données qui ne sont pas des dossiers
     d'application.
- **Validation individuelle obligatoire pour les dossiers.** Contrairement aux
  raccourcis et aux clés de registre, **chaque dossier doit être validé un par
  un** : le menu ne propose aucune sélection groupée pour cette catégorie, car
  c'est la seule où une erreur ferait perdre de vraies données.
- La recherche floue est volontairement **permissive dans le sens sûr** : en cas
  de doute, le dossier est considéré comme appartenant à une application
  installée et n'est donc pas proposé.

### Limites honnêtes (28-30)

- Sans `pywin32`, l'option 28 ne retourne **rien** (impossible de résoudre la
  cible d'un `.lnk`) — ce n'est pas un « aucun résidu trouvé », c'est une
  capacité absente. Le menu le signale.
- L'option 30 calcule la taille et l'âge en parcourant récursivement chaque
  dossier candidat : sur un `Program Files` fourni, l'analyse peut être longue.
- Les dossiers illisibles (permissions) sont ignorés silencieusement : ils
  n'apparaîtront pas dans les candidats.
- Le rapprochement « dossier ↔ application installée » se fait sur des noms,
  pas sur un inventaire de fichiers : un éditeur qui nomme son dossier
  différemment de son application peut apparaître à tort dans les candidats.
  D'où la validation individuelle.

## Interface web locale (en cours de construction)

Une interface graphique servie dans le navigateur est en cours de
développement dans `gui/`, pour remplacer la navigation au clavier dans un
menu à 30 entrées. Sa spécification est **figée** dans
[`gui/API_CONTRACT.md`](gui/API_CONTRACT.md) : backend et frontend sont écrits
en parallèle contre ce contrat.

Lancement (une fois le module disponible) :

```bash
python -m gui.server        # http://127.0.0.1:8777
python -m gui.server --port 9000
```

Ce qui est **spécifié par le contrat** :

| Principe | Détail |
|---|---|
| **Zéro nouvelle dépendance** | Backend `http.server` (stdlib) ; frontend HTML/CSS/JS purs, sans framework ni CDN — l'outil doit fonctionner hors ligne, sur une machine potentiellement infectée |
| **Écoute locale uniquement** | Bind sur `127.0.0.1` exclusivement, jamais `0.0.0.0`. Port 8777 par défaut, `--port` pour en changer |
| **Jeton de session obligatoire** | Généré par `secrets.token_urlsafe(32)` au démarrage et injecté dans la page servie ; toute requête `/api/*` sans en-tête `X-AZ-Token` valide reçoit un `403`. Protège contre un autre processus local |
| **Double validation avant toute action destructive** | En deux temps : `dry_run: true` (valeur par défaut) retourne le plan sans rien toucher et fournit un `confirm_token` ; l'exécution réelle exige `dry_run: false` **et** ce jeton, à **usage unique**, valable **5 minutes** |
| **Opérations longues asynchrones** | Scan de dossier, analyse disque, tri, liste des applications renvoient un `job_id`, interrogeable via `GET /api/job?id=…` (état, progression, fichier en cours) et annulable via `POST /api/job_cancel` |
| **Dégradation propre hors Windows** | Si `winreg`, `pywin32` ou `schtasks` sont absents, l'API répond `{"ok": false, "unavailable": true, "reason": "…"}` avec un HTTP 200 : aucune action ne plante, et l'interface reste entièrement navigable sous Linux/macOS |

Le contrat définit une action `POST /api/<action>` par fonction du menu CLI —
les 30 options y sont couvertes, chacune marquée destructive ou non — plus une
action `status` qui alimente le tableau de bord (plateforme, droits admin,
modules disponibles et raison d'indisponibilité, compteurs de quarantaine et de
zone tampon, état du temps réel et du bouclier, configuration VirusTotal).

**État honnête** : le contrat est figé et fait référence ; le serveur et le
frontend sont en cours d'écriture au moment de la rédaction. Tant que ce n'est
pas terminé, `python main.py` (menu CLI) reste le point d'entrée complet et
supporté. Ce README ne décrit délibérément aucun détail d'apparence de
l'interface, qui n'est pas spécifié par le contrat.

## Les 3 couches de détection

| Couche | Fichier | Détecte quoi | Limite |
|---|---|---|---|
| **Signature** | `hash_scanner.py` | Fichiers identiques à un malware déjà répertorié | Inefficace sur variantes/inconnus |
| **YARA** | `yara_scanner.py` | Patterns/familles de malware, commandes suspectes | Nécessite des règles à jour |
| **Heuristique** | `heuristics.py` | Comportements suspects (entropie, non-signé, double extension, ratio de chaînes lisibles) | Peut générer des faux positifs |

## Optimisations mathématiques et de performance (audit)

Un audit a passé en revue les séquences de calcul et les parcours de
fichiers du projet pour appliquer un regard mathématique/algorithmique
rigoureux (complexité, statistiques) plutôt que des micro-optimisations
ad hoc. Sept points ont été traités, tous couverts par la suite de tests
(`tests/`, voir section dédiée) :

| # | Fichier | Avant | Après | Gain |
|---|---|---|---|---|
| 1 | `scanner/heuristics.py` — `_shannon_entropy()` | Boucle Python octet par octet | Histogramme vectorisé `np.bincount` (repli pur Python auto si numpy absent) | ~50-100× sur les grosses sections PE |
| 2 | `scanner/heuristics.py` — `_printable_string_ratio()` | Technique #4 documentée mais absente du code | Implémentée : détection de runs vectorisée (`np.diff` sur un masque booléen), seuil calibré par simulation | Nouvelle détection de packers |
| 3 | `optimizer/disk_analyzer.py` — `analyze_disk()` | 3 parcours `rglob()` indépendants du même arbre + tri complet | 1 seul `os.walk()` + tas borné `heapq` pour le top-N | I/O disque ÷3, tri `O(N log N)` → `O(N log k)` |
| 4 | `main.py` — `scan_directory()` | Boucle séquentielle | `ThreadPoolExecutor` (I/O-bound, le GIL est libéré par hashlib/yara-python/numpy) | Temps mural `O(N)` → `O(N/W)` |
| 5 | `optimizer/reputation_checker.py` — `check_hash()` | Seuil absolu (`malicious_count >= 3`) | Borne inférieure de l'intervalle de Wilson (proportion, confiance 95%) | Corrige un biais statistique (faux positifs/négatifs selon le nombre de moteurs ayant répondu) |
| 6 | `optimizer/folder_organizer.py` — `move_folder_into()` (cas fusion) | 2 parcours `rglob()` + tri par profondeur `O(N log N)` | 1 seul `os.walk(topdown=False)` | I/O ÷2, plus de tri |
| 7 | `scanner/yara_scanner.py` | — | Audit : règles déjà compilées une seule fois à l'init, réutilisées à chaque scan | Confirmé déjà optimal |

### Détail — Ratio de chaînes imprimables (point 2)

Une simulation (30 tirages × 50 000 octets purement aléatoires) a mesuré
un « plancher de bruit » ≈ 6,1 % en moyenne (max observé 6,5 %) pour des
runs ≥ 4 octets imprimables apparaissant par pur hasard. Le seuil de
décision `MIN_PRINTABLE_STRING_RATIO` a donc été fixé à **15 %** (marge de
sécurité ≈ 2,3× ce plancher), documenté avec le calcul dans
`scanner/heuristics.py`.

### Détail — Verdict VirusTotal (point 5)

L'ancien seuil traitait de façon identique *3 détections sur 5 moteurs*
(60 %, très suspect) et *3 détections sur 70 moteurs* (4,3 %, probable
faux positif isolé) — même compte absolu, signaux pourtant très
différents. La borne de Wilson (fonction `wilson_lower_bound()`) corrige
ce biais en raisonnant sur une proportion ajustée à la taille de
l'échantillon :

```
L = ( p̂ + z²/(2n) − z·√( p̂(1−p̂)/n + z²/(4n²) ) ) / (1 + z²/n)     avec z = 1,96 (confiance 95%)
```

Valeurs cross-validées indépendamment avec `statsmodels` :
`wilson_lower_bound(3, 5) ≈ 0.231` (→ MALVEILLANT) contre
`wilson_lower_bound(3, 70) ≈ 0.015` (→ SUSPECT seulement).

## Tests

La suite `pytest` couvre les 7 optimisations décrites ci-dessus **et** les
modules capables de déplacer ou de supprimer des fichiers — c'est là que se
situe le vrai risque de cet outil.

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/ -v
```

### Couverture par module

| Module | Fichier de test | Ce qui est couvert |
|---|---|---|
| `scanner/heuristics.py` | `test_heuristics.py` | Entropie de Shannon, ratio de chaînes imprimables (cas limites, non-régression numpy / pur Python) |
| `optimizer/disk_analyzer.py` | `test_disk_analyzer.py` | `analyze_disk()` — top-N par tas, agrégation de dossiers, détection de doublons, non-régression vs méthodes historiques |
| `optimizer/reputation_checker.py` | `test_reputation_checker.py` | Borne de Wilson (valeurs cross-validées `statsmodels`) + `check_hash()` avec `requests.get` mocké, cache, cas 404 |
| `optimizer/folder_organizer.py` | `test_folder_organizer.py` | `move_folder_into()` — cas simple, cas de fusion (collision de noms, renommage, journal d'annulation, nettoyage des dossiers vides), refus des cas dangereux |
| `main.py` | `test_main_scan_directory.py` | Parallélisation de `scan_directory()` — tous fichiers scannés une seule fois, plusieurs threads réellement utilisés, gain de temps mural mesuré |
| `quarantine/quarantine_manager.py` | `test_quarantine_manager.py` | Aller-retour quarantaine/restauration, neutralisation du nom, collision à la restauration, refus de détruire un fichier existant, intégrité et atomicité de l'index, suppression définitive |
| `optimizer/file_triage.py` | `test_file_triage.py` | Extensions et dossiers « jamais touchés », classification en 3 niveaux, intégration des doublons, réversibilité de la mise de côté, délai de rétention avant purge, robustesse de l'index |
| `optimizer/temp_cleaner.py` | `test_temp_cleaner.py` | Le dossier de cache est conservé, seul son contenu part ; fichiers verrouillés ignorés sans planter ; cibles de nettoyage confinées à un bac à sable pendant les tests ; corbeille jamais réellement vidée ; orchestration de la passe complète |
| `optimizer/residue_cleaner.py` | `test_residue_cleaner.py` | Liste de protection des dossiers, âge minimal de 30 jours, rapprochement avec les applications installées, détection strictement en lecture seule, réversibilité de la mise de côté, raccourcis orphelins, aucun accès au vrai registre |
| `optimizer/guardian.py` | `test_guardian.py` | Orchestration de la passe complète, seuils interactif (180 j) vs automatique (365 j), et surtout : **aucune suppression définitive déclenchée automatiquement** |
| `optimizer/ransomware_shield.py` | `test_ransomware_shield.py` | Canaris, fenêtre glissante de modifications, statistiques de Welford et seuil adaptatif de Cantelli (jamais sous le plancher) |

Les deux derniers fichiers (`test_guardian.py`, `test_ransomware_shield.py`)
font partie de la même passe de couverture des modules destructifs ;
`pytest tests/ -v` donne l'état réel de ton clone.

### Ce qui n'est PAS couvert (honnêtement)

| Module | Pourquoi |
|---|---|
| `scanner/hash_scanner.py`, `scanner/yara_scanner.py` | Non couverts par un test dédié. `yara_scanner` dépend en plus de `yara-python`, absent d'une CI minimale |
| `monitor/realtime_monitor.py` | Dépend de `watchdog` et d'événements système réels — testable seulement via une machinerie d'intégration non écrite à ce jour |
| `optimizer/startup_manager.py`, `optimizer/task_scheduler.py` | Écrivent dans le registre Windows / créent de vraies tâches `schtasks` : non testables sans mock lourd ou machine Windows dédiée |
| `optimizer/app_manager.py` | Seule la logique pure (`_is_never_remove`, `_is_known_bloatware`, `sort_apps`) serait testable hors Windows ; la désinstallation, non réversible par nature, n'est pas exercée automatiquement — c'est délibéré |
| `optimizer/phishing_link_checker.py` | Dépend d'un flux public distant |
| `gui/` | Le serveur est en cours d'écriture ; aucun test tant que le contrat n'est pas implémenté |
| `optimizer/folder_organizer.py` (partiel) | Seul `move_folder_into()` est couvert. `build_plan()`, `apply_plan()`, `undo_session()`, `classify_*()` et `find_least_used_files()` ne le sont pas encore — c'est le principal trou de couverture restant sur un module qui déplace des fichiers |

**Note plateforme** : `tests/test_main_scan_directory.py` teste `main.py`, qui
dépend de `winreg` (stdlib Windows) et `watchdog`. Sur une machine non-Windows
(CI Linux par exemple), ce fichier est automatiquement **ignoré** (`skipped`,
via `pytest.importorskip`) plutôt qu'en échec ; les autres fichiers,
indépendants de la plateforme, s'exécutent normalement partout.

## Enrichir la base de signatures (IMPORTANT)

Le projet démarre avec des bases **vides/exemples**. Pour une protection
réelle, alimentez :

- **Hashes malveillants** (`signatures/malicious_hashes.txt`) via :
  - [MalwareBazaar](https://bazaar.abuse.ch/export/) (flux CSV gratuit, mis à jour en continu)
  - [VirusShare](https://virusshare.com/) (inscription requise)

- **Règles YARA** (`signatures/rules.yar`) via :
  - [Yara-Rules/rules](https://github.com/Yara-Rules/rules) (dépôt communautaire massif)
  - [Neo23x0/signature-base](https://github.com/Neo23x0/signature-base) (règles de Florian Roth, très réputées)

## Limites à connaître impérativement

Ce projet est un **bon outil pédagogique et complémentaire**, mais ce
n'est **pas un remplacement** d'un antivirus certifié (Windows Defender,
etc.) :

1. **Pas de driver kernel-mode** : la surveillance temps réel est en
   espace utilisateur (watchdog), donc plus lente et moins exhaustive
   qu'un filtre de système de fichiers kernel comme celui de Defender.
2. **Pas de sandbox d'exécution** : aucune détonation en environnement
   isolé pour observer le comportement réel d'un exécutable.
3. **Pas de réputation cloud en temps réel** : les antivirus commerciaux
   interrogent une base cloud à chaque fichier touché. Ici, l'interrogation
   de VirusTotal (option 14) et de la liste OpenPhish (option 15) est
   **ponctuelle et déclenchée à la main** ; les scans des options 1 à 3
   restent 100 % locaux.
4. **Base de signatures à maintenir manuellement** — sans mise à jour
   régulière, l'efficacité diminue rapidement (les malwares évoluent en
   permanence).
5. **Pas de protection contre les exploits mémoire** (injection de
   processus, exploitation de vulnérabilités 0-day).

**Recommandation** : gardez Windows Defender actif en parallèle. Utilisez
cet outil en complément (scan ciblé, apprentissage, surveillance d'un
dossier spécifique), jamais comme unique ligne de défense.

## Feuille de route

### En cours

- **Interface web locale** (`gui/`) — contrat d'API figé
  (`gui/API_CONTRACT.md`), serveur `http.server` sur `127.0.0.1:8777` et
  frontend sans framework en cours d'écriture. Voir § Interface web locale.
- **Couverture de tests des modules destructifs** — quarantaine, tri,
  nettoyage TEMP, résidus, Mode Gardien et bouclier anti-ransomware. Voir
  § Tests pour l'état exact, y compris ce qui reste découvert.

### Pistes futures

- Mise à jour automatique des flux de hashes / règles YARA (tâche planifiée)
- Notification Windows (toast) en cas de détection
- Export de rapports PDF des scans
- Service Windows (via `pywin32`) pour tourner en arrière-plan au démarrage
- Couverture de test de `build_plan()` / `apply_plan()` / `undo_session()`
  dans `folder_organizer.py` (voir § Tests)
