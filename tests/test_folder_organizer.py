"""
test_folder_organizer.py
Couvre move_folder_into() dans optimizer/folder_organizer.py, en particulier
le cas de fusion (conflit de nom à la destination) réécrit pour utiliser un
seul os.walk(topdown=False) au lieu de 2 parcours rglob() + un tri O(N log N).
"""

import os
from pathlib import Path

import pytest

from optimizer.folder_organizer import FolderOrganizer


@pytest.fixture
def organizer(tmp_path):
    return FolderOrganizer(str(tmp_path / "reorg_index.json"))


class TestMoveFolderIntoSimpleCase:
    def test_no_conflict_moves_folder_directly(self, organizer, tmp_path):
        source = tmp_path / "source" / "Projet"
        target_parent = tmp_path / "target"
        (source / "sub").mkdir(parents=True)
        (source / "file.txt").write_text("contenu")
        target_parent.mkdir()

        result = organizer.move_folder_into(str(source), str(target_parent))

        assert result["status"] == "ok"
        assert result["merged"] is False
        assert not source.exists()
        assert (target_parent / "Projet" / "file.txt").exists()

    def test_missing_source_returns_error(self, organizer, tmp_path):
        target_parent = tmp_path / "target"
        target_parent.mkdir()
        result = organizer.move_folder_into(str(tmp_path / "does_not_exist"), str(target_parent))
        assert result["status"] == "erreur"

    def test_moving_into_own_subfolder_is_rejected(self, organizer, tmp_path):
        source = tmp_path / "Parent"
        (source / "Enfant").mkdir(parents=True)
        result = organizer.move_folder_into(str(source), str(source / "Enfant"))
        assert result["status"] == "erreur"
        assert source.exists()  # rien n'a dû bouger


class TestMoveFolderIntoMergeCase:
    """Cas de conflit : un dossier de même nom existe déjà à la destination
    -> fusion fichier par fichier via le os.walk(topdown=False) unifié."""

    def _build_conflicting_trees(self, tmp_path):
        target_parent = tmp_path / "target"
        source = tmp_path / "source" / "ClientX"
        existing = target_parent / "ClientX"

        (existing / "sub").mkdir(parents=True)
        (source / "sub").mkdir(parents=True)
        (source / "other").mkdir(parents=True)

        # Fichier en collision de nom (même chemin relatif des deux côtés)
        (existing / "sub" / "report.txt").write_text("existant")
        (source / "sub" / "report.txt").write_text("nouveau")
        # Fichiers uniques côté source
        (source / "other" / "notes.txt").write_text("notes")
        (source / "top.txt").write_text("top")

        return source, target_parent, existing

    def test_merge_moves_all_files_and_removes_source_entirely(self, organizer, tmp_path):
        source, target_parent, existing = self._build_conflicting_trees(tmp_path)

        result = organizer.move_folder_into(str(source), str(target_parent))

        assert result["status"] == "ok"
        assert result["merged"] is True
        assert result["moved"] == 3
        assert result["errors"] == []
        # La source (et tous ses sous-dossiers, y compris ceux devenus
        # vides après déplacement) doit avoir totalement disparu.
        assert not source.exists()

    def test_merge_preserves_existing_file_and_renames_the_colliding_copy(self, organizer, tmp_path):
        source, target_parent, existing = self._build_conflicting_trees(tmp_path)
        organizer.move_folder_into(str(source), str(target_parent))

        sub_files = sorted(os.listdir(existing / "sub"))
        # Le fichier existant doit être conservé tel quel, et la version
        # entrante renommée automatiquement (jamais d'écrasement silencieux).
        assert "report.txt" in sub_files
        assert len(sub_files) == 2
        assert (existing / "sub" / "report.txt").read_text() == "existant"

    def test_merge_moves_unique_files_to_correct_relative_paths(self, organizer, tmp_path):
        source, target_parent, existing = self._build_conflicting_trees(tmp_path)
        organizer.move_folder_into(str(source), str(target_parent))

        assert (existing / "other" / "notes.txt").read_text() == "notes"
        assert (existing / "top.txt").read_text() == "top"

    def test_merge_records_every_moved_file_in_the_undo_log(self, organizer, tmp_path):
        source, target_parent, existing = self._build_conflicting_trees(tmp_path)
        result = organizer.move_folder_into(str(source), str(target_parent))

        sessions = organizer.list_sessions()
        matching = [s for s in sessions if s["session_id"] == result["session_id"]]
        assert len(matching) == 1
        assert matching[0]["count"] == 3

    def test_no_empty_directories_left_behind_after_merge(self, organizer, tmp_path):
        """Vérifie explicitement l'effet visé par la réécriture en un seul
        os.walk(topdown=False) : plus aucun dossier vide résiduel côté
        source, sans nécessiter un second parcours de nettoyage séparé."""
        source, target_parent, existing = self._build_conflicting_trees(tmp_path)
        organizer.move_folder_into(str(source), str(target_parent))

        assert not (tmp_path / "source").exists() or not any((tmp_path / "source").rglob("*"))
