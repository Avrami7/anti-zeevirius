# ANTI-ZEEVIRIUS — Antivirus Windows

Projet Python complet combinant 3 couches de détection + quarantaine +
surveillance temps réel.

## Architecture

```
antivirus_windows/
├── main.py                      # Point d'entrée — menu CLI
├── requirements.txt
├── requirements-dev.txt         # + pytest, pour lancer la suite de tests
├── tests/                       # Suite de tests pytest (voir section dédiée)
├── scanner/
│   ├── hash_scanner.py          # Détection par signature SHA-256
│   ├── yara_scanner.py          # Détection par règles YARA (patterns)
│   └── heuristics.py            # Analyse comportementale (entropie, PE, extensions)
├── monitor/
│   └── realtime_monitor.py      # Surveillance temps réel (watchdog)
├── quarantine/
│   └── quarantine_manager.py    # Isolation + restauration des fichiers
├── optimizer/
│   ├── temp_cleaner.py          # Nettoyage Temp/%temp%/cache/corbeille
│   ├── startup_manager.py       # Gestion des programmes au démarrage
│   ├── disk_analyzer.py         # Gros fichiers et doublons
│   ├── task_scheduler.py        # Planification du nettoyage automatique
│   └── file_triage.py           # Tri des fichiers avec confirmation
├── premium/ (dans optimizer/)
│   ├── ransomware_shield.py      # Fichiers canari + détection modification massive
│   ├── reputation_checker.py     # Réputation cloud via API VirusTotal
│   └── phishing_link_checker.py  # Vérification anti-phishing des liens
├── signatures/
│   ├── malicious_hashes.txt     # Base de hashes connus (à enrichir)
│   └── rules.yar                # Règles YARA (auto-générées au 1er lancement)
└── logs/                        # Journaux datés (scan_YYYYMMDD.log)
```

## Module Optimiseur (nouveau)

| Fonction | Ce qu'elle fait | Droits requis |
|---|---|---|
| **Nettoyage complet** | Vide `%TEMP%`, corbeille, cache miniatures, cache Chrome/Edge/Firefox. Avec droits admin : + `C:\Windows\Temp`, Prefetch, cache Windows Update | Utilisateur standard (partiel) / Admin (complet) |
| **Gestion démarrage** | Liste les programmes au démarrage (registre + dossier Démarrage), signale ceux couramment désactivables, permet de les désactiver de façon **réversible** | Admin pour les entrées HKLM |
| **Analyse disque** | Top gros fichiers/dossiers + détection de doublons (par hash) | Aucun |
| **Planification** | Crée une tâche Windows native (`schtasks`) pour un nettoyage hebdomadaire automatique | Admin recommandé |

### Lancer en administrateur (recommandé pour un nettoyage complet)

1. Ouvre une invite de commandes **en administrateur** (clic droit → "Exécuter en tant qu'administrateur")
2. Navigue jusqu'au dossier : `cd chemin\vers\antivirus_windows`
3. Lance : `python main.py`

### Sécurité des opérations de désactivation

- Le nettoyage Temp ne supprime que le **contenu** de dossiers de cache — jamais les dossiers eux-mêmes, et ignore silencieusement tout fichier verrouillé (même logique que le message "Accès refusé" que tu as rencontré dans l'Explorateur).
- La désactivation d'un programme au démarrage **déplace** l'entrée vers une clé de sauvegarde réversible plutôt que de la supprimer — restaurable à tout moment.

## Installation

```bash
cd antivirus_windows
pip install -r requirements.txt
```

**Note sur `yara-python` sous Windows** : l'installation via pip fournit
un wheel précompilé pour Windows (pas besoin de compiler YARA vous-même).
Si l'installation échoue, vérifiez que vous êtes en Python 64 bits.

## Utilisation

```bash
python main.py
```

Menu interactif :
1. **Scanner un fichier** — analyse ponctuelle (hash + YARA + heuristique)
2. **Scanner un dossier** — scan récursif complet
3. **Protection temps réel** — surveille Téléchargements/Bureau (ou dossiers personnalisés) et scanne automatiquement tout nouveau fichier
4. **Voir la quarantaine** — liste des fichiers isolés
5. **Restaurer** — en cas de faux positif
6. **Suppression définitive** — purge un fichier en quarantaine
7. **Nettoyage complet** — Temp, %temp%, cache navigateurs, corbeille, miniatures
8. **Gérer le démarrage** — liste et désactive les programmes au démarrage Windows
9. **Analyser le disque** — gros fichiers, gros dossiers, doublons
10. **Planifier un nettoyage automatique** — tâche Windows hebdomadaire
11. **Trier les fichiers d'un dossier** — classifie chaque fichier (sûr / à vérifier / jamais touché) et demande confirmation avant toute mise de côté
12. **Voir / restaurer les fichiers mis de côté** — récupère un fichier trié par erreur, ou purge définitivement après 30 jours

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
- **Détection de taux de modification** : plus de 15 fichiers modifiés en moins de 10 secondes dans un dossier protégé = alerte immédiate (seuils ajustables dans `ransomware_shield.py`).
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

Le projet inclut une suite `pytest` qui couvre les 7 optimisations
ci-dessus (formules statistiques, non-régression sur les parcours
unifiés, exécution parallèle réelle).

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/ -v
```

**Note plateforme** : `tests/test_main_scan_directory.py` teste
`main.py`, qui dépend de `winreg` (stdlib Windows) et `watchdog`. Sur une
machine non-Windows (ex: CI Linux), ce fichier est automatiquement
**ignoré** (`skipped`, via `pytest.importorskip`) plutôt qu'en échec — les
6 autres fichiers de tests, indépendants de la plateforme, s'exécutent
normalement partout.

| Fichier de test | Couvre |
|---|---|
| `test_heuristics.py` | Entropie de Shannon, ratio de chaînes imprimables (cas limites, non-régression numpy/pur Python) |
| `test_disk_analyzer.py` | `analyze_disk()` — top-N par tas, agrégation de dossiers, détection de doublons, non-régression vs méthodes historiques |
| `test_reputation_checker.py` | Borne de Wilson (valeurs de référence cross-validées `statsmodels`) + `check_hash()` avec `requests.get` mocké |
| `test_folder_organizer.py` | `move_folder_into()` — cas simple et cas de fusion (collision de noms, nettoyage des dossiers vides) |
| `test_main_scan_directory.py` | Parallélisation de `scan_directory()` — tous fichiers scannés, plusieurs threads réellement utilisés, gain de temps mural mesuré |


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
3. **Pas de réputation cloud en temps réel** : contrairement aux
   antivirus commerciaux qui interrogent des bases cloud à chaque
   fichier, ce projet fonctionne 100% en local.
4. **Base de signatures à maintenir manuellement** — sans mise à jour
   régulière, l'efficacité diminue rapidement (les malwares évoluent en
   permanence).
5. **Pas de protection contre les exploits mémoire** (injection de
   processus, exploitation de vulnérabilités 0-day).

**Recommandation** : gardez Windows Defender actif en parallèle. Utilisez
cet outil en complément (scan ciblé, apprentissage, surveillance d'un
dossier spécifique), jamais comme unique ligne de défense.

## Pistes d'amélioration (roadmap)

- Interface graphique (Tkinter ou PyQt) au lieu du CLI
- Mise à jour automatique des flux de hashes/règles YARA (tâche planifiée)
- Notification Windows (toast) en cas de détection
- Export de rapports PDF des scans (voir la skill `pdf` si intégré à Claude)
- Service Windows (via `pywin32`) pour tourner en arrière-plan au démarrage
