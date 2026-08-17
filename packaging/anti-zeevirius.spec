# -*- mode: python ; coding: utf-8 -*-
"""
packaging/anti-zeevirius.spec — recette PyInstaller d'ANTI-ZEEVIRIUS.

Produit UN exécutable Windows unique, fenêtré (sans console), qui embarque
l'interpréteur Python, les modules du projet, l'interface web et les bases de
signatures livrées par défaut.

    pyinstaller --noconfirm --clean packaging/anti-zeevirius.spec
    → dist/ANTI-ZEEVIRIUS.exe

Ce fichier est exécuté PAR PyInstaller, avec `SPECPATH`, `DISTPATH` et
`workpath` déjà définis dans son espace de noms : c'est du Python, mais qui ne
tourne que dans ce contexte.

Décisions structurantes, et pourquoi
------------------------------------
* **Un seul fichier (onefile).** L'utilisateur veut double-cliquer, pas
  naviguer dans un dossier de 300 fichiers. Contrepartie assumée : à chaque
  lancement, l'exécutable se décompresse dans `%TEMP%\\_MEIxxxxxx`, ce qui
  ajoute une à trois secondes au démarrage et fait passer tout le contenu sous
  le nez de l'antivirus du système. Voir packaging/README.md.

* **Pas de console** (`console=False`). Une fenêtre noire qui s'ouvre derrière
  l'interface ferait « script bricolé », et sa fermeture tuerait le serveur.

* **Pas d'UPX** (`upx=False`). La compression UPX gagnerait quelques mégaoctets
  et coûterait très cher : un exécutable Python compressé par UPX est un
  schéma classique de logiciel malveillant, et il est détecté comme tel par
  une bonne partie des moteurs de VirusTotal. Pour un antivirus, se faire
  prendre pour un virus est le pire des compromis.

* **Métadonnées de version renseignées.** Un binaire sans nom de produit ni
  description est anonyme dans le gestionnaire des tâches et dans la boîte de
  dialogue UAC — et SmartScreen note d'autant plus mal ce qui est anonyme.
"""

import os
import sys
from pathlib import Path

# ── Repères sur le disque ────────────────────────────────────────────────
# SPECPATH est injecté par PyInstaller ; le repli permet de relire ce fichier
# avec un outil ordinaire (py_compile, éditeur, revue) sans qu'il explose.
try:
    SPEC_DIR = Path(SPECPATH).resolve()  # noqa: F821 — fourni par PyInstaller
except NameError:
    SPEC_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SPEC_DIR.parent

APP_NAME = "ANTI-ZEEVIRIUS"
# Version : celle du dépôt par défaut, remplaçable par la chaîne
# d'intégration continue, qui la tire du tag Git (AZ_VERSION=1.2.0). Le même
# numéro est passé à Inno Setup par /DAppVersion, pour que l'exécutable,
# l'installeur et l'entrée « Applications installées » disent tous la même
# chose — trois versions divergentes rendent tout rapport de bug inexploitable.
APP_VERSION = os.environ.get("AZ_VERSION", "1.0.0")
ICON = SPEC_DIR / "anti-zeevirius.ico"

# ── Données embarquées ───────────────────────────────────────────────────
# (source sur la machine de build, destination dans sys._MEIPASS)
# Les destinations reproduisent l'arborescence du projet, parce que paths.py
# construit ses chemins avec resource_path("gui", "web") et
# resource_path("signatures") : changer ces destinations casserait l'interface.
datas = [
    (str(PROJECT_DIR / "gui" / "web"), "gui/web"),
    (str(PROJECT_DIR / "README.md"), "."),
]

# Bases de signatures : fichier par fichier, JAMAIS le dossier entier.
# `signatures/` peut contenir vt_api_key.txt — la clé VirusTotal PERSONNELLE
# de celui qui compile. Embarquer le dossier en bloc la distribuerait à tous
# les utilisateurs de l'installeur, avec le quota et la responsabilité qui
# vont avec. Le .gitignore la protège du dépôt ; ici, c'est cette liste
# explicite qui la protège du paquet.
for base in ("malicious_hashes.txt", "rules.yar"):
    source = PROJECT_DIR / "signatures" / base
    if source.is_file():
        datas.append((str(source), "signatures"))

# ── Imports que l'analyse statique ne peut pas voir ──────────────────────
# gui/bridge.py n'importe AUCUN module métier au chargement : il les résout à
# l'exécution par importlib.import_module(), depuis la table _MODULE_SPECS.
# C'est délibéré (le serveur doit démarrer même si yara ou winreg manquent),
# mais PyInstaller suit les imports en lisant le bytecode : une chaîne de
# caractères passée à importlib lui est invisible. Sans cette liste,
# l'exécutable se construit sans erreur et l'application démarre — puis chaque
# action répond « module indisponible ». Panne silencieuse, donc liste tenue
# à la main, à mettre à jour en même temps que _MODULE_SPECS.
hiddenimports = [
    # Racine du projet
    "main",
    "paths",
    # scanner/
    "scanner.hash_scanner",
    "scanner.yara_scanner",
    "scanner.heuristics",
    # quarantine/
    "quarantine.quarantine_manager",
    # monitor/
    "monitor.realtime_monitor",
    # optimizer/
    "optimizer.temp_cleaner",
    "optimizer.startup_manager",
    "optimizer.disk_analyzer",
    "optimizer.task_scheduler",
    "optimizer.file_triage",
    "optimizer.folder_organizer",
    "optimizer.guardian",
    "optimizer.app_manager",
    "optimizer.residue_cleaner",
    "optimizer.ransomware_shield",
    "optimizer.reputation_checker",
    "optimizer.phishing_link_checker",
    # Interface locale
    "gui.server",
    "gui.bridge",
    "gui.jobs",
]

# Dépendances tierces également chargées tardivement (dans un try/except au
# milieu d'une fonction, ou choisies selon la plateforme au démarrage).
hiddenimports += [
    "yara",                                        # scanner/yara_scanner.py
    "pefile",                                      # scanner/heuristics.py
    "psutil",                                      # optimizer/ransomware_shield.py
    "numpy",                                       # entropie vectorisée — gardé
    "requests",                                    # réputation + anti-hameçonnage
    "winreg",                                      # registre (démarrage, applis)
    # watchdog choisit son observateur selon le système ; sous Windows c'est
    # l'API ReadDirectoryChangesW qui est utilisée, via ces deux modules.
    "watchdog.observers",
    "watchdog.observers.polling",
    "watchdog.observers.read_directory_changes",
    "watchdog.observers.winapi",
    # pywin32 : optimizer/folder_organizer.py et residue_cleaner.py appellent
    # win32com.client.Dispatch("WScript.Shell") pour lire les raccourcis .lnk.
    "win32com.client",
    "pythoncom",
    "pywintypes",
]

# ── Ce qui n'a rien à faire dans un livrable ─────────────────────────────
# Attention à ne pas trop élaguer : numpy RESTE (scanner/heuristics.py s'en
# sert pour l'entropie de Shannon, ~50-100x plus rapide qu'une boucle Python
# sur des sections PE de plusieurs mégaoctets). Ce qui part, c'est l'outillage
# de développement et de test, jamais une dépendance d'exécution.
excludes = [
    "pytest", "_pytest", "py", "pluggy", "iniconfig",   # suite de tests
    "playwright", "pyee", "greenlet",                   # tests d'interface
    "PIL",                                              # sert à make_icon.py, pas à l'app
    "tkinter",                                          # aucune UI Tk : l'interface est web
    "matplotlib", "IPython", "jupyter", "notebook",
    "pydoc_data",
]

a = Analysis(
    [str(SPEC_DIR / "launcher.py")],
    pathex=[str(PROJECT_DIR)],   # pour que `import main`, `scanner.*` … résolvent
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# ── Ressource de version Windows ─────────────────────────────────────────
# Ce que lit l'onglet « Détails » des propriétés du fichier, ce qu'affiche la
# boîte UAC, et ce sur quoi s'appuie la réputation SmartScreen.
#
# L'import est conditionnel : PyInstaller.utils.win32.versioninfo dépend de
# pywin32 et lève ImportError hors Windows — sans ce garde-fou, ce .spec
# serait illisible sous Linux et macOS, où l'on veut pouvoir faire tourner
# l'analyse à blanc pour le vérifier. Le build Windows réel, lui, passe
# toujours par la branche du haut.
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable,
        VarFileInfo, VarStruct, VSVersionInfo,
    )

    # filevers/prodvers exigent EXACTEMENT quatre entiers : « 1.2 » comme
    # « 1.2.3.4.5 » doivent retomber sur leurs pieds, sinon le build casse au
    # tout dernier moment, après vingt minutes d'analyse.
    _parts = [int(p) for p in APP_VERSION.split(".") if p.isdigit()]
    _v = tuple((_parts + [0, 0, 0, 0])[:4])

    version_info = VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=_v, prodvers=_v,
            mask=0x3F, flags=0x0,
            OS=0x40004,        # Windows NT, 32 bits d'API
            fileType=0x1,      # application
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo([
                StringTable(
                    "040C04B0",  # français (France), jeu de caractères Unicode
                    [
                        StringStruct("CompanyName", "ANTI-ZEEVIRIUS"),
                        StringStruct("FileDescription",
                                     "Antivirus et optimiseur Windows ANTI-ZEEVIRIUS"),
                        StringStruct("FileVersion", APP_VERSION),
                        StringStruct("InternalName", "anti-zeevirius"),
                        StringStruct("LegalCopyright",
                                     "Logiciel libre — voir README.md"),
                        StringStruct("OriginalFilename", f"{APP_NAME}.exe"),
                        StringStruct("ProductName", APP_NAME),
                        StringStruct("ProductVersion", APP_VERSION),
                    ],
                )
            ]),
            # 0x040C = français, 1200 = Unicode. Les deux doivent correspondre
            # à la clé de la StringTable ci-dessus, sinon Windows ignore le bloc.
            VarFileInfo([VarStruct("Translation", [0x040C, 1200])]),
        ],
    )
else:
    version_info = None

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,               # voir l'en-tête : UPX = faux positifs antivirus
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,           # application fenêtrée : aucune console noire
    # Sur plantage précoce, afficher la trace dans une boîte de dialogue
    # plutôt que de disparaître sans un mot : sans console, c'est le seul
    # canal de diagnostic dont dispose l'utilisateur pour nous rapporter
    # quoi que ce soit d'exploitable.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON),
    version=version_info,
    # uac_admin RESTE À FAUX, volontairement. L'installeur, lui, exige
    # l'élévation (il écrit dans Program Files). Mais forcer l'élévation à
    # CHAQUE lancement de l'application imposerait une invite UAC pour
    # simplement consulter un rapport de scan, et ferait tourner en
    # permanence en administrateur un programme qui sert un serveur HTTP —
    # même limité à 127.0.0.1, ce n'est pas une surface qu'on veut élevée.
    # Les actions qui exigent réellement les droits (registre, tâches
    # planifiées, nettoyage système) testent elles-mêmes leur privilège
    # (Bridge._is_admin → IsUserAnAdmin) et le réclament au coup par coup.
    uac_admin=False,
)
