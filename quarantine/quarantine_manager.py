"""
quarantine_manager.py
Isole les fichiers détectés comme malveillants dans un dossier de
quarantaine chiffré/renommé, avec conservation des métadonnées pour
permettre une restauration si un résultat s'avère être un faux positif.

Bonne pratique : les fichiers en quarantaine sont renommés (extension
neutralisée) pour empêcher toute exécution accidentelle par double-clic.
"""

import json
import os
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


class QuarantineManager:
    def __init__(self, quarantine_dir: str):
        self.quarantine_dir = Path(quarantine_dir)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_file = self.quarantine_dir / "quarantine_index.json"
        # Protège l'index JSON contre les accès concurrents : la protection
        # temps réel (thread watchdog) et un scan manuel lancé depuis le menu
        # peuvent tous les deux appeler quarantine_file() en même temps.
        # Sans ce verrou, un classique read-modify-write race condition peut
        # faire perdre silencieusement une entrée de quarantaine.
        self._lock = threading.Lock()
        self._ensure_metadata_file()

    def _ensure_metadata_file(self) -> None:
        if not self.metadata_file.exists():
            self.metadata_file.write_text("[]", encoding="utf-8")

    def _load_index(self) -> List[Dict]:
        with open(self.metadata_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_index(self, index: List[Dict]) -> None:
        """Écriture atomique : on écrit d'abord dans un fichier temporaire
        puis on le renomme par-dessus l'original (os.replace est atomique
        sur Windows comme sur POSIX). Évite un index corrompu/tronqué si
        le processus est interrompu (crash, coupure) en plein milieu de
        l'écriture."""
        tmp_path = self.metadata_file.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, self.metadata_file)

    def quarantine_file(self, file_path: str, reason: str, detection_details: Dict) -> Optional[str]:
        """
        Déplace un fichier suspect en quarantaine.
        Retourne l'ID de quarantaine, ou None si l'opération échoue.
        """
        source = Path(file_path)
        if not source.exists():
            return None

        quarantine_id = str(uuid.uuid4())
        # Neutralise l'extension pour empêcher toute exécution accidentelle
        quarantined_name = f"{quarantine_id}.quarantined"
        destination = self.quarantine_dir / quarantined_name

        try:
            shutil.move(str(source), str(destination))
        except (PermissionError, OSError) as e:
            print(f"[ERREUR] Impossible de mettre en quarantaine {file_path} : {e}")
            return None

        with self._lock:
            index = self._load_index()
            index.append({
                "id": quarantine_id,
                "original_path": str(source.resolve()),
                "quarantined_name": quarantined_name,
                "quarantine_date": datetime.now().isoformat(),
                "reason": reason,
                "detection_details": detection_details,
                "restored": False,
            })
            self._save_index(index)
        return quarantine_id

    def list_quarantined(self) -> List[Dict]:
        """Liste tous les fichiers actuellement en quarantaine (non restaurés)."""
        with self._lock:
            return [entry for entry in self._load_index() if not entry["restored"]]

    @staticmethod
    def _unique_destination(destination: Path) -> Path:
        """Évite d'écraser un fichier existant : ajoute ' (2)', ' (3)'...

        Même convention que FolderOrganizer._unique_destination(), pour que le
        comportement en cas de collision soit identique dans tout le projet.
        """
        if not destination.exists():
            return destination
        stem, suffix, parent = destination.stem, destination.suffix, destination.parent
        counter = 2
        while True:
            candidate = parent / f"{stem} ({counter}){suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    def restore_file(self, quarantine_id: str) -> bool:
        """Restaure un fichier vers son emplacement d'origine (cas de faux positif)."""
        with self._lock:
            index = self._load_index()
            for entry in index:
                if entry["id"] == quarantine_id and not entry["restored"]:
                    quarantined_path = self.quarantine_dir / entry["quarantined_name"]
                    original_path = Path(entry["original_path"])
                    try:
                        original_path.parent.mkdir(parents=True, exist_ok=True)
                        # Un fichier a pu être recréé au chemin d'origine depuis la mise en
                        # quarantaine. shutil.move() l'écraserait silencieusement : on restaure
                        # alors à côté, sous un nom libre, plutôt que de détruire cette donnée.
                        # Même convention de suffixe que FolderOrganizer._unique_destination().
                        destination = self._unique_destination(original_path)
                        shutil.move(str(quarantined_path), str(destination))
                        if destination != original_path:
                            entry["restored_to"] = str(destination)
                            print(
                                f"[INFO] Un fichier existait déjà en {original_path} — "
                                f"restauration effectuée sous {destination.name}"
                            )
                        entry["restored"] = True
                        entry["restore_date"] = datetime.now().isoformat()
                        self._save_index(index)
                        return True
                    except (PermissionError, OSError) as e:
                        print(f"[ERREUR] Restauration échouée : {e}")
                        return False
            return False

    def delete_permanently(self, quarantine_id: str) -> bool:
        """Supprime définitivement un fichier en quarantaine."""
        with self._lock:
            index = self._load_index()
            for entry in index:
                if entry["id"] == quarantine_id:
                    quarantined_path = self.quarantine_dir / entry["quarantined_name"]
                    if quarantined_path.exists():
                        quarantined_path.unlink()
                    index.remove(entry)
                    self._save_index(index)
                    return True
            return False
