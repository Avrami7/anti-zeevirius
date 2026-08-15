"""
folder_organizer.py
Réorganisation intelligente de fichiers/dossiers :

1. classify_category()    → classe un fichier par TYPE (Documents, Images,
                             Vidéos, Code, Archives...)
2. classify_application()  → classe un fichier par APPLICATION qui l'ouvre
                             (Microsoft Word, Adobe Photoshop, VLC...)
3. classify_importance()   → classe un fichier par NIVEAU D'IMPORTANCE
                             (Actif récent / Important / Archive / À purger)
                             basé sur la fraîcheur d'utilisation
4. find_least_used_files() → identifie les fichiers les moins utilisés
                             (par date de dernier ACCÈS, avec repli sur la
                             date de modification — voir limite Windows
                             ci-dessous)
5. move_folder_into()      → déplace un dossier entier dans un autre, avec
                             gestion des conflits de noms

Principe de sécurité central (cohérent avec quarantine_manager.py et
file_triage.py) : AUCUNE opération n'est irréversible. Chaque déplacement
est journalisé dans un index JSON (session par session) et peut être
annulé via undo_session(). Rien n'est supprimé — uniquement déplacé.

LIMITE IMPORTANTE — dernier accès sous Windows :
Depuis Windows Vista, le suivi de la date de dernier accès (NTFS Last
Access Time) est DÉSACTIVÉ PAR DÉFAUT pour des raisons de performance
(NtfsDisableLastAccessUpdate=1). Résultat : st_atime peut être identique
à st_mtime sur de nombreux fichiers, rendant "fichier le moins UTILISÉ"
équivalent à "fichier le moins MODIFIÉ" — ce n'est pas la même chose
(un PDF qu'on relit souvent sans jamais le modifier semblera "non utilisé").
Ce module détecte cette situation (échantillonnage) et le signale
explicitement dans les résultats plutôt que de donner un faux sentiment
de précision. Pour un suivi fiable, il faudrait exécuter (droits admin,
puis redémarrer) :
SOLUTIONS APPORTÉES AUX 2 LIMITES SIGNALÉES PRÉCÉDEMMENT :

A) Fiabilité du "moins utilisé" → RecentItemsTracker (voir plus bas) exploite
   le dossier "Éléments récents" de Windows comme signal d'usage RÉEL,
   indépendant du réglage NTFS de dernier accès. Combiné avec la date de
   modification en repli — voir la classe pour le détail et ses limites.

B) Performance sur gros volumes → remplacement de Path.rglob() par un
   parcours récursif via os.scandir() (fonction _scan_files ci-dessous) :
   les métadonnées (stat) sont mises en cache par le système de fichiers
   lors du listage du dossier, évitant un appel stat() séparé par fichier
   — significativement plus rapide sur de gros volumes (>50-100k fichiers),
   et l'échantillonnage pour la fiabilité atime ne matérialise plus la
   totalité de l'arborescence en mémoire.
"""

import itertools
import json
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

try:
    import win32com.client
    PYWIN32_AVAILABLE = True
except ImportError:
    PYWIN32_AVAILABLE = False

# Nos dossiers générés suivent tous le format strict "NN_Nom" (2 chiffres +
# underscore, ex: "01_Documents", "00_Non_utilises_depuis_longtemps").
# Une regex précise évite de confondre un vrai dossier utilisateur comme
# "2024_Rapports" (4 chiffres) avec un dossier généré par l'outil.
_GENERATED_FOLDER_PATTERN = re.compile(r"^\d{2}_")

# ── Extensions jamais déplacées : fichiers systèmes/techniques dont le
# déplacement pourrait casser une application installée (une DLL sortie
# de son dossier d'installation, par exemple).
NEVER_MOVE_EXTENSIONS = {".dll", ".sys", ".ini", ".config", ".lnk"}

# ── Dossiers jamais traversés/réorganisés (système, pas dossiers perso)
NEVER_ENTER_FOLDER_KEYWORDS = [
    "windows", "program files", "programdata", "$recycle.bin",
    ".git", "node_modules", "__pycache__", "system volume information",
]

# ── Catégories par type de fichier ───────────────────────────────────
CATEGORY_MAP: Dict[str, str] = {}
_CATEGORY_EXTENSIONS = {
    "01_Documents": [".docx", ".doc", ".odt", ".rtf", ".txt", ".md"],
    "02_Feuilles_de_calcul": [".xlsx", ".xls", ".ods", ".csv"],
    "03_Presentations": [".pptx", ".ppt", ".odp"],
    "04_PDF": [".pdf"],
    "05_Images": [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".svg", ".tiff"],
    "06_Videos": [".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm"],
    "07_Audio": [".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"],
    "08_Archives": [".zip", ".rar", ".7z", ".tar", ".gz", ".iso"],
    "09_Code_Developpement": [
        ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".h",
        ".cs", ".php", ".html", ".css", ".json", ".xml", ".sql", ".sh", ".ps1", ".go", ".rs",
    ],
    "10_Executables_Installateurs": [".exe", ".msi", ".bat", ".cmd"],
}
for _cat, _exts in _CATEGORY_EXTENSIONS.items():
    for _ext in _exts:
        CATEGORY_MAP[_ext] = _cat
DEFAULT_CATEGORY = "11_Divers"

# ── Applications associées à chaque extension ────────────────────────
APPLICATION_MAP: Dict[str, str] = {
    ".docx": "Microsoft Word", ".doc": "Microsoft Word", ".rtf": "Microsoft Word",
    ".xlsx": "Microsoft Excel", ".xls": "Microsoft Excel", ".csv": "Microsoft Excel",
    ".pptx": "Microsoft PowerPoint", ".ppt": "Microsoft PowerPoint",
    ".pdf": "Lecteur PDF (Acrobat)",
    ".psd": "Adobe Photoshop", ".ai": "Adobe Illustrator", ".indd": "Adobe InDesign",
    ".xd": "Adobe XD", ".fig": "Figma", ".sketch": "Sketch",
    ".mp3": "Lecteur audio (VLC / Spotify)", ".wav": "Lecteur audio (VLC / Spotify)",
    ".flac": "Lecteur audio (VLC / Spotify)", ".aac": "Lecteur audio (VLC / Spotify)",
    ".ogg": "Lecteur audio (VLC / Spotify)", ".m4a": "Lecteur audio (VLC / Spotify)",
    ".mp4": "Lecteur vidéo (VLC)", ".mkv": "Lecteur vidéo (VLC)", ".avi": "Lecteur vidéo (VLC)",
    ".mov": "Lecteur vidéo (VLC)", ".wmv": "Lecteur vidéo (VLC)", ".webm": "Lecteur vidéo (VLC)",
    ".jpg": "Visionneuse d'images", ".jpeg": "Visionneuse d'images", ".png": "Visionneuse d'images",
    ".gif": "Visionneuse d'images", ".webp": "Visionneuse d'images", ".heic": "Visionneuse d'images",
    ".svg": "Illustrateur vectoriel",
    ".py": "Python / VS Code", ".js": "VS Code / Node.js", ".ts": "VS Code / Node.js",
    ".jsx": "VS Code / Node.js", ".tsx": "VS Code / Node.js",
    ".java": "IntelliJ IDEA / Eclipse", ".cpp": "Visual Studio / CLion", ".c": "Visual Studio / CLion",
    ".cs": "Visual Studio", ".php": "PHPStorm / VS Code", ".html": "Navigateur / VS Code",
    ".css": "Navigateur / VS Code", ".sql": "DBeaver / SQL Server Management Studio",
    ".zip": "Archiveur (7-Zip / WinRAR)", ".rar": "Archiveur (7-Zip / WinRAR)",
    ".7z": "Archiveur (7-Zip / WinRAR)", ".iso": "Graveur / monteur d'image disque",
    ".exe": "Installateur Windows", ".msi": "Installateur Windows",
    ".bat": "Script Windows (Invite de commandes)", ".ps1": "Script Windows (PowerShell)",
    ".vsdx": "Microsoft Visio", ".pub": "Microsoft Publisher", ".one": "Microsoft OneNote",
    ".accdb": "Microsoft Access", ".dwg": "AutoCAD", ".blend": "Blender", ".torrent": "Client Torrent",
}
DEFAULT_APPLICATION = "Application générique / inconnue"

# ── Seuils de fraîcheur pour l'importance ────────────────────────────
RECENT_DAYS = 30      # utilisé/modifié il y a moins de 30j → actif
IMPORTANT_DAYS = 180  # 30-180j → important
# au-delà de 180j → archive (sauf extensions "jetables" → à purger)
DISPOSABLE_EXTENSIONS = {".tmp", ".temp", ".log", ".bak", ".old", ".dmp", ".chk", ".~"}


# ── SOLUTION B : parcours performant pour gros volumes ────────────────
def _scan_files(root: Path) -> Iterator[Tuple[Path, os.stat_result]]:
    """Parcours récursif via os.scandir(), plus rapide et moins gourmand
    en mémoire que Path.rglob() sur de très gros volumes :
    - os.scandir() met en cache les métadonnées lors du listage du dossier
      (DirEntry.stat() réutilise l'info déjà récupérée par le système lors
      du listage, au lieu de refaire un appel stat() séparé par fichier) ;
    - c'est un générateur pur : aucune liste de l'arborescence complète
      n'est jamais construite en mémoire, contrairement à `list(root.rglob("*"))`.
    Chaque dossier système/protégé est ignoré silencieusement (verrouillé,
    lien symbolique cassé, permissions refusées...)."""
    try:
        with os.scandir(root) as it:
            entries = list(it)
    except (PermissionError, OSError, FileNotFoundError):
        return

    for entry in entries:
        try:
            if entry.is_dir(follow_symlinks=False):
                if FolderOrganizer._is_never_enter(Path(entry.path)):
                    continue
                yield from _scan_files(Path(entry.path))
            elif entry.is_file(follow_symlinks=False):
                yield Path(entry.path), entry.stat(follow_symlinks=False)
        except (PermissionError, OSError, FileNotFoundError):
            continue


# ── SOLUTION A : signal d'usage réel, indépendant du réglage NTFS ─────
class RecentItemsTracker:
    """
    Exploite le dossier "Éléments récents" de Windows
    (%APPDATA%\\Microsoft\\Windows\\Recent) comme signal d'usage RÉEL.

    Principe : chaque fois qu'un utilisateur ouvre un fichier via
    l'Explorateur, Office, ou la plupart des applications Windows qui
    respectent le mécanisme MRU (Most Recently Used) du shell, un
    raccourci .lnk est créé/mis à jour dans ce dossier. La date de
    MODIFICATION de ce raccourci correspond donc à la dernière ouverture
    RÉELLE du fichier cible — et ce, QUE le suivi NTFS du dernier accès
    (st_atime) soit activé ou non sur le disque. C'est la même technique
    qu'utilisent les outils d'investigation forensique Windows pour
    reconstituer un historique d'activité fiable.

    Limite honnête : ne couvre que les fichiers ouverts via ce mécanisme
    MRU. Un fichier lu par un script, par une application qui n'enregistre
    pas dans les Recent Items, ou copié/déplacé sans être "ouvert", ne sera
    pas détecté ici. C'est un signal COMPLÉMENTAIRE qui améliore la
    précision — pas une garantie absolue. Utilisé en repli combiné avec la
    date de modification (jamais comme unique source de vérité).

    Dépendance : pywin32 (pip install pywin32), Windows uniquement, pour
    résoudre la cible réelle d'un raccourci .lnk. Sans pywin32, l'index
    retourné est simplement vide — dégradation silencieuse, le reste de
    l'outil continue de fonctionner sur la date de modification seule.
    """

    def __init__(self):
        appdata = os.environ.get("APPDATA")
        self.recent_dir = Path(appdata) / "Microsoft" / "Windows" / "Recent" if appdata else None

    def build_usage_index(self) -> Dict[str, float]:
        """Retourne {chemin_absolu_normalisé_en_minuscules: timestamp_dernière_ouverture}."""
        index: Dict[str, float] = {}
        if not PYWIN32_AVAILABLE or not self.recent_dir or not self.recent_dir.exists():
            return index

        try:
            shell = win32com.client.Dispatch("WScript.Shell")
        except Exception:
            return index

        for lnk in self.recent_dir.glob("*.lnk"):
            try:
                shortcut = shell.CreateShortCut(str(lnk))
                target = shortcut.TargetPath
                if not target:
                    continue
                opened_at = lnk.stat().st_mtime
                key = str(Path(target).resolve()).lower()
                # Garde la date la plus récente si plusieurs raccourcis
                # pointent vers la même cible (renommages successifs du .lnk).
                index[key] = max(index.get(key, 0.0), opened_at)
            except Exception:
                continue
        return index


class FolderOrganizer:
    def __init__(self, log_path: str):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        # Même raisonnement que QuarantineManager/FileTriage : verrou +
        # écriture atomique pour éviter toute corruption/race condition
        # si plusieurs opérations de réorganisation tournent en parallèle.
        self._lock = threading.Lock()

        # SOLUTION A : signal d'usage réel via les Éléments récents Windows.
        # Construit une seule fois par instance (coûte plusieurs appels
        # système), mise en cache paresseuse — voir refresh_recent_usage_index().
        self._recent_tracker = RecentItemsTracker()
        self._recent_usage_index: Optional[Dict[str, float]] = None

    def _get_recent_usage_index(self) -> Dict[str, float]:
        if self._recent_usage_index is None:
            self._recent_usage_index = self._recent_tracker.build_usage_index()
        return self._recent_usage_index

    def refresh_recent_usage_index(self) -> int:
        """Force la reconstruction de l'index des Éléments récents (à
        appeler si l'utilisateur veut une mesure à jour sans relancer le
        programme). Retourne le nombre d'éléments indexés."""
        self._recent_usage_index = self._recent_tracker.build_usage_index()
        return len(self._recent_usage_index)

    # ── Journal (undo) ────────────────────────────────────────────
    def _load_log(self) -> List[Dict]:
        if not self.log_path.exists():
            return []
        try:
            return json.loads(self.log_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def _save_log(self, entries: List[Dict]) -> None:
        tmp_path = self.log_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_path, self.log_path)

    def _record_move(self, session_id: str, original_path: str, new_path: str, kind: str) -> None:
        with self._lock:
            entries = self._load_log()
            entries.append({
                "id": str(uuid.uuid4()),
                "session_id": session_id,
                "original_path": original_path,
                "new_path": new_path,
                "type": kind,  # "file" ou "folder"
                "date": datetime.now().isoformat(),
                "undone": False,
            })
            self._save_log(entries)

    def list_sessions(self) -> List[Dict]:
        """Résume chaque session de réorganisation (pour choisir laquelle annuler)."""
        entries = self._load_log()
        sessions: Dict[str, Dict] = {}
        for e in entries:
            sid = e["session_id"]
            s = sessions.setdefault(sid, {"session_id": sid, "date": e["date"], "count": 0, "undone": 0})
            s["count"] += 1
            if e["undone"]:
                s["undone"] += 1
        return sorted(sessions.values(), key=lambda x: x["date"], reverse=True)

    def undo_session(self, session_id: str) -> Dict:
        """Annule tous les déplacements d'une session en les remettant à
        leur emplacement d'origine (dans l'ordre inverse)."""
        with self._lock:
            entries = self._load_log()
            targets = [e for e in entries if e["session_id"] == session_id and not e["undone"]]
            restored, errors = 0, []

            for entry in reversed(targets):
                new_path = Path(entry["new_path"])
                original_path = Path(entry["original_path"])
                try:
                    if new_path.exists():
                        original_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(new_path), str(original_path))
                    entry["undone"] = True
                    restored += 1
                except (PermissionError, OSError) as e:
                    errors.append(f"{new_path} → {original_path} : {e}")

            self._save_log(entries)
            return {"restored": restored, "errors": errors}

    # ── Classification ────────────────────────────────────────────
    @staticmethod
    def classify_category(file_path: Path) -> str:
        return CATEGORY_MAP.get(file_path.suffix.lower(), DEFAULT_CATEGORY)

    @staticmethod
    def classify_application(file_path: Path) -> str:
        return APPLICATION_MAP.get(file_path.suffix.lower(), DEFAULT_APPLICATION)

    def classify_importance(
        self, file_path: Path, stat_result: Optional[os.stat_result] = None,
        use_recent_items: bool = True,
    ) -> Dict:
        try:
            stat = stat_result if stat_result is not None else file_path.stat()
        except OSError:
            return {"level": "11_Divers", "reason": "Fichier illisible"}

        ext = file_path.suffix.lower()
        last_used = max(stat.st_mtime, stat.st_atime)

        # SOLUTION A : si les Éléments récents Windows indiquent une
        # ouverture plus récente que mtime/atime (ex: atime désactivé, ou
        # fichier simplement consulté sans modification), on fait confiance
        # au signal le plus récent — un fichier relu chaque semaine ne doit
        # pas être classé "Archive" juste parce qu'il n'a jamais été modifié.
        if use_recent_items:
            recent_index = self._get_recent_usage_index()
            key = str(file_path.resolve()).lower()
            if key in recent_index:
                last_used = max(last_used, recent_index[key])

        age_days = int((time.time() - last_used) / 86400)

        if ext in DISPOSABLE_EXTENSIONS:
            return {
                "level": "04_A_Purger",
                "reason": f"Extension technique jetable ({ext})",
                "age_days": age_days,
            }
        if age_days <= RECENT_DAYS:
            return {
                "level": "01_Actif_Recent",
                "reason": f"Utilisé/modifié il y a {age_days} jour(s)",
                "age_days": age_days,
            }
        if age_days <= IMPORTANT_DAYS:
            return {
                "level": "02_Important",
                "reason": f"Non touché depuis {age_days} jours (< 6 mois)",
                "age_days": age_days,
            }
        return {
            "level": "03_Archive",
            "reason": f"Non touché depuis {age_days} jours (> 6 mois)",
            "age_days": age_days,
        }

    # ── Fiabilité du "dernier accès" (limite Windows — voir en-tête) ─
    @staticmethod
    def _atime_tracking_reliable(sample_stats: List[os.stat_result]) -> bool:
        """Échantillonne quelques fichiers (stat déjà récupéré, pas de
        nouvel appel disque) : si st_atime == st_mtime pour (quasi) tous,
        le suivi du dernier accès est probablement désactivé (comportement
        par défaut de Windows depuis Vista)."""
        if not sample_stats:
            return True
        identical = sum(1 for st in sample_stats if abs(st.st_atime - st.st_mtime) < 1)
        return (identical / len(sample_stats)) < 0.9  # <90% identiques → suivi probablement actif

    def find_least_used_files(
        self, dir_path: str, unused_since_days: int = 180, top_n: int = 100,
        excluded_dirs: Optional[List[Path]] = None,
    ) -> Dict:
        """Retourne les fichiers non utilisés depuis N jours.
        Combine 2 signaux : date de modification/accès (SOLUTION B : parcours
        performant via os.scandir) + Éléments récents Windows quand
        disponibles (SOLUTION A : signal d'usage réel, voir RecentItemsTracker)."""
        root = Path(dir_path)
        if not root.exists():
            return {"files": [], "atime_reliable": True, "note": "Dossier introuvable", "recent_items_count": 0}

        # Échantillon rapide (50 premiers fichiers rencontrés, sans
        # matérialiser toute l'arborescence) pour juger la fiabilité atime.
        sample_stats = [
            st for _, st in itertools.islice(_scan_files(root), 50)
        ]
        atime_reliable = self._atime_tracking_reliable(sample_stats)
        recent_index = self._get_recent_usage_index()

        now = time.time()
        excluded_dirs = excluded_dirs or []
        candidates = []
        for f, st in _scan_files(root):
            if self._is_never_move(f):
                continue
            # Même exclusion que build_plan() : sans elle, pointer cette
            # fonction sur le dossier d'installation rangeait les dossiers
            # internes de l'outil (quarantaine, staging, journaux).
            if any(f.resolve() == ex or ex in f.resolve().parents for ex in excluded_dirs):
                continue
            last_used = max(st.st_atime, st.st_mtime) if atime_reliable else st.st_mtime
            if recent_index:
                key = str(f.resolve()).lower()
                if key in recent_index:
                    last_used = max(last_used, recent_index[key])
            age_days = int((now - last_used) / 86400)
            if age_days >= unused_since_days:
                candidates.append({
                    "path": str(f),
                    "size_mb": round(st.st_size / (1024 * 1024), 2),
                    "age_days": age_days,
                })

        candidates.sort(key=lambda x: x["age_days"], reverse=True)

        if recent_index:
            note = (
                f"Mesure combinée : date de modification + Éléments récents Windows "
                f"({len(recent_index)} fichier(s) ouverts récemment détectés) — signal "
                f"d'usage réel, fiable même si le suivi NTFS du dernier accès est désactivé."
            )
        elif atime_reliable:
            note = "Mesure basée sur la date de dernier ACCÈS (fiable)."
        else:
            note = (
                "⚠️ Le suivi du dernier accès semble désactivé sur ce disque "
                "(comportement par défaut de Windows) et pywin32 n'est pas "
                "installé (pip install pywin32) pour croiser avec les Éléments "
                "récents — cette liste est basée sur la date de dernière "
                "MODIFICATION, pas de consultation réelle. Un fichier souvent "
                "relu mais jamais modifié peut donc apparaître ici à tort."
            )
        return {
            "files": candidates[:top_n],
            "atime_reliable": atime_reliable,
            "recent_items_count": len(recent_index),
            "note": note,
        }

    # ── Utilitaires internes ──────────────────────────────────────
    @staticmethod
    def _is_never_move(file_path: Path) -> bool:
        return file_path.suffix.lower() in NEVER_MOVE_EXTENSIONS

    @staticmethod
    def _is_never_enter(dir_path: Path) -> bool:
        lowered = str(dir_path).lower()
        return any(kw in lowered for kw in NEVER_ENTER_FOLDER_KEYWORDS)

    @staticmethod
    def _unique_destination(destination: Path) -> Path:
        """Évite d'écraser un fichier existant : ajoute ' (2)', ' (3)'... """
        if not destination.exists():
            return destination
        stem, suffix, parent = destination.stem, destination.suffix, destination.parent
        counter = 2
        while True:
            candidate = parent / f"{stem} ({counter}){suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    # ── Construction de plan (dry-run obligatoire avant toute action) ─
    def build_plan(
        self, dir_path: str, mode: str, excluded_dirs: Optional[List[Path]] = None
    ) -> List[Dict]:
        """
        mode : 'category' (par type de fichier), 'application' (par appli),
        ou 'importance' (par fraîcheur d'utilisation).
        Ne modifie RIEN — retourne uniquement la liste des déplacements
        proposés, à valider avant apply_plan().
        """
        root = Path(dir_path)
        if not root.exists():
            return []
        excluded_dirs = excluded_dirs or []

        plan = []
        for f, st in _scan_files(root):
            if self._is_never_move(f):
                continue
            if any(f.resolve() == ex or ex in f.resolve().parents for ex in excluded_dirs):
                continue
            # Ne pas re-traiter un fichier déjà rangé dans un dossier généré
            # par un run précédent (préfixe "NN_" strict) — idempotence.
            if any(_GENERATED_FOLDER_PATTERN.match(part) for part in f.relative_to(root).parts[:-1]):
                continue

            if mode == "category":
                label = self.classify_category(f)
                reason = f"Catégorie : {label[3:].replace('_', ' ')}"
            elif mode == "application":
                label = self.classify_application(f)
                reason = f"Application associée : {label}"
            elif mode == "importance":
                classification = self.classify_importance(f, stat_result=st)
                label = classification["level"]
                reason = classification["reason"]
            else:
                raise ValueError(f"Mode inconnu : {mode}")

            destination_dir = root / label
            destination = self._unique_destination(destination_dir / f.name)
            if destination == f:
                continue

            plan.append({
                "source": str(f),
                "destination": str(destination),
                "label": label,
                "reason": reason,
            })
        return plan

    def apply_plan(self, plan: List[Dict]) -> Dict:
        """Exécute un plan préalablement construit et validé par l'utilisateur.
        Chaque déplacement est journalisé individuellement pour undo_session()."""
        session_id = str(uuid.uuid4())
        moved, errors = 0, []

        for item in plan:
            source = Path(item["source"])
            destination = Path(item["destination"])
            if not source.exists():
                errors.append(f"{source} : introuvable (déjà déplacé ?)")
                continue
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
                self._record_move(session_id, str(source), str(destination), "file")
                moved += 1
            except (PermissionError, OSError) as e:
                errors.append(f"{source} : {e}")

        return {"session_id": session_id, "moved": moved, "errors": errors}

    def organize_least_used(
        self, dir_path: str, unused_since_days: int = 180,
        destination_folder_name: str = "00_Non_utilises_depuis_longtemps",
        excluded_dirs: Optional[List[Path]] = None,
    ) -> Dict:
        """Construit ET applique en une fois le rangement des fichiers les
        moins utilisés (le menu affiche toujours la liste avant confirmation —
        voir main.py)."""
        result = self.find_least_used_files(
            dir_path, unused_since_days, excluded_dirs=excluded_dirs
        )
        root = Path(dir_path)
        plan = []
        for f in result["files"]:
            source = Path(f["path"])
            relative = source.relative_to(root)
            destination = self._unique_destination(root / destination_folder_name / relative)
            plan.append({"source": str(source), "destination": str(destination),
                         "label": destination_folder_name, "reason": f"{f['age_days']} jours sans usage"})
        report = self.apply_plan(plan)
        report["atime_reliable"] = result["atime_reliable"]
        report["note"] = result["note"]
        return report

    # ── Déplacement d'un dossier entier dans un autre ────────────
    def move_folder_into(self, source_folder: str, target_parent_folder: str) -> Dict:
        """
        Déplace le dossier `source_folder` À L'INTÉRIEUR de
        `target_parent_folder` (ex: déplacer 'D:\\Projets\\ClientX' dans
        'D:\\Archives' → résultat 'D:\\Archives\\ClientX').
        Gère les conflits de nom (dossier de même nom déjà présent à la
        destination) en fusionnant fichier par fichier, avec renommage
        automatique en cas de collision de nom de fichier.
        """
        source = Path(source_folder)
        target_parent = Path(target_parent_folder)

        if not source.is_dir():
            return {"status": "erreur", "message": f"Dossier source introuvable : {source}"}
        if not target_parent.is_dir():
            return {"status": "erreur", "message": f"Dossier cible introuvable : {target_parent}"}
        if target_parent.resolve() in source.resolve().parents or target_parent.resolve() == source.resolve():
            return {"status": "erreur", "message": "Impossible de déplacer un dossier dans lui-même/un de ses sous-dossiers."}

        session_id = str(uuid.uuid4())
        destination = target_parent / source.name

        # Cas simple : pas de conflit de nom → déplacement direct du dossier entier
        if not destination.exists():
            try:
                shutil.move(str(source), str(destination))
                self._record_move(session_id, str(source), str(destination), "folder")
                return {"status": "ok", "session_id": session_id, "message": f"Déplacé vers {destination}", "merged": False}
            except (PermissionError, OSError) as e:
                return {"status": "erreur", "message": str(e)}

        # Cas conflit : un dossier du même nom existe déjà à la destination
        # → fusion fichier par fichier plutôt qu'écrasement.
        #
        # Un seul parcours os.walk(topdown=False) au lieu de 2 parcours
        # rglob("*") séparés (l'un pour déplacer les fichiers, l'autre
        # ensuite pour nettoyer les dossiers vides, avec un tri par
        # profondeur O(n log n) pour garantir l'ordre feuilles→racine).
        # topdown=False fournit DÉJÀ cette garantie d'ordre nativement
        # (un dossier n'est visité qu'après tous ses fichiers et
        # sous-dossiers) : le rmdir() peut donc se faire dans la même
        # boucle, en toute sécurité, sans second parcours ni tri.
        moved, errors = 0, []
        for dirpath, _dirnames, filenames in os.walk(source, topdown=False):
            current_dir = Path(dirpath)
            for fname in filenames:
                item = current_dir / fname
                relative = item.relative_to(source)
                dest_file = self._unique_destination(destination / relative)
                try:
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(item), str(dest_file))
                    self._record_move(session_id, str(item), str(dest_file), "file")
                    moved += 1
                except (PermissionError, OSError) as e:
                    errors.append(f"{item} : {e}")

            if current_dir != source:
                try:
                    current_dir.rmdir()
                except OSError:
                    pass  # non vide (erreur de déplacement) ou verrouillé — laissé en place

        try:
            source.rmdir()
        except OSError:
            pass

        return {
            "status": "ok", "session_id": session_id, "merged": True,
            "message": f"Un dossier '{source.name}' existait déjà dans la cible — fusion fichier par fichier effectuée.",
            "moved": moved, "errors": errors,
        }
