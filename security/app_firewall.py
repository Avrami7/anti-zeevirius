"""
app_firewall.py — décider quelle application a le droit de sortir.

`network_watch.py` observe et signale ; ce module agit. C'est le complément
naturel : voir qu'un logiciel communique sans raison ne sert à rien si on ne
peut pas l'en empêcher.

CE QUE CE MODULE EST
    Une façade lisible du pare-feu de Windows. On pose des règles nommées, on
    les liste, on les retire. Windows fait le blocage ; nous rendons la
    décision compréhensible et réversible.

CE QUE CE MODULE N'EST PAS — à lire avant d'en attendre autre chose
    Ce n'est PAS un pare-feu applicatif au sens commercial. Il ne peut pas
    afficher « telle application veut se connecter, autoriser ? » au moment où
    ça arrive : intercepter le trafic exige un callout WFP en mode noyau, que
    ce projet s'interdit. Tu poses donc des règles À FROID, en connaissance de
    cause, plutôt que de répondre dans l'urgence à une fenêtre surgissante.

    C'est un compromis assumé, et il a un avantage : une décision prise
    tranquillement est meilleure qu'un clic réflexe sur une alerte.

TOUTES nos règles portent le préfixe `AZ_APP_`. Elles sont donc reconnaissables
au milieu de celles de Windows, et ne peuvent pas être confondues avec la règle
`AZ_INCIDENT` du Mode Incident, qui bloque TOUT le réseau et relève d'un autre
mécanisme — les deux doivent pouvoir coexister sans se marcher dessus.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass, asdict
# PureWindowsPath, et non Path : ce module manipule TOUJOURS des chemins
# Windows. Sous Linux, `Path` est un PosixPath pour lequel la contre-oblique
# n'est pas un séparateur — `Path(r"C:\Windows\svchost.exe").name` rend le
# chemin entier au lieu du nom de fichier, et le garde-fou protégeant les
# composants essentiels de Windows ne se déclenche plus. Le type pur exprime
# l'intention et se comporte pareil quelle que soit la machine qui l'exécute.
from pathlib import PureWindowsPath
from typing import Callable, Dict, List, Optional, Sequence

__all__ = ["AppFirewall", "Regle", "PREFIXE", "PROGRAMMES_A_NE_PAS_BLOQUER"]


PREFIXE = "AZ_APP_"

# Nom réservé par le Mode Incident : ce module ne doit jamais le lire comme
# une de ses règles, ni le supprimer. Couper tout le réseau et bloquer une
# application sont deux gestes distincts, avec deux cycles de vie distincts.
NOM_REGLE_INCIDENT = "AZ_INCIDENT"

# Bloquer ces programmes casse des fonctions essentielles de Windows —
# notamment les mises à jour de sécurité, ce qui rendrait la machine PLUS
# vulnérable. Un outil de sécurité qui dégrade la sécurité est une faute.
PROGRAMMES_A_NE_PAS_BLOQUER = {
    "svchost.exe",          # porte la plupart des services Windows
    "services.exe",
    "lsass.exe",
    "wininit.exe",
    "winlogon.exe",
    "csrss.exe",
    "smss.exe",
    "wuauclt.exe",          # Windows Update
    "usoclient.exe",        # Windows Update (orchestrateur)
    "mpcmdrun.exe",         # Microsoft Defender
    "msmpeng.exe",          # Microsoft Defender (moteur)
    "securityhealthservice.exe",
    "dnscache.exe",
}


def _executer(commande: Sequence[str], timeout: int = 45) -> Dict:
    try:
        p = subprocess.run(list(commande), capture_output=True, text=True,
                           timeout=timeout, shell=False)
        return {"code": p.returncode, "sortie": p.stdout or "",
                "erreur": p.stderr or ""}
    except FileNotFoundError:
        return {"code": -1, "sortie": "", "erreur": "commande introuvable"}
    except subprocess.TimeoutExpired:
        return {"code": -2, "sortie": "", "erreur": "délai dépassé"}
    except OSError as e:                                # pragma: no cover
        return {"code": -3, "sortie": "", "erreur": str(e)}


def _indisponible(raison: str) -> Dict:
    return {"ok": False, "unavailable": True, "reason": raison, "error": raison}


@dataclass
class Regle:
    """Une règle posée par cet outil."""
    nom: str
    programme: str
    application: str
    sens: str                     # "sortant" | "entrant"
    active: bool = True

    def to_dict(self) -> Dict:
        return asdict(self)


class AppFirewall:
    """Pose, liste et retire des règles de blocage par application."""

    def __init__(self, runner: Optional[Callable[..., Dict]] = None):
        self._executer = runner or _executer

    # ── Nommage ────────────────────────────────────────────────────────────
    @staticmethod
    def nom_de_regle(programme: str, sens: str) -> str:
        """Nom stable et unique pour un couple (programme, sens).

        Le chemin complet est condensé en empreinte courte : deux programmes
        homonymes dans des dossiers différents doivent avoir des règles
        distinctes, et un nom de règle ne peut pas contenir un chemin entier.
        """
        empreinte = hashlib.sha256(programme.lower().encode("utf-8")).hexdigest()[:10]
        base = PureWindowsPath(programme).name or "programme"
        base = re.sub(r"[^A-Za-z0-9._-]", "_", base)[:40]
        return f"{PREFIXE}{base}_{sens}_{empreinte}"

    # ── Garde-fous ─────────────────────────────────────────────────────────
    @staticmethod
    def _refus_de_blocage(programme: str) -> Optional[str]:
        nom = PureWindowsPath(programme).name.lower()
        if nom in PROGRAMMES_A_NE_PAS_BLOQUER:
            return (f"{nom} est un composant essentiel de Windows. Le bloquer "
                    f"casserait des fonctions du système — pour svchost.exe et "
                    f"les composants de mise à jour, cela empêcherait les "
                    f"correctifs de sécurité d'arriver, ce qui rendrait la "
                    f"machine PLUS vulnérable, pas moins.")
        return None

    # ── Plan ───────────────────────────────────────────────────────────────
    def preparer_blocage(self, programme: str, sens: str = "sortant") -> Dict:
        """Décrit ce qui sera fait. Ne modifie rien."""
        programme = (programme or "").strip().strip('"')
        if not programme:
            return {"ok": False, "error": "aucun programme indiqué",
                    "unavailable": False}
        if sens not in ("sortant", "entrant"):
            return {"ok": False, "error": "sens invalide (sortant | entrant)",
                    "unavailable": False}

        refus = self._refus_de_blocage(programme)
        if refus:
            return {"ok": False, "error": refus, "unavailable": False}

        nom = self.nom_de_regle(programme, sens)
        existe = self._regle_existe(nom)

        return {"ok": True, "data": {
            "action": "bloquer_application",
            "programme": programme,
            "application": PureWindowsPath(programme).name,
            "sens": sens,
            "nom_regle": nom,
            "deja_bloquee": existe,
            "reversible": True,
            "etapes": [
                f"créer une règle de pare-feu {sens}e nommée « {nom} »",
                f"bloquer toute connexion {sens}e de {PureWindowsPath(programme).name}",
            ],
            "avertissements": [
                "Certains logiciels échouent silencieusement une fois bloqués, "
                "sans message d'erreur explicite.",
                "Exige les droits administrateur.",
                "La règle est retirable à tout moment depuis cette même page.",
            ],
        }}

    # ── Application ────────────────────────────────────────────────────────
    def bloquer(self, plan: Dict) -> Dict:
        """Applique un plan produit par `preparer_blocage()`."""
        plan = plan or {}
        programme = plan.get("programme")
        sens = plan.get("sens", "sortant")
        if not programme:
            return {"ok": False, "error": "plan vide ou invalide",
                    "unavailable": False}

        # Le garde-fou est REVÉRIFIÉ ici : un plan peut avoir été fabriqué ou
        # modifié entre sa production et son application, notamment s'il
        # transite par l'API web où l'appelant contrôle le dictionnaire.
        refus = self._refus_de_blocage(programme)
        if refus:
            return {"ok": False, "error": refus, "unavailable": False}

        nom = self.nom_de_regle(programme, sens)
        if self._regle_existe(nom):
            # Idempotence : reposer la règle créerait un doublon, et un seul
            # `delete` n'en retirerait qu'un — le blocage survivrait au
            # déblocage, ce qui est le pire des cas.
            return {"ok": True, "data": {"nom_regle": nom, "deja_presente": True,
                                         "programme": programme, "sens": sens}}

        direction = "out" if sens == "sortant" else "in"
        r = self._executer([
            "netsh", "advfirewall", "firewall", "add", "rule",
            f"name={nom}", f"dir={direction}", "action=block",
            f"program={programme}", "enable=yes",
        ])
        if r["code"] != 0:
            if "commande introuvable" in (r["erreur"] or ""):
                return _indisponible("netsh indisponible (Windows uniquement)")
            return {"ok": False, "unavailable": False,
                    "error": "création de la règle refusée — droits "
                             "administrateur nécessaires"}

        return {"ok": True, "data": {"nom_regle": nom, "programme": programme,
                                     "application": PureWindowsPath(programme).name,
                                     "sens": sens, "deja_presente": False}}

    def debloquer(self, nom_regle: str) -> Dict:
        """Retire une règle posée par cet outil."""
        nom_regle = (nom_regle or "").strip()
        if not nom_regle.startswith(PREFIXE):
            # Refus net : sans cette barrière, l'outil pourrait supprimer une
            # règle de Windows ou celle du Mode Incident, et rétablir
            # silencieusement un accès que l'utilisateur croyait coupé.
            return {"ok": False, "unavailable": False,
                    "error": f"cette règle n'a pas été posée par ANTI-ZEEVIRIUS "
                             f"(le préfixe {PREFIXE} est requis) — refus de la "
                             f"supprimer"}

        r = self._executer(["netsh", "advfirewall", "firewall", "delete", "rule",
                            f"name={nom_regle}"])
        if r["code"] != 0:
            texte = (r["sortie"] + r["erreur"]).lower()
            if "commande introuvable" in (r["erreur"] or ""):
                return _indisponible("netsh indisponible (Windows uniquement)")
            # « Aucune règle correspondante » n'est pas un échec : l'état
            # souhaité — la règle n'existe plus — est atteint.
            if "no rules match" in texte or "aucune règle" in texte:
                return {"ok": True, "data": {"nom_regle": nom_regle,
                                             "deja_absente": True}}
            return {"ok": False, "unavailable": False,
                    "error": "suppression refusée — droits administrateur "
                             "nécessaires"}
        return {"ok": True, "data": {"nom_regle": nom_regle, "deja_absente": False}}

    # ── Lecture ────────────────────────────────────────────────────────────
    def _regle_existe(self, nom: str) -> bool:
        r = self._executer(["netsh", "advfirewall", "firewall", "show", "rule",
                            f"name={nom}"])
        if r["code"] != 0:
            return False
        texte = (r["sortie"] or "").lower()
        return bool(texte.strip()) and "no rules match" not in texte \
            and "aucune règle" not in texte

    def lister_regles(self) -> Dict:
        """Règles posées par cet outil, avec le programme concerné.

        On analyse la sortie de `netsh` en français comme en anglais : la
        langue de Windows n'est pas la nôtre, et supposer l'anglais rendrait
        la liste vide sur une machine française — sans le moindre message
        d'erreur, donc sans que personne ne s'en aperçoive.
        """
        r = self._executer(["netsh", "advfirewall", "firewall", "show", "rule",
                            "name=all", "verbose"], timeout=90)
        if r["code"] != 0:
            if "commande introuvable" in (r["erreur"] or ""):
                return _indisponible("netsh indisponible (Windows uniquement)")
            return _indisponible("liste des règles illisible")

        regles: List[Regle] = []
        nom = programme = sens = None
        active = True

        for ligne in (r["sortie"] or "").splitlines():
            l = ligne.strip()
            if not l:
                if nom and nom.startswith(PREFIXE) and programme:
                    regles.append(Regle(nom=nom, programme=programme,
                                        application=PureWindowsPath(programme).name,
                                        sens=sens or "sortant", active=active))
                nom = programme = sens = None
                active = True
                continue

            cle, _, valeur = l.partition(":")
            cle, valeur = cle.strip().lower(), valeur.strip()
            if cle in ("rule name", "nom de la règle", "nom de la regle"):
                nom = valeur
            elif cle in ("program", "programme"):
                programme = valeur
            elif cle in ("direction", "sens"):
                v = valeur.lower()
                sens = "sortant" if v.startswith(("out", "sort")) else "entrant"
            elif cle in ("enabled", "activée", "activee"):
                active = valeur.lower() in ("yes", "oui")

        if nom and nom.startswith(PREFIXE) and programme:
            regles.append(Regle(nom=nom, programme=programme,
                                application=PureWindowsPath(programme).name,
                                sens=sens or "sortant", active=active))

        return {"ok": True, "data": {
            "regles": [x.to_dict() for x in regles],
            "total": len(regles),
            "note": ("Seules les règles posées par ANTI-ZEEVIRIUS sont listées. "
                     "Celles de Windows et la règle du Mode Incident ne sont ni "
                     "affichées ni modifiables ici."),
        }}

    def est_bloque(self, programme: str, sens: str = "sortant") -> bool:
        return self._regle_existe(self.nom_de_regle(programme, sens))
