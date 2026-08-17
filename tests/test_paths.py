"""
Tests de paths.py — séparation ressources embarquées / données utilisateur.

Ces tests couvrent un comportement qui ne se manifeste QUE dans l'exécutable
installé, jamais pendant le développement : c'est précisément pour cela qu'il
doit être testé ici. Une régression sur ce module ne se verrait autrement
qu'après installation chez l'utilisateur, sous la forme d'une quarantaine qui
n'enregistre rien ou d'une interface qui ne se charge pas.

Le mode « gelé » est simulé en posant `sys.frozen` et `sys._MEIPASS`, comme le
fait PyInstaller au démarrage.
"""

import importlib
import sys

import pytest

import paths as paths_module


@pytest.fixture
def gele(tmp_path, monkeypatch):
    """Simule l'exécutable gelé : bundle en lecture seule + données isolées."""
    bundle = tmp_path / "bundle"
    donnees = tmp_path / "donnees"
    (bundle / "signatures").mkdir(parents=True)
    (bundle / "gui" / "web").mkdir(parents=True)
    (bundle / "signatures" / "malicious_hashes.txt").write_text("aaa\n", encoding="utf-8")
    (bundle / "signatures" / "rules.yar").write_text("rule R {condition: true}\n", encoding="utf-8")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv("ANTIZEEVIRIUS_DATA_DIR", str(donnees))
    p = importlib.reload(paths_module)
    yield p, bundle, donnees
    importlib.reload(paths_module)


class TestModeDeveloppement:
    """Hors gel, rien ne doit changer : c'est ce qui garantit que les 379
    autres tests continuent de porter sur le même comportement."""

    def test_non_gele_par_defaut(self):
        p = importlib.reload(paths_module)
        assert p.is_frozen() is False

    def test_ressources_et_donnees_dans_le_projet(self, monkeypatch):
        monkeypatch.delenv("ANTIZEEVIRIUS_DATA_DIR", raising=False)
        p = importlib.reload(paths_module)
        racine = p.resource_path()
        assert p.data_path() == racine
        assert (racine / "main.py").exists(), "on doit bien pointer sur le projet"


class TestModeGele:
    def test_ressources_lues_depuis_le_bundle(self, gele):
        p, bundle, _ = gele
        assert p.is_frozen() is True
        assert p.resource_path("gui", "web") == bundle / "gui" / "web"
        assert p.resource_path("gui", "web").is_dir()

    def test_donnees_ecrites_hors_du_bundle(self, gele):
        p, bundle, donnees = gele
        assert p.data_path() == donnees
        # Le point critique : aucune écriture ne doit viser le bundle, qui
        # correspond à Program Files (interdit) ou au dossier temporaire de
        # PyInstaller (effacé à la fermeture).
        for chemin in (p.quarantine_dir(), p.staging_dir(), p.logs_dir(),
                       p.cache_dir(), p.organizer_log(), p.signatures_dir()):
            assert bundle not in chemin.parents, f"{chemin} écrirait dans le bundle"

    def test_arborescence_creee_au_premier_lancement(self, gele):
        p, _, donnees = gele
        p.ensure_user_data()
        attendus = {"signatures", "logs", "quarantine_storage",
                    "triage_staging", "cache", "organizer_logs"}
        assert attendus <= {d.name for d in donnees.iterdir()}

    def test_bases_de_signatures_amorcees_depuis_le_bundle(self, gele):
        p, _, _ = gele
        p.ensure_user_data()
        presents = {f.name for f in p.signatures_dir().iterdir()}
        assert {"malicious_hashes.txt", "rules.yar"} <= presents

    def test_base_enrichie_par_l_utilisateur_survit_a_une_mise_a_jour(self, gele):
        """Le scénario qui compte : l'utilisateur alimente sa base de hashes,
        une nouvelle version est installée. Ré-amorcer ne doit RIEN écraser."""
        p, _, _ = gele
        p.ensure_user_data()
        base = p.signatures_dir() / "malicious_hashes.txt"
        base.write_text("aaa\nhash-ajoute-par-l-utilisateur\n", encoding="utf-8")

        p.ensure_user_data()   # second lancement

        assert "hash-ajoute-par-l-utilisateur" in base.read_text(encoding="utf-8")

    def test_la_cle_virustotal_n_est_jamais_recopiee(self, gele):
        """Un secret personnel n'a rien à faire dans un paquet distribué :
        même si une clé traîne dans le bundle, elle ne doit pas être déployée."""
        p, bundle, _ = gele
        (bundle / "signatures" / "vt_api_key.txt").write_text("SECRET\n", encoding="utf-8")

        p.ensure_user_data()

        assert not (p.signatures_dir() / "vt_api_key.txt").exists()

    def test_variable_d_environnement_prioritaire(self, tmp_path, monkeypatch):
        """Permet une installation portable (clé USB) ou un test isolé."""
        ailleurs = tmp_path / "portable"
        monkeypatch.setenv("ANTIZEEVIRIUS_DATA_DIR", str(ailleurs))
        p = importlib.reload(paths_module)
        assert p.data_path("logs") == ailleurs / "logs"
