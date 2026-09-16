# Couche assistant — contrat gelé

ANTI-ZEEVIRIUS sait faire des choses ; il ne sait pas **décider** d'en faire.
Cette couche lui donne une mémoire, un jugement du risque, une surveillance
continue et une capacité d'initiative. C'est ce qui sépare un utilitaire qu'on
ouvre d'un gardien qui veille.

## Provenance

Le périmètre fonctionnel s'inspire d'un assistant tiers (GOD SKYLER, de
FatihMakes, sous licence Creative Commons BY‑NC 4.0). **Aucune ligne de son
code n'a été lue ni reprise** : cette licence interdit tout usage commercial et
contaminerait définitivement ANTI‑ZEEVIRIUS. Seule la *liste de ses
fonctionnalités* — une information factuelle, non protégeable — a servi de
cahier des charges. Tout ce qui suit est une écriture originale. La
copie de travail du projet tiers a été détruite avant le début du
développement, pour que l'emprunt involontaire soit matériellement impossible.

## Règle qui prime sur tout le reste

La doctrine du projet ne bouge pas d'un pouce : **rien de destructif sans
confirmation explicite, tout est réversible, le rapport précède l'action.**
L'autonomie ne desserre aucune de ces contraintes — elle s'exerce *à
l'intérieur*. Un assistant qui supprimerait un fichier « pour rendre service »
serait un échec du projet, pas une fonctionnalité.

---

## 1. Niveaux de risque — `assistant/risk.py`

```python
class Risque(enum.IntEnum):
    LECTURE     = 0   # n'écrit rien : analyse, inventaire, lecture de journal
    REVERSIBLE  = 1   # écrit, mais annulable par l'Historique (quarantaine, rangement)
    DESTRUCTIF  = 2   # perte de données possible (purge quarantaine, nettoyage)
    IRREVERSIBLE = 3  # ni annulable ni reconstructible (suppression définitive)
```

```python
def exige_confirmation(niveau: Risque, mode_autonome: bool) -> bool
```
Vrai dès `DESTRUCTIF`. En mode autonome, vrai dès `REVERSIBLE` : une machine
qui agit seule n'a pas le droit de toucher au disque sans qu'un humain ait dit
oui au moins une fois.

Le `_guarded()` existant de `gui/bridge.py` reste la seule porte d'exécution
des actions destructives. Cette couche ne le contourne jamais et ne le
duplique pas : elle le *décrit*.

---

## 2. Registre de capacités — `assistant/registry.py`

```python
@dataclasses.dataclass(frozen=True)
class Capacite:
    nom: str              # identifiant stable, ex. "scan.rapide"
    titre: str            # libellé humain, français
    categorie: str        # protection | nettoyage | securite | systeme | rangement
    risque: Risque
    description: str      # une phrase : ce que ça fait, pas comment
    action_bridge: str    # action du contrat d'API web, ou "" si interne
    parametres: tuple[str, ...] = ()
```

```python
class Registre:
    def enregistrer(self, c: Capacite) -> None      # nom déjà pris → ValueError
    def obtenir(self, nom: str) -> Capacite | None
    def toutes(self) -> tuple[Capacite, ...]        # triées par nom, DÉTERMINISTE
    def par_categorie(self, categorie: str) -> tuple[Capacite, ...]
```

`construire_registre_par_defaut() -> Registre` recense les capacités
d'ANTI‑ZEEVIRIUS en s'appuyant sur les actions déjà exposées par
`gui/bridge.py`. Il ne les réimplémente pas.

**Déterminisme obligatoire.** Toute itération sur un ensemble est triée. Un
`set` parcouru tel quel change d'ordre selon `PYTHONHASHSEED` — ce défaut a
déjà été trouvé une fois dans ce dépôt (`security/network_watch.py`), il ne
doit pas revenir.

---

## 3. Mémoire — `assistant/memory.py`

Fichier JSON unique sous `paths.data_path("assistant/memoire.json")`.
Jamais dans le dossier d'installation : il est en lecture seule une fois
l'application installée.

```python
@dataclasses.dataclass
class Profil:
    nom_assistant: str = "ANTI-ZEEVIRIUS"
    nom_utilisateur: str = ""      # vide = on n'invente pas de nom
    langue: str = "fr"

class Memoire:
    def profil(self) -> Profil
    def definir_profil(self, **champs) -> Profil
    def preference(self, cle: str, defaut=None)
    def definir_preference(self, cle: str, valeur) -> None
    def journaliser(self, evenement: str, detail: dict) -> None
    def decisions(self, limite: int = 50) -> list[dict]   # plus récentes d'abord
```

Écriture atomique (fichier temporaire + `os.replace`) : une coupure de courant
pendant l'écriture ne doit pas laisser une mémoire tronquée. Un fichier
illisible est renommé `.corrompu` et remplacé par un neuf — jamais une
exception qui empêche l'application de démarrer.

---

## 4. Compréhension des commandes — `assistant/intent.py`

```python
@dataclasses.dataclass(frozen=True)
class Intention:
    capacite: str            # nom d'une Capacite, ou "" si rien compris
    parametres: dict
    confiance: float         # 0.0 à 1.0
    justification: str       # pourquoi cette lecture — affiché à l'utilisateur
```

```python
def comprendre(phrase: str, registre: Registre) -> Intention
```

**Déterministe et sans réseau.** Correspondance par mots-clés pondérés et
distance d'édition, français et anglais. Aucun appel à un modèle de langage :
un antivirus qui exige une clé d'API payante pour lancer une analyse n'est pas
un antivirus. Un branchement LLM facultatif pourra venir plus tard, il ne sera
jamais requis.

En dessous de `confiance = 0.55`, `capacite` vaut `""` : l'assistant demande
une reformulation au lieu de deviner. Deviner, ici, c'est lancer une
suppression que personne n'a demandée.

---

## 5. Télémétrie — `assistant/telemetry.py`

```python
@dataclasses.dataclass(frozen=True)
class Echantillon:
    horodatage: float        # time.time()
    cpu: float               # pourcentage 0-100
    memoire: float           # pourcentage 0-100
    disque: float            # pourcentage du volume système
    temperature: float | None  # °C, None si la machine ne la publie pas
```

```python
class Telemetrie:
    def __init__(self, capacite_historique: int = 720)  # 1 h à 5 s d'intervalle
    def echantillonner(self) -> Echantillon
    def historique(self) -> tuple[Echantillon, ...]
    def moyennes(self, secondes: int) -> Echantillon | None
    def demarrer(self, intervalle: float = 5.0) -> None
    def arreter(self) -> None                 # idempotent
```

Tampon circulaire borné : une surveillance qui tourne une semaine ne doit pas
consommer la mémoire qu'elle surveille. `psutil` absent → échantillons à zéro
et `disponible = False`, jamais une exception.

---

## 6. Notifications — `assistant/notify.py`

```python
@dataclasses.dataclass(frozen=True)
class Notification:
    identifiant: str       # sha256 court, stable pour un même (titre, corps, source)
    horodatage: float
    gravite: str           # info | alerte | critique
    titre: str
    corps: str
    source: str            # module émetteur, ex. "camera_watch"
    acquittee: bool = False

class CentreNotifications:
    def pousser(self, gravite, titre, corps, source, *, systeme: bool = True) -> Notification
    def lister(self, *, non_acquittees_seulement: bool = False) -> tuple[Notification, ...]
    def acquitter(self, identifiant: str) -> bool
    def purger(self, avant: float) -> int
```

`systeme=True` déclenche en plus une bulle Windows. `security/camera_watch.py`
sait déjà le faire : **extraire sa fonction de notification ici et l'y faire
appeler**, sans dupliquer le code ni changer son comportement observable — ses
tests doivent passer sans modification.

Anti-répétition : une notification identique (même `identifiant`) non
acquittée n'est pas repoussée. Un gardien qui crie toutes les cinq secondes
finit par être ignoré, et c'est alors une faille.

---

## 7. Initiative — `assistant/proactive.py`

```python
@dataclasses.dataclass(frozen=True)
class Suggestion:
    capacite: str
    motif: str             # le fait constaté qui la motive
    urgence: int           # 0 (confort) à 3 (sécurité immédiate)

def suggerer(etat: dict, memoire: Memoire, registre: Registre) -> tuple[Suggestion, ...]
```

Fonction **pure** : même état, mêmes suggestions. Elle ne lit pas le disque et
n'exécute rien — elle propose, triée par urgence décroissante. Une suggestion
refusée deux fois est mise en sourdine (via `Memoire`) : insister est un
défaut, pas une vertu.

---

## 8. Résumé quotidien — `assistant/digest.py`

```python
def construire_resume(depuis: float, jusqu_a: float, sources: dict) -> dict
```
Renvoie `{"periode": ..., "faits": [...], "alertes": [...], "suggestions": [...]}`.
Purement lecture : ne déclenche aucune analyse, agrège ce qui existe déjà.

---

## 9. Mode autonome — `assistant/autonomy.py`

```python
class ModeAutonome:
    def __init__(self, registre, memoire, centre, *, budget: int = 12)
    def demarrer(self) -> None
    def arreter(self) -> None                 # idempotent, immédiat
    def actif(self) -> bool
    def journal(self) -> tuple[dict, ...]
```

Limites **non négociables**, à vérifier par des tests :

1. N'exécute **que** des capacités de niveau `LECTURE`. Rien d'autre. Jamais.
2. Au-delà du budget d'actions par cycle, s'arrête et le dit.
3. Chaque action est journalisée **avant** exécution, avec son motif.
4. `arreter()` prend effet avant l'action suivante, sans attendre la fin du cycle.
5. Tout ce qui dépasse `LECTURE` devient une `Suggestion` présentée à
   l'utilisateur — jamais une action.

---

## 10. Exposition web

Actions ajoutées à `gui/bridge.py`, mêmes conventions que l'existant
(enveloppe JSON, import paresseux, `_guarded()` pour tout ce qui écrit) :

| Action | Risque | Effet |
|---|---|---|
| `assistant.capacites` | LECTURE | liste le registre |
| `assistant.comprendre` | LECTURE | phrase → intention, **sans exécuter** |
| `assistant.profil` | REVERSIBLE | lit / écrit le profil |
| `assistant.telemetrie` | LECTURE | échantillon courant + historique |
| `assistant.notifications` | LECTURE | liste |
| `assistant.acquitter` | REVERSIBLE | acquitte une notification |
| `assistant.suggestions` | LECTURE | suggestions courantes |
| `assistant.resume` | LECTURE | résumé de la période |
| `assistant.autonomie` | REVERSIBLE | démarre / arrête / état |

`assistant.comprendre` **ne lance rien**. Comprendre et exécuter sont deux
étapes séparées par un clic de l'utilisateur. C'est ce qui empêche une phrase
mal formulée de déclencher une purge.

---

## Règles de tenue

- Python ≥ 3.10, bibliothèque standard uniquement (`psutil` toléré, absence gérée).
- Aucun module de cette couche n'importe `gui.*` : la dépendance va dans
  l'autre sens, sinon le serveur web devient obligatoire pour analyser un fichier.
- Hors Windows, tout doit rester importable et testable.
- Tests sous `tests/test_assistant_*.py`. La suite entière doit rester verte.
- Commentaires en français, expliquant **pourquoi**, jamais **quoi**.
