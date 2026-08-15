"""
test_ransomware_shield.py
Couvre optimizer/ransomware_shield.py : fichiers canari, détection de
modification massive, et surtout la COUCHE STATISTIQUE ADAPTATIVE
(_OnlineStats / _cantelli_t / adaptive_threshold), vérifiée sur des
valeurs de référence calculées à la main.

Pourquoi insister sur les maths : ce seuil décide si l'alarme
anti-ransomware se déclenche ou non. Une erreur de formule ne se voit
pas à l'œil nu et se paie en fichiers chiffrés. Chaque valeur attendue
ci-dessous est donc dérivée à la main dans le commentaire du test, pas
recopiée depuis la sortie du code.

Aucun test ne touche au système réel : les canaris sont déployés sous
`tmp_path`, `subprocess` et `psutil` sont mockés (aucun `attrib`, aucun
processus réellement suspendu).
"""

import math
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ransomware_shield = pytest.importorskip(
    "optimizer.ransomware_shield", reason="ransomware_shield indisponible sur cette plateforme"
)

RansomwareShield = ransomware_shield.RansomwareShield
_OnlineStats = ransomware_shield._OnlineStats
_cantelli_t = ransomware_shield._cantelli_t
CANARY_FILENAMES = ransomware_shield.CANARY_FILENAMES
CANARY_CONTENT = ransomware_shield.CANARY_CONTENT
MASS_MODIFICATION_THRESHOLD = ransomware_shield.MASS_MODIFICATION_THRESHOLD
MASS_MODIFICATION_WINDOW_SECONDS = ransomware_shield.MASS_MODIFICATION_WINDOW_SECONDS
BASELINE_MIN_SAMPLES = ransomware_shield.BASELINE_MIN_SAMPLES
BASELINE_TARGET_FALSE_POSITIVE_RATE = ransomware_shield.BASELINE_TARGET_FALSE_POSITIVE_RATE


@pytest.fixture
def no_subprocess():
    """Neutralise `attrib` : aucun appel système réel pendant les tests."""
    with patch.object(ransomware_shield.subprocess, "run") as run:
        run.return_value = MagicMock(returncode=0)
        yield run


@pytest.fixture
def protected(tmp_path):
    folder = tmp_path / "dossier_protege"
    folder.mkdir()
    return folder


class TestOnlineStatsAgainstHandComputedValues:
    """Welford : chaque valeur attendue est calculée à la main ci-dessous."""

    def test_textbook_sample_mean_and_variance(self):
        """Échantillon [2,4,4,4,5,5,7,9] (exemple classique) :
          μ = (2+4+4+4+5+5+7+9)/8 = 40/8 = 5
          Σ(x−μ)² = 9+1+1+1+0+0+4+16 = 32
          variance de population = 32/8 = 4      → σ = 2
        """
        stats = _OnlineStats()
        for x in [2, 4, 4, 4, 5, 5, 7, 9]:
            stats.update(x)

        assert stats.n == 8
        assert stats.mean == pytest.approx(5.0)
        assert stats.variance == pytest.approx(4.0)
        assert stats.std == pytest.approx(2.0)

    def test_variance_divides_by_n_population_not_n_minus_1(self):
        """Constat factuel : l'implémentation renvoie la variance de
        POPULATION (_m2 / n), pas la variance d'échantillon (_m2 / (n−1)).
        Pour [1,2,3,4,5] : Σ(x−3)² = 4+1+0+1+4 = 10
          → population : 10/5 = 2.0   (valeur renvoyée)
          → échantillon : 10/4 = 2.5  (convention usuelle de Welford)
        Écart de 3% seulement à n = 30, et dans le sens conservateur
        (σ sous-estimé → seuil plus bas → détection plus précoce)."""
        stats = _OnlineStats()
        for x in [1, 2, 3, 4, 5]:
            stats.update(x)

        assert stats.variance == pytest.approx(2.0)
        assert stats.variance != pytest.approx(2.5)

    def test_constant_series_has_zero_variance(self):
        stats = _OnlineStats()
        for _ in range(50):
            stats.update(7)
        assert stats.mean == pytest.approx(7.0)
        assert stats.variance == pytest.approx(0.0)
        assert stats.std == pytest.approx(0.0)

    def test_single_sample_variance_is_zero_not_a_division_error(self):
        stats = _OnlineStats()
        stats.update(42)
        assert stats.n == 1
        assert stats.mean == pytest.approx(42.0)
        assert stats.variance == 0.0

    def test_empty_stats_are_neutral(self):
        stats = _OnlineStats()
        assert (stats.n, stats.mean, stats.variance, stats.std) == (0, 0.0, 0.0, 0.0)

    def test_welford_matches_the_naive_formula_on_large_offset_values(self):
        """Intérêt de Welford : rester stable là où E[X²] − E[X]² explose.
        Série 1e8 + [1..100] : la variance ne dépend pas de l'offset, donc
        variance de population de [1..100] = (100² − 1)/12 = 833.25."""
        stats = _OnlineStats()
        for i in range(1, 101):
            stats.update(1e8 + i)

        assert stats.variance == pytest.approx(833.25, rel=1e-6)

    def test_incremental_mean_matches_batch_mean(self):
        values = [3, 1, 4, 1, 5, 9, 2, 6, 5, 3, 5]
        stats = _OnlineStats()
        for v in values:
            stats.update(v)
        assert stats.mean == pytest.approx(sum(values) / len(values))


class TestCantelliBound:
    def test_reference_values(self):
        """t = √((1 − p)/p)
          p = 0.5 → √1     = 1.0
          p = 0.2 → √4     = 2.0
          p = 0.1 → √9     = 3.0
          p = 1e-3 → √999  ≈ 31.6069612586
        """
        assert _cantelli_t(0.5) == pytest.approx(1.0)
        assert _cantelli_t(0.2) == pytest.approx(2.0)
        assert _cantelli_t(0.1) == pytest.approx(3.0)
        assert _cantelli_t(1e-3) == pytest.approx(math.sqrt(999))
        assert _cantelli_t(1e-3) == pytest.approx(31.6069612586, rel=1e-9)

    def test_the_bound_inverts_correctly(self):
        """Cantelli : P(X − μ ≥ tσ) ≤ 1/(1 + t²). En réinjectant
        t = √((1−p)/p), on doit retrouver exactement p."""
        for p in (0.5, 0.2, 0.05, 1e-3, 1e-6):
            t = _cantelli_t(p)
            assert 1.0 / (1.0 + t * t) == pytest.approx(p, rel=1e-12)

    def test_a_stricter_false_positive_rate_gives_a_larger_t(self):
        assert _cantelli_t(1e-4) > _cantelli_t(1e-3) > _cantelli_t(1e-2)

    def test_project_configuration_uses_one_in_a_thousand(self):
        assert BASELINE_TARGET_FALSE_POSITIVE_RATE == 1e-3
        assert BASELINE_MIN_SAMPLES == 30


class TestAdaptiveThreshold:
    def test_floor_is_used_until_enough_samples(self):
        shield = RansomwareShield([])
        for _ in range(BASELINE_MIN_SAMPLES - 1):
            shield._baseline_stats.update(1)

        assert shield._baseline_stats.n == 29
        assert shield.adaptive_threshold() == float(MASS_MODIFICATION_THRESHOLD)

    def test_a_perfectly_calm_machine_stays_on_the_floor(self):
        """μ = 0, σ = 0 → μ + tσ = 0 → max(15, 0) = 15."""
        shield = RansomwareShield([])
        for _ in range(40):
            shield._baseline_stats.update(0)

        assert shield.adaptive_threshold() == 15.0

    def test_hand_computed_threshold_on_a_busy_machine(self):
        """Baseline alternant 3 et 7, 40 échantillons :
          μ = 5, Σ(x−μ)² = 40×4 = 160, variance = 160/40 = 4 → σ = 2
          seuil = μ + t·σ = 5 + 31.6069612586×2 = 68.2139225172
          max(15, 68.21…) = 68.2139225172
        """
        shield = RansomwareShield([])
        for i in range(40):
            shield._baseline_stats.update(3 if i % 2 == 0 else 7)

        assert shield._baseline_stats.mean == pytest.approx(5.0)
        assert shield._baseline_stats.std == pytest.approx(2.0)
        assert shield.adaptive_threshold() == pytest.approx(68.2139225172, rel=1e-9)

    def test_threshold_never_drops_below_the_empirical_floor(self):
        """μ + tσ = 1 + 0 = 1, mais le plancher garantit 15."""
        shield = RansomwareShield([])
        for _ in range(40):
            shield._baseline_stats.update(1)

        assert shield.adaptive_threshold() == float(MASS_MODIFICATION_THRESHOLD)

    def test_reset_baseline_returns_to_the_floor(self):
        shield = RansomwareShield([])
        for i in range(40):
            shield._baseline_stats.update(3 if i % 2 == 0 else 7)
        assert shield.adaptive_threshold() > 15

        shield.reset_baseline()

        assert shield._baseline_stats.n == 0
        assert shield.adaptive_threshold() == 15.0

    def test_the_adaptive_layer_can_only_raise_the_threshold(self):
        """Constat factuel : à cause de `max(plancher, μ + tσ)`, le seuil
        adaptatif est TOUJOURS ≥ 15 — voir le test xfail ci-dessous."""
        for baseline in ([0] * 40, [1] * 40, [2, 3] * 20, [10] * 40):
            shield = RansomwareShield([])
            for x in baseline:
                shield._baseline_stats.update(x)
            assert shield.adaptive_threshold() >= float(MASS_MODIFICATION_THRESHOLD)

    def test_a_very_calm_machine_never_goes_below_the_fixed_floor(self):
        """LIMITE DE CONCEPTION, volontairement figée par ce test.

        Sur une machine totalement inactive, μ = σ = 0 : le seuil adaptatif
        vaudrait 0, mais max(plancher, μ+tσ) le ramène à 15. La couche
        adaptative n'apporte donc RIEN sur machine calme — elle ne sait que
        relever le seuil sur machine active, jamais l'abaisser.

        Ce test échouera si quelqu'un décide un jour d'autoriser un seuil
        sous le plancher : c'est un arbitrage de politique de détection
        (davantage de fausses alertes contre une détection plus précoce),
        qui doit être un choix conscient et non un effet de bord.
        """
        shield = RansomwareShield([])
        for _ in range(60):
            shield._baseline_stats.update(0)  # machine totalement inactive

        assert shield.adaptive_threshold() == float(MASS_MODIFICATION_THRESHOLD)


class TestMassModificationDetection:
    def test_below_the_floor_no_alert(self):
        shield = RansomwareShield([])
        for _ in range(MASS_MODIFICATION_THRESHOLD - 1):
            assert shield.record_modification_event() is False

    def test_reaching_the_floor_triggers_the_alert(self):
        shield = RansomwareShield([])
        results = [shield.record_modification_event() for _ in range(MASS_MODIFICATION_THRESHOLD)]

        assert results[-1] is True
        assert results[:-1] == [False] * (MASS_MODIFICATION_THRESHOLD - 1)

    def test_events_outside_the_sliding_window_are_forgotten(self):
        """14 événements anciens + 1 récent ne doivent pas déclencher :
        la fenêtre glissante purge les timestamps trop vieux."""
        shield = RansomwareShield([])
        old = time.time() - (MASS_MODIFICATION_WINDOW_SECONDS + 5)
        for _ in range(14):
            shield._modification_timestamps.append(old)

        assert shield.record_modification_event() is False
        assert len(shield._modification_timestamps) == 1

    def test_reset_clears_the_counter(self):
        shield = RansomwareShield([])
        for _ in range(10):
            shield.record_modification_event()
        shield.reset_modification_counter()

        assert len(shield._modification_timestamps) == 0
        assert shield.record_modification_event() is False

    def test_a_learned_high_baseline_silences_a_real_burst(self):
        """BUG RÉEL, conséquence directe de la couche adaptative :

        Sur une machine dont la baseline a été apprise "active"
        (μ = 5, σ = 2 → seuil 68,2), une rafale de 60 fichiers modifiés en
        10 secondes — un chiffrement ransomware caractérisé — ne déclenche
        AUCUNE alerte, alors que l'ancien seuil fixe (15) l'aurait détectée
        au 15e fichier. La couche adaptative rend donc la détection
        strictement MOINS sensible, à l'inverse de ce qu'annonce la
        docstring de record_modification_event()."""
        shield = RansomwareShield([])
        for i in range(40):
            shield._baseline_stats.update(3 if i % 2 == 0 else 7)

        alerts = [shield.record_modification_event() for _ in range(60)]

        assert shield.adaptive_threshold() == pytest.approx(68.2139225172, rel=1e-9)
        assert not any(alerts), "comportement observé : aucune alerte sur 60 modifications/10s"

    def test_the_baseline_learns_from_the_attack_itself(self):
        """BUG RÉEL (auto-empoisonnement de la baseline) :
        _sample_baseline_if_due() échantillonne `current_count`, c'est-à-dire
        le nombre d'événements de la fenêtre EN COURS — attaque comprise.
        Une attaque en cours fait donc monter μ, donc monter le seuil, donc
        rend les attaques suivantes encore plus difficiles à détecter.
        Une baseline saine ne devrait apprendre que des fenêtres jugées
        normales (ou exclure les fenêtres ayant déclenché une alerte)."""
        shield = RansomwareShield([])
        for _ in range(50):
            # Chaque fenêtre est considérée écoulée → un point de baseline
            # par événement, tous issus de la rafale d'attaque en cours.
            shield._last_baseline_sample_time = 0
            shield.record_modification_event()

        # Les comptages enregistrés sont 1, 2, … 50 → μ = 25.5
        assert shield._baseline_stats.n == 50
        assert shield._baseline_stats.mean == pytest.approx(25.5), (
            "comportement observé : les comptages de l'attaque alimentent la baseline"
        )
        assert shield.adaptive_threshold() > 100, (
            "après une seule attaque, le seuil explose et masque les suivantes"
        )

    def test_no_baseline_sample_before_a_full_window_has_elapsed(self):
        shield = RansomwareShield([])
        for _ in range(50):
            shield.record_modification_event()

        assert shield._baseline_stats.n == 0, (
            "un point de baseline par fenêtre écoulée, pas un par événement"
        )


class TestCanaryDeployment:
    def test_canaries_are_created_in_every_protected_folder(self, tmp_path, no_subprocess):
        a = tmp_path / "dossier_a"
        b = tmp_path / "dossier_b"
        a.mkdir()
        b.mkdir()
        shield = RansomwareShield([str(a), str(b)])

        deployed = shield.deploy_canaries()

        assert len(deployed) == 2 * len(CANARY_FILENAMES)
        for folder in (a, b):
            for name in CANARY_FILENAMES:
                assert (folder / name).read_bytes() == CANARY_CONTENT

    def test_nonexistent_folders_are_filtered_at_construction(self, tmp_path, no_subprocess):
        absent = tmp_path / "absent"
        shield = RansomwareShield([str(absent)])

        assert shield.protected_folders == []
        assert shield.deploy_canaries() == []
        assert not absent.exists(), "le bouclier ne doit pas créer le dossier"

    def test_hiding_uses_an_argument_list_never_a_shell(self, protected, no_subprocess):
        shield = RansomwareShield([str(protected)])
        shield.deploy_canaries()

        assert no_subprocess.call_count == len(CANARY_FILENAMES)
        for call_obj in no_subprocess.call_args_list:
            args = call_obj.args[0]
            assert isinstance(args, list), "pas de chaîne shell — pas d'injection possible"
            assert args[:2] == ["attrib", "+h"]
            assert call_obj.kwargs.get("shell") in (None, False)

    def test_a_failing_attrib_does_not_prevent_deployment(self, protected):
        with patch.object(
            ransomware_shield.subprocess, "run", side_effect=OSError("attrib introuvable")
        ):
            shield = RansomwareShield([str(protected)])
            deployed = shield.deploy_canaries()

        assert len(deployed) == len(CANARY_FILENAMES)
        assert (protected / CANARY_FILENAMES[0]).exists()

    def test_an_existing_canary_is_not_overwritten(self, protected, no_subprocess):
        """Si le fichier existe déjà, son contenu est laissé tel quel —
        sinon un redéploiement effacerait la preuve d'un chiffrement."""
        canary = protected / CANARY_FILENAMES[0]
        canary.write_bytes(b"DEJA-CHIFFRE-PAR-UN-RANSOMWARE")

        shield = RansomwareShield([str(protected)])
        shield.deploy_canaries()

        assert canary.read_bytes() == b"DEJA-CHIFFRE-PAR-UN-RANSOMWARE"

    def test_unwritable_folder_is_skipped_without_raising(self, protected):
        shield = RansomwareShield([str(protected)])
        with patch.object(Path, "write_bytes", side_effect=PermissionError("refusé")):
            assert shield.deploy_canaries() == []

    def test_redeploying_duplicates_the_tracking_list(self, protected, no_subprocess):
        """BUG RÉEL (mineur mais réel) : deploy_canaries() ajoute le chemin
        à self.canary_paths même quand le fichier existait déjà. Deux appels
        successifs (ex: réactivation du Mode Gardien) font donc doubler la
        liste — check_canaries() remonte alors des alertes en DOUBLE pour un
        seul et même fichier, et remove_canaries() itère deux fois dessus.
        Un `if canary_path not in self.canary_paths` corrigerait le
        symptôme."""
        shield = RansomwareShield([str(protected)])
        shield.deploy_canaries()
        shield.deploy_canaries()

        assert len(shield.canary_paths) == 2 * len(CANARY_FILENAMES)

        (protected / CANARY_FILENAMES[0]).unlink()
        alerts = shield.check_canaries()
        assert len(alerts) == 2, "comportement observé : la même alerte est remontée deux fois"


class TestCanaryDetection:
    def test_untouched_canaries_raise_no_alert(self, protected, no_subprocess):
        shield = RansomwareShield([str(protected)])
        shield.deploy_canaries()

        assert shield.check_canaries() == []

    def test_a_deleted_canary_is_a_critical_alert(self, protected, no_subprocess):
        shield = RansomwareShield([str(protected)])
        shield.deploy_canaries()
        (protected / CANARY_FILENAMES[1]).unlink()

        alerts = shield.check_canaries()

        assert len(alerts) == 1
        assert alerts[0]["status"] == "SUPPRIMÉ"
        assert alerts[0]["severity"] == "critical"

    def test_an_encrypted_canary_is_a_critical_alert(self, protected, no_subprocess):
        shield = RansomwareShield([str(protected)])
        shield.deploy_canaries()
        (protected / CANARY_FILENAMES[0]).write_bytes(b"\x9f\x3a CHIFFRE \x00")

        alerts = shield.check_canaries()

        assert [a["severity"] for a in alerts] == ["critical"]
        assert "chiffré" in alerts[0]["status"]

    def test_a_locked_canary_is_a_high_alert(self, protected, no_subprocess):
        shield = RansomwareShield([str(protected)])
        shield.deploy_canaries()

        with patch.object(Path, "read_bytes", side_effect=PermissionError("verrouillé")):
            alerts = shield.check_canaries()

        assert len(alerts) == len(CANARY_FILENAMES)
        assert {a["severity"] for a in alerts} == {"high"}

    def test_mass_encryption_flags_every_canary(self, protected, no_subprocess):
        shield = RansomwareShield([str(protected)])
        shield.deploy_canaries()
        for name in CANARY_FILENAMES:
            (protected / name).write_bytes(b"CHIFFRE")

        assert len(shield.check_canaries()) == len(CANARY_FILENAMES)

    def test_canary_names_use_extensions_the_triage_never_touches(self):
        """Cohérence inter-modules : les canaris portent des extensions
        (.docx/.xlsx/.jpg) qui figurent dans NEVER_TOUCH_EXTENSIONS de
        file_triage — l'optimiseur ne peut donc pas ranger ni supprimer
        les canaris du bouclier."""
        file_triage = pytest.importorskip("optimizer.file_triage")
        for name in CANARY_FILENAMES:
            assert Path(name).suffix.lower() in file_triage.NEVER_TOUCH_EXTENSIONS

    def test_removing_canaries_cleans_up_and_clears_the_list(self, protected, no_subprocess):
        shield = RansomwareShield([str(protected)])
        shield.deploy_canaries()

        shield.remove_canaries()

        assert shield.canary_paths == []
        assert list(protected.iterdir()) == []

    def test_removing_canaries_ignores_already_missing_files(self, protected, no_subprocess):
        shield = RansomwareShield([str(protected)])
        shield.deploy_canaries()
        (protected / CANARY_FILENAMES[0]).unlink()

        shield.remove_canaries()  # ne doit pas lever
        assert shield.canary_paths == []

    def test_removing_canaries_never_touches_user_files(self, protected, no_subprocess):
        user_file = protected / "rapport_important.docx"
        user_file.write_bytes(b"TRAVAIL DE L UTILISATEUR")
        shield = RansomwareShield([str(protected)])
        shield.deploy_canaries()

        shield.remove_canaries()

        assert user_file.read_bytes() == b"TRAVAIL DE L UTILISATEUR"


class TestAutomaticResponseIsMockedNeverReal:
    def test_no_psutil_means_no_process_actions(self):
        with patch.object(ransomware_shield, "PSUTIL_AVAILABLE", False):
            assert RansomwareShield.find_suspicious_processes() == []
            assert RansomwareShield.suspend_process(1234) is False
            assert RansomwareShield.resume_process(1234) is False

    def test_top_writers_are_returned_sorted(self):
        def proc(pid, name, write_bytes):
            p = MagicMock()
            p.info = {"pid": pid, "name": name, "io_counters": MagicMock(write_bytes=write_bytes)}
            return p

        fake_psutil = MagicMock()
        fake_psutil.process_iter.return_value = [
            proc(1, "calme.exe", 10),
            proc(2, "ransomware.exe", 10_000_000),
            proc(3, "moyen.exe", 5_000),
        ]

        with patch.object(ransomware_shield, "PSUTIL_AVAILABLE", True), \
             patch.object(ransomware_shield, "psutil", fake_psutil):
            top = RansomwareShield.find_suspicious_processes(top_n=2)

        assert [p["name"] for p in top] == ["ransomware.exe", "moyen.exe"]

    def test_processes_without_io_counters_are_skipped(self):
        p = MagicMock()
        p.info = {"pid": 1, "name": "sans_io.exe", "io_counters": None}
        fake_psutil = MagicMock()
        fake_psutil.process_iter.return_value = [p]

        with patch.object(ransomware_shield, "PSUTIL_AVAILABLE", True), \
             patch.object(ransomware_shield, "psutil", fake_psutil):
            assert RansomwareShield.find_suspicious_processes() == []

    def test_suspend_and_resume_never_kill(self):
        """Le module suspend — il ne tue jamais : un kill perdrait le
        travail en cours d'un processus légitime mal identifié."""
        fake_process = MagicMock()
        fake_psutil = MagicMock()
        fake_psutil.Process.return_value = fake_process
        fake_psutil.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
        fake_psutil.AccessDenied = type("AccessDenied", (Exception,), {})

        with patch.object(ransomware_shield, "PSUTIL_AVAILABLE", True), \
             patch.object(ransomware_shield, "psutil", fake_psutil):
            assert RansomwareShield.suspend_process(4242) is True
            assert RansomwareShield.resume_process(4242) is True

        fake_process.suspend.assert_called_once()
        fake_process.resume.assert_called_once()
        fake_process.kill.assert_not_called()
        fake_process.terminate.assert_not_called()

    def test_a_vanished_process_is_reported_not_raised(self):
        class NoSuchProcess(Exception):
            pass

        fake_psutil = MagicMock()
        fake_psutil.NoSuchProcess = NoSuchProcess
        fake_psutil.AccessDenied = type("AccessDenied", (Exception,), {})
        fake_psutil.Process.side_effect = NoSuchProcess()

        with patch.object(ransomware_shield, "PSUTIL_AVAILABLE", True), \
             patch.object(ransomware_shield, "psutil", fake_psutil):
            assert RansomwareShield.suspend_process(99999) is False
            assert RansomwareShield.resume_process(99999) is False

    def test_lock_and_unlock_use_argument_lists_never_a_shell(self, no_subprocess):
        chemin = r"C:\Users\Julz\Mes Documents (2024)"

        assert RansomwareShield.lock_folder_readonly(chemin) is True
        assert RansomwareShield.unlock_folder(chemin) is True

        lock_call, unlock_call = no_subprocess.call_args_list
        assert lock_call.args[0] == ["attrib", "+r", f"{chemin}\\*.*", "/s"]
        assert unlock_call.args[0] == ["attrib", "-r", f"{chemin}\\*.*", "/s"]
        for c in (lock_call, unlock_call):
            assert c.kwargs.get("shell") in (None, False)
            assert c.kwargs.get("timeout") == 30

    def test_lock_is_reversible_by_unlock(self, no_subprocess):
        """Symétrie stricte : `attrib +r` doit avoir exactement `attrib -r`
        comme inverse, mêmes cible et récursivité."""
        RansomwareShield.lock_folder_readonly("C:/x")
        RansomwareShield.unlock_folder("C:/x")

        lock_args, unlock_args = (c.args[0] for c in no_subprocess.call_args_list)
        assert lock_args[0] == unlock_args[0] == "attrib"
        assert lock_args[1] == "+r" and unlock_args[1] == "-r"
        assert lock_args[2:] == unlock_args[2:]

    def test_lock_failure_is_reported_not_raised(self):
        with patch.object(
            ransomware_shield.subprocess, "run", side_effect=OSError("attrib introuvable")
        ):
            assert RansomwareShield.lock_folder_readonly("C:/x") is False
            assert RansomwareShield.unlock_folder("C:/x") is False

    def test_a_nonzero_attrib_return_code_is_a_failure(self, no_subprocess):
        no_subprocess.return_value = MagicMock(returncode=1)
        assert RansomwareShield.lock_folder_readonly("C:/x") is False
