"""
residue_cleaner.py
Nettoyage des résidus laissés par des applications désinstallées
(manuellement, ou avant l'utilisation d'ANTI-ZEEVIRIUS) :

1. Raccourcis orphelins (.lnk dont la cible n'existe plus) — Bureau,
   Menu Démarrer (utilisateur + commun)
2. Entrées de registre "Uninstall" orphelines — le chemin d'installation
   référencé n'existe plus (app supprimée à la main, sans désinstalleur
   propre), la clé traîne pour rien
3. Dossiers orphelins dans Program Files / Program Files (x86) /
   AppData / ProgramData qui ne correspondent à AUCUNE application
   actuellement installée

RÈGLE DE SÉCURITÉ (identique au reste de l'outil) :
- Rien n'est jamais supprimé directement. Raccourcis et dossiers passent
  par le système de mise de côté (staging) de FileTriage — MÊME zone
  tampon, MÊME délai de rétention, MÊME écran de validation avant
  suppression définitive (option 12/22) que pour les autres fichiers
  "sûrs à supprimer". Une seule zone tampon pour tout l'outil, plus
  simple à superviser.
- Les entrées de registre orphelines sont SAUVEGARDÉES (toutes leurs
  valeurs, sous une clé dédiée à ANTI-ZEEVIRIUS) avant suppression —
  restaurables manuellement si jamais besoin.
- La détection de DOSSIERS orphelins est volontairement PRUDENTE :
  proposée seulement si (a) aucune application installée ne correspond
  au nom du dossier (recherche floue nom+éditeur), (b) le dossier n'a
  pas été modifié depuis au moins 30 jours, (c) le nom n'est pas dans la
  liste de protection (dossiers partagés Windows/Microsoft/runtimes/
  données UWP "Packages"...). Contrairement aux raccourcis et entrées de
  registre (sans risque de perte de données réelle), CHAQUE dossier
  candidat doit être validé INDIVIDUELLEMENT avant mise de côté — jamais
  de sélection groupée automatique pour cette catégorie précise.
"""

import os
import time
from pathlib import Path
from typing import Dict, List, Optional

try:
    import winreg
    WINREG_AVAILABLE = True
except ImportError:
    WINREG_AVAILABLE = False

try:
    import win32com.client
    PYWIN32_AVAILABLE = True
except ImportError:
    PYWIN32_AVAILABLE = False


# Dossiers jamais proposés comme "orphelins", même s'ils ne correspondent
# à aucune application installée détectée — composants partagés Windows,
# runtimes, ou données qui ne sont pas des dossiers d'application à
# proprement parler.
PROTECTED_FOLDER_NAMES = {
    "common files", "internet explorer", "windowsapps", "windows nt",
    "windows mail", "windows media player", "windows photo viewer",
    "windows security", "windows defender", "microsoft", "packages",
    "google", "mozilla firefox", "windows sidebar", "uninstall information",
    "installer", "temp", "crashdumps", "connecteddevicesplatform",
    "comms", "elevated diagnostics", "diagnostics", "d3dscache",
}

ORPHAN_MIN_AGE_DAYS = 30  # ne considère que les dossiers non modifiés depuis au moins X jours
REGISTRY_BACKUP_PATH = r"Software\AntiZeevirius\OrphanedUninstallBackup"


class ResidueCleaner:
    def __init__(self, file_triage, app_manager):
        # Réutilise l'infrastructure existante (verrou, écriture atomique,
        # journal, restauration, purge) plutôt que d'en recréer une : une
        # seule zone tampon à superviser pour tout l'outil.
        self.file_triage = file_triage
        self.app_manager = app_manager

    # ── 1. Raccourcis orphelins ───────────────────────────────────
    @staticmethod
    def _shortcut_scan_folders() -> List[Path]:
        home = Path.home()
        appdata = os.environ.get("APPDATA")
        programdata = os.environ.get("PROGRAMDATA")
        folders = [home / "Desktop"]
        if appdata:
            folders.append(Path(appdata) / "Microsoft" / "Windows" / "Start Menu")
        if programdata:
            folders.append(Path(programdata) / "Microsoft" / "Windows" / "Start Menu")
        return [f for f in folders if f.exists()]

    def find_orphaned_shortcuts(self) -> List[Dict]:
        """Raccourcis (.lnk) du Bureau et du Menu Démarrer dont la cible
        n'existe plus sur le disque."""
        if not PYWIN32_AVAILABLE:
            return []
        try:
            shell = win32com.client.Dispatch("WScript.Shell")
        except Exception:
            return []

        orphaned = []
        for folder in self._shortcut_scan_folders():
            for lnk in folder.rglob("*.lnk"):
                try:
                    shortcut = shell.CreateShortCut(str(lnk))
                    target = shortcut.TargetPath
                    if target and not Path(target).exists():
                        orphaned.append({
                            "path": str(lnk),
                            "target": target,
                            "reason": f"Raccourci orphelin — cible introuvable ({target})",
                        })
                except Exception:
                    continue
        return orphaned

    def stage_orphaned_shortcuts(self, shortcuts: Optional[List[Dict]] = None) -> Dict:
        """Met de côté les raccourcis orphelins (réversible, via FileTriage)."""
        shortcuts = shortcuts if shortcuts is not None else self.find_orphaned_shortcuts()
        staged, errors = 0, []
        for s in shortcuts:
            if self.file_triage.move_to_staging(s["path"], s["reason"]):
                staged += 1
            else:
                errors.append(s["path"])
        return {"staged": staged, "errors": errors, "total_candidates": len(shortcuts)}

    # ── 2. Entrées de registre "Uninstall" orphelines ─────────────
    def find_orphaned_uninstall_entries(self) -> List[Dict]:
        """Entrées du registre Uninstall dont le chemin d'installation
        référencé n'existe plus (app supprimée à la main, sans
        désinstalleur propre) — la clé de registre traîne, orpheline."""
        if not WINREG_AVAILABLE:
            return []

        orphaned = []
        hives = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        for hive, path in hives:
            try:
                key = winreg.OpenKey(hive, path)
            except OSError:
                continue
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    subkey_name = winreg.EnumKey(key, i)
                    with winreg.OpenKey(key, subkey_name) as subkey:
                        def _get(name, default=None):
                            try:
                                return winreg.QueryValueEx(subkey, name)[0]
                            except OSError:
                                return default

                        display_name = _get("DisplayName")
                        install_location = _get("InstallLocation")
                        # Pas assez d'info pour juger en toute sécurité -> on ne touche pas.
                        if not display_name or not install_location:
                            continue
                        if Path(install_location).exists():
                            continue  # l'appli existe toujours, rien d'orphelin

                        orphaned.append({
                            "hive_name": "HKLM" if hive == winreg.HKEY_LOCAL_MACHINE else "HKCU",
                            "hive": hive,
                            "parent_path": path,
                            "subkey": subkey_name,
                            "display_name": display_name,
                            "install_location": install_location,
                        })
                except OSError:
                    continue
            key.Close()
        return orphaned

    @staticmethod
    def backup_and_remove_uninstall_entry(entry: Dict) -> Dict:
        """Copie TOUTES les valeurs de la clé orpheline sous
        HKCU\\Software\\AntiZeevirius\\OrphanedUninstallBackup, puis
        supprime l'originale. Restaurable manuellement via la clé de
        sauvegarde (Regedit) si jamais besoin."""
        if not WINREG_AVAILABLE:
            return {"status": "erreur", "message": "winreg indisponible (Windows uniquement)."}
        try:
            full_original_path = f"{entry['parent_path']}\\{entry['subkey']}"
            with winreg.OpenKey(entry["hive"], full_original_path) as src_key:
                values = []
                i = 0
                while True:
                    try:
                        values.append(winreg.EnumValue(src_key, i))
                        i += 1
                    except OSError:
                        break

            backup_full_path = f"{REGISTRY_BACKUP_PATH}\\{entry['hive_name']}_{entry['subkey']}"
            backup_key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, backup_full_path)
            for name, value, value_type in values:
                winreg.SetValueEx(backup_key, name, 0, value_type, value)
            backup_key.Close()

            winreg.DeleteKey(entry["hive"], full_original_path)
            return {"status": "ok", "message": f"'{entry['display_name']}' — entrée orpheline supprimée (sauvegardée dans le registre)."}
        except OSError as e:
            return {"status": "erreur", "message": str(e)}

    # ── 3. Dossiers orphelins (Program Files / AppData / ProgramData) ─
    @staticmethod
    def _scan_root_folders() -> List[Path]:
        candidates = []
        for env_var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
            val = os.environ.get(env_var)
            if val:
                candidates.append(Path(val))
        for env_var in ("LOCALAPPDATA", "APPDATA"):
            val = os.environ.get(env_var)
            if val:
                candidates.append(Path(val))
        return [c for c in candidates if c.exists()]

    def find_candidate_orphaned_folders(self) -> List[Dict]:
        """Dossiers de premier niveau dans Program Files/AppData/ProgramData
        qui ne correspondent (recherche floue) à AUCUNE application
        installée, non modifiés depuis au moins ORPHAN_MIN_AGE_DAYS jours,
        et absents de la liste de protection.

        TOUJOURS un diagnostic en lecture seule — rien n'est mis de côté
        automatiquement, voir stage_orphaned_folder() qui exige une
        validation individuelle par dossier."""
        installed = self.app_manager.list_all_sorted(sort_by="name")["apps"]
        installed_terms = set()
        for app in installed:
            if app.get("name"):
                installed_terms.add(app["name"].lower())
            if app.get("publisher"):
                installed_terms.add(app["publisher"].lower())

        def _matches_installed(folder_name: str) -> bool:
            lowered = folder_name.lower()
            return any(lowered in term or term in lowered for term in installed_terms if term)

        now = time.time()
        candidates = []
        for root in self._scan_root_folders():
            try:
                children = [c for c in root.iterdir() if c.is_dir()]
            except (PermissionError, OSError):
                continue

            for child in children:
                if child.name.lower() in PROTECTED_FOLDER_NAMES:
                    continue
                if _matches_installed(child.name):
                    continue
                try:
                    files = [f for f in child.rglob("*") if f.is_file()]
                    last_modified = max((f.stat().st_mtime for f in files), default=child.stat().st_mtime)
                    size_mb = round(sum(f.stat().st_size for f in files) / (1024 * 1024), 1)
                except (PermissionError, OSError):
                    continue

                age_days = int((now - last_modified) / 86400)
                if age_days < ORPHAN_MIN_AGE_DAYS:
                    continue

                candidates.append({
                    "path": str(child),
                    "size_mb": size_mb,
                    "age_days": age_days,
                    "reason": (
                        f"Aucune application installée ne correspond à '{child.name}', "
                        f"non modifié depuis {age_days} jours"
                    ),
                })

        candidates.sort(key=lambda c: c["size_mb"], reverse=True)
        return candidates

    def stage_orphaned_folder(self, folder_path: str, reason: str) -> Dict:
        """Met UN SEUL dossier de côté (staging), après validation
        individuelle côté menu — jamais en lot pour cette catégorie
        précise (voir la règle de sécurité en tête de fichier)."""
        staging_id = self.file_triage.move_to_staging(folder_path, reason)
        if staging_id:
            return {"status": "ok", "message": f"'{folder_path}' mis de côté (récupérable via l'option 12)."}
        return {"status": "erreur", "message": f"Échec de la mise de côté de '{folder_path}'."}
