"""
assistant/autonomy.py — Mode autonome (section 9 du contrat gelé).

Ce module est le seul endroit du projet où ANTI-ZEEVIRIUS agit sans qu'un
humain ait cliqué. C'est donc le seul endroit où une erreur de conception se
paie en fichiers perdus, et il est écrit en conséquence : la liste de ce
qu'il a le droit de faire est fermée, et tout le reste sort par la porte des
suggestions.

Les cinq limites du contrat, et la raison de chacune :

1. **Seul le niveau `Risque.LECTURE` est exécuté.** Un assistant qui range,
   déplace ou supprime « pour rendre service » est un échec du projet : la
   personne qui retrouve son bureau vide n'a aucun moyen de savoir ce qui
   s'est passé. Le filtre est appliqué deux fois (niveau exact ET
   `exige_confirmation`), et la capacité est relue dans le registre juste
   avant l'appel : une suggestion ne peut pas désigner autre chose que ce
   que le registre déclare.
2. **Budget d'actions par cycle.** Une boucle qui s'emballe consomme la
   machine qu'elle est censée protéger. Au-delà du budget le cycle s'arrête
   et le journalise : un arrêt silencieux serait indiscernable d'une panne.
3. **Journalisation AVANT exécution.** L'entrée « engagée » est écrite avant
   l'appel et n'est jamais réécrite après : si le processus meurt pendant
   l'action, la trace de ce qui était en cours survit. Le résultat est une
   seconde entrée, pas une correction de la première.
4. **`arreter()` prend effet avant l'action suivante.** L'état d'arrêt est
   relu au sommet de chaque itération, pas en fin de cycle : quelqu'un qui
   coupe le mode autonome ne doit pas assister, impuissant, à la fin d'une
   série d'actions déjà planifiées.
5. **Tout ce qui dépasse `LECTURE` devient une suggestion.** Présentée, donc
   visible, donc refusable — jamais exécutée.

Aucune dépendance à `gui.*` : l'exécution réelle d'une capacité est fournie
par l'appelant (`executeur`). Le pont web injecte la sienne ; les tests
injectent la leur. Sans exécuteur, le mode autonome observe et propose, ce
qui reste un comportement utile et parfaitement sûr.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from .risk import Risque, exige_confirmation

__all__ = ["ModeAutonome"]


# Intervalle par défaut entre deux cycles. Cinq minutes : assez rare pour
# rester invisible sur la charge machine, assez fréquent pour qu'une alerte
# de sécurité ne dorme pas une demi-journée.
INTERVALLE_PAR_DEFAUT = 300.0

# Taille maximale du journal conservé en mémoire. Le mode autonome peut
# tourner des semaines : un journal non borné finirait par peser plus lourd
# que tout le reste de l'application.
JOURNAL_MAX = 500


def _suggereur_par_defaut(etat: Dict, memoire, registre):
    """Appelle `assistant.proactive.suggerer` — importé à l'usage.

    L'import est paresseux pour que `autonomy` reste importable, et donc
    testable, même si le module d'initiative n'est pas encore présent : un
    module manquant produit « aucune suggestion », jamais une exception au
    chargement du pont web.
    """
    try:
        from . import proactive
    except Exception:
        return ()
    try:
        return tuple(proactive.suggerer(etat, memoire, registre) or ())
    except Exception:
        # Une initiative qui plante ne doit pas emporter la surveillance :
        # le cycle continue, sans suggestion, et le journal le dira.
        return ()


class ModeAutonome:
    """Surveillance qui agit d'elle-même — dans les seules limites ci-dessus.

    Le cycle est public (`executer_cycle`) et synchrone : il se teste sans
    démarrer le moindre fil d'exécution, ce qui évite d'écrire des tests qui
    dorment et deviennent instables sur une machine chargée.
    """

    def __init__(
        self,
        registre,
        memoire,
        centre,
        *,
        budget: int = 12,
        executeur: Optional[Callable[[Any, Dict], Any]] = None,
        source_etat: Optional[Callable[[], Dict]] = None,
        suggereur: Optional[Callable[[Dict, Any, Any], Tuple]] = None,
        intervalle: float = INTERVALLE_PAR_DEFAUT,
    ) -> None:
        self.registre = registre
        self.memoire = memoire
        self.centre = centre
        # Un budget nul ou négatif n'a pas de sens : il rendrait le mode
        # autonome inerte tout en le présentant comme actif.
        self.budget = max(1, int(budget))
        self.intervalle = max(1.0, float(intervalle))

        self._executeur = executeur
        self._source_etat = source_etat or (lambda: {})
        self._suggereur = suggereur or _suggereur_par_defaut

        self._journal: list = []
        self._lock = threading.RLock()
        # L'événement porte la DEMANDE d'arrêt, pas l'état « éteint » : un
        # cycle lancé à la main (le cas des tests, et d'un futur bouton
        # « passer un tour maintenant ») doit pouvoir travailler sans qu'on
        # ait démarré la boucle. C'est `_fil` qui dit si la veille tourne.
        self._arret = threading.Event()
        self._fil: Optional[threading.Thread] = None
        self._cycles = 0

    # ── Cycle de vie ─────────────────────────────────────────────
    def demarrer(self) -> None:
        """Lance la boucle de surveillance. Idempotent."""
        with self._lock:
            if self._fil is not None and self._fil.is_alive():
                return
            self._arret.clear()
            self._fil = threading.Thread(
                target=self._boucle, name="az-mode-autonome", daemon=True
            )
            self._fil.start()
        self._noter(
            "demarrage",
            {"motif": "mode autonome activé par l'utilisateur",
             "budget": self.budget, "intervalle": self.intervalle},
        )

    def arreter(self) -> None:
        """Coupe la boucle. Idempotent, et immédiat.

        L'`Event` est posé avant toute autre chose : même si la boucle est en
        train de traiter un cycle, la prochaine action ne partira pas. On ne
        rejoint le fil qu'avec un délai borné — un exécuteur bloqué ne doit
        pas figer l'interface qui demande l'arrêt.
        """
        with self._lock:
            deja_arrete = self._fil is None
            self._arret.set()
            fil = self._fil
            self._fil = None
        if fil is not None and fil.is_alive() and fil is not threading.current_thread():
            fil.join(timeout=2.0)
        if not deja_arrete:
            self._noter("arret", {"motif": "mode autonome désactivé"})

    def actif(self) -> bool:
        if self._arret.is_set():
            return False
        with self._lock:
            return self._fil is not None and self._fil.is_alive()

    def journal(self) -> Tuple[Dict, ...]:
        """Journal complet, du plus ancien au plus récent (copie figée)."""
        with self._lock:
            return tuple(dict(e) for e in self._journal)

    def etat(self) -> Dict:
        """Photographie lisible par l'interface web."""
        with self._lock:
            cycles = self._cycles
            dernieres = [dict(e) for e in self._journal[-20:]]
        return {
            "actif": self.actif(),
            "budget": self.budget,
            "intervalle": self.intervalle,
            "cycles": cycles,
            "executeur": self._executeur is not None,
            "journal": dernieres,
        }

    # ── Un cycle ─────────────────────────────────────────────────
    def executer_cycle(self) -> Dict:
        """Un tour complet : observer, trier, agir dans les limites, proposer.

        Public et synchrone : c'est la surface que testent les tests des cinq
        limites, sans fil d'exécution ni attente.
        """
        with self._lock:
            self._cycles += 1

        try:
            etat = self._source_etat() or {}
        except Exception as exc:
            self._noter("erreur", {"motif": f"état de la machine illisible : {exc}"})
            etat = {}

        suggestions = self._suggereur(etat, self.memoire, self.registre) or ()

        agies = 0
        proposees: list = []
        budget_atteint = False
        interrompu = False

        for suggestion in suggestions:
            # Limite 4 : relu AVANT chaque action, pas en fin de cycle.
            if self._arret.is_set():
                interrompu = True
                self._noter("interruption",
                            {"motif": "arrêt demandé — cycle abandonné avant l'action suivante"})
                break

            # Limite 2 : au-delà du budget, on s'arrête et on le dit.
            if agies >= self.budget:
                budget_atteint = True
                self._noter("budget", {
                    "motif": f"budget de {self.budget} action(s) par cycle atteint",
                    "restantes": max(0, len(suggestions) - agies),
                })
                break

            nom = getattr(suggestion, "capacite", "") or ""
            motif = getattr(suggestion, "motif", "") or "sans motif déclaré"
            urgence = int(getattr(suggestion, "urgence", 0) or 0)

            capacite = self.registre.obtenir(nom) if nom else None
            if capacite is None:
                self._noter("inconnue", {
                    "capacite": nom, "motif": motif,
                    "raison": "capacité absente du registre — rien n'est tenté",
                })
                continue

            if not self._autorisee(capacite):
                # Limite 5 : ce qui dépasse LECTURE ne devient jamais une
                # action, seulement une proposition visible.
                proposees.append(self._proposer(capacite, motif, urgence))
                continue

            # Limite 3 : la trace précède l'acte.
            self._noter("action", {
                "capacite": capacite.nom,
                "titre": getattr(capacite, "titre", capacite.nom),
                "risque": int(capacite.risque),
                "motif": motif,
                "urgence": urgence,
                "etape": "engagee",
            })
            agies += 1
            self._executer(capacite, motif)

        return {
            "cycle": self._cycles,
            "suggestions_recues": len(suggestions),
            "actions": agies,
            "proposees": proposees,
            "budget_atteint": budget_atteint,
            "interrompu": interrompu,
        }

    # ── Garde-fous ───────────────────────────────────────────────
    def _autorisee(self, capacite) -> bool:
        """Double vérification du niveau de risque.

        Les deux conditions disent la même chose aujourd'hui. Elles sont
        maintenues ensemble volontairement : si un jour `exige_confirmation`
        est assouplie, l'égalité stricte à `LECTURE` continue d'interdire
        toute écriture automatique, et inversement.
        """
        try:
            niveau = Risque(capacite.risque)
        except (ValueError, TypeError):
            return False
        if niveau != Risque.LECTURE:
            return False
        return not exige_confirmation(niveau, True)

    def _proposer(self, capacite, motif: str, urgence: int) -> Dict:
        """Transforme une capacité trop risquée en proposition à l'utilisateur."""
        proposition = {
            "capacite": capacite.nom,
            "titre": getattr(capacite, "titre", capacite.nom),
            "risque": int(capacite.risque),
            "motif": motif,
            "urgence": urgence,
        }
        self._noter("suggestion", dict(proposition, raison=(
            "niveau supérieur à LECTURE — présentée à l'utilisateur, jamais exécutée"
        )))
        # Une suggestion de sécurité immédiate mérite une bulle ; le confort
        # attendra que la personne ouvre le Dashboard. Le centre de
        # notifications gère lui-même l'anti-répétition.
        try:
            self.centre.pousser(
                "alerte" if urgence >= 2 else "info",
                f"Action proposée : {proposition['titre']}",
                f"{motif} — ANTI-ZEEVIRIUS ne l'exécutera pas seul : "
                f"cette action demande votre validation.",
                "autonomy",
                systeme=urgence >= 3,
            )
        except Exception as exc:
            self._noter("erreur", {"motif": f"notification impossible : {exc}"})
        return proposition

    def _executer(self, capacite, motif: str) -> None:
        """Appelle l'exécuteur injecté, en contenant toute défaillance.

        Le registre est relu une dernière fois ici : entre la construction de
        la suggestion et cet appel, rien ne garantit que la capacité désignée
        est toujours celle qu'on croit. C'est bon marché, et c'est la seule
        vérification qui porte sur l'objet réellement transmis.
        """
        courante = self.registre.obtenir(capacite.nom)
        if courante is None or not self._autorisee(courante):
            self._noter("refus", {
                "capacite": capacite.nom,
                "motif": motif,
                "raison": "le registre ne déclare plus cette capacité en LECTURE",
            })
            return

        if self._executeur is None:
            self._noter("resultat", {
                "capacite": courante.nom,
                "etape": "non_executee",
                "raison": "aucun exécuteur branché — observation seule",
            })
            return

        debut = time.time()
        try:
            # Aucun paramètre n'est inventé : le mode autonome n'exécute que
            # des capacités de lecture, qui doivent savoir tourner sans
            # argument. Deviner une cible serait déjà décider à la place de
            # l'utilisateur.
            resultat = self._executeur(courante, {})
        except Exception as exc:
            self._noter("resultat", {
                "capacite": courante.nom,
                "etape": "echec",
                "raison": f"{type(exc).__name__}: {exc}",
                "duree": round(time.time() - debut, 3),
            })
            return

        self._noter("resultat", {
            "capacite": courante.nom,
            "etape": "terminee",
            "ok": bool(isinstance(resultat, dict) and resultat.get("ok", True)) if isinstance(resultat, dict) else True,
            "duree": round(time.time() - debut, 3),
        })

    # ── Boucle et journal ────────────────────────────────────────
    def _boucle(self) -> None:
        while not self._arret.is_set():
            try:
                self.executer_cycle()
            except Exception as exc:
                # Un cycle qui explose ne doit pas tuer la surveillance :
                # sinon une seule anomalie transitoire désarme le gardien
                # sans que personne ne s'en aperçoive.
                self._noter("erreur", {"motif": f"cycle interrompu : {type(exc).__name__}: {exc}"})
            # `wait` rend la main dès que `arreter()` pose l'événement : pas
            # d'attente résiduelle de plusieurs minutes après une demande
            # d'arrêt.
            self._arret.wait(self.intervalle)

    def _noter(self, type_entree: str, detail: Dict) -> None:
        entree = dict(detail)
        entree["type"] = type_entree
        entree["horodatage"] = time.time()
        with self._lock:
            self._journal.append(entree)
            if len(self._journal) > JOURNAL_MAX:
                del self._journal[: len(self._journal) - JOURNAL_MAX]
        # La mémoire persiste ce que le journal en RAM perdra au prochain
        # démarrage : c'est elle qui permet de répondre « pourquoi as-tu fait
        # ça hier ? ».
        try:
            self.memoire.journaliser(f"autonomie.{type_entree}", entree)
        except Exception:
            # Une mémoire illisible ne doit jamais empêcher la surveillance
            # de tourner — le contrat l'exige déjà côté `memory`.
            pass
