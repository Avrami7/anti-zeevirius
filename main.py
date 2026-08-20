"""
main.py — Point d'entrée de l'antivirus
Orchestre : scan par hash, scan YARA, heuristiques, quarantaine, temps réel.

Usage :
    python main.py
"""

import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Fix encodage console Windows ─────────────────────────────────────
# cmd.exe / PowerShell utilisent par défaut une page de code (cp1252/850)
# qui ne supporte pas tous les caractères accentués ou emoji (⚠️, etc.).
# Sans ce correctif, un simple print() peut lever UnicodeEncodeError et
# planter le programme en plein scan. reconfigure() est disponible
# depuis Python 3.7+ sur les flux texte standards.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from scanner.hash_scanner import HashScanner
from scanner.yara_scanner import YaraScanner
from scanner.heuristics import HeuristicScanner
from quarantine.quarantine_manager import QuarantineManager
from monitor.realtime_monitor import RealtimeMonitor
from optimizer.temp_cleaner import TempCleaner
from optimizer.startup_manager import StartupManager
from optimizer.disk_analyzer import DiskAnalyzer
from optimizer.task_scheduler import TaskScheduler
from optimizer.file_triage import FileTriage
from optimizer.ransomware_shield import RansomwareShield
from optimizer.reputation_checker import ReputationChecker
from optimizer.phishing_link_checker import PhishingLinkChecker
from optimizer.folder_organizer import FolderOrganizer
from optimizer.guardian import SystemGuardian
from optimizer.app_manager import AppManager
from optimizer.residue_cleaner import ResidueCleaner

import paths as _paths

BASE_DIR = Path(__file__).parent
SIGNATURES_DIR = _paths.signatures_dir()
LOGS_DIR = _paths.logs_dir()
QUARANTINE_DIR = _paths.quarantine_dir()

HASH_DB_PATH = SIGNATURES_DIR / "malicious_hashes.txt"
YARA_RULES_PATH = SIGNATURES_DIR / "rules.yar"

# ── Logging ──────────────────────────────────────────────────────────
LOGS_DIR.mkdir(exist_ok=True)
log_file = LOGS_DIR / f"scan_{datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("anti-zeevirius")


class AntivirusEngine:
    def __init__(self):
        logger.info("Initialisation du moteur ANTI-ZEEVIRIUS...")
        self.hash_scanner = HashScanner(str(HASH_DB_PATH))
        self.yara_scanner = YaraScanner(str(YARA_RULES_PATH))
        self.heuristic_scanner = HeuristicScanner()
        self.quarantine_manager = QuarantineManager(str(QUARANTINE_DIR))
        self.temp_cleaner = TempCleaner()
        self.startup_manager = StartupManager()
        self.disk_analyzer = DiskAnalyzer()
        self.file_triage = FileTriage(str(_paths.staging_dir()))
        self.reputation_checker = ReputationChecker(
            str(SIGNATURES_DIR / "vt_api_key.txt"),
            str(BASE_DIR / "cache" / "vt_cache.json"),
        )
        self.phishing_checker = PhishingLinkChecker(str(BASE_DIR / "cache" / "phishing_blocklist.txt"))
        # _paths, et non BASE_DIR : une fois l'application installée, les
        # données vivent dans %LOCALAPPDATA%. Avec l'ancien chemin, le menu CLI
        # et l'interface web écrivaient DEUX journaux de rangement distincts,
        # et l'historique unifié n'en voyait qu'un seul.
        self.folder_organizer = FolderOrganizer(str(_paths.organizer_log()))
        self.ransomware_shield: Optional[RansomwareShield] = None
        self._realtime_thread: Optional[threading.Thread] = None
        self._realtime_monitor: Optional[RealtimeMonitor] = None

        # Dossiers internes de l'outil — jamais scannés/quarantinés, pour
        # éviter que l'antivirus ne se mette lui-même en quarantaine (son
        # propre index JSON, son cache VT, etc.) quand on lui demande de
        # scanner un dossier qui le contient (ex: C:\ ou le dossier
        # utilisateur si l'outil y est installé).
        self._excluded_dirs = [
            p.resolve() for p in (
                QUARANTINE_DIR,
                LOGS_DIR,
                BASE_DIR / "cache",
                BASE_DIR / "triage_staging",
                BASE_DIR / "organizer_logs",
            )
        ]
        logger.info("ANTI-ZEEVIRIUS prêt.")
        self.guardian = SystemGuardian(self)
        self.app_manager = AppManager()
        self.residue_cleaner = ResidueCleaner(self.file_triage, self.app_manager)

    def _is_excluded(self, path: Path) -> bool:
        """True si le chemin se trouve dans un dossier interne de l'outil."""
        try:
            resolved = path.resolve()
        except OSError:
            return False
        return any(
            resolved == excluded or excluded in resolved.parents
            for excluded in self._excluded_dirs
        )

    def scan_single_file(self, file_path: str, auto_quarantine: bool = True) -> dict:
        """Lance les 3 couches de détection sur un fichier et agrège le résultat."""
        if not Path(file_path).is_file():
            return {"file": file_path, "verdict": "ERREUR", "details": "Fichier introuvable"}

        result = {
            "file": file_path,
            "hash_scan": self.hash_scanner.scan_file(file_path),
            "yara_scan": self.yara_scanner.scan_file(file_path),
            "heuristic_scan": self.heuristic_scanner.scan_file(file_path),
        }

        is_malicious = (
            result["hash_scan"]["matched"]
            or not result["yara_scan"]["clean"]
            or not result["heuristic_scan"]["clean"]
        )

        result["verdict"] = "MALVEILLANT" if is_malicious else "SAIN"

        if is_malicious:
            reasons = []
            if result["hash_scan"]["matched"]:
                reasons.append("Signature hash connue")
            if not result["yara_scan"]["clean"]:
                reasons.append(f"YARA: {result['yara_scan']['reason']}")
            if not result["heuristic_scan"]["clean"]:
                reasons.append(f"Heuristique: {result['heuristic_scan']['reason']}")
            reason_str = " | ".join(reasons)

            logger.warning(f"MENACE DÉTECTÉE : {file_path} — {reason_str}")

            if auto_quarantine:
                qid = self.quarantine_manager.quarantine_file(file_path, reason_str, result)
                result["quarantined"] = qid is not None
                result["quarantine_id"] = qid
                if qid:
                    logger.info(f"Fichier mis en quarantaine : {qid}")
        else:
            logger.info(f"Sain : {file_path}")

        return result

    def scan_directory(self, dir_path: str, auto_quarantine: bool = True, max_workers: Optional[int] = None) -> list:
        """
        Scan récursif d'un dossier entier.

        Parallélisé via ThreadPoolExecutor : scan_single_file() est dominé
        par des opérations qui libèrent le GIL pendant leur exécution native
        (lecture disque, hashlib.sha256 en C, yara-python en C, et le
        np.bincount de l'entropie si numpy est installé — voir
        scanner/heuristics.py). Le vrai goulot d'étranglement est l'I/O
        disque, pas le CPU Python : des threads suffisent, pas besoin d'un
        ProcessPoolExecutor (qui coûterait cher en sérialisation des
        résultats et en démarrage de processus sur Windows).

        Complexité : le scan séquentiel a un temps mural (wall-clock)
        ≈ Σ(temps par fichier) = O(N) avec une constante = latence disque
        + temps CPU par fichier. Avec W workers I/O-bound qui se recouvrent,
        le temps mural tend vers O(N/W) tant que le disque (SSD notamment,
        qui supporte de vraies I/O concurrentes) n'est pas saturé — sur HDD
        classique le gain sera moindre à cause des déplacements de tête de
        lecture, d'où le plafond raisonnable ci-dessous plutôt qu'un
        nombre de workers arbitrairement élevé.
        """
        results = []
        path = Path(dir_path)
        if not path.is_dir():
            logger.error(f"Dossier introuvable : {dir_path}")
            return results

        files = [f for f in path.rglob("*") if f.is_file() and not self._is_excluded(f)]
        total = len(files)
        logger.info(f"Scan de {total} fichiers dans {dir_path}...")

        if max_workers is None:
            # Charge I/O-bound : un multiple raisonnable du nombre de cœurs,
            # plafonné pour ne pas saturer un disque mécanique.
            max_workers = min(32, (os.cpu_count() or 4) * 4)

        scanned_lock = threading.Lock()
        scanned = 0

        def _scan_and_report(f: Path) -> dict:
            nonlocal scanned
            r = self.scan_single_file(str(f), auto_quarantine)
            with scanned_lock:
                scanned += 1
                if scanned % 50 == 0:
                    print(f"  ... {scanned}/{total} fichiers scannés")
            return r

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_scan_and_report, f) for f in files]
            for future in as_completed(futures):
                results.append(future.result())

        threats = [r for r in results if r["verdict"] == "MALVEILLANT"]
        logger.info(f"Scan terminé : {total} fichiers, {len(threats)} menace(s) détectée(s)")
        return results

    def _realtime_callback(self, path: str) -> None:
        """Callback appelé par le monitor à chaque événement fichier.
        Scanne le fichier ET alimente le bouclier anti-ransomware si actif
        (sinon record_modification_event() ne serait jamais appelé et la
        détection de chiffrement massif resterait du code mort)."""
        if self._is_excluded(Path(path)):
            return

        self.scan_single_file(path, auto_quarantine=True)

        if self.ransomware_shield is not None:
            mass_modification = self.ransomware_shield.record_modification_event()
            if mass_modification:
                logger.warning(
                    "ALERTE RANSOMWARE : taux de modification anormal détecté "
                    "dans un dossier protégé — vérification des canaris..."
                )
                alerts = self.ransomware_shield.check_canaries()
                suspects = RansomwareShield.find_suspicious_processes(top_n=3)
                if alerts:
                    logger.warning(f"{len(alerts)} fichier(s) canari altéré(s) : {alerts}")
                if suspects:
                    top = suspects[0]
                    logger.warning(
                        f"Processus le plus actif en écriture disque : "
                        f"PID {top['pid']} ({top['name']}) — à investiguer/suspendre "
                        f"manuellement via l'option 13 si confirmé malveillant."
                    )
                self.ransomware_shield.reset_modification_counter()

    def start_realtime_protection(self, folders: list, blocking: bool = True) -> RealtimeMonitor:
        """Démarre la surveillance temps réel.
        blocking=True (par défaut) : bloque le thread principal (Ctrl+C pour arrêter),
        comportement identique à l'original.
        blocking=False : démarre dans un thread daemon en arrière-plan et rend
        immédiatement la main, pour garder le menu utilisable en parallèle."""
        monitor = RealtimeMonitor(
            watched_folders=folders,
            scan_callback=self._realtime_callback,
        )
        self._realtime_monitor = monitor

        if blocking:
            monitor.start()
        else:
            self._realtime_thread = threading.Thread(target=monitor.start, daemon=True)
            self._realtime_thread.start()
        return monitor

    def stop_realtime_protection(self) -> bool:
        if self._realtime_monitor is None:
            return False
        self._realtime_monitor.stop()
        self._realtime_monitor = None
        self._realtime_thread = None
        return True


def print_menu() -> None:
    print("\n" + "=" * 60)
    print("  ANTI-ZEEVIRIUS — MENU PRINCIPAL")
    print("=" * 60)
    print("  --- MODE GARDIEN (protection + optimisation + rangement + validation) ---")
    print(" 22. Activer le MODE GARDIEN maintenant (un clic, suppression validée à la fin)")
    print(" 23. Planifier le MODE GARDIEN chaque jour (automatique, permanent)")
    print(" 24. Désactiver le Mode Gardien automatique quotidien")
    print("  1. Scanner un fichier")
    print("  2. Scanner un dossier (récursif)")
    print("  3. Démarrer la protection temps réel")
    print("  4. Voir les fichiers en quarantaine")
    print("  5. Restaurer un fichier de quarantaine")
    print("  6. Supprimer définitivement un fichier de quarantaine")
    print("  --- OPTIMISATION ---")
    print("  7. Nettoyage complet (Temp, %temp%, cache, corbeille)")
    print("  8. Gérer les programmes au démarrage")
    print("  9. Analyser le disque (gros fichiers / doublons)")
    print(" 10. Planifier un nettoyage automatique hebdomadaire")
    print(" 11. Trier les fichiers d'un dossier (avec confirmation)")
    print(" 12. Voir / restaurer les fichiers mis de côté")
    print("  --- FONCTIONNALITÉS PREMIUM (type Bitdefender) ---")
    print(" 13. Activer le bouclier anti-ransomware")
    print(" 14. Vérifier la réputation d'un fichier (cloud VirusTotal)")
    print(" 15. Vérifier un lien avant de cliquer (anti-phishing)")
    print("  --- ORGANISATION DES FICHIERS ---")
    print(" 16. Réorganiser un dossier par CATÉGORIE (Documents/Images/Vidéos...)")
    print(" 17. Réorganiser un dossier par APPLICATION associée")
    print(" 18. Réorganiser un dossier par NIVEAU D'IMPORTANCE (Actif/Important/Archive)")
    print(" 19. Déplacer un dossier entier dans un autre")
    print(" 20. Ranger les fichiers les moins utilisés dans un sous-dossier")
    print(" 21. Annuler une réorganisation précédente")
    print("  --- GESTION DES APPLICATIONS ---")
    print(" 25. Voir / trier les applications installées (taille, nom, bloatware)")
    print(" 26. Désinstaller une application")
    print(" 27. Retirer le bloatware Microsoft connu (en lot, avec confirmation)")
    print("  --- RÉSIDUS D'APPLICATIONS DÉSINSTALLÉES ---")
    print(" 28. Nettoyer les raccourcis orphelins (Bureau / Menu Démarrer)")
    print(" 29. Nettoyer les entrées de registre orphelines")
    print(" 30. Détecter les dossiers orphelins (Program Files / AppData)")
    print("  --- BASES DE DÉTECTION ---")
    print(" 31. Mettre à jour les signatures (empreintes + règles YARA)")
    print("  --- ACCÈS NON DÉSIRÉ ---")
    print(" 32. Qui accède à cet ordinateur ? (sessions, journal, connexions)")
    print(" 33. Tracer les accès FUTURS à mes documents")
    print(" 34. Qui utilise ma caméra / mon micro ?")
    print(" 35. Surveiller la caméra en continu (notification si activation)")
    print(" 36. Bloquer / débloquer une application (pare-feu)")
    print("  0. Quitter")
    print("=" * 60)


def run_auto_clean() -> None:
    """Exécution silencieuse déclenchée par la tâche planifiée Windows
    'AntiZeevirius_AutoCleanup' (voir optimizer/task_scheduler.py).
    Conservé tel quel (nettoyage TEMP uniquement) pour ne pas changer le
    comportement d'une tâche déjà planifiée par quelqu'un qui ne voulait
    qu'un simple nettoyage — voir run_guardian_unattended() pour la version
    complète (MODE GARDIEN)."""
    engine = AntivirusEngine()
    logger.info("Lancement du nettoyage automatique planifié...")
    report = engine.temp_cleaner.run_full_cleanup()
    logger.info(f"Nettoyage automatique terminé : {report['total_freed_mb']} Mo libérés")


def run_guardian_unattended() -> None:
    """Exécution silencieuse déclenchée par la tâche planifiée Windows
    'AntiZeevirius_GuardianDaily' (--guardian). Version complète : nettoyage
    TEMP + scan antivirus + rangement des fichiers non utilisés, sans
    aucune interaction (voir optimizer/guardian.py)."""
    engine = AntivirusEngine()
    engine.guardian.run_unattended()


def main() -> None:
    if "--auto-clean" in sys.argv:
        run_auto_clean()
        return
    if "--guardian" in sys.argv:
        run_guardian_unattended()
        return

    engine = AntivirusEngine()

    while True:
        try:
            print_menu()
            choice = input("Votre choix : ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nInterruption détectée — fermeture propre de l'antivirus.")
            if engine._realtime_monitor is not None:
                engine.stop_realtime_protection()
            break

        if choice == "1":
            path = input("Chemin complet du fichier : ").strip().strip('"')
            result = engine.scan_single_file(path)
            print(f"\nVerdict : {result['verdict']}")

        elif choice == "2":
            path = input("Chemin complet du dossier : ").strip().strip('"')
            results = engine.scan_directory(path)
            threats = [r for r in results if r["verdict"] == "MALVEILLANT"]
            print(f"\n{len(results)} fichier(s) scanné(s), {len(threats)} menace(s) trouvée(s).")
            for t in threats:
                print(f"  - {t['file']}")

        elif choice == "3":
            if engine._realtime_monitor is not None:
                stop = input("Protection temps réel déjà active. L'arrêter ? (o/n) : ").strip().lower()
                if stop == "o":
                    engine.stop_realtime_protection()
                continue

            print("Dossiers par défaut : Téléchargements, Bureau")
            custom = input("Dossiers à surveiller (séparés par ;), ou Entrée pour défaut : ").strip()
            if custom:
                folders = [f.strip() for f in custom.split(";")]
            else:
                home = str(Path.home())
                folders = [
                    os.path.join(home, "Downloads"),
                    os.path.join(home, "Desktop"),
                ]
            # Non bloquant : tourne dans un thread daemon pour que le menu
            # reste utilisable (ex: consulter la quarantaine pendant que
            # la surveillance temps réel tourne). Utilise l'option 3 à
            # nouveau pour l'arrêter proprement.
            engine.start_realtime_protection(folders, blocking=False)
            print("Protection temps réel démarrée en arrière-plan. Le menu reste utilisable.")

        elif choice == "4":
            quarantined = engine.quarantine_manager.list_quarantined()
            if not quarantined:
                print("Aucun fichier en quarantaine.")
            for q in quarantined:
                print(f"  ID: {q['id']}")
                print(f"    Origine : {q['original_path']}")
                print(f"    Date    : {q['quarantine_date']}")
                print(f"    Raison  : {q['reason']}")

        elif choice == "5":
            qid = input("ID de quarantaine à restaurer : ").strip()
            success = engine.quarantine_manager.restore_file(qid)
            print("Restauré avec succès." if success else "Échec de la restauration.")

        elif choice == "6":
            qid = input("ID de quarantaine à supprimer définitivement : ").strip()
            confirm = input("Confirmer la suppression DÉFINITIVE ? (oui/non) : ").strip().lower()
            if confirm == "oui":
                success = engine.quarantine_manager.delete_permanently(qid)
                print("Supprimé." if success else "Échec de la suppression.")

        elif choice == "7":
            print("\nDémarrage du nettoyage complet...")
            print("(Sans droits admin, Windows Temp/Prefetch/Update Cache seront ignorés)")
            report = engine.temp_cleaner.run_full_cleanup()
            print(f"\nMode administrateur : {'Oui' if report['is_admin'] else 'Non'}")
            for r in report["results"]:
                status = r.get("status", "?")
                freed = r.get("freed_mb", "?")
                print(f"  - {r['label']:<30} statut={status:<10} libéré={freed} Mo")
            print(f"\nTOTAL LIBÉRÉ : {report['total_freed_mb']} Mo")
            logger.info(f"Nettoyage complet effectué : {report['total_freed_mb']} Mo libérés")

        elif choice == "8":
            print("\n--- Programmes au démarrage (registre) ---")
            reg_items = engine.startup_manager.list_registry_startup_items()
            for idx, item in enumerate(reg_items):
                flag = " [RECOMMANDÉ: désactivable]" if item["recommended_disable"] else ""
                print(f"  [{idx}] {item['name']} ({item['hive']}){flag}")
                print(f"       {item['command']}")

            print("\n--- Dossier Démarrage ---")
            folder_items = engine.startup_manager.list_startup_folder_items()
            for idx, item in enumerate(folder_items):
                flag = " [RECOMMANDÉ: désactivable]" if item["recommended_disable"] else ""
                print(f"  [F{idx}] {item['name']}{flag}")

            sub_choice = input(
                "\nEntrez l'index à désactiver (ex: 2 ou F1), ou Entrée pour revenir : "
            ).strip()
            if sub_choice.startswith("F") and sub_choice[1:].isdigit():
                item = folder_items[int(sub_choice[1:])]
                success = engine.startup_manager.disable_startup_folder_item(item["path"])
                print("Désactivé." if success else "Échec.")
            elif sub_choice.isdigit():
                item = reg_items[int(sub_choice)]
                success = engine.startup_manager.disable_registry_item(
                    item["hive"], item["key_path"], item["name"]
                )
                print("Désactivé (restaurable)." if success else "Échec — droits admin requis pour HKLM.")

        elif choice == "9":
            path = input("Dossier à analyser (ex: C:\\Users\\VotreNom) : ").strip().strip('"')
            print("\nAnalyse en cours (peut prendre du temps sur de gros volumes)...")

            # Un seul parcours d'arborescence pour les 3 analyses au lieu de 3
            # parcours indépendants (rglob x3) — même résultat, ~3x moins d'I/O
            # disque sur un scan complet (voir disk_analyzer.analyze_disk).
            analysis = engine.disk_analyzer.analyze_disk(path)

            print("\n--- Top 15 des plus gros dossiers ---")
            for folder in analysis["largest_folders"]:
                print(f"  {folder['size_mb']:>10} Mo  -  {folder['path']}")

            print("\n--- Top 20 des plus gros fichiers ---")
            for f in analysis["largest_files"]:
                print(f"  {f['size_mb']:>10} Mo  -  {f['path']}")

            print("\n--- Fichiers en double (> 1 Mo) ---")
            duplicates = analysis["duplicates"]
            total_wasted = sum(d["wasted_mb"] for d in duplicates)
            for d in duplicates[:15]:
                print(f"  {d['count']}x copies, {d['wasted_mb']} Mo gaspillés :")
                for p in d["paths"]:
                    print(f"      - {p}")
            print(f"\nTotal gaspillé par les doublons : {round(total_wasted, 2)} Mo")

        elif choice == "10":
            day = input("Jour (MON/TUE/WED/THU/FRI/SAT/SUN) [SUN par défaut] : ").strip().upper() or "SUN"
            time_str = input("Heure (HH:MM) [09:00 par défaut] : ").strip() or "09:00"
            script = str(Path(__file__).resolve())
            result = TaskScheduler.create_weekly_cleanup_task(script, day, time_str)
            print(f"\n{result['message']}")

        elif choice == "11":
            path = input("Dossier à trier (ex: C:\\Users\\VotreNom\\Downloads) : ").strip().strip('"')
            print("\nAnalyse en cours...")
            duplicates = engine.disk_analyzer.find_duplicate_files(path)
            report = engine.file_triage.triage_directory(path, include_duplicates=duplicates)

            print(f"\n{report['never_touch_count']} fichier(s) personnel(s)/système — jamais proposés.")
            print(f"{report['neutral_count']} fichier(s) neutre(s) — aucune règle applicable.\n")

            for category, label in [("safe", "SÛR À SUPPRIMER"), ("caution", "À VÉRIFIER AVANT SUPPRESSION")]:
                items = report[category]
                if not items:
                    continue
                print(f"\n--- {label} ({len(items)} fichier(s)) ---")
                for idx, item in enumerate(items):
                    print(f"  [{idx}] {item['path']}")
                    print(f"       {item['size_mb']} Mo — {item['reason']}")

                mode = input(
                    f"\nPour la catégorie '{label}' : "
                    "(T)out mettre de côté / (U)n par un / (R)ien faire ? "
                ).strip().upper()

                if mode == "T":
                    confirm = input(f"Confirmer la mise de côté des {len(items)} fichiers ? (oui/non) : ").strip().lower()
                    if confirm == "oui":
                        for item in items:
                            engine.file_triage.move_to_staging(item["path"], item["reason"])
                        print(f"{len(items)} fichier(s) mis de côté (récupérables via l'option 12).")
                elif mode == "U":
                    for item in items:
                        resp = input(f"  Mettre de côté '{item['path']}' ({item['size_mb']} Mo) ? (o/n/stop) : ").strip().lower()
                        if resp == "stop":
                            break
                        if resp == "o":
                            engine.file_triage.move_to_staging(item["path"], item["reason"])
                            print("    -> mis de côté.")

        elif choice == "12":
            staged = engine.file_triage.list_staging()
            if not staged:
                print("Aucun fichier actuellement mis de côté.")
            else:
                for idx, entry in enumerate(staged):
                    print(f"  [{idx}] ID: {entry['id']}")
                    print(f"       Origine : {entry['original_path']}")
                    print(f"       Raison  : {entry['reason']}")
                    print(f"       Date    : {entry['date']}")

                sub = input(
                    "\nID à restaurer, ou 'purge' pour supprimer définitivement "
                    "les fichiers de plus de 30 jours, ou Entrée pour revenir : "
                ).strip()
                if sub == "purge":
                    confirm = input("Confirmer la suppression DÉFINITIVE des fichiers >30 jours ? (oui/non) : ").strip().lower()
                    if confirm == "oui":
                        count = engine.file_triage.purge_staging(older_than_days=30)
                        print(f"{count} fichier(s) supprimé(s) définitivement.")
                elif sub:
                    success = engine.file_triage.restore_from_staging(sub)
                    print("Restauré." if success else "Échec — ID introuvable.")

        elif choice == "13":
            print("Dossiers protégés par défaut : Documents, Bureau, Images")
            custom = input("Dossiers à protéger (séparés par ;), ou Entrée pour défaut : ").strip()
            if custom:
                folders = [f.strip() for f in custom.split(";")]
            else:
                home = str(Path.home())
                folders = [
                    os.path.join(home, "Documents"),
                    os.path.join(home, "Desktop"),
                    os.path.join(home, "Pictures"),
                ]
            engine.ransomware_shield = RansomwareShield(folders)
            deployed = engine.ransomware_shield.deploy_canaries()
            print(f"\n{len(deployed)} fichier(s) canari déployé(s) dans les dossiers protégés.")
            print("Le bouclier est actif. Utilise cette option à nouveau pour vérifier l'intégrité :")
            recheck = input("Vérifier l'intégrité des canaris maintenant ? (o/n) : ").strip().lower()
            if recheck == "o":
                alerts = engine.ransomware_shield.check_canaries()
                if not alerts:
                    print("Aucune alerte — tous les fichiers canari sont intacts.")
                else:
                    print("\n⚠️  ALERTE RANSOMWARE POTENTIELLE :")
                    for a in alerts:
                        print(f"  - {a['canary']} : {a['status']}")
                    suspects = RansomwareShield.find_suspicious_processes()
                    if suspects:
                        print("\nProcessus les plus actifs en écriture disque (à investiguer) :")
                        for s in suspects:
                            print(f"  PID {s['pid']:<8} {s['name']:<30} {s['write_bytes']} octets écrits")

        elif choice == "14":
            if not engine.reputation_checker.is_configured():
                print("\nClé API VirusTotal non configurée.")
                print(f"1. Crée un compte gratuit : https://www.virustotal.com/gui/join-us")
                print(f"2. Récupère ta clé : https://www.virustotal.com/gui/my-apikey")
                print(f"3. Colle-la dans : {SIGNATURES_DIR / 'vt_api_key.txt'}")
            else:
                path = input("Chemin du fichier à vérifier : ").strip().strip('"')
                file_hash = engine.hash_scanner.compute_sha256(path)
                if not file_hash:
                    print("Fichier illisible.")
                else:
                    print(f"Hash SHA-256 : {file_hash}")
                    print("Interrogation de VirusTotal (peut prendre jusqu'à 15s, quota gratuit)...")
                    result = engine.reputation_checker.check_hash(file_hash)
                    print(f"\nStatut : {result.get('status')}")
                    print(f"Détail : {result.get('reason')}")
                    if "verdict" in result:
                        print(f"Verdict : {result['verdict']}")

        elif choice == "15":
            url = input("URL à vérifier avant de cliquer : ").strip()
            update = input("Mettre à jour la liste noire anti-phishing d'abord ? (o/n) : ").strip().lower()
            if update == "o":
                update_result = engine.phishing_checker.update_blocklist()
                print(f"  {update_result.get('reason')}")
            result = engine.phishing_checker.check_url(url)
            print(f"\nVerdict : {result['verdict']}")
            print(f"Raison  : {result['reason']}")
            print(f"Sûr de cliquer : {'Oui' if result['safe_to_click'] else 'NON'}")

        elif choice in ("16", "17", "18"):
            mode = {"16": "category", "17": "application", "18": "importance"}[choice]
            label = {
                "16": "catégorie (Documents/Images/Vidéos/Code...)",
                "17": "application associée",
                "18": "niveau d'importance (Actif récent/Important/Archive/À purger)",
            }[choice]
            path = input(f"Dossier à réorganiser par {label} : ").strip().strip('"')

            print("\nAnalyse en cours (aucune modification pour l'instant)...")
            plan = engine.folder_organizer.build_plan(
                path, mode=mode, excluded_dirs=engine._excluded_dirs
            )
            if not plan:
                print("Rien à réorganiser (dossier vide, introuvable, ou déjà organisé).")
                continue

            print(f"\n{len(plan)} fichier(s) seraient déplacés :")
            for item in plan[:30]:
                print(f"  {item['source']}")
                print(f"    -> {item['destination']}   ({item['reason']})")
            if len(plan) > 30:
                print(f"  ... et {len(plan) - 30} de plus.")

            confirm = input(f"\nAppliquer ce plan ({len(plan)} déplacement(s)) ? (oui/non) : ").strip().lower()
            if confirm == "oui":
                report = engine.folder_organizer.apply_plan(plan)
                print(f"\n{report['moved']} fichier(s) déplacé(s).")
                if report["errors"]:
                    print(f"{len(report['errors'])} erreur(s) :")
                    for e in report["errors"][:10]:
                        print(f"  - {e}")
                print(f"Session : {report['session_id']} (à noter pour annuler via l'option 21)")

        elif choice == "19":
            source = input("Dossier à déplacer (ex: D:\\Projets\\ClientX) : ").strip().strip('"')
            target = input("Dossier de destination (ex: D:\\Archives) : ").strip().strip('"')
            confirm = input(
                f"Confirmer le déplacement de '{source}' dans '{target}' ? (oui/non) : "
            ).strip().lower()
            if confirm == "oui":
                result = engine.folder_organizer.move_folder_into(source, target)
                print(f"\n{result['message']}")
                if result["status"] == "ok":
                    print(f"Session : {result['session_id']} (à noter pour annuler via l'option 21)")
                    if result.get("errors"):
                        print(f"{len(result['errors'])} erreur(s) pendant la fusion :")
                        for e in result["errors"][:10]:
                            print(f"  - {e}")

        elif choice == "20":
            path = input("Dossier à analyser (ex: C:\\Users\\VotreNom\\Documents) : ").strip().strip('"')
            days = input("Considérer comme 'non utilisé' au-delà de combien de jours ? [180 par défaut] : ").strip()
            days = int(days) if days.isdigit() else 180

            refresh = input(
                "Rafraîchir l'index des Éléments récents Windows avant l'analyse "
                "(recommandé si tu viens d'ouvrir des fichiers) ? (o/n) : "
            ).strip().lower()
            if refresh == "o":
                count = engine.folder_organizer.refresh_recent_usage_index()
                print(f"  {count} élément(s) récent(s) indexé(s).")

            print("\nAnalyse en cours...")
            result = engine.folder_organizer.find_least_used_files(path, unused_since_days=days, excluded_dirs=engine._excluded_dirs)
            print(f"\n{result['note']}\n")
            if not result["files"]:
                print("Aucun fichier n'atteint ce seuil d'inutilisation.")
                continue

            print(f"{len(result['files'])} fichier(s) non utilisé(s) depuis plus de {days} jours :")
            for f in result["files"][:30]:
                print(f"  {f['age_days']:>5} j  {f['size_mb']:>8} Mo  {f['path']}")
            if len(result["files"]) > 30:
                print(f"  ... et {len(result['files']) - 30} de plus.")

            confirm = input(
                "\nLes ranger dans un sous-dossier '00_Non_utilises_depuis_longtemps' ? (oui/non) : "
            ).strip().lower()
            if confirm == "oui":
                report = engine.folder_organizer.organize_least_used(path, unused_since_days=days, excluded_dirs=engine._excluded_dirs)
                print(f"\n{report['moved']} fichier(s) rangé(s).")
                if report["errors"]:
                    print(f"{len(report['errors'])} erreur(s) :")
                    for e in report["errors"][:10]:
                        print(f"  - {e}")
                print(f"Session : {report['session_id']} (à noter pour annuler via l'option 21)")

        elif choice == "21":
            sessions = engine.folder_organizer.list_sessions()
            if not sessions:
                print("Aucune session de réorganisation enregistrée.")
                continue
            print("\n--- Sessions de réorganisation (les plus récentes d'abord) ---")
            for s in sessions:
                status = f"{s['count'] - s['undone']} actif(s) / {s['count']} total" if s["undone"] else f"{s['count']} déplacement(s)"
                print(f"  {s['session_id']}  —  {s['date']}  —  {status}")
            sid = input("\nID de session à annuler (Entrée pour revenir) : ").strip()
            if sid:
                result = engine.folder_organizer.undo_session(sid)
                print(f"\n{result['restored']} fichier(s) restauré(s) à leur emplacement d'origine.")
                if result["errors"]:
                    print(f"{len(result['errors'])} erreur(s) :")
                    for e in result["errors"][:10]:
                        print(f"  - {e}")

        elif choice == "22":
            print(
                "\nLe MODE GARDIEN va, en une seule fois :\n"
                "  1. Nettoyer TEMP / %temp% / caches navigateurs / corbeille\n"
                "  2. Mettre de côté les fichiers non utiles détectés (RÉVERSIBLE, rien n'est supprimé ici)\n"
                "  3. Scanner Téléchargements, Bureau et Documents (quarantaine automatique des menaces)\n"
                "  4. Ranger les fichiers non utilisés depuis plus de 180 jours\n"
                "  5. Activer la protection continue (anti-ransomware + surveillance temps réel)\n"
                "\nAucune suppression définitive n'aura lieu sans une confirmation séparée à la fin.\n"
            )
            confirm = input("Lancer le Mode Gardien maintenant ? (oui/non) : ").strip().lower()
            if confirm != "oui":
                continue

            print("\n--- Étape 1/3 : passe de maintenance ---")
            report = engine.guardian.run_full_pass(auto_apply_organization=True)
            print(f"Nettoyage : {report['temp_cleanup']['total_freed_mb']} Mo libérés")
            for f in report["staged_for_deletion"]["folders"]:
                if "error" in f:
                    continue
                print(f"  Mis de côté {f['folder']} : {f['staged']} fichier(s) non utile(s) "
                      f"(+ {f['needs_manual_review']} à examiner manuellement via l'option 11)")
            for s in report["scans"]:
                print(f"  Scan {s['folder']} : {s['files_scanned']} fichier(s), {s['threats_quarantined']} menace(s) mise(s) en quarantaine")
            for r in report["reorganizations"]:
                print(f"  Rangement {r['folder']} : {r.get('moved', 0)} fichier(s) non utilisé(s) déplacé(s) (session {r.get('session_id', 'N/A')})")

            print("\n--- Étape 2/3 : protection continue ---")
            activated = engine.guardian.activate_continuous_protection()
            print(f"Bouclier anti-ransomware : {'activé' if activated['ransomware_shield'] else 'déjà actif'}")
            print(f"Surveillance temps réel  : {'démarrée en arrière-plan' if activated['realtime_protection'] else 'déjà active'}")

            print("\n--- Étape 3/3 : validation avant suppression définitive ---")
            pending = engine.guardian.review_pending_deletions()
            if not pending:
                print("Aucun fichier en attente de suppression définitive.")
            else:
                print(f"{len(pending)} fichier(s) actuellement mis de côté (incluant les mises de côté précédentes) :")
                for entry in pending[:20]:
                    print(f"  {entry['date'][:10]}  {entry['original_path']}  ({entry['reason']})")
                if len(pending) > 20:
                    print(f"  ... et {len(pending) - 20} de plus.")
                purge_confirm = input(
                    "\nSupprimer DÉFINITIVEMENT ceux mis de côté depuis plus de 30 jours ? "
                    "(oui/non — les autres restent en attente) : "
                ).strip().lower()
                if purge_confirm == "oui":
                    deleted = engine.guardian.confirm_permanent_deletion(older_than_days=30)
                    print(f"{deleted} fichier(s) supprimé(s) définitivement.")
                else:
                    print("Rien n'a été supprimé — les fichiers restent récupérables via l'option 12.")

            print("\nMODE GARDIEN actif. Le menu reste utilisable normalement.")

        elif choice == "23":
            time_str = input("Heure d'exécution quotidienne [09:00 par défaut] : ").strip() or "09:00"
            script = os.path.abspath(__file__)
            result = TaskScheduler.create_daily_guardian_task(script, time_str)
            print(f"\n{result['message']}")
            if result["status"] == "ok":
                print(
                    "Le Mode Gardien complet (nettoyage + scan + rangement) tournera "
                    "désormais chaque jour, même sans ouvrir le programme."
                )

        elif choice == "24":
            result = TaskScheduler.remove_guardian_task()
            print(f"\n{result['message']}")

        elif choice == "25":
            sort_choice = input(
                "Trier par : (T)aille décroissante / (N)om / (B)loatware en premier ? [T par défaut] : "
            ).strip().upper() or "T"
            sort_by = {"T": "size", "N": "name", "B": "bloatware_first"}.get(sort_choice, "size")

            print("\nRécupération de la liste des applications installées...")
            result = engine.app_manager.list_all_sorted(sort_by=sort_by)
            print(f"\n{result['total']} application(s) trouvée(s), dont {result['known_bloatware_count']} bloatware connu.\n")
            for idx, app in enumerate(result["apps"]):
                tag = " [BLOATWARE CONNU]" if app.get("known_bloatware") else ""
                size = f"{app['size_mb']} Mo" if app.get("type") == "win32" else "UWP/Store"
                print(f"  [{idx}] {app['name']}{tag}")
                print(f"       {size} — {app.get('publisher', '')} — {app.get('version', '')}")

        elif choice == "26":
            result = engine.app_manager.list_all_sorted(sort_by="name")
            apps = result["apps"]
            if not apps:
                print("Aucune application détectée (ou fonctionnalité Windows uniquement).")
                continue
            for idx, app in enumerate(apps):
                tag = " [BLOATWARE CONNU]" if app.get("known_bloatware") else ""
                print(f"  [{idx}] {app['name']}{tag} ({app['type']})")

            sub = input("\nIndex de l'application à désinstaller, ou Entrée pour revenir : ").strip()
            if not sub.isdigit() or not (0 <= int(sub) < len(apps)):
                continue
            target = apps[int(sub)]
            confirm = input(f"Confirmer la désinstallation de '{target['name']}' ? (oui/non) : ").strip().lower()
            if confirm == "oui":
                result = engine.app_manager.uninstall(target)
                print(f"\n{result['message']}")

        elif choice == "27":
            print("\nRecherche du bloatware Microsoft connu (applications UWP/Store uniquement)...")
            uwp_apps = engine.app_manager.list_uwp_apps()
            candidates = [a for a in uwp_apps if a.get("known_bloatware")]
            if not candidates:
                print("Aucun bloatware connu détecté.")
                continue

            print(f"\n{len(candidates)} application(s) candidate(s) à la suppression :")
            for app in candidates:
                print(f"  - {app['name']} ({app['bloatware_reason']})")

            confirm = input(
                f"\nDésinstaller ces {len(candidates)} application(s) maintenant ? (oui/non) : "
            ).strip().lower()
            if confirm == "oui":
                report = engine.app_manager.remove_known_bloatware(candidates)
                print(f"\n{len(report['removed'])}/{report['total_candidates']} application(s) désinstallée(s) : {report['removed']}")
                if report["errors"]:
                    print(f"{len(report['errors'])} erreur(s) :")
                    for e in report["errors"]:
                        print(f"  - {e}")

        elif choice == "28":
            print("\nRecherche des raccourcis orphelins (Bureau / Menu Démarrer)...")
            orphans = engine.residue_cleaner.find_orphaned_shortcuts()
            if not orphans:
                print("Aucun raccourci orphelin détecté (ou pywin32 non installé — pip install pywin32).")
                continue
            print(f"\n{len(orphans)} raccourci(s) orphelin(s) détecté(s) :")
            for o in orphans:
                print(f"  {o['path']}")
                print(f"    -> cible manquante : {o['target']}")
            confirm = input(f"\nMettre de côté ces {len(orphans)} raccourci(s) ? (oui/non) : ").strip().lower()
            if confirm == "oui":
                report = engine.residue_cleaner.stage_orphaned_shortcuts(orphans)
                print(f"\n{report['staged']}/{report['total_candidates']} raccourci(s) mis de côté (récupérables via l'option 12).")
                if report["errors"]:
                    print(f"{len(report['errors'])} erreur(s) : {report['errors']}")

        elif choice == "29":
            print("\nRecherche des entrées de registre 'Uninstall' orphelines...")
            orphans = engine.residue_cleaner.find_orphaned_uninstall_entries()
            if not orphans:
                print("Aucune entrée orpheline détectée.")
                continue
            print(f"\n{len(orphans)} entrée(s) orpheline(s) détectée(s) :")
            for idx, o in enumerate(orphans):
                print(f"  [{idx}] {o['display_name']} ({o['hive_name']}) — chemin manquant : {o['install_location']}")

            sub = input(
                "\n(T)outes supprimer / index précis / Entrée pour revenir : "
            ).strip().upper()
            if sub == "T":
                confirm = input(f"Confirmer la suppression des {len(orphans)} entrées (sauvegardées avant suppression) ? (oui/non) : ").strip().lower()
                if confirm == "oui":
                    for o in orphans:
                        result = engine.residue_cleaner.backup_and_remove_uninstall_entry(o)
                        print(f"  {result['message']}")
            elif sub.isdigit() and 0 <= int(sub) < len(orphans):
                target = orphans[int(sub)]
                confirm = input(f"Confirmer la suppression de '{target['display_name']}' (sauvegardée avant suppression) ? (oui/non) : ").strip().lower()
                if confirm == "oui":
                    result = engine.residue_cleaner.backup_and_remove_uninstall_entry(target)
                    print(f"\n{result['message']}")

        elif choice == "30":
            print(
                "\nRecherche de dossiers dans Program Files / AppData / ProgramData "
                "ne correspondant à aucune application installée...\n"
                "(uniquement des dossiers non modifiés depuis 30+ jours — diagnostic prudent)"
            )
            candidates = engine.residue_cleaner.find_candidate_orphaned_folders()
            if not candidates:
                print("Aucun dossier orphelin candidat détecté.")
                continue

            print(f"\n{len(candidates)} dossier(s) candidat(s) — À EXAMINER UN PAR UN (jamais de suppression groupée ici) :")
            for idx, c in enumerate(candidates):
                print(f"  [{idx}] {c['path']}")
                print(f"       {c['size_mb']} Mo — {c['reason']}")

            sub = input("\nIndex du dossier à mettre de côté, ou Entrée pour revenir : ").strip()
            if sub.isdigit() and 0 <= int(sub) < len(candidates):
                target = candidates[int(sub)]
                print(f"\nDossier : {target['path']} ({target['size_mb']} Mo)")
                confirm = input("Confirmer la mise de côté de CE dossier ? (oui/non) : ").strip().lower()
                if confirm == "oui":
                    result = engine.residue_cleaner.stage_orphaned_folder(target["path"], target["reason"])
                    print(f"\n{result['message']}")

        elif choice == "31":
            print("\nMise à jour des bases de détection depuis les sources publiques.")
            print("Les bases ne sont JAMAIS remplacées par des données invalides :")
            print("en cas d'échec ou d'absence de réseau, celles en place restent utilisables.\n")
            from optimizer.signature_updater import SignatureUpdater
            updater = SignatureUpdater()

            print("Empreintes (MalwareBazaar)...")
            rh = updater.update_hashes(force=True)
            if rh["status"] == "ok":
                print(f"  {rh['ajoutees']} empreinte(s) installée(s).")
            elif rh["status"] == "inchange":
                print("  Déjà à jour.")
            else:
                print(f"  Échec : {rh.get('raison')}")

            print("\nRègles YARA (signature-base, Florian Roth)...")
            ry = updater.update_yara_rules(force=True)
            if ry["status"] == "ok":
                print(f"  {ry['retenues']} règle(s) installée(s).")
                if ry.get("ecartees"):
                    print(f"  {ry['ecartees']} règle(s) écartée(s) car elles ne compilent pas "
                          f"— écartées une par une, sans désarmer les autres.")
                if ry.get("rejetees_assemblage"):
                    print(f"  {len(ry['rejetees_assemblage'])} écartée(s) à l'assemblage.")
                # Rechargement à chaud des empreintes uniquement : HashScanner
                # relit son fichier, tandis que YaraScanner compile ses règles
                # une seule fois à l'initialisation et n'expose pas de
                # rechargement. Les nouvelles règles YARA ne seront donc
                # actives qu'au prochain lancement — le dire plutôt que de
                # laisser croire à une prise en compte immédiate.
                try:
                    engine.hash_scanner.reload()
                    print("\n  Empreintes rechargées, actives immédiatement.")
                except Exception as e:
                    print(f"\n  (rechargement des empreintes impossible : {e})")
                print("  Règles YARA : actives au prochain lancement de l'application.")
            else:
                print(f"  Échec : {ry.get('raison')}")
                if ry.get("echecs_reseau"):
                    for e in ry["echecs_reseau"]:
                        print(f"    - {e}")

        elif choice == "32":
            from security.intrusion_check import IntrusionCheck
            print("\nRecherche des accès à cet ordinateur...\n")
            d = IntrusionCheck().rapport(jours=7)["data"]

            if not d["constats"]:
                print("Aucun constat.")
            for c in d["constats"]:
                marque = {"important": "[!]", "a_verifier": "[?]",
                          "information": "[ ]"}[c["niveau"]]
                print(f"{marque} {c['titre']}")
                print(f"      {c['detail']}\n")

            indisponibles = [k for k, v in d["sources"].items() if v != "ok"]
            if indisponibles:
                # Ne jamais masquer une source muette : un rapport incomplet
                # présenté comme complet est pire qu'un rapport absent.
                print("Sources indisponibles (rapport partiel) :")
                for k in indisponibles:
                    print(f"  - {k} : {d['sources'][k]}")
                print()
            print(d["avertissement"])

        elif choice == "33":
            from security.intrusion_check import IntrusionCheck
            ic = IntrusionCheck()
            print("\nTracer les accès FUTURS à un dossier.")
            print("Rappel : le passé n'a jamais été enregistré par Windows,")
            print("il est donc définitivement irrécupérable.\n")
            dossier = input("Dossier à tracer (Entrée pour annuler) : ").strip()
            if not dossier:
                continue
            plan = ic.preparer_audit_fichiers([dossier])
            if not plan.get("ok"):
                print(plan.get("error"))
                continue
            print("\nCe qui sera fait :")
            for e in plan["data"]["etapes"]:
                print(f"  - {e}")
            print("\nAvertissements :")
            for a in plan["data"]["avertissements"]:
                print(f"  ! {a}")
            if input("\nConfirmer ? (oui/non) : ").strip().lower() == "oui":
                r = ic.activer_audit_fichiers(plan["data"])
                if r.get("ok"):
                    print(f"\n{r['data']['rappel']}")
                else:
                    print(f"\nÉchec : {r.get('error') or r.get('reason')}")

        elif choice == "34":
            from security.camera_watch import CameraWatch
            cw = CameraWatch()
            d = cw.etat()["data"]

            if d["alertes"]:
                print(f"\n!!  {len(d['alertes'])} ACCÈS NON AUTORISÉ EN COURS\n")
                for a in d["alertes"]:
                    print(f"    {a['appareil_lisible'].upper()} — {a['application']}")
                    print(f"      {a['chemin']}")
                    print(f"      depuis {a['debut']}\n")
            elif d["en_cours"]:
                print("\nAppareils utilisés, toutes applications autorisées :")
                for a in d["en_cours"]:
                    print(f"  {a['appareil_lisible']} — {a['application']}")
            else:
                print("\nNi la caméra ni le microphone ne sont utilisés.")

            if d["acces"]:
                print("\nHistorique :")
                for a in d["acces"]:
                    etat = "EN COURS" if a["en_cours"] else "terminé "
                    marque = "" if a["autorisee"] else "  <-- non autorisée"
                    print(f"  [{etat}] {a['appareil_lisible']:11s} "
                          f"{a['application']}{marque}")

            muettes = [k for k, v in d["sources"].items() if v != "ok"]
            if muettes:
                print("\nSources indisponibles (rapport partiel) :")
                for k in muettes:
                    print(f"  - {k} : {d['sources'][k]}")

            print(f"\n{d['rappel']}")

            if d["alertes"]:
                rep = input("\nAutoriser une de ces applications ? "
                            "(nom exact, ou Entrée) : ").strip()
                if rep:
                    r = cw.autoriser(rep)
                    print("Autorisée." if r.get("ok") else r.get("error"))

        elif choice == "35":
            from security.camera_watch import CameraWatch
            cw = CameraWatch()
            print("\nSurveillance de la caméra et du microphone.")
            print("Une notification s'affichera si une application NON autorisée")
            print("les active. Ctrl+C pour arrêter.\n")
            autorisees = cw.autorisations()
            print(f"Applications autorisées : "
                  f"{', '.join(autorisees) if autorisees else 'aucune pour l instant'}")
            print("(utilise l'option 34 pour en déclarer)\n")

            def signaler(a):
                print(f"  [!] {a['appareil_lisible'].upper()} activée par "
                      f"{a['application']} — {a['chemin']}")

            cw.surveiller(rappel=signaler, intervalle=5.0)
            try:
                while cw.surveillance_active:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
            finally:
                cw.arreter()
                print("\nSurveillance arrêtée.")

        elif choice == "36":
            from security.app_firewall import AppFirewall
            af = AppFirewall()
            res = af.lister_regles()
            if not res.get("ok"):
                print(f"\n{res.get('reason') or res.get('error')}")
                continue

            regles = res["data"]["regles"]
            print(f"\n{len(regles)} application(s) bloquée(s) par ANTI-ZEEVIRIUS :")
            for i, r in enumerate(regles):
                etat = "active" if r["active"] else "désactivée"
                print(f"  [{i}] {r['application']} ({r['sens']}, {etat})")
                print(f"       {r['programme']}")
            print(f"\n{res['data']['note']}")

            print("\n  b = bloquer une application    d = débloquer    Entrée = revenir")
            action = input("Action : ").strip().lower()

            if action == "b":
                chemin = input("Chemin complet du programme : ").strip()
                if not chemin:
                    continue
                plan = af.preparer_blocage(chemin)
                if not plan.get("ok"):
                    print(f"\nRefusé : {plan.get('error')}")
                    continue
                d = plan["data"]
                if d["deja_bloquee"]:
                    print("\nCette application est déjà bloquée.")
                    continue
                print("\nCe qui sera fait :")
                for e in d["etapes"]:
                    print(f"  - {e}")
                print("\nAvertissements :")
                for a in d["avertissements"]:
                    print(f"  ! {a}")
                if input("\nConfirmer le blocage ? (oui/non) : ").strip().lower() == "oui":
                    r = af.bloquer(d)
                    print(f"\n{d['application']} bloquée." if r.get("ok")
                          else f"\nÉchec : {r.get('error') or r.get('reason')}")

            elif action == "d":
                idx = input("Index de la règle à retirer : ").strip()
                if idx.isdigit() and 0 <= int(idx) < len(regles):
                    cible = regles[int(idx)]
                    r = af.debloquer(cible["nom"])
                    print(f"\n{cible['application']} débloquée." if r.get("ok")
                          else f"\nÉchec : {r.get('error') or r.get('reason')}")

        elif choice == "0":
            if engine._realtime_monitor is not None:
                engine.stop_realtime_protection()
            print("Fermeture de l'antivirus.")
            break

        else:
            print("Choix invalide.")


if __name__ == "__main__":
    main()
