"""
disk_analyzer.py
Identifie les plus gros fichiers/dossiers et les doublons pour cibler
un nettoyage manuel efficace (l'utilisateur garde la main sur la
suppression finale — jamais d'auto-suppression aveugle ici, contrairement
au temp_cleaner qui ne cible que des caches sans valeur).
"""

import hashlib
import heapq
import os
from pathlib import Path
from typing import Dict, List, Tuple


class DiskAnalyzer:

    @staticmethod
    def find_largest_files(root_path: str, top_n: int = 20) -> List[Dict]:
        """Retourne les N plus gros fichiers d'un dossier (récursif)."""
        root = Path(root_path)
        if not root.exists():
            return []

        files = []
        for f in root.rglob("*"):
            try:
                if f.is_file():
                    files.append({"path": str(f), "size_mb": round(f.stat().st_size / (1024 * 1024), 2)})
            except (PermissionError, OSError):
                continue

        files.sort(key=lambda x: x["size_mb"], reverse=True)
        return files[:top_n]

    @staticmethod
    def find_largest_folders(root_path: str, top_n: int = 15) -> List[Dict]:
        """Retourne les N plus gros sous-dossiers de premier niveau."""
        root = Path(root_path)
        if not root.exists():
            return []

        folder_sizes = []
        for entry in root.iterdir():
            if entry.is_dir():
                total = 0
                try:
                    for f in entry.rglob("*"):
                        if f.is_file():
                            total += f.stat().st_size
                except (PermissionError, OSError):
                    pass
                folder_sizes.append({"path": str(entry), "size_mb": round(total / (1024 * 1024), 2)})

        folder_sizes.sort(key=lambda x: x["size_mb"], reverse=True)
        return folder_sizes[:top_n]

    @staticmethod
    def find_duplicate_files(root_path: str, min_size_mb: float = 1.0) -> List[Dict]:
        """
        Détecte les fichiers en double par hash (regroupe les fichiers
        identiques). Ne compare que les fichiers > min_size_mb pour
        éviter de scanner des milliers de petits fichiers système.
        """
        root = Path(root_path)
        if not root.exists():
            return []

        size_groups: Dict[int, List[Path]] = {}
        min_bytes = int(min_size_mb * 1024 * 1024)

        # 1. Regrouper par taille (évite de hasher tous les fichiers inutilement)
        for f in root.rglob("*"):
            try:
                if f.is_file():
                    size = f.stat().st_size
                    if size >= min_bytes:
                        size_groups.setdefault(size, []).append(f)
            except (PermissionError, OSError):
                continue

        # 2. Hasher uniquement les groupes ayant plus d'un fichier de même taille
        duplicates = []
        for size, files in size_groups.items():
            if len(files) < 2:
                continue

            hash_groups: Dict[str, List[str]] = {}
            for f in files:
                file_hash = DiskAnalyzer._quick_hash(f)
                if file_hash:
                    hash_groups.setdefault(file_hash, []).append(str(f))

            for file_hash, paths in hash_groups.items():
                if len(paths) > 1:
                    duplicates.append({
                        "hash": file_hash,
                        "size_mb": round(size / (1024 * 1024), 2),
                        "count": len(paths),
                        "wasted_mb": round((size * (len(paths) - 1)) / (1024 * 1024), 2),
                        "paths": paths,
                    })

        duplicates.sort(key=lambda x: x["wasted_mb"], reverse=True)
        return duplicates

    @staticmethod
    def analyze_disk(
        root_path: str,
        top_n_files: int = 20,
        top_n_folders: int = 15,
        min_dup_size_mb: float = 1.0,
    ) -> Dict:
        """
        Version unifiée : remplace find_largest_files() + find_largest_folders()
        + find_duplicate_files() par UN SEUL parcours d'arborescence.

        Gain algorithmique :
        - Avant : 3 appels indépendants → chacun fait un root.rglob("*") complet,
          donc 3× le nombre de syscalls stat()/readdir() sur le MÊME arbre de
          fichiers. Sur un disque de N fichiers, coût I/O total ≈ 3·O(N).
        - Après : un seul os.walk() (implémenté en interne via os.scandir, qui
          réutilise le DirEntry retourné par readdir au lieu de refaire un
          stat() séparé pour chaque fichier) → coût I/O total ≈ O(N).
        - Top-N fichiers : au lieu de trier TOUS les fichiers (O(N log N))
          puis garder les top_n, on maintient un tas-min borné de taille
          top_n_files → complexité O(N log k) avec k = top_n_files (20),
          ce qui est très inférieur à O(N log N) dès que N >> k (cas courant
          sur un disque de plusieurs centaines de milliers de fichiers).

        Retourne {"largest_files": [...], "largest_folders": [...], "duplicates": [...]}.
        """
        root = Path(root_path)
        result = {"largest_files": [], "largest_folders": [], "duplicates": []}
        if not root.exists():
            return result

        min_dup_bytes = int(min_dup_size_mb * 1024 * 1024)
        folder_sizes: Dict[str, int] = {}
        size_groups: Dict[int, List[str]] = {}
        # Tas-min borné : le plus petit élément (heap[0]) est éjecté dès que
        # la taille dépasse top_n_files, garantissant O(log k) par insertion.
        files_heap: List[Tuple[int, str]] = []

        def _on_walk_error(err: OSError) -> None:
            pass  # dossier illisible (permissions) : on l'ignore et on continue

        for dirpath, dirnames, filenames in os.walk(root, onerror=_on_walk_error):
            try:
                rel_parts = Path(dirpath).relative_to(root).parts
            except ValueError:
                rel_parts = ()
            top_folder = rel_parts[0] if rel_parts else None

            for fname in filenames:
                fpath = os.path.join(dirpath, fname)
                try:
                    size = os.stat(fpath).st_size
                except OSError:
                    continue

                if top_folder is not None:
                    folder_sizes[top_folder] = folder_sizes.get(top_folder, 0) + size

                if len(files_heap) < top_n_files:
                    heapq.heappush(files_heap, (size, fpath))
                elif size > files_heap[0][0]:
                    heapq.heapreplace(files_heap, (size, fpath))

                if size >= min_dup_bytes:
                    size_groups.setdefault(size, []).append(fpath)

        result["largest_files"] = [
            {"path": p, "size_mb": round(s / (1024 * 1024), 2)}
            for s, p in sorted(files_heap, key=lambda x: x[0], reverse=True)
        ]

        result["largest_folders"] = sorted(
            (
                {"path": str(root / name), "size_mb": round(size / (1024 * 1024), 2)}
                for name, size in folder_sizes.items()
            ),
            key=lambda x: x["size_mb"],
            reverse=True,
        )[:top_n_folders]

        duplicates = []
        for size, paths in size_groups.items():
            if len(paths) < 2:
                continue
            hash_groups: Dict[str, List[str]] = {}
            for fp in paths:
                file_hash = DiskAnalyzer._quick_hash(Path(fp))
                if file_hash:
                    hash_groups.setdefault(file_hash, []).append(fp)
            for file_hash, dup_paths in hash_groups.items():
                if len(dup_paths) > 1:
                    duplicates.append({
                        "hash": file_hash,
                        "size_mb": round(size / (1024 * 1024), 2),
                        "count": len(dup_paths),
                        "wasted_mb": round((size * (len(dup_paths) - 1)) / (1024 * 1024), 2),
                        "paths": dup_paths,
                    })
        duplicates.sort(key=lambda x: x["wasted_mb"], reverse=True)
        result["duplicates"] = duplicates

        return result

    @staticmethod
    def _quick_hash(file_path: Path, sample_size: int = 1024 * 1024) -> str:
        """Hash rapide basé sur le début + la fin du fichier (suffisant
        pour détecter des doublons sans lire des fichiers volumineux
        en entier — compromis vitesse/précision)."""
        try:
            hasher = hashlib.md5()
            size = file_path.stat().st_size
            with open(file_path, "rb") as f:
                hasher.update(f.read(sample_size))
                if size > sample_size * 2:
                    f.seek(-sample_size, 2)
                    hasher.update(f.read(sample_size))
            return hasher.hexdigest()
        except (PermissionError, OSError):
            return ""
