"""
notify.py — prévenir une fois, au bon moment.

Ce module rassemble deux choses que le projet faisait jusqu'ici dans son coin,
au fond de `security/camera_watch.py` :

1. **la bulle Windows** — envoyée par PowerShell, sans dépendance
   supplémentaire, avec repli sur `msg` quand il n'y a pas de bureau ;
2. **la mémoire de ce qui a déjà été dit** — pour qu'une même alerte ne soit
   pas répétée tant que l'utilisateur ne l'a pas acquittée.

Le second point est le vrai sujet. Un gardien qui crie toutes les cinq
secondes n'est pas plus protecteur qu'un gardien muet : il est moins
protecteur, parce qu'on finit par ne plus le regarder, et c'est alors la
notification *utile* qui passe inaperçue. D'où l'identifiant stable calculé
sur `(titre, corps, source)` : deux alertes qui disent la même chose sont la
même alerte, et la seconde ne fait rien.

L'identifiant est un condensé SHA-256 tronqué, pas un compteur : il reste le
même d'un lancement à l'autre, ce qui permettra plus tard de reconnaître une
alerte déjà acquittée hier sans avoir à conserver l'ordre d'arrivée.

Hors Windows, l'envoi de bulle échoue proprement (`unavailable`) et le centre
continue de fonctionner : la liste des notifications reste consultable depuis
l'interface web, qui est de toute façon le canal principal.
"""

from __future__ import annotations

import dataclasses
import hashlib
import subprocess
import threading
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "Notification", "CentreNotifications",
    "identifiant_notification", "envoyer_bulle", "executer_commande",
    "GRAVITES",
]


# Vocabulaire fermé : une gravité inventée passerait inaperçue dans
# l'interface, et une alerte critique affichée comme une information est
# exactement le genre d'erreur qu'on ne remarque qu'après coup.
GRAVITES = ("info", "alerte", "critique")

CAPACITE_PAR_DEFAUT = 500          # au-delà, les plus vieilles sortent


def executer_commande(commande: Sequence[str], timeout: int = 45) -> Dict:
    """Lance un programme externe sans jamais lever.

    Les appelants de ce module sont des surveillances : elles doivent pouvoir
    traiter un échec comme une donnée (« PowerShell est absent ») et non comme
    un incident qui interrompt la boucle.
    """
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


def envoyer_bulle(titre: str, message: str,
                  executer: Optional[Callable[..., Dict]] = None) -> Dict:
    """Affiche une notification Windows.

    Passe par l'API de notifications de Windows via PowerShell, sans aucune
    dépendance supplémentaire. Si elle échoue — session sans bureau, mode
    présentation — on se rabat sur `msg`, puis on rend la main : la
    notification ne doit jamais faire échouer la surveillance.

    Les apostrophes sont doublées avant d'entrer dans le script : une
    application nommée « L'espion » refermerait sinon la chaîne PowerShell, et
    la suite du titre serait exécutée comme du code. Ce n'est pas une
    hypothèse d'école — le nom vient du registre, donc d'un tiers.
    """
    executer = executer or executer_commande
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
    r = executer(["powershell", "-NoProfile", "-NonInteractive",
                  "-Command", script], timeout=30)
    if r["code"] == 0:
        return {"ok": True, "data": {"methode": "notification Windows"}}

    secours = executer(["msg", "*", f"{titre} — {message}"], timeout=15)
    if secours["code"] == 0:
        return {"ok": True, "data": {"methode": "msg"}}

    return {"ok": False, "unavailable": True,
            "reason": "aucun moyen de notification disponible sur ce système",
            "error": "notification impossible"}


def identifiant_notification(titre: str, corps: str, source: str) -> str:
    """Condensé court et STABLE du contenu d'une notification.

    Le séparateur `\\x1f` (« unit separator ») ne peut pas apparaître dans un
    texte affichable : sans lui, ("ab", "c") et ("a", "bc") donneraient le
    même identifiant, et deux alertes différentes se masqueraient l'une
    l'autre.
    """
    brut = "\x1f".join((titre or "", corps or "", source or ""))
    return hashlib.sha256(brut.encode("utf-8")).hexdigest()[:16]


@dataclasses.dataclass(frozen=True)
class Notification:
    identifiant: str       # sha256 court, stable pour un même (titre, corps, source)
    horodatage: float
    gravite: str           # info | alerte | critique
    titre: str
    corps: str
    source: str            # module émetteur, ex. "camera_watch"
    acquittee: bool = False

    def to_dict(self) -> Dict:
        return dataclasses.asdict(self)


class CentreNotifications:
    """Journal des alertes, avec anti-répétition et envoi système optionnel."""

    def __init__(self, *, executer: Optional[Callable[..., Dict]] = None,
                 capacite: int = CAPACITE_PAR_DEFAUT):
        self._executer = executer
        self._capacite = max(1, int(capacite))
        self._notifications: List[Notification] = []
        # Les surveillances poussent depuis leurs propres fils ; sans verrou,
        # deux alertes simultanées pourraient franchir ensemble le test
        # d'anti-répétition et sonner deux fois.
        self._verrou = threading.RLock()
        self.derniere_bulle: Optional[Dict] = None

    def pousser(self, gravite: str, titre: str, corps: str, source: str,
                *, systeme: bool = True) -> Notification:
        """Enregistre une notification et, si demandé, l'affiche.

        Rend la notification existante — sans rien réafficher — quand la même
        alerte est déjà en attente d'acquittement.
        """
        if gravite not in GRAVITES:
            raise ValueError(f"gravité inconnue : {gravite!r} (attendu {GRAVITES})")

        identifiant = identifiant_notification(titre, corps, source)

        with self._verrou:
            for n in self._notifications:
                if n.identifiant == identifiant and not n.acquittee:
                    return n            # déjà dite, pas encore lue : on se tait

            notification = Notification(
                identifiant=identifiant, horodatage=time.time(),
                gravite=gravite, titre=titre, corps=corps, source=source,
            )
            self._notifications.append(notification)
            self._borner()

        if systeme:
            # Hors du verrou : l'appel à PowerShell peut durer des secondes et
            # ne doit pas bloquer les autres surveillances. Un échec n'empêche
            # pas la notification d'exister dans l'interface web.
            self.derniere_bulle = envoyer_bulle(titre, corps,
                                                executer=self._executer)
        return notification

    def lister(self, *, non_acquittees_seulement: bool = False
               ) -> Tuple[Notification, ...]:
        """Les notifications, la plus récente d'abord."""
        with self._verrou:
            retenues = [n for n in self._notifications
                        if not (non_acquittees_seulement and n.acquittee)]
        retenues.reverse()
        return tuple(retenues)

    def acquitter(self, identifiant: str) -> bool:
        """Marque une notification comme lue. Faux si rien n'a changé."""
        with self._verrou:
            for i, n in enumerate(self._notifications):
                if n.identifiant == identifiant and not n.acquittee:
                    self._notifications[i] = dataclasses.replace(n, acquittee=True)
                    return True
        return False

    def purger(self, avant: float) -> int:
        """Oublie les notifications antérieures à `avant`. Rend le nombre ôté."""
        with self._verrou:
            gardees = [n for n in self._notifications if n.horodatage >= avant]
            otees = len(self._notifications) - len(gardees)
            self._notifications = gardees
        return otees

    def _borner(self) -> None:
        """Empêche le journal de grossir indéfiniment.

        Le centre vit aussi longtemps que l'application ; sans borne, une
        surveillance bavarde finirait par occuper plus de place que ce qu'elle
        surveille. Les plus anciennes partent d'abord — ce sont aussi les
        moins actionnables.
        """
        if len(self._notifications) > self._capacite:
            del self._notifications[:len(self._notifications) - self._capacite]
