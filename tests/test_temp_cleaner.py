"""
test_temp_cleaner.py
Couvre optimizer/temp_cleaner.py — le module qui SUPPRIME définitivement
(sans staging, sans corbeille) le contenu des dossiers de cache.

Deux garanties à prouver, parce qu'elles sont la seule chose qui sépare
ce module d'un `rm -rf` :
1. Il supprime le CONTENU d'un dossier de cache, jamais le dossier
   lui-même (Windows et les applications comptent sur son existence).
2. Un fichier verrouillé/inaccessible est ignoré et comptabilisé — il
   ne doit jamais interrompre le reste du nettoyage.

ISOLATION STRICTE : ce module lit %TEMP%, %WINDIR%, Path.home() et
appelle l'API Windows SHEmptyRecycleBinW. Tous ces points d'entrée sont
redirigés vers `tmp_path` ou mockés — aucun test ne touche au vrai
%TEMP%, à la vraie corbeille ni au vrai profil utilisateur.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

temp_cleaner = pytest.importorskip(
    "optimizer.temp_cleaner", reason="temp_cleaner indisponible sur cette plateforme"
)
TempCleaner = temp_cleaner.TempCleaner


@pytest.fixture
def cleaner():
    return TempCleaner()


@pytest.fixture
def fake_windll():
    """Remplace ctypes.windll (inexistant hors Windows) par un mock —
    garantit qu'aucun appel à l'API Windows réelle n'a lieu."""
    mock = MagicMock()
    mock.shell32.IsUserAnAdmin.return_value = 0
    mock.shell32.SHEmptyRecycleBinW.return_value = 0
    with patch.object(temp_cleaner.ctypes, "windll", mock, create=True):
        yield mock


@pytest.fixture
def sandbox_home(tmp_path):
    """Redirige Path.home() vers tmp_path : clean_thumbnail_cache() et
    clean_browser_caches() ne doivent JAMAIS voir le vrai profil."""
    home = tmp_path / "faux_profil"
    home.mkdir()
    with patch.object(temp_cleaner.Path, "home", staticmethod(lambda: home)):
        yield home


def build_cache(root: Path) -> Path:
    """Un dossier de cache réaliste : fichiers + sous-dossiers imbriqués."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "session.tmp").write_bytes(b"a" * 1024)
    (root / "trace.log").write_bytes(b"b" * 2048)
    sub = root / "sous_cache"
    sub.mkdir()
    (sub / "blob.dat").write_bytes(b"c" * 4096)
    (sub / "encore" ).mkdir()
    (sub / "encore" / "profond.bin").write_bytes(b"d" * 512)
    return root


class TestCleanDirectoryKeepsTheFolder:
    def test_the_cache_folder_itself_is_never_deleted(self, cleaner, tmp_path):
        cache = build_cache(tmp_path / "Cache")

        cleaner._clean_directory(str(cache), "Cache test")

        assert cache.exists() and cache.is_dir(), (
            "le dossier de cache doit survivre — Windows/les applis comptent dessus"
        )

    def test_all_content_is_removed(self, cleaner, tmp_path):
        cache = build_cache(tmp_path / "Cache")

        result = cleaner._clean_directory(str(cache), "Cache test")

        assert list(cache.iterdir()) == []
        assert result["status"] == "ok"
        assert result["deleted_items"] == 3  # 2 fichiers + 1 sous-dossier de 1er niveau
        assert result["skipped_items"] == 0

    def test_nothing_outside_the_target_folder_is_touched(self, cleaner, tmp_path):
        cache = build_cache(tmp_path / "Cache")
        voisin = tmp_path / "Voisin"
        voisin.mkdir()
        (voisin / "important.docx").write_bytes(b"NE PAS TOUCHER")
        parent_file = tmp_path / "aussi_important.docx"
        parent_file.write_bytes(b"NE PAS TOUCHER NON PLUS")

        cleaner._clean_directory(str(cache), "Cache test")

        assert (voisin / "important.docx").read_bytes() == b"NE PAS TOUCHER"
        assert parent_file.read_bytes() == b"NE PAS TOUCHER NON PLUS"

    def test_freed_size_is_reported_in_megabytes(self, cleaner, tmp_path):
        cache = tmp_path / "Cache"
        cache.mkdir()
        (cache / "gros.bin").write_bytes(b"x" * (3 * 1024 * 1024))

        result = cleaner._clean_directory(str(cache), "Cache test")

        assert result["freed_mb"] == pytest.approx(3.0, abs=0.01)

    def test_absent_folder_is_reported_not_created(self, cleaner, tmp_path):
        absent = tmp_path / "jamais_installe"

        result = cleaner._clean_directory(str(absent), "Cache absent")

        assert result["status"] == "absent"
        assert result["freed_mb"] == 0
        assert not absent.exists(), "le nettoyage ne doit pas créer le dossier"

    def test_empty_folder_is_handled_cleanly(self, cleaner, tmp_path):
        cache = tmp_path / "Cache"
        cache.mkdir()

        result = cleaner._clean_directory(str(cache), "Cache vide")

        assert result["status"] == "ok"
        assert result["deleted_items"] == 0
        assert result["freed_mb"] == 0
        assert cache.exists()

    def test_a_symlink_is_unlinked_not_followed(self, cleaner, tmp_path):
        """Un lien symbolique dans le cache doit être supprimé en tant que
        lien — jamais suivi pour effacer sa cible (qui peut être un vrai
        dossier de l'utilisateur)."""
        precieux = tmp_path / "Precieux"
        precieux.mkdir()
        (precieux / "these.docx").write_bytes(b"TRAVAIL DE 3 ANS")

        cache = tmp_path / "Cache"
        cache.mkdir()
        link = cache / "raccourci"
        try:
            link.symlink_to(precieux, target_is_directory=True)
        except (OSError, NotImplementedError):  # pragma: no cover
            pytest.skip("liens symboliques non supportés sur cette plateforme/compte")

        cleaner._clean_directory(str(cache), "Cache test")

        assert (precieux / "these.docx").read_bytes() == b"TRAVAIL DE 3 ANS"
        assert not link.exists() and not link.is_symlink()


class TestLockedFilesAreSkippedNotFatal:
    def test_a_locked_file_is_counted_as_skipped(self, cleaner, tmp_path):
        cache = tmp_path / "Cache"
        cache.mkdir()
        (cache / "verrouille.tmp").write_bytes(b"x" * 100)
        (cache / "libre.tmp").write_bytes(b"y" * 100)

        real_unlink = Path.unlink

        def selective_unlink(self, *args, **kwargs):
            if self.name == "verrouille.tmp":
                raise PermissionError("Accès refusé — fichier en cours d'utilisation")
            return real_unlink(self, *args, **kwargs)

        with patch.object(Path, "unlink", selective_unlink):
            result = cleaner._clean_directory(str(cache), "Cache test")

        assert result["status"] == "ok", "un fichier verrouillé ne doit pas faire échouer le nettoyage"
        assert result["skipped_items"] == 1
        assert result["deleted_items"] == 1
        assert (cache / "verrouille.tmp").exists()
        assert not (cache / "libre.tmp").exists()

    def test_a_locked_subfolder_does_not_stop_the_rest(self, cleaner, tmp_path):
        cache = tmp_path / "Cache"
        cache.mkdir()
        bloque = cache / "bloque"
        bloque.mkdir()
        (bloque / "handle.dat").write_bytes(b"x")
        (cache / "libre.tmp").write_bytes(b"y")

        with patch.object(temp_cleaner.shutil, "rmtree", side_effect=PermissionError("verrouillé")):
            result = cleaner._clean_directory(str(cache), "Cache test")

        assert result["status"] == "ok"
        assert result["skipped_items"] == 1
        assert not (cache / "libre.tmp").exists()
        assert bloque.exists()

    def test_every_item_locked_still_returns_a_valid_report(self, cleaner, tmp_path):
        cache = tmp_path / "Cache"
        cache.mkdir()
        for i in range(4):
            (cache / f"f{i}.tmp").write_bytes(b"x")

        with patch.object(Path, "unlink", side_effect=PermissionError("tout est verrouillé")):
            result = cleaner._clean_directory(str(cache), "Cache test")

        assert result["status"] == "ok"
        assert result["skipped_items"] == 4
        assert result["deleted_items"] == 0
        assert result["freed_mb"] == 0
        assert len(list(cache.iterdir())) == 4

    def test_dir_size_ignores_unreadable_entries(self, cleaner, tmp_path):
        cache = tmp_path / "Cache"
        cache.mkdir()
        (cache / "a.bin").write_bytes(b"x" * 1024)

        real_stat = Path.stat

        def selective_stat(self, *args, **kwargs):
            if self.name == "a.bin":
                raise PermissionError("refusé")
            return real_stat(self, *args, **kwargs)

        with patch.object(Path, "stat", selective_stat):
            assert cleaner._dir_size(cache) == 0


class TestTargetResolutionStaysSandboxed:
    def test_user_temp_uses_the_TEMP_environment_variable(self, cleaner, tmp_path, monkeypatch):
        fake_temp = build_cache(tmp_path / "FauxTemp")
        monkeypatch.setenv("TEMP", str(fake_temp))

        result = cleaner.clean_user_temp()

        assert result["path"] == str(fake_temp)
        assert fake_temp.exists() and list(fake_temp.iterdir()) == []

    def test_user_temp_falls_back_to_TMP(self, cleaner, tmp_path, monkeypatch):
        fake_temp = build_cache(tmp_path / "FauxTmp")
        monkeypatch.delenv("TEMP", raising=False)
        monkeypatch.setenv("TMP", str(fake_temp))

        assert cleaner.clean_user_temp()["path"] == str(fake_temp)

    def test_windows_targets_are_derived_from_WINDIR(self, cleaner, tmp_path, monkeypatch):
        windir = tmp_path / "FauxWindir"
        build_cache(windir / "Temp")
        build_cache(windir / "Prefetch")
        build_cache(windir / "SoftwareDistribution" / "Download")
        monkeypatch.setenv("WINDIR", str(windir))

        assert cleaner.clean_windows_temp()["path"] == str(windir / "Temp")
        assert cleaner.clean_prefetch()["path"] == str(windir / "Prefetch")
        assert cleaner.clean_windows_update_cache()["path"] == str(
            windir / "SoftwareDistribution" / "Download"
        )
        # Les dossiers eux-mêmes survivent, leur contenu est vidé.
        for sub in ("Temp", "Prefetch"):
            assert (windir / sub).exists()
            assert list((windir / sub).iterdir()) == []

    def test_thumbnail_cache_only_removes_thumbcache_files(self, cleaner, sandbox_home):
        explorer = sandbox_home / "AppData/Local/Microsoft/Windows/Explorer"
        explorer.mkdir(parents=True)
        (explorer / "thumbcache_256.db").write_bytes(b"x" * 1024)
        (explorer / "thumbcache_1024.db").write_bytes(b"y" * 1024)
        (explorer / "iconcache_idx.db").write_bytes(b"NE PAS TOUCHER")
        (explorer / "notes_perso.txt").write_bytes(b"NE PAS TOUCHER")

        result = cleaner.clean_thumbnail_cache()

        assert result["deleted_items"] == 2
        assert (explorer / "iconcache_idx.db").exists()
        assert (explorer / "notes_perso.txt").read_bytes() == b"NE PAS TOUCHER"

    def test_thumbnail_cache_absent_folder_is_harmless(self, cleaner, sandbox_home):
        result = cleaner.clean_thumbnail_cache()
        assert result["status"] == "ok"
        assert result["deleted_items"] == 0

    def test_browser_caches_only_target_existing_cache_folders(self, cleaner, sandbox_home):
        chrome = sandbox_home / "AppData/Local/Google/Chrome/User Data/Default/Cache"
        build_cache(chrome)
        # Données sensibles voisines : elles ne sont PAS des cibles.
        profile = chrome.parent
        (profile / "Login Data").write_bytes(b"MOTS DE PASSE")
        (profile / "Bookmarks").write_bytes(b"FAVORIS")
        (profile / "History").write_bytes(b"HISTORIQUE")

        results = cleaner.clean_browser_caches()

        labels = [r["label"] for r in results]
        assert labels == ["Cache Chrome"], "seuls les caches existants sont traités"
        assert list(chrome.iterdir()) == []
        assert (profile / "Login Data").read_bytes() == b"MOTS DE PASSE"
        assert (profile / "Bookmarks").read_bytes() == b"FAVORIS"
        assert (profile / "History").read_bytes() == b"HISTORIQUE"

    def test_browser_caches_returns_empty_when_no_browser_installed(self, cleaner, sandbox_home):
        assert cleaner.clean_browser_caches() == []


class TestRecycleBinIsNeverTouchedForReal:
    def test_recycle_bin_uses_the_native_api_with_silent_flags(self, cleaner, fake_windll):
        result = cleaner.clean_recycle_bin()

        fake_windll.shell32.SHEmptyRecycleBinW.assert_called_once_with(None, None, 0x07)
        assert result["status"] == "ok"

    def test_already_empty_recycle_bin_is_not_an_error(self, cleaner, fake_windll):
        fake_windll.shell32.SHEmptyRecycleBinW.return_value = -2147418113
        assert cleaner.clean_recycle_bin()["status"] == "ok"

    def test_api_failure_is_reported_not_raised(self, cleaner, fake_windll):
        fake_windll.shell32.SHEmptyRecycleBinW.return_value = 5
        assert cleaner.clean_recycle_bin()["status"] == "erreur"

    def test_missing_windows_api_is_caught(self, cleaner):
        """Hors Windows (ou API indisponible) : erreur remontée dans le
        rapport, jamais d'exception qui casserait tout le nettoyage."""
        with patch.object(temp_cleaner.ctypes, "windll", None, create=True):
            result = cleaner.clean_recycle_bin()
        assert result["status"].startswith("erreur")
        assert result["freed_mb"] == 0


class TestFullCleanupOrchestration:
    def test_non_admin_run_skips_admin_targets(self, cleaner, tmp_path, monkeypatch, fake_windll, sandbox_home):
        windir = tmp_path / "FauxWindir"
        build_cache(windir / "Temp")
        monkeypatch.setenv("WINDIR", str(windir))
        monkeypatch.setenv("TEMP", str(build_cache(tmp_path / "FauxTemp")))
        fake_windll.shell32.IsUserAnAdmin.return_value = 0

        report = cleaner.run_full_cleanup(include_admin_targets=True)

        assert report["is_admin"] is False
        assert list((windir / "Temp").iterdir()), "Windows\\Temp ne doit PAS être vidé sans droits admin"
        assert any("droits administrateur requis" in str(r.get("status", "")) for r in report["results"])

    def test_admin_run_includes_admin_targets(self, cleaner, tmp_path, monkeypatch, fake_windll, sandbox_home):
        windir = tmp_path / "FauxWindir"
        build_cache(windir / "Temp")
        build_cache(windir / "Prefetch")
        build_cache(windir / "SoftwareDistribution" / "Download")
        monkeypatch.setenv("WINDIR", str(windir))
        monkeypatch.setenv("TEMP", str(build_cache(tmp_path / "FauxTemp")))
        fake_windll.shell32.IsUserAnAdmin.return_value = 1

        report = cleaner.run_full_cleanup(include_admin_targets=True)

        assert report["is_admin"] is True
        labels = {r["label"] for r in report["results"]}
        assert {"Windows Temp", "Prefetch", "Cache Windows Update"} <= labels
        assert list((windir / "Temp").iterdir()) == []

    def test_admin_targets_can_be_excluded_entirely(self, cleaner, tmp_path, monkeypatch, fake_windll, sandbox_home):
        windir = tmp_path / "FauxWindir"
        build_cache(windir / "Temp")
        monkeypatch.setenv("WINDIR", str(windir))
        monkeypatch.setenv("TEMP", str(build_cache(tmp_path / "FauxTemp")))
        fake_windll.shell32.IsUserAnAdmin.return_value = 1

        report = cleaner.run_full_cleanup(include_admin_targets=False)

        assert "Windows Temp" not in {r["label"] for r in report["results"]}
        assert list((windir / "Temp").iterdir()), "Windows\\Temp ne devait pas être ciblé"

    def test_total_freed_ignores_non_numeric_values(self, cleaner, tmp_path, monkeypatch, fake_windll, sandbox_home):
        """clean_recycle_bin() renvoie une chaîne pour freed_mb ("inconnu") :
        la somme doit l'ignorer sans planter."""
        temp_dir = tmp_path / "FauxTemp"
        temp_dir.mkdir()
        (temp_dir / "gros.bin").write_bytes(b"x" * (2 * 1024 * 1024))
        monkeypatch.setenv("TEMP", str(temp_dir))

        report = cleaner.run_full_cleanup(include_admin_targets=False)

        assert isinstance(report["total_freed_mb"], float)
        assert report["total_freed_mb"] == pytest.approx(2.0, abs=0.01)

    def test_is_admin_is_false_when_the_api_is_unavailable(self, cleaner):
        with patch.object(temp_cleaner.ctypes, "windll", None, create=True):
            assert cleaner._is_admin() is False
