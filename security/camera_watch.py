"""
camera_watch.py — qui allume la caméra, et quand.

Windows tient lui-même le registre de chaque application ayant accédé à la
caméra ou au microphone : c'est ce qui alimente le petit indicateur de
confidentialité de la barre des tâches. Nous lisons la MÊME source, sous
`CapabilityAccessManager\\ConsentStore`, ce qui donne une information
officielle plutôt qu'une devinette.

    ConsentStore\\webcam\\<application>
        LastUsedTimeStart   date de début d'utilisation (FILETIME)
        LastUsedTimeStop    date de fin — **0 si l'accès est EN COURS**

Cette valeur à zéro est la clé de tout le module : elle dit qu'une application
tient la caméra *en ce moment*.

CE QUE ÇA PERMET
    * savoir quelles applications utilisent la caméra ou le micro maintenant ;
    * savoir lesquelles s'en sont servies récemment, même brièvement — une
      capture d'une seconde laisse sa date de début, donc rien n'échappe à la
      surveillance par relevés successifs ;
    * être **notifié** quand une application non autorisée l'active.

CE QUE ÇA NE PERMET PAS
    * Dire QUI regarde, au sens d'une personne. On identifie l'APPLICATION qui
      a ouvert la caméra, pas l'humain derrière.
    * Détecter un accès qui contournerait la couche caméra de Windows. Un
      pilote malveillant ou un rootkit noyau peut parler directement au
      matériel sans passer par cette comptabilité. C'est la limite structurelle
      de tout outil sans composant noyau, déjà assumée par le projet.
    * Remplacer le voyant matériel. Sur la plupart des portables, la diode est
      câblée sur l'alimentation du capteur : si elle s'allume, la caméra
      filme, quoi qu'affiche un logiciel. C'est le témoin le plus fiable —
      plus que ce module.

Et l'explication la plus fréquente n'est pas un espion : c'est une application
de visioconférence laissée ouverte en arrière-plan. Le module est donc conçu
pour distinguer l'autorisé de l'inattendu, pas pour crier au loup.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Sequence

import paths

__all__ = ["CameraWatch", "Acces", "APPAREILS", "FILETIME_EPOCH"]


APPAREILS = {"webcam": "caméra", "microphone": "microphone"}

# Le FILETIME de Windows compte les intervalles de 100 ns depuis le
# 1er janvier 1601. L'écart avec l'époque Unix vaut 11 644 473 600 secondes.
FILETIME_EPOCH = 11_644_473_600
FILETIME_PAR_SECONDE = 10_000_000

INTERVALLE_PAR_DEFAUT = 5.0          # secondes entre deux relevés


def _filetime_vers_datetime(ft) -> Optional[datetime]:
    try:
        ft = int(ft)
    except (TypeError, ValueError):
        return None
    if ft <= 0:
        return None
    try:
        return datetime.fromtimestamp(ft / FILETIME_PAR_SECONDE - FILETIME_EPOCH)
    except (OverflowError, OSError, ValueError):
        return None


def _nom_lisible(cle: str) -> str:
    """Rend présentable un nom de clé du registre.

    Les applications de bureau sont enregistrées sous `NonPackaged` avec leur
    chemin complet, les contre-obliques remplacées par des `#` :
    `C:#Program Files#Zoom#bin#Zoom.exe`. Les applications du Store portent
    leur nom de famille de paquet.
    """
    chemin = cle.replace("#", "\\")
    if "\\" in chemin:
        return chemin.rsplit("\\", 1)[-1]
    if "_" in chemin:
        return chemin.split("_", 1)[0]           # Microsoft.Teams_8wek… → Microsoft.Teams
    return chemin


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
    except OSError as e:                              # pragma: no cover
        return {"code": -3, "sortie": "", "erreur": str(e)}


@dataclass
class Acces:
    """Une application et son usage d'un appareil."""
    appareil: str                 # "webcam" | "microphone"
    cle: str                      # identifiant brut du registre
    application: str              # nom lisible
    chemin: str
    en_cours: bool
    debut: Optional[str]
    fin: Optional[str]
    autorisee: bool = False

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["appareil_lisible"] = APPAREILS.get(self.appareil, self.appareil)
        return d


class CameraWatch:
    """Surveille l'usage de la caméra et du microphone."""

    def __init__(self, runner: Optional[Callable[..., Dict]] = None,
                 fichier_autorisations: Optional[str] = None):
        self._executer = runner or _executer
        self._fichier = (paths.data_path("camera", "autorisations.json")
                         if fichier_autorisations is None
                         else __import__("pathlib").Path(fichier_autorisations))
        self._arret = threading.Event()
        self._fil: Optional[threading.Thread] = None
        self._vus: Dict[str, str] = {}      # clé -> date de début déjà signalée

    # ── Applications autorisées ────────────────────────────────────────────
    def autorisations(self) -> List[str]:
        """Applications que l'utilisateur a déclarées légitimes."""
        try:
            data = json.loads(self._fichier.read_text(encoding="utf-8"))
            return [str(x) for x in data.get("autorisees", [])]
        except (OSError, ValueError):
            return []

    def autoriser(self, application: str) -> Dict:
        """Déclare une application légitime : elle ne déclenchera plus d'alerte."""
        application = (application or "").strip()
        if not application:
            return {"ok": False, "error": "nom d'application vide",
                    "unavailable": False}
        liste = self.autorisations()
        if application.lower() not in {a.lower() for a in liste}:
            liste.append(application)
        return self._ecrire(liste)

    def retirer_autorisation(self, application: str) -> Dict:
        liste = [a for a in self.autorisations()
                 if a.lower() != (application or "").strip().lower()]
        return self._ecrire(liste)

    def _ecrire(self, liste: List[str]) -> Dict:
        try:
            self._fichier.parent.mkdir(parents=True, exist_ok=True)
            temporaire = self._fichier.with_suffix(".tmp")
            temporaire.write_text(json.dumps({"autorisees": liste}, indent=2,
                                             ensure_ascii=False), encoding="utf-8")
            temporaire.replace(self._fichier)
            return {"ok": True, "data": {"autorisees": liste}}
        except OSError as e:
            return {"ok": False, "error": f"écriture impossible : {e}",
                    "unavailable": False}

    # ── Lecture du registre de consentement ────────────────────────────────
    def _lire_consentstore(self, appareil: str) -> Dict:
        """Lit ConsentStore pour un appareil, dans HKCU puis HKLM.

        HKCU couvre l'utilisateur courant, HKLM les accès à l'échelle de la
        machine — dont les services. Un logiciel espion installé en service
        n'apparaîtrait que dans le second.
        """
        script = (
            "$ErrorActionPreference='SilentlyContinue';"
            "$out=@();"
            f"foreach($racine in @('HKCU:','HKLM:')){{"
            f"  $base=\"$racine\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion"
            f"\\CapabilityAccessManager\\ConsentStore\\{appareil}\";"
            "  if(-not (Test-Path $base)){continue}"
            "  $cles=@(Get-ChildItem $base -ErrorAction SilentlyContinue);"
            "  $cles+=@(Get-ChildItem \"$base\\NonPackaged\" -ErrorAction SilentlyContinue);"
            "  foreach($c in $cles){"
            "    if($c.PSChildName -eq 'NonPackaged'){continue}"
            "    $p=Get-ItemProperty $c.PSPath -ErrorAction SilentlyContinue;"
            "    $out+=[pscustomobject]@{"
            "      Cle=$c.PSChildName; Racine=$racine;"
            "      Debut=$p.LastUsedTimeStart; Fin=$p.LastUsedTimeStop }"
            "  }"
            "}"
            "$out | ConvertTo-Json -Compress"
        )
        return self._executer(["powershell", "-NoProfile", "-NonInteractive",
                               "-Command", script])

    def etat(self, appareils: Sequence[str] = ("webcam", "microphone")) -> Dict:
        """Qui utilise la caméra / le micro, maintenant et récemment."""
        autorisees = {a.lower() for a in self.autorisations()}
        acces: List[Acces] = []
        sources: Dict[str, str] = {}

        for appareil in appareils:
            r = self._lire_consentstore(appareil)
            if r["code"] != 0 or not r["sortie"].strip():
                motif = (r["erreur"] or "").strip()
                sources[appareil] = ("PowerShell indisponible (Windows uniquement)"
                                     if "commande introuvable" in motif
                                     else motif or "aucune donnée")
                continue
            try:
                brut = json.loads(r["sortie"])
            except ValueError:
                sources[appareil] = "réponse du registre illisible"
                continue
            if isinstance(brut, dict):
                brut = [brut]
            sources[appareil] = "ok"

            for e in brut:
                cle = str(e.get("Cle") or "")
                if not cle:
                    continue
                debut = _filetime_vers_datetime(e.get("Debut"))
                fin_brute = e.get("Fin")
                # LastUsedTimeStop à 0 avec un début renseigné = accès EN COURS.
                # C'est la même règle que celle utilisée par Windows pour son
                # indicateur de confidentialité.
                en_cours = bool(debut) and (fin_brute in (0, "0", None))
                nom = _nom_lisible(cle)
                acces.append(Acces(
                    appareil=appareil, cle=cle, application=nom,
                    chemin=cle.replace("#", "\\"),
                    en_cours=en_cours,
                    debut=debut.isoformat(timespec="seconds") if debut else None,
                    fin=(_filetime_vers_datetime(fin_brute).isoformat(timespec="seconds")
                         if _filetime_vers_datetime(fin_brute) else None),
                    autorisee=nom.lower() in autorisees,
                ))

        acces.sort(key=lambda a: (not a.en_cours, a.debut or ""), reverse=False)
        en_cours = [a for a in acces if a.en_cours]

        return {"ok": True, "data": {
            "acces": [a.to_dict() for a in acces],
            "en_cours": [a.to_dict() for a in en_cours],
            "alertes": [a.to_dict() for a in en_cours if not a.autorisee],
            "sources": sources,
            "autorisees": sorted(autorisees),
            "rappel": (
                "Sur la plupart des portables, la diode de la caméra est câblée "
                "sur l'alimentation du capteur : si elle s'allume, la caméra "
                "filme, quel que soit ce qu'affiche un logiciel. C'est le témoin "
                "le plus fiable."),
        }}

    def utilisations_recentes(self, heures: int = 24) -> Dict:
        """Accès des dernières heures, y compris ceux déjà terminés.

        Une capture d'une seconde laisse sa date de début dans le registre :
        elle reste donc visible longtemps après, ce qui rattrape ce qu'un
        relevé périodique aurait pu manquer.
        """
        res = self.etat()
        if not res.get("ok"):
            return res
        limite = datetime.now() - timedelta(hours=heures)
        recents = []
        for a in res["data"]["acces"]:
            if not a["debut"]:
                continue
            try:
                if datetime.fromisoformat(a["debut"]) >= limite:
                    recents.append(a)
            except ValueError:
                continue
        recents.sort(key=lambda a: a["debut"], reverse=True)
        return {"ok": True, "data": {"acces": recents, "total": len(recents),
                                     "periode_heures": heures,
                                     "sources": res["data"]["sources"]}}

    # ── Notification ───────────────────────────────────────────────────────
    def notifier(self, titre: str, message: str) -> Dict:
        """Affiche une notification Windows.

        Passe par l'API de notifications de Windows via PowerShell, sans aucune
        dépendance supplémentaire. Si elle échoue — session sans bureau, mode
        présentation — on se rabat sur `msg`, puis on rend la main : la
        notification ne doit jamais faire échouer la surveillance.
        """
        t = titre.replace("'", "''")
        m = message.replace("'", "''")
        script = (
            "$ErrorActionPreference='Stop';"
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
            " ContentType=WindowsRuntime] > $null;"
            "$modele=[Windows.UI.Notifications.ToastNotificationManager]::"
            "GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
            "$textes=$modele.GetElementsByTagName('text');"
            f"$textes.Item(0).AppendChild($modele.CreateTextNode('{t}')) > $null;"
            f"$textes.Item(1).AppendChild($modele.CreateTextNode('{m}')) > $null;"
            "$toast=[Windows.UI.Notifications.ToastNotification]::new($modele);"
            "[Windows.UI.Notifications.ToastNotificationManager]::"
            "CreateToastNotifier('ANTI-ZEEVIRIUS').Show($toast)"
        )
        r = self._executer(["powershell", "-NoProfile", "-NonInteractive",
                            "-Command", script], timeout=30)
        if r["code"] == 0:
            return {"ok": True, "data": {"methode": "notification Windows"}}

        secours = self._executer(["msg", "*", f"{titre} — {message}"], timeout=15)
        if secours["code"] == 0:
            return {"ok": True, "data": {"methode": "msg"}}

        return {"ok": False, "unavailable": True,
                "reason": "aucun moyen de notification disponible sur ce système",
                "error": "notification impossible"}

    # ── Surveillance continue ──────────────────────────────────────────────
    def surveiller(self, rappel: Optional[Callable[[Dict], None]] = None,
                   intervalle: float = INTERVALLE_PAR_DEFAUT,
                   notifier: bool = True) -> Dict:
        """Démarre la surveillance en arrière-plan.

        Alerte à chaque NOUVELLE activation par une application non autorisée.
        Une même session d'utilisation ne notifie qu'une fois : sans cette
        mémoire, une visioconférence d'une heure produirait une alerte toutes
        les cinq secondes, et l'utilisateur cesserait de les lire.
        """
        if self._fil and self._fil.is_alive():
            return {"ok": True, "data": {"deja_active": True}}

        self._arret.clear()

        def boucle():
            while not self._arret.is_set():
                try:
                    res = self.etat()
                    if res.get("ok"):
                        for a in res["data"]["alertes"]:
                            signature = f"{a['appareil']}:{a['cle']}:{a['debut']}"
                            if self._vus.get(a["cle"]) == signature:
                                continue          # déjà signalé pour cette session
                            self._vus[a["cle"]] = signature
                            if notifier:
                                self.notifier(
                                    f"{APPAREILS.get(a['appareil'], a['appareil']).capitalize()} activée",
                                    f"{a['application']} utilise votre "
                                    f"{APPAREILS.get(a['appareil'], a['appareil'])} "
                                    f"sans autorisation déclarée.")
                            if rappel:
                                rappel(a)
                        # Oublie les applications qui ont relâché l'appareil,
                        # pour qu'une nouvelle session soit bien re-signalée.
                        actives = {a["cle"] for a in res["data"]["en_cours"]}
                        for cle in list(self._vus):
                            if cle not in actives:
                                del self._vus[cle]
                except Exception:                 # pragma: no cover
                    # Une surveillance qui meurt sur une erreur ponctuelle est
                    # pire qu'inutile : elle laisse croire qu'on est protégé.
                    pass
                self._arret.wait(intervalle)

        self._fil = threading.Thread(target=boucle, name="az-camera", daemon=True)
        self._fil.start()
        return {"ok": True, "data": {"actif": True, "intervalle": intervalle}}

    def arreter(self) -> Dict:
        self._arret.set()
        if self._fil:
            self._fil.join(timeout=5)
        self._fil = None
        return {"ok": True, "data": {"actif": False}}

    @property
    def surveillance_active(self) -> bool:
        return bool(self._fil and self._fil.is_alive())
