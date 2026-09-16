"""
Tests de assistant/notify.py.

Le module a deux responsabilités, et chacune a son piège :

  * **envoyer une bulle Windows** — le texte affiché vient du registre, donc
    d'un tiers. Une apostrophe non doublée referme la chaîne PowerShell et la
    suite s'exécute comme du code. Ce test-là est un test de sécurité, pas de
    confort.
  * **ne pas répéter** — une alerte identique non acquittée ne doit pas être
    repoussée. Sans cela, une caméra allumée pendant une réunion d'une heure
    produirait des centaines de bulles, et l'utilisateur apprendrait à les
    ignorer toutes, y compris la bonne.

Aucun processus n'est lancé : l'exécuteur est injecté.
"""

import time

import pytest

from assistant.notify import (
    CentreNotifications, GRAVITES, Notification, envoyer_bulle,
    identifiant_notification,
)


def _executeur(toast_ok=True, msg_ok=False):
    """Exécuteur factice : journalise les commandes, décide de leur sort."""
    journal = []

    def executer(commande, timeout=None):
        joint = " ".join(commande)
        journal.append(joint)
        if "ToastNotification" in joint:
            return {"code": 0 if toast_ok else 1, "sortie": "", "erreur": ""}
        if commande and commande[0] == "msg":
            return {"code": 0 if msg_ok else 1, "sortie": "", "erreur": ""}
        return {"code": 0, "sortie": "", "erreur": ""}

    executer.journal = journal
    return executer


# ═══════════════════════════════════════════════════════════════════
# Identifiant
# ═══════════════════════════════════════════════════════════════════
class TestIdentifiant:
    def test_stable_entre_deux_appels(self):
        a = identifiant_notification("Caméra", "espion.exe", "camera_watch")
        b = identifiant_notification("Caméra", "espion.exe", "camera_watch")
        assert a == b

    @pytest.mark.parametrize("triplet", [
        ("Caméra", "espion.exe", "network_watch"),
        ("Caméra", "autre.exe", "camera_watch"),
        ("Micro", "espion.exe", "camera_watch"),
    ])
    def test_un_changement_change_l_identifiant(self, triplet):
        reference = identifiant_notification("Caméra", "espion.exe", "camera_watch")
        assert identifiant_notification(*triplet) != reference

    def test_pas_de_collision_par_decoupage(self):
        """("ab","c") et ("a","bc") sont deux alertes différentes : sans
        séparateur, elles se masqueraient l'une l'autre."""
        assert (identifiant_notification("ab", "c", "s")
                != identifiant_notification("a", "bc", "s"))

    def test_court_et_hexadecimal(self):
        i = identifiant_notification("a", "b", "c")
        assert len(i) == 16 and int(i, 16) >= 0


# ═══════════════════════════════════════════════════════════════════
# Bulle système
# ═══════════════════════════════════════════════════════════════════
class TestBulle:
    def test_notification_windows(self):
        assert envoyer_bulle("Titre", "Corps", executer=_executeur())["ok"] is True

    def test_repli_sur_msg(self):
        """Sans bureau, la bulle échoue : `msg` reste le dernier canal."""
        e = _executeur(toast_ok=False, msg_ok=True)
        res = envoyer_bulle("Titre", "Corps", executer=e)

        assert res["ok"] is True and res["data"]["methode"] == "msg"
        assert any(c.startswith("msg ") for c in e.journal)

    def test_echec_total_ne_leve_pas(self):
        res = envoyer_bulle("a", "b", executer=_executeur(toast_ok=False,
                                                          msg_ok=False))
        assert res["ok"] is False and res["unavailable"] is True

    def test_apostrophe_echappee(self):
        """« L'application » casserait la chaîne PowerShell et pourrait faire
        exécuter la suite comme du code."""
        e = _executeur()
        envoyer_bulle("L'alerte", "d'un logiciel", executer=e)
        toast = [c for c in e.journal if "ToastNotification" in c][0]

        assert "L''alerte" in toast and "d''un logiciel" in toast

    def test_injection_powershell_neutralisee(self):
        e = _executeur()
        envoyer_bulle("x'); rm -rf /; ('", "corps", executer=e)
        toast = [c for c in e.journal if "ToastNotification" in c][0]

        assert "x''); rm -rf /; (''" in toast, "l'apostrophe doit rester doublée"

    def test_camera_watch_utilise_bien_ce_module(self):
        """La factorisation doit être réelle : camera_watch ne doit plus
        posséder sa propre copie du script PowerShell."""
        import inspect
        import security.camera_watch as cw

        assert "ToastNotificationManager" not in inspect.getsource(cw)
        assert cw.envoyer_bulle is envoyer_bulle


# ═══════════════════════════════════════════════════════════════════
# Centre de notifications
# ═══════════════════════════════════════════════════════════════════
class TestCentre:
    def test_pousser_rend_une_notification_complete(self):
        c = CentreNotifications(executer=_executeur())
        n = c.pousser("alerte", "Caméra", "espion.exe", "camera_watch")

        assert isinstance(n, Notification)
        assert (n.gravite, n.source, n.acquittee) == ("alerte", "camera_watch", False)
        assert n.horodatage > 0

    def test_gravite_inconnue_refusee(self):
        """Une gravité inventée s'afficherait comme une information : une
        alerte critique dégradée en silence est exactement l'erreur à éviter."""
        c = CentreNotifications(executer=_executeur())
        with pytest.raises(ValueError):
            c.pousser("urgentissime", "t", "c", "s")

    @pytest.mark.parametrize("gravite", GRAVITES)
    def test_les_trois_gravites_passent(self, gravite):
        c = CentreNotifications(executer=_executeur())
        assert c.pousser(gravite, "t", "c", "s").gravite == gravite

    def test_bulle_envoyee_si_systeme(self):
        e = _executeur()
        CentreNotifications(executer=e).pousser("info", "t", "c", "s")
        assert any("ToastNotification" in x for x in e.journal)

    def test_pas_de_bulle_si_systeme_faux(self):
        e = _executeur()
        CentreNotifications(executer=e).pousser("info", "t", "c", "s",
                                                systeme=False)
        assert e.journal == []

    def test_echec_de_bulle_n_empeche_pas_l_enregistrement(self):
        """L'interface web reste le canal principal : une bulle impossible ne
        doit pas faire disparaître l'alerte."""
        c = CentreNotifications(executer=_executeur(toast_ok=False))
        c.pousser("critique", "t", "c", "s")

        assert len(c.lister()) == 1


class TestAntiRepetition:
    def test_deux_alertes_identiques_ne_font_qu_une(self):
        e = _executeur()
        c = CentreNotifications(executer=e)
        a = c.pousser("alerte", "Caméra", "espion.exe", "camera_watch")
        b = c.pousser("alerte", "Caméra", "espion.exe", "camera_watch")

        assert a is b
        assert len(c.lister()) == 1
        assert len([x for x in e.journal if "ToastNotification" in x]) == 1

    def test_une_surveillance_bavarde_ne_sonne_qu_une_fois(self):
        """Douze relevés d'une même session : une seule bulle."""
        e = _executeur()
        c = CentreNotifications(executer=e)
        for _ in range(12):
            c.pousser("alerte", "Caméra", "espion.exe", "camera_watch")

        assert len([x for x in e.journal if "ToastNotification" in x]) == 1

    def test_apres_acquittement_la_meme_alerte_repasse(self):
        """Une nouvelle session d'espionnage doit bien réalerter."""
        c = CentreNotifications(executer=_executeur())
        a = c.pousser("alerte", "Caméra", "espion.exe", "camera_watch")
        c.acquitter(a.identifiant)
        b = c.pousser("alerte", "Caméra", "espion.exe", "camera_watch")

        assert b is not a and len(c.lister()) == 2

    def test_un_corps_different_n_est_pas_la_meme_alerte(self):
        c = CentreNotifications(executer=_executeur())
        c.pousser("alerte", "Caméra", "espion.exe", "camera_watch")
        c.pousser("alerte", "Caméra", "autre.exe", "camera_watch")

        assert len(c.lister()) == 2


class TestListerAcquitterPurger:
    def test_la_plus_recente_d_abord(self):
        c = CentreNotifications(executer=_executeur())
        c.pousser("info", "un", "c", "s")
        c.pousser("info", "deux", "c", "s")

        assert [n.titre for n in c.lister()] == ["deux", "un"]

    def test_filtre_des_non_acquittees(self):
        c = CentreNotifications(executer=_executeur())
        a = c.pousser("info", "un", "c", "s")
        c.pousser("info", "deux", "c", "s")
        c.acquitter(a.identifiant)

        assert [n.titre for n in c.lister(non_acquittees_seulement=True)] == ["deux"]

    def test_acquitter_deux_fois_rend_faux(self):
        c = CentreNotifications(executer=_executeur())
        n = c.pousser("info", "t", "c", "s")

        assert c.acquitter(n.identifiant) is True
        assert c.acquitter(n.identifiant) is False

    def test_acquitter_un_identifiant_inconnu(self):
        c = CentreNotifications(executer=_executeur())
        assert c.acquitter("00000000") is False

    def test_purger_ote_les_anciennes(self):
        c = CentreNotifications(executer=_executeur())
        vieille = c.pousser("info", "vieille", "c", "s")
        time.sleep(0.01)
        frontiere = time.time()
        c.pousser("info", "neuve", "c", "s")

        assert c.purger(frontiere) == 1
        assert [n.titre for n in c.lister()] == ["neuve"]
        assert vieille.identifiant not in [n.identifiant for n in c.lister()]

    def test_purger_sans_rien_a_oter(self):
        c = CentreNotifications(executer=_executeur())
        c.pousser("info", "t", "c", "s")
        assert c.purger(0.0) == 0

    def test_le_journal_reste_borne(self):
        """Le centre vit aussi longtemps que l'application : sans borne, une
        surveillance bavarde finirait par peser plus que ce qu'elle surveille."""
        c = CentreNotifications(executer=_executeur(), capacite=10)
        for i in range(200):
            c.pousser("info", f"alerte {i}", "c", "s", systeme=False)

        listees = c.lister()
        assert len(listees) == 10
        assert listees[0].titre == "alerte 199", "les plus récentes sont gardées"
