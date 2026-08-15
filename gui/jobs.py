"""
gui/jobs.py — Gestionnaire de tâches asynchrones pour l'interface web.

Les opérations longues du contrat d'API (scan de dossier, analyse disque,
triage, inventaire des applications, passe du Mode Gardien) ne peuvent pas
bloquer le thread HTTP : elles sont lancées ici dans un thread dédié et
suivies via `GET /api/job?id=<job_id>`.

Stdlib uniquement (threading, uuid, time) — aucune dépendance nouvelle.

Modèle :
    job_id = manager.submit("scan_directory", worker)
    worker(job) reçoit un objet Job et peut :
        job.set_total(n)        → borne la progression
        job.step("fichier.txt") → +1 traité, met à jour `current`
        job.check_cancel()      → lève JobCancelled si annulation demandée

L'état exposé respecte exactement la forme du contrat :
    {state, progress, current, done, total, result, error}
"""

import threading
import time
import uuid
from typing import Any, Callable, Dict, Optional

# Durée de conservation d'un job terminé avant nettoyage automatique.
JOB_RETENTION_SECONDS = 3600
MAX_JOBS = 200


class JobCancelled(Exception):
    """Levée dans le worker quand une annulation a été demandée."""


class Job:
    """État d'une tâche asynchrone. Toutes les mutations passent par un
    verrou : le worker écrit depuis son thread, le handler HTTP lit
    depuis un autre."""

    def __init__(self, job_id: str, name: str):
        self.id = job_id
        self.name = name
        self._lock = threading.Lock()
        self._cancel = threading.Event()

        self.state = "running"     # running | done | error
        self.current = ""
        self.done = 0
        self.total = 0
        self.result: Any = None
        self.error: Optional[str] = None
        self.cancelled = False
        self.created_at = time.time()
        self.finished_at: Optional[float] = None

    # ── API destinée au worker ───────────────────────────────────
    def set_total(self, total: int) -> None:
        with self._lock:
            self.total = max(0, int(total))

    def set_current(self, current: str) -> None:
        with self._lock:
            self.current = str(current)

    def step(self, current: str = "", increment: int = 1) -> None:
        """Marque `increment` élément(s) traité(s)."""
        with self._lock:
            self.done += increment
            if current:
                self.current = str(current)

    @property
    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def check_cancel(self) -> None:
        """À appeler régulièrement dans le worker : interrompt le travail
        proprement dès qu'une annulation a été demandée."""
        if self._cancel.is_set():
            raise JobCancelled("Tâche annulée par l'utilisateur.")

    # ── API destinée au manager / au serveur ─────────────────────
    def request_cancel(self) -> None:
        self._cancel.set()

    def snapshot(self) -> Dict:
        """Vue JSON-sérialisable, conforme au contrat d'API."""
        with self._lock:
            if self.total > 0:
                progress = min(1.0, self.done / self.total)
            else:
                # Progression indéterminée : 0.0 tant que ça tourne, 1.0 à la fin.
                progress = 1.0 if self.state == "done" else 0.0
            if self.state == "done":
                progress = 1.0
            return {
                "job_id": self.id,
                "name": self.name,
                "state": self.state,
                "progress": round(progress, 4),
                "current": self.current,
                "done": self.done,
                "total": self.total,
                "result": self.result,
                "error": self.error,
                "cancelled": self.cancelled,
            }


class JobManager:
    """Registre des jobs + lancement des threads workers."""

    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, name: str, worker: Callable[[Job], Any]) -> str:
        """Lance `worker(job)` dans un thread démon et retourne le job_id."""
        self._prune()
        job = Job(str(uuid.uuid4()), name)
        with self._lock:
            self._jobs[job.id] = job

        def _run() -> None:
            try:
                result = worker(job)
                with job._lock:
                    # Une annulation peut arriver juste avant la fin.
                    if job._cancel.is_set():
                        job.state = "error"
                        job.error = "Tâche annulée par l'utilisateur."
                        job.cancelled = True
                    else:
                        job.result = result
                        job.state = "done"
                        if job.total == 0:
                            job.total = job.done
            except JobCancelled as exc:
                with job._lock:
                    job.state = "error"
                    job.error = str(exc)
                    job.cancelled = True
            except Exception as exc:  # noqa: BLE001 — un job ne doit jamais tuer le serveur
                with job._lock:
                    job.state = "error"
                    job.error = f"{type(exc).__name__}: {exc}"
            finally:
                job.finished_at = time.time()

        threading.Thread(target=_run, name=f"az-job-{name}", daemon=True).start()
        return job.id

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def snapshot(self, job_id: str) -> Optional[Dict]:
        job = self.get(job_id)
        return job.snapshot() if job else None

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None:
            return False
        job.request_cancel()
        return True

    def list_jobs(self) -> list:
        with self._lock:
            jobs = list(self._jobs.values())
        return [j.snapshot() for j in jobs]

    def _prune(self) -> None:
        """Évite une croissance mémoire non bornée : purge les jobs
        terminés depuis longtemps, puis les plus anciens si dépassement."""
        now = time.time()
        with self._lock:
            expired = [
                jid for jid, j in self._jobs.items()
                if j.finished_at is not None and now - j.finished_at > JOB_RETENTION_SECONDS
            ]
            for jid in expired:
                del self._jobs[jid]

            if len(self._jobs) > MAX_JOBS:
                finished = sorted(
                    (j for j in self._jobs.values() if j.finished_at is not None),
                    key=lambda j: j.finished_at or 0,
                )
                for j in finished[: len(self._jobs) - MAX_JOBS]:
                    self._jobs.pop(j.id, None)
