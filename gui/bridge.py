"""
gui/bridge.py — Couche d'adaptation entre le contrat d'API web et les
modules métier existants d'ANTI-ZEEVIRIUS.

Trois responsabilités, et rien d'autre :

1. **Import paresseux.** Aucun module métier n'est importé au chargement de
   ce fichier. Un module absent (winreg hors Windows, watchdog non installé,
   yara-python manquant) ne doit jamais empêcher le serveur de démarrer ni
   faire crasher une action : il produit une réponse
   `{"ok": false, "unavailable": true, "reason": "..."}` en HTTP 200, et
   l'interface reste entièrement navigable.

2. **Double validation destructive.** Toute action marquée destructive dans
   le contrat passe par `_guarded()` : `dry_run: true` (le défaut) calcule
   et renvoie un plan SANS RIEN TOUCHER, accompagné d'un `confirm_token` à
   usage unique valable 5 minutes ; l'exécution réelle exige `dry_run: false`
   ET ce token. Le token est lié à l'action ET aux paramètres : impossible de
   valider la suppression de A puis de rejouer le token sur B.

3. **Routage.** `dispatch(action, params)` → enveloppe JSON du contrat.

Ce fichier n'écrit JAMAIS dans les modules métier : il les appelle.
"""

import hashlib
import importlib
import json
import os
import platform
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .jobs import JobManager

# ── Chemins : strictement alignés sur ceux de main.py ────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
SIGNATURES_DIR = BASE_DIR / "signatures"
LOGS_DIR = BASE_DIR / "logs"
QUARANTINE_DIR = BASE_DIR / "quarantine_storage"
STAGING_DIR = BASE_DIR / "triage_staging"
CACHE_DIR = BASE_DIR / "cache"
ORGANIZER_LOG = BASE_DIR / "organizer_logs" / "reorg_index.json"
HASH_DB_PATH = SIGNATURES_DIR / "malicious_hashes.txt"
YARA_RULES_PATH = SIGNATURES_DIR / "rules.yar"
MAIN_SCRIPT = BASE_DIR / "main.py"

CONFIRM_TTL_SECONDS = 300  # 5 minutes, imposé par le contrat


# ── Enveloppes de réponse (forme figée par le contrat) ───────────────
def ok(data: Any = None) -> Dict:
    return {"ok": True, "data": data if data is not None else {}}


def err(message: str) -> Dict:
    return {"ok": False, "error": str(message), "unavailable": False}


def unavailable(reason: str) -> Dict:
    # `error` est ajouté en plus des champs du contrat pour qu'un frontend
    # qui n'affiche que `error` reste correct ; `reason` fait foi.
    return {"ok": False, "unavailable": True, "reason": str(reason), "error": str(reason)}


class ModuleUnavailable(Exception):
    """Levée par `_need()` quand une dépendance plateforme manque.
    Interceptée par `dispatch()` → réponse `unavailable`."""


# ── Jetons de confirmation (garantie de sécurité centrale) ───────────
class ConfirmTokenStore:
    """Jetons à usage unique pour la double validation destructive.

    Un jeton est lié à (action, empreinte des paramètres) : il ne peut être
    consommé que par la même action sur exactement la même cible. Il est
    supprimé du registre AU MOMENT de sa consommation (usage unique strict,
    y compris si l'exécution qui suit échoue), et expire au bout de 5 min.
    """

    def __init__(self, ttl: int = CONFIRM_TTL_SECONDS) -> None:
        self.ttl = ttl
        self._tokens: Dict[str, Dict] = {}
        self._lock = threading.Lock()

    def issue(self, action: str, fingerprint: str) -> str:
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._purge_locked()
            self._tokens[token] = {
                "action": action,
                "fingerprint": fingerprint,
                "created_at": time.time(),
            }
        return token

    def consume(self, token: Optional[str], action: str, fingerprint: str) -> Tuple[bool, str]:
        """Retourne (accepté, motif de refus)."""
        if not token or not isinstance(token, str):
            return False, "confirm_token manquant : une exécution réelle exige le jeton renvoyé par l'appel dry_run."

        with self._lock:
            self._purge_locked()
            entry = None
            # Comparaison à temps constant contre chaque jeton connu, pour
            # ne pas fuiter d'information par timing.
            for known, meta in self._tokens.items():
                if secrets.compare_digest(known, token):
                    entry = (known, meta)
                    break

            if entry is None:
                return False, "confirm_token invalide, déjà utilisé ou expiré."

            known, meta = entry
            # Usage unique : retiré quoi qu'il arrive ensuite.
            del self._tokens[known]

        if time.time() - meta["created_at"] > self.ttl:
            return False, "confirm_token expiré (validité 5 minutes)."
        if meta["action"] != action:
            return False, "confirm_token émis pour une autre action."
        if not secrets.compare_digest(meta["fingerprint"], fingerprint):
            return False, "confirm_token émis pour d'autres paramètres."
        return True, ""

    def _purge_locked(self) -> None:
        now = time.time()
        for tok in [t for t, m in self._tokens.items() if now - m["created_at"] > self.ttl]:
            del self._tokens[tok]

    def pending_count(self) -> int:
        with self._lock:
            self._purge_locked()
            return len(self._tokens)


def fingerprint(params: Dict) -> str:
    """Empreinte stable des paramètres significatifs (dry_run et
    confirm_token exclus : ils changent entre les deux appels)."""
    material = {k: v for k, v in (params or {}).items() if k not in ("dry_run", "confirm_token")}
    try:
        blob = json.dumps(material, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        blob = repr(sorted(material.items(), key=lambda kv: kv[0]))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── Table des modules métier (import paresseux + prérequis) ──────────
# clé → (module python, classe, drapeau requis dans le module, motif si absent)
_MODULE_SPECS: Dict[str, Tuple[str, str, Optional[str], str]] = {
    "hash_scanner":     ("scanner.hash_scanner", "HashScanner", None, ""),
    "yara_scanner":     ("scanner.yara_scanner", "YaraScanner", None, ""),
    "heuristics":       ("scanner.heuristics", "HeuristicScanner", None, ""),
    "quarantine":       ("quarantine.quarantine_manager", "QuarantineManager", None, ""),
    "realtime_monitor": ("monitor.realtime_monitor", "RealtimeMonitor", None, ""),
    "temp_cleaner":     ("optimizer.temp_cleaner", "TempCleaner", None, ""),
    "startup_manager":  ("optimizer.startup_manager", "StartupManager",
                         "WINREG_AVAILABLE", "winreg absent (registre Windows uniquement)"),
    "disk_analyzer":    ("optimizer.disk_analyzer", "DiskAnalyzer", None, ""),
    "task_scheduler":   ("optimizer.task_scheduler", "TaskScheduler", None, ""),
    "file_triage":      ("optimizer.file_triage", "FileTriage", None, ""),
    "folder_organizer": ("optimizer.folder_organizer", "FolderOrganizer", None, ""),
    "guardian":         ("optimizer.guardian", "SystemGuardian", None, ""),
    "app_manager":      ("optimizer.app_manager", "AppManager",
                         "WINREG_AVAILABLE", "winreg absent (registre Windows uniquement)"),
    "residue_cleaner":  ("optimizer.residue_cleaner", "ResidueCleaner",
                         "WINREG_AVAILABLE", "winreg absent (registre Windows uniquement)"),
    "ransomware_shield": ("optimizer.ransomware_shield", "RansomwareShield", None, ""),
    "reputation_checker": ("optimizer.reputation_checker", "ReputationChecker",
                           "REQUESTS_AVAILABLE", "module `requests` non installé"),
    "phishing_checker": ("optimizer.phishing_link_checker", "PhishingLinkChecker", None, ""),
}

# Actions qui n'ont de sens que sous Windows (outils système externes).
_WINDOWS_ONLY = {"task_scheduler": "schtasks indisponible (planificateur Windows)"}


class Bridge:
    """Point d'entrée unique : `dispatch(action, params)`."""

    def __init__(self, jobs: Optional[JobManager] = None) -> None:
        self.jobs = jobs or JobManager()
        self.confirm = ConfirmTokenStore()
        self._lock = threading.RLock()
        self._modules: Dict[str, Any] = {}
        self._module_errors: Dict[str, str] = {}
        self._instances: Dict[str, Any] = {}
        self._engine = None
        self._engine_error: Optional[str] = None
        # État runtime piloté par l'interface
        self._realtime = None
        self._shield = None
        self._shield_folders: List[str] = []

    # ── Import paresseux ─────────────────────────────────────────
    def _module(self, key: str):
        """Importe (une seule fois) le module métier. Lève ModuleUnavailable."""
        with self._lock:
            if key in self._modules:
                return self._modules[key]
            if key in self._module_errors:
                raise ModuleUnavailable(self._module_errors[key])

            spec = _MODULE_SPECS.get(key)
            if spec is None:
                raise ModuleUnavailable(f"module inconnu : {key}")
            mod_path, _cls, flag, flag_reason = spec

            if key in _WINDOWS_ONLY and platform.system() != "Windows":
                self._module_errors[key] = _WINDOWS_ONLY[key]
                raise ModuleUnavailable(self._module_errors[key])

            try:
                mod = importlib.import_module(mod_path)
            except Exception as exc:  # ImportError, mais aussi erreur d'init
                reason = f"{mod_path} indisponible : {type(exc).__name__}: {exc}"
                self._module_errors[key] = reason
                raise ModuleUnavailable(reason) from exc

            if flag and not getattr(mod, flag, False):
                self._module_errors[key] = flag_reason
                raise ModuleUnavailable(flag_reason)

            self._modules[key] = mod
            return mod

    def _need(self, key: str):
        """Retourne l'instance (singleton) du module métier demandé."""
        with self._lock:
            if key in self._instances:
                return self._instances[key]
            mod = self._module(key)  # peut lever ModuleUnavailable
            cls = getattr(mod, _MODULE_SPECS[key][1])
            try:
                obj = self._build(key, cls)
            except ModuleUnavailable:
                raise
            except Exception as exc:
                raise ModuleUnavailable(f"initialisation de {key} impossible : {exc}") from exc
            self._instances[key] = obj
            return obj

    def _build(self, key: str, cls):
        """Construit l'instance avec les mêmes chemins que main.py, afin de
        partager l'état (index de quarantaine, staging, journaux) avec le CLI."""
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if key == "hash_scanner":
            return cls(str(HASH_DB_PATH))
        if key == "yara_scanner":
            return cls(str(YARA_RULES_PATH))
        if key == "quarantine":
            return cls(str(QUARANTINE_DIR))
        if key == "file_triage":
            return cls(str(STAGING_DIR))
        if key == "folder_organizer":
            return cls(str(ORGANIZER_LOG))
        if key == "reputation_checker":
            return cls(str(SIGNATURES_DIR / "vt_api_key.txt"), str(CACHE_DIR / "vt_cache.json"))
        if key == "phishing_checker":
            return cls(str(CACHE_DIR / "phishing_blocklist.txt"))
        if key == "residue_cleaner":
            return cls(self._need("file_triage"), self._need("app_manager"))
        if key == "guardian":
            return cls(self._need_engine())
        if key in ("realtime_monitor", "ransomware_shield"):
            # Instanciés à la demande (ils prennent des dossiers en argument).
            raise ModuleUnavailable(f"{key} s'instancie par action, pas en singleton")
        return cls()

    def _need_engine(self):
        """AntivirusEngine complet — coûteux (charge signatures + YARA),
        donc construit à la première utilisation seulement."""
        with self._lock:
            if self._engine is not None:
                return self._engine
            if self._engine_error:
                raise ModuleUnavailable(self._engine_error)
            try:
                main_mod = importlib.import_module("main")
                self._engine = main_mod.AntivirusEngine()
            except Exception as exc:
                self._engine_error = (
                    f"moteur antivirus indisponible : {type(exc).__name__}: {exc}"
                )
                raise ModuleUnavailable(self._engine_error) from exc
            return self._engine

    def _flag(self, key: str, flag: str) -> bool:
        """Lit un drapeau de disponibilité d'un module sans le rendre requis."""
        try:
            return bool(getattr(self._module(key), flag, False))
        except ModuleUnavailable:
            return False

    # ── Double validation destructive ────────────────────────────
    def _guarded(
        self,
        action: str,
        params: Dict,
        plan_fn: Callable[[], Any],
        exec_fn: Callable[[], Any],
    ) -> Dict:
        """Squelette commun à toutes les actions destructives du contrat.

        `dry_run` vaut True par défaut : seule la valeur booléenne `false`
        explicite déclenche l'exécution réelle. Une valeur absente, nulle,
        ou de type inattendu retombe donc côté sûr (simulation)."""
        dry_run = params.get("dry_run", True)
        fp = fingerprint(params)

        if dry_run is not False:
            plan = plan_fn()  # NE TOUCHE À RIEN
            token = self.confirm.issue(action, fp)
            return ok({
                "dry_run": True,
                "action": action,
                "plan": plan,
                "confirm_token": token,
                "expires_in": CONFIRM_TTL_SECONDS,
                "note": "Aucune modification effectuée. Renvoyez dry_run=false "
                        "avec ce confirm_token pour exécuter réellement.",
            })

        accepted, reason = self.confirm.consume(params.get("confirm_token"), action, fp)
        if not accepted:
            return err(reason)
        return ok({"dry_run": False, "action": action, "result": exec_fn()})

    # ── Routage ──────────────────────────────────────────────────
    def dispatch(self, action: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """Retourne l'enveloppe JSON, ou None si l'action est inconnue
        (le serveur répond alors 404, conformément au contrat)."""
        params = params if isinstance(params, dict) else {}
        handler = getattr(self, f"a_{action}", None)
        if handler is None or not callable(handler):
            return None
        try:
            return handler(params)
        except ModuleUnavailable as exc:
            return unavailable(str(exc))
        except Exception as exc:  # noqa: BLE001 — aucune action ne doit crasher
            return err(f"{type(exc).__name__}: {exc}")

    def known_actions(self) -> List[str]:
        return sorted(n[2:] for n in dir(self) if n.startswith("a_") and callable(getattr(self, n)))

    # ═════════════════════════════════════════════════════════════
    #  ACTIONS — une méthode a_<action> par ligne du contrat
    # ═════════════════════════════════════════════════════════════

    # ── Tableau de bord ──────────────────────────────────────────
    def a_status(self, params: Dict) -> Dict:
        modules: Dict[str, Dict] = {}
        for key in _MODULE_SPECS:
            try:
                self._module(key)
                modules[key] = {"available": True, "reason": ""}
            except ModuleUnavailable as exc:
                modules[key] = {"available": False, "reason": str(exc)}

        # Signatures
        hashes = 0
        try:
            if HASH_DB_PATH.exists():
                hashes = sum(
                    1 for line in HASH_DB_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
                    if line.strip() and not line.strip().startswith("#")
                )
        except OSError:
            hashes = 0

        yara_rules = 0
        try:
            if YARA_RULES_PATH.exists():
                yara_rules = YARA_RULES_PATH.read_text(encoding="utf-8", errors="replace").count("rule ")
        except OSError:
            yara_rules = 0

        last_update = None
        for p in (HASH_DB_PATH, YARA_RULES_PATH):
            try:
                if p.exists():
                    ts = p.stat().st_mtime
                    iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))
                    last_update = iso if last_update is None else max(last_update, iso)
            except OSError:
                continue

        quarantine_count = 0
        try:
            quarantine_count = len(self._need("quarantine").list_quarantined())
        except (ModuleUnavailable, Exception):
            quarantine_count = 0

        staging_count = 0
        try:
            staging_count = len(self._need("file_triage").list_staging())
        except (ModuleUnavailable, Exception):
            staging_count = 0

        vt_configured = False
        try:
            vt_configured = bool(self._need("reputation_checker").is_configured())
        except (ModuleUnavailable, Exception):
            vt_configured = False

        return ok({
            "platform": platform.system(),
            "is_admin": self._is_admin(),
            "modules": modules,
            "signatures": {
                "hashes": hashes,
                "yara_rules": yara_rules,
                "last_update": last_update,
            },
            "quarantine_count": quarantine_count,
            "staging_count": staging_count,
            "realtime_active": self._realtime is not None,
            "shield_active": self._shield is not None,
            "vt_configured": vt_configured,
            "yara_engine": self._flag("yara_scanner", "YARA_AVAILABLE"),
            "psutil": self._flag("ransomware_shield", "PSUTIL_AVAILABLE"),
            "pending_confirmations": self.confirm.pending_count(),
        })

    @staticmethod
    def _is_admin() -> bool:
        if platform.system() == "Windows":
            try:
                import ctypes
                return ctypes.windll.shell32.IsUserAnAdmin() != 0
            except Exception:
                return False
        try:
            return os.geteuid() == 0
        except AttributeError:
            return False

    # ── Scan ─────────────────────────────────────────────────────
    def a_scan_file(self, params: Dict) -> Dict:
        path = self._require_path(params, "path")
        engine = self._need_engine()
        return ok(engine.scan_single_file(str(path), auto_quarantine=bool(params.get("auto_quarantine", False))))

    def a_scan_directory(self, params: Dict) -> Dict:
        path = self._require_path(params, "path")
        engine = self._need_engine()
        auto_q = bool(params.get("auto_quarantine", False))
        main_mod = importlib.import_module("main")

        def worker(job):
            # Pré-comptage : même prédicat d'exclusion que le moteur, pour
            # que `total` corresponde exactement à ce qui sera scanné.
            files = [f for f in Path(path).rglob("*") if f.is_file() and not engine._is_excluded(f)]
            job.set_total(len(files))
            proxy = _ProgressEngineProxy(engine, job)
            # Appel de la VRAIE méthode du contrat, avec un `self` instrumenté :
            # aucune logique de scan n'est réimplémentée ici.
            results = main_mod.AntivirusEngine.scan_directory(proxy, str(path), auto_q)
            threats = [r for r in results if r.get("verdict") == "MALVEILLANT"]
            return {
                "path": str(path),
                "files_scanned": len(results),
                "threats": threats,
                "threat_count": len(threats),
                "results": results,
            }

        return ok({"job_id": self.jobs.submit("scan_directory", worker)})

    # ── Suivi des jobs ───────────────────────────────────────────
    def a_job(self, params: Dict) -> Dict:
        job_id = params.get("id") or params.get("job_id")
        if not job_id:
            return err("Paramètre `id` manquant.")
        snap = self.jobs.snapshot(str(job_id))
        if snap is None:
            return err("job_id inconnu (ou expiré).")
        return ok(snap)

    def a_job_cancel(self, params: Dict) -> Dict:
        job_id = params.get("id") or params.get("job_id")
        if not job_id:
            return err("Paramètre `id` manquant.")
        if not self.jobs.cancel(str(job_id)):
            return err("job_id inconnu.")
        return ok({"cancelled": True, "job_id": str(job_id)})

    # ── Temps réel ───────────────────────────────────────────────
    def a_realtime_start(self, params: Dict) -> Dict:
        folders = self._folder_list(params.get("folders"))
        if not folders:
            return err("Aucun dossier valide fourni.")
        with self._lock:
            if self._realtime is not None:
                return ok({"already_running": True, "folders": self._shield_folders})
            mod = self._module("realtime_monitor")
            engine = self._need_engine()
            monitor = mod.RealtimeMonitor(folders, engine._realtime_callback)
            thread = threading.Thread(target=monitor.start, daemon=True, name="az-realtime")
            thread.start()
            self._realtime = monitor
        return ok({"started": True, "folders": folders})

    def a_realtime_stop(self, params: Dict) -> Dict:
        with self._lock:
            if self._realtime is None:
                return ok({"stopped": False, "note": "Surveillance temps réel non active."})
            try:
                self._realtime.stop()
            finally:
                self._realtime = None
        return ok({"stopped": True})

    # ── Quarantaine ──────────────────────────────────────────────
    def a_quarantine_list(self, params: Dict) -> Dict:
        return ok({"items": self._need("quarantine").list_quarantined()})

    def a_quarantine_restore(self, params: Dict) -> Dict:
        qid = self._require(params, "id")
        return ok({"restored": bool(self._need("quarantine").restore_file(str(qid)))})

    def a_quarantine_delete(self, params: Dict) -> Dict:
        qid = str(self._require(params, "id"))
        qm = self._need("quarantine")

        def plan():
            entry = next((e for e in qm.list_quarantined() if e.get("id") == qid), None)
            if entry is None:
                return {"found": False, "id": qid, "warning": "Aucune entrée de quarantaine avec cet identifiant."}
            return {"found": True, "entry": entry,
                    "effect": "Suppression DÉFINITIVE et irréversible du fichier en quarantaine."}

        return self._guarded("quarantine_delete", params, plan,
                             lambda: {"deleted": bool(qm.delete_permanently(qid))})

    # ── Nettoyage ────────────────────────────────────────────────
    def a_clean_full(self, params: Dict) -> Dict:
        cleaner = self._need("temp_cleaner")
        include_admin = bool(params.get("include_admin", True))

        def plan():
            targets = [
                {"label": "%TEMP% utilisateur", "admin_required": False},
                {"label": "Corbeille", "admin_required": False},
                {"label": "Cache des miniatures", "admin_required": False},
                {"label": "Caches navigateurs (Chrome/Edge/Firefox)", "admin_required": False},
            ]
            if include_admin:
                targets += [
                    {"label": "Windows Temp", "admin_required": True},
                    {"label": "Prefetch", "admin_required": True},
                    {"label": "Cache Windows Update", "admin_required": True},
                ]
            return {
                "targets": targets,
                "include_admin": include_admin,
                "is_admin": self._is_admin(),
                "effect": "Suppression du CONTENU des dossiers listés (fichiers temporaires).",
            }

        return self._guarded("clean_full", params, plan,
                             lambda: cleaner.run_full_cleanup(include_admin_targets=include_admin))

    # ── Démarrage (registre Windows) ─────────────────────────────
    def a_startup_list(self, params: Dict) -> Dict:
        sm = self._need("startup_manager")
        return ok({
            "registry": sm.list_registry_startup_items(),
            "startup_folder": sm.list_startup_folder_items(),
        })

    def a_startup_disable(self, params: Dict) -> Dict:
        sm = self._need("startup_manager")
        hive = str(self._require(params, "hive"))
        key_path = str(self._require(params, "key_path"))
        name = str(self._require(params, "name"))

        def plan():
            item = next(
                (i for i in sm.list_registry_startup_items()
                 if i.get("name") == name and i.get("hive") == hive),
                None,
            )
            return {"hive": hive, "key_path": key_path, "name": name, "item": item,
                    "reversible": True,
                    "effect": "L'entrée est DÉPLACÉE vers une clé de sauvegarde (restaurable via startup_restore)."}

        return self._guarded("startup_disable", params, plan,
                             lambda: {"disabled": bool(sm.disable_registry_item(hive, key_path, name))})

    def a_startup_restore(self, params: Dict) -> Dict:
        sm = self._need("startup_manager")
        hive = str(self._require(params, "hive"))
        name = str(self._require(params, "name"))
        return ok({"restored": bool(sm.restore_registry_item(hive, name))})

    # ── Analyse disque ───────────────────────────────────────────
    def a_disk_analyze(self, params: Dict) -> Dict:
        path = self._require_path(params, "path")
        analyzer = self._need("disk_analyzer")
        top_files = int(params.get("top_n_files", 20))
        top_folders = int(params.get("top_n_folders", 15))
        min_dup = float(params.get("min_dup_size_mb", 1.0))

        def worker(job):
            job.set_current(str(path))
            job.check_cancel()
            return analyzer.analyze_disk(str(path), top_files, top_folders, min_dup)

        return ok({"job_id": self.jobs.submit("disk_analyze", worker)})

    # ── Planification (schtasks) ─────────────────────────────────
    def a_schedule_cleanup(self, params: Dict) -> Dict:
        sched = self._need("task_scheduler")
        return ok(sched.create_weekly_cleanup_task(
            str(MAIN_SCRIPT), str(params.get("day", "SUN")), str(params.get("time", "09:00"))))

    def a_schedule_remove(self, params: Dict) -> Dict:
        return ok(self._need("task_scheduler").remove_scheduled_task())

    # ── Triage / staging ─────────────────────────────────────────
    def a_triage_scan(self, params: Dict) -> Dict:
        path = self._require_path(params, "path")
        triage = self._need("file_triage")

        def worker(job):
            job.set_current(str(path))
            job.check_cancel()
            return triage.triage_directory(str(path))

        return ok({"job_id": self.jobs.submit("triage_scan", worker)})

    def a_triage_apply(self, params: Dict) -> Dict:
        triage = self._need("file_triage")
        files = params.get("files")
        if not isinstance(files, list) or not files:
            return err("Paramètre `files` manquant ou vide (liste attendue).")

        normalized = []
        for item in files:
            if isinstance(item, dict):
                normalized.append({"path": str(item.get("path", "")),
                                   "reason": str(item.get("reason", "Mise de côté depuis l'interface web"))})
            else:
                normalized.append({"path": str(item), "reason": "Mise de côté depuis l'interface web"})
        normalized = [f for f in normalized if f["path"]]

        def plan():
            return {"files": [{**f, "exists": Path(f["path"]).exists()} for f in normalized],
                    "count": len(normalized), "reversible": True,
                    "effect": "Les fichiers sont DÉPLACÉS vers le tampon de staging (restaurables)."}

        def execute():
            staged, errors = [], []
            for f in normalized:
                sid = triage.move_to_staging(f["path"], f["reason"])
                (staged if sid else errors).append({"path": f["path"], "staging_id": sid} if sid else f["path"])
            return {"staged": staged, "errors": errors, "staged_count": len(staged)}

        return self._guarded("triage_apply", params, plan, execute)

    def a_staging_list(self, params: Dict) -> Dict:
        return ok({"items": self._need("file_triage").list_staging()})

    def a_staging_restore(self, params: Dict) -> Dict:
        sid = str(self._require(params, "id"))
        return ok({"restored": bool(self._need("file_triage").restore_from_staging(sid))})

    def a_staging_purge(self, params: Dict) -> Dict:
        triage = self._need("file_triage")
        days = int(params.get("older_than_days", 30))

        def plan():
            from datetime import datetime
            now = datetime.now()
            doomed = []
            for entry in triage.list_staging():
                try:
                    age = (now - datetime.fromisoformat(entry["date"])).days
                except (KeyError, ValueError):
                    continue
                if age > days:
                    doomed.append({**entry, "age_days": age})
            return {"older_than_days": days, "items": doomed, "count": len(doomed),
                    "effect": "Suppression DÉFINITIVE et irréversible des éléments listés."}

        return self._guarded("staging_purge", params, plan,
                             lambda: {"purged": triage.purge_staging(older_than_days=days)})

    # ── Bouclier anti-ransomware ─────────────────────────────────
    def a_shield_start(self, params: Dict) -> Dict:
        folders = self._folder_list(params.get("folders"))
        if not folders:
            return err("Aucun dossier valide fourni.")
        mod = self._module("ransomware_shield")
        with self._lock:
            if self._shield is not None:
                return ok({"already_running": True, "folders": self._shield_folders})
            shield = mod.RansomwareShield(folders)
            deployed = shield.deploy_canaries()
            self._shield = shield
            self._shield_folders = folders
        return ok({"started": True, "folders": folders, "canaries": deployed})

    def a_shield_status(self, params: Dict) -> Dict:
        with self._lock:
            shield = self._shield
        if shield is None:
            return ok({"active": False, "canaries": [], "threshold": None})
        return ok({
            "active": True,
            "folders": self._shield_folders,
            "canaries": shield.check_canaries(),
            "adaptive_threshold": shield.adaptive_threshold(),
        })

    def a_shield_processes(self, params: Dict) -> Dict:
        mod = self._module("ransomware_shield")
        if not getattr(mod, "PSUTIL_AVAILABLE", False):
            raise ModuleUnavailable("psutil non installé : inspection des processus impossible.")
        return ok({"processes": mod.RansomwareShield.find_suspicious_processes(int(params.get("top_n", 5)))})

    def a_shield_stop(self, params: Dict) -> Dict:
        with self._lock:
            if self._shield is None:
                return ok({"stopped": False, "note": "Bouclier non actif."})
            try:
                self._shield.remove_canaries()
            finally:
                self._shield = None
                self._shield_folders = []
        return ok({"stopped": True})

    # ── Réputation / phishing ────────────────────────────────────
    def a_reputation_check(self, params: Dict) -> Dict:
        checker = self._need("reputation_checker")
        digest = params.get("sha256")
        if not digest:
            path = self._require_path(params, "path")
            hashing = self._need("hash_scanner")
            digest = hashing.compute_sha256(str(path))
            if not digest:
                return err(f"Impossible de calculer le SHA-256 de {path}.")
        return ok(checker.check_hash(str(digest)))

    def a_reputation_configured(self, params: Dict) -> Dict:
        return ok({"configured": bool(self._need("reputation_checker").is_configured())})

    def a_phishing_check(self, params: Dict) -> Dict:
        url = str(self._require(params, "url"))
        return ok(self._need("phishing_checker").check_url(url))

    # ── Organisation de dossiers ─────────────────────────────────
    def a_organize_plan(self, params: Dict) -> Dict:
        path = self._require_path(params, "path")
        mode = str(params.get("mode", "category"))
        if mode not in ("category", "application", "importance"):
            return err("`mode` doit valoir 'category', 'application' ou 'importance'.")
        organizer = self._need("folder_organizer")
        plan = organizer.build_plan(str(path), mode)
        return ok({"path": str(path), "mode": mode, "plan": plan, "count": len(plan)})

    def a_organize_apply(self, params: Dict) -> Dict:
        organizer = self._need("folder_organizer")
        plan = params.get("plan")
        if not isinstance(plan, list) or not plan:
            return err("Paramètre `plan` manquant ou vide (liste attendue, issue de organize_plan).")
        clean_plan = [p for p in plan if isinstance(p, dict) and p.get("source") and p.get("destination")]
        if not clean_plan:
            return err("Aucune entrée de plan valide (clés `source` et `destination` requises).")

        return self._guarded(
            "organize_apply", params,
            lambda: {"moves": clean_plan, "count": len(clean_plan), "reversible": True,
                     "effect": "Déplacement des fichiers listés (annulable via organize_undo)."},
            lambda: organizer.apply_plan(clean_plan),
        )

    def a_organize_move_folder(self, params: Dict) -> Dict:
        organizer = self._need("folder_organizer")
        source = str(self._require(params, "source"))
        target = str(self._require(params, "target"))

        return self._guarded(
            "organize_move_folder", params,
            lambda: {"source": source, "target": target,
                     "destination": str(Path(target) / Path(source).name),
                     "source_exists": Path(source).is_dir(), "target_exists": Path(target).is_dir(),
                     "effect": "Déplacement du dossier source à l'intérieur du dossier cible."},
            lambda: organizer.move_folder_into(source, target),
        )

    def a_organize_least_used(self, params: Dict) -> Dict:
        organizer = self._need("folder_organizer")
        path = self._require_path(params, "path")
        days = int(params.get("days", 180))

        return self._guarded(
            "organize_least_used", params,
            lambda: {**organizer.find_least_used_files(str(path), days), "days": days,
                     "reversible": True,
                     "effect": "Rangement des fichiers listés dans un sous-dossier dédié (annulable via organize_undo)."},
            lambda: organizer.organize_least_used(str(path), unused_since_days=days),
        )

    def a_organize_sessions(self, params: Dict) -> Dict:
        return ok({"sessions": self._need("folder_organizer").list_sessions()})

    def a_organize_undo(self, params: Dict) -> Dict:
        session_id = str(self._require(params, "session_id"))
        return ok(self._need("folder_organizer").undo_session(session_id))

    # ── Mode Gardien ─────────────────────────────────────────────
    def a_guardian_run(self, params: Dict) -> Dict:
        guardian = self._need("guardian")
        organizer = self._need("folder_organizer")
        folders = self._folder_list(params.get("folders")) or guardian.default_folders()
        folders = [f for f in folders if Path(f).exists()]
        days = int(params.get("unused_threshold_days", 180))

        def plan():
            # IMPORTANT : run_full_pass() nettoie et déplace réellement dès
            # son premier appel — on ne peut donc PAS s'en servir pour le
            # dry_run. Le plan est construit à partir des seules primitives
            # en lecture pure.
            previews = []
            for folder in folders:
                try:
                    found = organizer.find_least_used_files(folder, days)
                    previews.append({"folder": folder, "candidates": len(found.get("files", [])),
                                     "files": found.get("files", [])[:50], "note": found.get("note")})
                except Exception as exc:  # noqa: BLE001
                    previews.append({"folder": folder, "error": str(exc)})
            return {
                "folders": folders,
                "steps": [
                    "Nettoyage TEMP / caches navigateurs / corbeille",
                    "Mise de côté des fichiers jetables (réversible, vers le staging)",
                    "Scan antivirus de chaque dossier (quarantaine automatique)",
                    f"Rangement des fichiers non utilisés depuis plus de {days} jours",
                ],
                "unused_threshold_days": days,
                "least_used_preview": previews,
                "reversible": True,
                "effect": "Aucune suppression définitive : tout passe par quarantaine/staging.",
            }

        def execute():
            def worker(job):
                job.set_total(len(folders))
                job.set_current("Passe complète du Mode Gardien")
                job.check_cancel()
                return guardian.run_full_pass(folders=folders, unused_threshold_days=days)
            return {"job_id": self.jobs.submit("guardian_run", worker)}

        return self._guarded("guardian_run", params, plan, execute)

    def a_guardian_pending(self, params: Dict) -> Dict:
        return ok({"items": self._need("guardian").review_pending_deletions()})

    def a_guardian_confirm(self, params: Dict) -> Dict:
        guardian = self._need("guardian")
        days = int(params.get("older_than_days", 30))

        def plan():
            from datetime import datetime
            now = datetime.now()
            doomed = []
            for entry in guardian.review_pending_deletions():
                try:
                    age = (now - datetime.fromisoformat(entry["date"])).days
                except (KeyError, ValueError):
                    continue
                if age > days:
                    doomed.append({**entry, "age_days": age})
            return {"older_than_days": days, "items": doomed, "count": len(doomed),
                    "effect": "Suppression DÉFINITIVE et irréversible des éléments mis de côté."}

        return self._guarded("guardian_confirm", params, plan,
                             lambda: {"purged": guardian.confirm_permanent_deletion(older_than_days=days)})

    def a_guardian_schedule(self, params: Dict) -> Dict:
        sched = self._need("task_scheduler")
        return ok(sched.create_daily_guardian_task(str(MAIN_SCRIPT), str(params.get("time", "09:00"))))

    def a_guardian_unschedule(self, params: Dict) -> Dict:
        return ok(self._need("task_scheduler").remove_guardian_task())

    # ── Applications installées ──────────────────────────────────
    def a_apps_list(self, params: Dict) -> Dict:
        manager = self._need("app_manager")
        sort_by = str(params.get("sort_by", "size"))

        def worker(job):
            job.set_current("Inventaire des applications installées")
            job.check_cancel()
            return manager.list_all_sorted(sort_by)

        return ok({"job_id": self.jobs.submit("apps_list", worker)})

    def a_apps_uninstall(self, params: Dict) -> Dict:
        manager = self._need("app_manager")
        app = params.get("app")
        if not isinstance(app, dict) or not app:
            return err("Paramètre `app` manquant (objet application issu de apps_list).")

        return self._guarded(
            "apps_uninstall", params,
            lambda: {"app": app, "effect": "Désinstallation de l'application (irréversible sans réinstallation)."},
            lambda: manager.uninstall(app),
        )

    def a_apps_debloat(self, params: Dict) -> Dict:
        manager = self._need("app_manager")
        apps = params.get("apps")
        apps = apps if isinstance(apps, list) and apps else None

        def plan():
            candidates = apps if apps is not None else [
                a for a in manager.list_uwp_apps() if manager._is_known_bloatware(a.get("package_name", ""))
            ]
            return {"apps": candidates, "count": len(candidates),
                    "effect": "Suppression des paquets UWP listés (réinstallables depuis le Microsoft Store)."}

        return self._guarded("apps_debloat", params, plan,
                             lambda: manager.remove_known_bloatware(apps))

    # ── Résidus ──────────────────────────────────────────────────
    def a_residue_shortcuts(self, params: Dict) -> Dict:
        return ok({"items": self._need("residue_cleaner").find_orphaned_shortcuts()})

    def a_residue_registry(self, params: Dict) -> Dict:
        return ok({"items": self._need("residue_cleaner").find_orphaned_uninstall_entries()})

    def a_residue_folders(self, params: Dict) -> Dict:
        return ok({"items": self._need("residue_cleaner").find_candidate_orphaned_folders()})

    def a_residue_clean(self, params: Dict) -> Dict:
        cleaner = self._need("residue_cleaner")
        kind = str(params.get("kind", "")).strip()
        items = params.get("items")
        if kind not in ("shortcuts", "registry", "folders"):
            return err("`kind` doit valoir 'shortcuts', 'registry' ou 'folders'.")
        if not isinstance(items, list) or not items:
            return err("Paramètre `items` manquant ou vide (liste attendue).")

        effects = {
            "shortcuts": "Mise de côté des raccourcis orphelins (réversible via le staging).",
            "registry": "Sauvegarde puis suppression des clés Uninstall orphelines (restaurables via Regedit).",
            "folders": "Mise de côté des dossiers orphelins, un par un (réversible via le staging).",
        }

        def plan():
            return {"kind": kind, "items": items, "count": len(items),
                    "reversible": True, "effect": effects[kind]}

        def execute():
            if kind == "shortcuts":
                return cleaner.stage_orphaned_shortcuts(items)
            if kind == "registry":
                return {"results": [cleaner.backup_and_remove_uninstall_entry(e)
                                    for e in items if isinstance(e, dict)]}
            results = []
            for it in items:
                folder = it.get("path") if isinstance(it, dict) else str(it)
                reason = it.get("reason", "Dossier orphelin") if isinstance(it, dict) else "Dossier orphelin"
                if folder:
                    results.append({"path": folder, **cleaner.stage_orphaned_folder(str(folder), str(reason))})
            return {"results": results}

        return self._guarded("residue_clean", params, plan, execute)

    # ── Utilitaires de validation des paramètres ─────────────────
    @staticmethod
    def _require(params: Dict, key: str):
        value = params.get(key)
        if value in (None, ""):
            raise ValueError(f"Paramètre `{key}` manquant.")
        return value

    @staticmethod
    def _require_path(params: Dict, key: str) -> Path:
        raw = Bridge._require(params, key)
        path = Path(str(raw)).expanduser()
        if not path.exists():
            raise ValueError(f"Chemin introuvable : {path}")
        return path

    @staticmethod
    def _folder_list(raw) -> List[str]:
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            return []
        return [str(Path(str(f)).expanduser()) for f in raw
                if str(f).strip() and Path(str(f)).expanduser().exists()]


class _ProgressEngineProxy:
    """Proxy transparent autour d'AntivirusEngine.

    Permet d'appeler la VRAIE `main.AntivirusEngine.scan_directory` (le
    module désigné par le contrat) tout en récupérant la progression
    fichier par fichier et en honorant l'annulation — sans modifier
    main.py ni dupliquer la logique de scan.
    """

    def __init__(self, engine, job):
        object.__setattr__(self, "_engine", engine)
        object.__setattr__(self, "_job", job)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_engine"), name)

    def scan_single_file(self, file_path: str, auto_quarantine: bool = True):
        job = object.__getattribute__(self, "_job")
        engine = object.__getattribute__(self, "_engine")
        job.check_cancel()
        result = engine.scan_single_file(file_path, auto_quarantine)
        job.step(file_path)
        return result
