"""
test_assistant_autonomy.py — les cinq limites du mode autonome.

Le mode autonome est le seul endroit du projet où ANTI-ZEEVIRIUS agit sans
qu'un humain ait cliqué. Les tests qui suivent ne vérifient donc pas qu'il
« fonctionne » : ils vérifient qu'il **refuse**. Chacune des cinq limites de
la section 9 du contrat a son test, et chacun de ces tests doit pouvoir
échouer — les doubles enregistrent réellement ce qui les traverse.

Le plus important est `TestLimite1` : une capacité irréversible est déposée
dans le registre, une suggestion la réclame, et l'exécuteur doit rester
vierge. Si ce test tombe un jour, c'est qu'un utilisateur peut perdre des
fichiers pendant qu'il n'est pas devant sa machine.

Aucun test ne dort ni ne démarre de fil d'exécution, sauf celui du cycle de
vie : `executer_cycle()` est synchrone exprès, pour que la suite reste
déterministe sur une machine chargée.
"""

import threading
import time

import pytest

from assistant.autonomy import ModeAutonome
from assistant.notify import CentreNotifications
from assistant.registry import Capacite, Registre
from assistant.risk import Risque


# ── Doubles ─────────────────────────────────────────────────────────
class MemoireEspion:
    """Mémoire minimale : elle retient ce qu'on lui a journalisé."""

    def __init__(self):
        self.evenements = []

    def journaliser(self, evenement, detail):
        self.evenements.append((evenement, dict(detail)))

    def preference(self, cle, defaut=None):
        return defaut

    def definir_preference(self, cle, valeur):
        pass


class SuggestionFactice:
    """Même surface que `proactive.Suggestion` (capacite / motif / urgence).

    Un double plutôt que la vraie classe : ce test parle du mode autonome, pas
    de la façon dont les suggestions sont produites, et il ne doit pas tomber
    quand l'heuristique d'initiative évolue.
    """

    def __init__(self, capacite, motif="motif de test", urgence=1):
        self.capacite = capacite
        self.motif = motif
        self.urgence = urgence


class ExecuteurEspion:
    """Consigne chaque appel. C'est lui qui prouve ce qui a été exécuté."""

    def __init__(self, effet=None):
        self.appels = []
        self._effet = effet

    def __call__(self, capacite, parametres):
        self.appels.append(capacite.nom)
        if self._effet is not None:
            self._effet(capacite)
        return {"ok": True}

    @property
    def noms(self):
        return list(self.appels)


def capacite(nom, risque, categorie="systeme"):
    return Capacite(
        nom=nom,
        titre=f"Capacité {nom}",
        categorie=categorie,
        risque=risque,
        description="Capacité fabriquée pour les tests.",
        action_bridge="",
    )


def registre_de_test(*capacites):
    r = Registre()
    for c in capacites:
        r.enregistrer(c)
    return r


def construire(registre, suggestions, *, budget=12, executeur=None, centre=None):
    """Assemble un mode autonome entièrement piloté par le test."""
    return ModeAutonome(
        registre,
        MemoireEspion(),
        centre if centre is not None else CentreNotifications(executer=lambda *a, **k: {"ok": True}),
        budget=budget,
        executeur=executeur,
        suggereur=lambda etat, mem, reg: tuple(suggestions),
        intervalle=1.0,
    )


def entrees(mode, type_entree):
    return [e for e in mode.journal() if e["type"] == type_entree]


# ═══════════════════════════════════════════════════════════════════
# Limite 1 — n'exécute QUE des capacités de niveau LECTURE
# ═══════════════════════════════════════════════════════════════════
class TestLimite1SeulementLecture:

    def test_une_capacite_irreversible_n_est_jamais_appelee(self):
        """Le test qui compte : rien d'irréversible ne part tout seul."""
        reg = registre_de_test(
            capacite("purge.definitive", Risque.IRREVERSIBLE),
            capacite("scan.inventaire", Risque.LECTURE),
        )
        executeur = ExecuteurEspion()
        mode = construire(reg, [
            SuggestionFactice("purge.definitive", "quarantaine pleine", urgence=3),
            SuggestionFactice("scan.inventaire", "jamais inventorié", urgence=1),
        ], executeur=executeur)

        rapport = mode.executer_cycle()

        assert "purge.definitive" not in executeur.noms, (
            "le mode autonome a exécuté une capacité IRREVERSIBLE — "
            "c'est exactement ce que le contrat interdit"
        )
        assert executeur.noms == ["scan.inventaire"]
        assert rapport["actions"] == 1

    @pytest.mark.parametrize("niveau", [Risque.REVERSIBLE, Risque.DESTRUCTIF,
                                        Risque.IRREVERSIBLE])
    def test_aucun_niveau_au_dessus_de_lecture_ne_passe(self, niveau):
        reg = registre_de_test(capacite("cap.test", niveau))
        executeur = ExecuteurEspion()
        mode = construire(reg, [SuggestionFactice("cap.test")], executeur=executeur)

        mode.executer_cycle()

        assert executeur.noms == []

    def test_une_capacite_absente_du_registre_ne_declenche_rien(self):
        """Une suggestion peut nommer n'importe quoi : seul le registre fait foi."""
        reg = registre_de_test(capacite("scan.inventaire", Risque.LECTURE))
        executeur = ExecuteurEspion()
        mode = construire(reg, [SuggestionFactice("capacite.inexistante")],
                          executeur=executeur)

        mode.executer_cycle()

        assert executeur.noms == []
        assert entrees(mode, "inconnue"), "l'absence doit être tracée, pas avalée"

    def test_une_capacite_requalifiee_en_cours_de_cycle_est_refusee(self):
        """Dernière relecture du registre juste avant l'appel.

        Entre le moment où une suggestion désigne une capacité et celui où
        elle est lancée, rien ne garantit que le registre dit encore la même
        chose. Ce registre mouvant simule exactement ce décalage.
        """
        class RegistreMouvant:
            """`b.lire` est lue en LECTURE, puis relue en DESTRUCTIF.

            Le premier `obtenir` est celui du tri, le second celui de l'ultime
            vérification avant appel : seule cette seconde lecture décide.
            """

            def __init__(self):
                self.lectures = 0

            def obtenir(self, nom):
                if nom == "a.lire":
                    return capacite("a.lire", Risque.LECTURE)
                if nom == "b.lire":
                    self.lectures += 1
                    return capacite("b.lire",
                                    Risque.LECTURE if self.lectures == 1 else Risque.DESTRUCTIF)
                return None

        reg = RegistreMouvant()
        executeur = ExecuteurEspion()
        mode = construire(reg, [SuggestionFactice("a.lire"),
                                SuggestionFactice("b.lire")], executeur=executeur)

        mode.executer_cycle()

        assert executeur.noms == ["a.lire"], (
            "la requalification de `b.lire` en DESTRUCTIF n'a pas été vue"
        )
        assert entrees(mode, "refus")


# ═══════════════════════════════════════════════════════════════════
# Limite 2 — budget d'actions par cycle
# ═══════════════════════════════════════════════════════════════════
class TestLimite2Budget:

    def test_le_cycle_s_arrete_au_budget(self):
        caps = [capacite(f"lecture.{i}", Risque.LECTURE) for i in range(10)]
        reg = registre_de_test(*caps)
        executeur = ExecuteurEspion()
        mode = construire(reg, [SuggestionFactice(c.nom) for c in caps],
                          budget=3, executeur=executeur)

        rapport = mode.executer_cycle()

        assert len(executeur.noms) == 3
        assert rapport["actions"] == 3
        assert rapport["budget_atteint"] is True

    def test_l_arret_pour_budget_est_dit_et_non_silencieux(self):
        caps = [capacite(f"lecture.{i}", Risque.LECTURE) for i in range(5)]
        reg = registre_de_test(*caps)
        mode = construire(reg, [SuggestionFactice(c.nom) for c in caps],
                          budget=2, executeur=ExecuteurEspion())

        mode.executer_cycle()

        budget = entrees(mode, "budget")
        assert budget, "un arrêt silencieux est indiscernable d'une panne"
        assert "budget" in budget[0]["motif"]
        assert budget[0]["restantes"] == 3

    def test_un_budget_absurde_est_ramene_a_une_action(self):
        reg = registre_de_test(capacite("lecture.a", Risque.LECTURE))
        mode = construire(reg, [], budget=0)
        assert mode.budget == 1


# ═══════════════════════════════════════════════════════════════════
# Limite 3 — journalisation AVANT exécution, avec le motif
# ═══════════════════════════════════════════════════════════════════
class TestLimite3JournalAvantAction:

    def test_l_entree_existe_deja_quand_l_action_demarre(self):
        """L'exécuteur inspecte le journal pendant qu'il est appelé."""
        vu = {}

        def inspecter(cap):
            vu["journal"] = mode.journal()

        reg = registre_de_test(capacite("scan.inventaire", Risque.LECTURE))
        executeur = ExecuteurEspion(effet=inspecter)
        mode = construire(reg, [SuggestionFactice("scan.inventaire",
                                                  "aucun inventaire depuis 30 jours")],
                          executeur=executeur)

        mode.executer_cycle()

        avant = [e for e in vu["journal"] if e["type"] == "action"]
        assert avant, "l'action n'était pas journalisée au moment de son exécution"
        assert avant[0]["capacite"] == "scan.inventaire"
        assert avant[0]["motif"] == "aucun inventaire depuis 30 jours"
        assert avant[0]["etape"] == "engagee"
        assert not [e for e in vu["journal"] if e["type"] == "resultat"], (
            "le résultat ne peut pas précéder l'action"
        )

    def test_le_resultat_s_ajoute_sans_reecrire_l_engagement(self):
        """Un plantage doit laisser la trace de ce qui était en cours."""
        reg = registre_de_test(capacite("scan.inventaire", Risque.LECTURE))

        def exploser(cap, params):
            raise RuntimeError("sonde injoignable")

        mode = construire(reg, [SuggestionFactice("scan.inventaire")],
                          executeur=exploser)
        mode.executer_cycle()

        assert entrees(mode, "action"), "l'engagement doit survivre à l'échec"
        resultats = entrees(mode, "resultat")
        assert resultats and resultats[0]["etape"] == "echec"
        assert "sonde injoignable" in resultats[0]["raison"]

    def test_le_journal_rendu_est_une_copie(self):
        """Modifier la copie ne doit pas altérer la trace interne."""
        reg = registre_de_test(capacite("scan.inventaire", Risque.LECTURE))
        mode = construire(reg, [SuggestionFactice("scan.inventaire")],
                          executeur=ExecuteurEspion())
        mode.executer_cycle()

        copie = mode.journal()
        assert isinstance(copie, tuple)
        copie[0]["capacite"] = "falsifie"
        assert mode.journal()[0].get("capacite") != "falsifie"


# ═══════════════════════════════════════════════════════════════════
# Limite 4 — arreter() prend effet avant l'action suivante
# ═══════════════════════════════════════════════════════════════════
class TestLimite4ArretImmediat:

    def test_l_arret_coupe_le_cycle_avant_l_action_suivante(self):
        """Le cycle a du budget et des suggestions ; il s'arrête quand même."""
        caps = [capacite(f"lecture.{i}", Risque.LECTURE) for i in range(6)]
        reg = registre_de_test(*caps)
        executeur = ExecuteurEspion()
        mode = construire(reg, [SuggestionFactice(c.nom) for c in caps],
                          budget=12, executeur=executeur)
        # L'arrêt est demandé PENDANT la première action.
        executeur._effet = lambda cap: mode.arreter()

        rapport = mode.executer_cycle()

        assert executeur.noms == ["lecture.0"], (
            "des actions ont continué après la demande d'arrêt"
        )
        assert rapport["interrompu"] is True
        assert entrees(mode, "interruption")

    def test_arreter_est_idempotent(self):
        mode = construire(registre_de_test(), [])
        mode.arreter()
        mode.arreter()
        assert mode.actif() is False

    def test_arreter_sans_demarrer_ne_journalise_rien(self):
        """Arrêter ce qui n'a jamais tourné n'est pas un événement."""
        mode = construire(registre_de_test(), [])
        mode.arreter()
        assert mode.journal() == ()

    def test_demarrer_puis_arreter_rend_la_main_rapidement(self):
        """La boucle attend sur un `Event` : l'arrêt ne patiente pas l'intervalle."""
        reg = registre_de_test(capacite("lecture.a", Risque.LECTURE))
        mode = ModeAutonome(
            reg, MemoireEspion(),
            CentreNotifications(executer=lambda *a, **k: {"ok": True}),
            executeur=ExecuteurEspion(),
            suggereur=lambda etat, mem, r: (),
            intervalle=600.0,   # dix minutes : un arrêt paresseux ferait échouer le test
        )
        mode.demarrer()
        assert mode.actif() is True
        depart = time.monotonic()
        mode.arreter()
        assert time.monotonic() - depart < 2.0
        assert mode.actif() is False

    def test_demarrer_deux_fois_ne_cree_qu_un_fil(self):
        mode = construire(registre_de_test(), [])
        mode.demarrer()
        premier = mode._fil
        mode.demarrer()
        try:
            assert mode._fil is premier, "un second fil de surveillance a été ouvert"
        finally:
            mode.arreter()


# ═══════════════════════════════════════════════════════════════════
# Limite 5 — ce qui dépasse LECTURE devient une suggestion
# ═══════════════════════════════════════════════════════════════════
class TestLimite5SuggestionPlutotQueAction:

    def test_une_capacite_destructive_devient_une_proposition(self):
        reg = registre_de_test(capacite("quarantaine.purger", Risque.DESTRUCTIF))
        executeur = ExecuteurEspion()
        mode = construire(reg, [SuggestionFactice("quarantaine.purger",
                                                  "37 éléments dorment en quarantaine",
                                                  urgence=2)],
                          executeur=executeur)

        rapport = mode.executer_cycle()

        assert executeur.noms == []
        assert len(rapport["proposees"]) == 1
        proposition = rapport["proposees"][0]
        assert proposition["capacite"] == "quarantaine.purger"
        assert proposition["motif"] == "37 éléments dorment en quarantaine"
        assert proposition["risque"] == int(Risque.DESTRUCTIF)

    def test_la_proposition_est_visible_dans_les_notifications(self):
        """Proposer sans le dire revient à ne rien proposer."""
        centre = CentreNotifications(executer=lambda *a, **k: {"ok": True})
        reg = registre_de_test(capacite("rangement.appliquer", Risque.REVERSIBLE))
        mode = construire(reg, [SuggestionFactice("rangement.appliquer",
                                                  "1 200 fichiers en vrac", urgence=1)],
                          executeur=ExecuteurEspion(), centre=centre)

        mode.executer_cycle()

        notifications = centre.lister()
        assert len(notifications) == 1
        assert notifications[0].source == "autonomy"
        assert "1 200 fichiers en vrac" in notifications[0].corps

    def test_un_centre_en_panne_n_interrompt_pas_le_cycle(self):
        """Une bulle impossible ne doit pas désarmer la surveillance."""
        class CentreCasse:
            def pousser(self, *a, **k):
                raise OSError("aucun canal de notification")

        reg = registre_de_test(capacite("quarantaine.purger", Risque.DESTRUCTIF),
                               capacite("scan.inventaire", Risque.LECTURE))
        executeur = ExecuteurEspion()
        mode = construire(reg, [SuggestionFactice("quarantaine.purger"),
                                SuggestionFactice("scan.inventaire")],
                          executeur=executeur, centre=CentreCasse())

        rapport = mode.executer_cycle()

        assert executeur.noms == ["scan.inventaire"]
        assert rapport["proposees"]
        assert entrees(mode, "erreur")


# ═══════════════════════════════════════════════════════════════════
# Robustesse générale
# ═══════════════════════════════════════════════════════════════════
class TestRobustesse:

    def test_sans_executeur_rien_n_est_execute_mais_tout_est_trace(self):
        """Mode observation : utile, et sûr par construction."""
        reg = registre_de_test(capacite("scan.inventaire", Risque.LECTURE))
        mode = construire(reg, [SuggestionFactice("scan.inventaire")], executeur=None)

        rapport = mode.executer_cycle()

        assert rapport["actions"] == 1
        resultats = entrees(mode, "resultat")
        assert resultats[0]["etape"] == "non_executee"

    def test_un_cycle_qui_plante_ne_desarme_pas_la_veille(self):
        """Une anomalie transitoire ne doit pas éteindre le gardien en silence."""
        passage = threading.Event()

        def casse(etat, mem, reg):
            passage.set()
            raise ValueError("initiative indisponible")

        mode = ModeAutonome(registre_de_test(), MemoireEspion(),
                            CentreNotifications(executer=lambda *a, **k: {"ok": True}),
                            suggereur=casse, intervalle=1.0)

        # Appelé directement, le cycle laisse remonter l'anomalie : masquer une
        # erreur de programmation dans l'initiative rendrait sa mise au point
        # impossible.
        with pytest.raises(ValueError):
            mode.executer_cycle()

        # Dans la boucle, en revanche, elle est encaissée et tracée.
        mode.demarrer()
        assert passage.wait(5.0), "la boucle n'a jamais exécuté de cycle"
        mode.arreter()
        assert any(e["type"] == "erreur" for e in mode.journal())

    def test_un_etat_illisible_ne_bloque_pas_la_surveillance(self):
        def etat_casse():
            raise OSError("psutil absent")

        reg = registre_de_test(capacite("scan.inventaire", Risque.LECTURE))
        executeur = ExecuteurEspion()
        mode = ModeAutonome(reg, MemoireEspion(),
                            CentreNotifications(executer=lambda *a, **k: {"ok": True}),
                            executeur=executeur,
                            source_etat=etat_casse,
                            suggereur=lambda etat, mem, r: (SuggestionFactice("scan.inventaire"),))
        rapport = mode.executer_cycle()

        assert executeur.noms == ["scan.inventaire"]
        assert rapport["actions"] == 1
        assert entrees(mode, "erreur")

    def test_la_memoire_persiste_les_decisions(self):
        memoire = MemoireEspion()
        reg = registre_de_test(capacite("scan.inventaire", Risque.LECTURE))
        mode = ModeAutonome(reg, memoire,
                            CentreNotifications(executer=lambda *a, **k: {"ok": True}),
                            executeur=ExecuteurEspion(),
                            suggereur=lambda etat, mem, r: (SuggestionFactice("scan.inventaire"),))
        mode.executer_cycle()

        types = [nom for nom, _ in memoire.evenements]
        assert "autonomie.action" in types
        assert "autonomie.resultat" in types

    def test_une_memoire_illisible_n_empeche_pas_d_agir(self):
        class MemoireCassee:
            def journaliser(self, evenement, detail):
                raise OSError("mémoire corrompue")

        reg = registre_de_test(capacite("scan.inventaire", Risque.LECTURE))
        executeur = ExecuteurEspion()
        mode = ModeAutonome(reg, MemoireCassee(),
                            CentreNotifications(executer=lambda *a, **k: {"ok": True}),
                            executeur=executeur,
                            suggereur=lambda etat, mem, r: (SuggestionFactice("scan.inventaire"),))
        mode.executer_cycle()

        assert executeur.noms == ["scan.inventaire"]

    def test_le_journal_est_borne(self):
        from assistant import autonomy

        reg = registre_de_test(capacite("scan.inventaire", Risque.LECTURE))
        mode = construire(reg, [SuggestionFactice("scan.inventaire")],
                          executeur=ExecuteurEspion())
        for _ in range(autonomy.JOURNAL_MAX + 40):
            mode.executer_cycle()

        assert len(mode.journal()) <= autonomy.JOURNAL_MAX

    def test_etat_decrit_le_mode_pour_l_interface(self):
        reg = registre_de_test(capacite("scan.inventaire", Risque.LECTURE))
        mode = construire(reg, [SuggestionFactice("scan.inventaire")],
                          budget=5, executeur=ExecuteurEspion())
        mode.executer_cycle()
        etat = mode.etat()

        assert etat["actif"] is False
        assert etat["budget"] == 5
        assert etat["cycles"] == 1
        assert etat["executeur"] is True
        assert isinstance(etat["journal"], list)
