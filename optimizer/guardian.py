"""
guardian.py
MODE GARDIEN — orchestrateur "un clic" pour ANTI-ZEEVIRIUS.

Plutôt que de naviguer manuellement dans 6+ options de menu séparées à
chaque fois, ce module active en une seule action :
1. La protection continue (bouclier anti-ransomware + surveillance temps
   réel en arrière-plan)
2. L'optimisation des performances (nettoyage TEMP/%TEMP%/caches/corbeille)
3. Le scan antivirus des dossiers sensibles (Téléchargements/Bureau/Documents)
   avec mise en quarantaine automatique
4. Le rangement automatique des fichiers non utilisés

Principe : ce module N'AJOUTE AUCUNE LOGIQUE DE SUPPRESSION NOUVELLE. Il
orchestre uniquement les briques existantes (temp_cleaner, folder_organizer,
quarantine, ransomware_shield, realtime_monitor), qui restent chacune
individuellement réversibles (undo_session, restauration de quarantaine).
Le mode "un clic" ne rend rien MOINS sûr — il évite seulement à Julz de
cliquer 6 fois pour obtenir le même résultat.

Deux modes d'exécution :
- run_full_pass()  → interactif, appelé depuis le menu (option 22),
  seuil d'inutilisation à 180 jours par défaut.
- run_unattended()  → appelé par la tâche planifiée Windows (--guardian,
  voir optimizer/task_scheduler.py et main.py), SANS AUCUNE INTERACTION.
  Seuil plus prudent (365 jours) car personne ne supervise l'exécution :
  un seuil trop agressif en automatique et sans supervision humaine
  pourrait déplacer des fichiers que l'utilisateur cherche à retrouver
  le lendemain sans se souvenir avoir activé le Mode Gardien.

RÈGLE ABSOLUE — validation avant suppression :
Le Mode Gardien peut identifier et METTRE DE CÔTÉ (staging, réversible)
les fichiers jugés "non utiles" (extensions techniques jetables, doublons
— voir FileTriage.SAFE_EXTENSIONS), y compris en automatique/planifié,
car cette étape ne supprime rien : le fichier reste récupérable via
l'option 12 du menu. En revanche, la SUPPRESSION DÉFINITIVE
(purge_staging) n'est JAMAIS déclenchée automatiquement, ni par
run_full_pass(), ni par run_unattended() — uniquement depuis le menu
interactif, après confirmation explicite ("oui" tapé par l'utilisateur),
exactement comme l'option 12 existante. Aucun mode "un clic" ne doit
pouvoir supprimer définitivement des fichiers sans qu'un humain ait
explicitement validé la liste au préalable.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("anti-zeevirius")

INTERACTIVE_UNUSED_THRESHOLD_DAYS = 180
UNATTENDED_UNUSED_THRESHOLD_DAYS = 365
DEFAULT_PURGE_AFTER_DAYS = 30  # délai de rétention en staging avant que la suppression définitive soit proposée


class SystemGuardian:
    def __init__(self, engine):
        # `engine` = instance de AntivirusEngine (main.py). Injection de
        # dépendance plutôt qu'import de main.py, pour éviter tout import
        # circulaire (main.py importe déjà les modules optimizer/*).
        self.engine = engine

    @staticmethod
    def default_folders() -> List[str]:
        """Dossiers surveillés/optimisés par défaut si aucun n'est précisé."""
        home = Path.home()
        return [str(home / "Downloads"), str(home / "Desktop"), str(home / "Documents")]

    def stage_disposable_files(self, folders: List[str]) -> Dict:
        """
        Identifie les fichiers "sûrs à supprimer" (catégorie 'safe' de
        FileTriage : extensions techniques jetables, doublons détectés)
        et les MET DE CÔTÉ — jamais supprimés directement ici.

        La catégorie 'caution' (à vérifier) n'est JAMAIS mise de côté
        automatiquement, même en mode Gardien : elle nécessite un examen
        humain (option 11 du menu), car ce sont des fichiers où une classification
        automatique erronée aurait plus de conséquences (ex: document
        volumineux jamais ouvert mais potentiellement important).
        """
        report: Dict = {"folders": []}
        for folder in folders:
            if not Path(folder).exists():
                continue
            try:
                duplicates = self.engine.disk_analyzer.find_duplicate_files(folder)
                triage = self.engine.file_triage.triage_directory(folder, include_duplicates=duplicates)
            except (OSError, PermissionError) as e:
                report["folders"].append({"folder": folder, "error": str(e)})
                continue

            staged = 0
            for item in triage["safe"]:
                if self.engine.file_triage.move_to_staging(item["path"], item["reason"]):
                    staged += 1

            report["folders"].append({
                "folder": folder,
                "staged": staged,
                "needs_manual_review": len(triage["caution"]),  # jamais touché ici
            })
        return report

    def activate_continuous_protection(self) -> Dict:
        """Active la protection qui reste active en arrière-plan tant que
        le programme tourne : bouclier anti-ransomware + surveillance
        temps réel non bloquante (le menu reste utilisable pendant ce temps —
        voir RealtimeMonitor.start(blocking=False))."""
        from optimizer.ransomware_shield import RansomwareShield

        activated = {"ransomware_shield": False, "realtime_protection": False}

        if self.engine.ransomware_shield is None:
            self.engine.ransomware_shield = RansomwareShield(self.default_folders())
            activated["ransomware_shield"] = True

        if self.engine._realtime_monitor is None:
            self.engine.start_realtime_protection(self.default_folders(), blocking=False)
            activated["realtime_protection"] = True

        return activated

    def run_full_pass(
        self,
        folders: Optional[List[str]] = None,
        unused_threshold_days: int = INTERACTIVE_UNUSED_THRESHOLD_DAYS,
        auto_apply_organization: bool = True,
        scan_files: bool = True,
    ) -> Dict:
        """
        Passe complète de maintenance :
        1. Nettoyage TEMP/%TEMP%/caches navigateurs/corbeille
        2. Scan antivirus de chaque dossier (quarantaine automatique)
        3. Rangement des fichiers non utilisés depuis plus de N jours

        auto_apply_organization=False → prévisualisation seule (aucun
        fichier déplacé), utile pour un aperçu avant confirmation dans
        le menu interactif.
        """
        folders = [f for f in (folders or self.default_folders()) if Path(f).exists()]
        report: Dict = {
            "folders": folders, "temp_cleanup": None, "scans": [],
            "reorganizations": [], "staged_for_deletion": None,
        }

        # 1. Nettoyage TEMP / %TEMP% / caches navigateurs / corbeille
        report["temp_cleanup"] = self.engine.temp_cleaner.run_full_cleanup()

        # 2. Mise de côté des fichiers "non utiles" (RÉVERSIBLE — jamais
        # supprimés directement, voir stage_disposable_files et la règle
        # absolue documentée en tête de fichier).
        report["staged_for_deletion"] = self.stage_disposable_files(folders)

        for folder in folders:
            # 3. Scan antivirus (mise en quarantaine automatique des menaces)
            if scan_files:
                results = self.engine.scan_directory(folder, auto_quarantine=True)
                threats = [r for r in results if r.get("verdict") == "MALVEILLANT"]
                report["scans"].append({
                    "folder": folder,
                    "files_scanned": len(results),
                    "threats_quarantined": len(threats),
                })

            # 4. Rangement des fichiers les moins utilisés
            if auto_apply_organization:
                reorg = self.engine.folder_organizer.organize_least_used(
                    folder, unused_since_days=unused_threshold_days
                )
                reorg["preview_only"] = False
            else:
                found = self.engine.folder_organizer.find_least_used_files(
                    folder, unused_since_days=unused_threshold_days
                )
                reorg = {
                    "preview_only": True,
                    "candidates": len(found["files"]),
                    "files": found["files"],
                    "note": found["note"],
                }
            report["reorganizations"].append({"folder": folder, **reorg})

        return report

    def review_pending_deletions(self) -> List[Dict]:
        """Liste tout ce qui est actuellement en attente de suppression
        définitive (mis de côté par le Mode Gardien ou manuellement via
        l'option 11) — à examiner AVANT tout appel à confirm_permanent_deletion()."""
        return self.engine.file_triage.list_staging()

    def confirm_permanent_deletion(self, older_than_days: int = DEFAULT_PURGE_AFTER_DAYS) -> int:
        """
        Supprime DÉFINITIVEMENT les fichiers mis de côté depuis plus de
        N jours. NE JAMAIS appeler cette méthode sans validation humaine
        explicite au préalable (confirmation "oui" côté menu, voir main.py
        option 22 et 12) — c'est la seule méthode de ce module qui
        supprime réellement des données, et elle n'est appelée nulle part
        automatiquement dans run_full_pass() ou run_unattended().
        """
        return self.engine.file_triage.purge_staging(older_than_days=older_than_days)

    def run_unattended(self) -> Dict:
        """Point d'entrée utilisé par la tâche planifiée Windows (flag
        --guardian, voir main.py). Aucune interaction utilisateur possible
        ici (schtasks lance le script sans console attachée) — seuils plus
        prudents, tout est journalisé plutôt qu'affiché.

        IMPORTANT : cette méthode ne supprime JAMAIS rien définitivement.
        Elle met de côté (staging, réversible) les fichiers jetables
        détectés — la suppression définitive reste réservée au menu
        interactif, après validation humaine explicite (voir docstring de
        confirm_permanent_deletion)."""
        logger.info("MODE GARDIEN — passe automatique planifiée démarrée.")
        report = self.run_full_pass(
            unused_threshold_days=UNATTENDED_UNUSED_THRESHOLD_DAYS,
            auto_apply_organization=True,
            scan_files=True,
        )
        total_threats = sum(s["threats_quarantined"] for s in report["scans"])
        total_moved = sum(
            r.get("moved", 0) for r in report["reorganizations"] if not r.get("preview_only")
        )
        total_staged = sum(
            f.get("staged", 0) for f in report["staged_for_deletion"]["folders"]
        )
        pending_review = sum(
            f.get("needs_manual_review", 0) for f in report["staged_for_deletion"]["folders"]
        )
        logger.info(
            f"MODE GARDIEN terminé : {report['temp_cleanup']['total_freed_mb']} Mo libérés, "
            f"{total_threats} menace(s) mise(s) en quarantaine, "
            f"{total_moved} fichier(s) non utilisé(s) rangé(s), "
            f"{total_staged} fichier(s) jetable(s) mis de côté (récupérables, PAS supprimés), "
            f"{pending_review} fichier(s) à examiner manuellement (jamais touchés automatiquement). "
            f"Aucune suppression définitive n'a été effectuée — voir option 12 du menu pour valider."
        )
        return report
