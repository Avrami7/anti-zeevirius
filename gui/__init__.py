"""
Interface web locale d'ANTI-ZEEVIRIUS.

Trois modules, sans aucune dépendance hors stdlib :

* `gui.server` — serveur HTTP lié à 127.0.0.1, jeton de session,
  routage `/api/*`, service verrouillé des fichiers de `gui/web/`.
* `gui.bridge` — adaptation du contrat d'API vers les modules métier,
  avec import paresseux et double validation des actions destructives.
* `gui.jobs`   — tâches asynchrones (job_id, progression, annulation).

Le contrat d'échange figé est décrit dans `gui/API_CONTRACT.md`.
Ce fichier n'importe volontairement rien : `import gui` doit rester
sans effet de bord, même sur une machine où winreg/watchdog/yara
sont absents.
"""

__all__ = ["server", "bridge", "jobs"]
