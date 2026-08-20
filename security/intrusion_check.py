"""
intrusion_check.py — qui est entré, quand, depuis où.

Ce module répond à une question précise et fréquente : « quelqu'un accède-t-il
à mon ordinateur ? ». Il faut d'abord dire clairement ce qui est possible, car
l'écart avec ce qu'on imagine est grand.

CE QU'ON PEUT SAVOIR
    * quelles sessions à distance sont ouvertes EN CE MOMENT ;
    * quels logiciels d'accès à distance sont installés et actifs ;
    * quelles connexions ENTRANTES sont établies ;
    * quels comptes se sont connectés, quand, depuis quelle machine, et
      lesquels ont échoué — le journal de sécurité de Windows conserve cela
      rétroactivement, c'est la source la plus utile du module ;
    * quels comptes locaux ont été créés récemment.

CE QU'ON NE PEUT PAS SAVOIR
    * **QUI**, au sens d'une personne. Au mieux : un nom de compte, un nom de
      machine, une adresse IP. Un intrus passant par un relais ou une machine
      compromise ne sera pas identifié par cet outil. L'attribution n'est pas
      à la portée d'un logiciel installé sur la machine visée.
    * **QUELS DOCUMENTS ONT ÉTÉ LUS.** C'est le point le plus contre-intuitif :
      sous Windows, lire un fichier ne laisse par défaut AUCUNE trace.
      L'audit d'accès aux objets est désactivé d'origine. Une consultation
      passée n'a jamais été enregistrée — elle est donc irrécupérable. On ne
      peut que commencer à l'enregistrer POUR L'AVENIR, ce que propose
      `preparer_audit_fichiers()`.

Et l'explication la plus fréquente n'est pas la plus dramatique : un logiciel
d'accès à distance installé volontairement (souvent lors d'une fausse
assistance téléphonique), ou un dossier synchronisé partagé avec quelqu'un.
Le module cherche donc ces causes-là en premier.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Sequence

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:                                   # pragma: no cover
    PSUTIL_AVAILABLE = False

__all__ = ["IntrusionCheck", "LOGICIELS_ACCES_DISTANT", "PORTS_ACCES_DISTANT",
           "TYPES_DE_CONNEXION"]


# ── Ce qu'on cherche ───────────────────────────────────────────────────────

# Logiciels d'accès à distance légitimes. Leur présence n'est PAS une
# infection : ce sont des outils honnêtes, largement utilisés. Mais ils sont
# aussi le vecteur numéro un des arnaques au support technique — quelqu'un
# appelle, fait installer l'un d'eux, et garde l'accès. D'où la question à
# poser à l'utilisateur : « l'as-tu installé toi-même, et sais-tu pourquoi ? »
LOGICIELS_ACCES_DISTANT = {
    "teamviewer.exe": "TeamViewer",
    "tv_w32.exe": "TeamViewer (service)",
    "tv_x64.exe": "TeamViewer (service)",
    "anydesk.exe": "AnyDesk",
    "rustdesk.exe": "RustDesk",
    "ultraviewer_desktop.exe": "UltraViewer",
    "aa_v3.exe": "Ammyy Admin",
    "supremo.exe": "Supremo",
    "winvnc.exe": "VNC (serveur)",
    "tvnserver.exe": "TightVNC (serveur)",
    "vncserver.exe": "VNC (serveur)",
    "screenconnect.clientservice.exe": "ScreenConnect / ConnectWise",
    "logmein.exe": "LogMeIn",
    "lmiguardiansvc.exe": "LogMeIn (service)",
    "splashtop.exe": "Splashtop",
    "srserver.exe": "Splashtop (service)",
    "dwagent.exe": "DWService",
    "remoting_host.exe": "Chrome Remote Desktop",
    "quickassist.exe": "Assistance rapide (Microsoft)",
    "rdpclip.exe": "Bureau à distance (presse-papiers)",
    "radmin.exe": "Radmin",
}

# Ports d'écoute correspondants. Une machine qui ÉCOUTE sur ces ports peut
# recevoir une connexion entrante.
PORTS_ACCES_DISTANT = {
    3389: "Bureau à distance (RDP)",
    5900: "VNC",
    5901: "VNC",
    5938: "TeamViewer",
    6568: "AnyDesk",
    7070: "AnyDesk",
    5931: "Ammyy Admin",
    4899: "Radmin",
}

# Types de connexion du journal de sécurité Windows (événement 4624).
TYPES_DE_CONNEXION = {
    2: "interactive (clavier de la machine)",
    3: "réseau (partage de fichiers, etc.)",
    4: "traitement par lot",
    5: "service",
    7: "déverrouillage de session",
    8: "réseau en clair",
    9: "nouvelles informations d'identification",
    10: "BUREAU À DISTANCE",
    11: "interactive avec identifiants en cache",
}

# Types qui traduisent un accès depuis l'extérieur de la machine.
TYPES_A_DISTANCE = {3, 8, 10}


@dataclass
class Constat:
    """Un élément observé, avec son niveau et son explication."""
    categorie: str
    niveau: str                    # "information" | "a_verifier" | "important"
    titre: str
    detail: str
    donnees: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {"categorie": self.categorie, "niveau": self.niveau,
                "titre": self.titre, "detail": self.detail,
                "donnees": self.donnees}


def _executer(commande: Sequence[str], timeout: int = 25) -> Dict:
    """Lance une commande système sans shell. Ne lève jamais."""
    try:
        p = subprocess.run(list(commande), capture_output=True, text=True,
                           timeout=timeout, shell=False)
        return {"code": p.returncode, "sortie": p.stdout or "",
                "erreur": p.stderr or ""}
    except FileNotFoundError:
        return {"code": -1, "sortie": "", "erreur": "commande introuvable"}
    except subprocess.TimeoutExpired:
        return {"code": -2, "sortie": "", "erreur": "délai dépassé"}
    except OSError as e:                              # pragma: no cover
        return {"code": -3, "sortie": "", "erreur": str(e)}


def _indisponible(raison: str) -> Dict:
    return {"ok": False, "unavailable": True, "reason": raison, "error": raison}


class IntrusionCheck:
    """Rassemble ce qui est réellement observable sur un accès non désiré."""

    def __init__(self, runner: Optional[Callable[..., Dict]] = None):
        # `runner` injectable : les commandes Windows ne peuvent pas tourner
        # ailleurs, on les remplace en test. Même convention que les autres
        # modules du projet.
        self._executer = runner or _executer

    # ── 1. Sessions ouvertes en ce moment ──────────────────────────────────
    def sessions_actives(self) -> Dict:
        """Sessions ouvertes sur la machine, locales et distantes.

        `quser` liste les sessions avec leur origine. Une session dont le nom
        commence par `rdp-tcp` est une connexion Bureau à distance.
        """
        r = self._executer(["quser"])
        if r["code"] != 0:
            # `quser` renvoie un code non nul quand aucune session n'est
            # listable — ce n'est pas forcément une panne.
            if "commande introuvable" in r["erreur"]:
                return _indisponible("quser indisponible (Windows uniquement)")
            return {"ok": True, "data": {"sessions": [], "note": r["erreur"].strip()}}

        sessions = []
        for ligne in r["sortie"].splitlines()[1:]:
            champs = ligne.split()
            if len(champs) < 3:
                continue
            utilisateur = champs[0].lstrip(">")
            nom_session = champs[1] if not champs[1].isdigit() else ""
            distante = nom_session.lower().startswith("rdp-tcp")
            sessions.append({
                "utilisateur": utilisateur,
                "session": nom_session,
                "distante": distante,
                "ligne": ligne.strip(),
            })
        return {"ok": True, "data": {"sessions": sessions,
                                     "distantes": sum(1 for s in sessions if s["distante"])}}

    # ── 2. Logiciels d'accès à distance ────────────────────────────────────
    def logiciels_acces_distant(self) -> Dict:
        """Logiciels d'accès à distance en cours d'exécution."""
        if not PSUTIL_AVAILABLE:
            return _indisponible("psutil absent")

        trouves = []
        for p in psutil.process_iter(["pid", "name", "exe", "create_time", "username"]):
            try:
                nom = (p.info.get("name") or "").lower()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if nom in LOGICIELS_ACCES_DISTANT:
                trouves.append({
                    "pid": p.info.get("pid"),
                    "nom": nom,
                    "produit": LOGICIELS_ACCES_DISTANT[nom],
                    "chemin": p.info.get("exe") or "",
                    "utilisateur": p.info.get("username") or "",
                    "demarre_le": self._horodatage(p.info.get("create_time")),
                })
        return {"ok": True, "data": {"logiciels": trouves, "total": len(trouves)}}

    @staticmethod
    def _horodatage(ts) -> str:
        try:
            return datetime.fromtimestamp(ts).isoformat(timespec="seconds")
        except (TypeError, ValueError, OSError):
            return ""

    # ── 3. Connexions entrantes ────────────────────────────────────────────
    def connexions_entrantes(self) -> Dict:
        """Connexions établies VERS cette machine.

        Une connexion est entrante si son port local figure parmi les ports sur
        lesquels la machine écoute : c'est un tiers qui s'est connecté à nous,
        et non l'inverse. Cette distinction est le cœur du module — une
        connexion sortante est banale, une connexion entrante ne l'est pas.
        """
        if not PSUTIL_AVAILABLE:
            return _indisponible("psutil absent")

        try:
            toutes = psutil.net_connections(kind="inet")
        except (psutil.AccessDenied, PermissionError):
            return _indisponible("droits insuffisants — relancer en administrateur "
                                 "pour voir les connexions de tous les processus")

        etabli = getattr(psutil, "CONN_ESTABLISHED", "ESTABLISHED")
        ecoute = getattr(psutil, "CONN_LISTEN", "LISTEN")

        ports_ecoutes = {c.laddr.port for c in toutes
                         if c.status == ecoute and c.laddr}

        entrantes, services_exposes = [], []

        for c in toutes:
            if c.status == ecoute and c.laddr and c.laddr.port in PORTS_ACCES_DISTANT:
                services_exposes.append({
                    "port": c.laddr.port,
                    "service": PORTS_ACCES_DISTANT[c.laddr.port],
                    "adresse": c.laddr.ip,
                    "pid": c.pid,
                    "processus": self._nom_du_processus(c.pid),
                })
            if c.status != etabli or not c.raddr or not c.laddr:
                continue
            if c.laddr.port not in ports_ecoutes:
                continue                     # sortante : hors sujet ici
            entrantes.append({
                "depuis": c.raddr.ip,
                "port_local": c.laddr.port,
                "service": PORTS_ACCES_DISTANT.get(c.laddr.port, ""),
                "pid": c.pid,
                "processus": self._nom_du_processus(c.pid),
            })

        return {"ok": True, "data": {
            "entrantes": entrantes,
            "services_exposes": services_exposes,
            "total_entrantes": len(entrantes),
        }}

    @staticmethod
    def _nom_du_processus(pid) -> str:
        if not pid or not PSUTIL_AVAILABLE:
            return "inconnu"
        try:
            return psutil.Process(pid).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return "inconnu"

    # ── 4. Journal de sécurité Windows ─────────────────────────────────────
    def journal_connexions(self, jours: int = 7, maximum: int = 200) -> Dict:
        """Connexions réussies et échouées, depuis le journal de sécurité.

        C'est la seule source RÉTROSPECTIVE du module : Windows enregistre ces
        événements par défaut. On y lit le compte utilisé, le type de connexion,
        la machine d'origine et l'adresse réseau.

        Exige les droits administrateur : le journal de sécurité n'est pas
        lisible autrement.
        """
        depuis = (datetime.now() - timedelta(days=jours)).strftime("%Y-%m-%dT%H:%M:%S")
        # Un seul appel PowerShell, filtré côté source : lire tout le journal
        # puis filtrer en Python serait très lent sur une machine ancienne.
        script = (
            "$ErrorActionPreference='Stop';"
            f"$d=[datetime]'{depuis}';"
            "$f=@{LogName='Security';Id=@(4624,4625,4778,4779,4720);StartTime=$d};"
            f"Get-WinEvent -FilterHashtable $f -MaxEvents {int(maximum)} 2>$null |"
            " ForEach-Object {"
            "  $x=[xml]$_.ToXml();"
            "  $h=@{};"
            "  foreach($n in $x.Event.EventData.Data){ $h[$n.Name]=$n.'#text' }"
            "  [pscustomobject]@{"
            "    Id=$_.Id; Date=$_.TimeCreated.ToString('s');"
            "    Compte=$h['TargetUserName']; Domaine=$h['TargetDomainName'];"
            "    Type=$h['LogonType']; Source=$h['WorkstationName'];"
            "    Adresse=$h['IpAddress']; Processus=$h['ProcessName'] }"
            " } | ConvertTo-Json -Compress"
        )
        r = self._executer(["powershell", "-NoProfile", "-NonInteractive",
                            "-Command", script], timeout=90)

        if r["code"] != 0 or not r["sortie"].strip():
            motif = (r["erreur"] or "").strip()
            if "commande introuvable" in motif:
                return _indisponible("PowerShell indisponible (Windows uniquement)")
            if "access" in motif.lower() or "refus" in motif.lower():
                return _indisponible("journal de sécurité illisible — "
                                     "relancer en administrateur")
            return {"ok": True, "data": {"evenements": [], "note":
                    motif or "aucun événement sur la période"}}

        try:
            brut = json.loads(r["sortie"])
        except ValueError:
            return {"ok": False, "error": "réponse du journal illisible",
                    "unavailable": False}
        if isinstance(brut, dict):
            brut = [brut]

        evenements, echecs, a_distance = [], 0, 0
        for e in brut:
            try:
                type_num = int(e.get("Type") or 0)
            except (TypeError, ValueError):
                type_num = 0
            ident = e.get("Id")
            distant = type_num in TYPES_A_DISTANCE or ident in (4778, 4779)
            if ident == 4625:
                echecs += 1
            if distant:
                a_distance += 1
            evenements.append({
                "id": ident,
                "date": e.get("Date") or "",
                "libelle": self._libelle(ident, type_num),
                "compte": e.get("Compte") or "",
                "domaine": e.get("Domaine") or "",
                "type": TYPES_DE_CONNEXION.get(type_num, f"type {type_num}"),
                "machine_source": e.get("Source") or "",
                "adresse": e.get("Adresse") or "",
                "a_distance": distant,
            })

        evenements.sort(key=lambda x: x["date"], reverse=True)
        return {"ok": True, "data": {
            "evenements": evenements,
            "total": len(evenements),
            "echecs_de_connexion": echecs,
            "connexions_a_distance": a_distance,
            "periode_jours": jours,
        }}

    @staticmethod
    def _libelle(ident, type_num: int) -> str:
        if ident == 4624:
            return ("connexion à distance" if type_num in TYPES_A_DISTANCE
                    else "connexion")
        return {4625: "ÉCHEC de connexion",
                4778: "session à distance reconnectée",
                4779: "session à distance déconnectée",
                4720: "COMPTE UTILISATEUR CRÉÉ"}.get(ident, f"événement {ident}")

    # ── 5. Comptes locaux ──────────────────────────────────────────────────
    def comptes_locaux(self) -> Dict:
        """Comptes locaux, avec leur date de création et leur dernier accès.

        Un compte créé récemment et inconnu de l'utilisateur est l'un des
        signaux les plus nets d'un accès persistant installé par un tiers.
        """
        script = (
            "$ErrorActionPreference='Stop';"
            "Get-LocalUser | ForEach-Object {"
            " [pscustomobject]@{ Nom=$_.Name; Actif=$_.Enabled;"
            "  Cree=if($_.PSObject.Properties['WhenCreated']){$_.WhenCreated}else{$null};"
            "  DerniereConnexion=$_.LastLogon; Description=$_.Description }"
            "} | ConvertTo-Json -Compress"
        )
        r = self._executer(["powershell", "-NoProfile", "-NonInteractive",
                            "-Command", script], timeout=45)
        if r["code"] != 0 or not r["sortie"].strip():
            if "commande introuvable" in (r["erreur"] or ""):
                return _indisponible("PowerShell indisponible (Windows uniquement)")
            return _indisponible("liste des comptes locaux illisible")

        try:
            brut = json.loads(r["sortie"])
        except ValueError:
            return {"ok": False, "error": "réponse illisible", "unavailable": False}
        if isinstance(brut, dict):
            brut = [brut]

        comptes = [{"nom": c.get("Nom") or "", "actif": bool(c.get("Actif")),
                    "cree_le": str(c.get("Cree") or ""),
                    "derniere_connexion": str(c.get("DerniereConnexion") or ""),
                    "description": c.get("Description") or ""} for c in brut]
        return {"ok": True, "data": {"comptes": comptes, "total": len(comptes)}}

    # ── 6. Rapport ─────────────────────────────────────────────────────────
    def rapport(self, jours: int = 7) -> Dict:
        """Assemble toutes les sources en constats lisibles.

        Une source indisponible n'empêche jamais les autres de répondre : c'est
        le principe de tout le projet, et il compte double ici — un utilisateur
        inquiet ne doit pas se retrouver devant un écran vide parce qu'une
        commande a échoué.
        """
        constats: List[Constat] = []
        sources: Dict[str, str] = {}

        def enregistrer(nom: str, res: Dict) -> Optional[Dict]:
            if res.get("ok"):
                sources[nom] = "ok"
                return res.get("data", {})
            sources[nom] = res.get("reason") or res.get("error") or "indisponible"
            return None

        # Sessions à distance ouvertes maintenant
        d = enregistrer("sessions", self.sessions_actives())
        if d:
            for s in d.get("sessions", []):
                if s["distante"]:
                    constats.append(Constat(
                        "session", "important",
                        f"Session Bureau à distance ouverte : {s['utilisateur']}",
                        "Quelqu'un est connecté à distance sur cette machine "
                        "EN CE MOMENT. Si ce n'est pas toi, c'est le constat le "
                        "plus urgent de ce rapport.", s))

        # Logiciels d'accès à distance
        d = enregistrer("logiciels", self.logiciels_acces_distant())
        if d:
            for lg in d.get("logiciels", []):
                constats.append(Constat(
                    "logiciel", "a_verifier",
                    f"Logiciel d'accès à distance actif : {lg['produit']}",
                    "Ce logiciel est légitime, mais il permet à un tiers de "
                    "prendre la main. Question à te poser : l'as-tu installé "
                    "toi-même, et sais-tu pourquoi il tourne ? C'est le vecteur "
                    "le plus courant des fausses assistances téléphoniques.", lg))

        # Connexions entrantes et services exposés
        d = enregistrer("connexions", self.connexions_entrantes())
        if d:
            for c in d.get("entrantes", []):
                constats.append(Constat(
                    "connexion", "important" if c["service"] else "a_verifier",
                    f"Connexion entrante depuis {c['depuis']}",
                    f"Un tiers est connecté à cette machine sur le port "
                    f"{c['port_local']}"
                    + (f" ({c['service']})." if c["service"] else ".")
                    + " Une connexion entrante n'est pas banale sur un poste "
                      "personnel.", c))
            for s in d.get("services_exposes", []):
                constats.append(Constat(
                    "service", "a_verifier",
                    f"{s['service']} est en écoute",
                    "La machine accepte des connexions entrantes pour ce "
                    "service. Si tu ne t'en sers pas, il vaut mieux le "
                    "désactiver : c'est une porte ouverte.", s))

        # Journal de sécurité
        d = enregistrer("journal", self.journal_connexions(jours=jours))
        if d:
            distants = [e for e in d.get("evenements", []) if e["a_distance"]]
            if distants:
                constats.append(Constat(
                    "journal", "important",
                    f"{len(distants)} connexion(s) à distance sur {jours} jours",
                    "Ces connexions viennent d'ailleurs que du clavier de la "
                    "machine. Vérifie les comptes et les dates : c'est la seule "
                    "information rétrospective fiable dont on dispose.",
                    {"evenements": distants[:20]}))
            echecs = d.get("echecs_de_connexion", 0)
            if echecs >= 10:
                constats.append(Constat(
                    "journal", "a_verifier",
                    f"{echecs} échecs de connexion sur {jours} jours",
                    "Un nombre élevé d'échecs peut trahir des tentatives "
                    "répétées de deviner un mot de passe.",
                    {"echecs": echecs}))
            for e in d.get("evenements", []):
                if e["id"] == 4720:
                    constats.append(Constat(
                        "compte", "important",
                        f"Compte créé récemment : {e['compte']}",
                        "La création d'un compte est un moyen classique de "
                        "garder un accès. Si tu ne l'as pas créé, c'est grave.",
                        e))

        # Comptes locaux
        d = enregistrer("comptes", self.comptes_locaux())
        if d:
            actifs = [c for c in d.get("comptes", []) if c["actif"]]
            constats.append(Constat(
                "compte", "information",
                f"{len(actifs)} compte(s) local(aux) actif(s)",
                "Passe la liste en revue : un compte que tu ne reconnais pas "
                "mérite une explication.", {"comptes": actifs}))

        ordre = {"important": 0, "a_verifier": 1, "information": 2}
        constats.sort(key=lambda c: ordre.get(c.niveau, 3))

        return {"ok": True, "data": {
            "constats": [c.to_dict() for c in constats],
            "importants": sum(1 for c in constats if c.niveau == "important"),
            "a_verifier": sum(1 for c in constats if c.niveau == "a_verifier"),
            "sources": sources,
            "avertissement": (
                "Ce rapport ne dit pas QUI, au sens d'une personne : il donne un "
                "compte, une machine, une adresse. Et il ne dit pas quels "
                "documents ont été lus — Windows ne l'enregistre pas par défaut. "
                "Pour que les consultations FUTURES soient tracées, active "
                "l'audit d'accès aux fichiers."),
        }}

    # ── 7. Tracer les accès futurs aux documents ───────────────────────────
    def preparer_audit_fichiers(self, dossiers: Sequence[str]) -> Dict:
        """Plan d'activation de l'audit d'accès aux fichiers. Ne modifie rien.

        C'est la seule réponse honnête à « quels documents ont été lus » : le
        passé n'a pas été enregistré et reste inaccessible ; l'avenir peut
        l'être.
        """
        dossiers = [d for d in dossiers if d]
        if not dossiers:
            return {"ok": False, "error": "aucun dossier indiqué",
                    "unavailable": False}
        return {"ok": True, "data": {
            "action": "activer_audit_fichiers",
            "dossiers": list(dossiers),
            "etapes": [
                "activer la stratégie d'audit « Accès aux objets » (auditpol)",
                "poser une règle d'audit sur chaque dossier indiqué",
                "les accès apparaîtront ensuite dans le journal de sécurité "
                "(événement 4663)",
            ],
            "reversible": True,
            "avertissements": [
                "N'ENREGISTRE RIEN DU PASSÉ : seuls les accès postérieurs à "
                "l'activation seront tracés.",
                "Le journal de sécurité grossit vite ; sur un dossier très "
                "utilisé, il peut devenir volumineux et écraser ses plus "
                "anciennes entrées.",
                "Exige les droits administrateur.",
            ],
        }}

    def activer_audit_fichiers(self, plan: Dict) -> Dict:
        """Applique le plan produit par `preparer_audit_fichiers()`."""
        dossiers = (plan or {}).get("dossiers") or []
        if not dossiers:
            return {"ok": False, "error": "plan vide ou invalide",
                    "unavailable": False}

        r = self._executer(["auditpol", "/set", "/subcategory:File System",
                            "/success:enable", "/failure:enable"], timeout=45)
        if r["code"] != 0:
            if "commande introuvable" in (r["erreur"] or ""):
                return _indisponible("auditpol indisponible (Windows uniquement)")
            return {"ok": False, "unavailable": False,
                    "error": "activation de la stratégie d'audit refusée — "
                             "droits administrateur nécessaires"}

        appliques, echecs = [], []
        for dossier in dossiers:
            script = (
                "$ErrorActionPreference='Stop';"
                f"$p='{self._echapper(dossier)}';"
                "$acl=Get-Acl -Path $p -Audit;"
                "$r=New-Object System.Security.AccessControl.FileSystemAuditRule("
                "'Everyone','Read','ContainerInherit,ObjectInherit','None','Success');"
                "$acl.AddAuditRule($r); Set-Acl -Path $p -AclObject $acl; 'ok'"
            )
            res = self._executer(["powershell", "-NoProfile", "-NonInteractive",
                                  "-Command", script], timeout=60)
            (appliques if res["code"] == 0 else echecs).append(dossier)

        return {"ok": bool(appliques), "data": {
            "dossiers_traces": appliques,
            "echecs": echecs,
            "rappel": "Seuls les accès À PARTIR DE MAINTENANT seront enregistrés "
                      "(événement 4663 du journal de sécurité).",
        }}

    @staticmethod
    def _echapper(chemin: str) -> str:
        """Échappe une apostrophe pour une chaîne PowerShell (doublement)."""
        return chemin.replace("'", "''")
