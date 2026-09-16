# Feuille de route — absorber le périmètre de Skyler

Le propriétaire a demandé : « je veux tout cela dans ANTI-ZEEVIRIUS, couplé à une
voix off du nom de PROMÉTHÉE ». « Tout cela » désigne les 69 modules d'actions de
GOD SKYLER.

Ce document tranche le périmètre. Il ne dit pas « on verra » : chaque module est
classé, et les refus sont motivés.

## Rappel de licence — ce document ne l'oublie pas

Skyler est sous **Creative Commons BY-NC 4.0** : non commerciale et contaminante.
La décision du propriétaire est la **réécriture intégrale, aucune ligne copiée**.
Sa source a été détruite ; seule sa liste de fonctionnalités — une information
factuelle, non protégeable — sert de cahier des charges. Cette règle vaut pour
chaque vague ci-dessous, sans exception.

## Le critère de tri

Une seule question, posée à chaque module : **est-ce que ça rend la machine plus
sûre, ou est-ce que ça la rend juste plus bavarde ?**

ANTI-ZEEVIRIUS est un antivirus. Un antivirus qui réserve des restaurants n'est
pas un meilleur antivirus — c'est un antivirus dilué, avec une surface d'attaque
plus large et davantage de code non relu. La dilution est un risque de sécurité,
pas une fonctionnalité.

---

## Vague 0 — PROMÉTHÉE (en cours)

La voix off. Du grec *pro-mētheus*, « celui qui pense avant » : elle prévient.

Synthèse par **SAPI**, déjà présent dans Windows 10 et 11, via PowerShell. Ni
service en ligne, ni clé d'API, ni paquet à installer — la doctrine du projet
l'exige : un antivirus qui réclame un abonnement pour dire « menace détectée »
est un échec de conception.

Le point dur n'est pas technique, il est de confidentialité : **une voix est
entendue par tout le monde dans la pièce.** Par défaut PROMÉTHÉE ne prononce
jamais un chemin, un nom de fichier, un nom d'utilisateur, une adresse IP ni un
nom de machine. Elle dit « une menace dans vos documents ». Trois niveaux, et
l'utilisateur choisit en connaissance de cause.

Voir `docs/PROMETHEE.md`.

---

## Vague 1 — ce qui renforce réellement l'antivirus

| Fonction de Skyler | Ce qu'elle apporte ici | État |
|---|---|---|
| `risk_gate` | classement du risque avant toute action | **fait** (`assistant/risk.py`) |
| `auto_mode` | autonomie bornée à la lecture seule | **fait** (`assistant/autonomy.py`) |
| `notification_center` | centre d'alertes unifié | **fait** (`assistant/notify.py`) |
| `system_monitor` + `background_monitor` | télémétrie continue | **fait** (`assistant/telemetry.py`) |
| `daily_digest` | résumé quotidien | **fait** (`assistant/digest.py`) |
| `action_log` | journal auditable de chaque action | partiel (`comfort/history.py`) |
| `system_access` (lecture seule) | fenêtre active, applications ouvertes → contexte | à faire |
| `activity_log` (opt-in) | enchaînements d'applications inhabituels | à faire |

Les deux dernières lignes sont les plus intéressantes, et pour une raison que
Skyler n'exploitait pas : **savoir quelle application est active et depuis
combien de temps est un signal de détection**. Un processus qui ouvre trois
cents fichiers en deux minutes alors que l'utilisateur n'a rien lancé, c'est le
profil d'un rançongiciel. Le `behavior_analyzer` déjà identifié comme candidat
s'appuierait dessus, et donnerait au Mode Incident de meilleurs suspects que son
classement actuel par octets cumulés.

Contrainte reprise de Skyler, et bonne : **aucune frappe clavier, aucun clic
n'est jamais capturé.** Nom d'application, titre de fenêtre, durée. Désactivé
par défaut, purgeable. Un antivirus qui enregistre les frappes est un
enregistreur de frappes.

---

## Vague 2 — confort légitime, sans dénaturer le produit

| Fonction | Pourquoi elle a sa place |
|---|---|
| **Intelligence du presse-papier** | Le meilleur candidat de tout le lot. Vous copiez un lien, il est **vérifié avant que vous le colliez** — `optimizer/phishing_link_checker.py` existe déjà. Chez Skyler c'était du confort ; ici c'est de la sécurité déguisée en confort. |
| `file_processor`, `pdf_tools`, `docx_tools`, `office_files` | Lire et résumer un document sert le tri de fichiers (`optimizer/file_triage.py`) : classer selon le contenu, pas seulement l'extension. |
| `open_app`, `computer_settings` | Piloter la machine par la console de commande déjà en place. |
| `reminder` | Planifier une analyse, rappeler une mise à jour de signatures. |
| `personality` | Le ton de PROMÉTHÉE. Marginal, mais peu coûteux. |

---

## Vague 3 — hors sujet : ne sera pas fait

`flight_finder`, `weather_report`, `weather_widget`, `youtube_video`,
`game_updater`, `expense_tracker`, `instagram_stats`, `world_map`,
`procedural_3d`, `show_3d_object`, `codebase_city`, `website_builder`,
`office_builder`, `ppt_template_workflow`, `meeting_assistant`, `brahma_connect`,
`auto_chat`, `holographic/*`.

Aucun ne rend une machine plus sûre. Les intégrer, c'est ajouter des milliers de
lignes non relues et des dépendances réseau à un produit qui tourne avec les
droits administrateur. Si le propriétaire en veut un précisément, il sera traité
à l'unité — pas par principe.

---

## Refusés — dangereux dans un produit de sécurité

Ceux-là ne seront pas réécrits, et le refus est technique, pas frileux.

**`self_improve` — le plus grave.** Un programme qui réécrit son propre code.
Dans un antivirus, c'est intenable : la signature numérique de l'exécutable
devient caduque à la première modification, l'audit devient impossible, et un
attaquant qui détourne ce mécanisme obtient une exécution de code persistante
avec les droits administrateur. C'est exactement la capacité qu'un logiciel
malveillant cherche à obtenir.

**`instagram_dm`, `whatsapp_bot`, `send_message`, `phone_calls`.** Des
autopilotes de messagerie. Un antivirus qui peut envoyer des messages en votre
nom est un vecteur d'exfiltration et d'hameçonnage prêt à l'emploi. Le gain pour
l'utilisateur est nul, le risque est total.

**`reservation`.** Pilote un vrai navigateur jusqu'à une page de paiement.
Aucune raison qu'un antivirus approche un formulaire de paiement.

**`claude_code_bridge`, `codex_agent`, `gemini_agent`, `claude_agent`,
`dev_agent`, `code_helper`, `cli_updater`.** Exécution de code généré à la
volée. Même objection que `self_improve`, en version distribuée.

**`screen_processor` en continu.** Capturer l'écran en permanence et l'envoyer à
un service en ligne, dans un logiciel privilégié, c'est construire l'outil
d'espionnage que le produit est censé détecter. Une capture ponctuelle et
explicitement demandée par l'utilisateur serait discutable ; le flux continu,
non.

**Mot d'éveil et écoute permanente.** Skyler écoute en continu. PROMÉTHÉE
**parle mais n'écoute pas**, et c'est une décision, pas un manque : un micro
toujours actif dans un logiciel qui tourne en administrateur est un risque que
rien dans ce produit ne justifie. Si la commande vocale est demandée plus tard,
elle devra être à activation explicite — bouton pressé, pas mot d'éveil.

---

## Ce qui reste vrai quoi qu'il arrive

1. **Le `.exe` n'a jamais tourné sur Windows.** Les 1 449 tests simulent le
   système. `netsh`, `vssadmin`, `schtasks`, le registre : jamais exécutés. Aucun
   agent ne lèvera ce risque — il faut une machine Windows réelle. C'est le
   premier jalon utile du projet, avant toute nouvelle fonctionnalité.
2. **Le code écrit par les agents n'a pas été relu.** Une suite verte prouve que
   les tests écrits passent, pas que le code est correct.
3. Deux fichiers de tests manquent (`memory`, `intent`) : couverture indirecte
   seulement.

Ajouter des fonctionnalités par-dessus ces trois points augmente la dette plus
vite qu'elle ne se rembourse. La vague 1 peut avancer en parallèle ; la vague 2
devrait attendre que le point 1 soit levé.
