"""
test_reputation_checker.py
Couvre wilson_lower_bound() dans optimizer/reputation_checker.py — le
remplacement du seuil de verdict absolu ("malicious_count >= 3") par une
décision statistique fondée sur une proportion.

Les valeurs de référence utilisées ci-dessous (10/20 -> 0.2993, 3/5 -> 0.2307,
3/70 -> 0.0147) ont été cross-validées indépendamment avec la bibliothèque
`statsmodels` (proportion_confint(..., method="wilson")) avant d'être écrites
en dur ici, pour éviter de figer une éventuelle erreur d'implémentation.
"""

import pytest

from optimizer.reputation_checker import ReputationChecker, wilson_lower_bound


class _FakeResponse:
    """Simule une réponse requests.Response sans appel réseau réel."""

    def __init__(self, status_code: int, json_data: dict = None):
        self.status_code = status_code
        self._json_data = json_data or {}

    def json(self):
        return self._json_data


def _vt_payload(malicious: int, suspicious: int, harmless: int, undetected: int) -> dict:
    """Construit un payload minimal au format de l'API VirusTotal v3."""
    return {
        "data": {
            "attributes": {
                "last_analysis_stats": {
                    "malicious": malicious,
                    "suspicious": suspicious,
                    "harmless": harmless,
                    "undetected": undetected,
                }
            }
        }
    }


class TestWilsonLowerBound:
    def test_zero_trials_returns_zero(self):
        assert wilson_lower_bound(0, 0) == 0.0

    def test_zero_successes_returns_zero(self):
        assert wilson_lower_bound(0, 70) == 0.0

    def test_reference_value_10_of_20(self):
        """Valeur de référence cross-validée avec statsmodels
        (proportion_confint(10, 20, alpha=0.05, method='wilson') -> 0.29930)."""
        confidence = wilson_lower_bound(10, 20)
        assert confidence == pytest.approx(0.2993, abs=0.001)

    def test_small_sample_high_proportion_triggers_malicious_verdict(self):
        """3 détections sur 5 moteurs (60%) -> signal statistiquement fort,
        doit dépasser le seuil de décision MALVEILLANT (0.15) utilisé dans
        reputation_checker.check_hash()."""
        confidence = wilson_lower_bound(3, 5)
        assert confidence == pytest.approx(0.2307, abs=0.001)
        assert confidence >= 0.15

    def test_same_absolute_count_large_sample_stays_suspect_not_malicious(self):
        """C'est le cas central que corrige cette optimisation : 3
        détections sur 70 moteurs (4.3%) — MÊME compte absolu que le test
        précédent (3), mais un signal statistiquement faible. L'ancien code
        ("MALVEILLANT si malicious_count >= 3") traitait les deux cas de
        façon identique ; la borne de Wilson les distingue correctement."""
        confidence = wilson_lower_bound(3, 70)
        assert confidence == pytest.approx(0.0147, abs=0.001)
        assert confidence < 0.15

    def test_full_detection_gives_near_certainty(self):
        confidence = wilson_lower_bound(70, 70)
        assert confidence > 0.9

    def test_confidence_increases_monotonically_with_proportion(self):
        """À nombre total de moteurs fixe, plus il y a de détections, plus
        la confiance doit croître — propriété mathématique de base que
        toute reformulation future de la formule doit continuer à respecter."""
        c1 = wilson_lower_bound(1, 70)
        c2 = wilson_lower_bound(5, 70)
        c3 = wilson_lower_bound(20, 70)
        c4 = wilson_lower_bound(70, 70)
        assert c1 < c2 < c3 < c4

    def test_confidence_bounded_between_zero_and_one(self):
        """La borne de Wilson est par construction une proportion : le
        résultat doit toujours rester dans [0, 1], quels que soient k et n."""
        for k, n in [(1, 1), (0, 1), (100, 100), (1, 1000), (999, 1000)]:
            c = wilson_lower_bound(k, n)
            assert 0.0 <= c <= 1.0

    def test_larger_sample_at_same_proportion_gives_higher_confidence(self):
        """Propriété statistique fondamentale de l'intervalle de Wilson :
        à proportion observée IDENTIQUE (50%), un échantillon plus grand
        réduit l'incertitude et donne donc une borne basse plus élevée."""
        c_small_sample = wilson_lower_bound(5, 10)
        c_large_sample = wilson_lower_bound(50, 100)
        assert c_large_sample > c_small_sample


class TestCheckHashVerdictIntegration:
    """Teste le branchement complet de check_hash() (pas seulement la
    fonction mathématique isolée), en mockant requests.get pour éviter
    tout appel réseau réel vers l'API VirusTotal."""

    def _make_checker(self, tmp_path, monkeypatch, response: _FakeResponse):
        api_key_path = tmp_path / "vt_api_key.txt"
        api_key_path.write_text("fake-api-key-for-tests", encoding="utf-8")
        cache_path = tmp_path / "cache" / "vt_cache.json"

        checker = ReputationChecker(str(api_key_path), str(cache_path))
        monkeypatch.setattr(
            "optimizer.reputation_checker.requests.get",
            lambda *args, **kwargs: response,
        )
        return checker

    def test_zero_malicious_gives_sain_verdict(self, tmp_path, monkeypatch):
        response = _FakeResponse(200, _vt_payload(malicious=0, suspicious=0, harmless=65, undetected=5))
        checker = self._make_checker(tmp_path, monkeypatch, response)

        result = checker.check_hash("a" * 64)
        assert result["verdict"] == "SAIN"
        assert result["confidence_wilson_95"] == 0.0

    def test_small_sample_high_ratio_gives_malveillant_verdict(self, tmp_path, monkeypatch):
        """3 détections sur 5 moteurs répondants (60%) -> MALVEILLANT."""
        response = _FakeResponse(200, _vt_payload(malicious=3, suspicious=0, harmless=2, undetected=0))
        checker = self._make_checker(tmp_path, monkeypatch, response)

        result = checker.check_hash("b" * 64)
        assert result["verdict"] == "MALVEILLANT"
        assert result["malicious_count"] == 3
        assert result["total_engines"] == 5

    def test_same_absolute_count_large_sample_gives_suspect_not_malveillant(self, tmp_path, monkeypatch):
        """LE cas que corrige cette optimisation : 3 détections sur 70
        moteurs (4.3%) -> SUSPECT, alors que l'ancien seuil absolu
        ("malicious_count >= 3") aurait donné MALVEILLANT à tort, de façon
        strictement identique au cas 3/5 ci-dessus."""
        response = _FakeResponse(
            200, _vt_payload(malicious=3, suspicious=0, harmless=60, undetected=7)
        )
        checker = self._make_checker(tmp_path, monkeypatch, response)

        result = checker.check_hash("c" * 64)
        assert result["verdict"] == "SUSPECT"
        assert result["malicious_count"] == 3
        assert result["total_engines"] == 70

    def test_cached_result_is_reused_without_calling_requests_again(self, tmp_path, monkeypatch):
        response = _FakeResponse(200, _vt_payload(malicious=0, suspicious=0, harmless=70, undetected=0))
        checker = self._make_checker(tmp_path, monkeypatch, response)

        first = checker.check_hash("d" * 64)
        assert first["from_cache"] is False

        # Deuxième appel : ne doit plus toucher requests.get (le mock
        # renvoie toujours la même réponse de toute façon, mais on vérifie
        # explicitement le flag from_cache pour s'assurer que le chemin de
        # code emprunté est bien celui du cache, pas un nouvel appel réseau).
        second = checker.check_hash("d" * 64)
        assert second["from_cache"] is True
        assert second["verdict"] == first["verdict"]

    def test_404_gives_unknown_status_not_malveillant(self, tmp_path, monkeypatch):
        """Un hash jamais soumis à VirusTotal ne doit pas être traité comme
        suspect par défaut (fichier probablement juste rare/nouveau)."""
        response = _FakeResponse(404)
        checker = self._make_checker(tmp_path, monkeypatch, response)

        result = checker.check_hash("e" * 64)
        assert result["status"] == "inconnu"
