"""
startup_manager.py
Liste et désactive les programmes qui se lancent au démarrage de Windows
(clés de registre Run + dossier Démarrage), pour accélérer le boot.

Bonne pratique : plutôt que de SUPPRIMER une entrée (destructif et
irréversible), on la déplace vers une clé de "sauvegarde" — ce qui
permet de la restaurer si un programme important a été désactivé par
erreur.
"""

import os
import winreg
from pathlib import Path
from typing import Dict, List

RUN_KEY_PATHS = [
    (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run"),
]

BACKUP_KEY_PATH = r"Software\AntiZeevirius\DisabledStartupBackup"

# Programmes fréquemment safe à désactiver (liste indicative, à valider
# au cas par cas — ne jamais désactiver en aveugle sans vérifier ce que
# c'est réellement).
COMMONLY_SAFE_TO_DISABLE = [
    "onedrive", "skype", "spotify", "steam", "adobe", "itunes",
    "quicktime", "realplayer", "teamviewer",
]


class StartupManager:

    def list_registry_startup_items(self) -> List[Dict]:
        """Liste toutes les entrées des clés Run (HKCU + HKLM)."""
        items = []
        for hive, key_path in RUN_KEY_PATHS:
            hive_name = "HKCU" if hive == winreg.HKEY_CURRENT_USER else "HKLM"
            try:
                with winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ) as key:
                    i = 0
                    while True:
                        try:
                            name, value, _ = winreg.EnumValue(key, i)
                            items.append({
                                "name": name,
                                "command": value,
                                "hive": hive_name,
                                "key_path": key_path,
                                "recommended_disable": any(
                                    kw in name.lower() or kw in value.lower()
                                    for kw in COMMONLY_SAFE_TO_DISABLE
                                ),
                            })
                            i += 1
                        except OSError:
                            break
            except FileNotFoundError:
                continue
        return items

    def list_startup_folder_items(self) -> List[Dict]:
        """Liste les raccourcis dans le dossier Démarrage utilisateur."""
        startup_folder = Path(os.environ.get("APPDATA", "")) / \
            "Microsoft/Windows/Start Menu/Programs/Startup"
        items = []
        if startup_folder.exists():
            for f in startup_folder.iterdir():
                if f.is_file():
                    items.append({
                        "name": f.stem,
                        "path": str(f),
                        "recommended_disable": any(
                            kw in f.stem.lower() for kw in COMMONLY_SAFE_TO_DISABLE
                        ),
                    })
        return items

    def disable_registry_item(self, hive_name: str, key_path: str, name: str) -> bool:
        """Déplace une entrée de démarrage vers une clé de sauvegarde
        (réversible) au lieu de la supprimer définitivement."""
        hive = winreg.HKEY_CURRENT_USER if hive_name == "HKCU" else winreg.HKEY_LOCAL_MACHINE

        try:
            with winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ) as key:
                value, _ = winreg.QueryValueEx(key, name)
        except (FileNotFoundError, OSError):
            return False

        try:
            backup_key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, BACKUP_KEY_PATH)
            winreg.SetValueEx(backup_key, f"{hive_name}|{name}", 0, winreg.REG_SZ, value)
            winreg.CloseKey(backup_key)

            with winreg.OpenKey(hive, key_path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
            return True
        except (PermissionError, OSError) as e:
            print(f"[ERREUR] Désactivation de '{name}' échouée : {e}")
            return False

    def restore_registry_item(self, hive_name: str, name: str) -> bool:
        """Restaure une entrée précédemment désactivée."""
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, BACKUP_KEY_PATH, 0, winreg.KEY_READ) as backup_key:
                value, _ = winreg.QueryValueEx(backup_key, f"{hive_name}|{name}")
        except (FileNotFoundError, OSError):
            return False

        hive = winreg.HKEY_CURRENT_USER if hive_name == "HKCU" else winreg.HKEY_LOCAL_MACHINE
        target_key_path = RUN_KEY_PATHS[0][1] if hive_name == "HKCU" else RUN_KEY_PATHS[1][1]

        try:
            with winreg.CreateKey(hive, target_key_path) as key:
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, BACKUP_KEY_PATH, 0, winreg.KEY_SET_VALUE) as backup_key:
                winreg.DeleteValue(backup_key, f"{hive_name}|{name}")
            return True
        except (PermissionError, OSError):
            return False

    def disable_startup_folder_item(self, file_path: str) -> bool:
        """Renomme un raccourci du dossier Démarrage en .disabled
        (réversible en renommant à nouveau)."""
        path = Path(file_path)
        if not path.exists():
            return False
        try:
            new_path = path.with_suffix(path.suffix + ".disabled")
            path.rename(new_path)
            return True
        except (PermissionError, OSError):
            return False
