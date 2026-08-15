"""
test_quarantine_manager.py
Couvre quarantine/quarantine_manager.py — le module qui DÉPLACE des
fichiers de l'utilisateur hors de leur emplacement d'origine.

Angle d'attaque : la promesse centrale du module est la RÉVERSIBILITÉ.
Un fichier mis en quarantaine puis restauré doit revenir à l'identique,
même contenu, même emplacement. Tout écart est une perte de données.

NOTE PLATEFORME : `pytest.importorskip` suit la convention du projet
(voir tests/test_main_scan_directory.py) — le module est pur stdlib,
donc importable partout, mais on reste cohérent si une dépendance
Windows était ajoutée plus tard.

Aucun test ne touche au système réel : tout se passe sous `tmp_path`.
"""

import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

quarantine_manager = pytest.importorskip(
    "quarantine.quarantine_manager",
    reason="quarantine_manager indisponible sur cette plateforme",
)
QuarantineManager = quarantine_manager.QuarantineManager


@pytest.fixture
def manager(tmp_path):
    return QuarantineManager(str(tmp_path / "quarantine"))


@pytest.fixture
def infected_file(tmp_path):
    """Un fichier "malveillant" avec un contenu binaire précis, dans un
    sous-dossier — pour vérifier que l'emplacement exact est restauré."""
    folder = tmp_path / "user_data" / "sous_dossier"
    folder.mkdir(parents=True)
    target = folder / "facture_virus.exe"
    target.write_bytes(b"\x4d\x5a\x90\x00CONTENU-BINAIRE-EXACT\x00\xff")
    return target


class TestQuarantineRoundTrip:
    """Aller-retour quarantaine → restauration : le cœur de la sûreté."""

    def test_quarantined_file_leaves_original_location(self, manager, infected_file):
        qid = manager.quarantine_file(str(infected_file), "test", {"engine": "unit"})

        assert qid is not None
        assert not infected_file.exists(), "le fichier doit avoir quitté son emplacement"

    def test_quarantined_file_is_renamed_to_neutralize_execution(self, manager, infected_file):
        """Garde-fou documenté : l'extension est neutralisée pour empêcher
        toute exécution accidentelle par double-clic depuis la quarantaine."""
        qid = manager.quarantine_file(str(infected_file), "test", {})

        stored = list(Path(manager.quarantine_dir).glob("*.quarantined"))
        assert len(stored) == 1
        assert stored[0].name == f"{qid}.quarantined"
        assert not list(Path(manager.quarantine_dir).glob("*.exe"))

    def test_restore_returns_identical_content_at_identical_path(self, manager, infected_file):
        original_bytes = infected_file.read_bytes()
        original_path = str(infected_file)

        qid = manager.quarantine_file(original_path, "faux positif", {})
        assert manager.restore_file(qid) is True

        restored = Path(original_path)
        assert restored.exists(), "le fichier doit revenir à son emplacement exact"
        assert restored.read_bytes() == original_bytes, "le contenu doit être bit-à-bit identique"

    def test_restore_recreates_missing_parent_directories(self, manager, infected_file):
        """Cas réel : l'utilisateur supprime le dossier parent pendant que
        le fichier est en quarantaine. La restauration doit le recréer
        plutôt que d'échouer en perdant le fichier."""
        parent = infected_file.parent
        qid = manager.quarantine_file(str(infected_file), "test", {})
        parent.rmdir()
        assert not parent.exists()

        assert manager.restore_file(qid) is True
        assert infected_file.exists()

    def test_restored_file_disappears_from_active_list(self, manager, infected_file):
        qid = manager.quarantine_file(str(infected_file), "test", {})
        assert len(manager.list_quarantined()) == 1

        manager.restore_file(qid)
        assert manager.list_quarantined() == []

    def test_restore_twice_is_refused(self, manager, infected_file):
        """Une deuxième restauration ne doit rien faire (l'entrée est
        marquée restored) — sinon on écraserait le fichier déjà remis."""
        qid = manager.quarantine_file(str(infected_file), "test", {})
        assert manager.restore_file(qid) is True
        assert manager.restore_file(qid) is False

    def test_restore_unknown_id_returns_false(self, manager):
        assert manager.restore_file("id-qui-nexiste-pas") is False


class TestRestoreCollision:
    """Collision de nom à la restauration — le scénario de perte de données."""

    def test_restore_when_a_new_file_occupies_the_original_path(self, manager, infected_file):
        """Séquence : un fichier est mis en quarantaine ; l'utilisateur recrée
        ensuite un fichier LÉGITIME au même chemin ; la restauration ne doit pas
        le détruire. Le fichier restauré est déposé à côté, sous un nom libre,
        et l'entrée d'index note le chemin réellement utilisé.
        """
        original_path = infected_file
        qid = manager.quarantine_file(str(original_path), "test", {})

        original_path.write_bytes(b"NOUVEAU-FICHIER-LEGITIME-DE-L-UTILISATEUR")

        result = manager.restore_file(qid)

        assert result is True
        assert original_path.read_bytes() == b"NOUVEAU-FICHIER-LEGITIME-DE-L-UTILISATEUR"
        voisin = original_path.parent / f"{original_path.stem} (2){original_path.suffix}"
        assert voisin.exists(), "le fichier restauré doit être déposé à côté"
        # list_quarantined() ne renvoie que les entrées NON restaurées : on relit
        # l'index brut pour vérifier que le chemin réellement utilisé y est tracé.
        index = json.loads(Path(manager.metadata_file).read_text(encoding="utf-8"))
        entry = next(e for e in index if e["id"] == qid)
        assert entry["restored"] is True
        assert entry.get("restored_to") == str(voisin)

    def test_restore_must_not_destroy_an_existing_file(self, manager, infected_file):
        """Le garde-fou attendu : aucune restauration ne doit détruire un
        fichier présent à l'emplacement cible."""
        qid = manager.quarantine_file(str(infected_file), "test", {})
        infected_file.write_bytes(b"NOUVEAU-FICHIER-LEGITIME-DE-L-UTILISATEUR")

        manager.restore_file(qid)

        assert infected_file.read_bytes() == b"NOUVEAU-FICHIER-LEGITIME-DE-L-UTILISATEUR"


class TestQuarantineRefusalAndFailures:
    def test_nonexistent_source_returns_none_and_writes_nothing(self, manager, tmp_path):
        assert manager.quarantine_file(str(tmp_path / "absent.exe"), "test", {}) is None
        assert manager.list_quarantined() == []

    def test_move_failure_does_not_create_a_phantom_index_entry(self, manager, infected_file):
        """Si le déplacement échoue (fichier verrouillé), aucune entrée ne
        doit apparaître dans l'index : sinon l'outil croirait détenir un
        fichier qui est en réalité toujours en place."""
        with patch.object(quarantine_manager.shutil, "move", side_effect=PermissionError("verrouillé")):
            qid = manager.quarantine_file(str(infected_file), "test", {})

        assert qid is None
        assert manager.list_quarantined() == []
        assert infected_file.exists(), "le fichier d'origine doit rester intact"

    def test_restore_failure_keeps_entry_restorable(self, manager, infected_file):
        """Un échec de restauration ne doit pas marquer l'entrée comme
        restaurée : le fichier doit rester récupérable plus tard."""
        qid = manager.quarantine_file(str(infected_file), "test", {})

        with patch.object(quarantine_manager.shutil, "move", side_effect=OSError("disque plein")):
            assert manager.restore_file(qid) is False

        assert len(manager.list_quarantined()) == 1
        assert manager.restore_file(qid) is True


class TestPermanentDeletion:
    def test_delete_permanently_removes_file_and_index_entry(self, manager, infected_file):
        qid = manager.quarantine_file(str(infected_file), "test", {})
        stored = Path(manager.quarantine_dir) / f"{qid}.quarantined"
        assert stored.exists()

        assert manager.delete_permanently(qid) is True
        assert not stored.exists()
        assert manager.list_quarantined() == []
        assert json.loads(Path(manager.metadata_file).read_text(encoding="utf-8")) == []

    def test_delete_permanently_unknown_id_is_a_noop(self, manager, infected_file):
        manager.quarantine_file(str(infected_file), "test", {})
        assert manager.delete_permanently("inconnu") is False
        assert len(manager.list_quarantined()) == 1

    def test_delete_does_not_touch_other_entries(self, manager, tmp_path):
        paths = []
        for i in range(3):
            f = tmp_path / f"menace_{i}.exe"
            f.write_bytes(f"contenu-{i}".encode())
            paths.append(manager.quarantine_file(str(f), "test", {}))

        manager.delete_permanently(paths[1])

        remaining = {e["id"] for e in manager.list_quarantined()}
        assert remaining == {paths[0], paths[2]}
        assert len(list(Path(manager.quarantine_dir).glob("*.quarantined"))) == 2


class TestIndexIntegrity:
    def test_index_survives_concurrent_quarantines(self, manager, tmp_path):
        """Le verrou doit empêcher la classique race condition
        read-modify-write sur l'index JSON : 40 fichiers mis en quarantaine
        depuis 8 threads → 40 entrées, aucune perdue."""
        files = []
        for i in range(40):
            f = tmp_path / f"concurrent_{i}.exe"
            f.write_bytes(b"x")
            files.append(f)

        errors = []

        def worker(chunk):
            try:
                for f in chunk:
                    manager.quarantine_file(str(f), "concurrent", {})
            except Exception as e:  # pragma: no cover - remonte un vrai souci
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(files[i::8],)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(manager.list_quarantined()) == 40
        assert len({e["id"] for e in manager.list_quarantined()}) == 40

    def test_index_write_is_atomic_no_tmp_leftover(self, manager, infected_file):
        manager.quarantine_file(str(infected_file), "test", {})
        assert not list(Path(manager.quarantine_dir).glob("*.tmp")), (
            "le fichier temporaire d'écriture atomique doit avoir été renommé"
        )

    def test_metadata_records_reason_and_details(self, manager, infected_file):
        details = {"signature": "EICAR", "score": 9}
        qid = manager.quarantine_file(str(infected_file), "Signature connue", details)

        entry = manager.list_quarantined()[0]
        assert entry["id"] == qid
        assert entry["reason"] == "Signature connue"
        assert entry["detection_details"] == details
        assert entry["restored"] is False
        assert Path(entry["original_path"]).name == "facture_virus.exe"

    def test_existing_index_is_not_wiped_on_reinstantiation(self, tmp_path):
        """Rouvrir le dossier de quarantaine (redémarrage de l'outil) ne
        doit jamais réinitialiser l'index : les fichiers déjà isolés
        resteraient orphelins et non restaurables."""
        qdir = tmp_path / "q"
        f = tmp_path / "menace.exe"
        f.write_bytes(b"x")

        first = QuarantineManager(str(qdir))
        qid = first.quarantine_file(str(f), "test", {})

        second = QuarantineManager(str(qdir))
        assert [e["id"] for e in second.list_quarantined()] == [qid]
        assert second.restore_file(qid) is True
        assert f.exists()
