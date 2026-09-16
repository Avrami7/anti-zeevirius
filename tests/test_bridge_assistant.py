"""
test_bridge_assistant.py — exposition web de la couche assistant (section 10).

Trois choses seulement sont vérifiées ici, mais ce sont celles qui coûtent
cher quand elles cassent :

1. **Routage.** Les neuf actions du contrat existent, sous le nom exact que
   le contrat leur donne (`assistant.capacites`, avec un point), et rendent
   l'enveloppe JSON figée.
2. **`assistant.comprendre` n'exécute rien.** Une phrase qui désigne une
   capacité irréversible doit produire une lecture et zéro effet de bord.
   C'est la garantie qui empêche une phrase mal formulée de déclencher une
   purge, et elle est testée par des espions qui enregistrent réellement ce
   qui les traverse.
3. **Tout ce qui écrit passe par `_guarded()`.** Profil, acquittement et
   bascule du mode autonome exigent le cycle `dry_run` → `confirm_token`,
   avec jeton à usage unique lié à l'action ET aux paramètres.

Aucun test n'exige Windows ni `psutil` : la couche assistant est en
bibliothèque standard, et les modules absents sont simulés en injectant
l'indisponibilité dans le pont, comme le font déjà `test_gui_bridge.py` et
`test_gui_v2.py`.
"""

import dataclasses

import pytest

from assistant.memory import Profil
from assistant.notify import CentreNotifications
from assistant.registry import Capacite, Registre
from assistant.risk import Risque
from gui.bridge import ASSISTANT_ACTIONS, Bridge


# ── Doubles ─────────────────────────────────────────────────────────
class MemoireDouble:
    """Mémoire entièrement en RAM : aucun test n'écrit dans les données
    utilisateur de la personne qui lance la suite."""

    def __init__(self):
        self._profil = Profil()
        self._preferences = {}
        self.journal = []

    def profil(self):
        return self._profil

    def definir_profil(self, **champs):
        self._profil = dataclasses.replace(self._profil, **champs)
        return self._profil

    def preference(self, cle, defaut=None):
        return self._preferences.get(cle, defaut)

    def definir_preference(self, cle, valeur):
        self._preferences[cle] = valeur

    def journaliser(self, evenement, detail):
        self.journal.append((evenement, detail))

    def decisions(self, limite=50):
        return []


class IntentDouble:
    """Tient lieu du module `assistant.intent`."""

    @dataclasses.dataclass(frozen=True)
    class Intention:
        capacite: str
        parametres: dict
        confiance: float
        justification: str

    def __init__(self, intention=None):
        self.appels = []
        self._intention = intention or self.Intention(
            "test.purge", {}, 0.93,
            "« supprime » et « tout » désignent la purge définitive.")

    def comprendre(self, phrase, registre):
        self.appels.append(phrase)
        return self._intention


def registre_de_test():
    r = Registre()
    r.enregistrer(Capacite(
        nom="test.purge", titre="Purger définitivement la quarantaine",
        categorie="nettoyage", risque=Risque.IRREVERSIBLE,
        description="Supprime sans retour les fichiers isolés.",
        action_bridge="quarantine_delete", parametres=("id",)))
    r.enregistrer(Capacite(
        nom="test.inventaire", titre="Inventorier la quarantaine",
        categorie="protection", risque=Risque.LECTURE,
        description="Liste ce qui est isolé, sans rien toucher.",
        action_bridge="quarantine_list"))
    return r


@pytest.fixture
def pont():
    """Pont dont la couche assistant est entièrement pilotée par le test."""
    b = Bridge()
    memoire = MemoireDouble()
    centre = CentreNotifications(executer=lambda *a, **k: {"ok": True})
    intent = IntentDouble()
    b._instances.update({
        "assistant_registre": registre_de_test(),
        "assistant_memoire": memoire,
        "assistant_centre": centre,
    })
    b._modules["assistant:intent"] = intent
    return b, {"memoire": memoire, "centre": centre, "intent": intent}


def enveloppe_valide(rep):
    assert isinstance(rep, dict), "réponse non JSON"
    assert "ok" in rep and isinstance(rep["ok"], bool)
    if rep["ok"]:
        assert "data" in rep and isinstance(rep["data"], dict)
    else:
        assert rep.get("error"), "un échec doit porter un message lisible"
        assert "unavailable" in rep
        if rep["unavailable"]:
            assert rep.get("reason")
    return rep


def cycle_garde(pont, action, corps):
    """Exécute réellement une action gardée et rend son résultat."""
    sec = enveloppe_valide(pont.dispatch(action, dict(corps, dry_run=True)))
    assert sec["ok"], sec
    jeton = sec["data"]["confirm_token"]
    reel = enveloppe_valide(pont.dispatch(
        action, dict(corps, dry_run=False, confirm_token=jeton)))
    return sec, reel


# ═══════════════════════════════════════════════════════════════════
# 1. Routage des neuf actions du contrat
# ═══════════════════════════════════════════════════════════════════
class TestRoutage:

    def test_les_neuf_actions_sont_annoncees(self):
        connues = set(Bridge().known_actions())
        manquantes = [a for a in ASSISTANT_ACTIONS if a not in connues]
        assert not manquantes, f"actions absentes du pont : {manquantes}"

    def test_le_contrat_annonce_bien_neuf_actions(self):
        assert len(ASSISTANT_ACTIONS) == 9
        assert len(set(ASSISTANT_ACTIONS)) == 9

    @pytest.mark.parametrize("action,corps", [
        ("assistant.capacites", {}),
        ("assistant.comprendre", {"phrase": "montre la quarantaine"}),
        ("assistant.profil", {}),
        ("assistant.telemetrie", {}),
        ("assistant.notifications", {}),
        ("assistant.acquitter", {"identifiant": "inexistant"}),
        ("assistant.suggestions", {}),
        ("assistant.resume", {}),
        ("assistant.autonomie", {}),
    ])
    def test_chaque_action_repond_au_contrat(self, pont, action, corps):
        b, _ = pont
        enveloppe_valide(b.dispatch(action, corps))

    def test_le_nom_pointe_et_le_nom_souligne_menent_au_meme_endroit(self, pont):
        """Le contrat écrit `assistant.capacites` ; Python ne sait pas nommer
        une méthode ainsi. Les deux écritures doivent rester équivalentes."""
        b, _ = pont
        pointe = b.dispatch("assistant.capacites", {})
        souligne = b.dispatch("assistant_capacites", {})
        assert pointe["ok"] and souligne["ok"]
        assert pointe["data"]["total"] == souligne["data"]["total"]

    def test_une_action_assistant_inconnue_reste_un_404(self, pont):
        b, _ = pont
        assert b.dispatch("assistant.inventee", {}) is None

    def test_un_module_assistant_absent_degrade_proprement(self, pont):
        """Un module manquant ne fait jamais crasher le serveur."""
        b, _ = pont
        del b._modules["assistant:intent"]
        b._module_errors["assistant:intent"] = "assistant.intent indisponible : test"

        rep = enveloppe_valide(b.dispatch("assistant.comprendre", {"phrase": "analyse"}))
        assert rep["ok"] is False
        assert rep["unavailable"] is True
        assert "assistant.intent" in rep["reason"]


# ═══════════════════════════════════════════════════════════════════
# 2. assistant.comprendre N'EXÉCUTE RIEN
# ═══════════════════════════════════════════════════════════════════
class TestComprendreNExecuteRien:

    def test_une_phrase_destructive_ne_produit_aucun_effet_de_bord(self, pont):
        """Le test qui justifie la séparation comprendre / exécuter."""
        b, doubles = pont
        appels = []
        # Espion posé sur l'action que la capacité comprise désignerait.
        b.a_quarantine_delete = lambda params: appels.append(params) or {"ok": True}

        rep = enveloppe_valide(b.dispatch(
            "assistant.comprendre", {"phrase": "supprime tout définitivement"}))

        assert rep["ok"] is True
        data = rep["data"]
        assert data["capacite"] == "test.purge"
        assert data["execute"] is False
        assert appels == [], "comprendre a exécuté la capacité qu'elle décrivait"
        assert b.confirm.pending_count() == 0, (
            "comprendre a ouvert un cycle de confirmation : elle n'écrit rien"
        )
        assert "quarantine" not in b._instances, (
            "le module de quarantaine a été instancié par une simple lecture"
        )

    def test_la_reponse_porte_la_justification_et_le_risque(self, pont):
        """L'utilisateur doit pouvoir juger AVANT de cliquer sur Exécuter."""
        b, _ = pont
        data = b.dispatch("assistant.comprendre", {"phrase": "purge"})["data"]

        assert data["justification"]
        assert data["detail"]["risque"] == int(Risque.IRREVERSIBLE)
        assert data["detail"]["risque_libelle"] == "irréversible"
        assert data["detail"]["exige_confirmation"] is True
        assert data["detail"]["autonome"] is False
        assert 0.0 <= data["confiance"] <= 1.0

    def test_une_phrase_incomprise_ne_devine_pas(self, pont):
        b, doubles = pont
        doubles["intent"]._intention = IntentDouble.Intention(
            "", {}, 0.2, "aucune capacité ne correspond assez nettement.")

        data = b.dispatch("assistant.comprendre", {"phrase": "fais un truc"})["data"]

        assert data["capacite"] == ""
        assert data["comprise"] is False
        assert data["detail"] is None

    def test_une_phrase_vide_est_refusee(self, pont):
        b, _ = pont
        rep = enveloppe_valide(b.dispatch("assistant.comprendre", {"phrase": "   "}))
        assert rep["ok"] is False
        assert rep["unavailable"] is False


# ═══════════════════════════════════════════════════════════════════
# 3. Les actions qui écrivent passent par _guarded()
# ═══════════════════════════════════════════════════════════════════
class TestDoubleValidation:

    def test_lire_le_profil_n_exige_aucun_jeton(self, pont):
        b, _ = pont
        rep = b.dispatch("assistant.profil", {})
        assert rep["ok"] and rep["data"]["ecrit"] is False
        assert b.confirm.pending_count() == 0

    def test_ecrire_le_profil_exige_le_cycle_complet(self, pont):
        b, doubles = pont
        sec, reel = cycle_garde(b, "assistant.profil", {"nom_utilisateur": "Zeev"})

        assert sec["data"]["dry_run"] is True
        assert sec["data"]["plan"]["apres"]["nom_utilisateur"] == "Zeev"
        assert doubles["memoire"].profil().nom_utilisateur == "" or reel["ok"]
        assert reel["ok"] and reel["data"]["dry_run"] is False
        assert doubles["memoire"].profil().nom_utilisateur == "Zeev"

    def test_l_appel_a_blanc_ne_touche_pas_au_profil(self, pont):
        b, doubles = pont
        b.dispatch("assistant.profil", {"nom_utilisateur": "Zeev", "dry_run": True})
        assert doubles["memoire"].profil().nom_utilisateur == ""

    def test_une_ecriture_sans_jeton_est_refusee(self, pont):
        b, doubles = pont
        rep = enveloppe_valide(b.dispatch(
            "assistant.profil", {"nom_utilisateur": "Zeev", "dry_run": False}))
        assert rep["ok"] is False
        assert "confirm_token" in rep["error"]
        assert doubles["memoire"].profil().nom_utilisateur == ""

    def test_le_jeton_ne_sert_qu_une_fois(self, pont):
        b, _ = pont
        _sec, reel = cycle_garde(b, "assistant.profil", {"langue": "en"})
        assert reel["ok"]
        rejoue = b.dispatch("assistant.profil",
                            {"langue": "en", "dry_run": False,
                             "confirm_token": _sec["data"]["confirm_token"]})
        assert rejoue["ok"] is False

    def test_le_jeton_est_lie_aux_parametres(self, pont):
        """Un jeton obtenu pour « démarrer » ne doit pas arrêter le mode."""
        b, _ = pont
        sec = b.dispatch("assistant.autonomie", {"etat": "demarrer", "dry_run": True})
        jeton = sec["data"]["confirm_token"]
        detourne = enveloppe_valide(b.dispatch(
            "assistant.autonomie",
            {"etat": "arreter", "dry_run": False, "confirm_token": jeton}))
        assert detourne["ok"] is False
        assert "paramètres" in detourne["error"]

    def test_acquitter_exige_le_cycle_et_finit_par_acquitter(self, pont):
        b, doubles = pont
        notification = doubles["centre"].pousser(
            "alerte", "Caméra active", "inconnu32.exe filme.", "camera_watch",
            systeme=False)

        sec, reel = cycle_garde(b, "assistant.acquitter",
                                {"identifiant": notification.identifiant})

        assert sec["data"]["plan"]["count"] == 1
        assert reel["ok"] and reel["data"]["result"]["acquittee"] is True
        assert doubles["centre"].lister(non_acquittees_seulement=True) == ()

    def test_acquitter_a_blanc_laisse_la_notification_non_lue(self, pont):
        b, doubles = pont
        n = doubles["centre"].pousser("info", "Titre", "Corps", "test", systeme=False)
        b.dispatch("assistant.acquitter", {"identifiant": n.identifiant, "dry_run": True})
        assert len(doubles["centre"].lister(non_acquittees_seulement=True)) == 1

    def test_acquitter_un_identifiant_inconnu_annonce_un_plan_vide(self, pont):
        b, _ = pont
        rep = b.dispatch("assistant.acquitter", {"identifiant": "néant", "dry_run": True})
        assert rep["ok"] and rep["data"]["plan"]["count"] == 0

    def test_lire_l_etat_de_l_autonomie_n_exige_aucun_jeton(self, pont):
        b, _ = pont
        rep = b.dispatch("assistant.autonomie", {})
        assert rep["ok"] and rep["data"]["actif"] is False
        assert b.confirm.pending_count() == 0

    def test_basculer_l_autonomie_exige_le_cycle(self, pont):
        b, _ = pont
        try:
            _sec, reel = cycle_garde(b, "assistant.autonomie", {"etat": "demarrer"})
            assert reel["ok"] and reel["data"]["result"]["actif"] is True
            _sec2, reel2 = cycle_garde(b, "assistant.autonomie", {"etat": "arreter"})
            assert reel2["ok"] and reel2["data"]["result"]["actif"] is False
        finally:
            b._instances["assistant_autonomie"].arreter()

    def test_un_ordre_d_autonomie_inconnu_est_refuse(self, pont):
        b, _ = pont
        rep = enveloppe_valide(b.dispatch("assistant.autonomie", {"etat": "détruis"}))
        assert rep["ok"] is False
        assert rep["unavailable"] is False


# ═══════════════════════════════════════════════════════════════════
# 4. L'exécuteur du mode autonome, dernier verrou avant dispatch()
# ═══════════════════════════════════════════════════════════════════
class TestExecuteurAutonome:

    def test_il_refuse_tout_ce_qui_depasse_la_lecture(self, pont):
        b, _ = pont
        appels = []
        b.a_quarantine_delete = lambda params: appels.append(params) or {"ok": True}
        capacite = b._instances["assistant_registre"].obtenir("test.purge")

        rep = b._executer_capacite(capacite, {})

        assert rep["ok"] is False
        assert "lecture" in rep["error"]
        assert appels == [], "le pont a exécuté une capacité irréversible sans clic"

    def test_il_route_une_capacite_de_lecture(self, pont):
        b, _ = pont
        vus = []
        b.a_quarantine_list = lambda params: vus.append(params) or {"ok": True, "data": {}}
        capacite = b._instances["assistant_registre"].obtenir("test.inventaire")

        rep = b._executer_capacite(capacite, {})

        assert rep["ok"] is True
        assert vus == [{}]

    def test_une_capacite_interne_n_invente_aucune_action(self, pont):
        b, _ = pont
        interne = Capacite(nom="test.interne", titre="Interne", categorie="systeme",
                           risque=Risque.LECTURE, description="Sans action web.",
                           action_bridge="")
        rep = b._executer_capacite(interne, {})
        assert rep["ok"] is False
        assert "interne" in rep["error"]


# ═══════════════════════════════════════════════════════════════════
# 5. Contenu des lectures
# ═══════════════════════════════════════════════════════════════════
class TestLectures:

    def test_les_capacites_portent_leur_niveau_de_risque(self, pont):
        b, _ = pont
        data = b.dispatch("assistant.capacites", {})["data"]
        par_nom = {c["nom"]: c for c in data["capacites"]}

        assert par_nom["test.purge"]["risque"] == int(Risque.IRREVERSIBLE)
        assert par_nom["test.purge"]["autonome"] is False
        assert par_nom["test.inventaire"]["autonome"] is True
        assert "nettoyage" in data["categories"]

    def test_le_filtre_par_categorie_est_deterministe(self, pont):
        b, _ = pont
        d1 = b.dispatch("assistant.capacites", {"categorie": "nettoyage"})["data"]
        d2 = b.dispatch("assistant.capacites", {"categorie": "nettoyage"})["data"]
        assert [c["nom"] for c in d1["capacites"]] == [c["nom"] for c in d2["capacites"]]
        assert [c["nom"] for c in d1["capacites"]] == ["test.purge"]

    def test_la_telemetrie_rend_un_echantillon_meme_sans_psutil(self, pont):
        b, _ = pont
        data = b.dispatch("assistant.telemetrie", {})["data"]
        assert set(data["echantillon"]) >= {"horodatage", "cpu", "memoire", "disque"}
        assert isinstance(data["historique"], list)
        assert isinstance(data["disponible"], bool)

    def test_les_suggestions_portent_le_risque_de_leur_capacite(self, pont):
        b, _ = pont
        data = b.dispatch("assistant.suggestions", {})["data"]
        assert isinstance(data["suggestions"], list)
        for s in data["suggestions"]:
            assert {"capacite", "motif", "urgence"} <= set(s)
            if s["detail"] is not None:
                assert "risque" in s["detail"]
        # L'état qui motive les suggestions accompagne le résultat.
        assert "quarantaine" in data["etat"]

    def test_le_resume_declare_les_sources_qu_il_a_pu_lire(self, pont):
        b, _ = pont
        data = b.dispatch("assistant.resume", {})["data"]
        for champ in ("periode", "faits", "alertes", "suggestions"):
            assert champ in data
        assert "notifications" in data["sources_lues"]

    def test_le_resume_supporte_une_periode_a_l_envers(self, pont):
        b, _ = pont
        rep = b.dispatch("assistant.resume", {"depuis": 2_000_000, "jusqu_a": 1_000_000})
        assert rep["ok"] is True

    def test_les_notifications_comptent_les_non_acquittees(self, pont):
        b, doubles = pont
        doubles["centre"].pousser("info", "A", "corps A", "test", systeme=False)
        n = doubles["centre"].pousser("alerte", "B", "corps B", "test", systeme=False)
        doubles["centre"].acquitter(n.identifiant)

        data = b.dispatch("assistant.notifications", {})["data"]
        assert data["total"] == 2
        assert data["non_acquittees"] == 1

        filtre = b.dispatch("assistant.notifications",
                            {"non_acquittees_seulement": True})["data"]
        assert [x["titre"] for x in filtre["notifications"]] == ["A"]
