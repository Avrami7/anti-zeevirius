"""
task_scheduler.py
Crée une tâche planifiée Windows pour exécuter le nettoyage automatique
(via schtasks.exe, l'outil natif Windows — pas de dépendance externe).
"""

import subprocess
import sys
from pathlib import Path
from typing import Dict


class TaskScheduler:
    TASK_NAME = "AntiZeevirius_AutoCleanup"
    # Tâche distincte du nettoyage simple ci-dessus : le Mode Gardien fait
    # bien plus (scan antivirus + rangement des fichiers non utilisés en
    # plus du nettoyage TEMP). Garder deux tâches séparées évite de changer
    # silencieusement le comportement d'une tâche déjà planifiée par
    # quelqu'un qui ne voulait qu'un simple nettoyage hebdomadaire.
    GUARDIAN_TASK_NAME = "AntiZeevirius_GuardianDaily"

    @staticmethod
    def create_weekly_cleanup_task(script_path: str, day: str = "SUN", time: str = "09:00") -> Dict:
        """
        Planifie l'exécution hebdomadaire du script de nettoyage.
        day: MON, TUE, WED, THU, FRI, SAT, SUN
        time: format HH:MM (24h)
        """
        python_exe = sys.executable
        script = str(Path(script_path).resolve())

        command = [
            "schtasks", "/Create",
            "/TN", TaskScheduler.TASK_NAME,
            "/TR", f'"{python_exe}" "{script}" --auto-clean',
            "/SC", "WEEKLY",
            "/D", day,
            "/ST", time,
            "/F",  # force overwrite si la tâche existe déjà
        ]

        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=15)
            if result.returncode == 0:
                return {"status": "ok", "message": f"Tâche planifiée : chaque {day} à {time}"}
            return {"status": "erreur", "message": result.stderr.strip()}
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return {"status": "erreur", "message": str(e)}

    @staticmethod
    def remove_scheduled_task() -> Dict:
        """Supprime la tâche planifiée."""
        try:
            result = subprocess.run(
                ["schtasks", "/Delete", "/TN", TaskScheduler.TASK_NAME, "/F"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                return {"status": "ok", "message": "Tâche planifiée supprimée"}
            return {"status": "erreur", "message": result.stderr.strip()}
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return {"status": "erreur", "message": str(e)}

    @staticmethod
    def check_task_exists() -> bool:
        try:
            result = subprocess.run(
                ["schtasks", "/Query", "/TN", TaskScheduler.TASK_NAME],
                capture_output=True, text=True, timeout=10,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    # ── MODE GARDIEN : tâche quotidienne (protection + optimisation + rangement) ─
    @staticmethod
    def create_daily_guardian_task(script_path: str, time: str = "09:00") -> Dict:
        """Planifie l'exécution quotidienne du MODE GARDIEN complet
        (--guardian) : nettoyage TEMP, scan antivirus des dossiers
        sensibles, rangement des fichiers non utilisés. Contrairement au
        nettoyage simple ci-dessus, tourne tous les jours (SC DAILY)."""
        python_exe = sys.executable
        script = str(Path(script_path).resolve())

        command = [
            "schtasks", "/Create",
            "/TN", TaskScheduler.GUARDIAN_TASK_NAME,
            "/TR", f'"{python_exe}" "{script}" --guardian',
            "/SC", "DAILY",
            "/ST", time,
            "/F",
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=15)
            if result.returncode == 0:
                return {"status": "ok", "message": f"Mode Gardien planifié : tous les jours à {time}"}
            return {"status": "erreur", "message": result.stderr.strip()}
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return {"status": "erreur", "message": str(e)}

    @staticmethod
    def remove_guardian_task() -> Dict:
        try:
            result = subprocess.run(
                ["schtasks", "/Delete", "/TN", TaskScheduler.GUARDIAN_TASK_NAME, "/F"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                return {"status": "ok", "message": "Mode Gardien automatique désactivé"}
            return {"status": "erreur", "message": result.stderr.strip()}
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return {"status": "erreur", "message": str(e)}

    @staticmethod
    def check_guardian_task_exists() -> bool:
        try:
            result = subprocess.run(
                ["schtasks", "/Query", "/TN", TaskScheduler.GUARDIAN_TASK_NAME],
                capture_output=True, text=True, timeout=10,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
