# ANTI-ZEEVIRIUS — conception V2

Sécurité avancée, optimisation, confort, didacticiels.

Ce document répond au cahier des charges V2. Il est écrit selon la règle qui
gouverne déjà le projet : **on ne promet que ce qu'on peut tenir, et on dit
franchement où sont les limites**. Trois fonctions demandées se heurtent à la
contrainte « sécurité sans driver noyau » — elles sont réalisables, mais pas
au niveau que leur nom laisse espérer. C'est traité en section 5.

---

## 1. Organisation des modules

Le projet a déjà une architecture en couches. La V2 s'y insère au lieu de la
refonder — un module = un fichier, testable isolément, sans dépendance à
l'interface.

```
anti-zeevirius/
├── scanner/            (existant) détection : empreintes, YARA, heuristique
├── monitor/            (existant) surveillance temps réel
├── quarantine/         (existant) isolement réversible
├── optimizer/          (existant) nettoyage, rangement, applications, résidus
│
├── security/           ← NOUVEAU — couche défensive avancée
│   ├── sandbox.py              exécution isolée
│   ├── behavior_analyzer.py    analyse comportementale
│   ├── app_firewall.py         pare-feu applicatif (pilote celui de Windows)
│   ├── anomaly_detector.py     anomalies type rootkit (vue croisée)
│   ├── network_watch.py        connexions sortantes suspectes
│   └── incident_mode.py        mode incident
│
├── performance/        ← NOUVEAU — optimisation mesurée
│   ├── memory.py               diagnostic mémoire (voir l'avertissement §5.4)
│   ├── ssd.py                  TRIM et état SMART
│   ├── network_tune.py         DNS, latence
│   ├── benchmark.py            analyse de performance globale
│   └── services.py             services Windows, désactivation réversible
│
├── comfort/            ← NOUVEAU — confort et automatisation
│   ├── zen_mode.py             Mode Zen
│   ├── rules_engine.py         règles « si… alors… »
│   ├── history.py              historique unifié et réversible
│   ├── grand_menage.py         enchaînement complet
│   └── voice.py                assistant vocal local
│
├── gui/                (existant) interface locale + contrat d'API
├── packaging/          (existant) exécutable et installeur
└── docs/               didacticiels utilisateur
```

### Règle d'intégration

Chaque nouveau module respecte le contrat déjà en place :

1. **Aucune action destructive sans plan préalable.** Toute fonction qui
   modifie le système expose `preparer_*()` (lecture seule, retourne le plan)
   et `appliquer_*(plan, confirm_token)`.
2. **Réversibilité.** Ce qui est désactivé est sauvegardé, pas supprimé —
   même convention que `startup_manager.py`, qui déplace les entrées de
   registre vers une clé de sauvegarde.
3. **Dégradation propre.** Un module indisponible (droits insuffisants,
   édition de Windows incompatible) répond `{"ok": false, "unavailable": true,
   "reason": "..."}` au lieu d'échouer.
4. **Journalisation dans l'historique commun** (`comfort/history.py`), pour
   qu'une seule vue permette de tout annuler.

---

## 2. A — Sécurité avancée

### 2.1 Sandbox locale

**Trois niveaux d'isolement, du plus fort au plus faible.** Le module choisit
le meilleur disponible et **annonce lequel il a retenu** — c'est essentiel :
l'utilisateur doit savoir si son fichier douteux tourne vraiment sous cloche.

| Niveau | Mécanisme | Disponibilité | Isolement réel |
|---|---|---|---|
| 1 | **Windows Sandbox** (`.wsb`) | Windows Pro/Entreprise + virtualisation activée | Fort — machine virtuelle jetable |
| 2 | **AppContainer + Job Object** | Toutes éditions | Moyen — pas d'accès au profil, pas de réseau, arrêt forcé au bout de N secondes |
| 3 | **Observation seule** | Toutes éditions | Aucun — on n'exécute pas, on analyse statiquement |

Le niveau 2 s'appuie sur : jeton restreint (`CreateRestrictedToken`), niveau
d'intégrité bas, `Job Object` avec `JOB_OBJECT_LIMIT_ACTIVE_PROCESS` et
`JOB_OBJECT_LIMIT_JOB_TIME`, répertoire de travail temporaire jetable.

**Ce que ça ne fait pas** : un logiciel malveillant conscient d'être observé
peut rester inerte. Et le niveau 2 n'est pas une machine virtuelle : une
faille d'élévation de privilèges lui permettrait d'en sortir.

### 2.2 Analyse comportementale

Observe un processus pendant son exécution et note ce qui est anormal, plutôt
que de chercher une signature. Signaux collectés via `psutil` :

- **rythme d'écriture fichier** (déjà mesuré par `ransomware_shield.py`)
- **création de processus enfants** (`cmd`, `powershell`, `wscript`, `mshta`)
- **écriture dans les clés de démarrage** du registre
- **connexions sortantes** vers des adresses jamais contactées
- **suppression des clichés instantanés** (`vssadmin delete shadows`) — signal
  quasi certain de rançongiciel
- **énumération massive de fichiers** avant écriture

Chaque signal a un poids ; le score cumulé déclenche un avertissement puis, au
delà d'un seuil, une proposition de gel du processus.

**Réutilise l'existant** : `_OnlineStats` (Welford) et la borne de Cantelli de
`ransomware_shield.py` calibrent déjà des seuils par machine. On étend ce
mécanisme au lieu d'en inventer un second.

### 2.3 Pare-feu applicatif simplifié

**Point d'honnêteté central.** Sans driver, on ne peut pas intercepter le
trafic ni afficher « telle application veut se connecter, autoriser ? » comme
le font les suites commerciales : cela exige un callout WFP en mode noyau.

Ce qui est réalisable, et réellement utile : **une façade lisible du pare-feu
Windows**, qui existe déjà et est efficace.

- lister les règles existantes (`Get-NetFirewallRule`)
- bloquer une application en sortie (`New-NetFirewallRule -Direction Outbound
  -Program <chemin> -Action Block`)
- retirer la règle (réversible, chaque règle créée est étiquetée `AZ_`)
- détecter les applications qui communiquent alors qu'elles n'ont aucune
  raison de le faire, et proposer la règle

Exige les droits administrateur. Toute règle posée est enregistrée dans
l'historique et supprimable d'un clic.

### 2.4 Détection d'anomalies type rootkit

**Deuxième point d'honnêteté.** Un rootkit en mode noyau contrôle les
réponses que le système donne au mode utilisateur. Un détecteur en mode
utilisateur ne peut donc pas le voir *par construction*. Ce module ne
détecte pas les rootkits noyau, et ne doit pas le prétendre.

Ce qu'il fait : **la détection par vue croisée**, qui attrape les dissimulations
naïves — de loin les plus fréquentes.

| Objet | Vue A | Vue B | Anomalie |
|---|---|---|---|
| Processus | `CreateToolhelp32Snapshot` | `NtQuerySystemInformation` / WMI | présent dans l'une, absent de l'autre |
| Fichiers | `FindFirstFile` | énumération brute du répertoire | idem |
| Services | `Get-Service` | clés `HKLM\SYSTEM\CurrentControlSet\Services` | idem |
| Ports | `netstat` | `psutil.net_connections` | idem |

Autres signaux : pilotes non signés (`driverquery /si`), flux de données
alternatifs (ADS) sur des exécutables, tâches planifiées invisibles dans
l'interface graphique, écarts entre l'heure de modification affichée et celle
de la MFT.

**Divergence ≠ infection.** Ces écarts ont souvent des causes légitimes
(antivirus tiers, virtualisation). Le module rapporte, il n'accuse pas.

### 2.5 Analyse des connexions réseau suspectes

- inventaire des connexions établies avec le processus propriétaire
- résolution inverse et pays de destination
- signalement : IP brute sans nom de domaine, ports inhabituels (4444, 1337,
  8080 sortant), processus sans signature qui communique, connexion vers un
  domaine créé récemment
- croisement avec la liste noire OpenPhish déjà utilisée par
  `phishing_link_checker.py`

Aucune interception : on lit les tables de connexion, on ne s'insère pas dans
le flux.

### 2.6 Mode Incident

Séquence d'urgence, **en un bouton, entièrement réversible** :

1. **Couper le réseau** — règle pare-feu bloquant tout, plutôt que désactiver
   la carte : plus rapide à annuler, et le retour en arrière ne dépend pas du
   pilote réseau.
2. **Geler les processus suspects** — `suspend_process()` existe déjà dans
   `ransomware_shield.py`. Gel, pas arrêt : un processus tué perd ses preuves
   et peut déclencher un mécanisme de représailles.
3. **Sauvegarde rapide** — cliché instantané VSS (`vssadmin create shadow`)
   des dossiers personnels, avant toute manipulation.
4. **Rapport horodaté** — processus gelés, connexions actives, fichiers
   modifiés dans les dernières minutes.

**Sortie du mode** : un seul appel restaure le réseau et relance les processus.

---

## 3. B — Optimisation avancée

### 3.1 Mémoire — voir l'avertissement §5.4

Le module **ne « libère » pas la RAM**. Il diagnostique :
consommation par processus, détection de fuite (croissance monotone sur une
fenêtre d'observation), pression mémoire réelle, recommandations concrètes
(désactiver un programme au démarrage plutôt que « nettoyer »).

### 3.2 SSD

- `defrag <lettre> /L` — TRIM manuel (Windows le fait déjà chaque semaine)
- lecture SMART via WMI : heures de fonctionnement, octets écrits, usure
- détection d'un disque dur classique traité à tort comme un SSD
- **jamais de défragmentation sur SSD** — inutile et coûteux en cycles

### 3.3 Réseau

- test comparatif de résolveurs DNS (mesure réelle de latence sur un panel de
  domaines), proposition du plus rapide, changement réversible
- diagnostic : perte de paquets, latence, MTU
- **aucun réglage TCP avancé** appliqué automatiquement — voir §5.5

### 3.4 Analyse de performance globale

Un indice unique, décomposé : démarrage (via `Get-WinEvent` sur les
événements de boot), pression mémoire, saturation disque, processus les plus
coûteux, programmes au démarrage. Comparaison dans le temps grâce à
l'historique.

### 3.5 Services Windows

Même convention que `startup_manager.py` : analyse d'abord, désactivation
ensuite, toujours réversible et sauvegardée. Liste blanche stricte des
services jamais proposés (réseau, sécurité, audio, pilotes).

---

## 4. C — Confort et automatisation

### 4.1 Mode Zen
Suspend notifications, analyses planifiées et tâches de fond pendant une durée
choisie. Le bouclier anti-rançongiciel et la surveillance temps réel **restent
actifs** : le Mode Zen calme l'interface, il ne baisse pas la garde.

### 4.2 Règles « si… alors… »
Déclencheurs (branchement secteur, inactivité, seuil d'espace disque, heure) →
actions (nettoyage, analyse, rangement). Une règle qui déclenche une action
destructive exige une confirmation, sauf si l'utilisateur l'a explicitement
marquée « sans confirmation » — et ce marquage est lui-même journalisé.

### 4.3 Historique unifié
Les mécanismes de réversibilité existent déjà mais sont dispersés :
quarantaine, sas de tri, sessions de réorganisation, sauvegardes de démarrage.
`history.py` les agrège en **une seule vue chronologique**, avec un bouton
« annuler » par entrée.

### 4.4 Grand Ménage
Enchaînement complet en une fois. Le Mode Gardien existant en est la base ; on
y ajoute les étapes V2. Plan complet affiché avant, rapport après.

### 4.5 Assistant vocal local
Reconnaissance hors ligne (Vosk, modèle français léger). Vocabulaire fermé —
une vingtaine de commandes — et **aucune commande destructive par la voix** :
la voix peut lancer une analyse, pas valider une suppression.

---

## 5. Incohérences détectées dans le cahier des charges

Le brief demande de les signaler. Les voici, par ordre de gravité.

### 5.1 « Sandbox » et « sans driver noyau » sont en tension
Une vraie sandbox suppose une frontière que le mode utilisateur ne peut pas
poser seul. La solution retenue (Windows Sandbox si disponible, AppContainer
sinon) est honnête, mais **le niveau d'isolement varie selon la machine**.
L'interface doit l'afficher, jamais le masquer.

### 5.2 « Détection rootkit » sans driver : impossible au sens strict
Un rootkit noyau ment au mode utilisateur. Le module doit s'appeler
**« détection d'anomalies »**, pas « anti-rootkit » — sans quoi il donne une
fausse assurance, ce qui est pire que son absence.

### 5.3 « Pare-feu applicatif » : façade, pas moteur
Sans callout WFP, pas d'interception ni de question à la volée. Ce qu'on
livre pilote le pare-feu de Windows. C'est utile et honnête, mais ce n'est pas
un pare-feu applicatif au sens commercial du terme.

### 5.4 « Optimisation RAM intelligente » : le piège classique
Vider les jeux de travail (`EmptyWorkingSet`) fait chuter le compteur de RAM
utilisée et **dégrade les performances** : les pages retirées sont relues
depuis le disque à la première sollicitation. Les « nettoyeurs de RAM »
vendent un chiffre, pas un gain. Le module diagnostique donc, et ne « libère »
rien. Si tu veux malgré tout le geste visuel, il faut l'assumer comme
cosmétique.

### 5.5 « Optimisation réseau » : le réglage TCP est risqué
Modifier l'autotuning, la MTU ou la pile TCP dégrade souvent la connexion et
casse certains VPN. Retenu : mesure DNS (gain réel, mesurable, réversible).
Écarté : réglages TCP automatiques.

### 5.6 Assistant vocal contre philosophie « sans dépendance lourde »
Un modèle de reconnaissance vocale français pèse plusieurs dizaines de Mo et
alourdit l'installeur (34 Mo aujourd'hui). Proposition : **module optionnel,
téléchargé à la demande**, pas embarqué par défaut.

---

## 6. Ordre de réalisation proposé

Par rapport valeur/risque, en commençant par ce qui protège vraiment :

| Priorité | Module | Pourquoi |
|---|---|---|
| 1 | `network_watch.py` | Aucun risque, valeur immédiate, s'appuie sur l'existant |
| 2 | `incident_mode.py` | Le geste qui sauve, et 2 primitives sur 4 existent déjà |
| 3 | `behavior_analyzer.py` | Prolonge le bouclier anti-rançongiciel |
| 4 | `history.py` | Rend visible une réversibilité déjà présente mais dispersée |
| 5 | `app_firewall.py` | Fort en valeur, exige des droits admin et de la prudence |
| 6 | `anomaly_detector.py` | Utile, mais à nommer et présenter avec soin |
| 7 | `sandbox.py` | Le plus complexe, le plus dépendant de la machine |
| 8 | Optimisation | Gains réels mais modestes ; le diagnostic prime sur l'action |
| 9 | Confort | Agréable, non critique |
| 10 | Assistant vocal | Optionnel, à télécharger séparément |
