"""
reputation_checker.py
Reproduit le principe de "détection cloud" des antivirus premium
(Bitdefender, Kaspersky) : au lieu de se fier uniquement à une base de
signatures locale, on interroge un service de réputation partagé —
ici l'API publique VirusTotal, qui agrège les résultats de 70+ moteurs
antivirus.

IMPORTANT — Nécessite une clé API VirusTotal GRATUITE :
1. Crée un compte sur https://www.virustotal.com/gui/join-us
2. Récupère ta clé API personnelle sur https://www.virustotal.com/gui/my-apikey
3. Renseigne-la dans signatures/vt_api_key.txt (jamais dans le code)

Limite du niveau gratuit : 4 requêtes/minute, 500/jour — largement
suffisant pour un usage personnel, mais pas pour scanner un disque entier
en continu (contrairement aux clouds propriétaires de Bitdefender/Kaspersky
qui n'ont pas cette limite pour leurs propres clients).

Confidentialité : envoyer un HASH ne transmet PAS le contenu du fichier
(VirusTotal ne reçoit qu'une empreinte, pas le fichier lui-même — sauf si
tu utilises explicitement la fonction d'upload, absente ici par choix).
"""

import json
import math
import time
from pathlib import Path
from typing import Dict, Optional

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

VT_API_BASE = "https://www.virustotal.com/api/v3"
RATE_LIMIT_SECONDS = 16  # 4 req/min sur le tier gratuit = 1 requête toutes les 15s min

# Score Z pour un intervalle de confiance à 95% (loi normale centrée réduite,
# P(-1.96 < Z < 1.96) = 0.95) — utilisé par la borne de Wilson ci-dessous.
Z_95 = 1.959964


def wilson_lower_bound(successes: int, trials: int, z: float = Z_95) -> float:
    """
    Borne inférieure de l'intervalle de Wilson pour une proportion binomiale.

    Problème avec un seuil absolu (l'ancien code : "MALVEILLANT si
    malicious_count >= 3") : il traite de façon identique 3 détections sur
    5 moteurs interrogés (60% - très suspect) et 3 détections sur 70 moteurs
    (4.3% - probablement un faux positif isolé d'un moteur peu fiable).
    Le nombre de moteurs qui répondent varie réellement sur VirusTotal
    (certains fichiers récents n'ont pas encore été scannés par tous les
    moteurs), donc un seuil sur un COMPTE brut n'est pas statistiquement
    valide — il faut raisonner sur une PROPORTION, avec un ajustement pour
    la taille de l'échantillon.

    p̂ = successes / trials (proportion observée)

    Borne de Wilson (plus fiable que l'intervalle normal naïf p̂ ± z·σ
    quand trials est petit ou p̂ proche de 0 ou 1, cas fréquent ici avec
    peu de détections sur ~70 moteurs) :

        L = ( p̂ + z²/(2n) − z · √( p̂(1−p̂)/n + z²/(4n²) ) ) / (1 + z²/n)

    L est la valeur basse de l'intervalle : "on peut affirmer avec 95% de
    confiance que la vraie proportion de moteurs qui détecteraient ce
    fichier comme malveillant est AU MOINS L". C'est une estimation
    prudente (conservatrice), adaptée à une décision de sécurité où l'on
    préfère sous-estimer plutôt que sur-estimer la confiance.
    """
    if trials <= 0:
        return 0.0
    n = float(trials)
    p_hat = successes / n
    denom = 1.0 + (z * z) / n
    center = p_hat + (z * z) / (2.0 * n)
    margin = z * math.sqrt((p_hat * (1.0 - p_hat) / n) + (z * z) / (4.0 * n * n))
    return max(0.0, (center - margin) / denom)


class ReputationChecker:
    def __init__(self, api_key_path: str, cache_path: str):
        self.api_key_path = Path(api_key_path)
        self.cache_path = Path(cache_path)
        self._last_request_time = 0.0
        self._cache: Dict[str, Dict] = {}
        self._load_cache()

    def _load_cache(self) -> None:
        """Cache local des résultats déjà interrogés — évite de
        re-consommer le quota gratuit pour un hash déjà vérifié."""
        if self.cache_path.exists():
            try:
                self._cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._cache = {}

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self._cache, indent=2), encoding="utf-8")

    def _get_api_key(self) -> Optional[str]:
        if not self.api_key_path.exists():
            return None
        key = self.api_key_path.read_text(encoding="utf-8").strip()
        return key if key else None

    def is_configured(self) -> bool:
        return REQUESTS_AVAILABLE and self._get_api_key() is not None

    def _respect_rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < RATE_LIMIT_SECONDS:
            time.sleep(RATE_LIMIT_SECONDS - elapsed)

    def check_hash(self, sha256_hash: str) -> Dict:
        """
        Interroge la réputation d'un hash SHA-256 sur VirusTotal.
        Retourne un dict avec le nombre de moteurs qui le détectent
        comme malveillant sur le total de moteurs interrogés.
        """
        sha256_hash = sha256_hash.lower()

        if sha256_hash in self._cache:
            return {**self._cache[sha256_hash], "from_cache": True}

        if not REQUESTS_AVAILABLE:
            return {"status": "erreur", "reason": "Module 'requests' non installé (pip install requests)"}

        api_key = self._get_api_key()
        if not api_key:
            return {
                "status": "non_configuré",
                "reason": f"Clé API absente. Crée le fichier {self.api_key_path} avec ta clé VirusTotal.",
            }

        self._respect_rate_limit()
        self._last_request_time = time.time()

        try:
            response = requests.get(
                f"{VT_API_BASE}/files/{sha256_hash}",
                headers={"x-apikey": api_key},
                timeout=15,
            )
        except requests.RequestException as e:
            return {"status": "erreur", "reason": f"Erreur réseau : {e}"}

        if response.status_code == 404:
            result = {
                "status": "inconnu",
                "reason": "Hash jamais soumis à VirusTotal (fichier probablement rare/nouveau, pas forcément suspect)",
                "malicious_count": 0,
                "total_engines": 0,
            }
        elif response.status_code == 200:
            data = response.json()
            stats = data.get("data", {}).get("attributes", {}).get("last_analysis_stats", {})
            malicious = stats.get("malicious", 0)
            suspicious = stats.get("suspicious", 0)
            total = sum(stats.values()) if stats else 0

            # Verdict fondé sur une proportion (borne basse de Wilson à 95%)
            # plutôt qu'un compte absolu — voir wilson_lower_bound() plus haut
            # pour la justification statistique. confidence = 0 si total = 0
            # (évite une division par zéro et est traité comme "SAIN").
            confidence = wilson_lower_bound(malicious, total) if total > 0 else 0.0
            if malicious == 0:
                verdict = "SAIN"
            elif confidence >= 0.15:
                # 95% de confiance qu'AU MOINS 15% des moteurs détecteraient
                # ce fichier comme malveillant : signal statistiquement fort,
                # peu compatible avec un simple faux positif isolé.
                verdict = "MALVEILLANT"
            else:
                # Détection(s) présente(s) mais pas assez robuste(s) au vu du
                # nombre de moteurs interrogés — vérification manuelle conseillée.
                verdict = "SUSPECT"

            result = {
                "status": "trouvé",
                "malicious_count": malicious,
                "suspicious_count": suspicious,
                "total_engines": total,
                "confidence_wilson_95": round(confidence, 4),
                "reason": f"{malicious}/{total} moteurs antivirus le signalent comme malveillant "
                          f"(confiance statistique ≥ {confidence:.1%})"
                          if malicious > 0 else "Aucun moteur ne le signale comme malveillant",
                "verdict": verdict,
            }
        elif response.status_code == 429:
            return {"status": "erreur", "reason": "Quota API dépassé (limite gratuite : 500/jour, 4/min)"}
        else:
            return {"status": "erreur", "reason": f"Réponse inattendue : HTTP {response.status_code}"}

        self._cache[sha256_hash] = result
        self._save_cache()
        return {**result, "from_cache": False}
