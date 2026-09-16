"""
assistant/voice.py — PROMÉTHÉE, la voix off d'ANTI-ZEEVIRIUS.

Du grec *pro-mētheus*, « celui qui pense avant » : ce module PRÉVIENT. Il
annonce une menace trouvée, une caméra allumée sans consentement, une
intrusion, la fin d'une analyse, le passage en Mode Incident. Il ne commente
pas, il ne converse pas, et il **n'écoute rien** : aucune reconnaissance
vocale n'existe ici, ni n'est prévue par ce lot. Une voix qui écoute est un
micro ouvert en permanence, et ce n'est pas ce qu'on installe sur la machine
de quelqu'un qui cherche à se protéger.

QUATRE DÉCISIONS, DANS L'ORDRE D'IMPORTANCE

1. **La confidentialité passe avant l'information.** Une voix est entendue
   par toute la pièce : le collègue, l'enfant, la visioconférence en cours.
   Un antivirus qui prononce « C:\\Users\\Jules\\Impots\\declaration.pdf est
   infecté » vient de dire à voix haute le nom de l'utilisateur, sa
   situation fiscale et l'organisation de son disque. PROMÉTHÉE dit « une
   menace dans vos documents ». Tout texte traverse `expurger()` AVANT
   d'entrer dans la file : le texte brut ne survit nulle part dans l'objet,
   ni dans la file, ni dans `etat()`, ni dans la commande lancée.

2. **Aucune dépendance, aucune clé, aucun réseau.** Windows embarque son
   synthétiseur depuis toujours : `System.Speech.Synthesis.SpeechSynthesizer`,
   atteint par PowerShell, présent d'origine sur Windows 10 et 11. Un
   antivirus qui exigerait un abonnement pour dire « menace détectée » aurait
   raté sa cible — et une voix qui passe par un service en ligne expédie le
   nom des fichiers de l'utilisateur à un tiers, ce qui est exactement
   l'inverse du métier. Hors Windows (développement), repli sur `spd-say` ou
   `espeak-ng` s'ils existent ; sinon **silence propre** : le module reste
   importable, réglable et testable, il ne parle simplement pas. Jamais
   d'exception.

3. **Jamais bloquant.** Une analyse ne doit pas attendre la fin d'une phrase.
   `dire()` met en file et rend la main immédiatement ; un fil de travail
   énonce. Et comme trente menaces ne font pas trente phrases, les annonces
   identiques encore en file sont **regroupées** en une seule énonciation.
   Réciter trente chemins prendrait plusieurs minutes, pendant lesquelles la
   seule information utile — « il y en a trente » — resterait inaudible.

4. **`taire()` coupe au milieu d'un mot.** Le processus en cours est tué, la
   file est vidée. Une voix qu'on ne peut pas faire taire est insupportable,
   et dans un bureau c'est un problème réel, pas une coquetterie. C'est
   pourquoi l'énonciation passe par `Popen` et non par `subprocess.run` :
   on garde une prise sur le processus.

INJECTION POWERSHELL — le texte à dire vient de noms de fichiers et de
messages d'erreur, donc de données NON MAÎTRISÉES. Ce dépôt a déjà livré une
exécution de commande arbitraire par `shell=True` sur une chaîne venue du
registre. Ici : jamais `shell=True`, la commande est une LISTE d'arguments,
les apostrophes sont doublées comme le fait déjà `assistant/notify.py`, et
les caractères de contrôle sont ôtés. `tests/test_assistant_voice.py` tente
une évasion et vérifie qu'elle échoue.
"""

from __future__ import annotations

import collections
import dataclasses
import hashlib
import os
import platform
import re
import shutil
import subprocess
import threading
import time
import unicodedata
from datetime import datetime
from pathlib import PureWindowsPath
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence, Tuple

# La bulle Windows et le lancement de commande synchrone vivent déjà dans la
# couche assistant ; on les consomme plutôt que d'en écrire une seconde
# version. Un seul endroit du projet sait échapper pour PowerShell.
from .notify import GRAVITES, executer_commande

__all__ = [
    "Promethee", "Annonce",
    "expurger", "echapper_powershell", "nettoyer_pour_voix",
    "phrase_menaces", "nombre_en_mots", "identifiant_annonce",
    "VERBOSITES", "REGLAGES_DEFAUT", "MOTEURS",
]


# ── Vocabulaire fermé ──────────────────────────────────────────────────────
# Trois niveaux, et pas quatre : chacun répond à une question différente.
#   discrete — rien de nominatif. C'est le défaut, parce que le défaut est ce
#              qui s'applique dans un bureau partagé où personne n'a réglé
#              quoi que ce soit.
#   normale  — le nom du fichier est prononcé, mais ni le chemin, ni le nom
#              de l'utilisateur, ni aucune adresse.
#   complete — l'utilisateur assume : on énonce ce qu'on a reçu.
VERBOSITES = ("discrete", "normale", "complete")

MOTEURS = ("sapi", "spd-say", "espeak-ng")

# Réglages persistés. Les clés sont triées à la lecture comme à l'écriture :
# ce dépôt a déjà eu un bogue de non-déterminisme par itération de `set`
# (`security/network_watch.py`), on ne le refait pas.
REGLAGES_DEFAUT: Dict[str, Any] = {
    "actif": True,
    "debit": 0,                       # -10 (lent) à +10 (rapide), 0 = normal
    "delai_groupement": 0.4,          # fenêtre de regroupement, en secondes
    "delai_repetition": 120.0,        # anti-répétition, en secondes
    "heures_silence": ["22:00", "07:00"],
    "verbosite": "discrete",
    "voix": "",                       # "" = voix par défaut du système
    "volume": 80,                     # 0 à 100
}

CLE_PREFERENCE = "voix"               # où les réglages vivent dans la mémoire

LONGUEUR_MAX = 400                    # caractères énoncés au plus
FILE_MAX = 64                         # annonces en attente au plus
DELAI_ENONCE_MAX = 180.0              # une phrase qui dure 3 min est un bogue


# ═══════════════════════════════════════════════════════════════════════════
#  RÉDACTION — la partie la plus importante de ce module
# ═══════════════════════════════════════════════════════════════════════════
# Chaque motif ci-dessous correspond à une chose qu'on ne dit pas à voix
# haute dans une pièce où l'on n'est pas seul. L'ordre d'application compte :
# une adresse de courriel contient un nom de domaine, une URL contient un
# chemin, un jeton ressemble à un nom de fichier. Le plus spécifique passe
# d'abord, sinon un motif large avale la moitié d'un autre et le reste du
# texte redevient parlant.

_RE_URL = re.compile(r"\b(?:https?|ftps?|file)://[^\s<>\"']+", re.IGNORECASE)
_RE_COURRIEL = re.compile(r"\b[\w.+%-]+@[\w-]+(?:\.[\w-]+)+\b")
_RE_MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")
_RE_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b")
# Au moins deux « : » exigés, sinon « 22:00 » — une heure de silence — serait
# pris pour une adresse IPv6 et l'interface annoncerait n'importe quoi.
_RE_IPV6 = re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f]{1,4}\b")
_RE_UNC = re.compile(r"\\\\[A-Za-z0-9._-]+(?:\\[^\s\\/:*?\"<>|]+)*")
_RE_REGISTRE = re.compile(
    r"\b(?:HKEY_[A-Z_]+|HKLM|HKCU|HKCR|HKU|HKCC)(?:[\\/][^\s\\/:*?\"<>|]+)*")
_RE_CHEMIN_WINDOWS = re.compile(r"\b[A-Za-z]:[\\/](?:[^\s\\/:*?\"<>|]+[\\/]?)*")
_RE_CHEMIN_POSIX = re.compile(r"(?:/[A-Za-z0-9._@%+-]+){2,}/?")
# Un jeton hexadécimal (empreinte, clé) ou une chaîne base64 mêlant chiffres
# et lettres : personne n'a besoin d'entendre ça, et une clé d'API prononcée
# à voix haute est une clé compromise.
_RE_JETON_HEX = re.compile(r"\b[0-9A-Fa-f]{32,}\b")
_RE_JETON_B64 = re.compile(
    r"\b(?=[A-Za-z0-9+/=_-]*[0-9])(?=[A-Za-z0-9+/=_-]*[A-Za-z])"
    r"[A-Za-z0-9+/=_-]{20,}\b")
_RE_FICHIER_NU = re.compile(
    r"\b[\w][\w .()'&+-]{0,60}\."
    r"(?:exe|dll|sys|drv|bat|cmd|ps1|vbs|jse?|jar|scr|msi|com|pif|lnk"
    r"|zip|rar|7z|tar|gz|iso|img"
    r"|pdf|docx?|xlsx?|pptx?|odt|ods|rtf|txt|csv|md"
    r"|jpe?g|png|gif|bmp|webp|svg|heic"
    r"|mp[34]|wav|flac|avi|mkv|mov|wmv"
    r"|tmp|bak|log|json|xml|ini|cfg|reg|key|pem|pfx|crt|db|sqlite3?)\b",
    re.IGNORECASE)

# Remplacements : des groupes nominaux français, prononçables, qui disent le
# GENRE de l'information sans la livrer. « Une adresse réseau » suffit à
# comprendre qu'il s'agit d'un contact distant ; l'adresse elle-même est dans
# l'interface web, qui n'est pas entendue par la pièce entière.
_REMPLACEMENT = {
    "url": "une adresse web",
    "courriel": "une adresse de courriel",
    "mac": "une adresse matérielle",
    "ip": "une adresse réseau",
    "unc": "un dossier partagé",
    "registre": "une clé du registre",
    "jeton": "une clé",
    "fichier": "un fichier",
    "utilisateur": "l'utilisateur",
    "machine": "cet ordinateur",
}

# Dossiers reconnus, du plus parlant au plus générique. Dire « dans vos
# documents » situe la menace pour l'utilisateur — c'est ce qui rend
# l'annonce actionnable — sans nommer ni le compte ni l'arborescence.
_DOSSIERS_CONNUS: Tuple[Tuple[str, str], ...] = (
    ("documents", "un fichier dans vos documents"),
    ("desktop", "un fichier sur votre bureau"),
    ("bureau", "un fichier sur votre bureau"),
    ("downloads", "un fichier dans vos téléchargements"),
    ("telechargements", "un fichier dans vos téléchargements"),
    ("téléchargements", "un fichier dans vos téléchargements"),
    ("pictures", "un fichier dans vos images"),
    ("images", "un fichier dans vos images"),
    ("music", "un fichier dans votre musique"),
    ("musique", "un fichier dans votre musique"),
    ("videos", "un fichier dans vos vidéos"),
    ("vidéos", "un fichier dans vos vidéos"),
    ("appdata", "un fichier de données d'application"),
    ("programdata", "un fichier de données d'application"),
    ("temp", "un fichier temporaire"),
    ("tmp", "un fichier temporaire"),
    ("windows", "un fichier du dossier système"),
    ("system32", "un fichier du dossier système"),
    ("program files", "un fichier de programme"),
    ("program files (x86)", "un fichier de programme"),
    ("startup", "un fichier de démarrage"),
    ("demarrage", "un fichier de démarrage"),
)


def _sans_accents(texte: str) -> str:
    """Forme comparable d'un nom de dossier (« Téléchargements » = « telechargements »)."""
    decompose = unicodedata.normalize("NFD", texte)
    return "".join(c for c in decompose if unicodedata.category(c) != "Mn").lower()


def _libelle_chemin_windows(chemin: str) -> str:
    """Traduit un chemin Windows en groupe nominal non nominatif.

    `PureWindowsPath` et non `Path` : sous Linux, `Path("C:\\Users\\x")` ne se
    découpe pas du tout, et un garde-fou qui s'appuierait dessus ne se
    déclencherait jamais. Ce piège est déjà consigné dans AGENTS.md.
    """
    try:
        parties = [p for p in PureWindowsPath(chemin.replace("/", "\\")).parts]
    except (TypeError, ValueError):            # pragma: no cover — défensif
        return _REMPLACEMENT["fichier"]

    # Le dossier le PLUS PROFOND reconnu gagne : « …\\Windows\\Temp\\x » est
    # d'abord un fichier temporaire, pas un fichier système.
    for partie in reversed(parties):
        nu = _sans_accents(str(partie).strip("\\/"))
        for cle, libelle in _DOSSIERS_CONNUS:
            if nu == _sans_accents(cle):
                return libelle
    return _REMPLACEMENT["fichier"]


def _base_windows(chemin: str) -> str:
    """Dernier élément d'un chemin Windows — utilisé en verbosité `normale`."""
    try:
        nom = PureWindowsPath(chemin.replace("/", "\\")).name
    except (TypeError, ValueError):            # pragma: no cover — défensif
        nom = ""
    return nom or _REMPLACEMENT["fichier"]


def _remplacer_nom(texte: str, nom: Optional[str], remplacement: str) -> str:
    """Ôte un nom propre (utilisateur, machine) où qu'il apparaisse.

    Le nom de session et le nom de la machine fuient par des chemins qu'aucun
    motif ne couvre : un message d'erreur, un titre de fenêtre, une ligne de
    journal. On les connaît, donc on les cherche nommément. En dessous de
    trois caractères, on s'abstient : remplacer « al » partout mutilerait le
    texte plus sûrement qu'il ne protégerait qui que ce soit.
    """
    nom = (nom or "").strip()
    if len(nom) < 3:
        return texte
    return re.sub(r"(?<![\w])" + re.escape(nom) + r"(?![\w])",
                  remplacement, texte, flags=re.IGNORECASE)


def expurger(texte: str, verbosite: str = "discrete", *,
             nom_utilisateur: Optional[str] = None,
             nom_machine: Optional[str] = None) -> str:
    """Retire d'un texte tout ce qui ne doit pas être prononcé à voix haute.

    Chemins, noms de fichiers, nom d'utilisateur, nom de machine, adresses IP,
    adresses de courriel, URL, clés du registre, jetons et empreintes. En
    `complete`, l'utilisateur a explicitement accepté d'être entendu : le
    texte sort tel quel, seulement nettoyé de ses caractères de contrôle.

    Fonction pure, sans état, sans accès au disque : c'est ce qui permet de la
    tester avec de vrais exemples sans rien simuler.
    """
    texte = str(texte or "")
    if verbosite not in VERBOSITES:
        verbosite = "discrete"
    if verbosite == "complete":
        return nettoyer_pour_voix(texte)

    # 1. Ce qui est le plus spécifique d'abord.
    texte = _RE_URL.sub(_REMPLACEMENT["url"], texte)
    texte = _RE_COURRIEL.sub(_REMPLACEMENT["courriel"], texte)
    texte = _RE_MAC.sub(_REMPLACEMENT["mac"], texte)
    texte = _RE_IPV4.sub(_REMPLACEMENT["ip"], texte)
    texte = _RE_IPV6.sub(_REMPLACEMENT["ip"], texte)
    texte = _RE_UNC.sub(_REMPLACEMENT["unc"], texte)
    texte = _RE_REGISTRE.sub(_REMPLACEMENT["registre"], texte)

    # 2. Les chemins. En `normale`, le nom du fichier reste : c'est
    #    l'information que l'utilisateur seul devant sa machine veut
    #    entendre, et le chemin — donc son nom de compte — disparaît quand
    #    même.
    if verbosite == "normale":
        texte = _RE_CHEMIN_WINDOWS.sub(lambda m: _base_windows(m.group(0)), texte)
        texte = _RE_CHEMIN_POSIX.sub(
            lambda m: m.group(0).rstrip("/").rsplit("/", 1)[-1] or _REMPLACEMENT["fichier"],
            texte)
    else:
        texte = _RE_CHEMIN_WINDOWS.sub(
            lambda m: _libelle_chemin_windows(m.group(0)), texte)
        texte = _RE_CHEMIN_POSIX.sub(_REMPLACEMENT["fichier"], texte)

    # 3. Les jetons, après les chemins : une empreinte SHA-256 isolée n'est
    #    pas un chemin, mais un chemin peut en contenir une.
    texte = _RE_JETON_HEX.sub(_REMPLACEMENT["jeton"], texte)
    texte = _RE_JETON_B64.sub(_REMPLACEMENT["jeton"], texte)

    # 4. Les noms propres connus de la machine.
    texte = _remplacer_nom(texte, nom_utilisateur, _REMPLACEMENT["utilisateur"])
    texte = _remplacer_nom(texte, nom_machine, _REMPLACEMENT["machine"])

    # 5. Un nom de fichier nu, sans chemin : « declaration.pdf est infecté ».
    #    Uniquement en `discrete` — c'est précisément ce que `normale`
    #    autorise.
    if verbosite == "discrete":
        texte = _RE_FICHIER_NU.sub(_REMPLACEMENT["fichier"], texte)

    return nettoyer_pour_voix(texte)


def nettoyer_pour_voix(texte: str) -> str:
    """Rend un texte énonçable, et inoffensif pour la ligne de commande.

    Les caractères de contrôle partent : un `\\r` ou un `\\n` dans un
    argument n'apporte rien à une phrase parlée et brouille les analyses de
    sortie. Un tiret en tête aussi : `spd-say` et `espeak-ng` prendraient le
    texte pour une option, et l'annonce se transformerait en erreur de
    syntaxe. La longueur est bornée — une voix qui récite trois pages n'est
    plus un avertissement.
    """
    texte = str(texte or "")
    texte = "".join(" " if unicodedata.category(c) in ("Cc", "Cf") else c
                    for c in texte)
    texte = re.sub(r"\s+", " ", texte).strip()
    texte = texte.lstrip("-").strip()
    if len(texte) > LONGUEUR_MAX:
        texte = texte[:LONGUEUR_MAX].rstrip() + "…"
    return texte


def echapper_powershell(texte: str) -> str:
    """Double les apostrophes pour une chaîne PowerShell entre guillemets simples.

    Même discipline que `assistant/notify.envoyer_bulle()`, pour le même
    risque : un fichier nommé « L'espion.exe » refermerait la chaîne, et la
    suite du texte serait exécutée comme du code PowerShell. Le nom vient
    d'un tiers — c'est une donnée hostile par défaut.

    À l'intérieur d'une chaîne entre guillemets SIMPLES, PowerShell
    n'interprète ni `$`, ni les accents graves, ni les parenthèses de
    sous-expression : l'apostrophe est le seul caractère qui referme la
    chaîne, donc le seul à neutraliser. Les retours à la ligne sont déjà
    ôtés par `nettoyer_pour_voix()`.
    """
    return str(texte or "").replace("'", "''")


def nombre_en_mots(n: int) -> str:
    """Petit nombre en français, chiffres au-delà de douze.

    Un synthétiseur lit « 3 » correctement, mais « trois » se prononce mieux
    en tête de phrase et évite les surprises d'un moteur mal réglé.
    """
    mots = ("zéro", "une", "deux", "trois", "quatre", "cinq", "six", "sept",
            "huit", "neuf", "dix", "onze", "douze")
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "plusieurs"
    if 0 <= n < len(mots):
        return mots[n]
    return str(n)


def phrase_menaces(nombre: int) -> str:
    """L'annonce groupée d'une analyse : « Trois menaces détectées. »

    C'est la forme que doit prendre le résultat d'un scan, et non trente
    phrases nommant trente fichiers. Le détail est dans l'interface web ;
    la voix donne le chiffre, qui est la seule chose qu'on retient en
    l'entendant.
    """
    try:
        nombre = int(nombre)
    except (TypeError, ValueError):
        nombre = 0
    if nombre <= 0:
        return "Analyse terminée, aucune menace détectée."
    if nombre == 1:
        return "Une menace détectée."
    return f"{nombre_en_mots(nombre).capitalize()} menaces détectées."


def identifiant_annonce(texte: str, gravite: str) -> str:
    """Condensé court et STABLE d'une annonce, pour l'anti-répétition.

    Même raisonnement que `assistant/notify.identifiant_notification()`, y
    compris le séparateur `\\x1f` qui ne peut pas apparaître dans un texte
    affichable : sans lui, ("ab", "c") et ("a", "bc") donneraient le même
    identifiant et deux annonces différentes se masqueraient l'une l'autre.
    Un condensé et non un compteur : il reste le même d'un lancement à
    l'autre.
    """
    brut = "\x1f".join((str(texte or ""), str(gravite or "")))
    return hashlib.sha256(brut.encode("utf-8")).hexdigest()[:16]


# ═══════════════════════════════════════════════════════════════════════════
#  Heures de silence
# ═══════════════════════════════════════════════════════════════════════════
def _minutes(valeur: Any) -> Optional[int]:
    """« 22:00 » → 1320. Tout le reste → None, sans lever."""
    texte = str(valeur or "").strip()
    m = re.fullmatch(r"(\d{1,2})\s*[:hH]\s*(\d{1,2})?", texte)
    if not m:
        return None
    heures = int(m.group(1))
    minutes = int(m.group(2) or 0)
    if not (0 <= heures <= 23 and 0 <= minutes <= 59):
        return None
    return heures * 60 + minutes


def _normaliser_plage(valeur: Any) -> Optional[List[str]]:
    """Valide une plage `[debut, fin]`. `None` = aucune plage de silence."""
    if valeur in (None, "", (), []):
        return None
    if isinstance(valeur, dict):
        valeur = [valeur.get("debut"), valeur.get("fin")]
    if not isinstance(valeur, (list, tuple)) or len(valeur) != 2:
        raise ValueError(
            "heures_silence attend [début, fin] au format « HH:MM », "
            "ou null pour aucune plage")
    debut, fin = _minutes(valeur[0]), _minutes(valeur[1])
    if debut is None or fin is None:
        raise ValueError(
            f"heure illisible dans heures_silence : {list(valeur)!r} "
            "(format attendu « HH:MM »)")
    return [f"{debut // 60:02d}:{debut % 60:02d}", f"{fin // 60:02d}:{fin % 60:02d}"]


def _dans_la_plage(minute_du_jour: int, plage: Optional[Sequence[str]]) -> bool:
    """Vrai si l'instant tombe dans la plage, passage de minuit compris.

    Une plage 22:00 → 07:00 traverse minuit : le test naïf `debut <= t < fin`
    y serait faux toute la nuit, c'est-à-dire exactement quand la plage doit
    s'appliquer.
    """
    if not plage:
        return False
    debut, fin = _minutes(plage[0]), _minutes(plage[1])
    if debut is None or fin is None or debut == fin:
        return False
    if debut < fin:
        return debut <= minute_du_jour < fin
    return minute_du_jour >= debut or minute_du_jour < fin


# ═══════════════════════════════════════════════════════════════════════════
#  Annonce
# ═══════════════════════════════════════════════════════════════════════════
@dataclasses.dataclass(frozen=True)
class Annonce:
    """Une phrase en attente. `texte` est DÉJÀ expurgé.

    Le texte brut n'entre jamais ici : la rédaction a lieu dans `dire()`,
    avant la mise en file. Ainsi aucun chemin ne traîne dans la mémoire du
    processus, dans `etat()` servi à l'interface web, ni dans un journal.
    """
    texte: str
    gravite: str
    cle: str
    horodatage: float
    interrompre: bool = False

    def to_dict(self) -> Dict:
        return dataclasses.asdict(self)


# ═══════════════════════════════════════════════════════════════════════════
#  PROMÉTHÉE
# ═══════════════════════════════════════════════════════════════════════════
class Promethee:
    """La voix off. Elle annonce, elle n'écoute pas.

    Tout ce qui touche au système est injectable, parce que le développement
    se fait sous Linux et que la suite de tests ne lance aucun processus :
    `executer` (commande synchrone, pour lister les voix), `lanceur`
    (démarrage du processus d'énonciation, qu'on doit pouvoir tuer),
    `systeme`, `chercher_programme` et `horloge`.
    """

    def __init__(self, *,
                 memoire: Any = None,
                 executer: Optional[Callable[..., Dict]] = None,
                 lanceur: Optional[Callable[[Sequence[str]], Any]] = None,
                 systeme: Optional[str] = None,
                 chercher_programme: Optional[Callable[[str], Optional[str]]] = None,
                 horloge: Optional[Callable[[], float]] = None,
                 nom_utilisateur: Optional[str] = None,
                 nom_machine: Optional[str] = None,
                 demarrage_auto: bool = True) -> None:
        self._memoire = memoire
        self._executer = executer or executer_commande
        self._lanceur = lanceur or _lancer_processus
        self._systeme = systeme if systeme is not None else platform.system()
        self._which = chercher_programme or shutil.which
        self._horloge = horloge or time.time
        self._demarrage_auto = bool(demarrage_auto)

        # Ce que la machine sait de son propriétaire. Récupéré une fois, sans
        # lever : sur une machine mal configurée, `getuser()` échoue, et ce
        # n'est pas une raison pour que la voix cesse de fonctionner.
        self._nom_utilisateur = (nom_utilisateur if nom_utilisateur is not None
                                 else _nom_de_session())
        self._nom_machine = (nom_machine if nom_machine is not None
                             else _nom_de_machine())

        self._verrou = threading.RLock()
        self._reveil = threading.Event()
        self._arret = threading.Event()
        self._fil: Optional[threading.Thread] = None
        self._file: Deque[Annonce] = collections.deque()
        self._processus: Any = None
        self._derniers: Dict[str, float] = {}      # clé d'annonce → dernier dit
        self._voix_cache: Optional[Tuple[str, ...]] = None
        self._moteur_cache: Optional[Tuple[Optional[str], str, Optional[str]]] = None
        self._derniere_erreur: str = ""
        self._derniere_annonce: str = ""
        self._compteurs = {"acceptees": 0, "refusees": 0, "enoncees": 0,
                           "groupees": 0, "interruptions": 0, "echecs": 0}
        self._reglages = self._charger_reglages()

    # ── Réglages ───────────────────────────────────────────────────────────
    def _charger_reglages(self) -> Dict[str, Any]:
        """Réglages par défaut, complétés par ce que la mémoire a retenu.

        Une valeur illisible est IGNORÉE, pas refusée : un fichier de mémoire
        bricolé à la main ne doit pas rendre la voix inutilisable — il doit
        la ramener à son réglage d'usine, champ par champ.
        """
        reglages = {c: REGLAGES_DEFAUT[c] for c in sorted(REGLAGES_DEFAUT)}
        brut = None
        if self._memoire is not None:
            try:
                brut = self._memoire.preference(CLE_PREFERENCE, None)
            except Exception:
                brut = None
        if isinstance(brut, dict):
            for champ in sorted(REGLAGES_DEFAUT):
                if champ not in brut:
                    continue
                try:
                    reglages[champ] = _valider_reglage(champ, brut[champ])
                except ValueError:
                    pass
        return reglages

    def _persister(self) -> None:
        """Enregistre les réglages. L'échec n'est pas une exception.

        `assistant/memory.py` suit déjà cette règle : si le disque refuse
        l'écriture, l'état reste correct en mémoire et l'appel ne lève pas.
        On perd le souvenir, on ne perd pas la session — et surtout, on ne
        perd pas la voix.
        """
        if self._memoire is None:
            return
        try:
            self._memoire.definir_preference(
                CLE_PREFERENCE, {c: self._reglages[c] for c in sorted(self._reglages)})
        except Exception:
            pass

    def reglages(self, **champs) -> Dict[str, Any]:
        """Lit (sans argument) ou écrit les réglages, et rend l'état complet.

        Un champ inconnu lève `ValueError` au lieu d'être avalé : une faute
        de frappe silencieusement ignorée donnerait un réglage qui ne change
        jamais, et le bogue serait cherché ailleurs. Même raisonnement que
        `Memoire.definir_profil()`.
        """
        if champs:
            inconnus = sorted(set(champs) - set(REGLAGES_DEFAUT))
            if inconnus:
                raise ValueError(
                    "réglage inconnu : " + ", ".join(inconnus)
                    + " (attendus : " + ", ".join(sorted(REGLAGES_DEFAUT)) + ")")
            valides = {c: _valider_reglage(c, champs[c]) for c in sorted(champs)}
            with self._verrou:
                self._reglages.update(valides)
                # Couper la voix vide la file : laisser des phrases en attente
                # d'un réglage qu'on vient de désactiver, c'est promettre du
                # silence et parler quand même à la réactivation.
                coupee = not self._reglages["actif"]
            if coupee:
                self.taire()
            self._persister()
        with self._verrou:
            return {c: self._reglages[c] for c in sorted(self._reglages)}

    # ── Moteur de synthèse ─────────────────────────────────────────────────
    def _moteur(self) -> Tuple[Optional[str], str, Optional[str]]:
        """(nom du moteur, motif d'indisponibilité, programme) — mis en cache.

        Sous Windows, SAPI est présent d'origine : aucune détection à faire,
        aucun paquet à installer, aucune clé à saisir. Ailleurs, on cherche
        un synthétiseur de bureau ; s'il n'y en a pas, le moteur est `None`
        et le module se tait proprement. C'est un mode de fonctionnement
        normal en développement, pas une panne.
        """
        with self._verrou:
            if self._moteur_cache is not None:
                return self._moteur_cache

        if str(self._systeme).lower().startswith("windows"):
            programme = self._which("powershell") or self._which("pwsh") or "powershell"
            resultat = ("sapi", "", programme)
        else:
            trouve = None
            for candidat in ("spd-say", "espeak-ng", "espeak"):
                chemin = self._which(candidat)
                if chemin:
                    trouve = ("spd-say" if candidat == "spd-say" else "espeak-ng",
                              "", chemin)
                    break
            resultat = trouve or (
                None,
                "aucun synthétiseur vocal détecté (Windows : SAPI par PowerShell ; "
                "ailleurs : spd-say ou espeak-ng)",
                None)

        with self._verrou:
            self._moteur_cache = resultat
            return resultat

    def disponible(self) -> bool:
        return self._moteur()[0] is not None

    def voix_disponibles(self, *, rafraichir: bool = False) -> Tuple[str, ...]:
        """Les voix installées sur la machine, triées.

        Triées, et dédoublonnées par un `set` dont on ne parcourt JAMAIS
        l'ordre naturel : ce dépôt a déjà livré un résultat qui changeait
        d'une exécution à l'autre selon `PYTHONHASHSEED`. Une liste de voix
        dans un menu déroulant qui se réordonne à chaque rafraîchissement
        est le même défaut, en plus visible.

        Hors Windows, la liste est vide : `spd-say` et `espeak-ng` exposent
        des langues et des variantes, pas des voix SAPI, et prétendre le
        contraire donnerait une liste que l'interface ne saurait pas réutiliser.
        """
        with self._verrou:
            if self._voix_cache is not None and not rafraichir:
                return self._voix_cache

        moteur, _, programme = self._moteur()
        if moteur != "sapi" or not programme:
            with self._verrou:
                self._voix_cache = ()
                return ()

        script = (
            "$ErrorActionPreference='Stop';"
            "Add-Type -AssemblyName System.Speech;"
            "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            "$s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }"
        )
        reponse = self._executer(
            [programme, "-NoProfile", "-NonInteractive", "-Command", script],
            timeout=25)
        noms = set()
        if isinstance(reponse, dict) and reponse.get("code") == 0:
            for ligne in str(reponse.get("sortie") or "").splitlines():
                ligne = ligne.strip()
                if ligne:
                    noms.add(ligne)
        else:
            motif = ""
            if isinstance(reponse, dict):
                motif = str(reponse.get("erreur") or "").strip()
            with self._verrou:
                self._derniere_erreur = motif or "liste des voix indisponible"

        resultat = tuple(sorted(noms))
        with self._verrou:
            self._voix_cache = resultat
            return resultat

    # ── Construction de la commande ────────────────────────────────────────
    def _commande(self, texte: str) -> Optional[List[str]]:
        """La commande d'énonciation, sous forme de LISTE d'arguments.

        Jamais de chaîne, jamais `shell=True` : le texte vient de noms de
        fichiers et de messages d'erreur, donc d'un tiers. Une liste
        d'arguments ne passe par aucun interpréteur de commandes, ce qui
        supprime la classe entière des injections de commande — et laisse
        seulement l'échappement de la chaîne PowerShell à traiter.
        """
        moteur, _, programme = self._moteur()
        if moteur is None or not programme:
            return None
        with self._verrou:
            debit = int(self._reglages["debit"])
            volume = int(self._reglages["volume"])
            voix = str(self._reglages["voix"] or "")

        if moteur == "sapi":
            # `debit` et `volume` sont des entiers validés par
            # `_valider_reglage` : leur interpolation est sûre, et elle est
            # faite APRÈS conversion en `int`, pas sur la valeur reçue.
            morceaux = [
                "$ErrorActionPreference='Stop';",
                "Add-Type -AssemblyName System.Speech;",
                "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;",
                f"$s.Rate={max(-10, min(10, debit))};",
                f"$s.Volume={max(0, min(100, volume))};",
            ]
            if voix:
                # Une voix absente ne doit pas faire échouer l'annonce : on
                # retombe sur la voix par défaut du système plutôt que de se
                # taire parce qu'un réglage désigne une voix désinstallée.
                morceaux.append(
                    f"try{{$s.SelectVoice('{echapper_powershell(voix)}')}}catch{{}};")
            morceaux.append(f"$s.Speak('{echapper_powershell(texte)}')")
            return [programme, "-NoProfile", "-NonInteractive", "-Command",
                    "".join(morceaux)]

        if moteur == "spd-say":
            # `-w` attend la fin de l'énonciation : sans lui, le processus
            # rend la main aussitôt et `taire()` n'aurait plus rien à tuer.
            return [programme, "-w",
                    "-r", str(max(-100, min(100, debit * 10))),
                    "-i", str(max(-100, min(100, volume * 2 - 100))),
                    "--", texte]

        return [programme,
                "-s", str(max(80, min(450, 175 + debit * 10))),
                "-a", str(max(0, min(200, volume * 2))),
                "--", texte]

    # ── Dire ───────────────────────────────────────────────────────────────
    def dire(self, texte: str, *, gravite: str = "info",
             interrompre: bool = False) -> bool:
        """Met une annonce en file. **Ne bloque pas.**

        Rend `True` si l'annonce a été acceptée, `False` si elle a été
        écartée — voix coupée, heures de silence, répétition, texte vide,
        aucun moteur, file saturée. L'appelant n'a donc pas à savoir si la
        machine peut parler : il annonce, et le refus est une information,
        pas une erreur.

        `interrompre=True` coupe ce qui est en cours et vide la file avant
        d'entrer : réservé à ce qui ne peut pas attendre son tour derrière
        une annonce de confort.
        """
        if gravite not in GRAVITES:
            raise ValueError(f"gravité inconnue : {gravite!r} (attendu {GRAVITES})")

        with self._verrou:
            verbosite = str(self._reglages["verbosite"])
            actif = bool(self._reglages["actif"])
            plage = self._reglages["heures_silence"]
            delai = float(self._reglages["delai_repetition"])

        propre = expurger(texte, verbosite,
                          nom_utilisateur=self._nom_utilisateur,
                          nom_machine=self._nom_machine)
        if not propre:
            return self._refuser()
        if not actif:
            return self._refuser()
        if not self.disponible():
            return self._refuser()

        # Les heures de silence ne s'appliquent PAS au critique. Une
        # intrusion en cours à 3 h du matin est précisément ce qu'on veut
        # être réveillé pour entendre ; un fichier temporaire, non.
        if gravite != "critique" and self._en_silence(plage):
            return self._refuser()

        cle = identifiant_annonce(propre, gravite)
        maintenant = float(self._horloge())

        with self._verrou:
            # L'anti-répétition ne regarde que ce qui a DÉJÀ été dit. Une
            # annonce identique encore en file n'est pas refusée : elle sera
            # regroupée, ce qui est le comportement attendu quand une analyse
            # trouve trente fois la même chose.
            en_file = any(a.cle == cle for a in self._file)
            dernier = self._derniers.get(cle)
            if not en_file and dernier is not None and maintenant - dernier < delai:
                self._compteurs["refusees"] += 1
                return False

            if interrompre:
                self._file.clear()
            elif len(self._file) >= FILE_MAX:
                # Saturée : on écarte la nouvelle plutôt que la plus ancienne.
                # Ce qui attend depuis le plus longtemps est ce qui a le plus
                # de chances d'être l'annonce d'origine, donc la plus utile.
                self._compteurs["refusees"] += 1
                return False

            self._file.append(Annonce(texte=propre, gravite=gravite, cle=cle,
                                      horodatage=maintenant,
                                      interrompre=bool(interrompre)))
            self._compteurs["acceptees"] += 1
            self._reveil.set()

        if interrompre:
            self._tuer_processus()
        self._assurer_fil()
        return True

    def _refuser(self) -> bool:
        with self._verrou:
            self._compteurs["refusees"] += 1
        return False

    def annoncer_menaces(self, nombre: int, *, gravite: str = "alerte") -> bool:
        """Le résultat d'une analyse, en une phrase et un chiffre.

        Raccourci volontairement offert aux scanners pour qu'ils n'aient pas
        à boucler sur leurs résultats : c'est la boucle qui produit trente
        phrases, et l'empêcher à la source vaut mieux que la regrouper après.
        """
        return self.dire(phrase_menaces(nombre), gravite=gravite)

    def _en_silence(self, plage: Optional[Sequence[str]] = None) -> bool:
        """Vrai si l'instant courant tombe dans la plage de silence."""
        if plage is None:
            with self._verrou:
                plage = self._reglages["heures_silence"]
        try:
            instant = datetime.fromtimestamp(float(self._horloge()))
        except (OverflowError, OSError, ValueError):   # pragma: no cover
            return False
        return _dans_la_plage(instant.hour * 60 + instant.minute, plage)

    # ── Taire ──────────────────────────────────────────────────────────────
    def taire(self) -> None:
        """Coupe IMMÉDIATEMENT, au milieu d'un mot, et vide la file.

        Ne joint aucun fil et n'attend rien : une méthode « coupe la voix »
        qui prend une seconde à rendre la main a déjà échoué. Le fil de
        travail constatera la disparition de son processus et passera à la
        suite — qui est vide.
        """
        with self._verrou:
            self._file.clear()
            self._compteurs["interruptions"] += 1
            # Réveiller le fil : sans cela, il resterait en attente sur un
            # événement qu'aucune annonce ne viendra plus poser.
            self._reveil.set()
        self._tuer_processus()

    def _tuer_processus(self) -> None:
        with self._verrou:
            processus = self._processus
        if processus is None:
            return
        for methode in ("kill", "terminate"):
            fonction = getattr(processus, methode, None)
            if fonction is None:
                continue
            try:
                fonction()
                return
            except Exception:
                # Un processus déjà mort, ou un droit refusé : l'un et l'autre
                # aboutissent au silence demandé. On n'insiste pas.
                continue

    # ── État ───────────────────────────────────────────────────────────────
    def etat(self) -> Dict[str, Any]:
        """Photographie lisible par l'interface web.

        Tout ce qui sort d'ici est déjà expurgé : `file_details` et
        `derniere_annonce` contiennent le texte tel qu'il serait prononcé,
        donc sans chemin ni nom. L'interface peut l'afficher sans
        précaution supplémentaire.
        """
        moteur, motif, _ = self._moteur()
        with self._verrou:
            file = list(self._file)
            reglages = {c: self._reglages[c] for c in sorted(self._reglages)}
            compteurs = {c: self._compteurs[c] for c in sorted(self._compteurs)}
            parle = self._processus is not None
            erreur = self._derniere_erreur
            derniere = self._derniere_annonce
        return {
            "disponible": moteur is not None,
            "moteur": moteur,
            "raison": motif,
            "en_train_de_parler": parle,
            "file": len(file),
            "file_details": [a.to_dict() for a in file],
            "silence_actif": self._en_silence(),
            "reglages": reglages,
            "compteurs": compteurs,
            "derniere_annonce": derniere,
            "derniere_erreur": erreur,
            "fil_actif": bool(self._fil and self._fil.is_alive()),
            "ecoute": False,          # PROMÉTHÉE parle ; elle n'écoute jamais.
        }

    # ── Fil de travail ─────────────────────────────────────────────────────
    def _assurer_fil(self) -> None:
        """Démarre le fil d'énonciation au premier besoin, jamais avant.

        Construire un objet `Promethee` — ce que fait le pont web au premier
        appel de `voix.etat` — ne doit pas créer de fil : un module qu'on
        interroge sans jamais le faire parler ne coûte rien.
        """
        if not self._demarrage_auto:
            return
        with self._verrou:
            if self._fil is not None and self._fil.is_alive():
                return
            self._arret.clear()
            self._fil = threading.Thread(target=self._boucle, name="az-voix",
                                         daemon=True)
            self._fil.start()

    def _boucle(self) -> None:
        while not self._arret.is_set():
            if not self._reveil.wait(0.25):
                continue
            with self._verrou:
                if not self._file:
                    self._reveil.clear()
                    continue
                pressee = self._file[0].gravite == "critique" or self._file[0].interrompre
                fenetre = float(self._reglages["delai_groupement"])
            # Fenêtre de regroupement : un instant d'attente laisse arriver
            # les annonces sœurs d'une même analyse, pour qu'elles fassent
            # une phrase et non trente. Le critique ne l'attend pas.
            if not pressee and fenetre > 0:
                self._arret.wait(fenetre)
            self.traiter_file(limite=1)

    def traiter_file(self, limite: Optional[int] = None) -> int:
        """Énonce ce qui attend, ici et maintenant. Rend le nombre de phrases.

        Publique et synchrone : c'est la surface par laquelle les tests
        vérifient le regroupement, l'anti-répétition et la commande produite
        sans dépendre de l'ordonnancement d'un fil. Un appelant qui préfère
        piloter lui-même l'énonciation (`demarrage_auto=False`) passe par là.
        """
        dites = 0
        while limite is None or dites < limite:
            groupe = self._prochaine()
            if groupe is None:
                break
            annonce, nombre = groupe
            texte = annonce.texte
            if nombre > 1:
                texte = nettoyer_pour_voix(
                    f"{annonce.texte} — {nombre_en_mots(nombre)} occurrences.")
                with self._verrou:
                    self._compteurs["groupees"] += nombre - 1
            if self._enoncer(texte, annonce):
                dites += 1
        return dites

    def _prochaine(self) -> Optional[Tuple[Annonce, int]]:
        """Retire la tête de file et, avec elle, ses jumelles.

        Le regroupement s'appuie sur la clé d'annonce, donc sur le texte
        DÉJÀ expurgé : trente chemins différents dans le même dossier
        donnent trente fois « une menace dans vos documents », c'est-à-dire
        une seule annonce. La rédaction rend ainsi le regroupement exact au
        lieu d'approximatif.
        """
        with self._verrou:
            if not self._file:
                self._reveil.clear()
                return None
            annonce = self._file.popleft()
            nombre = 1
            restantes: Deque[Annonce] = collections.deque()
            for autre in self._file:
                if autre.cle == annonce.cle:
                    nombre += 1
                else:
                    restantes.append(autre)
            self._file = restantes
            if not self._file:
                self._reveil.clear()
            return annonce, nombre

    def _enoncer(self, texte: str, annonce: Annonce) -> bool:
        """Lance l'énonciation et attend sa fin. Ne lève jamais.

        Appelé depuis le fil de travail : c'est le seul endroit du module qui
        bloque, et c'est précisément pour que `dire()` ne bloque pas.
        """
        # La plage de silence est revérifiée ICI : une annonce entrée à
        # 21h59 et énoncée à 22h00 doit se taire. Le critique passe toujours.
        if annonce.gravite != "critique" and self._en_silence():
            with self._verrou:
                self._compteurs["refusees"] += 1
            return False

        commande = self._commande(texte)
        if commande is None:
            with self._verrou:
                self._compteurs["refusees"] += 1
                self._derniere_erreur = self._moteur()[1]
            return False

        try:
            processus = self._lanceur(commande)
        except Exception as exc:
            with self._verrou:
                self._compteurs["echecs"] += 1
                self._derniere_erreur = f"{type(exc).__name__}: {exc}"
            return False
        if processus is None:
            with self._verrou:
                self._compteurs["echecs"] += 1
                self._derniere_erreur = "synthèse vocale impossible à lancer"
            return False

        with self._verrou:
            self._processus = processus
            self._derniers[annonce.cle] = float(self._horloge())
            self._derniere_annonce = texte
            self._compteurs["enoncees"] += 1
        try:
            attendre = getattr(processus, "wait", None)
            if callable(attendre):
                try:
                    attendre(timeout=DELAI_ENONCE_MAX)
                except TypeError:
                    attendre()
        except subprocess.TimeoutExpired:
            self._tuer_processus()
        except Exception as exc:
            with self._verrou:
                self._derniere_erreur = f"{type(exc).__name__}: {exc}"
        finally:
            with self._verrou:
                if self._processus is processus:
                    self._processus = None
        return True

    def arreter(self) -> None:
        """Arrête le fil de travail et se tait. Idempotent.

        Utile à la fermeture de l'application : un fil démon disparaîtrait
        avec le processus, mais l'arrêt explicite garantit qu'aucune phrase
        ne survit à la fenêtre qu'on vient de fermer.
        """
        self._arret.set()
        self.taire()
        with self._verrou:
            fil = self._fil
            self._fil = None
        if fil is not None and fil.is_alive():
            fil.join(timeout=5)


# ── Validation des réglages ────────────────────────────────────────────────
def _valider_reglage(champ: str, valeur: Any) -> Any:
    """Contrôle et normalise UN réglage. Lève `ValueError` si c'est illisible.

    Les bornes ne sont pas décoratives : `Rate` hors de -10..10 fait échouer
    l'appel SAPI tout entier, et un volume négatif passé à `espeak-ng` est
    lu comme une option. Un réglage invalide accepté ici deviendrait une voix
    définitivement muette, pour une raison invisible côté interface.
    """
    if champ == "actif":
        if isinstance(valeur, str):
            return valeur.strip().lower() in ("1", "true", "vrai", "oui", "on")
        return bool(valeur)
    if champ == "debit":
        return max(-10, min(10, int(_nombre(valeur, "debit"))))
    if champ == "volume":
        return max(0, min(100, int(_nombre(valeur, "volume"))))
    if champ == "delai_repetition":
        return max(0.0, min(86400.0, float(_nombre(valeur, "delai_repetition"))))
    if champ == "delai_groupement":
        return max(0.0, min(10.0, float(_nombre(valeur, "delai_groupement"))))
    if champ == "verbosite":
        texte = str(valeur or "").strip().lower()
        if texte not in VERBOSITES:
            raise ValueError(
                f"verbosite inconnue : {valeur!r} (attendu {', '.join(VERBOSITES)})")
        return texte
    if champ == "voix":
        return nettoyer_pour_voix(str(valeur or ""))[:120]
    if champ == "heures_silence":
        return _normaliser_plage(valeur)
    raise ValueError(f"réglage inconnu : {champ}")       # pragma: no cover


def _nombre(valeur: Any, champ: str) -> float:
    try:
        return float(valeur)
    except (TypeError, ValueError):
        raise ValueError(f"{champ} attend un nombre, reçu {valeur!r}") from None


# ── Accès système, isolés pour rester injectables ──────────────────────────
def _lancer_processus(commande: Sequence[str]):
    """Démarre l'énonciation sans jamais lever, et sans `shell=True`.

    `Popen` et non `subprocess.run` : il faut garder une prise sur le
    processus pour pouvoir le tuer au milieu d'un mot. Les trois flux sont
    détournés vers le néant — une voix n'a rien à écrire sur la sortie
    standard, et un tube plein finirait par la bloquer.
    """
    try:
        return subprocess.Popen(
            list(commande), shell=False,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
    except (OSError, ValueError):
        return None


def _nom_de_session() -> str:
    """Nom de l'utilisateur courant, pour pouvoir NE PAS le prononcer."""
    for cle in ("USERNAME", "USER", "LOGNAME"):
        valeur = os.environ.get(cle)
        if valeur and valeur.strip():
            return valeur.strip()
    try:
        import getpass
        return getpass.getuser()
    except Exception:
        return ""


def _nom_de_machine() -> str:
    try:
        return (platform.node() or "").split(".")[0].strip()
    except Exception:                                    # pragma: no cover
        return ""
