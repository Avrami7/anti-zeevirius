"""
test_history.py
Couvre comfort/history.py — la vue chronologique unique qui agrège les
quatre mécanismes de réversibilité du projet.

Ce que ces tests protègent en priorité :

1. L'AGRÉGATION. Les quatre index existants n'ont ni le même format, ni le
   même champ de date, ni la même façon de marquer une entrée annulée. Si
   la traduction se trompe, l'utilisateur voit une action « annulable »
   qui ne l'est plus — ou pire, ne voit pas une action qu'il pourrait
   encore annuler.

2. LA ROBUSTESSE. On consulte l'historique quand quelque chose s'est mal
   passé. Un JSON tronqué, un dossier absent, un adaptateur qui lève : la
   vue doit continuer de répondre pour les autres sources. Un historique
   qui plante au moment où on en a besoin est un historique inutile.

3. LA DÉLÉGATION. `annuler()` ne réimplémente aucune restauration : il
   appelle le module d'origine. Les tests vérifient que c'est bien LE BON
   module qui est appelé, avec le bon identifiant — et qu'aucun module
   n'est appelé quand l'entrée n'est plus annulable.

Aucun test ne touche au système réel : `ANTIZEEVIRIUS_DATA_DIR` redirige
tous les chemins de `paths.py` sous `tmp_path`, et le registre Windows est
simulé par un faux module `winreg`.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import paths as _paths
from comfort import history
from comfort.history import (
    AdaptateurDemarrage,
    AdaptateurJournal,
    AdaptateurQuarantaine,
    AdaptateurRangement,
    AdaptateurSas,
    HistoriqueUnifie,
)

EPOCH_FILETIME = 11644473600


# ── Environnement de test ────────────────────────────────────────────────
@pytest.fixture
def donnees(tmp_path, monkeypatch):
    """Redirige TOUTES les données de l'application sous tmp_path.

    C'est la variable prévue par paths.py pour une installation portable ;
    elle sert ici à garantir qu'aucun test n'écrit dans la vraie quarantaine
    ni dans le vrai sas. Elle vérifie aussi, au passage, que le module lit
    ses chemins via paths.py et ne les a pas figés à l'import.
    """
    monkeypatch.setenv("ANTIZEEVIRIUS_DATA_DIR", str(tmp_path))
    for dossier in (_paths.quarantine_dir(), _paths.staging_dir(), _paths.organizer_log().parent):
        dossier.mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture(autouse=True)
def gestionnaires_propres():
    """Le registre des annulateurs est global : on le restaure après chaque
    test pour qu'un test n'en contamine pas un autre."""
    sauvegarde = dict(history._GESTIONNAIRES)
    yield
    history._GESTIONNAIRES.clear()
    history._GESTIONNAIRES.update(sauvegarde)


def iso(jours_avant=0, heures=12):
    return (datetime.now() - timedelta(days=jours_avant)).replace(
        hour=heures, minute=0, second=0, microsecond=0
    ).isoformat()


def ecrire_quarantaine(entrees):
    chemin = _paths.quarantine_dir() / "quarantine_index.json"
    chemin.write_text(json.dumps(entrees), encoding="utf-8")
    return chemin


def ecrire_sas(entrees):
    chemin = _paths.staging_dir() / "staging_index.json"
    chemin.write_text(json.dumps(entrees), encoding="utf-8")
    return chemin


def ecrire_rangement(entrees):
    chemin = _paths.organizer_log()
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(entrees), encoding="utf-8")
    return chemin


def entree_quarantaine(identifiant="q1", date=None, restaure=False, cree_fichier=True):
    nom = f"{identifiant}.quarantined"
    if cree_fichier and not restaure:
        (_paths.quarantine_dir() / nom).write_bytes(b"contenu isole")
    return {
        "id": identifiant,
        "original_path": f"/home/u/telechargements/{identifiant}.exe",
        "quarantined_name": nom,
        "quarantine_date": date or iso(1),
        "reason": "Signature connue",
        "detection_details": {"hash": "abc"},
        "restored": restaure,
    }


def entree_sas(identifiant="s1", date=None, cree_fichier=True):
    nom = f"{identifiant}_vieux.log"
    if cree_fichier:
        (_paths.staging_dir() / nom).write_bytes(b"journal")
    return {
        "id": identifiant,
        "original_path": f"/home/u/tmp/{identifiant}.log",
        "staged_name": nom,
        "date": date or iso(2),
        "reason": "Fichier technique temporaire (.log)",
    }


def deplacements_rangement(session="sess-1", nombre=2, date=None, annules=False):
    return [
        {
            "id": f"{session}-{i}",
            "session_id": session,
            "original_path": f"/home/u/atrier/fichier{i}.txt",
            "new_path": f"/home/u/atrier/01_Documents/fichier{i}.txt",
            "type": "file",
            "date": date or iso(3),
            "undone": annules,
        }
        for i in range(nombre)
    ]


# ── Faux registre Windows ────────────────────────────────────────────────
class FauxWinreg:
    """Simule le strict minimum de winreg utilisé par l'adaptateur.

    Le vrai module n'existe pas hors Windows : sans cette simulation, la
    quatrième source ne serait jamais testée ailleurs que sur Windows.
    """

    HKEY_CURRENT_USER = 1
    HKEY_LOCAL_MACHINE = 2
    KEY_READ = 0x20019

    def __init__(self, valeurs=None, erreur_ouverture=None, ecrit_le=None):
        self.valeurs = list(valeurs or [])
        self.erreur_ouverture = erreur_ouverture
        moment = ecrit_le if ecrit_le is not None else datetime.now() - timedelta(days=1)
        self.filetime = int((moment.timestamp() + EPOCH_FILETIME) * 10_000_000)
        self.ouvertures = []
        self.fermetures = 0

    def OpenKey(self, ruche, chemin, reserve, acces):
        if self.erreur_ouverture is not None:
            raise self.erreur_ouverture
        self.ouvertures.append((ruche, chemin))
        return "POIGNEE"

    def QueryInfoKey(self, cle):
        return (0, len(self.valeurs), self.filetime)

    def EnumValue(self, cle, index):
        if index >= len(self.valeurs):
            raise OSError("fin d'énumération")
        return self.valeurs[index]

    def CloseKey(self, cle):
        self.fermetures += 1


def historique_complet(winreg_module=None):
    """Les quatre adaptateurs + le journal, tous sous tmp_path."""
    return HistoriqueUnifie([
        AdaptateurQuarantaine(),
        AdaptateurSas(),
        AdaptateurRangement(),
        AdaptateurDemarrage(winreg_module=winreg_module or FauxWinreg()),
        AdaptateurJournal(),
    ])


# ── 1. Agrégation et tri ─────────────────────────────────────────────────
class TestAgregation:

    def test_les_quatre_sources_sont_agregees(self, donnees):
        ecrire_quarantaine([entree_quarantaine("q1", iso(1))])
        ecrire_sas([entree_sas("s1", iso(2))])
        ecrire_rangement(deplacements_rangement("sess-1", 3, iso(3)))
        faux = FauxWinreg([("HKCU|Spotify", "C:\\spotify.exe", 1)])

        resultat = historique_complet(faux).lister()

        assert resultat["ok"] is True
        sources = {e["source"] for e in resultat["data"]["entrees"]}
        assert sources == {"quarantaine", "sas", "rangement", "demarrage"}
        assert resultat["data"]["problemes"] == []

    def test_une_entree_par_session_de_rangement_pas_par_deplacement(self, donnees):
        ecrire_rangement(deplacements_rangement("sess-1", 5) + deplacements_rangement("sess-2", 2))

        entrees = historique_complet().lister()["data"]["entrees"]
        rangements = [e for e in entrees if e["source"] == "rangement"]

        assert len(rangements) == 2
        assert {e["details"]["deplacements"] for e in rangements} == {5, 2}

    def test_tri_du_plus_recent_au_plus_ancien(self, donnees):
        ecrire_quarantaine([entree_quarantaine("q1", iso(1))])
        ecrire_sas([entree_sas("s1", iso(5))])
        ecrire_rangement(deplacements_rangement("sess-1", 1, iso(3)))

        entrees = historique_complet(FauxWinreg()).lister()["data"]["entrees"]
        sources_ordonnees = [e["source"] for e in entrees]

        assert sources_ordonnees == ["quarantaine", "rangement", "sas"]

    def test_une_entree_sans_date_est_reléguee_en_fin_de_liste(self, donnees):
        ecrire_quarantaine([entree_quarantaine("q1", iso(10))])
        # Registre sans date exploitable : l'entrée n'a pas d'horodatage.
        faux = FauxWinreg([("HKCU|Spotify", "C:\\s.exe", 1)])
        faux.filetime = 0

        entrees = historique_complet(faux).lister()["data"]["entrees"]

        assert entrees[-1]["source"] == "demarrage"
        assert entrees[-1]["horodatage"] is None

    def test_limite_tronque_sans_fausser_le_total(self, donnees):
        ecrire_quarantaine([entree_quarantaine(f"q{i}", iso(i + 1)) for i in range(5)])

        resultat = historique_complet().lister(limite=2)

        assert len(resultat["data"]["entrees"]) == 2
        assert resultat["data"]["total"] == 5
        assert resultat["data"]["affichees"] == 2

    def test_chaque_entree_expose_le_contrat_commun(self, donnees):
        ecrire_sas([entree_sas("s1")])

        entree = historique_complet().lister()["data"]["entrees"][0]

        for champ in ("id", "source", "type_action", "description",
                      "horodatage", "annulable", "raison_non_annulable", "details"):
            assert champ in entree
        assert entree["id"] == "sas:s1"
        assert entree["annulable"] is True
        assert entree["raison_non_annulable"] is None

    def test_les_chemins_viennent_de_paths_et_pas_du_dossier_du_code(self, donnees):
        # Garde-fou d'architecture : installée, l'application écrit dans
        # %LOCALAPPDATA%. Un adaptateur qui figerait Path(__file__) lirait un
        # index vide sans jamais le dire.
        assert AdaptateurQuarantaine().chemin_index().is_relative_to(donnees)
        assert AdaptateurSas().chemin_index().is_relative_to(donnees)
        assert AdaptateurRangement().chemin_index().is_relative_to(donnees)
        assert AdaptateurJournal().chemin_index().is_relative_to(donnees)


# ── 2. Index absent, vide, corrompu, inattendu ───────────────────────────
class TestRobustesseDesIndex:

    def test_aucun_index_du_tout_ne_leve_pas(self, donnees):
        resultat = historique_complet().lister()

        assert resultat["ok"] is True
        assert resultat["data"]["entrees"] == []
        assert resultat["data"]["problemes"] == []

    def test_dossier_de_donnees_inexistant_ne_leve_pas(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANTIZEEVIRIUS_DATA_DIR", str(tmp_path / "jamais_cree"))

        resultat = historique_complet().lister()

        assert resultat["ok"] is True
        assert resultat["data"]["entrees"] == []

    def test_index_vide_nest_pas_signale_comme_anomalie(self, donnees):
        (_paths.quarantine_dir() / "quarantine_index.json").write_text("", encoding="utf-8")
        (_paths.staging_dir() / "staging_index.json").write_text("[]", encoding="utf-8")

        resultat = historique_complet().lister()

        assert resultat["data"]["problemes"] == []

    def test_index_corrompu_est_signale_et_nempeche_pas_les_autres(self, donnees):
        (_paths.quarantine_dir() / "quarantine_index.json").write_text(
            '[{"id": "q1", "original_pa', encoding="utf-8"
        )
        ecrire_sas([entree_sas("s1")])
        ecrire_rangement(deplacements_rangement("sess-1", 1))

        resultat = historique_complet().lister()

        assert resultat["ok"] is True
        sources = {e["source"] for e in resultat["data"]["entrees"]}
        assert sources == {"sas", "rangement"}
        problemes = {p["source"]: p["message"] for p in resultat["data"]["problemes"]}
        assert "quarantaine" in problemes
        assert "corrompu" in problemes["quarantaine"]

    def test_format_inattendu_est_signale(self, donnees):
        ecrire_quarantaine({"pas": "une liste"})

        resultat = historique_complet().lister()

        problemes = {p["source"]: p["message"] for p in resultat["data"]["problemes"]}
        assert "format inattendu" in problemes["quarantaine"]

    def test_entrees_ferraille_ignorees_les_valides_restent(self, donnees):
        ecrire_sas(["texte", 42, None, entree_sas("s1")])

        resultat = historique_complet().lister()

        assert [e["id"] for e in resultat["data"]["entrees"]] == ["sas:s1"]
        assert any("ignorée" in p["message"] for p in resultat["data"]["problemes"])

    def test_champs_manquants_nempechent_pas_laffichage(self, donnees):
        # Une entrée sans date, sans motif et sans nom de fichier isolé :
        # inexploitable pour annuler, mais l'utilisateur doit la VOIR.
        ecrire_quarantaine([{"id": "q1"}])

        entrees = historique_complet().lister()["data"]["entrees"]

        assert len(entrees) == 1
        assert entrees[0]["annulable"] is False
        assert "incomplète" in entrees[0]["raison_non_annulable"]

    def test_entree_sans_identifiant_est_ecartee(self, donnees):
        ecrire_quarantaine([{"original_path": "/x/y.exe", "quarantined_name": "z.quarantined"}])

        assert historique_complet().lister()["data"]["entrees"] == []

    def test_deplacement_sans_session_est_signale(self, donnees):
        ecrire_rangement([{"id": "m1", "original_path": "/a", "new_path": "/b", "undone": False}])

        resultat = historique_complet().lister()

        assert resultat["data"]["entrees"] == []
        assert any("session_id" in p["message"] for p in resultat["data"]["problemes"])


# ── 3. Un adaptateur en échec n'empêche pas les autres ───────────────────
class AdaptateurExplosif(AdaptateurQuarantaine):
    source = "explosif"
    libelle = "Adaptateur de test"

    def collecter(self):
        raise RuntimeError("index illisible, disque déconnecté")

    def annuler(self, identifiant_natif, entree):
        raise RuntimeError("boum")


class AdaptateurIncoherent(AdaptateurSas):
    source = "incoherent"
    libelle = "Adaptateur bavard"

    def collecter(self):
        return {"entrees": "pas une liste"}


class TestAdaptateurEnEchec:

    def test_un_adaptateur_qui_leve_est_signale_les_autres_repondent(self, donnees):
        ecrire_sas([entree_sas("s1")])
        historique = HistoriqueUnifie([AdaptateurExplosif(), AdaptateurSas()])

        resultat = historique.lister()

        assert resultat["ok"] is True
        assert [e["id"] for e in resultat["data"]["entrees"]] == ["sas:s1"]
        problemes = {p["source"]: p["message"] for p in resultat["data"]["problemes"]}
        assert "RuntimeError" in problemes["explosif"]

    def test_un_adaptateur_qui_renvoie_nimporte_quoi_est_tolere(self, donnees):
        ecrire_sas([entree_sas("s1")])
        historique = HistoriqueUnifie([AdaptateurIncoherent(), AdaptateurSas()])

        resultat = historique.lister()

        assert resultat["ok"] is True
        assert len(resultat["data"]["entrees"]) == 1

    def test_annuler_sur_un_adaptateur_en_echec_retourne_une_erreur_propre(self, donnees):
        historique = HistoriqueUnifie([AdaptateurExplosif()])

        resultat = historique.annuler("explosif:peu-importe")

        assert resultat["ok"] is False
        assert "RuntimeError" in resultat["error"]

    def test_le_journal_reste_disponible_meme_si_tout_le_reste_echoue(self, donnees):
        historique = HistoriqueUnifie([AdaptateurExplosif()])

        assert historique.enregistrer("test", "Une action V2")["ok"] is True
        resultat = historique.lister()
        assert [e["source"] for e in resultat["data"]["entrees"]] == ["journal"]


# ── 4. Entrées déjà annulées ─────────────────────────────────────────────
class TestDejaAnnulee:

    def test_quarantaine_restauree_nest_plus_annulable(self, donnees):
        entree = entree_quarantaine("q1", restaure=True)
        entree["restore_date"] = iso(0)
        ecrire_quarantaine([entree])

        affichee = historique_complet().lister()["data"]["entrees"][0]

        assert affichee["annulable"] is False
        assert "déjà restauré" in affichee["raison_non_annulable"]

    def test_annuler_une_entree_deja_annulee_ne_touche_pas_le_module(self, donnees):
        ecrire_quarantaine([entree_quarantaine("q1", restaure=True)])

        with patch("quarantine.quarantine_manager.QuarantineManager") as faux:
            resultat = historique_complet().annuler("quarantaine:q1")

        assert resultat["ok"] is False
        assert "déjà restauré" in resultat["error"]
        faux.assert_not_called()

    def test_session_de_rangement_entierement_annulee(self, donnees):
        ecrire_rangement(deplacements_rangement("sess-1", 2, annules=True))

        affichee = historique_complet().lister()["data"]["entrees"][0]

        assert affichee["annulable"] is False
        assert "déjà annulée" in affichee["raison_non_annulable"]

    def test_session_partiellement_annulee_reste_annulable(self, donnees):
        deplacements = deplacements_rangement("sess-1", 3)
        deplacements[0]["undone"] = True
        ecrire_rangement(deplacements)

        affichee = historique_complet().lister()["data"]["entrees"][0]

        assert affichee["annulable"] is True
        assert affichee["details"]["restants"] == 2

    def test_fichier_disparu_de_la_quarantaine_nest_plus_annulable(self, donnees):
        ecrire_quarantaine([entree_quarantaine("q1", cree_fichier=False)])

        affichee = historique_complet().lister()["data"]["entrees"][0]

        assert affichee["annulable"] is False
        assert "introuvable" in affichee["raison_non_annulable"]

    def test_fichier_purge_du_sas_nest_plus_annulable(self, donnees):
        ecrire_sas([entree_sas("s1", cree_fichier=False)])

        affichee = historique_complet().lister()["data"]["entrees"][0]

        assert affichee["annulable"] is False
        assert "purgé" in affichee["raison_non_annulable"]

    def test_annuler_relit_letat_courant_et_ne_se_fie_pas_a_laffichage(self, donnees):
        # L'entrée est annulable au moment de l'affichage, puis le fichier
        # disparaît (purge, restauration manuelle) avant le clic.
        ecrire_quarantaine([entree_quarantaine("q1")])
        historique = historique_complet()
        assert historique.lister()["data"]["entrees"][0]["annulable"] is True

        (_paths.quarantine_dir() / "q1.quarantined").unlink()

        with patch("quarantine.quarantine_manager.QuarantineManager") as faux:
            resultat = historique.annuler("quarantaine:q1")

        assert resultat["ok"] is False
        faux.assert_not_called()


# ── 5. Délégation au bon module ──────────────────────────────────────────
class TestDelegation:

    def test_quarantaine_delegue_a_quarantine_manager(self, donnees):
        ecrire_quarantaine([entree_quarantaine("q1")])

        with patch("quarantine.quarantine_manager.QuarantineManager") as faux:
            faux.return_value.restore_file.return_value = True
            resultat = historique_complet().annuler("quarantaine:q1")

        assert resultat["ok"] is True
        faux.assert_called_once_with(str(_paths.quarantine_dir()))
        faux.return_value.restore_file.assert_called_once_with("q1")
        assert resultat["data"]["source"] == "quarantaine"
        assert resultat["data"]["id"] == "quarantaine:q1"

    def test_sas_delegue_a_file_triage(self, donnees):
        ecrire_sas([entree_sas("s1")])

        with patch("optimizer.file_triage.FileTriage") as faux:
            faux.return_value.restore_from_staging.return_value = True
            resultat = historique_complet().annuler("sas:s1")

        assert resultat["ok"] is True
        faux.assert_called_once_with(str(_paths.staging_dir()))
        faux.return_value.restore_from_staging.assert_called_once_with("s1")

    def test_rangement_delegue_a_undo_session(self, donnees):
        ecrire_rangement(deplacements_rangement("sess-1", 2))

        with patch("optimizer.folder_organizer.FolderOrganizer") as faux:
            faux.return_value.undo_session.return_value = {"restored": 2, "errors": []}
            resultat = historique_complet().annuler("rangement:sess-1")

        assert resultat["ok"] is True
        faux.assert_called_once_with(str(_paths.organizer_log()))
        faux.return_value.undo_session.assert_called_once_with("sess-1")
        assert resultat["data"]["restaures"] == 2

    def test_demarrage_delegue_a_restore_registry_item(self, donnees):
        faux_registre = FauxWinreg([("HKLM|Spotify", "C:\\spotify.exe", 1)])
        gestionnaire = MagicMock()
        gestionnaire.restore_registry_item.return_value = True
        historique = HistoriqueUnifie([
            AdaptateurDemarrage(winreg_module=faux_registre, fabrique=lambda: gestionnaire)
        ])

        resultat = historique.annuler("demarrage:HKLM|Spotify")

        assert resultat["ok"] is True
        gestionnaire.restore_registry_item.assert_called_once_with("HKLM", "Spotify")

    def test_annuler_une_quarantaine_ne_reveille_pas_les_autres_modules(self, donnees):
        ecrire_quarantaine([entree_quarantaine("q1")])
        ecrire_sas([entree_sas("s1")])
        ecrire_rangement(deplacements_rangement("sess-1", 1))

        with patch("quarantine.quarantine_manager.QuarantineManager") as q, \
             patch("optimizer.file_triage.FileTriage") as t, \
             patch("optimizer.folder_organizer.FolderOrganizer") as f:
            q.return_value.restore_file.return_value = True
            historique_complet().annuler("quarantaine:q1")

        t.assert_not_called()
        f.assert_not_called()

    def test_refus_du_module_remonte_en_erreur_lisible(self, donnees):
        ecrire_quarantaine([entree_quarantaine("q1")])

        with patch("quarantine.quarantine_manager.QuarantineManager") as faux:
            faux.return_value.restore_file.return_value = False
            resultat = historique_complet().annuler("quarantaine:q1")

        assert resultat["ok"] is False
        assert resultat["unavailable"] is False
        assert "refusée" in resultat["error"]

    def test_exception_du_module_ne_remonte_pas_a_lappelant(self, donnees):
        ecrire_sas([entree_sas("s1")])

        with patch("optimizer.file_triage.FileTriage") as faux:
            faux.return_value.restore_from_staging.side_effect = PermissionError("verrou")
            resultat = historique_complet().annuler("sas:s1")

        assert resultat["ok"] is False
        assert "PermissionError" in resultat["error"]

    def test_rangement_en_echec_total_est_une_erreur(self, donnees):
        ecrire_rangement(deplacements_rangement("sess-1", 1))

        with patch("optimizer.folder_organizer.FolderOrganizer") as faux:
            faux.return_value.undo_session.return_value = {"restored": 0, "errors": ["accès refusé"]}
            resultat = historique_complet().annuler("rangement:sess-1")

        assert resultat["ok"] is False
        assert "accès refusé" in resultat["error"]

    def test_identifiant_invalide(self, donnees):
        assert historique_complet().annuler("sans-separateur")["ok"] is False
        assert historique_complet().annuler("")["ok"] is False
        assert historique_complet().annuler(None)["ok"] is False
        assert historique_complet().annuler("quarantaine:")["ok"] is False

    def test_source_inconnue(self, donnees):
        resultat = historique_complet().annuler("pare_feu:regle-42")

        assert resultat["ok"] is False
        assert "source inconnue" in resultat["error"]

    def test_entree_introuvable(self, donnees):
        ecrire_quarantaine([entree_quarantaine("q1")])

        resultat = historique_complet().annuler("quarantaine:q-inexistant")

        assert resultat["ok"] is False
        assert "introuvable" in resultat["error"]


# ── 6. Annulation réelle, de bout en bout (sans mock) ────────────────────
class TestAnnulationReelle:
    """Les mocks prouvent la délégation ; ces tests prouvent que la chaîne
    complète fonctionne vraiment, avec les modules d'origine intacts."""

    def test_restauration_reelle_dun_fichier_en_quarantaine(self, donnees, tmp_path):
        from quarantine.quarantine_manager import QuarantineManager

        origine = tmp_path / "atelier" / "suspect.exe"
        origine.parent.mkdir(parents=True, exist_ok=True)
        origine.write_bytes(b"charge utile")

        gestionnaire = QuarantineManager(str(_paths.quarantine_dir()))
        identifiant = gestionnaire.quarantine_file(str(origine), "Test", {})
        assert not origine.exists()

        historique = historique_complet()
        entree = historique.lister()["data"]["entrees"][0]
        assert entree["id"] == f"quarantaine:{identifiant}"
        assert entree["annulable"] is True

        resultat = historique.annuler(entree["id"])

        assert resultat["ok"] is True
        assert origine.exists()
        # Et la trace reste, marquée non annulable.
        apres = historique.lister()["data"]["entrees"][0]
        assert apres["annulable"] is False

    def test_recuperation_reelle_dun_fichier_du_sas(self, donnees, tmp_path):
        from optimizer.file_triage import FileTriage

        origine = tmp_path / "atelier" / "vieux.log"
        origine.parent.mkdir(parents=True, exist_ok=True)
        origine.write_bytes(b"journal")

        triage = FileTriage(str(_paths.staging_dir()))
        identifiant = triage.move_to_staging(str(origine), "Fichier technique")
        assert not origine.exists()

        historique = historique_complet()
        resultat = historique.annuler(f"sas:{identifiant}")

        assert resultat["ok"] is True
        assert origine.exists()
        # Limite du mécanisme d'origine : la ligne disparaît de l'index, donc
        # de l'historique. On la documente ici pour qu'un changement de
        # comportement de file_triage soit visible.
        assert historique.lister()["data"]["entrees"] == []

    def test_annulation_reelle_dune_session_de_rangement(self, donnees, tmp_path):
        origine = tmp_path / "atrier" / "note.txt"
        rangee = tmp_path / "atrier" / "01_Documents" / "note.txt"
        rangee.parent.mkdir(parents=True, exist_ok=True)
        rangee.write_text("contenu", encoding="utf-8")

        ecrire_rangement([{
            "id": "m1", "session_id": "sess-1",
            "original_path": str(origine), "new_path": str(rangee),
            "type": "file", "date": iso(1), "undone": False,
        }])

        resultat = historique_complet().annuler("rangement:sess-1")

        assert resultat["ok"] is True
        assert origine.exists()
        assert not rangee.exists()


# ── 7. Démarrage Windows (registre simulé) ───────────────────────────────
class TestAdaptateurDemarrage:

    def test_lecture_de_la_cle_de_sauvegarde(self, donnees):
        faux = FauxWinreg([
            ("HKCU|Spotify", "C:\\spotify.exe", 1),
            ("HKLM|Teams", "C:\\teams.exe", 1),
        ])

        entrees = AdaptateurDemarrage(winreg_module=faux).collecter()["entrees"]

        assert [e["details"]["nom"] for e in entrees] == ["Spotify", "Teams"]
        assert [e["details"]["ruche"] for e in entrees] == ["HKCU", "HKLM"]
        assert all(e["annulable"] for e in entrees)
        assert faux.fermetures == 1

    def test_la_cle_lue_est_celle_de_startup_manager(self, donnees):
        from optimizer.startup_manager import BACKUP_KEY_PATH

        faux = FauxWinreg([("HKCU|X", "c", 1)])
        AdaptateurDemarrage(winreg_module=faux).collecter()

        assert faux.ouvertures[0][1] == BACKUP_KEY_PATH

    def test_la_date_du_registre_est_marquee_approximative(self, donnees):
        # Le registre date la CLÉ, pas chaque valeur : deux programmes
        # désactivés à des semaines d'écart portent la même date.
        faux = FauxWinreg([("HKCU|A", "a", 1), ("HKCU|B", "b", 1)])

        entrees = AdaptateurDemarrage(winreg_module=faux).collecter()["entrees"]

        assert entrees[0]["horodatage"] == entrees[1]["horodatage"]
        assert entrees[0]["details"]["horodatage_approximatif"] is True

    def test_cle_absente_nest_pas_une_anomalie(self, donnees):
        faux = FauxWinreg(erreur_ouverture=FileNotFoundError("clé absente"))

        resultat = AdaptateurDemarrage(winreg_module=faux).collecter()

        assert resultat["entrees"] == []
        assert resultat["probleme"] is None

    def test_cle_illisible_est_signalee(self, donnees):
        faux = FauxWinreg(erreur_ouverture=PermissionError("accès refusé"))

        resultat = AdaptateurDemarrage(winreg_module=faux).collecter()

        assert "illisible" in resultat["probleme"]

    def test_nom_de_sauvegarde_inattendu_affiche_mais_non_annulable(self, donnees):
        faux = FauxWinreg([("SansRuche", "C:\\x.exe", 1)])

        entree = AdaptateurDemarrage(winreg_module=faux).collecter()["entrees"][0]

        assert entree["annulable"] is False
        assert "ruche" in entree["raison_non_annulable"]

    def test_hors_windows_la_source_est_indisponible_pas_en_erreur(self, donnees):
        adaptateur = AdaptateurDemarrage(winreg_module=None)
        adaptateur._module_registre = lambda: None
        historique = HistoriqueUnifie([adaptateur, AdaptateurSas()])
        ecrire_sas([entree_sas("s1")])

        resultat = historique.lister()

        assert resultat["ok"] is True
        assert len(resultat["data"]["entrees"]) == 1
        probleme = [p for p in resultat["data"]["problemes"] if p["source"] == "demarrage"][0]
        assert probleme["indisponible"] is True

        reponse = historique.annuler("demarrage:HKCU|Spotify")
        assert reponse["ok"] is False
        assert reponse["unavailable"] is True


# ── 8. Journal des modules V2 ────────────────────────────────────────────
class TestJournalV2:

    def test_enregistrer_puis_lister(self, donnees):
        historique = historique_complet()

        cree = historique.enregistrer(
            "mode_incident", "Réseau coupé et 3 processus gelés",
            details={"processus": 3},
        )

        assert cree["ok"] is True
        entree = historique.lister()["data"]["entrees"][0]
        assert entree["id"] == cree["data"]["id"]
        assert entree["type_action"] == "mode_incident"
        assert entree["details"]["processus"] == 3

    def test_action_sans_annulation_declaree_est_tracee_mais_non_annulable(self, donnees):
        historique = historique_complet()
        historique.enregistrer("analyse", "Analyse complète du disque C:")

        entree = historique.lister()["data"]["entrees"][0]

        assert entree["annulable"] is False
        assert "non réversible" in entree["raison_non_annulable"]

    def test_gestionnaire_absent_est_dit_franchement(self, donnees):
        historique = historique_complet()
        historique.enregistrer("regle_pare_feu", "Sortie bloquée pour jeu.exe",
                               annulation={"gestionnaire": "pare_feu",
                                           "parametres": {"regle": "AZ_jeu"}})

        entree = historique.lister()["data"]["entrees"][0]

        assert entree["annulable"] is False
        assert "pare_feu" in entree["raison_non_annulable"]

    def test_annulation_par_gestionnaire_enregistre(self, donnees):
        appels = []
        history.enregistrer_annulateur("pare_feu", lambda p: appels.append(p) or True)

        historique = historique_complet()
        cree = historique.enregistrer("regle_pare_feu", "Sortie bloquée pour jeu.exe",
                                      annulation={"gestionnaire": "pare_feu",
                                                  "parametres": {"regle": "AZ_jeu"}})
        entree = historique.lister()["data"]["entrees"][0]
        assert entree["annulable"] is True

        resultat = historique.annuler(cree["data"]["id"])

        assert resultat["ok"] is True
        assert appels == [{"regle": "AZ_jeu"}]
        apres = historique.lister()["data"]["entrees"][0]
        assert apres["annulable"] is False
        assert "déjà annulée" in apres["raison_non_annulable"]

    def test_annuler_deux_fois_est_refuse(self, donnees):
        history.enregistrer_annulateur("services", lambda p: True)
        historique = historique_complet()
        cree = historique.enregistrer("service_desactive", "Service Fax désactivé",
                                      annulation={"gestionnaire": "services",
                                                  "parametres": {"nom": "Fax"}})
        historique.annuler(cree["data"]["id"])

        second = historique.annuler(cree["data"]["id"])

        assert second["ok"] is False
        assert "déjà annulée" in second["error"]

    def test_gestionnaire_qui_leve_ne_marque_pas_lentree_annulee(self, donnees):
        def explose(_):
            raise OSError("service verrouillé")

        history.enregistrer_annulateur("services", explose)
        historique = historique_complet()
        cree = historique.enregistrer("service_desactive", "Service Fax désactivé",
                                      annulation={"gestionnaire": "services", "parametres": {}})

        resultat = historique.annuler(cree["data"]["id"])

        assert resultat["ok"] is False
        assert "OSError" in resultat["error"]
        assert historique.lister()["data"]["entrees"][0]["annulable"] is True

    def test_gestionnaire_qui_refuse_remonte_son_message(self, donnees):
        history.enregistrer_annulateur(
            "pare_feu", lambda p: {"ok": False, "error": "droits administrateur requis"}
        )
        historique = historique_complet()
        cree = historique.enregistrer("regle_pare_feu", "Blocage sortant",
                                      annulation={"gestionnaire": "pare_feu", "parametres": {}})

        resultat = historique.annuler(cree["data"]["id"])

        assert resultat["ok"] is False
        assert "administrateur" in resultat["error"]

    def test_enregistrement_invalide_est_refuse_sans_lever(self, donnees):
        historique = historique_complet()

        assert historique.enregistrer("", "description")["ok"] is False
        assert historique.enregistrer("action", "")["ok"] is False
        assert historique.enregistrer("action", "desc", annulation={"sans": "gestionnaire"})["ok"] is False

    def test_journal_corrompu_nempeche_pas_la_vue_ni_necrase_le_fichier(self, donnees):
        chemin = AdaptateurJournal().chemin_index()
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text('[{"id": "j1", "type_ac', encoding="utf-8")
        ecrire_sas([entree_sas("s1")])
        historique = historique_complet()

        resultat = historique.lister()

        assert [e["id"] for e in resultat["data"]["entrees"]] == ["sas:s1"]
        assert any(p["source"] == "journal" for p in resultat["data"]["problemes"])
        # Le fichier abîmé est laissé intact : on ne détruit pas une trace
        # peut-être récupérable à la main.
        assert historique.enregistrer("action", "desc")["ok"] is False
        assert chemin.read_text(encoding="utf-8") == '[{"id": "j1", "type_ac'

    def test_le_gestionnaire_ne_vient_jamais_du_fichier_json(self, donnees):
        # Garde-fou de sécurité : le journal ne stocke qu'un NOM. Une entrée
        # qui prétendrait embarquer du code n'est pas exécutable.
        chemin = AdaptateurJournal().chemin_index()
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(json.dumps([{
            "id": "j1", "type_action": "malveillant", "description": "Injection",
            "horodatage": iso(0),
            "annulation": {"gestionnaire": "os.system", "parametres": {"cmd": "echo pwn"}},
            "annulee": False,
        }]), encoding="utf-8")
        historique = historique_complet()

        entree = historique.lister()["data"]["entrees"][0]
        assert entree["annulable"] is False

        resultat = historique.annuler("journal:j1")
        assert resultat["ok"] is False


# ── 9. Filtres ───────────────────────────────────────────────────────────
class TestFiltres:

    @pytest.fixture
    def peuple(self, donnees):
        ecrire_quarantaine([entree_quarantaine("q1", iso(1)),
                            entree_quarantaine("q2", iso(30), restaure=True)])
        ecrire_sas([entree_sas("s1", iso(2))])
        ecrire_rangement(deplacements_rangement("sess-1", 2, iso(3)))
        return historique_complet()

    def test_filtre_par_source_en_chaine(self, peuple):
        entrees = peuple.lister(filtre="sas")["data"]["entrees"]

        assert [e["id"] for e in entrees] == ["sas:s1"]

    def test_filtre_par_liste_de_sources(self, peuple):
        entrees = peuple.lister(filtre=["sas", "rangement"])["data"]["entrees"]

        assert {e["source"] for e in entrees} == {"sas", "rangement"}

    def test_filtre_par_type_daction(self, peuple):
        entrees = peuple.lister(filtre={"type_action": "mise_en_quarantaine"})["data"]["entrees"]

        assert len(entrees) == 2

    def test_filtre_annulable_seulement(self, peuple):
        entrees = peuple.lister(filtre={"annulable": True})["data"]["entrees"]

        assert all(e["annulable"] for e in entrees)
        assert "quarantaine:q2" not in [e["id"] for e in entrees]

    def test_filtre_par_fenetre_de_temps(self, peuple):
        entrees = peuple.lister(filtre={"depuis": iso(4)})["data"]["entrees"]

        assert "quarantaine:q2" not in [e["id"] for e in entrees]
        assert len(entrees) == 3

    def test_filtre_textuel(self, peuple):
        entrees = peuple.lister(filtre={"texte": "q1.exe"})["data"]["entrees"]

        assert [e["id"] for e in entrees] == ["quarantaine:q1"]

    def test_filtre_predicat(self, peuple):
        entrees = peuple.lister(filtre=lambda e: e["source"] == "rangement")["data"]["entrees"]

        assert [e["source"] for e in entrees] == ["rangement"]

    def test_filtre_incomprehensible_est_ignore_et_signale(self, peuple):
        resultat = peuple.lister(filtre=42)

        assert len(resultat["data"]["entrees"]) == 4
        assert any(p["source"] == "filtre" for p in resultat["data"]["problemes"])

    def test_predicat_qui_leve_ne_vide_pas_la_vue(self, peuple):
        def predicat(entree):
            raise ValueError("prédicat bancal")

        resultat = peuple.lister(filtre=predicat)

        assert len(resultat["data"]["entrees"]) == 4
        assert any(p["source"] == "filtre" for p in resultat["data"]["problemes"])


# ── 10. Fonctions de confort ─────────────────────────────────────────────
class TestAccesDirect:

    def test_les_fonctions_de_module_partagent_une_instance(self, donnees):
        history._INSTANCE = None
        try:
            ecrire_sas([entree_sas("s1")])

            resultat = history.lister(limite=10)

            assert resultat["ok"] is True
            assert any(e["id"] == "sas:s1" for e in resultat["data"]["entrees"])
            assert history.historique_par_defaut() is history.historique_par_defaut()
        finally:
            history._INSTANCE = None

    def test_enregistrer_annulateur_refuse_une_declaration_invalide(self):
        with pytest.raises(ValueError):
            history.enregistrer_annulateur("", lambda p: True)
        with pytest.raises(ValueError):
            history.enregistrer_annulateur("x", "pas une fonction")
