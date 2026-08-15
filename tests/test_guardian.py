"""
test_guardian.py
Couvre optimizer/guardian.py — le "Mode Gardien", orchestrateur un-clic
qui peut aussi tourner SANS SUPERVISION HUMAINE (tâche planifiée Windows,
flag --guardian).

C'est le mode le plus risqué du produit : personne ne regarde l'écran
pendant qu'il agit. Le module s'engage sur une RÈGLE ABSOLUE écrite en
tête de fichier :

    « La SUPPRESSION DÉFINITIVE (purge_staging) n'est JAMAIS déclenchée
      automatiquement, ni par run_full_pass(), ni par run_unattended(). »

Les tests ci-dessous transforment cette phrase en garde-fou exécutable :
un faux moteur enregistre TOUS les appels, et on vérifie qu'aucun chemin
automatique n'atteint purge_staging(). On vérifie aussi que la catégorie
'caution' (fichiers nécessitant un examen humain) n'est jamais mise de
côté automatiquement, et que le staging reste réversible de bout en bout
avec un vrai FileTriage.

Aucun test ne touche au système réel : moteur mocké, Path.home() redirigé
vers `tmp_path`.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

guardian = pytest.importorskip(
    "optimizer.guardian", reason="guardian indisponible sur cette plateforme"
)
file_triage_mod = pytest.importorskip("optimizer.file_triage")

SystemGuardian = guardian.SystemGuardian
FileTriage = file_triage_mod.FileTriage
INTERACTIVE_UNUSED_THRESHOLD_DAYS = guardian.INTERACTIVE_UNUSED_THRESHOLD_DAYS
UNATTENDED_UNUSED_THRESHOLD_DAYS = guardian.UNATTENDED_UNUSED_THRESHOLD_DAYS
DEFAULT_PURGE_AFTER_DAYS = guardian.DEFAULT_PURGE_AFTER_DAYS


class RecordingFileTriage:
    """Faux FileTriage qui journalise tout — permet d'affirmer qu'une
    méthode destructive n'a JAMAIS été appelée."""

    def __init__(self, triage_result=None):
        self.triage_result = triage_result or {"safe": [], "caution": []}
        self.calls = []
        self.staged = []
        self.staging_entries = []

    def triage_directory(self, folder, include_duplicates=None):
        self.calls.append(("triage_directory", folder, include_duplicates))
        return self.triage_result

    def move_to_staging(self, path, reason):
        self.calls.append(("move_to_staging", path, reason))
        self.staged.append(path)
        return f"id-{len(self.staged)}"

    def purge_staging(self, older_than_days=30):
        self.calls.append(("purge_staging", older_than_days))
        return 7

    def list_staging(self):
        self.calls.append(("list_staging",))
        return self.staging_entries


def make_engine(triage=None, scan_results=None, folders_exist=True):
    """Faux AntivirusEngine : aucune des briques réelles n'est touchée."""
    engine = MagicMock()
    engine.file_triage = triage if triage is not None else RecordingFileTriage()
    engine.disk_analyzer.find_duplicate_files.return_value = []
    engine.temp_cleaner.run_full_cleanup.return_value = {"total_freed_mb": 12.5, "results": []}
    engine.scan_directory.return_value = scan_results if scan_results is not None else []
    engine.folder_organizer.organize_least_used.return_value = {"moved": 3, "note": "ok"}
    engine.folder_organizer.find_least_used_files.return_value = {"files": [], "note": "aperçu"}
    engine.ransomware_shield = None
    engine._realtime_monitor = None
    return engine


@pytest.fixture
def sandbox_home(tmp_path):
    home = tmp_path / "faux_profil"
    for sub in ("Downloads", "Desktop", "Documents"):
        (home / sub).mkdir(parents=True)
    with patch.object(guardian.Path, "home", staticmethod(lambda: home)):
        yield home


class TestNoAutomaticPermanentDeletion:
    """La règle absolue du module, rendue exécutable."""

    def test_run_full_pass_never_purges(self, tmp_path):
        folder = tmp_path / "atelier"
        folder.mkdir()
        triage = RecordingFileTriage({"safe": [{"path": str(folder / "a.tmp"), "reason": "r"}], "caution": []})
        engine = make_engine(triage)

        SystemGuardian(engine).run_full_pass(folders=[str(folder)])

        assert not any(c[0] == "purge_staging" for c in triage.calls), (
            "run_full_pass() ne doit JAMAIS déclencher de suppression définitive"
        )

    def test_run_unattended_never_purges(self, tmp_path, sandbox_home):
        folder = tmp_path / "atelier"
        folder.mkdir()
        triage = RecordingFileTriage({"safe": [{"path": str(folder / "a.tmp"), "reason": "r"}], "caution": []})
        engine = make_engine(triage)

        SystemGuardian(engine).run_unattended()

        assert not any(c[0] == "purge_staging" for c in triage.calls), (
            "le mode planifié sans supervision ne doit JAMAIS supprimer définitivement"
        )

    def test_no_guardian_entry_point_deletes_except_the_explicit_one(self, tmp_path, sandbox_home):
        """Balayage de TOUS les points d'entrée automatiques du module :
        seul confirm_permanent_deletion() a le droit d'appeler purge_staging()."""
        folder = tmp_path / "atelier"
        folder.mkdir()
        triage = RecordingFileTriage({"safe": [], "caution": []})
        g = SystemGuardian(make_engine(triage))

        g.stage_disposable_files([str(folder)])
        g.run_full_pass(folders=[str(folder)])
        g.run_full_pass(folders=[str(folder)], auto_apply_organization=False)
        g.run_unattended()
        g.review_pending_deletions()

        assert not any(c[0] == "purge_staging" for c in triage.calls)

        g.confirm_permanent_deletion()
        assert ("purge_staging", DEFAULT_PURGE_AFTER_DAYS) in triage.calls

    def test_confirm_permanent_deletion_forwards_the_retention_delay(self):
        triage = RecordingFileTriage()
        g = SystemGuardian(make_engine(triage))

        assert g.confirm_permanent_deletion(older_than_days=90) == 7
        assert ("purge_staging", 90) in triage.calls

    def test_review_pending_deletions_is_read_only(self):
        triage = RecordingFileTriage()
        triage.staging_entries = [{"id": "a"}, {"id": "b"}]
        g = SystemGuardian(make_engine(triage))

        assert g.review_pending_deletions() == [{"id": "a"}, {"id": "b"}]
        assert [c[0] for c in triage.calls] == ["list_staging"]


class TestCautionFilesAreNeverStagedAutomatically:
    def test_only_safe_files_are_staged(self, tmp_path):
        folder = tmp_path / "atelier"
        folder.mkdir()
        triage = RecordingFileTriage({
            "safe": [
                {"path": str(folder / "cache.tmp"), "reason": "temporaire"},
                {"path": str(folder / "trace.log"), "reason": "journal"},
            ],
            "caution": [
                {"path": str(folder / "setup.exe"), "reason": "vieil installeur"},
                {"path": str(folder / "archive.zip"), "reason": "gros fichier ancien"},
            ],
        })
        g = SystemGuardian(make_engine(triage))

        report = g.stage_disposable_files([str(folder)])

        assert set(triage.staged) == {str(folder / "cache.tmp"), str(folder / "trace.log")}
        assert str(folder / "setup.exe") not in triage.staged
        assert str(folder / "archive.zip") not in triage.staged
        assert report["folders"][0]["staged"] == 2
        assert report["folders"][0]["needs_manual_review"] == 2

    def test_caution_files_survive_a_full_unattended_pass(self, tmp_path, sandbox_home):
        folder = tmp_path / "atelier"
        folder.mkdir()
        triage = RecordingFileTriage({
            "safe": [],
            "caution": [{"path": str(folder / "these.docx"), "reason": "gros fichier ancien"}],
        })
        g = SystemGuardian(make_engine(triage))

        g.run_unattended()

        assert triage.staged == [], "aucun fichier 'à vérifier' ne doit être touché sans humain"

    def test_missing_folders_are_skipped_not_created(self, tmp_path):
        triage = RecordingFileTriage()
        g = SystemGuardian(make_engine(triage))
        absent = tmp_path / "jamais_installe"

        report = g.stage_disposable_files([str(absent)])

        assert report == {"folders": []}
        assert not absent.exists()
        assert triage.calls == []

    def test_an_unreadable_folder_is_reported_and_does_not_abort_the_others(self, tmp_path):
        ok_folder = tmp_path / "lisible"
        ok_folder.mkdir()
        ko_folder = tmp_path / "illisible"
        ko_folder.mkdir()

        triage = RecordingFileTriage({"safe": [{"path": str(ok_folder / "a.tmp"), "reason": "r"}], "caution": []})
        engine = make_engine(triage)

        def maybe_fail(folder):
            if folder == str(ko_folder):
                raise PermissionError("accès refusé")
            return []

        engine.disk_analyzer.find_duplicate_files.side_effect = maybe_fail
        report = SystemGuardian(engine).stage_disposable_files([str(ko_folder), str(ok_folder)])

        assert report["folders"][0]["error"] == "accès refusé"
        assert report["folders"][1]["staged"] == 1

    def test_duplicates_are_forwarded_to_the_triage(self, tmp_path):
        folder = tmp_path / "atelier"
        folder.mkdir()
        duplicates = [{"paths": ["/a", "/b"], "size_mb": 1.0}]
        triage = RecordingFileTriage()
        engine = make_engine(triage)
        engine.disk_analyzer.find_duplicate_files.return_value = duplicates

        SystemGuardian(engine).stage_disposable_files([str(folder)])

        assert ("triage_directory", str(folder), duplicates) in triage.calls


class TestStagingIsTrulyReversible:
    def test_end_to_end_with_a_real_file_triage(self, tmp_path):
        """Aller-retour complet avec le VRAI FileTriage : le Mode Gardien
        met de côté un fichier jetable, et l'utilisateur peut le récupérer
        intégralement."""
        folder = tmp_path / "atelier"
        folder.mkdir()
        jetable = folder / "cache.tmp"
        jetable.write_bytes(b"CONTENU-A-RECUPERER")

        real_triage = FileTriage(str(tmp_path / "staging"))
        engine = make_engine(MagicMock())
        engine.file_triage = real_triage
        engine.disk_analyzer.find_duplicate_files.return_value = []

        g = SystemGuardian(engine)
        report = g.stage_disposable_files([str(folder)])

        assert report["folders"][0]["staged"] == 1
        assert not jetable.exists()

        pending = g.review_pending_deletions()
        assert len(pending) == 1

        assert real_triage.restore_from_staging(pending[0]["id"]) is True
        assert jetable.read_bytes() == b"CONTENU-A-RECUPERER"

    def test_protected_files_are_not_staged_end_to_end(self, tmp_path):
        """Avec le vrai FileTriage : un .docx et une photo présents dans le
        dossier ne doivent jamais être mis de côté par le Mode Gardien."""
        folder = tmp_path / "atelier"
        folder.mkdir()
        (folder / "these.docx").write_bytes(b"TRAVAIL")
        (folder / "photo.jpg").write_bytes(b"SOUVENIR")
        (folder / "cache.tmp").write_bytes(b"jetable")

        real_triage = FileTriage(str(tmp_path / "staging"))
        engine = make_engine(MagicMock())
        engine.file_triage = real_triage
        engine.disk_analyzer.find_duplicate_files.return_value = []

        SystemGuardian(engine).stage_disposable_files([str(folder)])

        assert (folder / "these.docx").read_bytes() == b"TRAVAIL"
        assert (folder / "photo.jpg").read_bytes() == b"SOUVENIR"
        assert not (folder / "cache.tmp").exists()

    def test_a_staging_failure_is_not_counted_as_staged(self, tmp_path):
        folder = tmp_path / "atelier"
        folder.mkdir()
        triage = RecordingFileTriage({"safe": [{"path": str(folder / "absent.tmp"), "reason": "r"}], "caution": []})
        triage.move_to_staging = lambda path, reason: ""
        engine = make_engine(triage)

        report = SystemGuardian(engine).stage_disposable_files([str(folder)])
        assert report["folders"][0]["staged"] == 0


class TestThresholdsAndOrchestration:
    def test_interactive_pass_uses_the_180_day_threshold(self, tmp_path):
        folder = tmp_path / "atelier"
        folder.mkdir()
        engine = make_engine()

        SystemGuardian(engine).run_full_pass(folders=[str(folder)])

        engine.folder_organizer.organize_least_used.assert_called_once_with(
            str(folder), unused_since_days=INTERACTIVE_UNUSED_THRESHOLD_DAYS
        )
        assert INTERACTIVE_UNUSED_THRESHOLD_DAYS == 180

    def test_unattended_pass_uses_the_more_cautious_365_day_threshold(self, sandbox_home):
        """Sans supervision humaine, le seuil doit être plus prudent —
        sinon la tâche planifiée déplacerait des fichiers que l'utilisateur
        cherche le lendemain."""
        engine = make_engine()

        SystemGuardian(engine).run_unattended()

        for c in engine.folder_organizer.organize_least_used.call_args_list:
            assert c.kwargs["unused_since_days"] == UNATTENDED_UNUSED_THRESHOLD_DAYS
        assert UNATTENDED_UNUSED_THRESHOLD_DAYS == 365
        assert UNATTENDED_UNUSED_THRESHOLD_DAYS > INTERACTIVE_UNUSED_THRESHOLD_DAYS

    def test_preview_mode_moves_nothing(self, tmp_path):
        folder = tmp_path / "atelier"
        folder.mkdir()
        engine = make_engine()

        report = SystemGuardian(engine).run_full_pass(
            folders=[str(folder)], auto_apply_organization=False
        )

        engine.folder_organizer.organize_least_used.assert_not_called()
        engine.folder_organizer.find_least_used_files.assert_called_once()
        assert report["reorganizations"][0]["preview_only"] is True

    def test_scan_can_be_disabled(self, tmp_path):
        folder = tmp_path / "atelier"
        folder.mkdir()
        engine = make_engine()

        report = SystemGuardian(engine).run_full_pass(folders=[str(folder)], scan_files=False)

        engine.scan_directory.assert_not_called()
        assert report["scans"] == []

    def test_scan_counts_only_malicious_verdicts(self, tmp_path):
        folder = tmp_path / "atelier"
        folder.mkdir()
        engine = make_engine(scan_results=[
            {"verdict": "SAIN"}, {"verdict": "MALVEILLANT"},
            {"verdict": "SUSPECT"}, {"verdict": "MALVEILLANT"},
        ])

        report = SystemGuardian(engine).run_full_pass(folders=[str(folder)])

        assert report["scans"][0]["files_scanned"] == 4
        assert report["scans"][0]["threats_quarantined"] == 2

    def test_nonexistent_folders_are_filtered_out_of_the_pass(self, tmp_path):
        real = tmp_path / "existe"
        real.mkdir()
        engine = make_engine()

        report = SystemGuardian(engine).run_full_pass(
            folders=[str(real), str(tmp_path / "absent")]
        )

        assert report["folders"] == [str(real)]
        assert engine.scan_directory.call_count == 1

    def test_pass_with_no_valid_folder_still_cleans_temp_and_reports(self, tmp_path):
        engine = make_engine()

        report = SystemGuardian(engine).run_full_pass(folders=[str(tmp_path / "absent")])

        engine.temp_cleaner.run_full_cleanup.assert_called_once()
        assert report["folders"] == []
        assert report["scans"] == []
        assert report["temp_cleanup"]["total_freed_mb"] == 12.5

    def test_default_folders_are_the_three_sensitive_user_folders(self, sandbox_home):
        folders = SystemGuardian.default_folders()
        assert [Path(f).name for f in folders] == ["Downloads", "Desktop", "Documents"]
        assert all(Path(f).parent == sandbox_home for f in folders)

    def test_unattended_report_is_aggregatable_without_keyerror(self, sandbox_home, tmp_path):
        """run_unattended() agrège son propre rapport pour le journal :
        toute clé manquante ferait planter la tâche planifiée."""
        triage = RecordingFileTriage({"safe": [{"path": "/x.tmp", "reason": "r"}], "caution": [{"path": "/y"}]})
        engine = make_engine(triage, scan_results=[{"verdict": "MALVEILLANT"}])

        report = SystemGuardian(engine).run_unattended()

        assert set(report) >= {"folders", "temp_cleanup", "scans", "reorganizations", "staged_for_deletion"}
        assert len(report["scans"]) == 3
        assert sum(s["threats_quarantined"] for s in report["scans"]) == 3


class TestContinuousProtectionActivation:
    def test_activation_creates_the_shield_and_starts_monitoring(self, sandbox_home):
        engine = make_engine()

        activated = SystemGuardian(engine).activate_continuous_protection()

        assert activated == {"ransomware_shield": True, "realtime_protection": True}
        assert engine.ransomware_shield is not None
        engine.start_realtime_protection.assert_called_once()
        assert engine.start_realtime_protection.call_args.kwargs["blocking"] is False, (
            "la surveillance doit être non bloquante — le menu reste utilisable"
        )

    def test_activation_is_idempotent(self, sandbox_home):
        """Rappeler l'activation ne doit pas relancer un 2e moniteur ni
        écraser le bouclier existant (canaris déjà déployés)."""
        engine = make_engine()
        g = SystemGuardian(engine)

        g.activate_continuous_protection()
        existing_shield = engine.ransomware_shield
        engine._realtime_monitor = MagicMock()

        activated = g.activate_continuous_protection()

        assert activated == {"ransomware_shield": False, "realtime_protection": False}
        assert engine.ransomware_shield is existing_shield
        assert engine.start_realtime_protection.call_count == 1

    def test_activation_never_deletes_anything(self, sandbox_home):
        triage = RecordingFileTriage()
        engine = make_engine(triage)

        SystemGuardian(engine).activate_continuous_protection()

        assert triage.calls == []
