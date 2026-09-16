"""
proactive.py — proposer, jamais décider.

C'est le module qui transforme ANTI-ZEEVIRIUS d'un outil qu'on ouvre en un
gardien qui veille. Il regarde l'état constaté par ailleurs (télémétrie,
surveillances, inventaires) et en tire des propositions : « le volume système
est plein à 94 %, un nettoyage des fichiers temporaires est disponible ».

Trois règles de conception, toutes motivées par le même souci :

* **Fonction pure.** `suggerer()` ne lit pas le disque, n'exécute rien,
  n'ouvre aucune connexion. Tout ce qu'elle sait lui est passé en paramètre.
  C'est la seule façon de pouvoir affirmer qu'une suggestion ne peut pas, par
  construction, déclencher l'action qu'elle suggère — la frontière entre
  proposer et faire est ici structurelle, pas une question de discipline.

* **Déterminisme.** Même état, mêmes suggestions, dans le même ordre. Aucun
  `set` n'est parcouru, tous les tris sont totaux (urgence, puis nom, puis
  motif). Ce dépôt a déjà connu un ordre dépendant de `PYTHONHASHSEED` dans
  `security/network_watch.py` ; une interface dont les propositions changent
  de place à chaque ouverture est une interface qu'on cesse de lire.

* **Le refus compte.** Une suggestion écartée deux fois est mise en sourdine.
  Insister est un défaut : l'utilisateur qui a dit non deux fois a déjà donné
  sa réponse, et la troisième relance ne convainc personne — elle apprend
  seulement à ignorer le bandeau.

Les capacités suggérées sont retrouvées dans le `Registre` par l'action web
qu'elles exposent (`gui/bridge.py`), seule clé figée par le contrat d'API.
Suggérer une capacité imaginaire serait pire que ne rien suggérer.

**Statut exact de `NOMS_PAR_DEFAUT`, à lire avant de s'y fier.** Ce n'est pas
un second catalogue : aucun de ces noms de repli n'existe dans
`construire_registre_par_defaut()`, qui nomme les mêmes capacités autrement
(`nettoyage.complet` et non `nettoyage.temporaires`, `camera.etat` et non
`securite.camera`…). Ils ne sont jamais atteints en production, parce que le
registre par défaut publie les treize actions concernées et que la table
construite à partir de lui a toujours la priorité. Ils ne servent qu'à
nommer *quelque chose* de stable lorsqu'un appelant fournit un registre
restreint ou cassé. Les laisser passer pour des capacités existantes serait
en revanche un piège pour la relecture suivante — d'où cet avertissement.
"""

from __future__ import annotations

import dataclasses
import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Tuple

if TYPE_CHECKING:            # pragma: no cover - uniquement pour les types
    from assistant.memory import Memoire
    from assistant.registry import Registre

__all__ = [
    "Suggestion", "suggerer",
    "noter_refus", "est_en_sourdine", "oublier_refus",
    "CLE_REFUS", "SEUIL_SOURDINE", "NOMS_PAR_DEFAUT",
]


# ── Réglages ───────────────────────────────────────────────────────────────
# Deux refus, pas un : le premier peut être un « pas maintenant », le second
# est une opinion.
SEUIL_SOURDINE = 2
CLE_REFUS = "suggestions.refus"

SEUIL_DISQUE_CRITIQUE = 90.0     # au-delà, Windows lui-même commence à souffrir
SEUIL_DISQUE_ALERTE = 80.0
SEUIL_MEMOIRE = 90.0
SEUIL_CPU = 90.0
SEUIL_QUARANTAINE = 10           # éléments en quarantaine avant de proposer un tri
AGE_SIGNATURES_JOURS = 7
AGE_SCAN_JOURS = 7
JOUR = 86400.0

# Nom de repli d'une capacité, utilisé tant que le registre ne la publie pas.
# La clé est l'action web (`gui/bridge.py`), qui est, elle, figée par le
# contrat d'API : c'est l'ancrage le plus stable dont on dispose.
NOMS_PAR_DEFAUT: Dict[str, str] = {
    "camera_state": "securite.camera",
    "clean_full": "nettoyage.temporaires",
    "disk_analyze": "systeme.analyse_disque",
    "incident_state": "securite.incident",
    "network_connections": "securite.reseau",
    "quarantine_list": "protection.quarantaine",
    "realtime_start": "protection.temps_reel",
    "scan_directory": "scan.dossier",
    "shield_processes": "protection.processus_suspects",
    "shield_start": "protection.bouclier_rancongiciel",
    "staging_list": "rangement.sas",
    "startup_list": "systeme.demarrage",
    # L'état système est la seule capacité qui rende compte de la fraîcheur des
    # bases : la mise à jour elle-même (`optimizer/signature_updater.py`) n'est
    # pas exposée par le pont, donc pas nommable comme capacité. On renvoie
    # donc vers ce qui existe, le motif portant le fait constaté.
    "status": "systeme.etat",
}


@dataclasses.dataclass(frozen=True)
class Suggestion:
    capacite: str
    motif: str             # le fait constaté qui la motive
    urgence: int           # 0 (confort) à 3 (sécurité immédiate)

    def to_dict(self) -> Dict:
        return dataclasses.asdict(self)


# ── Sourdine ───────────────────────────────────────────────────────────────
def _refus(memoire: "Memoire") -> Dict[str, int]:
    """Compteur de refus par capacité, tel que la mémoire le conserve."""
    try:
        brut = memoire.preference(CLE_REFUS, {})
    except Exception:
        # Une mémoire illisible ne doit pas priver l'utilisateur de ses
        # suggestions : au pire, on réaffiche une proposition déjà écartée.
        return {}
    if not isinstance(brut, Mapping):
        return {}
    compte: Dict[str, int] = {}
    for cle in sorted(brut):                 # tri : aucun ordre de dict subi
        try:
            compte[str(cle)] = int(brut[cle])
        except (TypeError, ValueError):
            continue
    return compte


def est_en_sourdine(memoire: "Memoire", capacite: str) -> bool:
    return _refus(memoire).get(capacite, 0) >= SEUIL_SOURDINE


def noter_refus(memoire: "Memoire", capacite: str) -> int:
    """Enregistre qu'une suggestion a été écartée. Rend le total de refus.

    Le compteur vit dans les préférences et non dans le journal : le journal
    raconte l'histoire, les préférences décident du comportement, et mélanger
    les deux ferait dépendre l'affichage d'une purge d'historique.
    """
    compte = _refus(memoire)
    compte[capacite] = compte.get(capacite, 0) + 1
    memoire.definir_preference(CLE_REFUS, compte)
    memoire.journaliser("suggestion.refusee",
                        {"capacite": capacite, "refus": compte[capacite]})
    return compte[capacite]


def oublier_refus(memoire: "Memoire", capacite: str) -> None:
    """Lève la sourdine. L'utilisateur change d'avis, l'assistant aussi."""
    compte = _refus(memoire)
    if compte.pop(capacite, None) is not None:
        memoire.definir_preference(CLE_REFUS, compte)
        memoire.journaliser("suggestion.reactivee", {"capacite": capacite})


# ── Lecture de l'état ──────────────────────────────────────────────────────
def _sous(etat: Mapping, cle: str) -> Mapping:
    valeur = etat.get(cle) if isinstance(etat, Mapping) else None
    return valeur if isinstance(valeur, Mapping) else {}


def _nombre(source: Mapping, cle: str, defaut: float = 0.0) -> float:
    try:
        return float(source.get(cle, defaut))
    except (TypeError, ValueError):
        return defaut


def _entier(source: Mapping, cle: str, defaut: int = 0) -> int:
    try:
        return int(source.get(cle, defaut))
    except (TypeError, ValueError):
        return defaut


def _taille(valeur: Any) -> int:
    """Nombre d'éléments d'un champ qui peut être une liste ou un compteur."""
    if isinstance(valeur, (list, tuple)):
        return len(valeur)
    try:
        return int(valeur)
    except (TypeError, ValueError):
        return 0


def _table_capacites(registre: Optional["Registre"]) -> Dict[str, str]:
    """Action web → nom de capacité, d'après le registre.

    Le registre est la source de vérité des noms ; on ne les devine que
    lorsqu'il ne connaît pas encore la capacité. `toutes()` étant triée, la
    table construite ici l'est aussi : deux capacités partageant une action
    donneraient toujours le même gagnant.
    """
    table: Dict[str, str] = {}
    if registre is None:
        return table
    try:
        capacites = registre.toutes()
    except Exception:
        return table
    for c in capacites:
        action = getattr(c, "action_bridge", "") or ""
        nom = getattr(c, "nom", "") or ""
        if action and nom and action not in table:
            table[action] = nom
    return table


# ── Cœur ───────────────────────────────────────────────────────────────────
def suggerer(etat: dict, memoire: "Memoire",
             registre: "Registre") -> Tuple[Suggestion, ...]:
    """Ce que l'assistant proposerait, au vu de l'état fourni.

    Ne touche à rien : pas de disque, pas de réseau, pas d'exécution. Les
    suggestions sont rendues par urgence décroissante ; à urgence égale, par
    nom de capacité puis par motif, pour que l'ordre soit total et reproductible.
    """
    etat = etat if isinstance(etat, Mapping) else {}
    table = _table_capacites(registre)
    brutes: List[Suggestion] = []

    def proposer(action: str, motif: str, urgence: int,
                 nom_defaut: Optional[str] = None) -> None:
        nom = table.get(action) or nom_defaut or NOMS_PAR_DEFAUT.get(action, action)
        brutes.append(Suggestion(capacite=nom, motif=motif, urgence=urgence))

    # ── Urgence 3 : quelqu'un ou quelque chose agit maintenant ─────────────
    camera = _sous(etat, "camera")
    alertes_camera = _taille(camera.get("alertes"))
    if alertes_camera:
        proposer("camera_state",
                 f"{alertes_camera} application(s) utilisent la caméra ou le "
                 f"microphone sans autorisation déclarée", 3)

    menaces = _entier(_sous(etat, "menaces"), "en_attente")
    if menaces > 0:
        proposer("quarantine_list",
                 f"{menaces} menace(s) détectée(s) attendent une décision", 3)

    # ── Urgence 2 : une protection manque, ou le système est au bord ───────
    temps_reel = _sous(etat, "temps_reel")
    if "actif" in temps_reel and not temps_reel.get("actif"):
        proposer("realtime_start",
                 "la surveillance en temps réel des dossiers sensibles est "
                 "arrêtée", 2)

    reseau = _entier(_sous(etat, "reseau"), "connexions_suspectes")
    if reseau > 0:
        proposer("network_connections",
                 f"{reseau} connexion(s) réseau sortante(s) inhabituelle(s)", 2)

    if _sous(etat, "incident").get("actif"):
        proposer("incident_state",
                 "le mode incident est toujours actif : le réseau de cette "
                 "machine reste coupé", 2)

    telemetrie = _sous(etat, "telemetrie")
    disque = _nombre(telemetrie, "disque")
    if disque >= SEUIL_DISQUE_CRITIQUE:
        proposer("clean_full",
                 f"le volume système est occupé à {disque:.0f} %", 2)
    elif disque >= SEUIL_DISQUE_ALERTE:
        proposer("disk_analyze",
                 f"le volume système est occupé à {disque:.0f} %", 1)

    signatures = _sous(etat, "signatures")
    empreintes = _entier(signatures, "empreintes")
    if "empreintes" in signatures and empreintes <= 0:
        proposer("status", "les bases de signatures sont vides : la détection "
                           "par empreinte ne peut rien trouver", 2)
    else:
        age = _age_en_jours(etat, signatures.get("derniere_maj"))
        if age is not None and age >= AGE_SIGNATURES_JOURS:
            proposer("status",
                     f"les bases de signatures datent de {age:.0f} jours", 1)

    age_scan = _age_en_jours(etat, etat.get("dernier_scan"))
    if etat.get("dernier_scan") in (None, 0):
        proposer("scan_directory",
                 "aucune analyse complète n'a encore été faite sur cette "
                 "machine", 2)
    elif age_scan is not None and age_scan >= AGE_SCAN_JOURS:
        proposer("scan_directory",
                 f"la dernière analyse remonte à {age_scan:.0f} jours", 1)

    # ── Urgence 1 : confort de fonctionnement ──────────────────────────────
    bouclier = _sous(etat, "bouclier")
    if "actif" in bouclier and not bouclier.get("actif"):
        proposer("shield_start",
                 "le bouclier anti-rançongiciel (fichiers-appâts) n'est pas "
                 "déployé", 1)

    memoire_vive = _nombre(telemetrie, "memoire")
    if memoire_vive >= SEUIL_MEMOIRE:
        proposer("startup_list",
                 f"la mémoire vive est occupée à {memoire_vive:.0f} %", 1)

    cpu = _nombre(telemetrie, "cpu")
    if cpu >= SEUIL_CPU:
        proposer("shield_processes",
                 f"le processeur est à {cpu:.0f} % depuis un moment, ce qui "
                 f"peut trahir un chiffrement ou un minage en cours", 1)

    # ── Urgence 0 : rangement ──────────────────────────────────────────────
    quarantaine = _taille(_sous(etat, "quarantaine").get("total"))
    if quarantaine >= SEUIL_QUARANTAINE:
        proposer("quarantine_list",
                 f"{quarantaine} éléments dorment en quarantaine", 0)

    sas = _taille(_sous(etat, "sas").get("total"))
    if sas > 0:
        proposer("staging_list",
                 f"{sas} fichier(s) mis de côté attendent une décision", 0)

    # Sourdine puis tri total. Le tri porte sur des chaînes et des entiers :
    # aucun ordre de parcours de dict ou de set n'y survit.
    retenues = [s for s in brutes if not est_en_sourdine(memoire, s.capacite)]
    retenues.sort(key=lambda s: (-s.urgence, s.capacite, s.motif))
    return tuple(retenues)


def _en_secondes(valeur: Any) -> Optional[float]:
    """Convertit en temps Unix un horodatage numérique OU en ISO 8601.

    Les deux écritures circulent réellement dans le produit : la mémoire
    conserve des `time.time()`, tandis que `status` publie la date des bases
    de signatures au format ISO. Refuser la seconde reviendrait à ne jamais
    signaler des signatures périmées, ce qui est précisément le cas qu'on
    cherche à couvrir.
    """
    if isinstance(valeur, bool) or valeur is None:
        return None
    try:
        return float(valeur)
    except (TypeError, ValueError):
        pass
    if not isinstance(valeur, str):
        return None
    try:
        return datetime.datetime.fromisoformat(valeur.strip()).timestamp()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _age_en_jours(etat: Mapping, horodatage: Any) -> Optional[float]:
    """Âge d'un horodatage, mesuré depuis `etat["maintenant"]`.

    L'instant courant vient de l'état et non de `time.time()` : c'est ce qui
    rend la fonction pure, donc rejouable à l'identique dans un test comme
    dans un rapport a posteriori.
    """
    quand = _en_secondes(horodatage)
    if quand is None:
        return None
    if quand <= 0:
        return None
    maintenant = _en_secondes(etat.get("maintenant"))
    if maintenant is None:
        return None
    return max(0.0, (maintenant - quand) / JOUR)
