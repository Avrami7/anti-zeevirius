"""
temp_cleaner.py
Nettoyage des fichiers temporaires Windows, caches navigateurs, cache
Windows Update, prefetch et corbeille.

Bonne pratique appliquée : chaque suppression est encapsulée dans un
try/except individuel. Un fichier verrouillé (en cours d'utilisation)
est simplement ignoré et comptabilisé — jamais bloquant pour le reste
du nettoyage (même logique que le problème "Accès refusé" rencontré
dans l'Explorateur Windows).

Nécessite des droits administrateur pour un nettoyage complet
(cache Windows Update notamment). Fonctionne en mode utilisateur
standard pour Temp/%temp%/corbeille/caches navigateurs.
"""

import ctypes
import os
import shutil
from pathlib import Path
from typing import Dict, List


class TempCleaner:
    def __init__(self):
        self.results: Dict[str, Dict] = {}

    # ── Utilitaires ──────────────────────────────────────────────
    @staticmethod
    def _is_admin() -> bool:
        try:
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False

    @staticmethod
    def _dir_size(path: Path) -> int:
        total = 0
        for f in path.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
            except (PermissionError, OSError):
                continue
        return total

    def _clean_directory(self, dir_path: str, label: str) -> Dict:
        """Supprime le CONTENU d'un dossier (pas le dossier lui-même),
        fichier par fichier et sous-dossier par sous-dossier, en
        ignorant silencieusement ce qui est verrouillé/protégé."""
        path = Path(dir_path)
        freed_bytes = 0
        deleted_count = 0
        skipped_count = 0

        if not path.exists():
            return {"label": label, "path": dir_path, "status": "absent", "freed_mb": 0}

        before_size = self._dir_size(path)

        for entry in path.iterdir():
            try:
                if entry.is_file() or entry.is_symlink():
                    size = entry.stat().st_size
                    entry.unlink()
                    freed_bytes += size
                    deleted_count += 1
                elif entry.is_dir():
                    size = self._dir_size(entry)
                    shutil.rmtree(entry, ignore_errors=False)
                    freed_bytes += size
                    deleted_count += 1
            except (PermissionError, OSError):
                skipped_count += 1
                continue

        return {
            "label": label,
            "path": dir_path,
            "status": "ok",
            "freed_mb": round(freed_bytes / (1024 * 1024), 2),
            "deleted_items": deleted_count,
            "skipped_items": skipped_count,
        }

    # ── Cibles de nettoyage ──────────────────────────────────────
    def clean_windows_temp(self) -> Dict:
        """C:\\Windows\\Temp — nécessite généralement les droits admin."""
        windir = os.environ.get("WINDIR", "C:\\Windows")
        return self._clean_directory(os.path.join(windir, "Temp"), "Windows Temp")

    def clean_user_temp(self) -> Dict:
        """%TEMP% utilisateur — C:\\Users\\<user>\\AppData\\Local\\Temp."""
        user_temp = os.environ.get("TEMP") or os.environ.get("TMP")
        if not user_temp:
            user_temp = str(Path.home() / "AppData" / "Local" / "Temp")
        return self._clean_directory(user_temp, "%TEMP% utilisateur")

    def clean_prefetch(self) -> Dict:
        """Cache de préchargement Windows — nécessite les droits admin.
        Windows le régénère automatiquement, le vider force une
        reconstruction propre (peut ralentir très légèrement le 1er
        démarrage suivant, puis redevient normal)."""
        windir = os.environ.get("WINDIR", "C:\\Windows")
        return self._clean_directory(os.path.join(windir, "Prefetch"), "Prefetch")

    def clean_windows_update_cache(self) -> Dict:
        """Cache des téléchargements Windows Update — nécessite les
        droits admin. Sans risque : Windows retélécharge si besoin."""
        windir = os.environ.get("WINDIR", "C:\\Windows")
        return self._clean_directory(
            os.path.join(windir, "SoftwareDistribution", "Download"),
            "Cache Windows Update",
        )

    def clean_recycle_bin(self) -> Dict:
        """Vide la corbeille via l'API Windows native (SHEmptyRecycleBin)."""
        try:
            # SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
            flags = 0x00000001 | 0x00000002 | 0x00000004
            result = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)
            # result == 0 (S_OK) ou -2147418113 si corbeille déjà vide (ignorable)
            success = result in (0, -2147418113)
            return {
                "label": "Corbeille",
                "path": "Corbeille Windows",
                "status": "ok" if success else "erreur",
                "freed_mb": "inconnu (API ne retourne pas la taille)",
            }
        except Exception as e:
            return {"label": "Corbeille", "path": "Corbeille Windows", "status": f"erreur: {e}", "freed_mb": 0}

    def clean_browser_caches(self) -> List[Dict]:
        """Cache des navigateurs les plus courants (Chrome, Edge, Firefox).
        Ne supprime QUE le cache, jamais l'historique, les mots de passe
        ou les favoris."""
        home = Path.home()
        targets = [
            (home / "AppData/Local/Google/Chrome/User Data/Default/Cache", "Cache Chrome"),
            (home / "AppData/Local/Microsoft/Edge/User Data/Default/Cache", "Cache Edge"),
            (home / "AppData/Local/Mozilla/Firefox/Profiles", "Cache Firefox (profils)"),
        ]
        results = []
        for path, label in targets:
            if path.exists():
                results.append(self._clean_directory(str(path), label))
        return results

    def clean_thumbnail_cache(self) -> Dict:
        """Cache des miniatures Windows Explorer — régénéré automatiquement."""
        path = Path.home() / "AppData/Local/Microsoft/Windows/Explorer"
        freed = 0
        deleted = 0
        if path.exists():
            for f in path.glob("thumbcache_*.db"):
                try:
                    freed += f.stat().st_size
                    f.unlink()
                    deleted += 1
                except (PermissionError, OSError):
                    continue
        return {
            "label": "Cache miniatures",
            "path": str(path),
            "status": "ok",
            "freed_mb": round(freed / (1024 * 1024), 2),
            "deleted_items": deleted,
        }

    def run_full_cleanup(self, include_admin_targets: bool = True) -> Dict:
        """Lance le nettoyage complet et retourne un rapport consolidé."""
        is_admin = self._is_admin()
        report = {"is_admin": is_admin, "results": []}

        report["results"].append(self.clean_user_temp())
        report["results"].append(self.clean_recycle_bin())
        report["results"].append(self.clean_thumbnail_cache())
        report["results"].extend(self.clean_browser_caches())

        if include_admin_targets:
            if is_admin:
                report["results"].append(self.clean_windows_temp())
                report["results"].append(self.clean_prefetch())
                report["results"].append(self.clean_windows_update_cache())
            else:
                report["results"].append({
                    "label": "Windows Temp / Prefetch / Update Cache",
                    "status": "ignoré — droits administrateur requis",
                    "freed_mb": 0,
                })

        total_freed = sum(
            r["freed_mb"] for r in report["results"]
            if isinstance(r.get("freed_mb"), (int, float))
        )
        report["total_freed_mb"] = round(total_freed, 2)
        return report
