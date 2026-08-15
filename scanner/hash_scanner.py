"""
hash_scanner.py
Détection par signature : compare l'empreinte SHA-256 de chaque fichier
à une base locale de hashes malveillants connus.

Bonne pratique : cette base doit être alimentée par des flux de threat
intelligence réels (ex. MalwareBazaar, VirusTotal, Abuse.ch) et mise à
jour régulièrement — voir README.md pour les sources recommandées.
"""

import hashlib
from pathlib import Path
from typing import Optional, Set


class HashScanner:
    def __init__(self, hash_db_path: str):
        self.hash_db_path = Path(hash_db_path)
        self.known_malicious_hashes: Set[str] = set()
        self._load_hash_database()

    def _load_hash_database(self) -> None:
        """Charge la base de hashes malveillants (un hash SHA-256 par ligne)."""
        if not self.hash_db_path.exists():
            self.hash_db_path.parent.mkdir(parents=True, exist_ok=True)
            self.hash_db_path.write_text("", encoding="utf-8")

        with open(self.hash_db_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip().lower()
                if line and not line.startswith("#"):
                    self.known_malicious_hashes.add(line)

    def reload(self) -> None:
        """Recharge la base après une mise à jour (ex: téléchargement d'un nouveau flux)."""
        self.known_malicious_hashes.clear()
        self._load_hash_database()

    @staticmethod
    def compute_sha256(file_path: str, chunk_size: int = 65536) -> Optional[str]:
        """Calcule le SHA-256 d'un fichier par lecture en flux (évite de charger
        des gros fichiers entièrement en mémoire)."""
        sha256 = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                while chunk := f.read(chunk_size):
                    sha256.update(chunk)
            return sha256.hexdigest()
        except (PermissionError, FileNotFoundError, OSError):
            return None

    def scan_file(self, file_path: str) -> dict:
        """
        Analyse un fichier unique.
        Retourne un dict : {clean: bool, hash: str|None, matched: bool, reason: str}
        """
        file_hash = self.compute_sha256(file_path)
        if file_hash is None:
            return {
                "clean": True,
                "hash": None,
                "matched": False,
                "reason": "Fichier illisible (verrouillé ou inaccessible) — non scanné",
            }

        matched = file_hash in self.known_malicious_hashes
        return {
            "clean": not matched,
            "hash": file_hash,
            "matched": matched,
            "reason": "Signature connue détectée" if matched else "Aucune correspondance",
        }

    def add_malicious_hash(self, sha256_hash: str) -> None:
        """Ajoute manuellement un hash à la base locale (ex: après analyse manuelle)."""
        sha256_hash = sha256_hash.strip().lower()
        if sha256_hash not in self.known_malicious_hashes:
            self.known_malicious_hashes.add(sha256_hash)
            with open(self.hash_db_path, "a", encoding="utf-8") as f:
                f.write(sha256_hash + "\n")
