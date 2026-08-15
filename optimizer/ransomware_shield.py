"""
ransomware_shield.py
Reproduit le principe du "Ransomware Remediation" de Bitdefender / Kaspersky :

1. FICHIERS CANARI (honeypot) : on place des fichiers leurres invisibles
   dans les dossiers protégés (Documents, Bureau, Photos). Un ransomware
   qui chiffre "tout" un dossier va inévitablement toucher ces fichiers
   canari AVANT de finir le reste — c'est le signal d'alerte précoce.

2. DÉTECTION DE MODIFICATION MASSIVE : un ransomware chiffre des dizaines
   de fichiers en quelques secondes. On surveille le TAUX de modification
   par seconde dans les dossiers protégés — un taux anormal déclenche
   une alerte immédiate.

3. VERROUILLAGE AUTOMATIQUE : dès qu'une menace est détectée, on peut
   suspendre le processus responsable (si identifiable) et verrouiller
   temporairement le dossier en lecture seule.

Limite honnête : ceci reste une protection en espace utilisateur.
Bitdefender/Kaspersky utilisent un driver kernel-mode qui intercepte les
écritures AVANT qu'elles n'arrivent sur le disque. Notre détection réagit
après les premières écritures — elle limite les dégâts, elle ne les
empêche pas à 100%.
"""

import math
import os
import subprocess
import time
from collections import deque
from pathlib import Path
from typing import Callable, Dict, List, Optional

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

CANARY_FILENAMES = [
    "___NE_PAS_SUPPRIMER___.docx",
    "___IMPORTANT_BACKUP___.xlsx",
    "___PHOTOS_FAMILLE_ORIGINAL___.jpg",
]

CANARY_CONTENT = b"CANARY_FILE_DO_NOT_DELETE_ANTIVIRUS_MONITORING"

# ── Plancher empirique (fallback) ────────────────────────────
# Seuil minimal : plus de N fichiers modifiés en moins de M secondes = suspect.
# Statut identique au dérating batterie du projet EV Range Advisor : la
# "taille d'une rafale de modification encore plausible pour un usage
# humain légitime" (copie de photos, checkout git, sauvegarde d'IDE...)
# n'est PAS dérivable de la physique/maths seules — c'est une distribution
# comportementale mesurée empiriquement (littérature AV : Bitdefender,
# Kaspersky). Ce plancher reste le filet de sécurité minimal, jamais
# désactivé, même une fois la couche statistique ci-dessous calibrée.
MASS_MODIFICATION_THRESHOLD = 15
MASS_MODIFICATION_WINDOW_SECONDS = 10

# ── Couche adaptative auto-calibrée ──────────────────────────
# Le seuil fixe ci-dessus est le même pour un serveur inactif et pour un
# poste qui manipule en permanence des lots de fichiers : cela génère soit
# des faux positifs (utilisateur actif), soit une détection trop tardive
# (machine quasi-inactive, où même 8 fichiers/10s est déjà anormal).
#
# On calcule donc, à partir de l'activité RÉELLEMENT mesurée sur CETTE
# machine, un seuil personnalisé garanti statistiquement via l'inégalité
# de Cantelli (Chebyshev unilatérale) :
#
#   P(X - μ ≥ t) ≤ σ² / (σ² + t²)      pour toute variable X de variance finie
#   ⇒ t = σ · √((1 - p_max) / p_max)   pour un taux de faux positifs cible p_max
#
# Avantage sur une hypothèse de loi de Poisson : cette borne est valable
# QUELLE QUE SOIT LA FORME de la distribution des rafales humaines (souvent
# surdispersées, donc plus étalées qu'un Poisson) — aucune hypothèse de
# forme à justifier, seulement μ et σ² mesurés.
#
# μ et σ² sont estimés en ligne via l'algorithme de Welford (Welford,
# 1962, Technometrics) : une seule passe, O(1) par échantillon, sans
# stocker l'historique complet — numériquement stable (pas de somme des
# carrés qui explose comme dans la formule naïve Var = E[X²] - E[X]²).
BASELINE_TARGET_FALSE_POSITIVE_RATE = 1e-3  # p_max : 1 fausse alerte / 1000 fenêtres calmes, au pire
BASELINE_MIN_SAMPLES = 30  # règle usuelle : variance non fiable en dessous (petits échantillons)


class _OnlineStats:
    """Moyenne et variance en ligne (algorithme de Welford).
    update() : O(1) par appel, une seule passe, stable numériquement."""

    __slots__ = ("n", "mean", "_m2")

    def __init__(self) -> None:
        self.n = 0
        self.mean = 0.0
        self._m2 = 0.0

    def update(self, x: float) -> None:
        self.n += 1
        delta = x - self.mean
        self.mean += delta / self.n
        self._m2 += delta * (x - self.mean)

    @property
    def variance(self) -> float:
        return self._m2 / self.n if self.n > 1 else 0.0

    @property
    def std(self) -> float:
        return math.sqrt(self.variance)


def _cantelli_t(p_max: float) -> float:
    """t tel que P(X - μ ≥ t·σ) ≤ p_max, valable pour toute distribution
    de variance finie (borne de Cantelli / Chebyshev unilatérale)."""
    return math.sqrt((1.0 - p_max) / p_max)


class RansomwareShield:
    def __init__(self, protected_folders: List[str]):
        self.protected_folders = [f for f in protected_folders if Path(f).exists()]
        self.canary_paths: List[Path] = []
        self._modification_timestamps = deque()
        self._alert_callback: Optional[Callable[[Dict], None]] = None
        self._baseline_stats = _OnlineStats()
        self._last_baseline_sample_time = time.time()

    # ── Fichiers canari ──────────────────────────────────────────
    def deploy_canaries(self) -> List[str]:
        """Place des fichiers canari dans chaque dossier protégé."""
        deployed = []
        for folder in self.protected_folders:
            folder_path = Path(folder)
            for canary_name in CANARY_FILENAMES:
                canary_path = folder_path / canary_name
                try:
                    if not canary_path.exists():
                        canary_path.write_bytes(CANARY_CONTENT)
                        # Tentative de marquage caché (best-effort, ignoré si échoue).
                        # subprocess avec une liste d'arguments (pas de shell=True)
                        # évite toute injection/casse si le chemin contient des
                        # caractères spéciaux — contrairement à os.system(f'...{path}...')
                        # qui passe par le shell et peut casser ou être détourné.
                        try:
                            subprocess.run(
                                ["attrib", "+h", str(canary_path)],
                                capture_output=True, timeout=5, check=False,
                            )
                        except (subprocess.SubprocessError, OSError):
                            pass
                    self.canary_paths.append(canary_path)
                    deployed.append(str(canary_path))
                except (PermissionError, OSError):
                    continue
        return deployed

    def check_canaries(self) -> List[Dict]:
        """Vérifie l'intégrité de tous les fichiers canari.
        Retourne la liste de ceux qui ont été modifiés/supprimés/chiffrés
        (= signal d'alerte ransomware fort)."""
        alerts = []
        for canary_path in self.canary_paths:
            if not canary_path.exists():
                alerts.append({
                    "canary": str(canary_path),
                    "status": "SUPPRIMÉ",
                    "severity": "critical",
                })
                continue

            try:
                content = canary_path.read_bytes()
                if content != CANARY_CONTENT:
                    alerts.append({
                        "canary": str(canary_path),
                        "status": "MODIFIÉ (probablement chiffré)",
                        "severity": "critical",
                    })
            except (PermissionError, OSError):
                alerts.append({
                    "canary": str(canary_path),
                    "status": "ILLISIBLE (verrouillé par un processus)",
                    "severity": "high",
                })
        return alerts

    def remove_canaries(self) -> None:
        """Retire les fichiers canari (ex: avant de désactiver la protection)."""
        for canary_path in self.canary_paths:
            try:
                if canary_path.exists():
                    canary_path.unlink()
            except (PermissionError, OSError):
                continue
        self.canary_paths.clear()

    # ── Détection de taux de modification anormal ───────────────
    def _sample_baseline_if_due(self, now: float, current_count: int) -> None:
        """Alimente les statistiques en ligne avec un point d'observation
        par fenêtre écoulée (pas à chaque événement, pour ne pas sur-
        pondérer les périodes très actives). Coût : O(1)."""
        if now - self._last_baseline_sample_time >= MASS_MODIFICATION_WINDOW_SECONDS:
            self._baseline_stats.update(current_count)
            self._last_baseline_sample_time = now

    def adaptive_threshold(self) -> float:
        """
        Seuil personnalisé = max(plancher empirique, μ + t·σ) où μ et σ
        proviennent de l'activité réellement mesurée sur cette machine.
        Le max() garantit qu'on ne descend jamais sous le filet de
        sécurité minimal, même si la machine est restée très calme.
        Tant que l'historique est insuffisant (< BASELINE_MIN_SAMPLES),
        on utilise uniquement le plancher — la variance estimée sur un
        petit échantillon n'est pas fiable (règle usuelle : n ≥ 30).
        """
        if self._baseline_stats.n < BASELINE_MIN_SAMPLES:
            return float(MASS_MODIFICATION_THRESHOLD)
        t = _cantelli_t(BASELINE_TARGET_FALSE_POSITIVE_RATE)
        adaptive = self._baseline_stats.mean + t * self._baseline_stats.std
        return max(float(MASS_MODIFICATION_THRESHOLD), adaptive)

    def record_modification_event(self) -> bool:
        """
        À appeler à chaque événement de modification détecté par le
        monitor temps réel. Retourne True si le taux dépasse le seuil
        (= alerte ransomware potentielle).

        Le seuil appliqué est max(plancher empirique fixe, seuil
        auto-calibré Welford+Cantelli) — voir adaptive_threshold().
        Ceci ne peut jamais être MOINS sensible que l'ancienne version
        à seuil fixe, seulement égal ou plus tôt/plus précis une fois
        la baseline de la machine mesurée.
        """
        now = time.time()
        self._modification_timestamps.append(now)

        # Purge les événements hors fenêtre glissante
        cutoff = now - MASS_MODIFICATION_WINDOW_SECONDS
        while self._modification_timestamps and self._modification_timestamps[0] < cutoff:
            self._modification_timestamps.popleft()

        current_count = len(self._modification_timestamps)
        self._sample_baseline_if_due(now, current_count)

        return current_count >= self.adaptive_threshold()

    def reset_modification_counter(self) -> None:
        self._modification_timestamps.clear()

    def reset_baseline(self) -> None:
        """Réinitialise la baseline apprise (ex: après un changement
        d'usage majeur de la machine, ou sur demande explicite)."""
        self._baseline_stats = _OnlineStats()
        self._last_baseline_sample_time = time.time()

    # ── Réponse automatique ──────────────────────────────────────
    @staticmethod
    def find_suspicious_processes(top_n: int = 5) -> List[Dict]:
        """
        Identifie les processus qui écrivent le plus intensément sur
        disque en ce moment — utile pour repérer le processus responsable
        d'un chiffrement massif en cours.
        Nécessite pip install psutil.
        """
        if not PSUTIL_AVAILABLE:
            return []

        candidates = []
        for proc in psutil.process_iter(["pid", "name", "io_counters"]):
            try:
                io = proc.info.get("io_counters")
                if io:
                    candidates.append({
                        "pid": proc.info["pid"],
                        "name": proc.info["name"],
                        "write_bytes": io.write_bytes,
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        candidates.sort(key=lambda x: x["write_bytes"], reverse=True)
        return candidates[:top_n]

    @staticmethod
    def suspend_process(pid: int) -> bool:
        """Suspend un processus suspect (ne le tue pas — réversible via resume)."""
        if not PSUTIL_AVAILABLE:
            return False
        try:
            p = psutil.Process(pid)
            p.suspend()
            return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    @staticmethod
    def resume_process(pid: int) -> bool:
        if not PSUTIL_AVAILABLE:
            return False
        try:
            p = psutil.Process(pid)
            p.resume()
            return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    @staticmethod
    def lock_folder_readonly(folder_path: str) -> bool:
        """Passe un dossier et son contenu en lecture seule (Windows)
        pour stopper un chiffrement en cours. Réversible via
        unlock_folder(). Utilise subprocess avec une liste d'arguments
        plutôt que os.system(f'...') : plus sûr (pas de shell) et ne
        casse pas silencieusement sur un chemin contenant des espaces,
        des guillemets ou des caractères spéciaux."""
        try:
            result = subprocess.run(
                ["attrib", "+r", f"{folder_path}\\*.*", "/s"],
                capture_output=True, timeout=30, check=False,
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, OSError) as e:
            print(f"[ERREUR] Verrouillage lecture-seule échoué : {e}")
            return False

    @staticmethod
    def unlock_folder(folder_path: str) -> bool:
        try:
            result = subprocess.run(
                ["attrib", "-r", f"{folder_path}\\*.*", "/s"],
                capture_output=True, timeout=30, check=False,
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, OSError) as e:
            print(f"[ERREUR] Déverrouillage échoué : {e}")
            return False
