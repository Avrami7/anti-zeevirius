"""
intent.py — de la phrase de l'utilisateur à une capacité nommée.

Aucun modèle de langage, aucune requête réseau, aucune clé d'API. C'est une
contrainte de conception, pas une limite technique : un antivirus qui exige un
abonnement à un service tiers pour comprendre « lance une analyse » n'est plus
un antivirus, c'est un client. Il doit fonctionner sur une machine hors ligne,
possiblement déjà compromise — exactement la machine où on en a le plus besoin.

Le mécanisme est donc explicite et vérifiable : des règles de mots-clés
pondérées, une tolérance aux fautes de frappe par distance d'édition, et un
seuil en dessous duquel l'assistant dit « je n'ai pas compris » au lieu de
deviner. Le contrat fixe ce seuil à 0,55.

## Pourquoi un refus vaut mieux qu'une supposition

Deviner, ici, ce n'est pas afficher la mauvaise page : c'est proposer une
suppression que personne n'a demandée. Trois garde-fous, dans cet ordre :

1. **Spécificité exigée.** Une règle décrit des GROUPES de sens qui doivent
   TOUS être présents (« quarantaine » ET un verbe de suppression). Un seul
   mot-clé isolé ne déclenche rien de destructif.
2. **Verbe d'action obligatoire.** Toute capacité de niveau `DESTRUCTIF` ou
   plus exige, en plus de sa règle, un verbe d'action explicite dans la
   phrase. Filet de sécurité redondant, et c'est voulu : le jour où une règle
   sera ajoutée en oubliant son verbe, ce garde-fou tiendra quand même.
3. **Ambiguïté = refus.** Deux capacités qui marquent presque le même score,
   c'est une phrase qui veut dire deux choses. La confiance est rabattue, et
   sous le seuil elle rend `capacite=""`.

## Déterminisme

Aucun parcours d'ensemble non ordonné : les règles sont un tuple, les groupes
des tuples, le classement final trie sur `(-score, nom)`. Ce dépôt a déjà
connu un défaut où un `set` parcouru tel quel changeait de résultat selon
`PYTHONHASHSEED` (`security/network_watch.py`). Deux lancements de la même
phrase doivent donner la même intention, sans quoi rien n'est testable.

## Ce que ce module ne fait PAS

Il n'exécute rien et ne touche à rien. `comprendre()` est une fonction pure
d'une phrase et d'un registre. Comprendre et exécuter sont séparés par un clic
de l'utilisateur — c'est cette séparation qui empêche une phrase maladroite de
déclencher une purge.
"""

from __future__ import annotations

import dataclasses
import re
import unicodedata
from typing import Dict, Optional, Tuple

from assistant.registry import Capacite, Registre
from assistant.risk import Risque, libelle

__all__ = ["Intention", "comprendre", "SEUIL_CONFIANCE"]

# Normatif : fixé par le contrat de la couche assistant.
SEUIL_CONFIANCE = 0.55

# Une règle d'un seul groupe de sens part à 0,60 — juste au-dessus du seuil :
# un mot-clé bien identifié suffit à agir, un mot-clé approximatif non. Chaque
# groupe supplémentaire ajoute 0,05, parce qu'une règle qui exige deux signaux
# concordants se trompe moins souvent qu'une règle qui n'en exige qu'un.
_BASE = 0.60
_BONUS_GROUPE = 0.05
_POIDS_RENFORT = 0.10
_PLAFOND_RENFORTS = 0.30
# Un groupe reconnu à une faute de frappe près ne vaut pas un groupe reconnu
# exactement : 0,60 × 0,75 = 0,45, sous le seuil. Une phrase mal tapée doit
# donc apporter un second signal pour être exécutée.
_FACTEUR_APPROX = 0.75
# Appliqué à une capacité destructive dont la phrase ne contient aucun verbe
# d'action : 0,70 × 0,45 = 0,31, très loin du seuil.
_FACTEUR_SANS_VERBE = 0.45
# Appliqué dès qu'une négation porte sur la phrase et que la capacité écrit
# sur le disque : 1,00 × 0,30 = 0,30, sous le seuil quelle que soit la force
# du reste de la lecture. On garde un score non nul plutôt que zéro pour que
# la justification nomme quand même la capacité reconnue — « je crois que
# vous parlez de la purge, mais vous me dites de ne pas la faire » est un
# refus utile ; un refus sans objet ne l'est pas.
_FACTEUR_NEGATION = 0.30
# Deux candidats à moins de ce écart l'un de l'autre : la phrase est ambiguë.
#
# Il valait 0,06, c'est-à-dire PLUS que `_BONUS_GROUPE`. Conséquence : une
# règle qui exigeait un groupe de sens de plus qu'une autre la dépassait
# d'exactement 0,05 et restait donc, par construction, dans la fenêtre
# d'ambiguïté — sa spécificité supplémentaire ne pouvait jamais la faire
# gagner. « Analyse le fichier /tmp/x.exe » comparait ainsi `scan.fichier`
# (0,65, deux groupes exigés) à `scan.dossier` (0,60, un seul), concluait à
# l'ambiguïté et refusait la demande la plus élémentaire qu'on puisse
# adresser à un antivirus. Même effet sur « état du bouclier ».
#
# 0,05 rétablit la cohérence des constantes : un groupe exigé de plus
# départage, deux candidats à score STRICTEMENT égal restent ambigus. C'est
# ce que la docstring du module promettait déjà — « une règle qui exige deux
# signaux concordants se trompe moins souvent qu'une règle qui n'en exige
# qu'un » — et que l'arithmétique contredisait.
_ECART_AMBIGU = 0.05
_FACTEUR_AMBIGU = 0.75


@dataclasses.dataclass(frozen=True)
class Intention:
    """Lecture d'une phrase. `capacite` vide = rien compris avec assez de
    certitude ; la `justification` est écrite pour être MONTRÉE à
    l'utilisateur, c'est elle qui rend le refus utile plutôt que vexant."""

    capacite: str
    parametres: dict
    confiance: float
    justification: str


# ── Normalisation ──────────────────────────────────────────────────────────

def _sans_accents(texte: str) -> str:
    """« Téléchargements » et « telechargements » doivent se valoir.

    Décomposition NFD puis retrait des diacritiques : personne ne tape les
    accents correctement dans une barre de commande, et refuser « analyse mon
    repertoire » serait une pédanterie coûteuse.
    """
    decompose = unicodedata.normalize("NFD", texte)
    return "".join(c for c in decompose if not unicodedata.combining(c))


def _normaliser(phrase: str) -> Tuple[str, Tuple[str, ...]]:
    """Rend (phrase normalisée, mots). L'apostrophe sépare : « l'historique »
    donne « l » et « historique », sinon aucun mot-clé ne serait reconnu."""
    plate = _sans_accents(str(phrase)).lower()
    plate = re.sub(r"[^a-z0-9]+", " ", plate).strip()
    return plate, tuple(m for m in plate.split(" ") if m)


def _distance_edition(a: str, b: str) -> int:
    """Distance de Levenshtein, deux lignes glissantes.

    Même algorithme que `security/network_watch.py`, volontairement recopié
    plutôt qu'importé : cette couche ne doit dépendre d'aucun module de
    sécurité, sinon comprendre une phrase exigerait que `psutil` soit
    installé. Quinze lignes dupliquées valent mieux qu'un couplage.
    """
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    precedente = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        courante = [i]
        for j, cb in enumerate(b, 1):
            courante.append(min(
                precedente[j] + 1,
                courante[j - 1] + 1,
                precedente[j - 1] + (ca != cb),
            ))
        precedente = courante
    return precedente[-1]


def _tolerance(terme: str) -> int:
    """Combien de fautes on pardonne sur un mot-clé, selon sa longueur.

    Aucune sur les mots courts : à deux fautes près, « scan » devient « plan »
    et « sas » devient « pas ». La tolérance n'a de sens que là où elle ne
    crée pas de collision.
    """
    if len(terme) >= 9:
        return 2
    if len(terme) >= 6:
        return 1
    return 0


def _apparie(terme: str, mots: Tuple[str, ...], plate: str) -> float:
    """1.0 si le terme est présent, 0.75 à une faute près, 0.0 sinon.

    Le pluriel est traité comme une correspondance EXACTE (« connexions » vaut
    « connexion ») : c'est une variation grammaticale, pas une faute de frappe,
    et la pénaliser ferait rater des phrases parfaitement écrites.
    """
    if " " in terme:                      # expression figée : « temps reel »
        return 1.0 if f" {terme} " in f" {plate} " else 0.0
    for mot in mots:                      # tuple : ordre stable par construction
        if mot == terme or mot == terme + "s" or terme == mot + "s":
            return 1.0
    seuil = _tolerance(terme)
    if seuil:
        for mot in mots:
            if abs(len(mot) - len(terme)) <= seuil and _distance_edition(mot, terme) <= seuil:
                return _FACTEUR_APPROX
    return 0.0


# ── Vocabulaire commun ─────────────────────────────────────────────────────
# Groupes réutilisés par plusieurs règles. Les factoriser évite qu'un synonyme
# ajouté à un endroit manque à l'autre — le défaut typique d'une table écrite
# à la main, qui rend une capacité inaccessible sans prévenir personne.

_VOIR = ("liste", "lister", "montre", "montrer", "affiche", "afficher", "voir",
         "consulte", "consulter", "quels", "quelles", "quoi", "contenu", "etat",
         "statut", "list", "show", "display", "view", "see", "status")
_DEMARRER = ("demarre", "demarrer", "active", "activer", "lance", "lancer",
             "allume", "allumer", "mets", "mettre", "deploie", "deployer",
             "start", "enable", "run", "on")
_ARRETER = ("arrete", "arreter", "stop", "stoppe", "stopper", "coupe", "couper",
            "desactive", "desactiver", "eteins", "eteindre", "retire", "retirer",
            "disable", "off")
_SUPPRIMER = ("supprime", "supprimer", "efface", "effacer", "detruit", "detruire",
              "vide", "vider", "purge", "purger", "delete", "remove", "wipe",
              "empty", "erase")
_NETTOYER = ("nettoie", "nettoyer", "nettoyage", "libere", "liberer", "degage",
             "degager", "clean", "cleanup", "clear") + _SUPPRIMER
_RESTAURER = ("restaure", "restaurer", "recupere", "recuperer", "remets",
              "remettre", "retablis", "retablir", "rends", "restore", "recover")
_ANNULER = ("annule", "annuler", "undo", "defais", "defaire", "revenir",
            "retour", "cancel", "reviens")
_APPLI = ("application", "applications", "appli", "applis", "logiciel",
          "logiciels", "programme", "programmes", "app", "apps", "software")
_SCAN = ("scan", "scanne", "scanner", "scans", "analyse", "analyser", "analysez",
         "examine", "examiner", "inspecte", "inspecter", "verifie", "verifier",
         "controle", "controler", "scanning")
_QUARANTAINE = ("quarantaine", "quarantine", "isole", "isolement")
_BOUCLIER = ("bouclier", "rancongiciel", "rancongiciels", "ransomware", "leurre",
             "leurres", "canari", "canaris", "shield", "chiffrement")
_TEMPS_REEL = ("temps reel", "realtime", "real time", "surveillance continue",
               "protection continue", "temps-reel")
_TEMP = ("temporaire", "temporaires", "temp", "tmp", "cache", "caches",
         "corbeille", "poubelle", "junk", "temporary", "trash", "recycle")
_SAS = ("sas", "staging", "cote")
_RANGER = ("range", "ranger", "rangement", "organise", "organiser",
           "organisation", "classe", "classer", "organize", "tidy")
_PLANIF = ("planifie", "planifier", "planification", "planifiee", "programme",
           "programmer", "schedule", "automatique", "automatiquement",
           "hebdomadaire", "quotidien", "quotidienne", "tache", "taches")
_GARDIEN = ("gardien", "guardian")
_HISTORIQUE = ("historique", "history", "journal")
_CAMERA = ("camera", "webcam", "micro", "microphone", "cam")
_RESEAU = ("connexion", "connexions", "reseau", "network", "internet", "trafic",
           "port", "ports")
_INCIDENT = ("incident", "confinement", "confine", "urgence", "panique")
_RESIDU = ("residu", "residus", "residuel", "residuels", "residuelle",
           "residuelles", "restes", "leftovers", "orphelin", "orphelins",
           "orpheline", "orphelines")

# Verbes qui expriment une VOLONTÉ D'AGIR. Une capacité destructive dont la
# phrase n'en contient aucun voit sa confiance effondrée — voir `_verdict`.
_VERBES_ACTION = tuple(sorted(set(
    _SUPPRIMER + _NETTOYER + _DEMARRER + _ARRETER + _RANGER + (
        "applique", "appliquer", "execute", "executer", "fais", "faire",
        "confirme", "confirmer", "valide", "valider", "deplace", "deplacer",
        "bouge", "bouger", "desinstalle", "desinstaller", "desinstallation",
        "degraisse", "degraisser", "debloat", "uninstall", "apply", "move",
        "stage", "do", "purge", "trie", "trier", "tri",
    )
)))

# Même contenu, en ensemble, POUR LES SEULS TESTS D'APPARTENANCE de
# `_negation`. Un `set` n'est jamais parcouru ici, seulement interrogé : le
# défaut de `security/network_watch.py` venait d'une *itération* sur un
# ensemble, pas d'un `in`, et celui-ci ne change rien à l'ordre de sortie.
_VERBES_ACTION_SET = frozenset(_VERBES_ACTION)


# ── Négation ───────────────────────────────────────────────────────────────
# Le module lisait la phrase comme un sac de mots : « ne vide pas la
# quarantaine » contenait « quarantaine » et « vide », donc déclenchait la
# purge définitive avec 65 % de certitude. Un utilisateur qui écrit
# explicitement de NE PAS faire quelque chose se voyait proposer exactement
# cette chose — le pire contresens possible pour cette couche, puisque le
# contrat rappelle que « deviner, ici, c'est lancer une suppression que
# personne n'a demandée ».
#
# La négation française est DISCONTINUE (« ne … pas »), et c'est précisément
# ce qui permet de la reconnaître sans casser le vocabulaire : « jamais » est
# un mot-clé légitime de `rangement.peu_utilises` (« range les fichiers
# jamais ouverts »), donc « jamais » seul ne peut pas valoir négation. On
# exige soit la paire particule + adverbe, soit une construction sans
# ambiguïté possible (« pas de », « sans supprimer », « arrête de vider »),
# soit un verbe d'interdiction.
_NEG_PARTICULES = ("ne", "n")
_NEG_ADVERBES = ("pas", "plus", "jamais", "rien", "aucun", "aucune",
                 "guere", "point")
_NEG_INTERDITS = ("interdit", "interdis", "interdire", "interdiction",
                  "evite", "eviter", "epargne", "epargner")
_NEG_CESSATIFS = ("arrete", "arreter", "cesse", "cesser", "stoppe", "stopper")
_NEG_DETERMINANTS = ("de", "d", "du", "des")


def _negation(mots: Tuple[str, ...]) -> bool:
    """La phrase interdit-elle l'action au lieu de la demander ?

    Volontairement prudente dans le sens qui protège : un faux positif coûte
    une reformulation à l'utilisateur, un faux négatif coûte des fichiers.
    Le contrat tranche ce compromis une fois pour toutes — « face à
    l'inconnu, on demande ».
    """
    if any(m in _NEG_INTERDITS for m in mots):
        return True
    if (any(m in _NEG_PARTICULES for m in mots)
            and any(m in _NEG_ADVERBES for m in mots)):
        return True
    for courant, suivant in zip(mots, mots[1:]):
        # « pas de nettoyage », « jamais de purge » : l'adverbe nie
        # directement le nom de l'action, sans particule.
        if courant in ("pas", "jamais") and suivant in _NEG_DETERMINANTS:
            return True
        # « sans supprimer la quarantaine », « jamais supprimer »
        if courant in ("sans", "jamais") and suivant in _VERBES_ACTION_SET:
            return True
        # « arrête de vider » : ordre d'interrompre, pas d'exécuter. La paire
        # est exigée (« arrête la surveillance » reste un ordre normal, et
        # c'est une capacité à part entière).
        if courant in _NEG_CESSATIFS and suivant in _NEG_DETERMINANTS:
            return True
    return False


@dataclasses.dataclass(frozen=True)
class _Regle:
    """Une lecture possible d'une phrase.

    `noyau` : des groupes de synonymes dont CHACUN doit être représenté. C'est
    la conjonction qui fait la précision — « quarantaine » seul ne dit pas ce
    qu'on veut en faire.

    `renforts` : des mots qui confirment sans être nécessaires ; ils montent la
    confiance, jamais ils ne la créent.

    `exclusions` : des mots qui disqualifient la règle. Servent à départager
    deux lectures d'une même phrase, typiquement la version « je regarde » et
    la version « j'agis » d'un même sujet.
    """

    capacite: str
    noyau: Tuple[Tuple[str, ...], ...]
    renforts: Tuple[str, ...] = ()
    exclusions: Tuple[str, ...] = ()


_REGLES: Tuple[_Regle, ...] = (
    # ── Protection ─────────────────────────────────────────────────────────
    _Regle("scan.fichier", (_SCAN, ("fichier", "file", "document", "piece", "jointe", "exe")),
           ("ce", "cet", "chemin", "suspect", "suspecte", "telecharge", "virus", "malware")),
    _Regle("scan.dossier", (_SCAN,),
           ("dossier", "repertoire", "disque", "pc", "ordinateur", "machine",
            "systeme", "tout", "complet", "complete", "rapide", "profond",
            "virus", "menace", "menaces", "folder", "directory", "full", "quick",
            "lance", "lancer", "demarre", "demarrer", "run", "start")),
    _Regle("temps_reel.demarrer", (_TEMPS_REEL, _DEMARRER),
           ("protection", "fond", "permanente", "surveillance"),
           _CAMERA + _BOUCLIER),
    _Regle("temps_reel.arreter", (_TEMPS_REEL, _ARRETER),
           ("protection", "fond", "permanente", "surveillance"),
           _CAMERA + _BOUCLIER),
    _Regle("quarantaine.lister", (_QUARANTAINE, _VOIR),
           ("fichiers", "combien", "dedans"),
           _SUPPRIMER + _RESTAURER),
    _Regle("quarantaine.restaurer", (_QUARANTAINE, _RESTAURER),
           ("fichier", "faux", "positif", "erreur")),
    _Regle("quarantaine.supprimer", (_QUARANTAINE, _SUPPRIMER),
           ("definitivement", "definitive", "pour", "toujours", "vraiment")),
    _Regle("bouclier.demarrer", (_BOUCLIER, _DEMARRER), ("protection", "dossiers")),
    _Regle("bouclier.etat", (_BOUCLIER, _VOIR), ("actif", "intacts", "seuil")),
    _Regle("bouclier.processus", (("processus", "process", "taches", "tasks"),
                                  ("suspect", "suspects", "suspicious", "ecriture",
                                   "ecrivent", "massive", "anormal", "anormaux")),
           _BOUCLIER + ("quels", "liste", "montre")),
    _Regle("bouclier.arreter", (_BOUCLIER, _ARRETER), ("protection", "dossiers")),
    _Regle("reputation.verifier", (("reputation", "virustotal"),),
           ("fichier", "verifie", "verifier", "hash", "empreinte", "sha256",
            "connu", "dangereux", "avis", "moteurs", "check"),
           ("cle", "clef", "api", "configure", "configuree", "configurer", "configuration")),
    _Regle("reputation.configuree", (("reputation", "virustotal"),
                                     ("cle", "clef", "api", "configure", "configuree",
                                      "configurer", "configuration", "key")),
           ("est", "elle", "ai", "je")),
    _Regle("hameconnage.verifier", (("hameconnage", "phishing", "lien", "liens", "url",
                                     "adresse", "site"),),
           ("verifie", "verifier", "suspect", "suspecte", "danger", "dangereux",
            "arnaque", "fraude", "cliquer", "clic", "mail", "courriel", "sur",
            "sure", "fiable", "safe", "check")),

    # ── Nettoyage ──────────────────────────────────────────────────────────
    _Regle("nettoyage.complet", (_TEMP, _NETTOYER),
           ("fichiers", "disque", "espace", "place", "navigateur", "navigateurs",
            "windows", "tout")),
    _Regle("disque.analyser", (("disque", "espace", "stockage", "place", "disk",
                                "space", "storage", "volume"),),
           ("occupation", "occupe", "prend", "gros", "grosse", "volumineux",
            "doublons", "plein", "libre", "reste", "manque", "quoi", "combien",
            "utilise", "satures", "sature")),
    _Regle("residus.raccourcis", (("raccourci", "raccourcis", "shortcut", "shortcuts"),),
           ("orphelin", "orphelins", "casse", "casses", "mort", "morts", "bureau",
            "inutile", "inutiles", "broken", "liste", "montre", "cherche")),
    _Regle("residus.registre", (("registre", "registry", "regedit"),),
           ("orphelin", "orphelines", "orphelins", "entree", "entrees", "cle",
            "cles", "desinstallation", "residu", "residus", "nettoie", "nettoyer",
            "liste", "montre")),
    _Regle("residus.dossiers", (_RESIDU, ("dossier", "dossiers", "folder", "folders",
                                          "repertoire", "repertoires")),
           ("desinstalle", "desinstalles", "programmes", "restes", "liste", "montre"),
           _NETTOYER),
    _Regle("residus.nettoyer", (_RESIDU, _NETTOYER),
           ("desinstallation", "programmes", "raccourcis", "registre", "dossiers")),
    _Regle("applications.lister", (_APPLI, _VOIR),
           ("installe", "installes", "installees", "taille", "poids", "combien",
            "grosses", "inutilisees")),
    _Regle("applications.desinstaller", (_APPLI, ("desinstalle", "desinstaller",
                                                  "desinstallation", "uninstall",
                                                  "vire", "virer", "enleve", "enlever")
                                         + _SUPPRIMER),
           ("cette", "ce", "completement"),
           ("preinstalle", "preinstallee", "preinstallees", "preinstalles",
            "bloatware", "superflu", "superflues", "superflus", "debloat",
            "degraisse", "degraisser")),
    _Regle("applications.degraisser", (("bloatware", "preinstalle", "preinstallee",
                                        "preinstallees", "preinstalles", "superflu",
                                        "superflus", "superflues", "debloat",
                                        "degraisse", "degraisser"),),
           ("application", "applications", "apps", "windows", "supprime", "supprimer",
            "retire", "retirer", "nettoie", "store", "inutiles", "livrees")),

    # ── Rangement ──────────────────────────────────────────────────────────
    _Regle("tri.analyser", (("tri", "trie", "trier", "triage", "sort"),),
           ("fichiers", "dossier", "telechargements", "bureau", "repere", "propose",
            "candidats", "quoi", "analyse", "regarde")),
    _Regle("tri.appliquer", (_SAS, ("mets", "mettre", "deplace", "deplacer", "envoie",
                                    "envoyer", "range", "ranger", "move", "stage",
                                    "applique", "appliquer")),
           ("fichiers", "ces", "selection")),
    _Regle("sas.lister", (_SAS, _VOIR), ("fichiers", "combien", "dedans"),
           _SUPPRIMER + _RESTAURER),
    _Regle("sas.restaurer", (_SAS, _RESTAURER), ("fichier", "erreur")),
    _Regle("sas.purger", (_SAS, _SUPPRIMER), ("vieux", "anciens", "jours", "definitivement")),
    _Regle("rangement.plan", (_RANGER,),
           ("plan", "propose", "proposer", "comment", "suggere", "dossier",
            "fichiers", "categorie", "telechargements", "bureau", "images",
            "documents", "quoi"),
           ("applique", "appliquer", "execute", "executer", "confirme", "confirmer",
            "annule", "annuler", "undo", "session", "sessions")),
    _Regle("rangement.appliquer", (_RANGER, ("applique", "appliquer", "execute",
                                             "executer", "confirme", "confirmer",
                                             "valide", "valider", "apply")),
           ("plan", "propose", "maintenant", "vas")),
    _Regle("rangement.deplacer_dossier", (("deplace", "deplacer", "move", "bouge", "bouger"),
                                          ("dossier", "repertoire", "folder")),
           ("dans", "vers", "sous", "into", "cible")),
    _Regle("rangement.peu_utilises", (("peu", "rarement", "jamais", "vieux", "anciens",
                                       "ancien", "oublies", "least", "inutilises"),
                                      ("utilise", "utilises", "utilisees", "ouvert",
                                       "ouverts", "touche", "touches", "servi", "used",
                                       "opened", "fichiers")),
           ("range", "ranger", "rangement", "dossier", "jours", "mois")),
    _Regle("rangement.sessions", (("session", "sessions"),),
           ("rangement", "rangements", "liste", "lister", "montre", "precedents",
            "annuler", "faites")),
    _Regle("rangement.annuler", (_ANNULER, ("rangement", "rangements", "session",
                                            "deplacement", "deplacements", "organisation")),
           ("dernier", "derniere", "tout")),

    # ── Système ────────────────────────────────────────────────────────────
    _Regle("etat.systeme", (("etat", "statut", "status", "sante", "diagnostic",
                             "resume", "apercu", "tableau", "bord", "dashboard",
                             "overview"),),
           ("systeme", "machine", "pc", "ordinateur", "general", "global",
            "protection", "protections", "modules", "situation", "va")),
    _Regle("demarrage.lister", (("demarrage", "startup", "boot"),),
           ("programme", "programmes", "application", "applications", "liste",
            "lister", "montre", "quels", "lance", "lancent", "windows", "session",
            "ralentit", "lent")),
    _Regle("demarrage.desactiver", (("demarrage", "startup", "boot"),
                                    ("desactive", "desactiver", "disable", "empeche",
                                     "empecher", "bloque", "bloquer", "enleve",
                                     "enlever", "retire", "retirer")
                                    + _SUPPRIMER),
           ("programme", "application", "lance", "plus", "automatiquement")),
    _Regle("demarrage.restaurer", (("demarrage", "startup", "boot"),
                                   _RESTAURER + ("reactive", "reactiver", "enable")),
           ("programme", "application", "desactive")),
    _Regle("planification.nettoyage", (_PLANIF, _NETTOYER),
           ("semaine", "hebdomadaire", "jour", "heure", "dimanche", "chaque", "toutes"),
           _GARDIEN + _ARRETER),
    _Regle("planification.retirer", (_PLANIF, _ARRETER + _ANNULER),
           ("nettoyage", "semaine", "hebdomadaire", "plus"),
           _GARDIEN),
    _Regle("gardien.executer", (_GARDIEN, _DEMARRER + ("execute", "executer", "fais",
                                                       "faire", "passe", "tourne")),
           ("passe", "maintenant", "complet", "dossiers")),
    _Regle("gardien.en_attente", (_GARDIEN, ("attente", "attend", "pending", "cote",
                                             "reserve") + _VOIR),
           ("suppression", "suppressions", "elements", "combien")),
    _Regle("gardien.confirmer", (_GARDIEN, ("confirme", "confirmer", "valide",
                                            "valider", "definitive", "definitivement",
                                            "confirm") + _SUPPRIMER),
           ("suppression", "suppressions", "jours", "vieux", "anciens"),
           _PLANIF),
    _Regle("gardien.planifier", (_GARDIEN, _PLANIF),
           ("jour", "chaque", "heure", "quotidien", "matin"),
           _ARRETER + _ANNULER),
    _Regle("gardien.deplanifier", (_GARDIEN, _PLANIF,
                                   _ARRETER + _ANNULER + ("deplanifie", "deplanifier")
                                   + _SUPPRIMER),
           ("plus", "quotidien", "jour")),
    _Regle("historique.lister", (_HISTORIQUE, _VOIR),
           ("actions", "action", "derniere", "dernieres", "fait", "faites",
            "hier", "recent", "recentes"),
           _ANNULER),
    _Regle("historique.annuler", (_HISTORIQUE, _ANNULER),
           ("action", "derniere", "entree")),

    # ── Sécurité avancée ───────────────────────────────────────────────────
    _Regle("reseau.connexions", (_RESEAU,),
           ("liste", "lister", "montre", "etablies", "sortantes", "ouvertes",
            "qui", "dehors", "exterieur", "parle", "communique", "actives",
            "suspectes", "quelles")),
    _Regle("reseau.applications", (_RESEAU, _APPLI),
           ("quelles", "resume", "regroupe", "utilisent", "parlent", "communiquent")),
    _Regle("intrusion.rapport", (("connecte", "connectee", "connectes", "intrusion",
                                 "intrusions", "ouverture", "logon", "login",
                                 "authentification", "identifie"),),
           ("qui", "pc", "ordinateur", "machine", "tentative", "tentatives",
            "echec", "echecs", "distance", "rdp", "quand", "hier", "jours",
            "semaine", "session", "sessions", "rapport")),
    _Regle("intrusion.audit", (("audit", "auditer", "auditee", "journalisation",
                                "journaliser"),
                               ("dossier", "dossiers", "acces", "fichiers", "folder")),
           ("active", "activer", "enable", "windows", "trace", "tracer")),
    _Regle("camera.etat", (_CAMERA, _VOIR),
           ("acces", "autorisees", "autorisation", "allumee", "utilise", "qui")),
    _Regle("camera.recentes", (_CAMERA, ("recent", "recente", "recentes", "recents",
                                         "derniere", "dernieres", "utilisation",
                                         "utilisations", "hier", "heures", "quand",
                                         "historique")),
           ("qui", "application", "applications", "allumee", "utilise")),
    _Regle("camera.autoriser", (_CAMERA, ("autorise", "autoriser", "autorisation",
                                          "permets", "permettre", "accepte",
                                          "accepter", "allow", "confiance")),
           ("application", "zoom", "teams", "skype", "toujours"),
           ("retire", "retirer", "revoque", "revoquer", "interdis", "interdire",
            "enleve", "enlever", "bloque", "bloquer", "supprime", "supprimer")),
    _Regle("camera.retirer", (_CAMERA, ("retire", "retirer", "revoque", "revoquer",
                                        "interdis", "interdire", "enleve", "enlever",
                                        "bloque", "bloquer", "revoke") + _SUPPRIMER),
           ("autorisation", "acces", "application", "plus")),
    _Regle("camera.surveiller", (_CAMERA, ("surveille", "surveiller", "surveillance",
                                           "alerte", "alerter", "previens", "prevenir",
                                           "watch", "monitor") + _DEMARRER),
           ("espionne", "espion", "alerte", "acces")),
    _Regle("camera.arreter", (_CAMERA, _ARRETER),
           ("surveillance", "alerte", "alertes", "plus")),
    _Regle("incident.etat", (_INCIDENT, _VOIR), ("mode", "actif", "depuis", "encore")),
    _Regle("incident.plan", (_INCIDENT, ("plan", "prevoir", "ferait", "simule",
                                         "simuler", "apercu", "avant", "preview",
                                         "consequences")),
           ("mode", "quoi", "montre")),
    _Regle("incident.activer", (_INCIDENT, _DEMARRER + ("declenche", "declencher",
                                                        "passe", "confine", "confiner",
                                                        "isole", "isoler")),
           ("mode", "urgence", "attaque", "maintenant", "vite")),
    _Regle("incident.retablir", (_INCIDENT, _RESTAURER + ("sors", "sortir", "sortie",
                                                          "quitte", "quitter",
                                                          "desactive", "desactiver",
                                                          "arrete", "arreter",
                                                          "normal")),
           ("mode", "reseau", "fini")),
)


# ── Extraction des paramètres ──────────────────────────────────────────────
# Volontairement chiche : on ne renseigne que ce qui est écrit noir sur blanc
# dans la phrase et que la capacité déclare accepter. Deviner un paramètre est
# aussi dangereux que deviner une capacité — un chemin supposé, c'est un
# nettoyage au mauvais endroit. Le reste est demandé par l'interface.

_MOTIF_CHEMIN = re.compile(
    r"""["']([^"']{2,})["']"""                 # « analyse "C:\Mes docs\x" »
    # Triple guillemets ici AUSSI : la classe `[^\s"']` contient un guillemet
    # double, qui refermait la chaîne simple et faisait de ce fichier un
    # module inimportable — `SyntaxError` à la lecture, pas à l'exécution.
    # Personne ne l'avait vu parce que personne ne l'importait : c'est le seul
    # module de la couche assistant sans test dédié, et le pont le remplace
    # par un double dans `test_bridge_assistant.py`. Une suite verte ne
    # prouve que ce qu'elle charge.
    r"""|((?:[A-Za-z]:[\\/]|~[\\/]|/)[^\s"']+)"""  # chemin absolu, Windows/POSIX
)
_MOTIF_URL = re.compile(r"\b((?:https?://|www\.)[^\s\"'<>]+)")
_MOTIF_NOMBRE = re.compile(r"(?<![\w./\\-])(\d{1,6})(?![\w./\\-])")

# Les paramètres numériques des capacités, dans l'ordre où on tente de les
# renseigner à partir du premier nombre trouvé dans la phrase.
_PARAMS_NOMBRE = ("jours", "heures", "older_than_days", "days",
                  "unused_threshold_days", "limite", "top_n")


def _extraire_parametres(phrase: str, capacite: Capacite) -> Dict[str, object]:
    params: Dict[str, object] = {}
    acceptes = tuple(capacite.parametres)

    reste = phrase
    if "url" in acceptes:
        trouve = _MOTIF_URL.search(phrase)
        if trouve:
            params["url"] = trouve.group(1)
            reste = reste.replace(trouve.group(1), " ")

    if "path" in acceptes or "source" in acceptes:
        trouve = _MOTIF_CHEMIN.search(reste)
        if trouve:
            chemin = (trouve.group(1) or trouve.group(2) or "").rstrip(".,;:!?")
            if chemin:
                params["path" if "path" in acceptes else "source"] = chemin
                reste = reste.replace(chemin, " ")

    for nom in _PARAMS_NOMBRE:
        if nom in acceptes:
            trouve = _MOTIF_NOMBRE.search(reste)
            if trouve:
                params[nom] = int(trouve.group(1))
            break                      # un seul nombre, celui qui saute aux yeux

    return params


# ── Évaluation ─────────────────────────────────────────────────────────────

def _score(regle: _Regle, mots: Tuple[str, ...], plate: str) -> float:
    for terme in regle.exclusions:
        if _apparie(terme, mots, plate) == 1.0:
            return 0.0

    facteur = 1.0
    for groupe in regle.noyau:
        meilleur = 0.0
        for terme in groupe:
            valeur = _apparie(terme, mots, plate)
            if valeur > meilleur:
                meilleur = valeur
            if meilleur == 1.0:
                break
        if meilleur == 0.0:
            return 0.0                 # un groupe manquant tue la règle
        facteur *= meilleur

    renforts = 0
    for terme in regle.renforts:
        if _apparie(terme, mots, plate) == 1.0:
            renforts += 1

    base = _BASE + _BONUS_GROUPE * (len(regle.noyau) - 1)
    bonus = min(_PLAFOND_RENFORTS, _POIDS_RENFORT * renforts)
    return facteur * (base + bonus)


def _verdict(capacite: Capacite, score: float, mots: Tuple[str, ...],
             plate: str) -> Tuple[float, str]:
    """Applique les garde-fous. Rend (score, mention).

    Deux garde-fous, à deux seuils différents, et l'écart entre les deux est
    délibéré :

    * La **négation** est examinée dès `REVERSIBLE`, c'est-à-dire dès que la
      capacité touche au disque ou désarme une protection. « N'arrête pas la
      surveillance » ne doit pas arrêter la surveillance, même si l'action
      est annulable.
    * Le **verbe d'action** n'est exigé qu'à partir de `DESTRUCTIF`.

    Les niveaux `LECTURE` échappent aux deux, et c'est voulu : refuser une
    lecture coûte une reformulation sans rien protéger, et les négations
    incidentes se rencontrent surtout là (« quels programmes ne se lancent
    pas au démarrage ? » est une question, pas une interdiction).
    """
    if int(capacite.risque) >= int(Risque.REVERSIBLE) and _negation(mots):
        return score * _FACTEUR_NEGATION, (
            " La phrase contient une négation (« ne… pas », « sans », "
            "« arrête de »…) : elle interdit l'action plutôt qu'elle ne la "
            "demande, et cette capacité écrit. La lecture est écartée."
        )
    if int(capacite.risque) < int(Risque.DESTRUCTIF):
        return score, ""
    for verbe in _VERBES_ACTION:
        if _apparie(verbe, mots, plate) == 1.0:
            return score, ""
    return score * _FACTEUR_SANS_VERBE, (
        " Aucun verbe d'action explicite dans la phrase alors que cette "
        "capacité est " + libelle(capacite.risque) + " : la lecture est écartée."
    )


def comprendre(phrase: str, registre: Registre) -> Intention:
    """Lit une phrase et rend l'intention la plus probable.

    Ne lance RIEN, ne consulte RIEN d'autre que le registre reçu. Une capacité
    absente du registre n'est jamais proposée, même si une règle la décrit :
    c'est ce qui permet à l'interface de restreindre le vocabulaire selon ce
    qui est réellement disponible sur la machine.
    """
    if not isinstance(phrase, str) or not phrase.strip():
        return Intention("", {}, 0.0, "Phrase vide : il n'y a rien à comprendre.")

    plate, mots = _normaliser(phrase)
    if not mots:
        return Intention("", {}, 0.0,
                         "Phrase sans aucun mot exploitable : reformulez avec des "
                         "mots, par exemple « analyse le dossier Téléchargements ».")

    candidats = []
    for regle in _REGLES:
        capacite = registre.obtenir(regle.capacite)
        if capacite is None:
            continue                   # capacité non offerte ici : on l'ignore
        score = _score(regle, mots, plate)
        if score <= 0.0:
            continue
        score, mention = _verdict(capacite, score, mots, plate)
        candidats.append((score, regle.capacite, capacite, mention))

    if not candidats:
        return Intention("", {}, 0.0, _refus_general())

    # Tri sur (-score, nom) : à score égal, c'est le nom qui départage, jamais
    # l'ordre de déclaration ni celui d'un dictionnaire.
    candidats.sort(key=lambda c: (-c[0], c[1]))
    score, nom, capacite, mention = candidats[0]

    ambigu = ""
    if len(candidats) > 1:
        second = candidats[1]
        if score - second[0] < _ECART_AMBIGU:
            score *= _FACTEUR_AMBIGU
            ambigu = (" Lecture concurrente presque aussi probable : « "
                      + second[2].titre + " » — d'où la confiance rabattue.")

    confiance = round(max(0.0, min(1.0, score)), 3)

    if confiance < SEUIL_CONFIANCE:
        return Intention("", {}, confiance,
                         "Lecture la plus probable : « " + capacite.titre
                         + " », mais la certitude ("
                         + _pourcent(confiance) + ") reste sous le seuil de "
                         + _pourcent(SEUIL_CONFIANCE) + " requis pour agir."
                         + mention + ambigu
                         + " Reformulez en nommant l'action et son objet.")

    parametres = _extraire_parametres(phrase, capacite)
    justification = ("« " + capacite.titre + " » (" + libelle(capacite.risque)
                     + ") reconnue avec une certitude de " + _pourcent(confiance)
                     + "." + ambigu)
    if parametres:
        justification += (" Paramètres lus dans la phrase : "
                          + ", ".join(f"{c} = {v}" for c, v in sorted(parametres.items()))
                          + ".")
    return Intention(nom, parametres, confiance, justification)


def _pourcent(valeur: float) -> str:
    return str(int(round(valeur * 100))) + " %"


def _refus_general() -> str:
    return ("Aucune capacité ne correspond à cette phrase. Nommez l'action et "
            "son objet — par exemple « analyse le dossier Téléchargements », "
            "« liste la quarantaine » ou « qui s'est connecté à mon PC ».")
