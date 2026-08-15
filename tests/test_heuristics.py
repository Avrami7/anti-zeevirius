"""
test_heuristics.py
Couvre scanner/heuristics.py :
- _shannon_entropy() : cas limites connus (données uniformes = 0 bit,
  données aléatoires ≈ 8 bits, distribution à 2 symboles = exactement 1 bit),
  et non-régression entre la version vectorisée numpy et le repli pur Python.
- _printable_string_ratio() : texte lisible vs binaire aléatoire, seuil
  calibré (MIN_PRINTABLE_STRING_RATIO), et non-comptage des runs trop courts.

Ces tests figent le comportement des deux heuristiques statistiques
introduites/optimisées lors de l'audit — toute régression future (ex: un
refactoring qui casse le calcul vectorisé) sera détectée automatiquement.
"""

import random

import pytest

import scanner.heuristics as heuristics_module
from scanner.heuristics import HeuristicScanner, MIN_PRINTABLE_STRING_RATIO, MIN_STRING_LEN


class TestShannonEntropy:
    def test_empty_data_returns_zero(self):
        assert HeuristicScanner._shannon_entropy(b"") == 0.0

    def test_uniform_data_has_zero_entropy(self):
        """Un seul symbole répété -> incertitude nulle -> H = 0 bit."""
        data = bytes([0x41]) * 10_000
        assert HeuristicScanner._shannon_entropy(data) == pytest.approx(0.0, abs=1e-9)

    def test_two_equiprobable_symbols_give_exactly_one_bit(self):
        """H = -[0.5*log2(0.5) + 0.5*log2(0.5)] = 1.0 bit exactement —
        valeur calculable analytiquement, sert de test de non-régression
        exact plutôt qu'un simple encadrement."""
        data = bytes([0x00, 0x01] * 5000)
        assert HeuristicScanner._shannon_entropy(data) == pytest.approx(1.0, abs=1e-9)

    def test_random_data_approaches_maximum_entropy(self):
        """256 symboles équiprobables -> H max = log2(256) = 8 bits.
        Des données réellement aléatoires doivent s'en approcher (>7.9)."""
        random.seed(42)
        data = bytes(random.randint(0, 255) for _ in range(200_000))
        h = HeuristicScanner._shannon_entropy(data)
        assert 7.9 < h <= 8.0

    def test_above_packer_threshold_on_random_data(self):
        """Vérifie que le comportement métier attendu tient : des données
        packées/chiffrées (approximées ici par du bruit uniforme) dépassent
        bien ENTROPY_THRESHOLD (7.2), le seuil utilisé par _analyze_pe()."""
        random.seed(1)
        data = bytes(random.randint(0, 255) for _ in range(100_000))
        h = HeuristicScanner._shannon_entropy(data)
        assert h >= heuristics_module.ENTROPY_THRESHOLD

    def test_numpy_and_pure_python_paths_agree(self, monkeypatch):
        """La version vectorisée (numpy) et le repli pur Python doivent
        produire un résultat identique à la précision flottante près —
        sinon la présence/absence de numpy changerait le verdict de scan
        d'une machine à l'autre, ce qui serait inacceptable pour un AV."""
        random.seed(7)
        data = bytes(random.randint(0, 255) for _ in range(5000))

        monkeypatch.setattr(heuristics_module, "NUMPY_AVAILABLE", True)
        h_numpy = HeuristicScanner._shannon_entropy(data)

        monkeypatch.setattr(heuristics_module, "NUMPY_AVAILABLE", False)
        h_python = HeuristicScanner._shannon_entropy(data)

        assert h_numpy == pytest.approx(h_python, abs=1e-9)


class TestPrintableStringRatio:
    def test_empty_data_returns_zero(self):
        assert HeuristicScanner._printable_string_ratio(b"") == 0.0

    def test_readable_text_has_high_ratio(self):
        data = b"KERNEL32.dll GetProcAddress LoadLibraryA CreateFileW " * 50
        ratio = HeuristicScanner._printable_string_ratio(data)
        assert ratio > 0.9

    def test_random_binary_stays_below_decision_threshold(self):
        """Plancher de bruit mesuré par simulation (30 tirages x 50 000
        octets uniformes) : ~6.1% en moyenne, 6.5% en maximum observé.
        Le seuil de décision (15%) doit rester nettement au-dessus pour
        ne pas confondre du bruit statistique avec une vraie absence de
        chaînes exploitables."""
        random.seed(123)
        data = bytes(random.randint(0, 255) for _ in range(20_000))
        ratio = HeuristicScanner._printable_string_ratio(data)
        assert ratio < MIN_PRINTABLE_STRING_RATIO

    def test_noise_floor_stays_under_safety_margin(self):
        """Reproduit la simulation qui a servi à calibrer
        MIN_PRINTABLE_STRING_RATIO : sur plusieurs échantillons aléatoires
        indépendants, le ratio mesuré ne doit jamais dépasser ~2x le
        plancher de bruit attendu (~6.5%), sans quoi le seuil de 15%
        choisi ne serait plus une marge de sécurité suffisante."""
        random.seed(2024)
        max_ratio = 0.0
        for _ in range(15):
            data = bytes(random.randint(0, 255) for _ in range(20_000))
            max_ratio = max(max_ratio, HeuristicScanner._printable_string_ratio(data))
        assert max_ratio < 0.10

    def test_runs_shorter_than_min_string_len_are_not_counted(self):
        """Un run de (MIN_STRING_LEN - 1) octets imprimables noyé dans du
        binaire ne doit strictement rien changer au ratio — sinon
        l'algorithme de comptage par runs serait cassé (comptage octet
        par octet au lieu d'un comptage par séquence)."""
        random.seed(9)
        buf = bytearray(random.randint(0, 255) for _ in range(10_000))
        # Isole explicitement le run avec des octets NON imprimables aux
        # deux bornes (0x00), pour éviter qu'il ne fusionne par hasard avec
        # un octet imprimable voisin déjà présent dans le bruit aléatoire
        # (ce qui donnerait un run >= MIN_STRING_LEN et fausserait le test).
        buf[99] = 0x00
        buf[100:100 + (MIN_STRING_LEN - 1)] = b"a" * (MIN_STRING_LEN - 1)
        buf[100 + (MIN_STRING_LEN - 1)] = 0x00
        ratio_with_short_run = HeuristicScanner._printable_string_ratio(bytes(buf))

        baseline = bytearray(buf)
        baseline[99:100 + (MIN_STRING_LEN - 1) + 1] = b"\x00" * (MIN_STRING_LEN + 1)
        ratio_baseline = HeuristicScanner._printable_string_ratio(bytes(baseline))

        assert ratio_with_short_run == pytest.approx(ratio_baseline, abs=1e-9)

    def test_run_at_exactly_min_string_len_is_counted(self):
        """Cas limite : un run de EXACTEMENT MIN_STRING_LEN octets doit,
        lui, être compté intégralement dans le ratio."""
        data = b"\x00" * 100 + b"a" * MIN_STRING_LEN + b"\x00" * 100
        ratio = HeuristicScanner._printable_string_ratio(data)
        expected = MIN_STRING_LEN / len(data)
        assert ratio == pytest.approx(expected, abs=1e-9)

    def test_numpy_and_pure_python_paths_agree(self, monkeypatch):
        random.seed(3)
        data = b"some readable text here " * 20 + bytes(random.randint(0, 255) for _ in range(3000))

        monkeypatch.setattr(heuristics_module, "NUMPY_AVAILABLE", True)
        r_numpy = HeuristicScanner._printable_string_ratio(data)

        monkeypatch.setattr(heuristics_module, "NUMPY_AVAILABLE", False)
        r_python = HeuristicScanner._printable_string_ratio(data)

        assert r_numpy == pytest.approx(r_python, abs=1e-9)
