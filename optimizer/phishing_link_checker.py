"""
phishing_link_checker.py
Reproduit le principe de "Chat Protection" / "Web Protection" (Bitdefender,
Kaspersky Safe Money) : vérifie un lien avant que l'utilisateur ne clique
dessus, contre une liste noire de phishing publique et régulièrement mise
à jour.

Source utilisée : OpenPhish (flux public, gratuit, mis à jour en continu)
https://openphish.com/phishing_targets.html — même type de source
qu'utilisent de nombreux moteurs anti-phishing commerciaux en complément
de leur propre télémétrie.

Limite honnête : Bitdefender/Kaspersky combinent PLUSIEURS flux propriétaires
+ leur propre télémétrie de centaines de millions d'utilisateurs en temps
réel. Un flux public gratuit comme OpenPhish est solide mais moins exhaustif
et moins réactif (mise à jour périodique, pas seconde par seconde).
"""

import re
import time
from pathlib import Path
from typing import Dict, Optional, Set
from urllib.parse import urlparse

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

OPENPHISH_FEED_URL = "https://openphish.com/feed.txt"
CACHE_MAX_AGE_SECONDS = 3600  # rafraîchir la liste noire toutes les heures

# Heuristiques locales complémentaires (fonctionnent même hors ligne)
SUSPICIOUS_PATTERNS = [
    r"paypal.*\.(tk|ml|ga|cf)$",           # faux domaines paypal sur TLD gratuits suspects
    r"(banque|bank).*-securite",            # typosquatting bancaire courant
    r"^\d+\.\d+\.\d+\.\d+",                 # URL utilisant une IP brute au lieu d'un domaine
]

SUSPICIOUS_TLDS = {".tk", ".ml", ".ga", ".cf", ".gq"}  # TLDs gratuits très utilisés en phishing


class PhishingLinkChecker:
    def __init__(self, blocklist_cache_path: str):
        self.cache_path = Path(blocklist_cache_path)
        self._blocklist: Set[str] = set()
        self._last_update = 0.0
        self._load_local_cache()

    def _load_local_cache(self) -> None:
        if self.cache_path.exists():
            self._blocklist = set(self.cache_path.read_text(encoding="utf-8").splitlines())
            self._last_update = self.cache_path.stat().st_mtime

    def update_blocklist(self, force: bool = False) -> Dict:
        """Télécharge la dernière liste noire OpenPhish."""
        if not REQUESTS_AVAILABLE:
            return {"status": "erreur", "reason": "Module 'requests' non installé"}

        if not force and (time.time() - self._last_update) < CACHE_MAX_AGE_SECONDS:
            return {"status": "ok", "reason": "Cache encore valide, pas de mise à jour nécessaire"}

        try:
            response = requests.get(OPENPHISH_FEED_URL, timeout=15)
            response.raise_for_status()
        except requests.RequestException as e:
            return {"status": "erreur", "reason": f"Erreur réseau : {e}"}

        urls = {line.strip() for line in response.text.splitlines() if line.strip()}
        self._blocklist = urls
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text("\n".join(sorted(urls)), encoding="utf-8")
        self._last_update = time.time()

        return {"status": "ok", "reason": f"{len(urls)} URLs de phishing chargées"}

    def _check_local_heuristics(self, url: str) -> Optional[str]:
        """Vérifications hors ligne, sans dépendre du flux OpenPhish."""
        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        for tld in SUSPICIOUS_TLDS:
            if domain.endswith(tld):
                return f"Domaine sur une extension gratuite fréquemment utilisée en phishing ({tld})"

        # IP brute utilisée à la place d'un nom de domaine (très suspect
        # pour un site "légitime" — vérifié sur le netloc, pas l'URL brute)
        if re.match(r"^\d+\.\d+\.\d+\.\d+(:\d+)?$", domain):
            return "Utilise une adresse IP brute au lieu d'un nom de domaine (fortement suspect)"

        for pattern in SUSPICIOUS_PATTERNS:
            if pattern.startswith(r"^\d+"):
                continue  # déjà couvert ci-dessus via le netloc
            if re.search(pattern, url, re.IGNORECASE):
                return "Correspond à un pattern de phishing connu (typosquatting)"

        return None

    def check_url(self, url: str) -> Dict:
        """
        Vérifie une URL avant que l'utilisateur ne clique.
        Combine : liste noire OpenPhish + heuristiques locales.
        """
        url_normalized = url.strip().rstrip("/")

        # 1. Vérification exacte contre la liste noire
        if url_normalized in self._blocklist or url in self._blocklist:
            return {
                "url": url,
                "verdict": "PHISHING CONFIRMÉ",
                "reason": "Présent dans la liste noire OpenPhish (signalé par la communauté)",
                "safe_to_click": False,
            }

        # 2. Vérification du domaine seul (au cas où l'URL complète diffère légèrement)
        parsed = urlparse(url)
        for blocked_url in self._blocklist:
            if parsed.netloc and parsed.netloc in blocked_url:
                return {
                    "url": url,
                    "verdict": "DOMAINE SUSPECT",
                    "reason": f"Domaine associé à une URL de phishing connue ({blocked_url})",
                    "safe_to_click": False,
                }

        # 3. Heuristiques locales
        heuristic_reason = self._check_local_heuristics(url)
        if heuristic_reason:
            return {
                "url": url,
                "verdict": "SUSPECT (heuristique)",
                "reason": heuristic_reason,
                "safe_to_click": False,
            }

        return {
            "url": url,
            "verdict": "AUCUNE MENACE CONNUE",
            "reason": "Absent des listes noires et aucun pattern suspect détecté "
                      "(ne garantit pas 100% de sécurité)",
            "safe_to_click": True,
        }
