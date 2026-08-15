"""
file_triage.py
Classe les fichiers d'un dossier en 3 catégories de risque et propose
leur suppression avec confirmation obligatoire de l'utilisateur.

Principe de sécurité central : AUCUNE suppression définitive directe.
Les fichiers validés pour suppression sont d'abord déplacés vers un
dossier tampon ("staging") — récupérables tant qu'ils n'en sont pas
purgés explicitement. Même logique que le quarantine_manager, appliquée
ici au nettoyage plutôt qu'à la détection de menaces.

Catégories de risque :
- NEVER_TOUCH  : documents, photos, code source, dossiers système —
                 jamais proposé à la suppression, même pas affiché en option
- CAUTION      : vieux installeurs, gros fichiers non touchés depuis longtemps,
                 doublons — proposé mais avec avertissement explicite
- SAFE         : fichiers temporaires, logs, caches, fichiers .bak/.old —
                 recommandé à la suppression mais confirmation quand même requise
"""

import json
import os
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List

# Extensions qu'on ne propose JAMAIS à la suppression, quel que soit le contexte
NEVER_TOUCH_EXTENSIONS = {
    # Documents / bureautique
    ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".pdf", ".odt", ".ods",
    # Médias personnels
    ".jpg", ".jpeg", ".png", ".heic", ".mp4", ".mov", ".mp3", ".wav",
    # Code source / projets
    ".py", ".js", ".ts", ".java", ".cpp", ".c", ".cs", ".php", ".html", ".css",
    # Système / exécutables critiques
    ".dll", ".sys", ".ini", ".config",
}

NEVER_TOUCH_FOLDER_KEYWORDS = [
    "windows", "program files", "programdata", "documents", "desktop",
    "pictures", "videos", "music", "onedrive",
]

# Extensions sûres à nettoyer (résidus techniques sans valeur pour l'utilisateur)
SAFE_EXTENSIONS = {".tmp", ".temp", ".log", ".bak", ".old", ".dmp", ".chk", ".~"}

# Installeurs — jamais supprimés automatiquement, toujours en CAUTION
INSTALLER_EXTENSIONS = {".exe", ".msi"}

CAUTION_AGE_DAYS = 180  # fichier non modifié depuis 6 mois → candidat "à vérifier"


class FileTriage:
    def __init__(self, staging_dir: str):
        self.staging_dir = Path(staging_dir)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.staging_index_path = self.staging_dir / "staging_index.json"
        # Même raison que QuarantineManager : évite une race condition si
        # deux opérations touchent l'index en même temps.
        self._lock = threading.Lock()

    def _load_index(self) -> List[Dict]:
        if not self.staging_index_path.exists():
            return []
        return json.loads(self.staging_index_path.read_text(encoding="utf-8"))

    def _save_index(self, index: List[Dict]) -> None:
        """Écriture atomique (temp file + os.replace) — voir QuarantineManager
        pour le détail du raisonnement."""
        tmp_path = self.staging_index_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_path, self.staging_index_path)

    # ── Classification ───────────────────────────────────────────
    @staticmethod
    def _is_never_touch(file_path: Path) -> bool:
        ext = file_path.suffix.lower()
        if ext in NEVER_TOUCH_EXTENSIONS:
            return True
        path_lower = str(file_path).lower()
        return any(kw in path_lower for kw in NEVER_TOUCH_FOLDER_KEYWORDS)

    def classify_file(self, file_path: Path) -> Dict:
        """Retourne la classification d'un fichier : never_touch / caution / safe / neutral."""
        try:
            stat = file_path.stat()
        except (PermissionError, OSError):
            return {"category": "neutral", "reason": "Fichier illisible", "size_mb": 0}

        size_mb = round(stat.st_size / (1024 * 1024), 2)
        age_days = int((time.time() - stat.st_mtime) / 86400)
        ext = file_path.suffix.lower()

        if self._is_never_touch(file_path):
            return {"category": "never_touch", "reason": "Fichier personnel/système", "size_mb": size_mb}

        if ext in SAFE_EXTENSIONS:
            return {
                "category": "safe",
                "reason": f"Fichier technique temporaire ({ext}), sans valeur pour l'utilisateur",
                "size_mb": size_mb,
                "age_days": age_days,
            }

        if ext in INSTALLER_EXTENSIONS and age_days > CAUTION_AGE_DAYS:
            return {
                "category": "caution",
                "reason": f"Installeur ({ext}) non modifié depuis {age_days} jours — "
                          f"probablement déjà installé, mais vérifie avant de supprimer",
                "size_mb": size_mb,
                "age_days": age_days,
            }

        if size_mb > 100 and age_days > CAUTION_AGE_DAYS:
            return {
                "category": "caution",
                "reason": f"Gros fichier ({size_mb} Mo) non modifié depuis {age_days} jours",
                "size_mb": size_mb,
                "age_days": age_days,
            }

        return {"category": "neutral", "reason": "Aucune règle de nettoyage applicable", "size_mb": size_mb}

    def triage_directory(self, dir_path: str, include_duplicates: List[Dict] = None) -> Dict:
        """
        Scanne un dossier et retourne les candidats classés par catégorie.
        include_duplicates : résultat optionnel de DiskAnalyzer.find_duplicate_files()
        pour intégrer les doublons dans la catégorie 'caution'.
        """
        root = Path(dir_path)
        candidates = {"safe": [], "caution": [], "never_touch_count": 0, "neutral_count": 0}

        if not root.exists():
            return candidates

        for f in root.rglob("*"):
            if not f.is_file():
                continue
            classification = self.classify_file(f)
            category = classification["category"]

            if category == "never_touch":
                candidates["never_touch_count"] += 1
            elif category == "neutral":
                candidates["neutral_count"] += 1
            else:
                candidates[category].append({"path": str(f), **classification})

        if include_duplicates:
            for dup in include_duplicates:
                # Garde toujours la 1ère copie, propose les suivantes en "caution"
                for extra_path in dup["paths"][1:]:
                    candidates["caution"].append({
                        "path": extra_path,
                        "category": "caution",
                        "reason": f"Copie en double (original conservé : {dup['paths'][0]})",
                        "size_mb": dup["size_mb"],
                    })

        return candidates

    # ── Suppression avec filet de sécurité ───────────────────────
    def move_to_staging(self, file_path: str, reason: str) -> str:
        """Déplace un fichier vers le dossier tampon plutôt que de le
        supprimer directement — récupérable via restore_from_staging()."""
        source = Path(file_path)
        if not source.exists():
            return ""

        staging_id = str(uuid.uuid4())
        destination = self.staging_dir / f"{staging_id}_{source.name}"

        try:
            shutil.move(str(source), str(destination))
        except (PermissionError, OSError) as e:
            print(f"[ERREUR] Impossible de déplacer {file_path} : {e}")
            return ""

        with self._lock:
            index = self._load_index()
            index.append({
                "id": staging_id,
                "original_path": str(source.resolve()),
                "staged_name": destination.name,
                "date": datetime.now().isoformat(),
                "reason": reason,
            })
            self._save_index(index)
        return staging_id

    def purge_staging(self, older_than_days: int = 30) -> int:
        """Supprime DÉFINITIVEMENT les fichiers/dossiers du tampon plus
        vieux que N jours. À lancer manuellement, jamais automatiquement."""
        with self._lock:
            index = self._load_index()
            remaining = []
            purged = 0
            now = datetime.now()

            for entry in index:
                entry_date = datetime.fromisoformat(entry["date"])
                age_days = (now - entry_date).days
                staged_item = self.staging_dir / entry["staged_name"]

                if age_days > older_than_days:
                    if staged_item.is_dir():
                        shutil.rmtree(staged_item, ignore_errors=True)
                    elif staged_item.exists():
                        staged_item.unlink()
                    purged += 1
                else:
                    remaining.append(entry)

            self._save_index(remaining)
            return purged

    def list_staging(self) -> List[Dict]:
        with self._lock:
            return self._load_index()

    def restore_from_staging(self, staging_id: str) -> bool:
        with self._lock:
            index = self._load_index()
            for entry in index:
                if entry["id"] == staging_id:
                    staged_file = self.staging_dir / entry["staged_name"]
                    original = Path(entry["original_path"])
                    try:
                        original.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(staged_file), str(original))
                        index.remove(entry)
                        self._save_index(index)
                        return True
                    except (PermissionError, OSError):
                        return False
            return False
