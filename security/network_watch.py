"""
network_watch.py — qui parle à qui, et qui n'a aucune raison de le faire.

Un logiciel malveillant finit presque toujours par communiquer : pour recevoir
ses ordres, exfiltrer des données, ou télécharger sa charge utile. Cette
communication est souvent le signal le plus visible d'une infection — bien plus
que le fichier lui-même, qui peut être inconnu de toutes les bases.

Ce module dresse l'inventaire des connexions établies, l'associe au processus
propriétaire, et **note ce qui est anormal**.

Ce qu'il ne fait PAS, et c'est délibéré :

  * il n'intercepte rien. Aucun driver, aucune insertion dans le flux : on lit
    les tables de connexion du système. On voit QUI parle à QUI, jamais ce qui
    est dit. Le trafic chiffré reste chiffré.
  * il ne bloque rien. Le blocage relève de `app_firewall.py`, qui pilote le
    pare-feu de Windows. Ici on observe et on rapporte.
  * il n'accuse pas. Une adresse à l'étranger, une IP sans nom de domaine, un
    port inhabituel : aucun de ces signaux n'est une preuve. C'est leur
    ACCUMULATION sur une même connexion qui mérite un regard.

Conséquence pratique : une connexion très brève, ouverte et fermée entre deux
relevés, passe inaperçue. Ce module donne une photographie, pas un film.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Set

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:                                  # pragma: no cover
    PSUTIL_AVAILABLE = False

__all__ = ["NetworkWatch", "Connexion", "PORTS_SUSPECTS", "PROCESSUS_SANS_RESEAU"]


# ── Signaux ────────────────────────────────────────────────────────────────

# Ports associés à des outils d'intrusion courants. Leur présence n'est pas une
# preuve — un développeur peut légitimement écouter sur 4444 — mais sur une
# machine ordinaire, une connexion SORTANTE vers ces ports est peu banale.
PORTS_SUSPECTS = {
    4444: "port par défaut de Metasploit / porte dérobée",
    5555: "porte dérobée courante, ADB Android",
    1337: "port d'usage historique dans les outils d'intrusion",
    31337: "port d'usage historique dans les outils d'intrusion",
    6667: "IRC — canal de commande de réseaux de zombies",
    6697: "IRC chiffré — canal de commande",
    9001: "relais Tor",
    9050: "proxy Tor",
    4899: "outil d'administration à distance (Radmin)",
}

# Programmes système qui n'ont, sur une machine de particulier, aucune raison
# d'ouvrir une connexion sortante. Un nom de cette liste qui communique est
# presque toujours une USURPATION : un logiciel malveillant qui se fait passer
# pour un composant de Windows.
PROCESSUS_SANS_RESEAU = {
    "csrss.exe", "smss.exe", "wininit.exe", "winlogon.exe",
    "lsass.exe", "services.exe", "fontdrvhost.exe", "dwm.exe",
    "notepad.exe", "calc.exe", "mspaint.exe",
}

# Noms de composants Windows fréquemment imités. La comparaison se fait sur la
# distance d'édition : `svch0st.exe` (zéro) ou `winlogin.exe` (i au lieu de o)
# sont des classiques.
NOMS_SYSTEME_IMITES = {
    "svchost.exe", "csrss.exe", "lsass.exe", "winlogon.exe", "services.exe",
    "explorer.exe", "taskhostw.exe", "dwm.exe", "conhost.exe", "rundll32.exe",
}

# Poids des signaux. Le score total classe la connexion ; aucun signal seul ne
# suffit à déclarer une connexion malveillante.
POIDS = {
    "processus_sans_reseau": 50,
    "nom_imite": 45,
    "port_suspect": 30,
    "sans_nom_de_domaine": 12,
    "processus_inconnu": 15,
    "chemin_temporaire": 25,
    "port_eleve_non_standard": 5,
}

SEUIL_SUSPECT = 25
SEUIL_A_EXAMINER = 45


@dataclass
class Connexion:
    """Une connexion établie, avec son processus et son analyse."""
    pid: Optional[int]
    processus: str
    chemin: str
    adresse_locale: str
    adresse_distante: str
    port_distant: Optional[int]
    statut: str
    nom_de_domaine: Optional[str] = None
    score: int = 0
    raisons: List[str] = field(default_factory=list)

    @property
    def niveau(self) -> str:
        if self.score >= SEUIL_A_EXAMINER:
            return "a_examiner"
        if self.score >= SEUIL_SUSPECT:
            return "suspect"
        return "normal"

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["niveau"] = self.niveau
        return d


def _distance_edition(a: str, b: str) -> int:
    """Distance de Levenshtein, pour repérer les noms imités.

    Implémentation à deux lignes glissantes : O(len(a)) en mémoire. Les noms de
    processus sont courts, mais cette fonction est appelée pour chaque
    connexion croisée avec chaque nom système — autant qu'elle soit sobre.
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
                precedente[j] + 1,          # suppression
                courante[j - 1] + 1,        # insertion
                precedente[j - 1] + (ca != cb),   # substitution
            ))
        precedente = courante
    return precedente[-1]


class NetworkWatch:
    """Inventaire et analyse des connexions réseau établies."""

    def __init__(self, resoudre_noms: bool = True, timeout_dns: float = 0.35):
        # La résolution inverse est le seul poste coûteux de ce module : chaque
        # nom demandé peut attendre le réseau. On la borne, et on la met en
        # cache — plusieurs connexions visent souvent la même adresse.
        self.resoudre_noms = resoudre_noms
        self.timeout_dns = timeout_dns
        self._cache_dns: Dict[str, Optional[str]] = {}

    # ── Collecte ───────────────────────────────────────────────────────────
    def lister_connexions(self) -> Dict:
        """Relève les connexions établies. Lecture seule, aucun effet de bord."""
        if not PSUTIL_AVAILABLE:
            return {"ok": False, "unavailable": True,
                    "reason": "psutil absent — inventaire réseau indisponible",
                    "error": "psutil absent — inventaire réseau indisponible"}

        try:
            brutes = psutil.net_connections(kind="inet")
        except (psutil.AccessDenied, PermissionError):
            # Sans élévation, Windows ne révèle que les connexions du compte
            # courant. C'est une limitation réelle, pas une panne : on le dit
            # plutôt que de rendre une liste tronquée sans avertissement.
            return {"ok": False, "unavailable": True,
                    "reason": "droits insuffisants — relancer en administrateur "
                              "pour voir les connexions de tous les processus",
                    "error": "droits insuffisants pour l'inventaire complet"}
        except Exception as e:                        # pragma: no cover
            return {"ok": False, "error": f"inventaire impossible : {e}",
                    "unavailable": False}

        connexions = []
        for c in brutes:
            if c.status != getattr(psutil, "CONN_ESTABLISHED", "ESTABLISHED"):
                continue
            if not c.raddr:
                continue
            connexions.append(self._construire(c))

        self._analyser(connexions)
        connexions.sort(key=lambda x: (-x.score, x.processus.lower()))

        return {
            "ok": True,
            "data": {
                "connexions": [c.to_dict() for c in connexions],
                "total": len(connexions),
                "a_examiner": sum(1 for c in connexions if c.niveau == "a_examiner"),
                "suspects": sum(1 for c in connexions if c.niveau == "suspect"),
            },
        }

    def _construire(self, c) -> Connexion:
        nom, chemin = "inconnu", ""
        if c.pid:
            try:
                p = psutil.Process(c.pid)
                nom = p.name()
                try:
                    chemin = p.exe()
                except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
                    chemin = ""
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        return Connexion(
            pid=c.pid,
            processus=nom,
            chemin=chemin,
            adresse_locale=f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "",
            adresse_distante=c.raddr.ip if c.raddr else "",
            port_distant=c.raddr.port if c.raddr else None,
            statut=str(c.status),
        )

    # ── Analyse ────────────────────────────────────────────────────────────
    def _analyser(self, connexions: List[Connexion]) -> None:
        for c in connexions:
            if self._est_locale(c.adresse_distante):
                # Une connexion vers la boucle locale ou le réseau privé ne
                # sort pas de la machine ou du domicile : hors périmètre.
                c.raisons.append("destination locale ou réseau privé")
                continue

            nom_min = c.processus.lower()

            if nom_min in PROCESSUS_SANS_RESEAU:
                c.score += POIDS["processus_sans_reseau"]
                c.raisons.append(
                    f"{c.processus} n'a normalement aucune raison de communiquer — "
                    f"usurpation probable")

            imite = self._nom_imite(nom_min)
            if imite:
                c.score += POIDS["nom_imite"]
                c.raisons.append(
                    f"nom très proche du composant Windows « {imite} » — "
                    f"imitation probable")

            if c.port_distant in PORTS_SUSPECTS:
                c.score += POIDS["port_suspect"]
                c.raisons.append(
                    f"port {c.port_distant} : {PORTS_SUSPECTS[c.port_distant]}")

            if c.processus == "inconnu":
                c.score += POIDS["processus_inconnu"]
                c.raisons.append("processus propriétaire non identifiable")

            if c.chemin and self._chemin_temporaire(c.chemin):
                c.score += POIDS["chemin_temporaire"]
                c.raisons.append(
                    "exécutable situé dans un dossier temporaire — "
                    "emplacement inhabituel pour un programme installé")

            if self.resoudre_noms:
                c.nom_de_domaine = self._nom_inverse(c.adresse_distante)
                if not c.nom_de_domaine:
                    c.score += POIDS["sans_nom_de_domaine"]
                    c.raisons.append(
                        "aucun nom de domaine associé — fréquent pour un serveur "
                        "de commande, mais aussi pour certains hébergeurs légitimes")

            if (c.port_distant and c.port_distant > 1024
                    and c.port_distant not in PORTS_SUSPECTS
                    and c.port_distant not in (3478, 5222, 8080, 8443)):
                c.score += POIDS["port_eleve_non_standard"]

    @staticmethod
    def _est_locale(ip: str) -> bool:
        try:
            adr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return (adr.is_private or adr.is_loopback or adr.is_link_local
                or adr.is_multicast or adr.is_reserved)

    @staticmethod
    def _nom_imite(nom: str) -> Optional[str]:
        """Retourne le nom système imité, ou None.

        Une distance de 1 ou 2 sur un nom qui n'est PAS exactement un nom
        système : c'est la signature d'une imitation (`svch0st`, `winlogin`).

        On retient la référence LA PLUS PROCHE, et l'ordre de parcours est
        rendu déterministe. Une version antérieure parcourait l'ensemble
        `NOMS_SYSTEME_IMITES` et renvoyait la première correspondance sous le
        seuil : comme un `set` Python n'a pas d'ordre stable d'une exécution à
        l'autre, `1sass.exe` était attribué tantôt à `lsass.exe` (distance 1),
        tantôt à `csrss.exe` (distance 2). Un antivirus qui désigne un coupable
        différent à chaque lancement n'est pas utilisable — et le défaut
        échappait aux tests, qui passaient une fois sur deux selon
        PYTHONHASHSEED.
        """
        if nom in NOMS_SYSTEME_IMITES:
            return None                      # c'est le vrai
        meilleure, meilleure_distance = None, None
        for reference in sorted(NOMS_SYSTEME_IMITES):
            if abs(len(nom) - len(reference)) > 2:
                continue
            d = _distance_edition(nom, reference)
            if 0 < d <= 2 and (meilleure_distance is None or d < meilleure_distance):
                meilleure, meilleure_distance = reference, d
        return meilleure

    @staticmethod
    def _chemin_temporaire(chemin: str) -> bool:
        c = chemin.lower().replace("/", "\\")
        marqueurs = ("\\temp\\", "\\tmp\\", "\\appdata\\local\\temp\\",
                     "\\windows\\temp\\", "\\downloads\\")
        return any(m in c for m in marqueurs)

    def _nom_inverse(self, ip: str) -> Optional[str]:
        if ip in self._cache_dns:
            return self._cache_dns[ip]
        ancien = socket.getdefaulttimeout()
        try:
            socket.setdefaulttimeout(self.timeout_dns)
            nom = socket.gethostbyaddr(ip)[0]
        except (socket.herror, socket.gaierror, OSError):
            nom = None
        finally:
            socket.setdefaulttimeout(ancien)
        self._cache_dns[ip] = nom
        return nom

    # ── Vue par application ────────────────────────────────────────────────
    def resumer_par_application(self) -> Dict:
        """Regroupe les connexions par programme.

        C'est la vue qui sert à décider d'un blocage : on ne bloque pas une
        connexion, on bloque une application.
        """
        res = self.lister_connexions()
        if not res.get("ok"):
            return res

        par_app: Dict[str, Dict] = {}
        for c in res["data"]["connexions"]:
            cle = c["chemin"] or c["processus"]
            e = par_app.setdefault(cle, {
                "processus": c["processus"], "chemin": c["chemin"],
                "connexions": 0, "destinations": set(),
                "score_max": 0, "raisons": set(),
            })
            e["connexions"] += 1
            e["destinations"].add(c["adresse_distante"])
            e["score_max"] = max(e["score_max"], c["score"])
            e["raisons"].update(c["raisons"])

        applications = []
        for e in par_app.values():
            e["destinations"] = sorted(e["destinations"])
            e["raisons"] = sorted(e["raisons"])
            e["niveau"] = ("a_examiner" if e["score_max"] >= SEUIL_A_EXAMINER
                           else "suspect" if e["score_max"] >= SEUIL_SUSPECT
                           else "normal")
            applications.append(e)
        applications.sort(key=lambda a: -a["score_max"])

        return {"ok": True, "data": {"applications": applications,
                                     "total": len(applications)}}
