"""
heuristics.py
Détection heuristique — repère des comportements/caractéristiques
suspectes SANS avoir besoin d'une signature connue au préalable.
C'est ce qui permet de détecter des malwares inédits (zero-day-like).

Techniques utilisées (celles des vrais moteurs AV) :
1. Analyse d'entropie des sections PE (code "packé"/chiffré = suspect)
2. Absence de signature numérique Authenticode sur un exécutable
3. Emplacement d'exécution suspect (Temp, Downloads, AppData)
4. Ratio de caractères imprimables anormalement bas dans les strings

Installation requise : pip install pefile
"""

import math
import os
from pathlib import Path
from typing import Dict, List

try:
    import pefile
    PEFILE_AVAILABLE = True
except ImportError:
    PEFILE_AVAILABLE = False

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

SUSPICIOUS_LOCATIONS = [
    "\\temp\\", "\\tmp\\", "\\appdata\\local\\temp\\",
    "\\downloads\\", "\\users\\public\\",
]

ENTROPY_THRESHOLD = 7.2  # Au-delà, section très probablement compressée/chiffrée (packer)

# Longueur minimale d'une séquence d'octets imprimables pour être comptée
# comme une "chaîne" exploitable (convention utilisée par l'outil `strings`
# et par la plupart des moteurs heuristiques commerciaux).
MIN_STRING_LEN = 4

# En dessous de ce ratio (proportion de l'exécutable occupée par des chaînes
# lisibles ≥ MIN_STRING_LEN), le binaire est anormalement dépourvu de texte
# exploitable (noms de fonctions importées, messages d'erreur, chemins...) —
# signal typique d'un packer/obfuscateur qui a remplacé le contenu original
# par un blob compressé/chiffré. NOTE MÉTHODOLOGIQUE : contrairement à la
# borne de Wilson utilisée dans reputation_checker.py, ceci n'est PAS une
# estimation statistique — le ratio est mesuré sur la population complète
# (tous les octets du fichier), pas sur un échantillon.
#
# Choix du seuil : p(un octet est "imprimable") = 98/256 ≈ 0.383 (95 codes
# ASCII visibles 0x20-0x7E + tab/LF/CR). Même dans des données PUREMENT
# aléatoires (aucune structure, cas d'un packer parfait), des runs de
# min_string_len octets imprimables apparaissent par pur hasard. Simulation
# (30 tirages, 50 000 octets uniformes, min_string_len=4) : plancher de
# bruit mesuré ≈ 6.1% en moyenne, jusqu'à 6.5% en maximum observé. Le seuil
# est donc fixé avec une marge de sécurité ≈ 2,3× ce plancher, pour éviter
# de déclencher un faux positif sur du simple bruit statistique plutôt que
# sur une réelle absence de chaînes exploitables.
MIN_PRINTABLE_STRING_RATIO = 0.15


class HeuristicScanner:

    @staticmethod
    def _shannon_entropy(data: bytes) -> float:
        """
        Entropie de Shannon : H(X) = - Σ p_i · log2(p_i)

        Complexité :
        - Version Python pure (boucle octet par octet) : O(n) itérations
          d'interpréteur, coût constant élevé (~50-100 ns/octet en CPython).
        - Version vectorisée numpy : le comptage des 256 symboles se fait
          en C via np.bincount (O(n) mais avec une constante ~50-100x plus
          faible), puis la somme -Σp·log2(p) est appliquée sur un vecteur
          de taille fixe 256, pas sur les n octets.
        Sur une section PE de quelques Mo, cela transforme un calcul de
        plusieurs centaines de ms en quelques ms — déterminant quand on
        scanne un disque entier fichier par fichier.
        """
        if not data:
            return 0.0
        length = len(data)

        if NUMPY_AVAILABLE:
            # Histogramme des 256 valeurs d'octets en une passe vectorisée C
            buf = np.frombuffer(data, dtype=np.uint8)
            counts = np.bincount(buf, minlength=256).astype(np.float64)
            probs = counts[counts > 0] / length
            entropy = float(-np.sum(probs * np.log2(probs)))
            return entropy

        # Repli pur Python si numpy n'est pas installé (résultat identique,
        # juste plus lent — pip install numpy pour la version optimisée).
        occurrences = [0] * 256
        for byte in data:
            occurrences[byte] += 1
        entropy = 0.0
        for count in occurrences:
            if count == 0:
                continue
            p = count / length
            entropy -= p * math.log2(p)
        return entropy

    @staticmethod
    def _printable_string_ratio(data: bytes, min_string_len: int = MIN_STRING_LEN) -> float:
        """
        Ratio (0.0 à 1.0) des octets appartenant à une séquence de
        caractères imprimables d'au moins `min_string_len` octets — même
        principe que la commande Unix `strings`.

        Algorithme (identique en substance aux deux implémentations,
        seule la vectorisation change) :
        1. Marquer chaque octet comme imprimable ou non (ASCII 0x20-0x7E,
           + tab/LF/CR).
        2. Regrouper les octets imprimables consécutifs en "runs".
        3. Ne garder que les runs de longueur ≥ min_string_len (une chaîne
           isolée de 2-3 caractères imprimables est trop fréquente par
           hasard dans des données binaires pour être significative).
        4. Ratio = (somme des longueurs de ces runs) / (taille totale).

        Version numpy : la détection des frontières de runs se fait par
        différence discrète sur le masque booléen (np.diff), en O(n) avec
        une constante C au lieu d'une boucle Python octet par octet —
        même logique de gain que _shannon_entropy ci-dessus.
        """
        if not data:
            return 0.0
        length = len(data)

        if NUMPY_AVAILABLE:
            buf = np.frombuffer(data, dtype=np.uint8)
            printable = ((buf >= 0x20) & (buf <= 0x7E)) | (buf == 0x09) | (buf == 0x0A) | (buf == 0x0D)
            if not printable.any():
                return 0.0
            # Bordures à False pour capter les runs qui touchent le début/la fin
            padded = np.concatenate(([False], printable, [False]))
            diff = np.diff(padded.astype(np.int8))
            starts = np.flatnonzero(diff == 1)
            ends = np.flatnonzero(diff == -1)
            run_lengths = ends - starts
            total_string_bytes = int(run_lengths[run_lengths >= min_string_len].sum())
            return total_string_bytes / length

        # Repli pur Python (résultat identique, plus lent sans numpy)
        total_string_bytes = 0
        current_run = 0
        for byte in data:
            is_printable = (0x20 <= byte <= 0x7E) or byte in (0x09, 0x0A, 0x0D)
            if is_printable:
                current_run += 1
            else:
                if current_run >= min_string_len:
                    total_string_bytes += current_run
                current_run = 0
        if current_run >= min_string_len:
            total_string_bytes += current_run
        return total_string_bytes / length

    @staticmethod
    def _check_suspicious_location(file_path: str) -> bool:
        normalized = file_path.lower().replace("/", "\\")
        return any(loc in normalized for loc in SUSPICIOUS_LOCATIONS)

    def _analyze_pe(self, file_path: str) -> List[Dict]:
        """Analyse structurelle du fichier PE (exécutable Windows)."""
        findings = []
        if not PEFILE_AVAILABLE:
            return findings

        try:
            pe = pefile.PE(file_path, fast_load=True)
        except pefile.PEFormatError:
            return findings  # Pas un exécutable PE valide, pas de scan PE nécessaire
        except Exception:
            return findings

        # 1. Entropie des sections
        for section in pe.sections:
            data = section.get_data()
            entropy = self._shannon_entropy(data)
            if entropy >= ENTROPY_THRESHOLD:
                findings.append({
                    "check": "entropy",
                    "severity": "medium",
                    "detail": f"Section {section.Name.decode(errors='ignore').strip()} "
                              f"a une entropie de {entropy:.2f} (packer/chiffrement probable)",
                })

        # 2. Signature numérique Authenticode
        try:
            pe.parse_data_directories(
                directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_SECURITY"]]
            )
            security_dir = pe.OPTIONAL_HEADER.DATA_DIRECTORY[
                pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_SECURITY"]
            ]
            if security_dir.VirtualAddress == 0:
                findings.append({
                    "check": "signature",
                    "severity": "low",
                    "detail": "Exécutable non signé numériquement (Authenticode absent)",
                })
        except Exception:
            pass

        # 3. Nombre de sections anormalement élevé (technique d'obfuscation)
        if pe.FILE_HEADER.NumberOfSections > 10:
            findings.append({
                "check": "sections_count",
                "severity": "low",
                "detail": f"{pe.FILE_HEADER.NumberOfSections} sections PE (nombre inhabituel)",
            })

        # 4. Ratio de chaînes imprimables anormalement bas (technique #4 —
        # voir _printable_string_ratio ci-dessus). On réutilise pe.__data__
        # (le fichier déjà chargé en mémoire par pefile) plutôt que de relire
        # le fichier depuis le disque — évite une I/O redondante.
        file_data = getattr(pe, "__data__", None)
        if file_data:
            ratio = self._printable_string_ratio(bytes(file_data))
            if ratio < MIN_PRINTABLE_STRING_RATIO:
                findings.append({
                    "check": "printable_ratio",
                    "severity": "medium" if ratio < MIN_PRINTABLE_STRING_RATIO / 2 else "low",
                    "detail": f"Seulement {ratio:.1%} du fichier est composé de chaînes lisibles "
                              f"(seuil {MIN_PRINTABLE_STRING_RATIO:.0%}) — packer/chiffrement probable",
                })

        pe.close()
        return findings

    def scan_file(self, file_path: str) -> Dict:
        findings: List[Dict] = []

        # Vérif emplacement — s'applique à tout type de fichier
        if self._check_suspicious_location(file_path):
            findings.append({
                "check": "location",
                "severity": "low",
                "detail": f"Fichier exécuté depuis un emplacement à risque : {os.path.dirname(file_path)}",
            })

        # Vérif double extension
        name = Path(file_path).name.lower()
        parts = name.split(".")
        if len(parts) > 2 and parts[-1] in ("exe", "scr", "bat", "cmd", "vbs", "ps1"):
            findings.append({
                "check": "double_extension",
                "severity": "medium",
                "detail": f"Extension multiple suspecte : {name}",
            })

        # Analyse PE si applicable
        if file_path.lower().endswith((".exe", ".dll", ".scr", ".sys")):
            findings.extend(self._analyze_pe(file_path))

        severity_score = {"low": 1, "medium": 2, "high": 3, "critical": 4}
        total_score = sum(severity_score.get(f["severity"], 0) for f in findings)

        return {
            "clean": total_score < 2,  # seuil ajustable
            "findings": findings,
            "risk_score": total_score,
            "reason": f"{len(findings)} indicateur(s) suspect(s), score={total_score}" if findings else "Aucun indicateur suspect",
        }
