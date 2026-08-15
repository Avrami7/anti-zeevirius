"""
yara_scanner.py
Détection par règles YARA — le standard utilisé par la plupart des moteurs
antivirus/EDR professionnels (VirusTotal, CrowdStrike Falcon, etc.).

Permet de détecter des familles de malware par patterns binaires,
chaînes de caractères suspectes, ou structures de fichiers, sans
connaître le hash exact (contrairement au hash_scanner qui ne détecte
que des fichiers strictement identiques à un échantillon déjà vu).

Installation requise : pip install yara-python
"""

from pathlib import Path
from typing import List, Dict

try:
    import yara
    YARA_AVAILABLE = True
except ImportError:
    YARA_AVAILABLE = False


class YaraScanner:
    def __init__(self, rules_path: str):
        self.rules_path = Path(rules_path)
        self.compiled_rules = None

        if not YARA_AVAILABLE:
            print(
                "[AVERTISSEMENT] yara-python n'est pas installé. "
                "Exécutez : pip install yara-python\n"
                "Le scan YARA sera désactivé jusqu'à installation."
            )
            return

        self._load_rules()

    def _load_rules(self) -> None:
        if not self.rules_path.exists():
            self.rules_path.parent.mkdir(parents=True, exist_ok=True)
            self.rules_path.write_text(self._default_rules(), encoding="utf-8")

        try:
            self.compiled_rules = yara.compile(filepath=str(self.rules_path))
        except yara.Error as e:
            print(f"[ERREUR] Compilation des règles YARA échouée : {e}")
            self.compiled_rules = None

    @staticmethod
    def _default_rules() -> str:
        """Quelques règles de démarrage — à enrichir avec des flux publics
        (ex: règles YARA publiques de Yara-Rules/rules sur GitHub, Florian
        Roth's signature-base, etc.)."""
        return """
rule Suspicious_Double_Extension
{
    meta:
        description = "Fichier avec double extension déguisant un exécutable (ex: facture.pdf.exe)"
        severity = "medium"
    strings:
        $ext1 = ".pdf.exe" nocase
        $ext2 = ".docx.exe" nocase
        $ext3 = ".jpg.exe" nocase
        $ext4 = ".xlsx.exe" nocase
    condition:
        any of them
}

rule Suspicious_PowerShell_Encoded_Command
{
    meta:
        description = "Commande PowerShell encodée en base64 (technique d'évasion courante)"
        severity = "high"
    strings:
        $a = "-enc " nocase
        $b = "-EncodedCommand" nocase
        $c = "FromBase64String" nocase
    condition:
        any of them
}

rule Suspicious_Macro_AutoExec
{
    meta:
        description = "Macro Office avec exécution automatique (vecteur d'infection classique)"
        severity = "medium"
    strings:
        $a = "AutoOpen" nocase
        $b = "Document_Open" nocase
        $c = "Shell(" nocase
        $d = "WScript.Shell" nocase
    condition:
        2 of them
}

rule Suspicious_Reverse_Shell_Strings
{
    meta:
        description = "Chaînes typiques d'un reverse shell"
        severity = "critical"
    strings:
        $a = "cmd.exe /c" nocase
        $b = "/bin/sh -i" nocase
        $c = "socket.connect" nocase
    condition:
        any of them
}
""".strip()

    def scan_file(self, file_path: str) -> Dict:
        """Retourne les règles YARA déclenchées pour un fichier donné."""
        if not YARA_AVAILABLE or self.compiled_rules is None:
            return {"clean": True, "matches": [], "reason": "Moteur YARA indisponible"}

        try:
            matches = self.compiled_rules.match(file_path, timeout=10)
            match_list = [
                {
                    "rule": m.rule,
                    "severity": m.meta.get("severity", "unknown"),
                    "description": m.meta.get("description", ""),
                }
                for m in matches
            ]
            return {
                "clean": len(match_list) == 0,
                "matches": match_list,
                "reason": f"{len(match_list)} règle(s) déclenchée(s)" if match_list else "Aucune correspondance",
            }
        except yara.Error as e:
            return {"clean": True, "matches": [], "reason": f"Erreur de scan : {e}"}
        except (PermissionError, FileNotFoundError, OSError):
            return {"clean": True, "matches": [], "reason": "Fichier illisible"}
