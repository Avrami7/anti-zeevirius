"""
incident_mode.py — LE BOUTON D'URGENCE.

L'utilisateur pense être infecté MAINTENANT : des fichiers changent
d'extension sous ses yeux, une demande de rançon s'affiche, ou il vient de
comprendre que la pièce jointe qu'il a ouverte n'en était pas une. Un seul
geste doit couper la propagation et préserver les preuves ; un seul geste
doit tout remettre en place.

Séquence (docs/CONCEPTION-V2.md §2.6, didacticiel n°6) :

    1. Couper le réseau      règle de pare-feu étiquetée AZ_INCIDENT
    2. Geler les processus   suspend, JAMAIS kill
    3. Sauvegarde rapide     cliché instantané VSS des dossiers personnels
    4. Rapport horodaté      gelés, connexions, fichiers modifiés

    retablir()               retire la règle, relance les processus gelés

Quatre décisions de conception méritent d'être justifiées, parce qu'elles
ne sont pas les plus évidentes :

**Règle de pare-feu, pas désactivation de la carte réseau.** Désactiver
l'interface serait plus radical, mais le retour en arrière dépendrait alors
du pilote réseau (et d'un `netsh interface set interface ... enable` qui peut
échouer, ou d'un adaptateur qui ne revient qu'au redémarrage). Une règle
nommée se retire en une commande, et si l'application meurt entre-temps,
n'importe qui peut la retirer à la main :

    netsh advfirewall firewall delete rule name=AZ_INCIDENT

Cette commande est imprimée dans le rapport, précisément pour ça. À noter :
le pare-feu Windows ne filtre pas la boucle locale — l'interface web locale
d'ANTI-ZEEVIRIUS continue de fonctionner réseau coupé.

**Gel, jamais arrêt.** Un processus tué perd sa mémoire, donc les preuves —
et, pour plusieurs familles de rançongiciels, la clé de chiffrement encore
résidente. Certaines réagissent en outre à leur propre arrêt (relance depuis
un compagnon, destruction accélérée). Un processus gelé, lui, s'examine.

**État persistant.** Si l'application est fermée ou plante en mode incident,
la règle de pare-feu et les processus gelés SURVIVENT — c'est le but, mais
un utilisateur qui se retrouve sans réseau sans savoir pourquoi est un échec
grave. L'état est donc écrit sur disque (via `paths.data_path()`, jamais
`Path(__file__)` : voir l'en-tête de paths.py) dès que la règle est posée,
et relu au lancement suivant par `etat()`. Il est réécrit après le gel, avant
la sauvegarde VSS — l'étape lente : une panne pendant le cliché laisse
malgré tout une trace exploitable de tout ce qui a été fait.

**Liste noire stricte.** Geler `lsass`, `csrss` ou `smss` provoque un écran
bleu ; geler `services` ou `winlogon` fige la session. Ces processus ne sont
jamais candidats, quel que soit leur débit d'écriture disque. Le processus
courant et ses ancêtres non plus — se geler soi-même, ou geler la console
qui nous a lancés, rendrait le rétablissement impossible.

Ce module N'INVENTE AUCUN MÉCANISME NOUVEAU de gel : il réutilise
`RansomwareShield.suspend_process()`, `resume_process()` et
`find_suspicious_processes()` (optimizer/ransomware_shield.py). Un second
mécanisme de suspension serait un second endroit où se tromper.

Limites annoncées à l'utilisateur (didacticiel n°6), et donc tenues ici :
- un chiffrement déjà terminé n'est pas annulé — on limite les dégâts ;
- pare-feu et VSS exigent les droits administrateur ;
- un logiciel malveillant plus privilégié que l'outil peut résister au gel ;
- hors Windows, chaque étape se déclare indisponible sans faire échouer
  les autres (le gel de processus, lui, fonctionne partout où psutil
  fonctionne).
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

import paths

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:  # pragma: no cover - dépend de l'installation
    psutil = None  # type: ignore[assignment]
    PSUTIL_AVAILABLE = False

try:
    from optimizer.ransomware_shield import RansomwareShield
    SHIELD_AVAILABLE = True
except ImportError:  # pragma: no cover - le paquet optimizer doit être là
    RansomwareShield = None  # type: ignore[assignment]
    SHIELD_AVAILABLE = False


# ── Constantes ──────────────────────────────────────────────────────────────

#: Étiquette unique de la règle de pare-feu. Les deux sens (entrant/sortant)
#: portent le MÊME nom : netsh accepte l'homonymie et `delete rule name=...`
#: les retire toutes les deux d'un coup. Un seul nom à connaître pour
#: l'utilisateur qui voudrait défaire le mode à la main.
NOM_REGLE = "AZ_INCIDENT"

DESCRIPTION_REGLE = (
    "ANTI-ZEEVIRIUS - Mode Incident. Coupure reseau temporaire et reversible. "
    "Retrait : netsh advfirewall firewall delete rule name=AZ_INCIDENT"
)

ETAT_VERSION = 1

#: Nombre de processus proposés au gel par défaut. On reste volontairement bas :
#: le plan est lu par un humain paniqué, une liste de trente lignes ne se lit pas.
NB_PROCESSUS_CANDIDATS = 5

#: Fenêtre d'observation des fichiers récemment modifiés, pour le rapport.
FENETRE_FICHIERS_MINUTES = 15
MAX_FICHIERS_RAPPORT = 500
MAX_CONNEXIONS_RAPPORT = 200
PROFONDEUR_MAX_PARCOURS = 4

#: Tolérance sur la date de création d'un processus, pour vérifier qu'un PID
#: désigne toujours le même processus. Les PID sont recyclés — dégeler à
#: l'aveugle un PID noté dix minutes plus tôt, c'est risquer de toucher un
#: processus innocent qui a hérité du numéro.
TOLERANCE_CREATE_TIME = 1.0

#: JAMAIS gelés : les geler plante Windows (écran bleu) ou détruit la session.
#: Cette liste n'est pas une précaution de confort, c'est une barrière de
#: sûreté. `lsass` et `csrss` sont les deux cas les plus cités : le noyau
#: considère leur non-réponse comme une faute critique du système.
PROCESSUS_CRITIQUES = frozenset({
    "system", "system idle process", "registry", "memory compression",
    "smss", "csrss", "wininit", "winlogon", "services", "lsass", "lsaiso",
    "ntoskrnl", "svchost", "init", "systemd",
})

#: Jamais gelés non plus, pour une raison différente : ils ne font pas planter
#: la machine, ils la rendent inutilisable. Geler `explorer` fige le bureau et
#: la barre des tâches — l'utilisateur ne peut plus cliquer sur « Rétablir ».
#: (Point signalé comme « à discuter » : la discussion est tranchée ici du côté
#: prudent, et la constante est isolée pour qu'on puisse revenir dessus sans
#: toucher à la barrière de sûreté ci-dessus.)
PROCESSUS_SENSIBLES = frozenset({
    "explorer", "dwm", "fontdrvhost", "sihost", "ctfmon", "taskhostw",
    "logonui", "userinit", "audiodg",
})

#: Dossiers personnels sauvegardés. Les deux jeux de noms cohabitent : un
#: Windows français affiche « Bureau » et « Images », un Windows anglais
#: « Desktop » et « Pictures ». On garde ceux qui existent réellement.
DOSSIERS_PERSONNELS = (
    "Desktop", "Bureau",
    "Documents",
    "Pictures", "Images",
    "Downloads", "Téléchargements", "Telechargements",
)

CONSEILS = (
    "NE REDÉMARRE PAS : pour plusieurs familles de rançongiciels, la clé de "
    "chiffrement est encore en mémoire vive tant que la machine reste allumée. "
    "Un redémarrage la perd définitivement.",
    "Examine la liste des fichiers modifiés ci-dessous avant toute "
    "manipulation : c'est la mesure des dégâts.",
    "Les processus sont GELÉS, pas arrêtés. Ne les tue pas : ils contiennent "
    "les preuves.",
    "Sortie du mode : bouton « Rétablir » (ou appel de retablir()). Le réseau "
    "revient et les processus repartent.",
    "Si l'application ne répond plus, la coupure réseau se retire à la main : "
    "netsh advfirewall firewall delete rule name=" + NOM_REGLE,
)


def _indisponible(raison: str, **extra: Any) -> Dict[str, Any]:
    """Réponse normalisée d'une étape qui ne peut pas s'exécuter ici.

    Contrat du projet (CONCEPTION-V2.md §1, règle 3) : une étape
    indisponible ne lève pas, elle se déclare — et les autres continuent.
    Une sauvegarde VSS impossible ne doit pas empêcher la coupure réseau.
    """
    reponse: Dict[str, Any] = {"ok": False, "unavailable": True, "reason": raison}
    reponse.update(extra)
    return reponse


def _echec(raison: str, **extra: Any) -> Dict[str, Any]:
    """Étape disponible mais qui a échoué : distinct de `_indisponible`.

    « Je ne peux pas faire ça sur cette machine » et « j'ai essayé et ça a
    raté » demandent deux réactions différentes de l'interface.
    """
    reponse: Dict[str, Any] = {"ok": False, "unavailable": False, "reason": raison}
    reponse.update(extra)
    return reponse


def _horodatage(t: Optional[float] = None) -> str:
    """Horodatage local lisible et triable (l'utilisateur lit une heure de
    montre, pas un compte de secondes depuis 1970)."""
    return datetime.fromtimestamp(time.time() if t is None else t).isoformat(timespec="seconds")


def _nom_normalise(nom: Optional[str]) -> str:
    """« Explorer.EXE » et « explorer » désignent le même processus."""
    if not nom:
        return ""
    n = nom.strip().lower()
    if n.endswith(".exe"):
        n = n[:-4]
    return n


class IncidentMode:
    """Mode Incident : activation, état persistant, rétablissement.

    Toutes les dépendances au monde extérieur sont injectables — c'est ce qui
    rend le module testable sous Linux alors que sa cible est Windows :

    - `runner`      remplace l'exécution de netsh / vssadmin / powershell
    - `etat_path`   déplace le fichier d'état (par défaut paths.data_path())
    - `shield`      remplace RansomwareShield (gel/dégel/candidats)
    - `plateforme`  force la plateforme vue par le module
    - `admin`       force le verdict « droits administrateur »
    """

    def __init__(
        self,
        runner: Optional[Callable[..., Any]] = None,
        etat_path: Optional[Path] = None,
        dossier_rapports: Optional[Path] = None,
        shield: Any = None,
        plateforme: Optional[str] = None,
        admin: Optional[bool] = None,
    ) -> None:
        self._runner = runner or self._executer_reellement
        self._etat_path = Path(etat_path) if etat_path else None
        self._dossier_rapports = Path(dossier_rapports) if dossier_rapports else None
        self._shield = shield if shield is not None else RansomwareShield
        self._plateforme = plateforme
        self._admin = admin

    # ── Emplacements ────────────────────────────────────────────────────────
    # Résolus à chaque appel et non au constructeur : paths.data_path() lit
    # ANTIZEEVIRIUS_DATA_DIR, qui peut changer entre deux appels (installation
    # portable, test isolé).

    @property
    def dossier(self) -> Path:
        if self._dossier_rapports is not None:
            return self._dossier_rapports
        return Path(paths.data_path("incident"))

    @property
    def etat_path(self) -> Path:
        if self._etat_path is not None:
            return self._etat_path
        return self.dossier / "incident_state.json"

    # ── Environnement ───────────────────────────────────────────────────────

    def plateforme(self) -> str:
        return self._plateforme if self._plateforme is not None else platform.system()

    def est_windows(self) -> bool:
        return self.plateforme() == "Windows"

    def est_admin(self) -> bool:
        if self._admin is not None:
            return self._admin
        if self.est_windows():
            try:
                import ctypes
                return ctypes.windll.shell32.IsUserAnAdmin() != 0  # type: ignore[attr-defined]
            except Exception:
                return False
        try:
            return os.geteuid() == 0  # type: ignore[attr-defined]
        except AttributeError:
            return False

    def _prerequis_systeme(self, quoi: str) -> Optional[Dict[str, Any]]:
        """Vérifie ce que réclament les étapes pare-feu et VSS.

        Retourne None si tout va bien, sinon la réponse `unavailable` à
        renvoyer telle quelle par l'étape appelante.
        """
        if not self.est_windows():
            return _indisponible(
                f"{quoi} : disponible uniquement sous Windows "
                f"(plateforme détectée : {self.plateforme()})."
            )
        if not self.est_admin():
            return _indisponible(
                f"{quoi} : droits administrateur requis. Relance "
                "ANTI-ZEEVIRIUS en tant qu'administrateur."
            )
        return None

    # ── Exécution de commandes ──────────────────────────────────────────────

    @staticmethod
    def _executer_reellement(commande: Sequence[str], timeout: int = 30) -> subprocess.CompletedProcess:
        """Exécution réelle, sans shell (liste d'arguments) — même règle que
        `ransomware_shield.lock_folder_readonly` : pas d'interpolation dans une
        ligne de commande, donc pas d'injection possible par un nom de dossier."""
        return subprocess.run(
            list(commande), capture_output=True, text=True,
            timeout=timeout, check=False,
        )

    def _run(self, commande: Sequence[str], timeout: int = 30) -> subprocess.CompletedProcess:
        """Lance une commande et ne lève jamais : un exécutable absent
        (netsh sous Linux) ou un dépassement de délai devient un code de
        retour -1, que l'appelant traite comme un échec ordinaire."""
        try:
            return self._runner(list(commande), timeout=timeout)
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(
                list(commande), -1, "", f"délai dépassé ({timeout}s)")
        except (OSError, subprocess.SubprocessError) as e:
            return subprocess.CompletedProcess(list(commande), -1, "", str(e))

    @staticmethod
    def _sortie(res: subprocess.CompletedProcess) -> str:
        return ((res.stdout or "") + (res.stderr or "")).strip()

    # ── État persistant ─────────────────────────────────────────────────────

    def lire_etat(self) -> Dict[str, Any]:
        """Relit l'état du mode incident. Ne lève jamais.

        Un fichier illisible est signalé (`corrompu`) mais n'est pas traité
        comme « pas de mode incident » : `retablir()` tentera quand même de
        retirer la règle de pare-feu, qui est peut-être bien posée.
        """
        chemin = self.etat_path
        try:
            if not chemin.is_file():
                return {"actif": False, "existe": False}
            donnees = json.loads(chemin.read_text(encoding="utf-8"))
            if not isinstance(donnees, dict):
                raise ValueError("structure inattendue")
            donnees["existe"] = True
            donnees.setdefault("actif", False)
            return donnees
        except (OSError, ValueError) as e:
            return {"actif": False, "existe": True, "corrompu": True, "reason": str(e)}

    def _ecrire_etat(self, etat: Dict[str, Any]) -> bool:
        """Écriture atomique : fichier temporaire puis `os.replace`.

        Sans ça, une panne pendant l'écriture laisserait un JSON tronqué —
        c'est-à-dire un mode incident actif sans trace lisible, exactement
        la situation qu'on veut éviter.
        """
        chemin = self.etat_path
        try:
            chemin.parent.mkdir(parents=True, exist_ok=True)
            tmp = chemin.with_name(chemin.name + ".tmp")
            tmp.write_text(json.dumps(etat, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, chemin)
            return True
        except OSError:
            return False

    # ── Sélection des processus ─────────────────────────────────────────────

    def _pids_proteges(self) -> Dict[int, str]:
        """PID à ne jamais geler, indépendamment de leur nom.

        Le processus courant (on se figerait soi-même, plus personne pour
        rétablir), ses ancêtres (la console ou le service qui nous a lancés),
        et les PID de base du système.
        """
        proteges: Dict[int, str] = {
            os.getpid(): "processus courant (ANTI-ZEEVIRIUS)",
        }
        if os.name == "nt":
            proteges[0] = "System Idle Process"
            proteges[4] = "System"
        else:
            proteges[1] = "init/systemd"
        if PSUTIL_AVAILABLE:
            try:
                courant = psutil.Process(os.getpid())
                for ancetre in courant.parents():
                    proteges.setdefault(ancetre.pid, "ancêtre du processus courant")
            except Exception:
                pass
        return proteges

    def raison_protection(self, pid: int, nom: Optional[str]) -> Optional[str]:
        """Retourne pourquoi ce processus ne doit pas être gelé, ou None.

        Point critique du module : c'est ici que se joue la différence entre
        « le rançongiciel est gelé » et « écran bleu ».
        """
        proteges = self._pids_proteges()
        if pid in proteges:
            return proteges[pid]
        if pid is None or pid < 0:
            return "PID invalide"
        n = _nom_normalise(nom)
        if not n:
            return "nom de processus inconnu — trop risqué pour un gel"
        if n in PROCESSUS_CRITIQUES:
            return f"processus critique de Windows ({nom}) : le geler plante le système"
        if n in PROCESSUS_SENSIBLES:
            return f"processus de session ({nom}) : le geler rendrait le bureau inutilisable"
        return None

    def candidats(self, top_n: int = NB_PROCESSUS_CANDIDATS) -> Dict[str, List[Dict[str, Any]]]:
        """Candidats au gel, via `RansomwareShield.find_suspicious_processes()`.

        On ne réimplémente pas la recherche : le bouclier classe déjà les
        processus par volume écrit sur disque, ce qui est exactement le signal
        recherché pendant un chiffrement en cours.

        Retourne les retenus ET les écartés : l'utilisateur doit voir que
        `lsass.exe` a été vu et volontairement laissé tranquille, sinon il
        croira l'outil aveugle.
        """
        retenus: List[Dict[str, Any]] = []
        exclus: List[Dict[str, Any]] = []
        if not (PSUTIL_AVAILABLE and SHIELD_AVAILABLE and self._shield is not None):
            return {"retenus": retenus, "exclus": exclus}
        try:
            # On demande large puis on filtre : sinon la liste noire mange
            # les places du top_n et on ne propose plus rien.
            bruts = self._shield.find_suspicious_processes(top_n=max(top_n * 4, top_n))
        except Exception:
            bruts = []
        for proc in bruts or []:
            pid = proc.get("pid")
            nom = proc.get("name")
            raison = self.raison_protection(pid, nom)
            entree = {
                "pid": pid,
                "nom": nom,
                "octets_ecrits": proc.get("write_bytes"),
            }
            if raison:
                exclus.append({**entree, "raison": raison})
                continue
            entree["create_time"] = self._create_time(pid)
            entree["motif"] = "parmi les processus qui écrivent le plus sur disque"
            retenus.append(entree)
            if len(retenus) >= top_n:
                break
        return {"retenus": retenus, "exclus": exclus}

    @staticmethod
    def _create_time(pid: Optional[int]) -> Optional[float]:
        """Date de création : l'empreinte qui distingue ce processus d'un
        futur homonyme ayant récupéré le même PID."""
        if not PSUTIL_AVAILABLE or pid is None:
            return None
        try:
            return psutil.Process(pid).create_time()
        except Exception:
            return None

    # ── Dossiers personnels ─────────────────────────────────────────────────

    @staticmethod
    def dossiers_personnels() -> List[str]:
        home = Path.home()
        trouves: List[str] = []
        for nom in DOSSIERS_PERSONNELS:
            candidat = home / nom
            try:
                if candidat.is_dir():
                    trouves.append(str(candidat))
            except OSError:
                continue
        return trouves

    @staticmethod
    def _lecteurs(dossiers: Sequence[str]) -> List[str]:
        """Lettres de lecteur concernées : VSS travaille par volume, pas par
        dossier. Trois dossiers sur C: = un seul cliché."""
        lecteurs: List[str] = []
        for d in dossiers:
            lecteur = os.path.splitdrive(str(d))[0]
            if lecteur and lecteur not in lecteurs:
                lecteurs.append(lecteur)
        return lecteurs

    # ── Étape 1 : couper le réseau ──────────────────────────────────────────

    def _commandes_regle(self) -> List[List[str]]:
        """Deux règles homonymes : sortant (exfiltration, chiffrement piloté à
        distance, propagation) et entrant (retour du pilote, latéralisation).

        `action=block` l'emporte sur les règles d'autorisation existantes dans
        le pare-feu Windows : c'est ce qui permet de tout couper sans toucher
        aux règles de l'utilisateur, qui restent intactes pour le retour.
        """
        base = ["netsh", "advfirewall", "firewall", "add", "rule",
                f"name={NOM_REGLE}", "enable=yes", "profile=any",
                "protocol=any", "remoteip=any", "action=block",
                f"description={DESCRIPTION_REGLE}"]
        return [base + ["dir=out"], base + ["dir=in"]]

    def regle_presente(self) -> Optional[bool]:
        """True / False / None quand la question n'a pas pu être posée."""
        res = self._run(["netsh", "advfirewall", "firewall", "show", "rule",
                         f"name={NOM_REGLE}"], timeout=20)
        if res.returncode == 0 and NOM_REGLE.lower() in self._sortie(res).lower():
            return True
        sortie = self._sortie(res).lower()
        if res.returncode != 0 and ("no rules match" in sortie or "aucune règle" in sortie
                                    or "aucune regle" in sortie or res.returncode == 1):
            return False
        if res.returncode == -1:
            return None
        return False

    def couper_reseau(self) -> Dict[str, Any]:
        """Étape 1. Idempotente : si la règle est déjà là, on ne la repose pas.

        Reposer la règle créerait un doublon dans le pare-feu — inoffensif au
        blocage, mais qui survivrait au premier `delete rule` et laisserait
        l'utilisateur sans réseau après un rétablissement annoncé réussi.
        """
        manque = self._prerequis_systeme("Coupure réseau (pare-feu)")
        if manque:
            return {**manque, "regle": NOM_REGLE}

        presente = self.regle_presente()
        if presente is True:
            return {"ok": True, "regle": NOM_REGLE, "deja_presente": True,
                    "message": f"La règle {NOM_REGLE} était déjà en place."}

        posees, erreurs = [], []
        for commande in self._commandes_regle():
            res = self._run(commande, timeout=20)
            if res.returncode == 0:
                posees.append(" ".join(commande))
            else:
                erreurs.append(self._sortie(res) or f"code {res.returncode}")
        if not posees:
            return _echec("La règle de pare-feu n'a pas pu être posée : "
                          + " / ".join(erreurs), regle=NOM_REGLE)
        return {"ok": True, "regle": NOM_REGLE, "deja_presente": False,
                "regles_posees": len(posees), "commandes": posees,
                "erreurs": erreurs,
                "retrait_manuel": f"netsh advfirewall firewall delete rule name={NOM_REGLE}"}

    def retablir_reseau(self) -> Dict[str, Any]:
        """Retire la règle. Idempotente : une règle déjà absente est un succès,
        pas une erreur — c'est l'état recherché."""
        manque = self._prerequis_systeme("Rétablissement réseau (pare-feu)")
        if manque:
            return {**manque, "regle": NOM_REGLE}
        res = self._run(["netsh", "advfirewall", "firewall", "delete", "rule",
                         f"name={NOM_REGLE}"], timeout=20)
        sortie = self._sortie(res)
        if res.returncode == 0:
            return {"ok": True, "regle": NOM_REGLE, "retiree": True, "sortie": sortie}
        if "no rules match" in sortie.lower() or "aucune r" in sortie.lower():
            return {"ok": True, "regle": NOM_REGLE, "retiree": False,
                    "deja_absente": True, "sortie": sortie}
        return _echec(f"Retrait de la règle {NOM_REGLE} impossible : "
                      f"{sortie or 'code ' + str(res.returncode)}", regle=NOM_REGLE)

    # ── Étape 2 : geler les processus ───────────────────────────────────────

    def geler_processus(self, candidats: Sequence[Dict[str, Any]],
                        deja_geles: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
        """Étape 2. Gel via `RansomwareShield.suspend_process()`.

        Trois vérifications avant chaque gel, dans cet ordre :
        1. la liste noire (revérifiée ici, même si `preparer()` l'a déjà
           appliquée : le plan vient peut-être de l'extérieur, édité par
           l'interface ou par un appel direct à l'API) ;
        2. le PID existe toujours ;
        3. le processus derrière ce PID est bien celui du plan (nom et date de
           création) — entre `preparer()` et `activer()`, un PID a pu être
           recyclé.
        """
        if not PSUTIL_AVAILABLE:
            return _indisponible("Gel de processus : psutil n'est pas installé.",
                                 geles=[], ignores=[])
        if not (SHIELD_AVAILABLE and self._shield is not None):
            return _indisponible("Gel de processus : optimizer.ransomware_shield "
                                 "est introuvable.", geles=[], ignores=[])

        pids_connus = {p.get("pid") for p in deja_geles or ()}
        geles: List[Dict[str, Any]] = []
        ignores: List[Dict[str, Any]] = []

        for cand in candidats or ():
            pid = cand.get("pid")
            nom_attendu = cand.get("nom") or cand.get("name")
            entree = {"pid": pid, "nom": nom_attendu}

            raison = self.raison_protection(pid, nom_attendu)
            if raison:
                ignores.append({**entree, "raison": raison, "protege": True})
                continue
            if pid in pids_connus:
                ignores.append({**entree, "raison": "déjà gelé par le mode incident"})
                continue

            try:
                proc = psutil.Process(pid)
                nom_reel = proc.name()
                create_time = proc.create_time()
                statut = proc.status()
            except Exception as e:
                ignores.append({**entree, "raison": f"processus introuvable ({e.__class__.__name__})"})
                continue

            # Le nom réel repasse par la liste noire : un plan qui annonce
            # « facture.exe » pour un PID devenu celui de lsass ne doit pas
            # passer au travers.
            raison_reelle = self.raison_protection(pid, nom_reel)
            if raison_reelle:
                ignores.append({**entree, "nom_reel": nom_reel,
                                "raison": raison_reelle, "protege": True})
                continue
            if nom_attendu and _nom_normalise(nom_attendu) != _nom_normalise(nom_reel):
                ignores.append({**entree, "nom_reel": nom_reel,
                                "raison": "PID recyclé : ce n'est plus le processus du plan"})
                continue
            attendu_ct = cand.get("create_time")
            if attendu_ct is not None and abs(float(attendu_ct) - create_time) > TOLERANCE_CREATE_TIME:
                ignores.append({**entree, "raison": "PID recyclé : date de création différente"})
                continue
            if statut == getattr(psutil, "STATUS_STOPPED", "stopped"):
                # Gelé par quelqu'un d'autre : on n'y touche pas, sinon on le
                # relancerait au rétablissement alors qu'on ne l'a pas gelé.
                ignores.append({**entree, "raison": "déjà suspendu (pas par nous)"})
                continue

            try:
                succes = bool(self._shield.suspend_process(pid))
            except Exception as e:
                succes = False
                entree["erreur"] = str(e)
            if succes:
                geles.append({"pid": pid, "nom": nom_reel, "create_time": create_time,
                              "gele_le": _horodatage(), "octets_ecrits": cand.get("octets_ecrits")})
            else:
                ignores.append({**entree, "nom_reel": nom_reel,
                                "raison": "gel refusé (droits insuffisants ou processus "
                                          "plus privilégié que l'outil)"})

        return {"ok": True, "geles": geles, "ignores": ignores,
                "nb_geles": len(geles), "nb_ignores": len(ignores)}

    def degeler_processus(self, geles: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        """Relance les processus gelés. Retourne aussi ceux qu'il n'a PAS pu
        relancer : l'appelant en a besoin pour décider s'il peut effacer
        l'état ou s'il doit le conserver."""
        if not PSUTIL_AVAILABLE:
            return _indisponible("Dégel : psutil n'est pas installé.",
                                 relances=[], restants=list(geles or ()))
        if not (SHIELD_AVAILABLE and self._shield is not None):
            return _indisponible("Dégel : optimizer.ransomware_shield est introuvable.",
                                 relances=[], restants=list(geles or ()))

        relances: List[Dict[str, Any]] = []
        disparus: List[Dict[str, Any]] = []
        restants: List[Dict[str, Any]] = []

        for entree in geles or ():
            pid = entree.get("pid")
            try:
                proc = psutil.Process(pid)
                nom_reel = proc.name()
                create_time = proc.create_time()
            except Exception:
                # Le processus est mort pendant le gel (ou a été tué) : rien à
                # relancer, et surtout rien à conserver dans l'état.
                disparus.append({**entree, "raison": "processus disparu — rien à relancer"})
                continue

            attendu_ct = entree.get("create_time")
            recycle = (
                (attendu_ct is not None and abs(float(attendu_ct) - create_time) > TOLERANCE_CREATE_TIME)
                or (entree.get("nom") and _nom_normalise(entree["nom"]) != _nom_normalise(nom_reel))
            )
            if recycle:
                # Ce PID appartient maintenant à quelqu'un d'autre : le
                # « dégeler » toucherait un processus innocent.
                disparus.append({**entree, "nom_reel": nom_reel,
                                 "raison": "PID recyclé — laissé intact"})
                continue

            try:
                succes = bool(self._shield.resume_process(pid))
            except Exception:
                succes = False
            if succes:
                relances.append({**entree, "relance_le": _horodatage()})
            else:
                restants.append({**entree, "raison": "relance refusée (droits insuffisants)"})

        return {"ok": not restants, "relances": relances, "disparus": disparus,
                "restants": restants, "nb_relances": len(relances)}

    # ── Étape 3 : sauvegarde rapide (cliché VSS) ────────────────────────────

    def sauvegarder(self, dossiers: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        """Étape 3. Cliché instantané des volumes portant les dossiers personnels.

        Deux méthodes, dans cet ordre :

        1. `vssadmin create shadow /for=C:` — la commande documentée en §2.6…
           mais qui, sur les éditions CLIENT de Windows (10/11 Famille et Pro),
           n'existe pas : `vssadmin` y sait lister et supprimer, pas créer.
        2. Repli : `Win32_ShadowCopy.Create` via PowerShell, qui fonctionne, lui,
           sur les éditions client.

        La méthode réellement employée est reportée dans le rapport. C'est le
        genre de détail qu'un outil honnête affiche plutôt que d'annoncer
        « sauvegarde effectuée » quand rien n'a été créé.
        """
        dossiers = list(dossiers) if dossiers is not None else self.dossiers_personnels()
        manque = self._prerequis_systeme("Cliché instantané (VSS)")
        if manque:
            return {**manque, "dossiers": dossiers, "cliches": []}
        lecteurs = self._lecteurs(dossiers)
        if not lecteurs:
            return _echec("Aucun volume identifié pour les dossiers personnels.",
                          dossiers=dossiers, cliches=[])

        cliches: List[Dict[str, Any]] = []
        for lecteur in lecteurs:
            res = self._run(["vssadmin", "create", "shadow", f"/for={lecteur}"], timeout=300)
            if res.returncode == 0:
                cliches.append({"lecteur": lecteur, "ok": True, "methode": "vssadmin",
                                "sortie": self._sortie(res)})
                continue
            premier_echec = self._sortie(res) or f"code {res.returncode}"
            res2 = self._run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 f"(Get-WmiObject -List Win32_ShadowCopy).Create('{lecteur}\\','ClientAccessible')"],
                timeout=300)
            if res2.returncode == 0:
                cliches.append({"lecteur": lecteur, "ok": True, "methode": "Win32_ShadowCopy",
                                "sortie": self._sortie(res2),
                                "note": "vssadmin a refusé (édition client de Windows) — "
                                        "repli WMI utilisé."})
            else:
                cliches.append({"lecteur": lecteur, "ok": False, "methode": None,
                                "erreur": premier_echec,
                                "erreur_repli": self._sortie(res2) or f"code {res2.returncode}"})

        reussis = [c for c in cliches if c["ok"]]
        if not reussis:
            return _echec("Aucun cliché instantané n'a pu être créé.",
                          dossiers=dossiers, cliches=cliches)
        return {"ok": True, "dossiers": dossiers, "cliches": cliches,
                "nb_cliches": len(reussis), "partiel": len(reussis) != len(cliches)}

    # ── Étape 4 : rapport horodaté ──────────────────────────────────────────

    def connexions_actives(self) -> Dict[str, Any]:
        """Photographie des connexions au moment de l'incident.

        Prise APRÈS la coupure : les connexions établies avant restent visibles
        dans la table tant que les sockets ne sont pas fermées — c'est
        justement la trace qu'on veut conserver. Aucune interception : on lit
        la table, on ne s'insère pas dans le flux (didacticiel n°5).
        """
        if not PSUTIL_AVAILABLE:
            return _indisponible("Connexions : psutil n'est pas installé.", connexions=[])
        try:
            brutes = psutil.net_connections(kind="inet")
        except Exception as e:
            return _indisponible(f"Connexions illisibles ({e.__class__.__name__}) : "
                                 "droits insuffisants.", connexions=[])
        connexions: List[Dict[str, Any]] = []
        for c in brutes[:MAX_CONNEXIONS_RAPPORT]:
            nom = None
            if c.pid:
                try:
                    nom = psutil.Process(c.pid).name()
                except Exception:
                    nom = None
            connexions.append({
                "pid": c.pid, "processus": nom,
                "local": f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else None,
                "distant": f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else None,
                "statut": c.status,
            })
        etablies = [c for c in connexions if c["distant"]]
        return {"ok": True, "connexions": connexions, "nb": len(connexions),
                "nb_etablies": len(etablies),
                "tronque": len(brutes) > MAX_CONNEXIONS_RAPPORT}

    def fichiers_recents(self, dossiers: Optional[Sequence[str]] = None,
                         minutes: int = FENETRE_FICHIERS_MINUTES) -> Dict[str, Any]:
        """Fichiers modifiés dans les N dernières minutes — la mesure des dégâts.

        Parcours borné en profondeur et en nombre : ce rapport est produit
        pendant une urgence, il doit sortir en secondes. Un parcours complet
        d'un disque personnel prendrait des minutes pendant lesquelles le
        chiffrement continue.
        """
        dossiers = list(dossiers) if dossiers is not None else self.dossiers_personnels()
        limite = time.time() - minutes * 60
        trouves: List[Dict[str, Any]] = []
        tronque = False

        def parcourir(racine: Path, profondeur: int) -> None:
            nonlocal tronque
            if profondeur > PROFONDEUR_MAX_PARCOURS or len(trouves) >= MAX_FICHIERS_RAPPORT:
                tronque = tronque or len(trouves) >= MAX_FICHIERS_RAPPORT
                return
            try:
                with os.scandir(racine) as it:
                    for entree in it:
                        if len(trouves) >= MAX_FICHIERS_RAPPORT:
                            tronque = True
                            return
                        try:
                            if entree.is_dir(follow_symlinks=False):
                                parcourir(Path(entree.path), profondeur + 1)
                            elif entree.is_file(follow_symlinks=False):
                                st = entree.stat()
                                if st.st_mtime >= limite:
                                    trouves.append({
                                        "chemin": entree.path,
                                        "modifie_le": _horodatage(st.st_mtime),
                                        "taille": st.st_size,
                                    })
                        except OSError:
                            continue
            except OSError:
                return

        for d in dossiers:
            parcourir(Path(d), 0)
        trouves.sort(key=lambda f: f["modifie_le"], reverse=True)
        return {"ok": True, "fichiers": trouves, "nb": len(trouves),
                "fenetre_minutes": minutes, "dossiers": dossiers, "tronque": tronque}

    # ── Plan (lecture seule) ────────────────────────────────────────────────

    def preparer(self) -> Dict[str, Any]:
        """Retourne EXACTEMENT ce que fera `activer()`. Ne modifie rien.

        Règle 1 du projet : aucune action sans plan préalable. Ici elle a une
        valeur particulière — le mode incident coupe le réseau et fige des
        programmes ; l'utilisateur doit voir la liste avant, pas après.
        Seule commande lancée : `netsh ... show rule`, qui lit.
        """
        etat = self.lire_etat()
        selection = self.candidats()
        dossiers = self.dossiers_personnels()
        windows = self.est_windows()
        admin = self.est_admin()
        regle_presente = self.regle_presente() if windows else None

        avertissements: List[str] = [
            "Coupure réseau immédiate : le travail en ligne non enregistré sera "
            "perdu, les appels coupés, les téléchargements interrompus.",
            "Un processus légitime gelé peut perdre ses données non enregistrées.",
            "Un chiffrement déjà terminé ne sera pas annulé : ce mode limite les "
            "dégâts, il ne les répare pas.",
        ]
        if not windows:
            avertissements.append(
                f"Plateforme {self.plateforme()} : la coupure réseau et le cliché "
                "VSS sont indisponibles (Windows uniquement). Le gel des processus "
                "et le rapport, eux, fonctionnent.")
        elif not admin:
            avertissements.append(
                "Sans droits administrateur, la règle de pare-feu et le cliché VSS "
                "échoueront. Relance en tant qu'administrateur pour la séquence complète.")
        if not selection["retenus"]:
            avertissements.append(
                "Aucun processus candidat au gel : soit rien n'écrit anormalement, "
                "soit psutil ne voit pas les processus des autres comptes. "
                "Les autres étapes s'exécutent quand même.")
        if etat.get("actif"):
            avertissements.append(
                "Le mode incident est DÉJÀ actif : activer de nouveau ne fera rien "
                "(pas de seconde règle, pas de second gel).")

        return {
            "ok": True,
            "genere_le": _horodatage(),
            "plateforme": self.plateforme(),
            "administrateur": admin,
            "deja_actif": bool(etat.get("actif")),
            "etat_persistant": str(self.etat_path),
            "reseau": {
                "etape": 1,
                "action": "règle de pare-feu bloquant tout le trafic",
                "regle": NOM_REGLE,
                "sens": ["sortant", "entrant"],
                "deja_presente": regle_presente,
                "disponible": bool(windows and admin),
                "commandes": [" ".join(c) for c in self._commandes_regle()],
                "retrait": f"netsh advfirewall firewall delete rule name={NOM_REGLE}",
                "note": "La carte réseau n'est PAS désactivée : le retour en arrière "
                        "ne dépend donc pas du pilote. La boucle locale (127.0.0.1) "
                        "reste ouverte, l'interface d'ANTI-ZEEVIRIUS continue de répondre.",
            },
            "processus": selection["retenus"],
            "processus_exclus": selection["exclus"],
            "gel": {
                "etape": 2,
                "action": "suspension (SIGSTOP / SuspendThread), jamais arrêt",
                "disponible": bool(PSUTIL_AVAILABLE and SHIELD_AVAILABLE),
                "mecanisme": "optimizer.ransomware_shield.RansomwareShield.suspend_process",
                "liste_noire": sorted(PROCESSUS_CRITIQUES | PROCESSUS_SENSIBLES),
            },
            "sauvegarde": {
                "etape": 3,
                "action": "cliché instantané VSS",
                "dossiers": dossiers,
                "lecteurs": self._lecteurs(dossiers),
                "disponible": bool(windows and admin),
                "commandes": [f"vssadmin create shadow /for={l}" for l in self._lecteurs(dossiers)],
                "repli": "Win32_ShadowCopy.Create via PowerShell si vssadmin refuse "
                         "(éditions client de Windows).",
            },
            "rapport": {
                "etape": 4,
                "action": "rapport horodaté : processus gelés, connexions actives, "
                          "fichiers modifiés récemment",
                "dossier": str(self.dossier),
                "fenetre_minutes": FENETRE_FICHIERS_MINUTES,
            },
            "avertissements": avertissements,
        }

    # ── Activation ──────────────────────────────────────────────────────────

    def _etape_sure(self, nom: str, fonction: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
        """Exécute une étape sans jamais laisser filtrer une exception.

        C'est la traduction en code de « dégradation propre » : une étape qui
        casse est une étape qui rapporte son échec, pas une séquence d'urgence
        interrompue au milieu.
        """
        try:
            return fonction()
        except Exception as e:  # pragma: no cover - filet de sécurité
            return _echec(f"Étape {nom} interrompue : {e.__class__.__name__} — {e}")

    def activer(self, plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Exécute la séquence d'urgence. Idempotent : deux appels = un effet.

        L'ordre est celui de §2.6 et il est délibéré : on coupe d'abord (la
        propagation et l'exfiltration s'arrêtent à la seconde), on gèle
        ensuite, et le cliché VSS vient après le gel — prendre l'instantané
        pendant qu'un chiffrement tourne encore reviendrait à photographier
        des fichiers déjà chiffrés.
        """
        etat = self.lire_etat()
        if etat.get("actif"):
            return {
                "ok": True, "actif": True, "deja_actif": True, "etat": etat,
                "message": f"Mode incident déjà actif depuis {etat.get('active_le')} — "
                           "aucune seconde règle posée, aucun second gel.",
            }

        if plan is None:
            plan = self.preparer()
        debut = time.time()
        etapes: Dict[str, Any] = {}

        # ── 1/4 réseau
        etapes["reseau"] = self._etape_sure("réseau", self.couper_reseau)

        # L'état part sur disque DÈS que la règle est posée : à partir de cet
        # instant, une fermeture brutale de l'application laisse une machine
        # sans réseau, et il faut qu'on sache pourquoi au prochain lancement.
        courant: Dict[str, Any] = {
            "version": ETAT_VERSION,
            "actif": True,
            "active_le": _horodatage(debut),
            "active_le_ts": debut,
            "etape_atteinte": "reseau",
            "regle_pare_feu": NOM_REGLE,
            "reseau_coupe": bool(etapes["reseau"].get("ok")),
            "reseau_detail": etapes["reseau"],
            "processus_geles": [],
            "plateforme": self.plateforme(),
        }
        self._ecrire_etat(courant)

        # ── 2/4 gel
        candidats = plan.get("processus") or []
        etapes["processus"] = self._etape_sure(
            "gel", lambda: self.geler_processus(candidats))
        courant["processus_geles"] = etapes["processus"].get("geles", []) or []
        courant["etape_atteinte"] = "processus"
        self._ecrire_etat(courant)

        # ── 3/4 sauvegarde (étape lente : l'état est déjà complet au-dessus)
        dossiers = (plan.get("sauvegarde") or {}).get("dossiers")
        etapes["sauvegarde"] = self._etape_sure(
            "sauvegarde", lambda: self.sauvegarder(dossiers))
        courant["sauvegarde"] = etapes["sauvegarde"]
        courant["etape_atteinte"] = "sauvegarde"
        self._ecrire_etat(courant)

        # ── 4/4 rapport
        etapes["rapport"] = self._etape_sure(
            "rapport", lambda: self.produire_rapport(plan, etapes, debut))
        courant["rapport"] = {k: etapes["rapport"].get(k) for k in ("json", "texte")}
        courant["etape_atteinte"] = "termine"
        self._ecrire_etat(courant)

        degrade = [nom for nom, res in etapes.items() if not res.get("ok")]
        return {
            "ok": True,
            "actif": True,
            "deja_actif": False,
            "horodatage": _horodatage(debut),
            "duree_s": round(time.time() - debut, 2),
            "etapes": etapes,
            "ordre": ["reseau", "processus", "sauvegarde", "rapport"],
            "degrade": bool(degrade),
            "etapes_en_echec": degrade,
            "nb_geles": len(courant["processus_geles"]),
            "etat_persistant": str(self.etat_path),
            "conseils": list(CONSEILS),
        }

    # ── Rapport ─────────────────────────────────────────────────────────────

    def produire_rapport(self, plan: Dict[str, Any], etapes: Dict[str, Any],
                         debut: Optional[float] = None) -> Dict[str, Any]:
        """Construit et écrit le rapport horodaté (JSON + texte lisible).

        Deux formats parce qu'il y a deux lecteurs : l'interface (JSON) et
        l'humain qui devra peut-être montrer ce fichier à quelqu'un — ou le
        lire depuis une autre machine, si celle-ci est éteinte.
        """
        debut = debut if debut is not None else time.time()
        # None = « choisis pour moi » ; [] = « aucun dossier », et on respecte
        # ce choix plutôt que de repartir fouiller le profil de l'utilisateur.
        dossiers = (plan.get("sauvegarde") or {}).get("dossiers")
        if dossiers is None:
            dossiers = self.dossiers_personnels()
        connexions = self.connexions_actives()
        fichiers = self.fichiers_recents(dossiers)

        rapport: Dict[str, Any] = {
            "titre": "ANTI-ZEEVIRIUS — MODE INCIDENT",
            "horodatage": _horodatage(debut),
            "plateforme": self.plateforme(),
            "administrateur": self.est_admin(),
            "reseau": etapes.get("reseau", {}),
            "processus": etapes.get("processus", {}),
            "sauvegarde": etapes.get("sauvegarde", {}),
            "connexions": connexions,
            "fichiers_recents": fichiers,
            "conseils": list(CONSEILS),
            "limites": [
                "Gel, pas arrêt : les processus gelés conservent leurs preuves.",
                "Un chiffrement déjà terminé n'est pas annulé.",
                "Un logiciel malveillant plus privilégié que l'outil peut résister au gel.",
                "ANTI-ZEEVIRIUS est un complément à Windows Defender, pas un remplacement.",
            ],
        }

        horodatage_fichier = datetime.fromtimestamp(debut).strftime("%Y%m%d_%H%M%S")
        dossier = self.dossier
        chemins: Dict[str, Any] = {}
        try:
            dossier.mkdir(parents=True, exist_ok=True)
            chemin_json = dossier / f"incident_{horodatage_fichier}.json"
            chemin_json.write_text(json.dumps(rapport, indent=2, ensure_ascii=False),
                                   encoding="utf-8")
            chemins["json"] = str(chemin_json)
            chemin_txt = dossier / f"incident_{horodatage_fichier}.txt"
            chemin_txt.write_text(self.rapport_texte(rapport), encoding="utf-8")
            chemins["texte"] = str(chemin_txt)
        except OSError as e:
            return _echec(f"Rapport non écrit sur disque : {e}", rapport=rapport, **chemins)

        return {"ok": True, "rapport": rapport, **chemins,
                "nb_fichiers_modifies": fichiers.get("nb", 0),
                "nb_connexions": connexions.get("nb", 0)}

    @staticmethod
    def rapport_texte(rapport: Dict[str, Any]) -> str:
        """Version lisible, dans la forme montrée à l'utilisateur au
        didacticiel n°6 — pour qu'il retrouve ce qu'on lui a promis."""
        L: List[str] = []
        L.append(f"{rapport['titre']} — {rapport['horodatage']}")
        L.append("=" * 66)

        reseau = rapport.get("reseau", {})
        if reseau.get("ok") and reseau.get("deja_presente"):
            etat_reseau = f"déjà coupé (règle {NOM_REGLE} en place)"
        elif reseau.get("ok"):
            etat_reseau = f"coupé (règle pare-feu {NOM_REGLE})"
        else:
            etat_reseau = f"NON COUPÉ — {reseau.get('reason', 'raison inconnue')}"
        L.append(f"[1/4] Réseau           {etat_reseau}")

        proc = rapport.get("processus", {})
        geles = proc.get("geles", []) or []
        if geles:
            liste = ", ".join(f"{p.get('nom')} (PID {p.get('pid')})" for p in geles)
        else:
            liste = "aucun" if proc.get("ok") else f"indisponible — {proc.get('reason', '')}"
        L.append(f"[2/4] Processus gelés  {liste}")
        for ignore in proc.get("ignores", []) or []:
            L.append(f"        écarté : {ignore.get('nom')} (PID {ignore.get('pid')}) — "
                     f"{ignore.get('raison')}")

        sauv = rapport.get("sauvegarde", {})
        if sauv.get("ok"):
            lecteurs = ", ".join(c.get("lecteur", "?") for c in sauv.get("cliches", []) if c.get("ok"))
            L.append(f"[3/4] Sauvegarde       cliché VSS : {lecteurs}")
        else:
            L.append(f"[3/4] Sauvegarde       indisponible — {sauv.get('reason', '')}")

        fichiers = rapport.get("fichiers_recents", {})
        connexions = rapport.get("connexions", {})
        L.append(f"[4/4] Rapport          {fichiers.get('nb', 0)} fichier(s) modifié(s) en "
                 f"{fichiers.get('fenetre_minutes', '?')} min, "
                 f"{connexions.get('nb_etablies', 0)} connexion(s) établie(s)")
        L.append("")
        L.append("À FAIRE MAINTENANT")
        for conseil in rapport.get("conseils", []):
            L.append(f"  • {conseil}")

        if fichiers.get("fichiers"):
            L.append("")
            L.append(f"FICHIERS MODIFIÉS RÉCEMMENT ({fichiers.get('nb', 0)})")
            for f in fichiers["fichiers"][:50]:
                L.append(f"  {f['modifie_le']}  {f['taille']:>10}  {f['chemin']}")
            if fichiers.get("tronque"):
                L.append("  … liste tronquée (rapport d'urgence, parcours volontairement borné)")

        etablies = [c for c in connexions.get("connexions", []) if c.get("distant")]
        if etablies:
            L.append("")
            L.append(f"CONNEXIONS AU MOMENT DE L'INCIDENT ({len(etablies)})")
            for c in etablies[:50]:
                L.append(f"  {str(c.get('processus')):<24} {str(c.get('local')):<24} → "
                         f"{c.get('distant')}  [{c.get('statut')}]")

        L.append("")
        L.append("LIMITES")
        for limite in rapport.get("limites", []):
            L.append(f"  - {limite}")
        return "\n".join(L) + "\n"

    # ── Sortie du mode ──────────────────────────────────────────────────────

    def retablir(self) -> Dict[str, Any]:
        """Retire la règle de pare-feu et relance les processus gelés.

        Règle de prudence : l'état n'est marqué « rétabli » que si TOUT est
        revenu. Si la règle n'a pas pu être retirée ou si un processus refuse
        de repartir, le fichier d'état conserve ce qui reste — sans quoi
        l'utilisateur se retrouverait avec un réseau coupé et plus aucune
        trace expliquant pourquoi.
        """
        etat = self.lire_etat()
        if not etat.get("existe"):
            return {"ok": True, "actif": False, "rien_a_faire": True,
                    "message": "Aucun mode incident enregistré : rien à rétablir."}

        geles = etat.get("processus_geles") or []

        # Si l'état dit qu'aucune règle n'a jamais été posée (hors Windows, ou
        # échec au moment de l'activation), on ne va pas la retirer. En
        # revanche, un état corrompu déclenche une tentative de retrait : la
        # règle est peut-être là, et une suppression inutile ne coûte rien.
        if not etat.get("corrompu") and not etat.get("reseau_coupe"):
            res_reseau: Dict[str, Any] = {
                "ok": True, "regle": NOM_REGLE, "rien_a_faire": True,
                "message": "Aucune règle de pare-feu n'avait été posée.",
            }
        else:
            res_reseau = self._etape_sure("réseau", self.retablir_reseau)

        res_proc = self._etape_sure("dégel", lambda: self.degeler_processus(geles))
        restants = res_proc.get("restants") or []
        if res_proc.get("unavailable"):
            # psutil absent : on ne peut pas relancer, donc on ne perd pas la
            # liste. Elle sera rejouable au prochain lancement.
            restants = list(geles)

        complet = bool(res_reseau.get("ok")) and not restants

        if complet:
            nouvel_etat = {
                "version": ETAT_VERSION,
                "actif": False,
                "retabli_le": _horodatage(),
                "dernier_incident": {
                    "active_le": etat.get("active_le"),
                    "nb_geles": len(geles),
                    "rapport": etat.get("rapport"),
                },
            }
        else:
            nouvel_etat = dict(etat)
            nouvel_etat.pop("existe", None)
            nouvel_etat["actif"] = True
            nouvel_etat["processus_geles"] = restants
            nouvel_etat["reseau_coupe"] = not res_reseau.get("ok")
            nouvel_etat["retablissement_partiel"] = {
                "tente_le": _horodatage(),
                "reseau": res_reseau,
                "processus_restants": len(restants),
            }
        self._ecrire_etat(nouvel_etat)

        if complet:
            message = (f"Mode incident levé : réseau rétabli, "
                       f"{res_proc.get('nb_relances', 0)} processus relancé(s).")
        else:
            manques = []
            if not res_reseau.get("ok"):
                manques.append(f"règle {NOM_REGLE} toujours en place "
                               f"({res_reseau.get('reason', '')})")
            if restants:
                manques.append(f"{len(restants)} processus toujours gelé(s)")
            message = ("Rétablissement PARTIEL — " + " ; ".join(manques)
                       + ". L'état est conservé : relance « Rétablir » "
                         "(au besoin en administrateur).")

        return {
            "ok": complet,
            "actif": not complet,
            "complet": complet,
            "etapes": {"reseau": res_reseau, "processus": res_proc},
            "relances": res_proc.get("relances", []),
            "restants": restants,
            "message": message,
            "etat_persistant": str(self.etat_path),
        }

    # ── État (à lire au lancement de l'application) ─────────────────────────

    def etat(self) -> Dict[str, Any]:
        """Ce que l'application doit afficher à son démarrage.

        Un utilisateur qui reste avec un réseau coupé sans savoir pourquoi,
        c'est un échec grave : cette fonction existe pour que ça n'arrive
        jamais silencieusement.
        """
        brut = self.lire_etat()
        actif = bool(brut.get("actif"))
        geles = brut.get("processus_geles") or []

        if brut.get("corrompu"):
            message = ("Fichier d'état du mode incident illisible. Si le réseau est "
                       f"coupé, retire la règle : netsh advfirewall firewall "
                       f"delete rule name={NOM_REGLE}")
        elif actif:
            details = []
            if brut.get("reseau_coupe"):
                details.append(f"réseau coupé (règle {NOM_REGLE})")
            if geles:
                details.append(f"{len(geles)} processus gelé(s) : "
                               + ", ".join(str(p.get("nom")) for p in geles))
            message = (f"MODE INCIDENT ACTIF depuis {brut.get('active_le', '?')} — "
                       + (" ; ".join(details) if details else "aucune action encore appliquée")
                       + ". Utilise « Rétablir » pour tout remettre en place.")
        else:
            message = "Mode incident inactif."

        return {
            "ok": True,
            "actif": actif,
            "restauration_requise": actif,
            "depuis": brut.get("active_le"),
            "reseau_coupe": bool(brut.get("reseau_coupe")),
            "regle": NOM_REGLE,
            "processus_geles": geles,
            "nb_geles": len(geles),
            "etape_atteinte": brut.get("etape_atteinte"),
            "rapport": brut.get("rapport"),
            "corrompu": bool(brut.get("corrompu")),
            "etat_persistant": str(self.etat_path),
            "retrait_manuel": f"netsh advfirewall firewall delete rule name={NOM_REGLE}",
            "message": message,
        }


# ── API de module ───────────────────────────────────────────────────────────
# Quatre fonctions pour l'interface et le menu : un plan, une activation, une
# sortie, et l'état à afficher au lancement.

def preparer() -> Dict[str, Any]:
    return IncidentMode().preparer()


def activer(plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return IncidentMode().activer(plan)


def retablir() -> Dict[str, Any]:
    return IncidentMode().retablir()


def etat() -> Dict[str, Any]:
    return IncidentMode().etat()
