"""
telemetry.py — ce que la machine dit d'elle-même, en continu.

Un antivirus qui ne regarde que les fichiers est aveugle à la moitié des
symptômes. Un rançongiciel qui chiffre, un mineur de cryptomonnaie caché, une
fuite de mémoire : rien de tout cela n'a de signature, mais tout cela se voit
dans la charge du processeur, l'occupation de la mémoire et le remplissage du
disque. Ce module fournit cette vue, et rien d'autre : il **mesure**, il
n'interprète pas et il n'agit pas. L'interprétation est le travail de
`assistant/proactive.py`, l'action celui de l'utilisateur.

Deux contraintes ont dicté l'écriture :

* **Le tampon est borné.** Une surveillance lancée le lundi et oubliée
  jusqu'au lundi suivant ne doit pas finir par consommer la mémoire qu'elle
  est censée surveiller. D'où `collections.deque(maxlen=...)` : le plus vieil
  échantillon tombe tout seul, sans qu'aucune ligne de code n'ait à y penser.

* **L'absence de `psutil` n'est pas une panne.** ANTI-ZEEVIRIUS doit rester
  lançable sur une machine à désinfecter, où l'installation d'une dépendance
  peut être impossible. Sans `psutil`, la télémétrie se déclare indisponible
  et rend des échantillons à zéro — jamais une exception qui empêcherait
  l'analyse d'un fichier, qui, elle, n'a aucun besoin de savoir où en est le
  processeur.
"""

from __future__ import annotations

import dataclasses
import os
import threading
import time
from collections import deque
from typing import Deque, List, Optional, Tuple

__all__ = ["Echantillon", "Telemetrie", "volume_systeme"]


CAPACITE_PAR_DEFAUT = 720        # 1 h à 5 s d'intervalle
INTERVALLE_PAR_DEFAUT = 5.0


def volume_systeme() -> str:
    """Point de montage dont le remplissage compte vraiment.

    C'est le volume qui héberge Windows : c'est lui qui, une fois plein,
    empêche les mises à jour, la mise en quarantaine et le point de
    restauration. Un disque de données saturé est ennuyeux ; celui-ci est
    bloquant.
    """
    if os.name == "nt":
        return (os.environ.get("SystemDrive") or "C:") + "\\"
    return "/"


def _importer_psutil():
    """Import différé, et volontairement tolérant.

    Différé parce que l'absence de `psutil` doit se constater à la
    construction d'une `Telemetrie` et non à l'import du module : sinon un
    simple `import assistant.telemetry` déciderait pour toute la durée du
    processus, et le cas « psutil manquant » deviendrait intestable.
    """
    try:
        import psutil                                   # noqa: WPS433
        return psutil
    except Exception:       # ImportError, mais aussi un psutil mal installé
        return None


@dataclasses.dataclass(frozen=True)
class Echantillon:
    """Une photographie instantanée de la charge de la machine."""
    horodatage: float          # time.time()
    cpu: float                 # pourcentage 0-100
    memoire: float             # pourcentage 0-100
    disque: float              # pourcentage du volume système
    temperature: Optional[float]   # °C, None si la machine ne la publie pas

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


class Telemetrie:
    """Relevés périodiques de charge, conservés dans un tampon circulaire."""

    def __init__(self, capacite_historique: int = CAPACITE_PAR_DEFAUT):
        if capacite_historique < 1:
            # Un historique de taille nulle rendrait `moyennes()` toujours
            # vide : autant refuser franchement qu'observer un silence.
            raise ValueError("capacite_historique doit valoir au moins 1")

        self._ps = _importer_psutil()
        self.disponible: bool = self._ps is not None
        self._historique: Deque[Echantillon] = deque(maxlen=capacite_historique)
        self._verrou = threading.Lock()
        self._arret = threading.Event()
        self._fil: Optional[threading.Thread] = None

        # `cpu_percent` sans intervalle rend la charge écoulée DEPUIS l'appel
        # précédent ; le tout premier appel rend 0.0. On le consomme ici pour
        # que le premier échantillon utile porte une vraie valeur.
        if self._ps is not None:
            try:
                self._ps.cpu_percent(interval=None)
            except Exception:
                pass

    # ── Mesure ─────────────────────────────────────────────────────────────
    def echantillonner(self) -> Echantillon:
        """Prend un relevé, l'ajoute à l'historique et le rend.

        Chaque mesure est isolée des autres : une machine qui ne publie pas
        sa température ou qui refuse l'accès au volume système doit quand
        même livrer sa charge processeur.
        """
        e = Echantillon(
            horodatage=time.time(),
            cpu=self._mesurer(self._cpu),
            memoire=self._mesurer(self._memoire),
            disque=self._mesurer(self._disque),
            temperature=self._temperature(),
        )
        with self._verrou:
            self._historique.append(e)
        return e

    def historique(self) -> Tuple[Echantillon, ...]:
        """Les échantillons conservés, du plus ancien au plus récent."""
        with self._verrou:
            return tuple(self._historique)

    def moyennes(self, secondes: int) -> Optional[Echantillon]:
        """Moyenne des relevés des `secondes` dernières secondes.

        `None` quand la fenêtre est vide : rendre un échantillon à zéro
        laisserait croire à une machine au repos alors qu'on n'a simplement
        rien mesuré. Ces deux situations n'appellent pas la même réaction.
        """
        if secondes <= 0:
            return None
        limite = time.time() - secondes
        retenus = [e for e in self.historique() if e.horodatage >= limite]
        if not retenus:
            return None

        temperatures = [e.temperature for e in retenus if e.temperature is not None]
        return Echantillon(
            # L'horodatage est celui du relevé le plus récent de la fenêtre :
            # une moyenne datée de « maintenant » masquerait le fait qu'elle
            # peut ne porter que sur des mesures anciennes.
            horodatage=max(e.horodatage for e in retenus),
            cpu=sum(e.cpu for e in retenus) / len(retenus),
            memoire=sum(e.memoire for e in retenus) / len(retenus),
            disque=sum(e.disque for e in retenus) / len(retenus),
            temperature=(sum(temperatures) / len(temperatures)
                         if temperatures else None),
        )

    # ── Surveillance continue ──────────────────────────────────────────────
    def demarrer(self, intervalle: float = INTERVALLE_PAR_DEFAUT) -> None:
        """Lance les relevés périodiques en arrière-plan (sans effet si actif)."""
        if self._fil is not None and self._fil.is_alive():
            return
        if intervalle <= 0:
            raise ValueError("intervalle doit être strictement positif")

        self._arret.clear()

        def boucle():
            while not self._arret.is_set():
                try:
                    self.echantillonner()
                except Exception:           # pragma: no cover
                    # Une télémétrie qui meurt sur un relevé raté laisse
                    # croire à une machine au repos : on saute la mesure, on
                    # ne quitte pas la boucle.
                    pass
                self._arret.wait(intervalle)

        self._fil = threading.Thread(target=boucle, name="az-telemetrie",
                                     daemon=True)
        self._fil.start()

    def arreter(self) -> None:
        """Arrête les relevés. Appelable autant de fois qu'on veut."""
        self._arret.set()
        fil = self._fil
        if fil is not None:
            fil.join(timeout=5)
        self._fil = None

    @property
    def actif(self) -> bool:
        return bool(self._fil is not None and self._fil.is_alive())

    # ── Mesures unitaires ──────────────────────────────────────────────────
    @staticmethod
    def _mesurer(sonde) -> float:
        """Applique une sonde en garantissant un nombre utilisable.

        Sans `psutil`, ou si la sonde échoue, la valeur est zéro : c'est le
        prix à payer pour que l'absence de mesure ne remonte jamais sous
        forme d'exception jusqu'à l'appelant.
        """
        try:
            valeur = sonde()
        except Exception:
            return 0.0
        try:
            return float(valeur)
        except (TypeError, ValueError):
            return 0.0

    def _cpu(self) -> float:
        if self._ps is None:
            return 0.0
        # interval=None : mesure non bloquante. Un interval>0 endormirait le
        # fil appelant, et une télémétrie ne doit jamais faire attendre
        # l'interface.
        return self._ps.cpu_percent(interval=None)

    def _memoire(self) -> float:
        if self._ps is None:
            return 0.0
        return self._ps.virtual_memory().percent

    def _disque(self) -> float:
        if self._ps is None:
            return 0.0
        return self._ps.disk_usage(volume_systeme()).percent

    def _temperature(self) -> Optional[float]:
        """La plus chaude des sondes publiées, ou None.

        Windows n'expose presque jamais ces capteurs sans pilote
        constructeur : l'absence est le cas NORMAL, pas une anomalie. On
        retient le maximum car c'est la sonde la plus chaude qui déclenche
        les protections matérielles, pas la moyenne.
        """
        if self._ps is None:
            return None
        try:
            sondes = self._ps.sensors_temperatures()
        except Exception:
            return None
        if not sondes:
            return None

        valeurs: List[float] = []
        # `sensors_temperatures()` rend un dict : on trie les clés pour que
        # deux exécutions identiques lisent les sondes dans le même ordre.
        for nom in sorted(sondes):
            for entree in sondes[nom] or ():
                courante = getattr(entree, "current", None)
                if courante is None:
                    continue
                try:
                    valeurs.append(float(courante))
                except (TypeError, ValueError):
                    continue
        return max(valeurs) if valeurs else None
