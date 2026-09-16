"""
Tests de assistant/digest.py.

Le résumé quotidien est un rapport, pas une commande : il agrège ce qui est
déjà connu et ne déclenche rien. Les tests portent donc sur trois promesses :

  * **il ne lance rien** — aucune analyse, aucun accès disque, aucun réseau ;
  * **il ne ment pas sur la période** — ce qui est hors fenêtre reste dehors,
    et une fenêtre inversée ne devient pas un rapport vide (« il ne s'est rien
    passé » est le mensonge le plus coûteux qu'un résumé puisse produire) ;
  * **il est reproductible** — deux résumés des mêmes données sont identiques,
    sinon on ne peut pas les comparer d'un jour sur l'autre.

Les sources sont acceptées aussi bien en objets du projet qu'en dictionnaires :
les appelants n'ont pas tous les mêmes objets sous la main.
"""

import pytest

from assistant.digest import construire_resume
from assistant.notify import Notification
from assistant.proactive import Suggestion
from assistant.telemetry import Echantillon

T0 = 1_700_000_000.0
T1 = T0 + 86400.0


def _notification(horodatage, gravite="info", titre="titre", corps="corps",
                  source="camera_watch", identifiant="abc123"):
    return Notification(identifiant=identifiant, horodatage=horodatage,
                        gravite=gravite, titre=titre, corps=corps,
                        source=source)


# ═══════════════════════════════════════════════════════════════════
# Forme du rapport
# ═══════════════════════════════════════════════════════════════════
class TestForme:
    def test_les_quatre_sections_sont_toujours_la(self):
        r = construire_resume(T0, T1, {})
        assert set(r) == {"periode", "faits", "alertes", "suggestions"}
        assert r["faits"] == [] and r["alertes"] == [] and r["suggestions"] == []

    def test_la_periode_est_datee_lisiblement(self):
        p = construire_resume(T0, T1, {})["periode"]
        assert p["depuis"] == T0 and p["jusqu_a"] == T1
        assert p["duree_heures"] == 24.0
        assert p["depuis_iso"] and p["jusqu_a_iso"]

    def test_sources_absentes_ou_farfelues_tolerees(self):
        r = construire_resume(T0, T1, {"notifications": None,
                                       "telemetrie": 42,
                                       "suggestions": "rien",
                                       "inconnu": [1, 2, 3]})
        assert r["faits"] == [] and r["alertes"] == []

    def test_periode_inversee_redressee(self):
        """Une fenêtre à l'envers est une erreur d'appel ; rendre un rapport
        vide laisserait croire qu'il ne s'est rien passé."""
        r = construire_resume(T1, T0, {"notifications": [
            _notification(T0 + 10, gravite="critique")]})
        assert r["periode"]["depuis"] == T0
        assert len(r["alertes"]) == 1


# ═══════════════════════════════════════════════════════════════════
# Notifications
# ═══════════════════════════════════════════════════════════════════
class TestNotifications:
    def test_alertes_et_faits_sont_separes(self):
        r = construire_resume(T0, T1, {"notifications": [
            _notification(T0 + 1, gravite="info", identifiant="i1"),
            _notification(T0 + 2, gravite="alerte", identifiant="i2"),
            _notification(T0 + 3, gravite="critique", identifiant="i3"),
        ]})
        assert [a["identifiant"] for a in r["alertes"]] == ["i3", "i2"]
        assert [f["categorie"] for f in r["faits"]] == ["notification"]

    def test_hors_periode_ecarte(self):
        r = construire_resume(T0, T1, {"notifications": [
            _notification(T0 - 10, gravite="critique", identifiant="vieille"),
            _notification(T1 + 10, gravite="critique", identifiant="future"),
            _notification(T0 + 10, gravite="critique", identifiant="bonne"),
        ]})
        assert [a["identifiant"] for a in r["alertes"]] == ["bonne"]

    def test_bornes_incluses(self):
        r = construire_resume(T0, T1, {"notifications": [
            _notification(T0, gravite="alerte", identifiant="debut"),
            _notification(T1, gravite="alerte", identifiant="fin"),
        ]})
        assert len(r["alertes"]) == 2

    def test_dictionnaires_acceptes_comme_objets(self):
        r = construire_resume(T0, T1, {"notifications": [
            {"identifiant": "d1", "horodatage": T0 + 5, "gravite": "alerte",
             "titre": "T", "corps": "C", "source": "s", "acquittee": True},
        ]})
        assert r["alertes"][0]["titre"] == "T"
        assert r["alertes"][0]["acquittee"] is True

    def test_notification_sans_date_ecartee(self):
        """Dater d'office un enregistrement reviendrait à affirmer une chose
        qu'on ne sait pas."""
        r = construire_resume(T0, T1, {"notifications": [
            {"identifiant": "x", "gravite": "critique", "titre": "T"}]})
        assert r["alertes"] == []

    def test_les_alertes_les_plus_recentes_d_abord(self):
        r = construire_resume(T0, T1, {"notifications": [
            _notification(T0 + 1, gravite="alerte", identifiant="a"),
            _notification(T0 + 900, gravite="alerte", identifiant="b"),
        ]})
        assert [a["identifiant"] for a in r["alertes"]] == ["b", "a"]


# ═══════════════════════════════════════════════════════════════════
# Télémétrie
# ═══════════════════════════════════════════════════════════════════
class TestTelemetrie:
    def _echantillons(self):
        return [Echantillon(T0 + 10, 10.0, 50.0, 70.0, None),
                Echantillon(T0 + 20, 90.0, 60.0, 70.0, 45.0)]

    def test_une_ligne_de_synthese(self):
        r = construire_resume(T0, T1, {"telemetrie": self._echantillons()})
        fait = [f for f in r["faits"] if f["categorie"] == "telemetrie"][0]
        assert fait["detail"]["cpu_moyen"] == 50.0
        assert fait["detail"]["echantillons"] == 2

    def test_le_pic_est_conserve(self):
        """Un chiffrement d'un quart d'heure disparaît dans une moyenne sur
        vingt-quatre heures : c'est le maximum qui porte l'information."""
        r = construire_resume(T0, T1, {"telemetrie": self._echantillons()})
        fait = [f for f in r["faits"] if f["categorie"] == "telemetrie"][0]
        assert fait["detail"]["cpu_max"] == 90.0
        assert "pic processeur 90 %" in fait["libelle"]

    def test_echantillons_hors_periode_ignores(self):
        r = construire_resume(T0, T1, {"telemetrie": [
            Echantillon(T0 - 5000, 100.0, 100.0, 100.0, None)]})
        assert [f for f in r["faits"] if f["categorie"] == "telemetrie"] == []

    def test_sans_telemetrie_pas_de_ligne(self):
        assert construire_resume(T0, T1, {"telemetrie": []})["faits"] == []


# ═══════════════════════════════════════════════════════════════════
# Journal et faits libres
# ═══════════════════════════════════════════════════════════════════
class TestFaits:
    def test_decisions_reprises(self):
        r = construire_resume(T0, T1, {"decisions": [
            {"horodatage": T0 + 5, "evenement": "quarantaine.ajout",
             "detail": {"fichier": "x.exe"}}]})
        fait = r["faits"][0]
        assert fait["categorie"] == "decision"
        assert fait["libelle"] == "quarantaine.ajout"
        assert fait["detail"]["fichier"] == "x.exe"

    def test_nom_de_champ_de_date_alternatif(self):
        """Le contrat ne fige pas le nom de la clé de date du journal :
        perdre tout l'historique sur un mot serait absurde."""
        r = construire_resume(T0, T1, {"decisions": [
            {"ts": T0 + 5, "evenement": "scan.fini"}]})
        assert len(r["faits"]) == 1

    def test_evenements_libres(self):
        r = construire_resume(T0, T1, {"evenements": [
            {"horodatage": T0 + 7, "categorie": "scan",
             "libelle": "analyse de C:\\Users"}]})
        assert r["faits"][0]["categorie"] == "scan"

    def test_faits_dans_l_ordre_chronologique(self):
        r = construire_resume(T0, T1, {"evenements": [
            {"horodatage": T0 + 300, "libelle": "second"},
            {"horodatage": T0 + 100, "libelle": "premier"},
        ]})
        assert [f["libelle"] for f in r["faits"]] == ["premier", "second"]


# ═══════════════════════════════════════════════════════════════════
# Suggestions
# ═══════════════════════════════════════════════════════════════════
class TestSuggestions:
    def test_triees_par_urgence_decroissante(self):
        r = construire_resume(T0, T1, {"suggestions": [
            Suggestion("rangement.sas", "des fichiers attendent", 0),
            Suggestion("securite.camera", "caméra active", 3),
            Suggestion("protection.temps_reel", "surveillance arrêtée", 2),
        ]})
        assert [s["urgence"] for s in r["suggestions"]] == [3, 2, 0]

    def test_les_suggestions_ne_sont_pas_datees(self):
        """Une suggestion porte sur maintenant, pas sur la période : la
        filtrer sur la fenêtre la ferait disparaître sans raison."""
        r = construire_resume(T0, T1, {"suggestions": [
            {"capacite": "x", "motif": "m", "urgence": 1}]})
        assert len(r["suggestions"]) == 1

    def test_urgence_illisible_ramenee_a_zero(self):
        r = construire_resume(T0, T1, {"suggestions": [
            {"capacite": "x", "motif": "m", "urgence": "beaucoup"}]})
        assert r["suggestions"][0]["urgence"] == 0


# ═══════════════════════════════════════════════════════════════════
# Lecture seule et reproductibilité
# ═══════════════════════════════════════════════════════════════════
class TestLectureSeule:
    def test_aucun_acces_disque_ni_reseau(self, monkeypatch):
        """Un résumé qui lancerait une analyse chaque matin serait une charge
        périodique déguisée en information."""
        import builtins
        import os
        import socket
        import subprocess

        def interdit(*a, **k):
            raise AssertionError("construire_resume() a agi au lieu d'agréger")

        for cible, nom in ((builtins, "open"), (os, "listdir"), (os, "stat"),
                           (socket, "socket"), (subprocess, "run"),
                           (subprocess, "Popen")):
            monkeypatch.setattr(cible, nom, interdit)

        construire_resume(T0, T1, {
            "notifications": [_notification(T0 + 1, gravite="alerte")],
            "telemetrie": [Echantillon(T0 + 2, 1.0, 2.0, 3.0, None)],
            "decisions": [{"horodatage": T0 + 3, "evenement": "e"}],
            "suggestions": [Suggestion("c", "m", 1)],
        })

    def test_resultat_reproductible(self):
        sources = {
            "notifications": [_notification(T0 + 1, gravite="alerte",
                                            identifiant="a"),
                              _notification(T0 + 2, identifiant="b")],
            "telemetrie": [Echantillon(T0 + 3, 1.0, 2.0, 3.0, None)],
            "suggestions": [Suggestion("c", "m", 1), Suggestion("a", "m", 1)],
        }
        assert construire_resume(T0, T1, sources) == construire_resume(T0, T1, sources)

    def test_les_sources_ne_sont_pas_modifiees(self):
        notifications = [_notification(T0 + 1, gravite="alerte")]
        sources = {"notifications": notifications}
        construire_resume(T0, T1, sources)

        assert sources["notifications"] is notifications
        assert len(notifications) == 1
