"""
test_disk_analyzer.py
Couvre optimizer/disk_analyzer.py, en particulier analyze_disk() (le
parcours unifié qui remplace 3 rglob() séparés par un seul os.walk()).

Utilise la fixture pytest `tmp_path` : chaque test travaille dans un
répertoire temporaire isolé, automatiquement nettoyé par pytest.
"""

from pathlib import Path

import pytest

from optimizer.disk_analyzer import DiskAnalyzer


def _write_file(path: Path, size_bytes: int, byte: bytes = b"X") -> None:
    """Crée un fichier de taille exacte `size_bytes`, rempli du motif `byte`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(byte * size_bytes)


class TestAnalyzeDiskEmptyOrMissing:
    def test_nonexistent_root_returns_empty_result(self, tmp_path):
        result = DiskAnalyzer.analyze_disk(str(tmp_path / "does_not_exist"))
        assert result == {"largest_files": [], "largest_folders": [], "duplicates": []}

    def test_empty_directory_returns_empty_lists(self, tmp_path):
        result = DiskAnalyzer.analyze_disk(str(tmp_path))
        assert result["largest_files"] == []
        assert result["largest_folders"] == []
        assert result["duplicates"] == []


class TestLargestFiles:
    def test_top_n_ranked_by_size_descending(self, tmp_path):
        _write_file(tmp_path / "a.bin", 5_000_000)
        _write_file(tmp_path / "b.bin", 1_000_000)
        _write_file(tmp_path / "c.bin", 3_000_000)

        result = DiskAnalyzer.analyze_disk(str(tmp_path), top_n_files=2)

        names = [Path(f["path"]).name for f in result["largest_files"]]
        assert names == ["a.bin", "c.bin"]  # top 2 seulement, ordre décroissant

    def test_heap_based_top_n_matches_full_sort_on_larger_set(self, tmp_path):
        """Vérifie que le tas borné (heapq) donne exactement le même top-N
        qu'un tri complet classique — garantit que l'optimisation de
        complexité (O(N log k) au lieu de O(N log N)) n'a pas introduit
        d'erreur de sélection."""
        sizes = [7, 2, 9, 1, 5, 8, 3, 6, 4, 10]  # en Mo, ordre volontairement mélangé
        for i, size_mb in enumerate(sizes):
            _write_file(tmp_path / f"file_{i}.bin", size_mb * 1_000_000)

        result = DiskAnalyzer.analyze_disk(str(tmp_path), top_n_files=3)
        top_sizes_via_heap = sorted([f["size_mb"] for f in result["largest_files"]], reverse=True)

        all_files_sorted = sorted(sizes, reverse=True)
        expected_top_3_mb = all_files_sorted[:3]

        # Comparaison approximative (arrondi Mo binaire vs Mo décimal du test)
        assert len(top_sizes_via_heap) == 3
        for measured, expected in zip(top_sizes_via_heap, expected_top_3_mb):
            assert measured == pytest.approx(expected * 1_000_000 / (1024 * 1024), abs=0.1)


class TestLargestFolders:
    def test_root_level_file_not_counted_as_a_folder(self, tmp_path):
        _write_file(tmp_path / "root_file.bin", 2_000_000)
        _write_file(tmp_path / "folderA" / "file.bin", 1_000_000)

        result = DiskAnalyzer.analyze_disk(str(tmp_path))
        folder_names = {Path(f["path"]).name for f in result["largest_folders"]}
        file_names = {Path(f["path"]).name for f in result["largest_files"]}

        assert "root_file.bin" not in folder_names
        assert "root_file.bin" in file_names
        assert "folderA" in folder_names

    def test_folder_size_aggregates_nested_subfolders(self, tmp_path):
        _write_file(tmp_path / "folderA" / "big.bin", 5_000_000)
        _write_file(tmp_path / "folderA" / "sub" / "small.bin", 1_000_000)
        _write_file(tmp_path / "folderB" / "medium.bin", 2_000_000)

        result = DiskAnalyzer.analyze_disk(str(tmp_path))
        sizes = {Path(f["path"]).name: f["size_mb"] for f in result["largest_folders"]}

        expected_folder_a = round(6_000_000 / (1024 * 1024), 2)  # big.bin + sub/small.bin
        expected_folder_b = round(2_000_000 / (1024 * 1024), 2)

        assert sizes["folderA"] == pytest.approx(expected_folder_a, abs=0.01)
        assert sizes["folderB"] == pytest.approx(expected_folder_b, abs=0.01)
        assert sizes["folderA"] > sizes["folderB"]


class TestDuplicateDetection:
    def test_identical_content_across_folders_is_detected(self, tmp_path):
        content = b"Y" * 2_000_000
        (tmp_path / "folderA").mkdir()
        (tmp_path / "folderB").mkdir()
        (tmp_path / "folderA" / "dup1.bin").write_bytes(content)
        (tmp_path / "folderB" / "dup2.bin").write_bytes(content)
        (tmp_path / "folderB" / "unique.bin").write_bytes(b"Z" * 2_000_000)

        result = DiskAnalyzer.analyze_disk(str(tmp_path), min_dup_size_mb=1.0)

        assert len(result["duplicates"]) == 1
        assert result["duplicates"][0]["count"] == 2
        assert sorted(Path(p).name for p in result["duplicates"][0]["paths"]) == ["dup1.bin", "dup2.bin"]

    def test_same_size_different_content_is_not_a_false_positive(self, tmp_path):
        """Deux fichiers de MÊME taille mais de contenu différent ne
        doivent PAS être signalés comme doublons (le groupement par taille
        n'est qu'une pré-sélection, le hash tranche ensuite)."""
        (tmp_path / "a.bin").write_bytes(b"A" * 2_000_000)
        (tmp_path / "b.bin").write_bytes(b"B" * 2_000_000)

        result = DiskAnalyzer.analyze_disk(str(tmp_path), min_dup_size_mb=1.0)
        assert result["duplicates"] == []

    def test_files_below_min_dup_size_are_ignored(self, tmp_path):
        content = b"Y" * 500_000  # 0.5 Mo, sous le seuil par défaut de 1 Mo
        (tmp_path / "dup1.bin").write_bytes(content)
        (tmp_path / "dup2.bin").write_bytes(content)

        result = DiskAnalyzer.analyze_disk(str(tmp_path), min_dup_size_mb=1.0)
        assert result["duplicates"] == []

    def test_wasted_space_computed_as_n_minus_one_copies(self, tmp_path):
        content = b"Y" * 2_000_000
        for i in range(3):  # 3 copies identiques
            (tmp_path / f"dup{i}.bin").write_bytes(content)

        result = DiskAnalyzer.analyze_disk(str(tmp_path), min_dup_size_mb=1.0)
        dup = result["duplicates"][0]
        assert dup["count"] == 3
        expected_wasted = round((2_000_000 * 2) / (1024 * 1024), 2)  # (3-1) copies gaspillées
        assert dup["wasted_mb"] == pytest.approx(expected_wasted, abs=0.01)


class TestConsistencyWithLegacyMethods:
    def test_unified_pass_matches_the_three_separate_legacy_calls(self, tmp_path):
        """Garantie de non-régression : la fusion des 3 parcours en un seul
        (analyze_disk) doit produire EXACTEMENT le même résultat que les 3
        méthodes historiques appelées séparément (find_largest_files,
        find_largest_folders, find_duplicate_files), qui restent
        disponibles pour les appelants qui n'ont besoin que d'une seule
        de ces analyses (ex: optimizer/guardian.py)."""
        _write_file(tmp_path / "folderA" / "big.bin", 5_000_000)
        _write_file(tmp_path / "folderB" / "dup1.bin", 2_000_000, byte=b"Y")
        _write_file(tmp_path / "folderB" / "dup2_copy.bin", 2_000_000, byte=b"Y")

        unified = DiskAnalyzer.analyze_disk(str(tmp_path))
        legacy_files = DiskAnalyzer.find_largest_files(str(tmp_path))
        legacy_folders = DiskAnalyzer.find_largest_folders(str(tmp_path))
        legacy_duplicates = DiskAnalyzer.find_duplicate_files(str(tmp_path))

        assert {f["path"] for f in unified["largest_files"]} == {f["path"] for f in legacy_files}
        assert {f["path"]: f["size_mb"] for f in unified["largest_folders"]} == \
               {f["path"]: f["size_mb"] for f in legacy_folders}
        assert len(unified["duplicates"]) == len(legacy_duplicates)
        assert {d["hash"] for d in unified["duplicates"]} == {d["hash"] for d in legacy_duplicates}
