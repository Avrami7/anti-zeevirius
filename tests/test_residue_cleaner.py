"""
test_residue_cleaner.py
Couvre optimizer/residue_cleaner.py — le module qui identifie des DOSSIERS
entiers (Program Files, AppData, ProgramData) comme "orphelins" et les met
de côté. La plus grosse surface de destruction de l'outil : une erreur ici
ne coûte pas un fichier, mais un dossier applicatif complet.

Garde-fous à prouver :
- PROTECTED_FOLDER_NAMES : les composants partagés Windows ne sont jamais
  proposés, même sans application installée correspondante.
- ORPHAN_MIN_AGE_DAYS : un dossier modifié récemment n'est jamais candidat.
- La détection est en LECTURE SEULE : find_*() ne déplace/supprime rien.
- Les entrées de registre sont SAUVEGARDÉES AVANT suppression.

ISOLATION STRICTE : `winreg` et `win32com` sont entièrement mockés
(injectés dans le module), `_scan_root_folders`/`_shortcut_scan_folders`
sont redirigés vers `tmp_path`. Aucun test ne lit ni n'écrit le vrai
registre Windows, ni le vrai Bureau/Menu Démarrer.
"""

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

residue_cleaner = pytest.importorskip(
    "optimizer.residue_cleaner", reason="residue_cleaner indisponible sur cette plateforme"
)
file_triage_mod = pytest.importorskip("optimizer.file_triage")

ResidueCleaner = residue_cleaner.ResidueCleaner
FileTriage = file_triage_mod.FileTriage
PROTECTED_FOLDER_NAMES = residue_cleaner.PROTECTED_FOLDER_NAMES
ORPHAN_MIN_AGE_DAYS = residue_cleaner.ORPHAN_MIN_AGE_DAYS
REGISTRY_BACKUP_PATH = residue_cleaner.REGISTRY_BACKUP_PATH

DAY = 86400


class FakeAppManager:
    """Substitut d'AppManager : évite tout accès au registre / PowerShell."""

    def __init__(self, apps=None):
        self.apps = apps or []

    def list_all_sorted(self, sort_by="size"):
        return {"apps": self.apps, "total": len(self.apps), "known_bloatware_count": 0}


@pytest.fixture
def triage(tmp_path):
    return FileTriage(str(tmp_path / "staging"))


@pytest.fixture
def cleaner(triage):
    return ResidueCleaner(triage, FakeAppManager())


def make_app_folder(root: Path, name: str, age_days: int = 365, size_bytes: int = 1024) -> Path:
    """Un dossier applicatif avec un fichier daté — l'âge du dossier est
    dérivé du mtime le plus récent de ses fichiers."""
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / "app.dat"
    f.write_bytes(b"x" * size_bytes)
    stamp = time.time() - age_days * DAY
    os.utime(f, (stamp, stamp))
    os.utime(folder, (stamp, stamp))
    return folder


def with_roots(*roots):
    """Redirige _scan_root_folders() vers des dossiers de test."""
    return patch.object(
        ResidueCleaner, "_scan_root_folders", staticmethod(lambda: [Path(r) for r in roots])
    )


class TestProtectedFolderNames:
    @pytest.mark.parametrize("protected_name", sorted(PROTECTED_FOLDER_NAMES))
    def test_every_protected_name_is_excluded(self, cleaner, tmp_path, protected_name):
        """Chaque nom de la liste de protection doit être exclu, même
        ancien et sans application installée correspondante."""
        root = tmp_path / "FauxProgramFiles"
        make_app_folder(root, protected_name, age_days=900)

        with with_roots(root):
            candidates = cleaner.find_candidate_orphaned_folders()

        assert candidates == []

    def test_protection_is_case_insensitive(self, cleaner, tmp_path):
        root = tmp_path / "FauxProgramFiles"
        make_app_folder(root, "Common Files", age_days=900)
        make_app_folder(root, "WindowsApps", age_days=900)
        make_app_folder(root, "MICROSOFT", age_days=900)

        with with_roots(root):
            assert cleaner.find_candidate_orphaned_folders() == []

    def test_an_unprotected_orphan_is_detected(self, cleaner, tmp_path):
        """Contrôle négatif : sans ce test, les précédents ne prouveraient
        pas que la détection fonctionne du tout."""
        root = tmp_path / "FauxProgramFiles"
        make_app_folder(root, "VieuxLogicielDisparu", age_days=900)

        with with_roots(root):
            candidates = cleaner.find_candidate_orphaned_folders()

        assert [Path(c["path"]).name for c in candidates] == ["VieuxLogicielDisparu"]


class TestOrphanMinimumAge:
    def test_a_folder_modified_yesterday_is_never_a_candidate(self, cleaner, tmp_path):
        root = tmp_path / "FauxProgramFiles"
        make_app_folder(root, "AppUtiliseeHier", age_days=1)

        with with_roots(root):
            assert cleaner.find_candidate_orphaned_folders() == []

    def test_the_age_boundary_is_respected(self, cleaner, tmp_path):
        """`age_days < ORPHAN_MIN_AGE_DAYS` → exclu. Le jour pile au seuil
        devient candidat, la veille non."""
        root = tmp_path / "FauxProgramFiles"
        make_app_folder(root, "JusteAvantLeSeuil", age_days=ORPHAN_MIN_AGE_DAYS - 1)
        make_app_folder(root, "PileAuSeuil", age_days=ORPHAN_MIN_AGE_DAYS)
        make_app_folder(root, "BienApresLeSeuil", age_days=ORPHAN_MIN_AGE_DAYS + 60)

        with with_roots(root):
            names = {Path(c["path"]).name for c in cleaner.find_candidate_orphaned_folders()}

        assert "JusteAvantLeSeuil" not in names
        assert names == {"PileAuSeuil", "BienApresLeSeuil"}

    def test_a_single_recent_file_protects_the_whole_folder(self, cleaner, tmp_path):
        """L'âge retenu est le mtime le PLUS RÉCENT du dossier : un seul
        fichier touché hier suffit à sanctuariser un dossier par ailleurs
        très ancien."""
        root = tmp_path / "FauxProgramFiles"
        folder = make_app_folder(root, "AppAncienneMaisActive", age_days=900)
        recent = folder / "config_recent.ini"
        recent.write_bytes(b"x")

        with with_roots(root):
            assert cleaner.find_candidate_orphaned_folders() == []


class TestInstalledApplicationMatching:
    def test_a_folder_matching_an_installed_app_name_is_spared(self, tmp_path, triage):
        cleaner = ResidueCleaner(triage, FakeAppManager([{"name": "SuperEditeur", "publisher": "ACME"}]))
        root = tmp_path / "FauxProgramFiles"
        make_app_folder(root, "SuperEditeur", age_days=900)

        with with_roots(root):
            assert cleaner.find_candidate_orphaned_folders() == []

    def test_a_folder_matching_a_publisher_is_spared(self, tmp_path, triage):
        cleaner = ResidueCleaner(triage, FakeAppManager([{"name": "Outil X", "publisher": "Nuance"}]))
        root = tmp_path / "FauxProgramFiles"
        make_app_folder(root, "Nuance", age_days=900)

        with with_roots(root):
            assert cleaner.find_candidate_orphaned_folders() == []

    def test_matching_is_case_insensitive_and_partial(self, tmp_path, triage):
        cleaner = ResidueCleaner(triage, FakeAppManager([{"name": "VLC media player"}]))
        root = tmp_path / "FauxProgramFiles"
        make_app_folder(root, "VLC", age_days=900)

        with with_roots(root):
            assert cleaner.find_candidate_orphaned_folders() == []

    def test_apps_without_name_or_publisher_do_not_break_matching(self, tmp_path, triage):
        cleaner = ResidueCleaner(
            triage, FakeAppManager([{"name": None, "publisher": ""}, {"size_mb": 12}])
        )
        root = tmp_path / "FauxProgramFiles"
        make_app_folder(root, "Orphelin", age_days=900)

        with with_roots(root):
            assert len(cleaner.find_candidate_orphaned_folders()) == 1


class TestDetectionIsReadOnly:
    def test_find_never_moves_or_deletes_anything(self, cleaner, tmp_path):
        root = tmp_path / "FauxProgramFiles"
        folder = make_app_folder(root, "VieuxLogiciel", age_days=900)

        with with_roots(root):
            candidates = cleaner.find_candidate_orphaned_folders()

        assert len(candidates) == 1
        assert folder.exists()
        assert (folder / "app.dat").exists()
        assert cleaner.file_triage.list_staging() == [], "aucune mise de côté automatique"

    def test_unreadable_root_is_skipped_without_crashing(self, cleaner, tmp_path):
        root = tmp_path / "FauxProgramFiles"
        make_app_folder(root, "VieuxLogiciel", age_days=900)

        with with_roots(root), patch.object(Path, "iterdir", side_effect=PermissionError("refusé")):
            assert cleaner.find_candidate_orphaned_folders() == []

    def test_files_at_root_level_are_ignored_only_folders_count(self, cleaner, tmp_path):
        root = tmp_path / "FauxProgramFiles"
        root.mkdir()
        stray = root / "installeur_oublie.exe"
        stray.write_bytes(b"x")
        os.utime(stray, (time.time() - 900 * DAY,) * 2)

        with with_roots(root):
            assert cleaner.find_candidate_orphaned_folders() == []
        assert stray.exists()

    def test_candidates_are_sorted_by_size_descending(self, cleaner, tmp_path):
        root = tmp_path / "FauxProgramFiles"
        make_app_folder(root, "Petit", age_days=900, size_bytes=1024)
        make_app_folder(root, "Enorme", age_days=900, size_bytes=5 * 1024 * 1024)
        make_app_folder(root, "Moyen", age_days=900, size_bytes=2 * 1024 * 1024)

        with with_roots(root):
            names = [Path(c["path"]).name for c in cleaner.find_candidate_orphaned_folders()]

        assert names == ["Enorme", "Moyen", "Petit"]

    def test_candidate_reason_names_the_folder_and_its_age(self, cleaner, tmp_path):
        root = tmp_path / "FauxProgramFiles"
        make_app_folder(root, "VieuxLogiciel", age_days=200)

        with with_roots(root):
            candidate = cleaner.find_candidate_orphaned_folders()[0]

        assert "VieuxLogiciel" in candidate["reason"]
        assert candidate["age_days"] >= 199


class TestStagingIsReversible:
    def test_staged_folder_leaves_its_place_and_can_come_back(self, cleaner, tmp_path):
        root = tmp_path / "FauxProgramFiles"
        folder = make_app_folder(root, "VieuxLogiciel", age_days=900)
        content = (folder / "app.dat").read_bytes()

        result = cleaner.stage_orphaned_folder(str(folder), "orphelin")

        assert result["status"] == "ok"
        assert not folder.exists()

        staged = cleaner.file_triage.list_staging()
        assert len(staged) == 1
        assert cleaner.file_triage.restore_from_staging(staged[0]["id"]) is True
        assert (folder / "app.dat").read_bytes() == content

    def test_staging_a_missing_folder_reports_an_error(self, cleaner, tmp_path):
        result = cleaner.stage_orphaned_folder(str(tmp_path / "inexistant"), "orphelin")
        assert result["status"] == "erreur"
        assert cleaner.file_triage.list_staging() == []

    def test_staging_uses_the_single_shared_buffer(self, cleaner, tmp_path):
        """Garantie documentée : une SEULE zone tampon pour tout l'outil —
        le dossier mis de côté par residue_cleaner doit apparaître dans
        l'index de FileTriage (même écran de validation, même purge)."""
        root = tmp_path / "FauxProgramFiles"
        folder = make_app_folder(root, "VieuxLogiciel", age_days=900)

        cleaner.stage_orphaned_folder(str(folder), "orphelin détecté")

        entry = cleaner.file_triage.list_staging()[0]
        assert entry["reason"] == "orphelin détecté"
        assert Path(entry["original_path"]).name == "VieuxLogiciel"


class TestOrphanedShortcuts:
    def test_no_pywin32_means_no_shortcut_scan(self, cleaner):
        with patch.object(residue_cleaner, "PYWIN32_AVAILABLE", False):
            assert cleaner.find_orphaned_shortcuts() == []

    def _fake_shell(self, mapping):
        """mapping : nom du .lnk → chemin cible renvoyé par WScript.Shell."""
        def create_shortcut(path):
            shortcut = MagicMock()
            shortcut.TargetPath = mapping.get(Path(path).name, "")
            return shortcut

        shell = MagicMock()
        shell.CreateShortCut.side_effect = create_shortcut
        win32com = MagicMock()
        win32com.client.Dispatch.return_value = shell
        return win32com

    def test_only_shortcuts_with_a_missing_target_are_flagged(self, cleaner, tmp_path):
        bureau = tmp_path / "FauxBureau"
        bureau.mkdir()
        cible_vivante = tmp_path / "app_installee.exe"
        cible_vivante.write_bytes(b"x")
        for name in ("vivant.lnk", "mort.lnk", "sans_cible.lnk"):
            (bureau / name).write_bytes(b"lnk")

        win32com = self._fake_shell({
            "vivant.lnk": str(cible_vivante),
            "mort.lnk": str(tmp_path / "app_supprimee.exe"),
            "sans_cible.lnk": "",
        })

        with patch.object(residue_cleaner, "PYWIN32_AVAILABLE", True), \
             patch.object(residue_cleaner, "win32com", win32com, create=True), \
             patch.object(ResidueCleaner, "_shortcut_scan_folders", staticmethod(lambda: [bureau])):
            orphaned = cleaner.find_orphaned_shortcuts()

        assert [Path(o["path"]).name for o in orphaned] == ["mort.lnk"]
        assert all((bureau / n).exists() for n in ("vivant.lnk", "mort.lnk", "sans_cible.lnk")), (
            "la détection ne doit rien supprimer"
        )

    def test_an_unreadable_shortcut_is_skipped_silently(self, cleaner, tmp_path):
        bureau = tmp_path / "FauxBureau"
        bureau.mkdir()
        (bureau / "corrompu.lnk").write_bytes(b"x")

        win32com = MagicMock()
        win32com.client.Dispatch.return_value.CreateShortCut.side_effect = Exception("COM error")

        with patch.object(residue_cleaner, "PYWIN32_AVAILABLE", True), \
             patch.object(residue_cleaner, "win32com", win32com, create=True), \
             patch.object(ResidueCleaner, "_shortcut_scan_folders", staticmethod(lambda: [bureau])):
            assert cleaner.find_orphaned_shortcuts() == []

    def test_staging_shortcuts_is_reversible_and_counted(self, cleaner, tmp_path):
        bureau = tmp_path / "FauxBureau"
        bureau.mkdir()
        lnk = bureau / "mort.lnk"
        lnk.write_bytes(b"contenu-lnk")

        report = cleaner.stage_orphaned_shortcuts(
            [{"path": str(lnk), "target": "C:/absent.exe", "reason": "orphelin"}]
        )

        assert report == {"staged": 1, "errors": [], "total_candidates": 1}
        assert not lnk.exists()
        staged_id = cleaner.file_triage.list_staging()[0]["id"]
        assert cleaner.file_triage.restore_from_staging(staged_id) is True
        assert lnk.read_bytes() == b"contenu-lnk"

    def test_a_shortcut_that_cannot_be_staged_is_reported_as_an_error(self, cleaner, tmp_path):
        report = cleaner.stage_orphaned_shortcuts(
            [{"path": str(tmp_path / "absent.lnk"), "reason": "orphelin"}]
        )
        assert report["staged"] == 0
        assert report["errors"] == [str(tmp_path / "absent.lnk")]


class TestRegistryCleanupNeverTouchesTheRealRegistry:
    def test_no_winreg_means_no_registry_scan(self, cleaner):
        with patch.object(residue_cleaner, "WINREG_AVAILABLE", False):
            assert cleaner.find_orphaned_uninstall_entries() == []

    def test_no_winreg_means_backup_and_remove_refuses(self):
        with patch.object(residue_cleaner, "WINREG_AVAILABLE", False):
            result = ResidueCleaner.backup_and_remove_uninstall_entry({"subkey": "X"})
        assert result["status"] == "erreur"

    def _fake_winreg(self, subkeys):
        """subkeys : {nom_de_cle: {"DisplayName": ..., "InstallLocation": ...}}"""
        fake = MagicMock()
        fake.HKEY_LOCAL_MACHINE = 0x80000002
        fake.HKEY_CURRENT_USER = 0x80000001
        names = list(subkeys)

        opened_hives = []

        def open_key(hive_or_key, path):
            # 1er niveau : ouverture du chemin Uninstall d'une ruche
            if hive_or_key in (fake.HKEY_LOCAL_MACHINE, fake.HKEY_CURRENT_USER):
                # Une seule des 3 ruches parcourues est peuplée : on vérifie
                # aussi au passage que les 2 autres (WOW6432Node, HKCU)
                # absentes n'interrompent pas le balayage.
                if hive_or_key != fake.HKEY_LOCAL_MACHINE or "WOW6432Node" in path:
                    raise OSError("ruche absente")
                opened_hives.append(path)
                root = MagicMock()
                root._is_root = True
                return root
            # 2e niveau : ouverture d'une sous-clé (utilisée en context manager)
            values = subkeys[path]
            subkey = MagicMock()
            subkey.__enter__ = lambda s: s
            subkey.__exit__ = lambda s, *a: False
            subkey._values = values
            return subkey

        fake.OpenKey.side_effect = open_key
        fake.QueryInfoKey.side_effect = lambda key: (len(names), 0, 0)
        fake.EnumKey.side_effect = lambda key, i: names[i]

        def query_value_ex(subkey, name):
            if name not in subkey._values:
                raise OSError("valeur absente")
            return (subkey._values[name], 1)

        fake.QueryValueEx.side_effect = query_value_ex
        return fake

    def test_only_entries_whose_install_path_disappeared_are_flagged(self, cleaner, tmp_path):
        installe = tmp_path / "AppToujoursLa"
        installe.mkdir()
        fake = self._fake_winreg({
            "AppVivante": {"DisplayName": "App Vivante", "InstallLocation": str(installe)},
            "AppMorte": {"DisplayName": "App Morte", "InstallLocation": str(tmp_path / "disparue")},
            "SansNom": {"InstallLocation": str(tmp_path / "disparue2")},
            "SansChemin": {"DisplayName": "App Sans Chemin"},
        })

        with patch.object(residue_cleaner, "WINREG_AVAILABLE", True), \
             patch.object(residue_cleaner, "winreg", fake, create=True):
            orphaned = cleaner.find_orphaned_uninstall_entries()

        assert [o["subkey"] for o in orphaned] == ["AppMorte"], (
            "sans DisplayName ou sans InstallLocation, on ne juge pas — on ne touche pas"
        )
        assert orphaned[0]["hive_name"] == "HKLM"
        fake.DeleteKey.assert_not_called()

    def test_scanning_never_deletes(self, cleaner, tmp_path):
        fake = self._fake_winreg({
            "AppMorte": {"DisplayName": "App Morte", "InstallLocation": str(tmp_path / "disparue")},
        })
        with patch.object(residue_cleaner, "WINREG_AVAILABLE", True), \
             patch.object(residue_cleaner, "winreg", fake, create=True):
            cleaner.find_orphaned_uninstall_entries()

        fake.DeleteKey.assert_not_called()
        fake.SetValueEx.assert_not_called()

    def test_backup_happens_before_deletion(self, tmp_path):
        """Garde-fou central : toutes les valeurs sont copiées sous la clé
        de sauvegarde AVANT que l'originale ne soit supprimée. Si l'ordre
        s'inversait, un échec de sauvegarde ferait perdre l'entrée."""
        fake = MagicMock()
        fake.HKEY_LOCAL_MACHINE = 0x80000002
        fake.HKEY_CURRENT_USER = 0x80000001

        src_key = MagicMock()
        src_key.__enter__ = lambda s: s
        src_key.__exit__ = lambda s, *a: False
        fake.OpenKey.return_value = src_key
        fake.EnumValue.side_effect = [
            ("DisplayName", "App Morte", 1),
            ("InstallLocation", "C:/Disparue", 1),
            OSError("fin"),
        ]
        backup_key = MagicMock()
        fake.CreateKey.return_value = backup_key

        order = []
        fake.SetValueEx.side_effect = lambda *a, **k: order.append(("set", a[1]))
        fake.DeleteKey.side_effect = lambda *a, **k: order.append(("delete", a[1]))

        entry = {
            "hive": fake.HKEY_LOCAL_MACHINE,
            "hive_name": "HKLM",
            "parent_path": r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
            "subkey": "AppMorte",
            "display_name": "App Morte",
            "install_location": "C:/Disparue",
        }

        with patch.object(residue_cleaner, "WINREG_AVAILABLE", True), \
             patch.object(residue_cleaner, "winreg", fake, create=True):
            result = ResidueCleaner.backup_and_remove_uninstall_entry(entry)

        assert result["status"] == "ok"
        assert [o[0] for o in order] == ["set", "set", "delete"], (
            "la sauvegarde doit précéder la suppression"
        )
        assert {o[1] for o in order if o[0] == "set"} == {"DisplayName", "InstallLocation"}

    def test_backup_key_is_created_under_the_dedicated_path(self, tmp_path):
        fake = MagicMock()
        fake.HKEY_LOCAL_MACHINE = 0x80000002
        fake.HKEY_CURRENT_USER = 0x80000001
        src_key = MagicMock()
        src_key.__enter__ = lambda s: s
        src_key.__exit__ = lambda s, *a: False
        fake.OpenKey.return_value = src_key
        fake.EnumValue.side_effect = OSError("aucune valeur")

        entry = {
            "hive": fake.HKEY_LOCAL_MACHINE, "hive_name": "HKLM",
            "parent_path": "P", "subkey": "AppMorte",
            "display_name": "App Morte", "install_location": "C:/x",
        }

        with patch.object(residue_cleaner, "WINREG_AVAILABLE", True), \
             patch.object(residue_cleaner, "winreg", fake, create=True):
            ResidueCleaner.backup_and_remove_uninstall_entry(entry)

        fake.CreateKey.assert_called_once_with(
            fake.HKEY_CURRENT_USER, f"{REGISTRY_BACKUP_PATH}\\HKLM_AppMorte"
        )

    def test_a_registry_failure_is_reported_not_raised(self):
        fake = MagicMock()
        fake.HKEY_LOCAL_MACHINE = 0x80000002
        fake.HKEY_CURRENT_USER = 0x80000001
        fake.OpenKey.side_effect = OSError("accès refusé")

        entry = {
            "hive": fake.HKEY_LOCAL_MACHINE, "hive_name": "HKLM",
            "parent_path": "P", "subkey": "AppMorte",
            "display_name": "App Morte", "install_location": "C:/x",
        }

        with patch.object(residue_cleaner, "WINREG_AVAILABLE", True), \
             patch.object(residue_cleaner, "winreg", fake, create=True):
            result = ResidueCleaner.backup_and_remove_uninstall_entry(entry)

        assert result["status"] == "erreur"
        assert "accès refusé" in result["message"]
        fake.DeleteKey.assert_not_called()
