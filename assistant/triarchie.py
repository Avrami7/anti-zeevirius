"""
triarchie.py — délibérer avant de proposer, jamais décider à la place de l'humain.

ANTI-ZEEVIRIUS n'est pas gouverné par une voix unique qui annonce. Trois
instances se prononcent avant qu'une action conséquente soit présentée à
l'utilisateur, et elles ne raisonnent pas de la même façon — c'est tout
l'intérêt :

  * **PROMÉTHÉE**, l'avant, juge le **risque encouru si l'on n'agit pas**.
    Biais assumé vers l'action : c'est la voix de la prévention.
  * **JUANITA JCV**, le pendant, juge la **faisabilité et le coût** — droits
    disponibles, charge machine, opération déjà en cours, réversibilité
    effective. Biais assumé vers la prudence opérationnelle.
  * **ÉPIMÉTHÉE**, l'après, juge à partir du **passé** — cette action a-t-elle
    déjà échoué, l'utilisateur l'a-t-il déjà annulée, ce fichier a-t-il déjà
    été restauré depuis la quarantaine ? Biais assumé vers la contestation.

Ce dernier point est celui qui apporte le plus : **une action que
l'utilisateur a annulée trois fois ne doit plus être proposée de la même
façon.** Un antivirus qui repropose indéfiniment ce qu'on lui a refusé
n'apprend rien de son utilisateur, et finit par être cliqué sans être lu.

--------------------------------------------------------------------------
Les deux règles qui rendent cette architecture saine plutôt que dangereuse
--------------------------------------------------------------------------

**1. L'humain reste l'arbitre.** `exige_humain` vaut VRAI dès que le risque de
la capacité dépasse `Risque.LECTURE`, *quel que soit le verdict*. Le champ
n'est jamais calculé à partir des avis : ni l'unanimité, ni une confiance de
1,0 ne l'abaissent. Une délibération ne produit ni ne transporte de
`confirm_token` — le cycle `dry_run` → plan affiché → `confirm_token` de
`gui/bridge.py::_guarded()` reste la seule porte d'exécution, et ce module ne
la duplique pas. Un consensus de trois agents qui autoriserait une suppression
serait une régression de sécurité déguisée en fonctionnalité ; c'est
précisément le piège de cette architecture.

**2. Une triarchie incomplète ne décide pas.** Sous trois instances actives, le
verdict est `"incomplete"` et l'affaire remonte directement à l'humain. On ne
passe PAS à une majorité de deux : avec deux membres, une majorité devient un
seul avis, la contradiction disparaît, et le quorum n'a plus de sens. Un
quorum qui s'adapte à l'absence n'est pas un quorum.

Corollaire de forme, et il est délibéré : une délibération incomplète rend
`avis = ()`. Publier les deux avis recueillis inviterait l'appelant suivant à
compter les voix lui-même — `sum(1 for a in avis if a.position is POUR) >= 2`
est une ligne trop facile à écrire. Ce qui manque au quorum est dit dans
`resume`, en clair, pour l'utilisateur ; ce qui est absent du champ `avis` ne
peut pas être recompté.

--------------------------------------------------------------------------
`deliberer()` est une fonction PURE
--------------------------------------------------------------------------

Pas de disque, pas de réseau, pas d'exécution : tout ce que la délibération
sait lui arrive dans `etat`. C'est la seule façon de pouvoir affirmer qu'une
délibération ne peut pas, par construction, déclencher l'action dont elle
débat — la frontière est structurelle, pas une question de discipline.

C'est aussi ce qui oblige à séparer la lecture des sources :
`construire_etat_depuis_disque()` est la fonction impure qui interroge
`comfort/history.py` et `assistant/memory.py`. Elle touche au disque ;
`deliberer()` jamais.

**Déterminisme.** Même capacité, même état, mêmes instances → même
délibération, dans le même ordre. `avis` est trié par nom d'instance, aucun
`set` n'est parcouru tel quel, et tous les tris sont totaux. Ce dépôt a déjà
livré un ordre dépendant de `PYTHONHASHSEED` dans `security/network_watch.py`.

--------------------------------------------------------------------------
Forme de `etat` — ce que la délibération attend qu'on lui apporte
--------------------------------------------------------------------------

Toutes les clés sont facultatives ; une clé absente vaut « je ne sais pas »,
et l'inconnu ne vote pas. Les seuils reprennent ceux de
`assistant/proactive.py` pour qu'un même état puisse nourrir les deux.

    {
      "maintenant": float,                      # time.time()
      "cible": str,                             # chemin visé, ou ""
      "telemetrie": {"cpu": %, "memoire": %, "disque": %},
      "menaces": {"en_attente": int},
      "camera": {"alertes": list | int},
      "reseau": {"connexions_suspectes": int},
      "temps_reel": {"actif": bool},
      "bouclier": {"actif": bool},
      "incident": {"actif": bool},
      "signatures": {"empreintes": int, "derniere_maj": float | str ISO},
      "dernier_scan": float | str ISO | None,
      "droits": {"administrateur": bool},
      "operation_en_cours": str | bool,
      "sauvegarde": {"recente": bool},
      "historique": {
          "lisible": bool,                      # le passé est relisible — matière d'ÉPIMÉTHÉE
          "disponible": bool,                   # l'Historique peut annuler — filet de JUANITA
          "annulations": {capacite: int},
          "echecs": {capacite: int},
          "succes": {capacite: int},
          "restaurations": [chemins déjà restaurés depuis la quarantaine],
      },
    }

Deux clés d'historique et non une, parce que ce sont deux questions
différentes : « puis-je relire ce qui s'est passé ? » (ÉPIMÉTHÉE) et « existe-t-il
un chemin de retour ? » (JUANITA). Un journal effacé ne supprime pas le
mécanisme d'annulation, et un mécanisme en panne n'efface pas le journal.

**Convention de lecture des drapeaux.** Un booléen manquant n'est pas un
booléen faux : `temps_reel.actif` absent signifie « on n'a pas regardé », pas
« la surveillance est arrêtée ». On exige donc `is False` explicite avant de
constater un manque — même prudence que `proactive.suggerer()`. Une seule
exception, et elle va dans le sens sûr : pour une capacité `IRREVERSIBLE`,
JUANITA exige `sauvegarde.recente is True`. Là, la charge de la preuve
s'inverse, parce qu'une suppression définitive sans filet déclaré est
exactement ce qu'on ne veut pas laisser passer par défaut.
"""

from __future__ import annotations

import dataclasses
import datetime
import enum
from pathlib import PureWindowsPath
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Mapping, Optional, Tuple

from assistant.risk import Risque, libelle

if TYPE_CHECKING:            # pragma: no cover - uniquement pour les types
    from assistant.memory import Memoire
    from assistant.registry import Capacite

__all__ = [
    "Position", "Avis", "Deliberation", "deliberer",
    "construire_etat_depuis_disque",
    "INSTANCES", "LIBELLES_INSTANCES", "VERDICTS",
    "CAPACITES_ADMIN", "SEUIL_CONTESTATION",
    "SEUIL_CPU", "SEUIL_MEMOIRE", "SEUIL_DISQUE_ALERTE",
    "SEUIL_DISQUE_CRITIQUE", "AGE_SIGNATURES_JOURS", "AGE_SCAN_JOURS",
]


# ── Constantes de la maison ────────────────────────────────────────────────

class Position(enum.Enum):
    POUR = "pour"              # j'approuve cette action
    CONTRE = "contre"          # je m'y oppose, et je dis pourquoi
    ABSTENTION = "abstention"  # hors de ma compétence


# Ordre alphabétique : c'est le tri imposé au champ `avis` par le contrat, et
# le garder ici évite qu'un second ordre coexiste dans le module.
INSTANCES: Tuple[str, ...] = ("epimethee", "juanita", "promethee")

LIBELLES_INSTANCES: Dict[str, str] = {
    "promethee": "PROMÉTHÉE",
    "juanita": "JUANITA JCV",
    "epimethee": "ÉPIMÉTHÉE",
}

# Ordre de lecture pour l'utilisateur : l'avant, le pendant, l'après. Il ne
# sert QU'au `resume`, jamais au champ `avis`.
ORDRE_CHRONOLOGIQUE: Tuple[str, ...] = ("promethee", "juanita", "epimethee")

VERDICTS: Tuple[str, ...] = ("unanime", "majorite", "partage", "incomplete")

# Trois refus, c'est une opinion arrêtée, plus un « pas maintenant » répété.
# `proactive.SEUIL_SOURDINE` vaut 2 pour une simple suggestion ; ici on parle
# de contester une action déjà proposée, on laisse une marge de plus.
SEUIL_CONTESTATION = 3

# Mêmes seuils que `assistant/proactive.py`, volontairement : deux modules qui
# regardent le même état ne doivent pas décrire deux machines différentes.
SEUIL_DISQUE_CRITIQUE = 90.0
SEUIL_DISQUE_ALERTE = 80.0
SEUIL_MEMOIRE = 90.0
SEUIL_CPU = 90.0
AGE_SIGNATURES_JOURS = 7
AGE_SCAN_JOURS = 7
JOUR = 86400.0

# Capacités qui ne passent pas sans élévation sous Windows : service,
# planificateur de tâches, pare-feu, base de registre en écriture,
# désinstallation d'un logiciel installé pour toute la machine. Les noms sont
# ceux de `construire_registre_par_defaut()` — un test vérifie qu'ils y
# existent tous, parce qu'une capacité imaginaire ici rendrait JUANITA
# silencieuse sur un vrai blocage.
CAPACITES_ADMIN: Tuple[str, ...] = (
    "applications.desinstaller",
    "demarrage.desactiver",
    "demarrage.restaurer",
    "incident.activer",
    "incident.retablir",
    "intrusion.audit",
    "planification.nettoyage",
    "planification.retirer",
)


# ── L'avis ─────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class Avis:
    """La prise de position d'une instance, motivée.

    `motif` non vide est une contrainte de CODE, pas une convention, et le
    refus est franc jusque pour une abstention : un avis sans motif est un
    vote à l'aveugle, et il rend la délibération inauditable. Le jour où
    l'utilisateur demande « pourquoi m'a-t-on proposé ça ? », un champ vide
    n'a aucune réponse à offrir.
    """

    instance: str        # "promethee" | "juanita" | "epimethee"
    position: Position
    motif: str           # UNE PHRASE, obligatoire et non vide
    confiance: float     # 0.0 à 1.0

    def __post_init__(self) -> None:
        if self.instance not in INSTANCES:
            raise ValueError(
                f"instance inconnue « {self.instance} » ; "
                f"attendu l'une de {', '.join(INSTANCES)}"
            )
        if not isinstance(self.position, Position):
            raise ValueError(f"position attendue, reçu {type(self.position).__name__}")
        if not isinstance(self.motif, str) or not self.motif.strip():
            raise ValueError(
                f"motif obligatoire et non vide pour « {self.instance} » : "
                "un avis sans motif rend la délibération inauditable"
            )
        if isinstance(self.confiance, bool) or not isinstance(self.confiance, (int, float)):
            raise ValueError(
                f"confiance numérique attendue pour « {self.instance} », "
                f"reçu {type(self.confiance).__name__}"
            )
        if not 0.0 <= float(self.confiance) <= 1.0:
            raise ValueError(
                f"confiance hors échelle pour « {self.instance} » : {self.confiance!r} "
                "(attendu entre 0.0 et 1.0)"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instance": self.instance,
            "libelle": LIBELLES_INSTANCES[self.instance],
            "position": self.position.value,
            "motif": self.motif,
            "confiance": float(self.confiance),
        }


# ── La délibération ────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class Deliberation:
    """Le résultat d'une délibération — une proposition étayée, pas une décision.

    `exige_humain` est posé par `deliberer()` d'après le seul risque de la
    capacité. Il n'est jamais déduit des avis, et rien dans cet objet ne peut
    tenir lieu d'autorisation : le contrôle de `__post_init__` refuse jusqu'à
    la mention textuelle d'un `confirm_token`, pour qu'un résumé recopié dans
    une requête web ne puisse pas en transporter un par accident.
    """

    capacite: str
    avis: Tuple[Avis, ...]     # TRIÉS par instance — déterminisme
    verdict: str               # unanime | majorite | partage | incomplete
    exige_humain: bool         # VRAI dès que le risque dépasse LECTURE
    resume: str                # les trois motifs, lisibles par l'utilisateur

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(
                f"verdict inconnu « {self.verdict} » ; "
                f"attendu l'un de {', '.join(VERDICTS)}"
            )
        instances = [a.instance for a in self.avis]
        if instances != sorted(instances):
            raise ValueError("les avis doivent être triés par instance")
        if len(set(instances)) != len(instances):
            raise ValueError("deux avis pour la même instance")
        if self.verdict == "incomplete" and self.avis:
            raise ValueError(
                "une délibération incomplète ne publie aucun avis : "
                "des voix publiées seraient des voix recomptables"
            )
        # Aucun verdict ne peut produire ni contenir un jeton de confirmation.
        textes = [self.capacite, self.verdict, self.resume]
        textes.extend(a.motif for a in self.avis)
        for texte in textes:
            if isinstance(texte, str) and "confirm_token" in texte.casefold():
                raise ValueError(
                    "une délibération ne transporte jamais de confirm_token : "
                    "la confirmation appartient à gui/bridge.py::_guarded()"
                )

    # Petits accès de confort pour l'interface. Ils comptent des avis ; ils ne
    # décident rien, et surtout pas `exige_humain`.
    def compte(self, position: Position) -> int:
        return sum(1 for a in self.avis if a.position is position)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capacite": self.capacite,
            "avis": [a.to_dict() for a in self.avis],
            "verdict": self.verdict,
            "exige_humain": self.exige_humain,
            "resume": self.resume,
        }


# ── Lecture de l'état ──────────────────────────────────────────────────────

def _sous(etat: Any, cle: str) -> Mapping:
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


def _instant(valeur: Any) -> Optional[float]:
    """Horodatage en secondes, qu'il arrive en flottant ou en ISO 8601.

    `comfort/history.py` rend des dates ISO, `assistant/memory.py` des
    `time.time()`. Les deux nourrissent le même état, donc les deux doivent
    être lisibles ici.
    """
    if isinstance(valeur, bool) or valeur is None:
        return None
    if isinstance(valeur, (int, float)):
        return float(valeur)
    if isinstance(valeur, str) and valeur.strip():
        try:
            return datetime.datetime.fromisoformat(valeur.strip()).timestamp()
        except ValueError:
            return None
    return None


def _age_en_jours(etat: Any, valeur: Any) -> Optional[float]:
    instant = _instant(valeur)
    maintenant = _instant(etat.get("maintenant")) if isinstance(etat, Mapping) else None
    if instant is None or maintenant is None:
        return None
    return max(0.0, (maintenant - instant) / JOUR)


def _compteur(historique: Mapping, cle: str, capacite: str) -> int:
    """Compteur d'une capacité dans un sous-dictionnaire de l'historique."""
    table = historique.get(cle)
    if not isinstance(table, Mapping):
        return 0
    try:
        return max(0, int(table.get(capacite, 0)))
    except (TypeError, ValueError):
        return 0


def _cle_chemin(chemin: Any) -> str:
    """Forme comparable d'un chemin Windows, manipulé depuis Linux.

    Windows ne distingue pas la casse et accepte les deux séparateurs : deux
    écritures du même fichier doivent se reconnaître, sinon ÉPIMÉTHÉE rate
    justement la restauration qu'il est là pour voir. `PureWindowsPath` et non
    `Path` — piège déjà rencontré dans ce dépôt, `Path("C:\\x\\y")` ne se
    découpe pas sous Linux.
    """
    if not isinstance(chemin, str) or not chemin.strip():
        return ""
    return str(PureWindowsPath(chemin.strip())).casefold()


def _nom_fichier(chemin: Any) -> str:
    if not isinstance(chemin, str) or not chemin.strip():
        return ""
    return PureWindowsPath(chemin.strip()).name or chemin.strip()


def _risque(capacite: Any) -> Risque:
    """Niveau de la capacité, `IRREVERSIBLE` si on n'arrive pas à le lire.

    Face à l'inconnu on prend le niveau le plus exigeant : supposer inoffensif
    ce qu'on ne sait pas lire est exactement l'erreur qui supprime un fichier.
    C'est aussi la position de `risk.exige_confirmation()`.
    """
    brut = getattr(capacite, "risque", None)
    if isinstance(brut, Risque):
        return brut
    if isinstance(brut, bool):
        return Risque.IRREVERSIBLE
    try:
        entier = int(brut)
    except (TypeError, ValueError, OverflowError):
        return Risque.IRREVERSIBLE
    if entier != brut:
        return Risque.IRREVERSIBLE
    try:
        return Risque(entier)
    except ValueError:
        return Risque.IRREVERSIBLE


# ── PROMÉTHÉE — le risque encouru si l'on n'agit pas ───────────────────────

def _perils(etat: Any) -> List[Tuple[int, str]]:
    """Les périls constatés dans l'état, du plus grave au moins grave.

    Liste construite dans un ordre littéral fixe : à gravité égale, c'est
    toujours le même péril qui est cité, quel que soit `PYTHONHASHSEED`.
    """
    telemetrie = _sous(etat, "telemetrie")
    trouves: List[Tuple[int, str]] = []

    menaces = _entier(_sous(etat, "menaces"), "en_attente")
    if menaces > 0:
        trouves.append((3, f"{menaces} menace(s) détectée(s) attendent une décision"))

    alertes = _taille(_sous(etat, "camera").get("alertes"))
    if alertes > 0:
        trouves.append((3, f"{alertes} application(s) accèdent à la caméra ou au "
                            f"microphone sans autorisation déclarée"))

    suspectes = _entier(_sous(etat, "reseau"), "connexions_suspectes")
    if suspectes > 0:
        trouves.append((2, f"{suspectes} connexion(s) sortante(s) inhabituelle(s)"))

    temps_reel = _sous(etat, "temps_reel")
    if temps_reel.get("actif") is False:
        trouves.append((2, "la surveillance en temps réel des dossiers sensibles "
                           "est arrêtée"))

    signatures = _sous(etat, "signatures")
    empreintes = _entier(signatures, "empreintes", -1)
    if "empreintes" in signatures and empreintes <= 0:
        trouves.append((2, "les bases de signatures sont vides : la détection par "
                           "empreinte ne peut rien trouver"))

    if isinstance(etat, Mapping) and "dernier_scan" in etat and \
            etat.get("dernier_scan") in (None, 0):
        trouves.append((2, "aucune analyse complète n'a encore été faite sur cette "
                           "machine"))

    disque = _nombre(telemetrie, "disque", -1.0)
    if disque >= SEUIL_DISQUE_CRITIQUE:
        trouves.append((2, f"le volume système est occupé à {disque:.0f} %"))

    if _sous(etat, "incident").get("actif") is True:
        trouves.append((2, "le mode incident est encore actif : le réseau de cette "
                           "machine reste coupé"))

    if SEUIL_DISQUE_ALERTE <= disque < SEUIL_DISQUE_CRITIQUE:
        trouves.append((1, f"le volume système est occupé à {disque:.0f} %"))

    if _sous(etat, "bouclier").get("actif") is False:
        trouves.append((1, "le bouclier anti-rançongiciel (fichiers-appâts) n'est "
                           "pas déployé"))

    memoire_vive = _nombre(telemetrie, "memoire", -1.0)
    if memoire_vive >= SEUIL_MEMOIRE:
        trouves.append((1, f"la mémoire vive est occupée à {memoire_vive:.0f} %"))

    cpu = _nombre(telemetrie, "cpu", -1.0)
    if cpu >= SEUIL_CPU:
        trouves.append((1, f"le processeur est à {cpu:.0f} %"))

    age_signatures = _age_en_jours(etat, signatures.get("derniere_maj"))
    if age_signatures is not None and age_signatures >= AGE_SIGNATURES_JOURS:
        trouves.append((1, f"les bases de signatures datent de "
                           f"{age_signatures:.0f} jours"))

    age_scan = _age_en_jours(etat, etat.get("dernier_scan") if isinstance(etat, Mapping) else None)
    if age_scan is not None and age_scan >= AGE_SCAN_JOURS:
        trouves.append((1, f"la dernière analyse remonte à {age_scan:.0f} jours"))

    # Tri stable sur la seule gravité : `sorted` conserve l'ordre littéral
    # ci-dessus à gravité égale, ce qui suffit au déterminisme sans inventer
    # un ordre alphabétique qui n'aurait aucun sens pour un péril.
    return sorted(trouves, key=lambda p: -p[0])


def _avis_promethee(capacite: Any, etat: Any, risque: Risque) -> Avis:
    """L'avant : ce qu'il coûte de ne rien faire.

    PROMÉTHÉE ne juge pas le prix de l'action — c'est le travail de JUANITA —
    mais celui de l'inaction. D'où son biais assumé : dès qu'un péril est
    constaté, il est POUR, et sa confiance suit la gravité.

    Le seul cas où il s'oppose est symétrique de son critère : aucun péril
    constaté et une action au moins destructive. Il n'y a alors rien à
    prévenir, et la prévention n'a aucun argument à opposer à une perte de
    données. En dessous de `DESTRUCTIF`, l'absence de péril le met simplement
    hors sujet : il s'abstient plutôt que de peser sur une décision qui ne
    relève pas de lui.
    """
    perils = _perils(etat)
    gravite = perils[0][0] if perils else 0
    motif = perils[0][1] if perils else ""

    if gravite >= 2:
        return Avis("promethee", Position.POUR,
                    f"{motif} : attendre aggrave la situation",
                    min(1.0, 0.55 + 0.15 * gravite))
    if gravite == 1:
        return Avis("promethee", Position.POUR,
                    f"{motif} : rien d'urgent, mais l'inaction ne le corrigera pas",
                    0.55)
    if risque >= Risque.DESTRUCTIF:
        return Avis("promethee", Position.CONTRE,
                    "aucun péril constaté : rien ne justifie une action "
                    f"{libelle(risque)} maintenant",
                    0.70)
    return Avis("promethee", Position.ABSTENTION,
                "aucun péril constaté : il n'y a rien à prévenir ici",
                0.40)


# ── JUANITA JCV — faisabilité, coût, réversibilité effective ───────────────

def _avis_juanita(capacite: Any, etat: Any, risque: Risque) -> Avis:
    """Le pendant : est-ce faisable maintenant, proprement, et peut-on revenir ?

    Elle ne conteste ni l'utilité ni le passé : elle regarde l'opération. Les
    refus sont examinés du plus rédhibitoire au plus discutable, pour que le
    motif cité soit celui qui bloque vraiment.

    Son abstention sur une capacité de niveau `LECTURE` n'est pas une
    dérobade : une lecture ne s'annule pas, ne coûte rien et ne peut pas
    déraper. La faisabilité n'est pas en cause, donc ce n'est pas sa question.
    C'est aussi ce qui la fait diverger de PROMÉTHÉE sur tout le domaine de la
    lecture seule.
    """
    nom = getattr(capacite, "nom", "") or ""
    telemetrie = _sous(etat, "telemetrie")
    historique = _sous(etat, "historique")

    if nom in CAPACITES_ADMIN and _sous(etat, "droits").get("administrateur") is False:
        return Avis("juanita", Position.CONTRE,
                    "cette action exige des droits d'administrateur dont la "
                    "session ne dispose pas : elle échouerait à mi-parcours",
                    0.90)

    en_cours = etat.get("operation_en_cours") if isinstance(etat, Mapping) else None
    if en_cours and risque > Risque.LECTURE:
        detail = f" (« {en_cours} »)" if isinstance(en_cours, str) and en_cours.strip() else ""
        return Avis("juanita", Position.CONTRE,
                    f"une opération est déjà en cours{detail} : deux écritures "
                    "concurrentes sur le même disque se corrompent l'une l'autre",
                    0.80)

    cpu = _nombre(telemetrie, "cpu", -1.0)
    memoire_vive = _nombre(telemetrie, "memoire", -1.0)
    if risque > Risque.LECTURE and (cpu >= SEUIL_CPU or memoire_vive >= SEUIL_MEMOIRE):
        return Avis("juanita", Position.CONTRE,
                    f"la machine est saturée (processeur {max(cpu, 0.0):.0f} %, "
                    f"mémoire {max(memoire_vive, 0.0):.0f} %) : l'opération serait "
                    "longue et interruptible au mauvais moment",
                    0.65)

    if risque >= Risque.REVERSIBLE and historique.get("disponible") is False:
        return Avis("juanita", Position.CONTRE,
                    "l'Historique est indisponible : l'annulation promise à "
                    "l'utilisateur n'existerait pas",
                    0.75)

    if risque >= Risque.IRREVERSIBLE:
        if _sous(etat, "sauvegarde").get("recente") is True:
            return Avis("juanita", Position.POUR,
                        "action sans retour possible, mais une sauvegarde récente "
                        "est déclarée : le risque opérationnel est couvert",
                        0.60)
        return Avis("juanita", Position.CONTRE,
                    "action sans retour possible et aucune sauvegarde récente "
                    "déclarée : la réversibilité effective est nulle",
                    0.70)

    if risque <= Risque.LECTURE:
        return Avis("juanita", Position.ABSTENTION,
                    "une lecture n'écrit rien et ne s'annule pas : la faisabilité "
                    "n'est pas en cause ici",
                    0.40)

    return Avis("juanita", Position.POUR,
                "droits suffisants, aucune opération concurrente, machine "
                "disponible et retour arrière assuré par l'Historique",
                0.65)


# ── ÉPIMÉTHÉE — le passé ───────────────────────────────────────────────────

def _avis_epimethee(capacite: Any, etat: Any, risque: Risque) -> Avis:
    """L'après : ce que les tentatives précédentes ont appris.

    C'est l'instance qui apporte le plus, et la seule capable de dire « on a
    déjà essayé ». Elle est examinée du plus grave au plus léger :

      1. l'utilisateur a annulé trois fois — proposer à l'identique une
         quatrième fois n'est plus une suggestion, c'est du harcèlement, et
         c'est le cas que cette architecture existe pour attraper ;
      2. la cible a déjà été restaurée depuis la quarantaine — la traiter de
         nouveau comme une menace refait exactement l'erreur d'hier ;
      3. une ou deux annulations, puis les échecs techniques.

    Elle s'abstient dans deux cas, et ce sont de vraies abstentions : quand le
    passé n'est pas lisible, et quand l'action n'a aucun précédent. On ne
    juge pas ce qui n'a jamais eu lieu.
    """
    nom = getattr(capacite, "nom", "") or ""
    historique = _sous(etat, "historique")

    if historique.get("lisible") is False:
        return Avis("epimethee", Position.ABSTENTION,
                    "le passé de cette machine n'est pas lisible : je n'ai aucune "
                    "matière pour contester",
                    0.20)

    annulations = _compteur(historique, "annulations", nom)
    echecs = _compteur(historique, "echecs", nom)
    succes = _compteur(historique, "succes", nom)

    if annulations >= SEUIL_CONTESTATION:
        return Avis("epimethee", Position.CONTRE,
                    f"l'utilisateur a déjà annulé cette action {annulations} fois : "
                    "la reproposer à l'identique répète l'erreur au lieu d'en tenir "
                    "compte",
                    0.95)

    cible = etat.get("cible") if isinstance(etat, Mapping) else None
    cle_cible = _cle_chemin(cible)
    restaurees = historique.get("restaurations")
    if cle_cible and isinstance(restaurees, (list, tuple)):
        connues = {_cle_chemin(c) for c in restaurees}
        if cle_cible in connues:
            return Avis("epimethee", Position.CONTRE,
                        f"« {_nom_fichier(cible)} » a déjà été restauré depuis la "
                        "quarantaine : le classer encore comme menace refait la "
                        "même erreur",
                        0.85)

    if annulations >= 1:
        return Avis("epimethee", Position.CONTRE,
                    f"l'utilisateur a déjà annulé cette action {annulations} fois : "
                    "son avis passé compte avant de la reproposer",
                    min(0.90, 0.45 + 0.15 * annulations))

    if echecs >= 2:
        return Avis("epimethee", Position.CONTRE,
                    f"cette action a déjà échoué {echecs} fois sur cette machine : "
                    "rien n'indique que la prochaine tentative se passerait mieux",
                    0.70)

    if echecs == 1:
        return Avis("epimethee", Position.ABSTENTION,
                    "un seul échec passé et aucune réussite : trop peu pour "
                    "trancher dans un sens ou dans l'autre",
                    0.35)

    if succes >= 1:
        return Avis("epimethee", Position.POUR,
                    f"{succes} exécution(s) passée(s) sans annulation ni échec : "
                    "le précédent est bon",
                    min(0.85, 0.45 + 0.10 * succes))

    return Avis("epimethee", Position.ABSTENTION,
                "aucun précédent pour cette action : je ne peux pas juger de ce "
                "qui n'a jamais eu lieu",
                0.25)


_JURY = {
    "promethee": _avis_promethee,
    "juanita": _avis_juanita,
    "epimethee": _avis_epimethee,
}


# ── Verdict et résumé ──────────────────────────────────────────────────────

def _verdict(avis: Tuple[Avis, ...]) -> str:
    """Unanimité, majorité de deux, ou partage — sur trois voix exactement.

    `Position` est parcouru dans son ordre de définition, pas dans celui d'un
    `set` : le décompte ne dépend pas de `PYTHONHASHSEED`.
    """
    comptes = [sum(1 for a in avis if a.position is p) for p in Position]
    maxi = max(comptes)
    if maxi == len(avis):
        return "unanime"
    if maxi >= 2:
        return "majorite"
    return "partage"


def _confiance_fr(valeur: float) -> str:
    return f"{float(valeur):.2f}".replace(".", ",")


def _resume(nom: str, risque: Risque, verdict: str, avis: Tuple[Avis, ...],
            exige_humain: bool, actives: Tuple[str, ...]) -> str:
    """Les motifs, en clair, dans l'ordre de lecture avant / pendant / après.

    Le champ `avis` est trié alphabétiquement parce que le contrat l'exige ;
    le résumé, lui, est écrit pour un humain, et l'ordre qui a du sens pour
    lui est chronologique. Deux ordres, deux usages, et c'est écrit ici pour
    qu'aucune relecture ne prenne l'un pour un bogue de l'autre.
    """
    lignes = [f"Délibération sur « {nom} » ({libelle(risque)}) — "
              f"verdict : {verdict}."]

    if verdict == "incomplete":
        manquantes = tuple(i for i in INSTANCES if i not in actives)
        presentes = ", ".join(LIBELLES_INSTANCES[i] for i in ORDRE_CHRONOLOGIQUE
                              if i in actives) or "aucune"
        absentes = ", ".join(LIBELLES_INSTANCES[i] for i in ORDRE_CHRONOLOGIQUE
                             if i in manquantes)
        lignes.append(f"Instances actives : {presentes}. Manquantes : {absentes}.")
        lignes.append("Une triarchie incomplète ne décide pas : on ne passe pas à "
                      "une majorité de deux, l'affaire remonte directement à vous.")
        return "\n".join(lignes)

    par_instance = {a.instance: a for a in avis}
    for instance in ORDRE_CHRONOLOGIQUE:
        a = par_instance.get(instance)
        if a is None:
            continue
        lignes.append(f"{LIBELLES_INSTANCES[instance]} — {a.position.value} "
                      f"(confiance {_confiance_fr(a.confiance)}) : {a.motif}.")

    if exige_humain:
        lignes.append("Décision réservée à l'utilisateur : la triarchie propose, "
                      "elle ne confirme rien.")
    else:
        lignes.append("Action en lecture seule : aucune confirmation requise.")
    return "\n".join(lignes)


# ── La délibération ────────────────────────────────────────────────────────

def _actives(instances_actives: Any) -> Tuple[str, ...]:
    """Les instances retenues, triées, dédoublonnées, sans nom inconnu.

    Un nom inconnu — faute de frappe, instance renommée ailleurs — ne compte
    pas. La triarchie devient alors incomplète, ce qui est exactement le bon
    échec : une coquille ne doit pas fabriquer un quorum de trois voix dont
    une n'existe pas.
    """
    if isinstance(instances_actives, str) or not isinstance(instances_actives, Iterable):
        return ()
    retenues = {n.strip().lower() for n in instances_actives
                if isinstance(n, str) and n.strip().lower() in INSTANCES}
    return tuple(sorted(retenues))


def deliberer(capacite: "Capacite", etat: dict,
              instances_actives: "frozenset[str]") -> Deliberation:
    """Fait délibérer la triarchie sur une capacité. Fonction PURE.

    Ne lit pas le disque, n'exécute rien, ne fait aucun appel réseau : tout ce
    qu'elle sait est dans `etat`, que `construire_etat_depuis_disque()` sait
    fabriquer. Même capacité, même état, mêmes instances → même délibération,
    dans le même ordre.

    Deux invariants ne dépendent ni des avis, ni du verdict, ni de la
    confiance, et ce sont eux qui séparent cette fonctionnalité d'une
    régression de sécurité :

      * `exige_humain` vaut VRAI dès que `capacite.risque` dépasse
        `Risque.LECTURE`. Il est posé ici, à partir du risque et de rien
        d'autre — pas d'unanimité qui l'abaisse, pas de confiance de 1,0 qui
        le désarme.
      * `verdict == "incomplete"` dès que moins de trois instances sont
        actives, et la délibération ne publie alors aucun avis.

    Le résultat est une proposition étayée à présenter à l'utilisateur. Il ne
    contient pas de `confirm_token` et ne peut pas en tenir lieu.
    """
    nom = getattr(capacite, "nom", None)
    if not isinstance(nom, str) or not nom.strip():
        raise ValueError(
            f"capacité attendue (avec un nom), reçu {type(capacite).__name__}"
        )
    nom = nom.strip()
    if not isinstance(etat, Mapping):
        etat = {}
    risque = _risque(capacite)

    # Posé AVANT toute consultation, et jamais recalculé : c'est la règle 1.
    exige_humain = risque > Risque.LECTURE

    actives = _actives(instances_actives)
    if len(actives) < len(INSTANCES):
        return Deliberation(
            capacite=nom, avis=(), verdict="incomplete",
            exige_humain=exige_humain,
            resume=_resume(nom, risque, "incomplete", (), exige_humain, actives),
        )

    # `actives` est trié, donc `avis` l'est aussi — la garantie d'ordre du
    # contrat ne repose pas sur un tri supplémentaire qu'on pourrait oublier.
    avis = tuple(_JURY[instance](capacite, etat, risque) for instance in actives)
    verdict = _verdict(avis)
    return Deliberation(
        capacite=nom, avis=avis, verdict=verdict, exige_humain=exige_humain,
        resume=_resume(nom, risque, verdict, avis, exige_humain, actives),
    )


# ══════════════════════════════════════════════════════════════════════════
# Construction de l'état — LA SEULE PARTIE QUI TOUCHE AU DISQUE
# ══════════════════════════════════════════════════════════════════════════

# Événements du journal de `assistant/memory.py` interprétés comme un refus de
# l'utilisateur. `suggestion.refusee` est écrit par `proactive.noter_refus()`
# et existe déjà ; les deux autres sont la convention offerte aux appelants qui
# voudront consigner une annulation explicite. Le détail doit porter la clé
# `capacite`, sinon l'entrée est ignorée — mieux vaut ne rien compter que
# compter au hasard.
EVENEMENTS_ANNULATION: Tuple[str, ...] = (
    "capacite.annulee", "historique.annulee", "suggestion.refusee",
)
EVENEMENTS_ECHEC: Tuple[str, ...] = ("capacite.echec",)
EVENEMENTS_SUCCES: Tuple[str, ...] = ("capacite.succes",)
# `assistant/autonomy.py` journalise déjà ses résultats sous ce nom, avec
# `etape` valant « echec » ou « terminee » et un drapeau `ok`.
EVENEMENT_AUTONOMIE = "autonomie.resultat"


def _incrementer(table: Dict[str, int], capacite: Any) -> None:
    if isinstance(capacite, str) and capacite.strip():
        cle = capacite.strip()
        table[cle] = table.get(cle, 0) + 1


def construire_etat_depuis_disque(
    *,
    memoire: "Optional[Memoire]" = None,
    historique: Any = None,
    base: Optional[Mapping] = None,
    cible: str = "",
    maintenant: Optional[float] = None,
    limite_journal: int = 500,
) -> Dict[str, Any]:
    """Fabrique le `etat` que `deliberer()` attend, en lisant les sources.

    C'est ICI que le disque est touché, et nulle part ailleurs. La séparation
    n'est pas cosmétique : elle est ce qui permet d'affirmer qu'une
    délibération ne peut pas, par construction, aller chercher un fait qu'on
    ne lui a pas montré — et donc de la rejouer à l'identique dans un test.

    `base` est l'état déjà rassemblé par l'appelant (télémétrie,
    surveillances, inventaires — typiquement le même dictionnaire que celui
    passé à `proactive.suggerer()`). Il est recopié tel quel ; seules les clés
    `historique`, `cible` et `maintenant` sont posées ou complétées ici.

    Ne lève jamais. Une source en panne rend `historique["lisible"] = False`,
    ce qui fait abstenir ÉPIMÉTHÉE au lieu de le faire inventer un passé.
    """
    import time

    etat: Dict[str, Any] = dict(base) if isinstance(base, Mapping) else {}
    etat["maintenant"] = float(maintenant) if maintenant is not None else time.time()
    if cible or "cible" not in etat:
        etat["cible"] = cible if isinstance(cible, str) else ""

    annulations: Dict[str, int] = {}
    echecs: Dict[str, int] = {}
    succes: Dict[str, int] = {}
    restaurations: List[str] = []
    lisible = True
    disponible = True

    # ── comfort/history.py : les restaurations et les annulations effectives ──
    if historique is None:
        try:
            from comfort.history import historique_par_defaut
            historique = historique_par_defaut()
        except Exception:
            # L'Historique absent n'est pas une panne de la délibération : on
            # le dit, et JUANITA en tirera la conséquence sur la réversibilité.
            historique = None
            disponible = False
            lisible = False

    if historique is not None:
        try:
            reponse = historique.lister(limite=None)
        except Exception:
            reponse = None
        if not isinstance(reponse, Mapping) or not reponse.get("ok"):
            lisible = False
            disponible = False
        else:
            donnees = reponse.get("data")
            donnees = donnees if isinstance(donnees, Mapping) else {}
            if donnees.get("problemes"):
                # Une source sur quatre en panne ne rend pas le passé illisible,
                # mais elle le rend incomplet : on continue avec ce qu'on a.
                pass
            entrees = donnees.get("entrees")
            for entree in entrees if isinstance(entrees, (list, tuple)) else ():
                if not isinstance(entree, Mapping):
                    continue
                details = entree.get("details")
                details = details if isinstance(details, Mapping) else {}
                if details.get("restaure") is True:
                    origine = details.get("chemin_origine") or details.get("restaure_vers")
                    if isinstance(origine, str) and origine.strip():
                        restaurations.append(origine.strip())
                if details.get("annulee") is True:
                    _incrementer(annulations, details.get("capacite"))

    # ── assistant/memory.py : le journal des décisions ────────────────────
    if memoire is not None:
        try:
            decisions = memoire.decisions(limite_journal)
        except Exception:
            decisions = None
        if decisions is None:
            lisible = False
        else:
            for entree in decisions if isinstance(decisions, (list, tuple)) else ():
                if not isinstance(entree, Mapping):
                    continue
                evenement = entree.get("evenement")
                detail = entree.get("detail")
                detail = detail if isinstance(detail, Mapping) else {}
                capacite = detail.get("capacite")
                if evenement in EVENEMENTS_ANNULATION:
                    _incrementer(annulations, capacite)
                elif evenement in EVENEMENTS_ECHEC:
                    _incrementer(echecs, capacite)
                elif evenement in EVENEMENTS_SUCCES:
                    _incrementer(succes, capacite)
                elif evenement == EVENEMENT_AUTONOMIE:
                    etape = detail.get("etape")
                    if etape == "echec":
                        _incrementer(echecs, capacite)
                    elif etape == "terminee":
                        _incrementer(succes if detail.get("ok", True) else echecs,
                                     capacite)

    ancien = etat.get("historique")
    fusionne: Dict[str, Any] = dict(ancien) if isinstance(ancien, Mapping) else {}
    fusionne.update({
        "lisible": lisible,
        "disponible": disponible,
        "annulations": dict(sorted(annulations.items())),
        "echecs": dict(sorted(echecs.items())),
        "succes": dict(sorted(succes.items())),
        # Trié et dédoublonné : la liste est comparée par ÉPIMÉTHÉE et affichée
        # dans les rapports, elle ne doit pas changer d'ordre d'un appel à
        # l'autre. Doublons retirés sur la forme comparable, pas sur la chaîne
        # brute — « C:\\X\\a.exe » et « c:/x/a.exe » sont le même fichier.
        "restaurations": [chemin for _, chemin in sorted(
            {_cle_chemin(c): c for c in restaurations}.items())],
    })
    etat["historique"] = fusionne
    return etat
