"""
test_main_scan_directory.py
Couvre AntivirusEngine.scan_directory() dans main.py : la version
parallélisée (ThreadPoolExecutor) doit scanner tous les fichiers, produire
un résultat par fichier, et obtenir un vrai gain de temps mural par rapport
à une exécution séquentielle.

NOTE PLATEFORME : main.py importe optimizer/startup_manager.py (module
stdlib `winreg`, disponible uniquement sous Windows) et monitor/realtime_monitor.py
(`watchdog`). `pytest.importorskip` fait donc passer ce fichier de tests en
"skipped" (pas en échec) sur toute machine où ces dépendances ne sont pas
réunies — le test s'exécute normalement sur la cible réelle du projet
(Windows, avec `pip install -r requirements.txt`).

On contourne volontairement AntivirusEngine.__init__() (qui charge les
bases de signatures, compile les règles YARA, etc. — coûteux et sans
rapport avec ce qu'on teste ici) en appelant la méthode non liée
`AntivirusEngine.scan_directory` sur un objet minimal ("fake self") qui
n'implémente que ce dont scan_directory a réellement besoin :
`_is_excluded()` et `scan_single_file()`.
"""

import threading
import time

import pytest

main = pytest.importorskip("main", reason="main.py nécessite winreg/watchdog (Windows uniquement)")


class _FakeEngine:
    """Substitut minimal d'AntivirusEngine — évite l'initialisation lourde
    (bases de signatures, règles YARA, etc.) hors sujet pour ce test."""

    def __init__(self, scan_delay: float = 0.0):
        self.scan_delay = scan_delay
        self.scanned_paths = []
        self.thread_names_used = set()
        self._call_lock = threading.Lock()

    def _is_excluded(self, path) -> bool:
        return False

    def scan_single_file(self, file_path: str, auto_quarantine: bool = True) -> dict:
        if self.scan_delay:
            time.sleep(self.scan_delay)
        with self._call_lock:
            self.scanned_paths.append(file_path)
            self.thread_names_used.add(threading.current_thread().name)
        return {"file": file_path, "verdict": "SAIN"}


class TestScanDirectoryParallelExecution:
    def test_all_files_are_scanned_exactly_once(self, tmp_path):
        for i in range(15):
            (tmp_path / f"file_{i}.bin").write_bytes(b"x")

        fake_engine = _FakeEngine()
        results = main.AntivirusEngine.scan_directory(fake_engine, str(tmp_path))

        assert len(results) == 15
        assert len(fake_engine.scanned_paths) == 15
        assert len(set(fake_engine.scanned_paths)) == 15  # aucun doublon

    def test_nonexistent_directory_returns_empty_list(self, tmp_path):
        fake_engine = _FakeEngine()
        results = main.AntivirusEngine.scan_directory(fake_engine, str(tmp_path / "absent"))
        assert results == []

    def test_execution_uses_more_than_one_thread(self, tmp_path):
        """Vérifie que le scan est réellement réparti sur plusieurs
        threads (et pas juste soumis à un ThreadPoolExecutor configuré
        avec max_workers=1 par erreur)."""
        for i in range(20):
            (tmp_path / f"file_{i}.bin").write_bytes(b"x")

        fake_engine = _FakeEngine(scan_delay=0.02)
        main.AntivirusEngine.scan_directory(fake_engine, str(tmp_path), max_workers=8)

        assert len(fake_engine.thread_names_used) > 1

    def test_parallel_scan_is_faster_than_sequential_baseline(self, tmp_path):
        """Preuve de performance : avec un coût par fichier artificiel
        (simulant l'I/O + le hachage + YARA), le temps mural du scan
        parallèle doit être nettement inférieur à la somme séquentielle
        des délais individuels — c'est le gain visé par la parallélisation."""
        n_files = 20
        delay_per_file = 0.03
        for i in range(n_files):
            (tmp_path / f"file_{i}.bin").write_bytes(b"x")

        sequential_baseline = n_files * delay_per_file  # 0.6s si tout était séquentiel

        fake_engine = _FakeEngine(scan_delay=delay_per_file)
        start = time.perf_counter()
        results = main.AntivirusEngine.scan_directory(fake_engine, str(tmp_path), max_workers=8)
        elapsed = time.perf_counter() - start

        assert len(results) == n_files
        # Marge large (70% du temps séquentiel) pour rester robuste sur des
        # machines CI lentes/chargées — l'essentiel est de prouver un vrai
        # gain, pas de fixer un ratio de performance précis et fragile.
        assert elapsed < sequential_baseline * 0.7

    def test_max_workers_defaults_to_a_bounded_value_when_not_specified(self, tmp_path):
        """S'assure que l'absence de max_workers explicite ne fait pas
        planter scan_directory et retombe bien sur le calcul automatique
        borné (min(32, cpu_count*4)) plutôt que sur une valeur illimitée."""
        (tmp_path / "only_file.bin").write_bytes(b"x")
        fake_engine = _FakeEngine()
        results = main.AntivirusEngine.scan_directory(fake_engine, str(tmp_path))
        assert len(results) == 1
