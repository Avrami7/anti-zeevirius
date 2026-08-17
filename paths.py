"""
paths.py — où l'application lit, où elle écrit.

Tant qu'ANTI-ZEEVIRIUS se lançait depuis ses sources, tout vivait dans le
dossier du projet : le code, les bases de signatures, mais aussi la
quarantaine, le sas de fichiers mis de côté et les journaux. C'est
inoffensif en développement, et cassé dès qu'on installe l'application.

Deux raisons, distinctes :

1. **Gel en exécutable.** PyInstaller décompresse les données embarquées
   dans un dossier temporaire exposé par `sys._MEIPASS`. `Path(__file__)`
   ne pointe alors plus nulle part d'utile : `gui/web/` serait introuvable
   et l'interface ne se chargerait pas.

2. **Installation dans Program Files.** Windows refuse l'écriture dans
   `C:\\Program Files` à un processus non élevé. Pire que l'échec franc :
   la virtualisation UAC peut rediriger silencieusement ces écritures vers
   `%LOCALAPPDATA%\\VirtualStore`, où l'utilisateur ne les retrouvera
   jamais. Un fichier mis en quarantaine qui disparaît dans un dossier
   fantôme est exactement ce qu'un antivirus ne doit pas faire.

D'où la séparation :

    resource_path()  ressources EN LECTURE SEULE, livrées avec le programme
                     (gui/web/, bases de signatures par défaut, README)

    data_path()      données PRODUITES par l'utilisateur, en écriture
                     (quarantaine, sas, journaux, cache, clé VirusTotal)

En exécution depuis les sources, les deux pointent sur le dossier du projet :
le comportement de développement — et les 379 tests — restent inchangés.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

__all__ = [
    "is_frozen", "resource_path", "data_path",
    "signatures_dir", "logs_dir", "quarantine_dir", "staging_dir",
    "cache_dir", "organizer_log", "ensure_user_data",
]

APP_NAME = "ANTI-ZEEVIRIUS"

# Racine des sources : ce fichier est à la racine du projet.
_PROJECT_DIR = Path(__file__).resolve().parent


def is_frozen() -> bool:
    """Vrai lorsque le programme tourne depuis un exécutable PyInstaller."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def resource_path(*parts: str) -> Path:
    """Chemin d'une ressource embarquée, en LECTURE SEULE.

    N'écris jamais ici : gelé, cela pointe sur un dossier temporaire effacé
    à la fermeture ; installé, sur un dossier protégé par Windows.
    """
    base = Path(getattr(sys, "_MEIPASS")) if is_frozen() else _PROJECT_DIR
    return base.joinpath(*parts)


def data_path(*parts: str) -> Path:
    """Chemin d'une donnée utilisateur, en ÉCRITURE.

    - Gelé sous Windows : %LOCALAPPDATA%\\ANTI-ZEEVIRIUS
    - Gelé ailleurs     : ~/.local/share/anti-zeevirius (convention XDG)
    - Depuis les sources : le dossier du projet, comme avant.

    ANTIZEEVIRIUS_DATA_DIR force l'emplacement — utile pour une installation
    portable (clé USB) ou pour isoler un test.
    """
    forced = os.environ.get("ANTIZEEVIRIUS_DATA_DIR")
    if forced:
        return Path(forced).expanduser().joinpath(*parts)

    if not is_frozen():
        return _PROJECT_DIR.joinpath(*parts)

    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return root.joinpath(APP_NAME, *parts)

    xdg = os.environ.get("XDG_DATA_HOME")
    root = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return root.joinpath("anti-zeevirius", *parts)


# ── Emplacements nommés ────────────────────────────────────────────────────
# Les bases de signatures sont un cas mixte : livrées avec le programme, mais
# destinées à être enrichies (MalwareBazaar, règles YARA à jour). Elles vivent
# donc côté données, et sont amorcées au premier lancement depuis la copie
# embarquée — voir ensure_user_data().

def signatures_dir() -> Path: return data_path("signatures")
def logs_dir() -> Path:       return data_path("logs")
def quarantine_dir() -> Path: return data_path("quarantine_storage")
def staging_dir() -> Path:    return data_path("triage_staging")
def cache_dir() -> Path:      return data_path("cache")
def organizer_log() -> Path:  return data_path("organizer_logs", "reorg_index.json")


def ensure_user_data() -> Path:
    """Crée l'arborescence de données au premier lancement et y recopie les
    bases de signatures livrées avec le programme.

    La copie n'écrase JAMAIS un fichier existant : une base enrichie par
    l'utilisateur ne doit pas être réinitialisée par une mise à jour.
    Retourne la racine des données.
    """
    root = data_path()
    for d in (signatures_dir(), logs_dir(), quarantine_dir(),
              staging_dir(), cache_dir(), organizer_log().parent):
        d.mkdir(parents=True, exist_ok=True)

    embarquees = resource_path("signatures")
    if embarquees.is_dir() and embarquees.resolve() != signatures_dir().resolve():
        for src in embarquees.iterdir():
            if not src.is_file():
                continue
            # La clé VirusTotal est un secret personnel : jamais livrée,
            # jamais recopiée depuis le paquet.
            if src.name == "vt_api_key.txt":
                continue
            dst = signatures_dir() / src.name
            if not dst.exists():
                shutil.copy2(src, dst)

    return root
