"""
test_file_triage.py
Couvre optimizer/file_triage.py — le module qui décide quels fichiers de
l'utilisateur peuvent être proposés à la suppression.

C'est le module le plus dangereux de l'outil : une erreur de
classification ici se traduit directement par un document, une photo ou
un projet de code proposé à la suppression. Les tests ci-dessous
vérifient donc en priorité les GARDE-FOUS annoncés par le module :
NEVER_TOUCH_EXTENSIONS, NEVER_TOUCH_FOLDER_KEYWORDS, l'étanchéité des
3 niveaux de classement, la réversibilité du staging et le respect
strict du délai de rétention avant purge.

ATTENTION MÉTHODOLOGIQUE : `_is_never_touch()` teste les mots-clés
contre le CHEMIN COMPLET. Les noms des tests de ce fichier évitent donc
volontairement les mots "documents", "desktop", "pictures", "windows"...
qui se retrouveraient dans le chemin de `tmp_path` et fausseraient les
assertions (tout deviendrait `never_touch`).

Aucun test ne touche au système réel : tout se passe sous `tmp_path`.
"""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

file_triage = pytest.importorskip(
    "optimizer.file_triage", reason="file_triage indisponible sur cette plateforme"
)
FileTriage = file_triage.FileTriage
NEVER_TOUCH_EXTENSIONS = file_triage.NEVER_TOUCH_EXTENSIONS
NEVER_TOUCH_FOLDER_KEYWORDS = file_triage.NEVER_TOUCH_FOLDER_KEYWORDS
SAFE_EXTENSIONS = file_triage.SAFE_EXTENSIONS
INSTALLER_EXTENSIONS = file_triage.INSTALLER_EXTENSIONS
CAUTION_AGE_DAYS = file_triage.CAUTION_AGE_DAYS

DAY = 86400


@pytest.fixture
def triage(tmp_path):
    return FileTriage(str(tmp_path / "staging"))


def make_file(path: Path, size_bytes: int = 16, age_days: int = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"z" * size_bytes)
    if age_days:
        old = time.time() - age_days * DAY
        import os

        os.utime(path, (old, old))
    return path


class TestNeverTouchExtensions:
    """Le catalogue NEVER_TOUCH doit être respecté sans exception."""

    @pytest.mark.parametrize("ext", sorted(NEVER_TOUCH_EXTENSIONS))
    def test_every_declared_extension_is_never_touch(self, triage, tmp_path, ext):
        f = make_file(tmp_path / "atelier" / f"fichier{ext}")
        assert triage.classify_file(f)["category"] == "never_touch"

    def test_a_word_file_is_protected_even_when_huge_and_ancient(self, triage, tmp_path):
        """Un .docx de 500 Mo jamais ouvert depuis 3 ans reste intouchable :
        la règle 'gros fichier ancien' ne doit jamais l'emporter sur
        NEVER_TOUCH."""
        f = make_file(tmp_path / "atelier" / "these.docx", size_bytes=200, age_days=1200)
        with patch.object(Path, "stat", autospec=True) as fake_stat:
            fake_stat.return_value = type(
                "S", (), {"st_size": 500 * 1024 * 1024, "st_mtime": time.time() - 1200 * DAY}
            )()
            result = triage.classify_file(f)
        assert result["category"] == "never_touch"

    def test_a_photo_is_protected(self, triage, tmp_path):
        f = make_file(tmp_path / "atelier" / "vacances.jpg", age_days=2000)
        assert triage.classify_file(f)["category"] == "never_touch"

    def test_extension_matching_is_case_insensitive(self, triage, tmp_path):
        """Windows ne distingue pas la casse : `RAPPORT.DOCX` doit être
        aussi protégé que `rapport.docx`."""
        for name in ("RAPPORT.DOCX", "Photo.JPG", "Script.PY"):
            f = make_file(tmp_path / "atelier" / name)
            assert triage.classify_file(f)["category"] == "never_touch", name

    def test_source_code_is_protected_even_with_a_safe_looking_neighbour(self, triage, tmp_path):
        code = make_file(tmp_path / "projet" / "main.py")
        log = make_file(tmp_path / "projet" / "build.log")
        assert triage.classify_file(code)["category"] == "never_touch"
        assert triage.classify_file(log)["category"] == "safe"


class TestNeverTouchFolderKeywords:
    @pytest.mark.parametrize("keyword", NEVER_TOUCH_FOLDER_KEYWORDS)
    def test_every_declared_folder_keyword_shields_its_content(self, triage, tmp_path, keyword):
        """Un `.tmp` (normalement 'safe') placé dans un dossier protégé
        doit basculer en never_touch — le mot-clé dossier prime."""
        f = make_file(tmp_path / "racine" / keyword / "residu.tmp")
        assert triage.classify_file(f)["category"] == "never_touch"

    def test_folder_keyword_is_case_insensitive(self, triage, tmp_path):
        f = make_file(tmp_path / "racine" / "OneDrive" / "cache.tmp")
        assert triage.classify_file(f)["category"] == "never_touch"

    def test_nested_protected_folder_shields_deep_content(self, triage, tmp_path):
        f = make_file(tmp_path / "racine" / "Desktop" / "a" / "b" / "c" / "vieux.bak")
        assert triage.classify_file(f)["category"] == "never_touch"

    def test_a_neutral_folder_does_not_shield(self, triage, tmp_path):
        """Contrôle négatif : sans mot-clé protégé, un .tmp reste bien
        classé 'safe' — sinon le test précédent ne prouverait rien."""
        f = make_file(tmp_path / "racine" / "atelier" / "residu.tmp")
        assert triage.classify_file(f)["category"] == "safe"


class TestThreeLevelClassification:
    """Les 3 niveaux (safe / caution / never_touch) ne doivent jamais se mélanger."""

    @pytest.mark.parametrize("ext", sorted(SAFE_EXTENSIONS))
    def test_technical_residues_are_safe(self, triage, tmp_path, ext):
        f = make_file(tmp_path / "atelier" / f"residu{ext}")
        assert triage.classify_file(f)["category"] == "safe"

    @pytest.mark.parametrize("ext", sorted(INSTALLER_EXTENSIONS))
    def test_an_old_installer_is_caution_never_safe(self, triage, tmp_path, ext):
        """Un installeur ancien est un candidat 'à vérifier' — jamais
        recommandé automatiquement."""
        f = make_file(tmp_path / "atelier" / f"setup{ext}", age_days=CAUTION_AGE_DAYS + 10)
        result = triage.classify_file(f)
        assert result["category"] == "caution"

    @pytest.mark.parametrize("ext", sorted(INSTALLER_EXTENSIONS))
    def test_a_recent_installer_is_left_alone(self, triage, tmp_path, ext):
        f = make_file(tmp_path / "atelier" / f"setup{ext}", age_days=CAUTION_AGE_DAYS - 10)
        assert triage.classify_file(f)["category"] == "neutral"

    def test_installer_age_boundary_is_strict(self, triage, tmp_path):
        """Exactement CAUTION_AGE_DAYS jours : la règle est `> seuil`,
        donc le fichier ne doit PAS basculer en caution."""
        f = make_file(tmp_path / "atelier" / "setup.exe", age_days=CAUTION_AGE_DAYS)
        assert triage.classify_file(f)["category"] == "neutral"
        f2 = make_file(tmp_path / "atelier" / "setup2.exe", age_days=CAUTION_AGE_DAYS + 1)
        assert triage.classify_file(f2)["category"] == "caution"

    def test_a_big_old_archive_is_caution(self, triage, tmp_path):
        f = make_file(tmp_path / "atelier" / "archive.zip")
        fake = type("S", (), {"st_size": 150 * 1024 * 1024, "st_mtime": time.time() - 400 * DAY})()
        with patch.object(Path, "stat", autospec=True, return_value=fake):
            result = triage.classify_file(f)
        assert result["category"] == "caution"
        assert result["size_mb"] == 150.0

    def test_a_big_but_recent_archive_is_untouched(self, triage, tmp_path):
        f = make_file(tmp_path / "atelier" / "archive.zip")
        fake = type("S", (), {"st_size": 150 * 1024 * 1024, "st_mtime": time.time()})()
        with patch.object(Path, "stat", autospec=True, return_value=fake):
            assert triage.classify_file(f)["category"] == "neutral"

    def test_unreadable_file_is_neutral_not_deletable(self, triage, tmp_path):
        """Un fichier illisible (permissions) ne doit JAMAIS tomber dans
        'safe' par défaut : l'inconnu se classe en neutre."""
        f = make_file(tmp_path / "atelier" / "verrouille.tmp")
        with patch.object(Path, "stat", autospec=True, side_effect=PermissionError("refusé")):
            result = triage.classify_file(f)
        assert result["category"] == "neutral"

    def test_no_file_can_belong_to_two_categories(self, triage, tmp_path):
        """Balayage complet d'un dossier mixte : chaque fichier apparaît
        dans exactement une catégorie, et les compteurs sont cohérents."""
        make_file(tmp_path / "melange" / "rapport.docx")
        make_file(tmp_path / "melange" / "photo.png")
        make_file(tmp_path / "melange" / "trace.log")
        make_file(tmp_path / "melange" / "sauvegarde.bak")
        make_file(tmp_path / "melange" / "setup.msi", age_days=400)
        make_file(tmp_path / "melange" / "notes.txt")

        result = triage.triage_directory(str(tmp_path / "melange"))

        safe_paths = {Path(i["path"]).name for i in result["safe"]}
        caution_paths = {Path(i["path"]).name for i in result["caution"]}
        assert safe_paths == {"trace.log", "sauvegarde.bak"}
        assert caution_paths == {"setup.msi"}
        assert safe_paths.isdisjoint(caution_paths)
        assert result["never_touch_count"] == 2  # rapport.docx + photo.png
        assert result["neutral_count"] == 1      # notes.txt
        total = len(result["safe"]) + len(result["caution"]) + result["never_touch_count"] + result["neutral_count"]
        assert total == 6

    def test_protected_files_never_appear_in_any_candidate_list(self, triage, tmp_path):
        for name in ("cv.pdf", "photo.jpeg", "musique.mp3", "lib.dll", "app.ini", "code.js"):
            make_file(tmp_path / "melange" / name)

        result = triage.triage_directory(str(tmp_path / "melange"))

        assert result["safe"] == []
        assert result["caution"] == []
        assert result["never_touch_count"] == 6

    def test_missing_directory_returns_empty_candidates(self, triage, tmp_path):
        result = triage.triage_directory(str(tmp_path / "inexistant"))
        assert result["safe"] == [] and result["caution"] == []

    def test_directories_themselves_are_never_listed_as_candidates(self, triage, tmp_path):
        (tmp_path / "melange" / "sous.tmp").mkdir(parents=True)
        make_file(tmp_path / "melange" / "reel.tmp")
        result = triage.triage_directory(str(tmp_path / "melange"))
        assert [Path(i["path"]).name for i in result["safe"]] == ["reel.tmp"]


class TestDuplicateIntegration:
    def test_duplicates_of_neutral_files_are_proposed_with_first_copy_kept(self, triage, tmp_path):
        a = make_file(tmp_path / "melange" / "archive1.zip")
        b = make_file(tmp_path / "melange" / "archive2.zip")
        c = make_file(tmp_path / "melange" / "archive3.zip")

        result = triage.triage_directory(
            str(tmp_path / "melange"),
            include_duplicates=[{"paths": [str(a), str(b), str(c)], "size_mb": 1.0}],
        )

        proposed = {i["path"] for i in result["caution"]}
        assert str(a) not in proposed, "la première copie doit toujours être conservée"
        assert proposed == {str(b), str(c)}

    def test_duplicate_photos_are_reported_as_never_touch_in_the_counters(self, triage, tmp_path):
        """Les deux photos sont comptées never_touch par le balayage, et la copie
        soumise via include_duplicates est elle aussi écartée puis comptée — d'où 3
        et non 2. C'est la trace observable du filtre appliqué aux doublons."""
        a = make_file(tmp_path / "melange" / "photo1.jpg")
        b = make_file(tmp_path / "melange" / "photo2.jpg")

        result = triage.triage_directory(
            str(tmp_path / "melange"),
            include_duplicates=[{"paths": [str(a), str(b)], "size_mb": 1.0}],
        )
        assert result["never_touch_count"] == 3

    def test_never_touch_duplicates_must_not_be_proposed_for_deletion(self, triage, tmp_path):
        a = make_file(tmp_path / "melange" / "photo1.jpg")
        b = make_file(tmp_path / "melange" / "photo2.jpg")
        doc_a = make_file(tmp_path / "melange" / "rapport1.docx")
        doc_b = make_file(tmp_path / "melange" / "rapport2.docx")

        result = triage.triage_directory(
            str(tmp_path / "melange"),
            include_duplicates=[
                {"paths": [str(a), str(b)], "size_mb": 1.0},
                {"paths": [str(doc_a), str(doc_b)], "size_mb": 1.0},
            ],
        )

        assert result["caution"] == [], (
            "aucun fichier NEVER_TOUCH ne doit apparaître dans les candidats"
        )

    def test_ordinary_duplicates_are_still_proposed(self, triage, tmp_path):
        """Contrepartie du filtre : il ne doit pas être trop large. Un doublon
        d'extension banale reste proposé — seuls les fichiers NEVER_TOUCH sont
        écartés, pas la fonctionnalité de déduplication elle-même."""
        a = make_file(tmp_path / "melange" / "archive1.zip")
        b = make_file(tmp_path / "melange" / "archive2.zip")

        result = triage.triage_directory(
            str(tmp_path / "melange"),
            include_duplicates=[{"paths": [str(a), str(b)], "size_mb": 1.0}],
        )
        assert [i["path"] for i in result["caution"]] == [str(b)]


class TestStagingReversibility:
    def test_staging_moves_the_file_and_keeps_it_recoverable(self, triage, tmp_path):
        f = make_file(tmp_path / "atelier" / "residu.tmp", size_bytes=32)
        content = f.read_bytes()

        staging_id = triage.move_to_staging(str(f), "fichier technique")

        assert staging_id
        assert not f.exists()
        staged = list(Path(triage.staging_dir).glob("*_residu.tmp"))
        assert len(staged) == 1
        assert staged[0].read_bytes() == content

    def test_restore_puts_the_file_back_identically(self, triage, tmp_path):
        f = make_file(tmp_path / "atelier" / "residu.tmp", size_bytes=64)
        content = f.read_bytes()
        original = str(f)

        staging_id = triage.move_to_staging(original, "test")
        assert triage.restore_from_staging(staging_id) is True

        assert Path(original).exists()
        assert Path(original).read_bytes() == content
        assert triage.list_staging() == []

    def test_restore_recreates_a_deleted_parent_folder(self, triage, tmp_path):
        f = make_file(tmp_path / "atelier" / "sous" / "residu.tmp")
        parent = f.parent
        staging_id = triage.move_to_staging(str(f), "test")
        parent.rmdir()

        assert triage.restore_from_staging(staging_id) is True
        assert f.exists()

    def test_staging_two_files_with_the_same_name_does_not_collide(self, triage, tmp_path):
        a = make_file(tmp_path / "a" / "cache.tmp", size_bytes=10)
        b = make_file(tmp_path / "b" / "cache.tmp", size_bytes=20)
        id_a = triage.move_to_staging(str(a), "test")
        id_b = triage.move_to_staging(str(b), "test")

        assert id_a != id_b
        assert triage.restore_from_staging(id_a) is True
        assert triage.restore_from_staging(id_b) is True
        assert a.read_bytes() == b"z" * 10
        assert b.read_bytes() == b"z" * 20

    def test_staging_a_directory_is_reversible(self, triage, tmp_path):
        """residue_cleaner met des DOSSIERS entiers en staging via cette
        même API — l'aller-retour doit fonctionner pour un dossier."""
        folder = tmp_path / "atelier" / "AppOrpheline"
        make_file(folder / "sous" / "data.bin", size_bytes=8)

        staging_id = triage.move_to_staging(str(folder), "dossier orphelin")
        assert staging_id
        assert not folder.exists()

        assert triage.restore_from_staging(staging_id) is True
        assert (folder / "sous" / "data.bin").read_bytes() == b"z" * 8

    def test_staging_a_missing_file_returns_empty_id(self, triage, tmp_path):
        assert triage.move_to_staging(str(tmp_path / "absent.tmp"), "test") == ""
        assert triage.list_staging() == []

    def test_move_failure_leaves_no_index_entry(self, triage, tmp_path):
        f = make_file(tmp_path / "atelier" / "residu.tmp")
        with patch.object(file_triage.shutil, "move", side_effect=PermissionError("verrouillé")):
            assert triage.move_to_staging(str(f), "test") == ""
        assert triage.list_staging() == []
        assert f.exists()

    def test_restore_unknown_id_returns_false(self, triage):
        assert triage.restore_from_staging("id-inconnu") is False

    def test_restore_failure_keeps_the_entry_recoverable(self, triage, tmp_path):
        f = make_file(tmp_path / "atelier" / "residu.tmp")
        staging_id = triage.move_to_staging(str(f), "test")

        with patch.object(file_triage.shutil, "move", side_effect=OSError("disque plein")):
            assert triage.restore_from_staging(staging_id) is False

        assert len(triage.list_staging()) == 1
        assert triage.restore_from_staging(staging_id) is True

    def test_restore_places_the_file_alongside_without_overwriting(self, triage, tmp_path):
        """Si l'utilisateur a recréé un fichier au chemin d'origine pendant que
        l'ancien était en staging, la restauration ne l'écrase pas : elle dépose
        la copie restaurée à côté, sous un nom libre (convention ' (2)')."""
        f = make_file(tmp_path / "atelier" / "residu.tmp", size_bytes=8)
        staging_id = triage.move_to_staging(str(f), "test")
        f.write_bytes(b"NOUVEAU-CONTENU-UTILISATEUR")

        assert triage.restore_from_staging(staging_id) is True
        assert f.read_bytes() == b"NOUVEAU-CONTENU-UTILISATEUR"
        voisin = f.parent / "residu (2).tmp"
        assert voisin.exists() and voisin.read_bytes() == b"z" * 8

    def test_restore_must_not_overwrite_a_recreated_file(self, triage, tmp_path):
        f = make_file(tmp_path / "atelier" / "residu.tmp", size_bytes=8)
        staging_id = triage.move_to_staging(str(f), "test")
        f.write_bytes(b"NOUVEAU-CONTENU-UTILISATEUR")

        triage.restore_from_staging(staging_id)
        assert f.read_bytes() == b"NOUVEAU-CONTENU-UTILISATEUR"


class TestPurgeRetention:
    """La purge est la SEULE opération réellement destructive du module."""

    def _stage_with_age(self, triage, tmp_path, name, age_days):
        f = make_file(tmp_path / "atelier" / name)
        staging_id = triage.move_to_staging(str(f), "test")
        index = triage._load_index()
        for entry in index:
            if entry["id"] == staging_id:
                entry["date"] = (datetime.now() - timedelta(days=age_days)).isoformat()
        triage._save_index(index)
        return staging_id

    def test_a_29_day_old_item_survives_a_30_day_purge(self, triage, tmp_path):
        recent = self._stage_with_age(triage, tmp_path, "recent.tmp", 29)

        assert triage.purge_staging(older_than_days=30) == 0
        assert [e["id"] for e in triage.list_staging()] == [recent]
        assert triage.restore_from_staging(recent) is True

    def test_the_boundary_day_itself_survives(self, triage, tmp_path):
        """Exactement 30 jours avec `older_than_days=30` : la règle est
        `age > seuil`, donc l'élément doit rester."""
        exact = self._stage_with_age(triage, tmp_path, "limite.tmp", 30)
        assert triage.purge_staging(older_than_days=30) == 0
        assert [e["id"] for e in triage.list_staging()] == [exact]

    def test_a_31_day_old_item_is_purged(self, triage, tmp_path):
        old = self._stage_with_age(triage, tmp_path, "vieux.tmp", 31)
        assert triage.purge_staging(older_than_days=30) == 1
        assert triage.list_staging() == []
        assert not list(Path(triage.staging_dir).glob(f"{old}_*"))

    def test_purge_only_removes_the_expired_ones(self, triage, tmp_path):
        keep_a = self._stage_with_age(triage, tmp_path, "a.tmp", 1)
        keep_b = self._stage_with_age(triage, tmp_path, "b.tmp", 29)
        self._stage_with_age(triage, tmp_path, "c.tmp", 45)
        self._stage_with_age(triage, tmp_path, "d.tmp", 400)

        assert triage.purge_staging(older_than_days=30) == 2
        assert {e["id"] for e in triage.list_staging()} == {keep_a, keep_b}
        assert len(list(Path(triage.staging_dir).glob("*.tmp"))) == 2

    def test_purge_of_an_empty_staging_is_a_noop(self, triage):
        assert triage.purge_staging(older_than_days=30) == 0

    def test_purge_deletes_staged_directories_recursively(self, triage, tmp_path):
        folder = tmp_path / "atelier" / "AppOrpheline"
        make_file(folder / "sous" / "data.bin")
        staging_id = triage.move_to_staging(str(folder), "test")
        index = triage._load_index()
        index[0]["date"] = (datetime.now() - timedelta(days=90)).isoformat()
        triage._save_index(index)

        assert triage.purge_staging(older_than_days=30) == 1
        assert not list(Path(triage.staging_dir).glob(f"{staging_id}_*"))

    def test_purge_never_touches_the_index_file_itself(self, triage, tmp_path):
        self._stage_with_age(triage, tmp_path, "vieux.tmp", 400)
        triage.purge_staging(older_than_days=30)
        assert Path(triage.staging_index_path).exists()
        assert json.loads(Path(triage.staging_index_path).read_text(encoding="utf-8")) == []

    def test_purge_with_a_longer_retention_keeps_everything(self, triage, tmp_path):
        self._stage_with_age(triage, tmp_path, "a.tmp", 100)
        self._stage_with_age(triage, tmp_path, "b.tmp", 200)
        assert triage.purge_staging(older_than_days=365) == 0
        assert len(triage.list_staging()) == 2


class TestIndexRobustness:
    def test_index_is_created_lazily_and_reread_after_restart(self, tmp_path):
        staging_dir = tmp_path / "staging"
        f = make_file(tmp_path / "atelier" / "residu.tmp")

        first = FileTriage(str(staging_dir))
        assert first.list_staging() == []
        staging_id = first.move_to_staging(str(f), "test")

        second = FileTriage(str(staging_dir))
        assert [e["id"] for e in second.list_staging()] == [staging_id]
        assert second.restore_from_staging(staging_id) is True
        assert f.exists()

    def test_atomic_write_leaves_no_temp_file(self, triage, tmp_path):
        f = make_file(tmp_path / "atelier" / "residu.tmp")
        triage.move_to_staging(str(f), "test")
        assert not list(Path(triage.staging_dir).glob("*.json.tmp"))
