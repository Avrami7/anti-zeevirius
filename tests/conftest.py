"""
conftest.py
Configuration pytest partagée. Ajoute la racine du projet (antivirus_windows/)
au sys.path pour que les tests puissent faire `from scanner.heuristics import ...`
`from optimizer.disk_analyzer import ...` etc., quel que soit le répertoire
depuis lequel `pytest` est lancé.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
