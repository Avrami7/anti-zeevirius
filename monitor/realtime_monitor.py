"""
realtime_monitor.py
Surveillance temps réel des dossiers sensibles (Downloads, Desktop, etc.)
Déclenche un scan automatique dès qu'un fichier est créé ou modifié.

Limite importante à connaître (transparence) : ceci est une surveillance
en espace utilisateur (user-space), pas un driver kernel-mode comme les
vrais antivirus professionnels (Defender, Kaspersky...). Elle ne peut
donc pas intercepter un malware AVANT sa première écriture sur le disque,
contrairement à un filtre de système de fichiers kernel. Elle reste
néanmoins efficace contre la grande majorité des menaces courantes
(téléchargements, pièces jointes, clés USB).

Installation requise : pip install watchdog
"""

import threading
import time
from pathlib import Path
from typing import Callable, List

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent


class ScanEventHandler(FileSystemEventHandler):
    def __init__(self, scan_callback: Callable[[str], None]):
        super().__init__()
        self.scan_callback = scan_callback
        # Petite déduplication : évite de scanner 3x le même fichier
        # quand Windows déclenche plusieurs événements pour une seule copie
        self._recent_events = {}
        self._debounce_seconds = 2

    def _should_process(self, path: str) -> bool:
        now = time.time()
        last_seen = self._recent_events.get(path, 0)
        self._recent_events[path] = now
        return (now - last_seen) > self._debounce_seconds

    def purge_stale_entries(self, max_age_seconds: float = 300) -> None:
        """Retire les entrées trop anciennes de la table de déduplication.
        Sans cela, un dossier avec beaucoup de fichiers différents créés/modifiés
        au fil du temps (ex: Téléchargements sur plusieurs semaines) fait grossir
        _recent_events indéfiniment — fuite mémoire lente sur une surveillance
        de longue durée."""
        now = time.time()
        stale = [p for p, t in self._recent_events.items() if (now - t) > max_age_seconds]
        for p in stale:
            del self._recent_events[p]

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory and self._should_process(event.src_path):
            # Petit délai pour laisser le temps au fichier d'être
            # complètement écrit sur le disque avant de le scanner
            time.sleep(0.5)
            self.scan_callback(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory and self._should_process(event.src_path):
            self.scan_callback(event.src_path)


class RealtimeMonitor:
    def __init__(self, watched_folders: List[str], scan_callback: Callable[[str], None]):
        self.watched_folders = [f for f in watched_folders if Path(f).exists()]
        self.scan_callback = scan_callback
        self.observer = Observer()
        self._handler = ScanEventHandler(scan_callback)
        # Event plutôt qu'un simple `while True` : permet un arrêt propre
        # depuis un AUTRE thread (stop() appelé depuis le thread principal
        # pendant que start() tourne dans un thread daemon en arrière-plan),
        # sans dépendre uniquement de KeyboardInterrupt.
        self._stop_event = threading.Event()

    def start(self) -> None:
        if not self.watched_folders:
            print("[AVERTISSEMENT] Aucun dossier valide à surveiller.")
            return

        for folder in self.watched_folders:
            self.observer.schedule(self._handler, folder, recursive=True)
            print(f"[SURVEILLANCE] Dossier surveillé : {folder}")

        self.observer.start()
        print("[SURVEILLANCE] Protection temps réel active. Ctrl+C pour arrêter.")

        try:
            while not self._stop_event.is_set():
                time.sleep(1)
                # Purge périodique de la table de déduplication du handler :
                # sans ça, _recent_events grossit indéfiniment (une entrée par
                # chemin de fichier vu) et finit par consommer de la mémoire
                # inutilement sur une surveillance de très longue durée.
                self._handler.purge_stale_entries()
        except KeyboardInterrupt:
            pass
        finally:
            if self.observer.is_alive():
                self._shutdown_observer()

    def stop(self) -> None:
        """Demande l'arrêt. Peut être appelé depuis un autre thread que
        celui qui exécute start()."""
        self._stop_event.set()
        if self.observer.is_alive():
            self._shutdown_observer()

    def _shutdown_observer(self) -> None:
        self.observer.stop()
        self.observer.join()
        print("[SURVEILLANCE] Protection temps réel arrêtée.")
